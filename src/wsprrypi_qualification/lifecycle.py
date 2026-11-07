"""RF-disabled lifecycle plans and durable, fail-closed production-command execution.

Commands are explicit reviewed recipes, not inferred from historical evidence.
Product tools own host/device locks and recovery; the controller owns evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from wsprrypi_qualification.progress import ProgressReporter
from wsprrypi_qualification.progress_viewer import tracking_command
from wsprrypi_qualification.repository_protection import (
    RepositoryProtectionError,
    assert_outside_protected,
    discover_protected_roots,
)
from wsprrypi_qualification.transports import CommandPlan, LocalCommandTransport


class LifecycleError(ValueError):
    """Invalid plan or unresolved lifecycle duty."""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate(value: dict[str, Any], name: str) -> None:
    schema = json.loads(
        files("wsprrypi_qualification.schemas")
        .joinpath(name + ".schema.json")
        .read_text(encoding="utf-8")
    )
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise LifecycleError(f"{name}: {errors[0].json_path}: {errors[0].message}")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise LifecycleError("expected a JSON object")
    return value


def save(path: Path, value: Any, *, new: bool = False) -> None:
    """Atomic checkpoint replace; fsync contents and directory where supported."""
    data = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    if new:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".pending")
        save(temporary, value, new=True)
        os.replace(temporary, path)
    if os.name != "nt":
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], timeout=30).decode().strip()


def source_identity(component: str, path: Path) -> dict[str, str]:
    """Include tracked diffs and untracked bytes; never modify the checkout."""
    path = path.resolve()
    status = subprocess.check_output(
        ["git", "-C", str(path), "status", "--porcelain=v1", "-z"], timeout=30
    )
    changes = subprocess.check_output(
        ["git", "-C", str(path), "diff", "HEAD", "--binary"], timeout=30
    )
    names = subprocess.check_output(
        ["git", "-C", str(path), "ls-files", "--others", "--exclude-standard", "-z"], timeout=30
    )
    untracked = []
    for raw in names.split(b"\0"):
        if raw:
            name = os.fsdecode(raw)
            item = path / name
            untracked.append((name, os.readlink(item) if item.is_symlink() else file_sha(item)))
    return {
        "component": component,
        "path": str(path),
        "revision": git(path, "rev-parse", "HEAD"),
        "dirty_sha256": digest([status.hex(), changes.hex(), untracked]),
    }


def _check_artifacts(items: list[dict[str, str]]) -> None:
    for item in items:
        path = Path(item["path"])
        if not path.is_absolute() or not path.is_file() or file_sha(path) != item["sha256"]:
            raise LifecycleError(f"input missing or changed: {path}")


def _outside(path: Path, roots: list[str]) -> None:
    probe = path
    while not probe.exists():
        probe = probe.parent
    git_path = shutil.which("git")
    if git_path is None:
        raise LifecycleError("Git required to verify the external runtime boundary")
    try:
        discovered = discover_protected_roots([probe], git_executable=Path(git_path), timeout_s=30)
        assert_outside_protected(path, discovered, must_exist=False)
    except RepositoryProtectionError as error:
        raise LifecycleError(str(error)) from error
    for root in roots:
        resolved = Path(root).resolve()
        if path == resolved or path.is_relative_to(resolved):
            raise LifecycleError(
                f"controller state/output overlaps protected product/source: {root}"
            )


def controller_identity() -> dict[str, Any]:
    package = Path(__file__).parent
    names = (
        "cli.py",
        "__init__.py",
        "lifecycle.py",
        "lifecycle_cli.py",
        "lifecycle_recipes.py",
        "lifecycle_release.py",
        "transports.py",
        "repository_protection.py",
        "progress.py",
        "progress_viewer.py",
    )
    modules = [{"path": str(package / name), "sha256": file_sha(package / name)} for name in names]
    modules += [
        {"path": str(path), "sha256": file_sha(path)}
        for path in sorted((package / "schemas").glob("lifecycle-*.schema.json"))
    ]
    executable = Path(sys.executable).resolve()
    root = package.parent.parent
    source = source_identity("harness", root) if (root / ".git").exists() else None
    return {
        "python": {"path": str(executable), "sha256": file_sha(executable)},
        "version": sys.version,
        "modules": modules,
        "source": source,
    }


def compose(request: dict[str, Any], *, publication_selected: bool = False) -> dict[str, Any]:
    """Resolve locally only. No host/device/CI access, mutation or publication."""
    validate(request, "lifecycle-request")
    if publication_selected and request["operation"] != "release":
        raise LifecycleError("publication requires a selected release operation")
    selected = {"pi", "pico", "rp1"} if request["scope"] == "system" else {request["scope"]}
    if request["profile"] in {"pi-public-eight", "pi-streamed-eight"} and request["scope"] == "pi":
        selected.add("rp1")  # Public Pi 5 installation explicitly includes this provider.
    blockers: list[str] = []
    from wsprrypi_qualification.lifecycle_recipes import CHECKS

    if request["profile"] in CHECKS and not set(CHECKS[request["profile"]]).issubset(
        request["required_checks"]
    ):
        raise LifecycleError("profile required checks cannot be omitted")
    ids = [step["id"] for step in request["steps"]]
    if len(ids) != len(set(ids)):
        raise LifecycleError("step IDs must be unique")
    sources = request["sources"]
    if len({s["component"] for s in sources}) != len(sources):
        raise LifecycleError("duplicate source component")
    if not selected.issubset({s["component"] for s in sources}):
        raise LifecycleError("selected component lacks exact source identity")
    for source in sources:
        if source_identity(source["component"], Path(source["path"])) != source:
            raise LifecycleError("source identity changed: " + source["component"])
    roots = request["protected_paths"] + [s["path"] for s in sources]
    state = Path(request["state_root"])
    if not state.is_absolute():
        raise LifecycleError("state_root must be absolute")
    _outside(state.resolve(), roots)
    _check_artifacts(request["artifacts"])
    checks: set[str] = set()
    for step in request["steps"]:
        if step["component"] not in selected or not set(step["mutates"]).issubset(selected):
            raise LifecycleError("independent scope would mutate an unselected companion")
        if step["phase"] in {"inspect", "verify", "public_acceptance"} and step["mutates"]:
            raise LifecycleError("read-only step declares mutation")
        if step["mutates"] and step["replay_safe"]:
            raise LifecycleError(
                "mutating effects cannot be automatically replayed after controller loss"
            )
        if step["phase"] in {"effect", "recover", "cleanup", "publish"} and not step["mutates"]:
            raise LifecycleError("effect must declare its component ownership")
        if step["phase"] == "publish" and not publication_selected:
            blockers.append("publication is disabled in validation plans: " + step["id"])
        cmd = step["command"]
        _check_artifacts(cmd["inputs"])
        exe = Path(cmd["argv"][0])
        if not exe.is_absolute() or not any(i["path"] == str(exe) for i in cmd["inputs"]):
            raise LifecycleError("command executable must be absolute and hash-bound")
        if not Path(cmd["cwd"]).is_absolute():
            raise LifecycleError("command cwd must be absolute")
        # Mutable product inputs must live outside repositories; even test processes
        # run externally. Recipes pass immutable source paths explicitly.
        _outside(Path(cmd["cwd"]).resolve(), [s["path"] for s in sources])
        if step["phase"] in {"inspect", "verify", "public_acceptance"}:
            checks.add(step["check"])
            if not step["expect_json"]:
                raise LifecycleError("verification needs independent JSON assertions")
    missing = sorted(set(request["required_checks"]) - checks)
    blockers.extend("missing required check: " + check for check in missing)
    if request["steps"][0]["phase"] != "inspect":
        raise LifecycleError("first step must inspect actual state")
    if request["steps"][-1]["phase"] not in {"verify", "public_acceptance"}:
        raise LifecycleError("last step must independently verify final state")
    # Every mutating phase must have a later independent check, including cleanup.
    for index, step in enumerate(request["steps"]):
        if step["mutates"] and not any(
            s["component"] == step["component"] and s["phase"] in {"verify", "public_acceptance"}
            for s in request["steps"][index + 1 :]
        ):
            raise LifecycleError("effect lacks later independent component verification")
    key = digest({k: v for k, v in request.items() if k != "ci"})
    ci = []
    for evidence in request["ci"]:
        reuse = (
            evidence["input_sha256"] == key
            and evidence["conclusion"] == "success"
            and bool(evidence["required_jobs"])
            and set(evidence["required_jobs"]).issubset(evidence["successful_jobs"])
        )
        ci.append(
            {
                "component": evidence["component"],
                "workflow": evidence["workflow"],
                "reuse": reuse,
                "reason": "exact inputs and required jobs passed"
                if reuse
                else "canonical run required: input key, conclusion or required jobs differ",
                "url": evidence["url"],
            }
        )
    plan = {
        "schema_version": 1,
        "kind": "lifecycle-plan",
        "controller": controller_identity(),
        "request": request,
        "input_sha256": key,
        "blockers": blockers,
        "ci_selection": ci,
        "publication_enabled": publication_selected,
    }
    plan["plan_sha256"] = digest(plan)
    validate(plan, "lifecycle-plan")
    return plan


def verify_plan(plan: dict[str, Any]) -> None:
    validate(plan, "lifecycle-plan")
    unsigned = {k: v for k, v in plan.items() if k != "plan_sha256"}
    if digest(unsigned) != plan["plan_sha256"]:
        raise LifecycleError("plan digest mismatch")
    if compose(plan["request"], publication_selected=plan["publication_enabled"]) != plan:
        raise LifecycleError("resolved plan no longer matches inputs")


def controller_lock_root() -> Path:
    """One controller lock namespace across run roots for this OS account."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    else:
        base = Path.home() / ".local/state"
    return base / "wsprrypi-qualification/lifecycle-locks"


class ControllerLease:
    def __init__(self) -> None:
        self.files: list[tuple[Path, dict[str, Any]]] = []
        self.released = False
        self.errors: list[str] = []

    def release(self) -> list[str]:
        if self.released:
            return self.errors
        self.released = True
        for path, identity in reversed(self.files):
            try:
                if load(path) != identity:
                    raise LifecycleError("controller lock changed; foreign ownership retained")
                path.unlink()
            except (OSError, ValueError) as error:
                self.errors.append(str(path) + ": " + str(error))
        return self.errors


@contextmanager
def ownership(root: Path, resources: list[str]) -> Iterator[ControllerLease]:
    """Exclusive account-wide resources; never steal or retire foreign ownership."""
    lease = ControllerLease()
    try:
        for resource in sorted(set(resources)):
            path = root / (digest(resource) + ".lock")
            identity = {"resource": resource, "pid": os.getpid(), "nonce": secrets.token_hex(16)}
            try:
                save(path, identity, new=True)
            except FileExistsError as error:
                raise LifecycleError(
                    f"resource already owned: {resource}; inspect existing controller"
                ) from error
            lease.files.append((path, identity))
        yield lease
    finally:
        if not lease.released and lease.release():
            raise LifecycleError("controller cleanup failed: " + "; ".join(lease.errors))


def record_proofs(plan: dict[str, Any], directory: Path, records: list[dict[str, Any]]) -> set[str]:
    for item in records:
        path = directory / item["log"]
        if path.parent != directory or file_sha(path) != item["sha256"]:
            raise LifecycleError("execution log missing or changed")
    steps = {s["id"]: s for s in plan["request"]["steps"]}
    proved = set()
    for item in records:
        step = steps.get(item["step"])
        if step is None or item["phase"] != step["phase"]:
            raise LifecycleError("result names an unknown step/phase")
        observed = load(directory / item["log"])
        ok = (
            observed.get("return_code") == step["expect_exit"]
            and observed.get("cleanup_verified") is True
            and observed.get("timed_out") is False
            and observed.get("cancelled") is False
            and not observed.get("input_integrity_error")
        )
        if ok and step["expect_json"]:
            try:
                output = json.loads(observed["stdout"])
                ok = isinstance(output, dict) and all(
                    k in output and digest(output[k]) == digest(v)
                    for k, v in step["expect_json"].items()
                )
            except (ValueError, KeyError):
                ok = False
        if ok:
            proved.add(step["id"])
        if (item["status"] == "pass") != ok:
            raise LifecycleError("record outcome contradicts child evidence")
    return proved


def validate_checkpoint(plan: dict[str, Any], directory: Path, checkpoint: dict[str, Any]) -> None:
    validate(checkpoint, "lifecycle-checkpoint")
    if checkpoint["plan_sha256"] != plan["plan_sha256"]:
        raise LifecycleError("checkpoint plan identity differs")
    steps = {step["id"]: step for step in plan["request"]["steps"]}
    if not set(checkpoint["completed"]).issubset(steps) or (
        checkpoint["pending"] is not None and checkpoint["pending"] not in steps
    ):
        raise LifecycleError("checkpoint names unknown steps")
    proved = record_proofs(plan, directory, checkpoint["records"])
    if not set(checkpoint["completed"]).issubset(proved):
        raise LifecycleError("checkpoint completed steps lack verified execution records")
    published = sorted(
        {
            steps[name]["component"]
            for name in checkpoint["completed"]
            if steps[name]["phase"] == "publish"
        }
    )
    if checkpoint["published_components"] != published:
        raise LifecycleError("checkpoint publication state contradicts completed effects")


def execute_command(step: dict[str, Any]) -> dict[str, Any]:
    command = step["command"]
    _check_artifacts(command["inputs"])
    command_plan = CommandPlan(
        executable=Path(command["argv"][0]),
        arguments=tuple(command["argv"][1:]),
        working_directory=Path(command["cwd"]),
        timeout_s=command["timeout_s"],
        environment=command["environment"],
    )
    cancellation = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            LocalCommandTransport().execute, command_plan, cancellation=cancellation
        )
        while True:
            try:
                record = future.result(timeout=0.1)
                break
            except FutureTimeout:
                continue
            except KeyboardInterrupt:
                cancellation.set()
                record = future.result(timeout=5)
                break
    result = record.to_dict()
    try:
        _check_artifacts(command["inputs"])
    except (OSError, ValueError) as error:
        result["input_integrity_error"] = str(error)
    result["assertions_passed"] = False
    if (
        record.return_code == step["expect_exit"]
        and not (record.timed_out or record.cancelled)
        and record.cleanup_verified
        and not result.get("input_integrity_error")
    ):
        if step["expect_json"]:
            try:
                observed = json.loads(record.stdout)
                result["assertions_passed"] = all(
                    k in observed and digest(observed[k]) == digest(v)
                    for k, v in step["expect_json"].items()
                )
            except (ValueError, AttributeError):
                pass
        else:
            result["assertions_passed"] = True
    return result


def run(
    plan: dict[str, Any],
    directory: Path,
    *,
    confirm: str,
    resume: bool = False,
    inspection: str | None = None,
    enable_publication: bool = False,
) -> dict[str, Any]:
    verify_plan(plan)
    if confirm != plan["plan_sha256"]:
        raise LifecycleError("execution requires exact plan digest confirmation")
    if plan["publication_enabled"] and not enable_publication:
        raise LifecycleError(
            "publishing execution requires --enable-publication as well as the exact release plan"
        )
    if enable_publication and not plan["publication_enabled"]:
        raise LifecycleError("publication was not selected in this plan")
    for candidate in (directory, *directory.parents):
        if candidate.is_symlink():
            raise LifecycleError("symlink run path refused")
    directory = directory.resolve()
    request = plan["request"]
    state_root = Path(request["state_root"]).resolve()
    if not directory.is_relative_to(state_root) or directory == state_root:
        raise LifecycleError("run must be a new child of the durable state_root")
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_root = controller_lock_root()
    lock_root.mkdir(exist_ok=True, mode=0o700)
    with ownership(lock_root, request["resources"]) as lease:
        if resume:
            if load(directory / "plan.json") != plan:
                raise LifecycleError("run plan differs")
            checkpoint = load(directory / "checkpoint.json")
            validate_checkpoint(plan, directory, checkpoint)
        else:
            directory.mkdir(mode=0o700)
            save(directory / "plan.json", plan, new=True)
            checkpoint = {
                "plan_sha256": plan["plan_sha256"],
                "touched_components": [],
                "completed": [],
                "records": [],
                "pending": None,
                "published_components": [],
                "recovery_required": False,
                "unverified_components": [],
            }
            save(directory / "checkpoint.json", checkpoint, new=True)
        progress_path = directory / ("progress-" + secrets.token_hex(8) + ".jsonl")
        print(
            f"Progress: {progress_path}\nViewer: {tracking_command(progress_path)}",
            file=sys.stderr,
            flush=True,
        )
        status = "inconclusive"
        controller_error = None
        with ProgressReporter(progress_path) as progress:
            if resume:
                # Reinspect actual state on EVERY continuation, including controller
                # loss after an effect but before its completion receipt.
                candidates = [
                    s for s in request["steps"] if s["id"] == inspection and s["phase"] == "inspect"
                ]
                if len(candidates) != 1:
                    raise LifecycleError("resume requires a named read-only inspection step")
                observed = execute_command(candidates[0])
                save(
                    directory / ("resume-inspection-" + secrets.token_hex(8) + ".json"),
                    observed,
                    new=True,
                )
                if not observed["assertions_passed"]:
                    raise LifecycleError(
                        "actual-state inspection failed; recovery remains required"
                    )
                pending = checkpoint["pending"]
                if pending is not None:
                    step = next(s for s in request["steps"] if s["id"] == pending)
                    if not step["replay_safe"]:
                        raise LifecycleError(
                            "ambiguous non-replayable effect: use owning-component recovery; "
                            + pending
                        )
            try:
                for step in [] if plan["blockers"] else request["steps"]:
                    if step["id"] in checkpoint["completed"]:
                        continue
                    checkpoint["pending"] = step["id"]
                    checkpoint["touched_components"] = sorted(
                        set(checkpoint["touched_components"]) | set(step["mutates"])
                    )
                    checkpoint["unverified_components"] = sorted(
                        set(checkpoint["unverified_components"]) | set(step["mutates"])
                    )
                    checkpoint["recovery_required"] = bool(checkpoint["unverified_components"])
                    save(directory / "checkpoint.json", checkpoint)
                    progress.emit(
                        campaign_id=directory.name,
                        mode=None,
                        stage=step["id"],
                        status="started",
                        detail="",
                    )
                    observed = execute_command(step)
                    log = (
                        f"{len(checkpoint['records']):04d}-{step['id']}-{secrets.token_hex(4)}.json"
                    )
                    save(directory / log, observed, new=True)
                    ok = observed["assertions_passed"]
                    checkpoint["records"].append(
                        {
                            "step": step["id"],
                            "phase": step["phase"],
                            "status": "pass" if ok else "unexpected_failure",
                            "log": log,
                            "sha256": file_sha(directory / log),
                        }
                    )
                    if not ok:
                        status = (
                            "abort"
                            if observed["cancelled"]
                            else "cleanup_failure"
                            if step["phase"] == "cleanup" or not observed["cleanup_verified"]
                            else "blocked_prerequisite"
                            if step["phase"] == "inspect"
                            and not checkpoint["unverified_components"]
                            else "unexpected_failure"
                        )
                        save(directory / "checkpoint.json", checkpoint)
                        break
                    checkpoint["completed"].append(step["id"])
                    if step["phase"] == "publish":
                        checkpoint["published_components"] = sorted(
                            set(checkpoint["published_components"]) | {step["component"]}
                        )
                    checkpoint["pending"] = None
                    # An effect remains a recovery duty until its independent check.
                    if step["phase"] in {"verify", "public_acceptance"}:
                        checkpoint["unverified_components"] = [
                            c for c in checkpoint["unverified_components"] if c != step["component"]
                        ]
                        checkpoint["recovery_required"] = bool(checkpoint["unverified_components"])
                    save(directory / "checkpoint.json", checkpoint)
                    progress.emit(
                        campaign_id=directory.name,
                        mode=None,
                        stage=step["id"],
                        status="completed",
                        detail="",
                    )
                else:
                    status = "blocked_prerequisite" if plan["blockers"] else "pass"
            except KeyboardInterrupt:
                status = "abort"
                checkpoint["recovery_required"] = True
                save(directory / "checkpoint.json", checkpoint)
            except Exception as error:
                controller_error = str(error)
                status = (
                    "unexpected_failure"
                    if checkpoint["touched_components"]
                    else "blocked_prerequisite"
                )
                checkpoint["recovery_required"] = bool(checkpoint["touched_components"])
                save(directory / "checkpoint.json", checkpoint)
            if status != "pass":
                for cleanup_step in request["steps"]:
                    if (
                        cleanup_step["phase"] != "cleanup"
                        or cleanup_step["id"] in checkpoint["completed"]
                        or not set(cleanup_step["mutates"]).intersection(
                            checkpoint["touched_components"]
                        )
                    ):
                        continue
                    checkpoint["pending"] = cleanup_step["id"]
                    checkpoint["unverified_components"] = sorted(
                        set(checkpoint["unverified_components"]) | set(cleanup_step["mutates"])
                    )
                    checkpoint["recovery_required"] = True
                    save(directory / "checkpoint.json", checkpoint)
                    try:
                        cleanup_observed = execute_command(cleanup_step)
                        log = f"{len(checkpoint['records']):04d}-{cleanup_step['id']}-"
                        log += secrets.token_hex(4) + ".json"
                        save(directory / log, cleanup_observed, new=True)
                        ok = cleanup_observed["assertions_passed"]
                        checkpoint["records"].append(
                            {
                                "step": cleanup_step["id"],
                                "phase": "cleanup",
                                "status": "pass" if ok else "cleanup_failure",
                                "log": log,
                                "sha256": file_sha(directory / log),
                            }
                        )
                        if ok:
                            checkpoint["completed"].append(cleanup_step["id"])
                        else:
                            status = "cleanup_failure"
                    except (OSError, ValueError):
                        status = "cleanup_failure"
                    save(directory / "checkpoint.json", checkpoint)
            checked = {
                s["check"]
                for s in request["steps"]
                if s["id"] in checkpoint["completed"]
                and s["phase"] in {"inspect", "verify", "public_acceptance"}
            }
            missing = sorted(set(request["required_checks"]) - checked)
            if status == "pass" and (missing or checkpoint["recovery_required"]):
                status = "inconclusive"
            controller_cleanup = lease.release()
            if controller_cleanup:
                status = "cleanup_failure"
                checkpoint["recovery_required"] = True
                save(directory / "checkpoint.json", checkpoint)
            result = {
                "schema_version": 1,
                "controller_cleanup": controller_cleanup,
                "controller_error": controller_error,
                "blockers": plan["blockers"],
                "kind": "lifecycle-result",
                "plan_sha256": plan["plan_sha256"],
                "status": status,
                "completed": checkpoint["completed"],
                "missing_checks": missing,
                "recovery_required": checkpoint["recovery_required"],
                "published_components": checkpoint["published_components"],
                "records": checkpoint["records"],
            }
            validate(result, "lifecycle-result")
            save(directory / "result.json", result)
            progress.emit(
                campaign_id=directory.name,
                mode=None,
                stage="terminal",
                status="completed" if status == "pass" else "failed",
                detail=status,
            )
            return result


def report(directory: Path) -> dict[str, Any]:
    plan = load(directory / "plan.json")
    # Review old evidence without requiring current source bytes to remain present.
    validate(plan, "lifecycle-plan")
    if digest({k: v for k, v in plan.items() if k != "plan_sha256"}) != plan["plan_sha256"]:
        raise LifecycleError("plan digest mismatch")
    result = load(directory / "result.json")
    validate(result, "lifecycle-result")
    if result["plan_sha256"] != plan["plan_sha256"]:
        raise LifecycleError("result plan mismatch")
    proved = record_proofs(plan, directory, result["records"])
    if not set(result["completed"]).issubset(proved):
        raise LifecycleError("completed step lacks successful execution evidence")
    missing = sorted(
        set(plan["request"]["required_checks"])
        - {
            s["check"]
            for s in plan["request"]["steps"]
            if s["id"] in result["completed"]
            and s["phase"] in {"inspect", "verify", "public_acceptance"}
        }
    )
    if missing != result["missing_checks"]:
        raise LifecycleError("missing-check list contradicts completed verification")
    if result.get("controller_cleanup") and result["status"] != "cleanup_failure":
        raise LifecycleError("controller cleanup failure contradicts result status")
    if result["status"] == "pass" and (
        result["missing_checks"]
        or result["recovery_required"]
        or result["completed"] != [s["id"] for s in plan["request"]["steps"]]
    ):
        raise LifecycleError("pass contradicts required checks or recovery duties")
    return result
