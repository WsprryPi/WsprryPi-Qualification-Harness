"""Durable prepare/test and separately enabled, exact-byte release continuation.

The portable core owns retained identities/checkpoints. Owning build and public
consumer mechanics live in prepared_release_adapters; no RF authority is inferred.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

from wsprrypi_qualification import lifecycle as lc

REPOSITORIES = {
    "pi": "WsprryPi/WsprryPi",
    "pico": "WsprryPi/WsprryPico",
    "rp1": "WsprryPi/RP1-GPIO-CLK-DKMS",
}
ORDER = ("pico", "rp1", "pi")


class ReleaseAdapter(Protocol):
    def prepare(self, name: str, spec: dict[str, Any], root: Path) -> dict[str, Any]: ...

    def inspect_public(self, packet: dict[str, Any], root: Path) -> dict[str, Any]: ...

    def publish(self, packet: dict[str, Any], root: Path) -> dict[str, Any]: ...

    def accept(self, packet: dict[str, Any], root: Path) -> dict[str, Any]: ...


def require(value: Any, message: str) -> None:
    if not value:
        raise lc.LifecycleError(message)


def regular(path: Path) -> Path:
    require(
        path.is_absolute() and path.is_file() and not path.is_symlink(),
        f"missing, changed or substituted retained file: {path}",
    )
    require(not any(p.is_symlink() for p in path.parents), f"symlink retained parent: {path}")
    return path


def inventory(directory: Path) -> dict[str, dict[str, Any]]:
    require(
        directory.is_absolute()
        and directory.resolve() == directory
        and directory.is_dir()
        and not directory.is_symlink(),
        f"missing or substituted packet: {directory}",
    )
    result = {}
    for path in sorted(directory.rglob("*")):
        require(not path.is_symlink(), f"symlink in retained packet: {path}")
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = {
                "bytes": path.stat().st_size,
                "sha256": lc.file_sha(path),
            }
    require(result, f"empty retained packet: {directory}")
    return result


def git(path: Path, *args: str) -> str:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.check_output(
        ["git", "-c", "core.fsmonitor=false", "-C", str(path), *args],
        text=True,
        timeout=60,
        env=environment,
    ).strip()


def source(path: Path, role: str) -> dict[str, Any]:
    require(
        path.is_absolute() and path.resolve() == path and path.is_dir(),
        f"absolute, unsubstituted source root required: {path}",
    )
    require(
        Path(git(path, "rev-parse", "--show-toplevel")).resolve() == path,
        "source must name a complete repository root",
    )
    require(
        not git(path, "status", "--porcelain=v1", "--untracked-files=all"),
        "release source has existing changes; preserve it and use a clean worktree",
    )
    require(
        all(line.startswith("H ") for line in git(path, "ls-files", "-v").splitlines()),
        "source uses unsupported index flags",
    )
    remote = git(path, "remote", "get-url", "origin").removesuffix(".git")
    expected = REPOSITORIES[role]
    require(
        remote in {"https://github.com/" + expected, "git@github.com:" + expected},
        f"source origin is not the owning repository: {expected}",
    )
    return {
        "path": str(path),
        "repository": expected,
        "commit": git(path, "rev-parse", "HEAD"),
        "tree": git(path, "rev-parse", "HEAD^{tree}"),
        "branch": git(path, "branch", "--show-current"),
    }


def source_files(path: Path) -> dict[str, str]:
    names = (
        subprocess.check_output(
            ["git", "-c", "core.fsmonitor=false", "-C", str(path), "ls-files", "--stage", "-z"],
            timeout=60,
        )
        .decode()
        .split("\0")
    )
    result = {}
    for record in names:
        if not record:
            continue
        meta, name = record.split("\t", 1)
        mode, oid, _ = meta.split()
        item = path / name
        if mode == "160000":
            result["@gitlink/" + name] = oid
            if (item / ".git").exists():
                require(git(item, "rev-parse", "HEAD") == oid, "frozen submodule source changed")
                result.update({name + "/" + k: v for k, v in source_files(item).items()})
        else:
            require(
                item.is_file() and not item.is_symlink(),
                "missing or substituted source file: " + name,
            )
            result[name] = lc.file_sha(item)
    return result


def tools(request: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for name in ("git", "python", "gh", "docker"):
        argv = request.get("tools", {}).get(name, [sys.executable if name == "python" else name])
        executable = shutil.which(argv[0])
        if executable is None:
            result[name] = {"argv": argv, "available": False}
            continue
        argv = [str(Path(executable).absolute()), *argv[1:]]
        inputs = {
            str(Path(value).resolve()): lc.file_sha(Path(value))
            for value in argv
            if Path(value).is_absolute() and Path(value).is_file()
        }
        config = Path(argv[0]).parent.parent / "pyvenv.cfg"
        if config.is_file():
            inputs[str(config.resolve())] = lc.file_sha(config)
        result[name] = {
            "argv": argv,
            "available": True,
            "inputs": inputs,
            "resolved_executable": str(Path(argv[0]).resolve()),
        }
    return result


def controller() -> dict[str, str]:
    root = Path(__file__).parent
    names = ("prepared_release.py", "prepared_release_adapters.py", "prepared_release_cli.py")
    paths = [root / name for name in names]
    paths += sorted((root / "schemas").glob("prepared-release-*.schema.json"))
    return {str(path): lc.file_sha(path) for path in paths}


def mechanism(name: str) -> dict[str, Any]:
    return {
        "pi": {
            "prepare": "container_build.py --release-version; no tag required",
            "tag_creation": "publication only; new annotated tag or verified existing tag",
            "public_effects": (
                "explicit GitHub draft/assets/publication; tag alone is not a publisher"
            ),
        },
        "rp1": {
            "prepare": "build-release-candidate --release; no tag required; published:false",
            "tag_creation": "publication only; new annotated tag or verified existing tag",
            "public_effects": "explicit ordinary GitHub release and seven assets",
        },
        "pico": {
            "prepare": "software-build.yml publish=false and exact canonical run retrieval",
            "tag_creation": (
                "CI tags at publication; tagged builds require their exact existing tag"
            ),
            "public_effects": (
                "devel/v* pushes verify; manual trusted publisher publishes; never move a tag"
            ),
        },
    }[name]


def plan(request: dict[str, Any], state_root: Path) -> dict[str, Any]:
    lc.validate(request, "prepared-release-request")
    state_root = Path(os.path.abspath(state_root))
    require(not state_root.exists(), "new external retained-state directory required")
    require(
        not any(p.is_symlink() for p in state_root.parents), "retained-state parent is a symlink"
    )
    components = request["components"]
    scope = request["scope"]
    require(scope == "system" or scope in components, "selected independent component is missing")
    require(
        scope != "system" or set(components) == set(REPOSITORIES),
        "system preparation requires all three component identities",
    )
    require(
        all(name == scope or not spec["publish"] for name, spec in components.items())
        if scope != "system"
        else True,
        "independent preparation cannot publish companions",
    )
    sources = {}
    inputs: dict[str, Any] = {}
    for name, spec in components.items():
        sources[name] = source(Path(spec["source"]), name)
        for field, role in (("tooling_source", name), ("consumer_source", "pi")):
            if spec.get(field):
                sources[name + ":" + field] = source(Path(spec[field]), role)
        require(spec["tag"] == "v" + spec["version"], f"version/tag disagreement: {name}")
        require(
            re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", spec["version"]
            ),
            f"invalid release version: {name}",
        )
        for field in ("exports", "candidate", "packet"):
            value = spec.get(field)
            if isinstance(value, str):
                inputs[name + ":" + field] = {"path": value, "files": inventory(Path(value))}
            elif isinstance(value, dict):
                for target, directory in value.items():
                    inputs[name + ":" + field + ":" + target] = {
                        "path": directory,
                        "files": inventory(Path(directory)),
                    }
    lc._outside(state_root, [v["path"] for v in sources.values()])
    result = {
        "schema_version": 1,
        "kind": "prepared-release-execution-plan",
        "state_root": str(state_root),
        "request": request,
        "sources": sources,
        "retained_inputs": inputs,
        "mechanics": {name: mechanism(name) for name in components},
        "publication_enabled": False,
        "tools": tools(request),
        "controller": controller(),
    }
    result["plan_sha256"] = lc.digest(result)
    return result


def check_plan(value: dict[str, Any]) -> None:
    lc.validate(value, "prepared-release-plan")
    require(
        value.get("plan_sha256")
        == lc.digest({k: v for k, v in value.items() if k != "plan_sha256"}),
        "release preparation plan changed",
    )
    require(value.get("publication_enabled") is False, "preparation cannot select publication")
    require(tools(value["request"]) == value["tools"], "preparation tool identity changed")
    require(controller() == value["controller"], "preparation controller changed")
    for key, identity in value["sources"].items():
        require(
            source(
                Path(identity["path"]), key.split(":")[0] if ":consumer_source" not in key else "pi"
            )
            == identity,
            "source changed; return to preparation: " + key,
        )
    for key, item in value["retained_inputs"].items():
        require(
            inventory(Path(item["path"])) == item["files"],
            "preparation artifact changed; return to preparation: " + key,
        )


def freeze_source(identity: dict[str, Any], destination: Path) -> dict[str, Any]:
    subprocess.run(
        ["git", "clone", "--no-local", "--no-checkout", identity["path"], str(destination)],
        check=True,
        capture_output=True,
        timeout=180,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "remote",
            "set-url",
            "origin",
            "https://github.com/" + identity["repository"] + ".git",
        ],
        check=True,
        timeout=30,
    )
    args = (
        ["checkout", "-B", identity["branch"], identity["commit"]]
        if identity["branch"]
        else ["checkout", "--detach", identity["commit"]]
    )
    subprocess.run(
        ["git", "-C", str(destination), *args], check=True, capture_output=True, timeout=60
    )
    return {
        **identity,
        "original_path": identity["path"],
        "path": str(destination),
        "files": source_files(destination),
    }


def check_dependencies(request: dict[str, Any], packets: dict[str, Any]) -> None:
    selections = request.get("pi_dependencies", {})
    if "pi" not in packets:
        require(not selections, "Pi dependency selections require a selected Pi packet")
        return
    require(set(selections) == {"pico", "rp1"}, "Pi must fix both companion selections")
    require(
        packets["pi"].get("ci_pico_source") == selections["pico"]["source"],
        "Pi canonical CI tested another Pico selection",
    )
    require(
        packets["pi"].get("default_rp1_tag") == "v" + selections["rp1"]["version"],
        "Pi authoritative RP1 selection differs from the prepared provider",
    )
    require(
        packets["rp1"].get("consumer_source_commit") == packets["pi"]["source"],
        "RP1 validation used another Pi consumer source; return to preparation",
    )
    for name, selected in selections.items():
        require(name in packets, "Pi requires retained fixed or prepared companion: " + name)
        candidate = packets[name]
        require(
            selected == {"source": candidate["source"], "version": candidate["version"]},
            "incompatible dependency selection; return to preparation: " + name,
        )


def prepare(value: dict[str, Any], confirmed: str, adapter: ReleaseAdapter) -> dict[str, Any]:
    require(confirmed == value.get("plan_sha256"), "preparation digest not confirmed")
    check_plan(value)
    root = Path(value["state_root"])
    require(not root.exists(), "retained state already exists; inspect it rather than overwrite")
    root.mkdir(mode=0o700, parents=True)
    lc.save(root / "preparation-plan.json", value, new=True)
    (root / "sources").mkdir(mode=0o700)
    frozen = {}
    state: dict[str, Any] = {
        "schema_version": 1,
        "kind": "prepared-release-state",
        "phase": "preparing",
        "prepared_sha256": None,
        "components": {},
        "pending": None,
        "errors": [],
    }
    lc.save(root / "state.json", state, new=True)
    try:
        for key, identity in value["sources"].items():
            frozen[key] = freeze_source(identity, root / "sources" / key.replace(":", "-"))
        request = json.loads(json.dumps(value["request"]))
        packets = {}
        for name in ORDER:
            if name not in request["components"]:
                continue
            spec = request["components"][name]
            spec["source"] = frozen[name]["path"]
            for field in ("tooling_source", "consumer_source"):
                if name + ":" + field in frozen:
                    spec[field] = frozen[name + ":" + field]["path"]
            packet = adapter.prepare(name, spec, root)
            require(
                packet["source"] == frozen[name]["commit"]
                and packet["version"] == spec["version"]
                and packet["tag"] == spec["tag"],
                "owning preparation returned another source/version",
            )
            packet["files"] = inventory(Path(packet["directory"]))
            packet["packet_sha256"] = lc.digest(packet["files"])
            packet["publish_selected"] = spec["publish"]
            require(
                packet.get("validation", {}).get("status") == "PASS",
                "owning validation did not pass",
            )
            if not spec["publish"]:
                observed = adapter.inspect_public(packet, root)
                require(
                    observed.get("state") == "published",
                    "fixed companion is not publicly available",
                )
                packet["fixed_public"] = observed
            packets[name] = packet
        check_dependencies(request, packets)
        for identity in frozen.values():
            identity["files"] = source_files(Path(identity["path"]))
        sealed = {
            "schema_version": 1,
            "kind": "prepared-release",
            "scope": request["scope"],
            "request": request,
            "sources": frozen,
            "components": packets,
            "mechanics": value["mechanics"],
            "publication_enabled": False,
            "tools": value["tools"],
            "controller": value["controller"],
        }
        # Validation and build evidence are sealed independently from mutable continuation state.
        evidence = root / "evidence"
        sealed["evidence"] = inventory(evidence) if evidence.exists() else {}
        sealed["prepared_sha256"] = lc.digest(sealed)
        lc.save(root / "prepared.json", sealed, new=True)
        state.update(phase="prepared", prepared_sha256=sealed["prepared_sha256"])
        lc.save(root / "state.json", state)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        state.update(phase="not_ready", errors=[str(error)])
        lc.save(root / "state.json", state)
        return {
            "status": "not_ready",
            "state_root": str(root),
            "errors": [str(error)],
            "publication_performed": False,
        }
    return status(root)


def verify_retained(root: Path) -> dict[str, Any]:
    regular(root / "prepared.json")
    value = lc.load(root / "prepared.json")
    lc.validate(value, "prepared-release-prepared")
    require(
        value.get("prepared_sha256")
        == lc.digest({k: v for k, v in value.items() if k != "prepared_sha256"}),
        "prepared release plan changed; return to preparation",
    )
    require(
        value.get("publication_enabled") is False, "prepared plan contains publication authority"
    )
    require(controller() == value["controller"], "controller changed; return to preparation")
    require(
        tools(value["request"]) == value["tools"], "external tool changed; return to preparation"
    )
    for key, identity in value["sources"].items():
        role = "pi" if key.endswith(":consumer_source") else key.split(":")[0]
        actual = source(Path(identity["path"]), role)
        require(all(actual[k] == identity[k] for k in actual), "frozen source changed: " + key)
        require(
            source_files(Path(identity["path"])) == identity["files"],
            "frozen source bytes changed: " + key,
        )
    for name, packet in value["components"].items():
        require(
            inventory(Path(packet["directory"])) == packet["files"],
            "prepared artifact changed or missing; return to preparation: " + name,
        )
        require(lc.digest(packet["files"]) == packet["packet_sha256"], "packet digest differs")
        if "provenance_directory" in packet:
            require(
                inventory(Path(packet["provenance_directory"])) == packet["build_provenance"],
                "required build/test provenance changed or missing: " + name,
            )
    evidence = root / "evidence"
    require(
        (inventory(evidence) if evidence.exists() else {}) == value["evidence"],
        "required validation/provenance changed; return to preparation",
    )
    check_dependencies(value["request"], value["components"])
    return value


def status(root: Path) -> dict[str, Any]:
    state = lc.load(regular(root / "state.json"))
    lc.validate(state, "prepared-release-state")
    try:
        prepared = verify_retained(root)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {
            "status": "invalidated" if (root / "prepared.json").exists() else state["phase"],
            "ready_for_publication": False,
            "errors": [str(error), *state.get("errors", [])],
            "state_root": str(root),
            "publication": state.get("components", {}),
        }
    return {
        "status": state["phase"],
        "readiness_scope": "retained local artifacts; run --validate-only for current public gates",
        "ready_for_publication": False,
        "retained_artifacts_ready": state["phase"]
        in {"prepared", "partially_published", "acceptance_pending", "complete"},
        "prepared_sha256": prepared["prepared_sha256"],
        "state_root": str(root),
        "publication": state["components"],
        "pending": state["pending"],
        "errors": state["errors"],
        "publication_performed": False,
    }


def prior_acceptance(
    state: dict[str, Any], packet: dict[str, Any], observed: dict[str, Any]
) -> dict[str, Any] | None:
    previous = state["components"].get(packet["component"], {})
    accepted = previous.get("acceptance")
    if (
        isinstance(accepted, dict)
        and accepted.get("status") == "PASS"
        and accepted.get("packet_sha256") == packet["packet_sha256"]
        and previous.get("acceptance_sha256") == lc.digest(accepted)
        and previous.get("publication") == observed
    ):
        return accepted
    return None


def _publish(
    root: Path,
    confirmed: str,
    adapter: ReleaseAdapter,
    *,
    enable_publication: bool = False,
    validate_only: bool = False,
    resume: bool = False,
) -> dict[str, Any]:
    require(
        enable_publication != validate_only,
        "select either explicit publication enablement or read-only validation",
    )
    value = verify_retained(root)
    require(confirmed == value["prepared_sha256"], "prepared release digest not confirmed")
    state = lc.load(regular(root / "state.json"))
    lc.validate(state, "prepared-release-state")
    require(
        state["prepared_sha256"] == confirmed,
        "continuation state belongs to another prepared release",
    )
    require(
        validate_only or resume or state["pending"] is None,
        "interrupted publication requires --resume and actual-state inspection",
    )
    observations = {}
    # Check every retained packet and every fixed companion before any public effect.
    for name in ORDER:
        if name not in value["components"]:
            continue
        packet = value["components"][name]
        observed = adapter.inspect_public(packet, root)
        require(
            observed.get("state") in {"absent", "draft", "published", "publication_pending"},
            "ambiguous public state",
        )
        require(
            packet["publish_selected"] or observed["state"] == "published",
            "fixed companion became unavailable",
        )
        preflight = getattr(adapter, "preflight", None)
        if preflight is not None:
            preflight(packet, root, observed)
        observations[name] = observed
    consumer_checks = {}
    for name, observed in observations.items():
        if observed["state"] == "published":
            packet = value["components"][name]
            checked = prior_acceptance(state, packet, observed) or adapter.accept(packet, root)
            require(
                checked.get("status") == "PASS"
                and checked.get("packet_sha256") == packet["packet_sha256"],
                "public consumer acceptance did not pass: " + name,
            )
            consumer_checks[name] = checked
    if validate_only:
        return {
            "status": "validated",
            "prepared_sha256": confirmed,
            "public_state": observations,
            "consumer_checks": consumer_checks,
            "publication_performed": False,
            "remaining": [n for n, o in observations.items() if o["state"] != "published"],
        }
    try:
        for name in ORDER:
            if name not in value["components"]:
                continue
            packet = value["components"][name]
            observed = observations[name]
            if observed["state"] != "published":
                require(packet["publish_selected"], "publication was not selected: " + name)
                state.update(
                    phase="partially_published", pending={"component": name, "operation": "publish"}
                )
                lc.save(root / "state.json", state)
                observed = adapter.publish(packet, root)
                require(
                    observed.get("state") == "published", "owning publication remains incomplete"
                )
            if prior_acceptance(state, packet, observed) is not None:
                continue
            state["components"][name] = {"publication": observed, "acceptance": "pending"}
            state.update(pending=None, phase="acceptance_pending")
            lc.save(root / "state.json", state)
            verified = consumer_checks.get(name) or adapter.accept(packet, root)
            require(
                verified.get("status") == "PASS"
                and verified.get("packet_sha256") == packet["packet_sha256"],
                "public consumer acceptance did not pass: " + name,
            )
            state["components"][name]["acceptance"] = verified
            state["components"][name]["acceptance_sha256"] = lc.digest(verified)
            lc.save(root / "state.json", state)
        state.update(phase="complete", pending=None, errors=[])
        lc.save(root / "state.json", state)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        state["errors"] = [str(error)]
        lc.save(root / "state.json", state)
        return {
            "status": state["phase"],
            "prepared_sha256": confirmed,
            "publication": state["components"],
            "pending": state["pending"],
            "errors": state["errors"],
        }
    return {
        "status": "complete",
        "prepared_sha256": confirmed,
        "publication": state["components"],
        "pending": None,
        "errors": [],
    }


def publish(
    root: Path,
    confirmed: str,
    adapter: ReleaseAdapter,
    *,
    enable_publication: bool = False,
    validate_only: bool = False,
    resume: bool = False,
) -> dict[str, Any]:
    lock_root = lc.controller_lock_root()
    lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lc.ownership(lock_root, ["prepared-release-publication", "prepared-release:" + str(root)]):
        return _publish(
            root,
            confirmed,
            adapter,
            enable_publication=enable_publication,
            validate_only=validate_only,
            resume=resume,
        )
