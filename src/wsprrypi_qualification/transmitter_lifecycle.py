"""Sealed hardware-free transmitter lifecycle and evidence transaction."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from wsprrypi_qualification.application_shims import validate_application_plan
from wsprrypi_qualification.manifests import build_manifest, render_manifest, write_manifest
from wsprrypi_qualification.offline import validate_document, write_json_new


class TransmitterLifecycleError(RuntimeError):
    """A no-qualification transmitter lifecycle invariant failed."""


class TransmitterInjection(StrEnum):
    NONE = "none"
    CAPABILITY = "missing_capability"
    HOST = "wrong_host"
    HELPER = "helper_mismatch"
    OWNERSHIP = "ownership_conflict"
    RF_IDLE = "rf_idle_unverified"
    CLEANUP_REGISTRATION = "cleanup_registration_failed"
    LAUNCH = "launch_failed"
    NONZERO = "process_nonzero"
    TIMEOUT = "process_timeout"
    CANCEL = "process_cancelled"
    DISCONNECT = "transport_disconnected"
    PROCESS_LEAK = "process_leak"
    SERVICE_RESTORE = "service_restore_failed"
    QUIESCENCE = "quiescence_unverified"


@dataclass(frozen=True)
class ResolvedTransmitterLifecyclePlan:
    document: dict[str, Any]

    def validated(self) -> dict[str, Any]:
        validate_transmitter_plan(self.document)
        return self.document

    @property
    def sha256(self) -> str:
        return _digest(self.validated())


@dataclass(frozen=True)
class TransmitterRuntimeAuthorization:
    operator: str
    recorded_utc: datetime
    plan_sha256: str

    def document(self) -> dict[str, object]:
        result = {
            "schema_version": 1,
            "evidence_type": "transmitter_runtime_authorization",
            "operator": self.operator,
            "recorded_utc": _utc(self.recorded_utc),
            "plan_sha256": self.plan_sha256,
            "external_access_authorized": True,
            "rf_authorized": False,
            "scope": "hardware_free_no_qualification",
        }
        validate_document(result, "transmitter-runtime-authorization.schema.json")
        return result


class SealedFakeTransmitterAdapters:
    """No subprocess, network, service, GPIO, I2C, or RF access is possible."""

    __slots__ = ("injection",)

    def __init__(self, injection: TransmitterInjection = TransmitterInjection.NONE) -> None:
        if type(self) is not SealedFakeTransmitterAdapters:
            raise TypeError("transmitter fake adapter is sealed")
        self.injection = injection

    def preflight(self, name: str, plan: dict[str, Any], digest: str) -> dict[str, object]:
        mapping = {
            "capabilities": TransmitterInjection.CAPABILITY,
            "host": TransmitterInjection.HOST,
            "helper": TransmitterInjection.HELPER,
            "ownership": TransmitterInjection.OWNERSHIP,
            "rf_idle": TransmitterInjection.RF_IDLE,
            "cleanup_registration": TransmitterInjection.CLEANUP_REGISTRATION,
        }
        passed = mapping[name] is not self.injection
        details = _expected_preflight(name, plan, passed)
        return _stage(
            name, digest, plan["deadlines"]["helper_s"], "passed" if passed else "blocked", details
        )

    def launch_and_wait(self, plan: dict[str, Any], digest: str) -> dict[str, object]:
        injection = self.injection
        outcome = {
            TransmitterInjection.LAUNCH: "launch_failed",
            TransmitterInjection.NONZERO: "nonzero_exit",
            TransmitterInjection.TIMEOUT: "timed_out",
            TransmitterInjection.CANCEL: "cancelled",
            TransmitterInjection.DISCONNECT: "disconnected",
        }.get(injection, "completed")
        return_code = (
            1
            if injection is TransmitterInjection.NONZERO
            else 0
            if outcome == "completed"
            else None
        )
        return {
            "schema_version": 1,
            "evidence_type": "transmitter_owned_process",
            "plan_sha256": digest,
            "application_plan": plan["application_plan"],
            "arguments": plan["application_plan"]["arguments"],
            "handle_id": None if outcome == "launch_failed" else "fake-owned-wsprrypi-1",
            "ownership_recorded_before_wait": outcome != "launch_failed",
            "deadline_s": plan["deadlines"]["transmitter_s"],
            "return_code": return_code,
            "stdout": "sealed hardware-free fixture",
            "stderr": "",
            "timed_out": outcome == "timed_out",
            "cancelled": outcome == "cancelled",
            "disconnected": outcome == "disconnected",
            "output_enabled": False,
            "rf_emitted": False,
            "outcome": outcome,
        }

    def cleanup(
        self, plan: dict[str, Any], digest: str, handle_id: str | None
    ) -> dict[str, object]:
        process_absent = self.injection is not TransmitterInjection.PROCESS_LEAK
        service_restored = self.injection is not TransmitterInjection.SERVICE_RESTORE
        quiescent = self.injection is not TransmitterInjection.QUIESCENCE
        verified = process_absent and service_restored and quiescent
        return {
            "schema_version": 1,
            "evidence_type": "transmitter_cleanup",
            "plan_sha256": digest,
            "handle_id": handle_id,
            "process_absent": process_absent,
            "helper_absent": True,
            "services": [
                {
                    "name": policy["name"],
                    "initial_running": policy["initial_running"],
                    "changed_by_harness": policy["change_for_exercise"],
                    "restored_running": not policy["initial_running"]
                    if not service_restored and policy["change_for_exercise"]
                    else policy["initial_running"],
                    "restoration_verified": service_restored or not policy["change_for_exercise"],
                }
                for policy in plan["services"]
            ],
            "quiescence": {
                "backend": plan["backend"],
                "verified": quiescent,
                "read_only_fixture": True,
                "gpio_direction": "input" if plan["backend"] == "gpio" and quiescent else None,
                "si5351_enabled_outputs": [] if plan["backend"] == "si5351" and quiescent else None,
            },
            "cleanup_verified": verified,
            "outcome": "verified" if verified else "failed",
        }


class TransmitterLifecycleSession:
    """Single-use hardware-free lifecycle that can never qualify a transmitter."""

    def __init__(
        self,
        plan: ResolvedTransmitterLifecyclePlan,
        adapters: SealedFakeTransmitterAdapters,
        *,
        now: datetime,
    ) -> None:
        if type(adapters) is not SealedFakeTransmitterAdapters:
            raise TypeError("only the sealed transmitter fixture is accepted")
        self.plan, self.adapters, self.now = plan, adapters, now
        self._used = False

    def run(
        self, authorization: TransmitterRuntimeAuthorization | None, output_parent: Path
    ) -> dict[str, Any]:
        if self._used:
            raise TransmitterLifecycleError("transmitter lifecycle sessions are single-use")
        self._used = True
        plan = self.plan.validated()
        digest = self.plan.sha256
        if authorization is None:
            raise TransmitterLifecycleError("ephemeral runtime authorization is required")
        auth = authorization.document()
        if auth["plan_sha256"] != digest:
            raise TransmitterLifecycleError("runtime authorization does not bind the plan")
        started = self.now.astimezone(UTC)
        recorded = authorization.recorded_utc.astimezone(UTC)
        age = (started - recorded).total_seconds()
        if age < 0 or age > plan["deadlines"]["overall_s"]:
            raise TransmitterLifecycleError("runtime authorization is stale or future-dated")
        parent = output_parent.resolve()
        final = parent / plan["run_id"]
        temporary = parent / f".incomplete-{plan['run_id']}"
        if (
            final.parent != parent
            or temporary.parent != parent
            or final.exists()
            or temporary.exists()
        ):
            raise TransmitterLifecycleError("unsafe or reused evidence destination")
        parent.mkdir(parents=True, exist_ok=True)
        temporary.mkdir()
        stages: dict[str, dict[str, object]] = {}
        events: list[dict[str, object]] = []
        process: dict[str, object] | None = None
        cleanup: dict[str, object] | None = None
        cleanup_registered = False

        def event(phase: str, outcome: str) -> None:
            events.append({"sequence": len(events) + 1, "phase": phase, "outcome": outcome})

        event("requested", "recorded")
        status = "inconclusive"
        try:
            event("validated", "passed")
            for name in (
                "capabilities",
                "host",
                "helper",
                "ownership",
                "rf_idle",
                "cleanup_registration",
            ):
                if name == "cleanup_registration":
                    cleanup_registered = True
                stage = self.adapters.preflight(name, plan, digest)
                stages[name] = stage
                event(name, str(stage["outcome"]))
                if stage["outcome"] != "passed":
                    status = "fixture_blocked"
                    break
            if status == "inconclusive":
                process = self.adapters.launch_and_wait(plan, digest)
                event("process_attempt", str(process["outcome"]))
                if process["outcome"] != "completed":
                    status = "aborted"
        finally:
            if cleanup_registered:
                cleanup = self.adapters.cleanup(
                    plan,
                    digest,
                    str(process["handle_id"])
                    if process and process["handle_id"] is not None
                    else None,
                )
                event("cleanup", str(cleanup["outcome"]))
                quiescence = cleanup["quiescence"]
                assert isinstance(quiescence, dict)
                event("quiescence", "verified" if quiescence["verified"] else "failed")
                if not cleanup["cleanup_verified"]:
                    status = "cleanup_failed"
        causes = _derive_causes(stages, process, cleanup)
        session = {
            "schema_version": 1,
            "evidence_type": "transmitter_lifecycle_session",
            "run_id": plan["run_id"],
            "plan_sha256": digest,
            "started_utc": _utc(started),
            "authorization": auth,
            "events": events,
            "stages": stages,
            "process": process,
            "cleanup": cleanup,
            "failure_causes": causes,
            "final_status": status,
            "qualification_claim": False,
            "rf_emitted": False,
        }
        validate_transmitter_session(session, plan)
        result = {
            "schema_version": 1,
            "evidence_type": "transmitter_lifecycle_result",
            "run_id": plan["run_id"],
            "status": status,
            "failure_causes": causes,
            "cleanup_outcome": "verified"
            if cleanup and cleanup["cleanup_verified"]
            else "failed"
            if cleanup
            else "not_required",
            "qualification_claim": False,
            "rf_emitted": False,
        }
        validate_document(result, "transmitter-lifecycle-result.schema.json")
        try:
            write_json_new(
                temporary / "resolved-plan.json",
                plan,
                schema_name="resolved-transmitter-lifecycle-plan.schema.json",
            )
            write_json_new(
                temporary / "runtime-authorization.json",
                auth,
                schema_name="transmitter-runtime-authorization.schema.json",
            )
            write_json_new(
                temporary / "session.json",
                session,
                schema_name="transmitter-lifecycle-session.schema.json",
            )
            write_json_new(
                temporary / "result.json",
                result,
                schema_name="transmitter-lifecycle-result.schema.json",
            )
            write_manifest(temporary)
            temporary.replace(final)
            validate_transmitter_bundle(final)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            shutil.rmtree(final, ignore_errors=True)
            raise
        return {"bundle": str(final), "session": session, "result": result}


def validate_transmitter_plan(plan: dict[str, Any]) -> None:
    validate_document(plan, "resolved-transmitter-lifecycle-plan.schema.json")
    if plan["execution_mode"] != "hardware_free_validation" or plan["rf_authorized"]:
        raise TransmitterLifecycleError("live or RF transmitter lifecycle is unavailable")
    validate_application_plan(plan["application_plan"])
    if plan["application_plan"]["execution_authorized"]:
        raise TransmitterLifecycleError("profile-derived execution authorization is forbidden")
    if plan["application_plan"]["backend"] != plan["backend"]:
        raise TransmitterLifecycleError("application backend contradicts lifecycle backend")
    expected_bindings = {"ssh", "process", plan["backend"]}
    if set(plan["capability_bindings"]) != expected_bindings:
        raise TransmitterLifecycleError("capability bindings do not exactly match the backend")
    service_names = [item["name"] for item in plan["services"]]
    if len(service_names) != len(set(service_names)):
        raise TransmitterLifecycleError("service policy names must be unique")
    if plan["deadlines"]["transmitter_s"] > plan["deadlines"]["overall_s"]:
        raise TransmitterLifecycleError("transmitter deadline exceeds overall deadline")


def validate_transmitter_session(session: dict[str, Any], plan: dict[str, Any]) -> None:
    validate_document(session, "transmitter-lifecycle-session.schema.json")
    if session["run_id"] != plan["run_id"] or session["plan_sha256"] != _digest(plan):
        raise TransmitterLifecycleError("session identity contradicts plan")
    if session["authorization"]["plan_sha256"] != session["plan_sha256"]:
        raise TransmitterLifecycleError("authorization contradicts session")
    age = (
        _parse_utc(session["started_utc"]) - _parse_utc(session["authorization"]["recorded_utc"])
    ).total_seconds()
    if age < 0 or age > plan["deadlines"]["overall_s"]:
        raise TransmitterLifecycleError("retained authorization is stale or future-dated")
    if [item["sequence"] for item in session["events"]] != list(
        range(1, len(session["events"]) + 1)
    ):
        raise TransmitterLifecycleError("lifecycle sequence is not exact")
    stages = session["stages"]
    ordered = ("capabilities", "host", "helper", "ownership", "rf_idle", "cleanup_registration")
    reached: list[str] = []
    for name in ordered:
        if name not in stages:
            break
        reached.append(name)
        stage = stages[name]
        _require_keys(
            stage,
            {
                "schema_version",
                "evidence_type",
                "stage",
                "plan_sha256",
                "deadline_s",
                "elapsed_s",
                "outcome",
                "details",
            },
            f"{name} stage",
        )
        passed = stage["outcome"] == "passed"
        if (
            stage["stage"] != name
            or stage["plan_sha256"] != session["plan_sha256"]
            or stage["deadline_s"] != plan["deadlines"]["helper_s"]
            or not 0 <= stage["elapsed_s"] <= stage["deadline_s"]
            or stage["details"] != _expected_preflight(name, plan, passed)
        ):
            raise TransmitterLifecycleError(f"{name} stage contradicts the resolved plan")
    if set(stages) != set(reached) or any(
        stages[name]["outcome"] == "blocked" for name in reached[:-1]
    ):
        raise TransmitterLifecycleError("preflight stage set or order is contradictory")
    event_pairs = [(item["phase"], item["outcome"]) for item in session["events"]]
    expected_events = [("requested", "recorded"), ("validated", "passed")]
    expected_events.extend((name, str(stages[name]["outcome"])) for name in reached)
    process = session["process"]
    if process is not None:
        expected_events.append(("process_attempt", str(process["outcome"])))
    cleanup = session["cleanup"]
    if cleanup is not None:
        expected_events.extend(
            [
                ("cleanup", str(cleanup["outcome"])),
                (
                    "quiescence",
                    "verified" if cleanup["quiescence"]["verified"] else "failed",
                ),
            ]
        )
    if event_pairs != expected_events:
        raise TransmitterLifecycleError("lifecycle event sequence contradicts evidence")
    cleanup_index = next(
        (i for i, item in enumerate(event_pairs) if item[0] == "cleanup_registration"), None
    )
    process_index = next(
        (i for i, item in enumerate(event_pairs) if item[0] == "process_attempt"), None
    )
    if process_index is not None and (cleanup_index is None or cleanup_index >= process_index):
        raise TransmitterLifecycleError("cleanup was not registered before process launch")
    if process is not None:
        _require_keys(
            process,
            {
                "schema_version",
                "evidence_type",
                "plan_sha256",
                "application_plan",
                "arguments",
                "handle_id",
                "ownership_recorded_before_wait",
                "deadline_s",
                "return_code",
                "stdout",
                "stderr",
                "timed_out",
                "cancelled",
                "disconnected",
                "output_enabled",
                "rf_emitted",
                "outcome",
            },
            "owned process",
        )
        if reached != list(ordered) or any(stages[name]["outcome"] != "passed" for name in reached):
            raise TransmitterLifecycleError("process exists without complete passing preflight")
        if (
            process["arguments"] != plan["application_plan"]["arguments"]
            or process["deadline_s"] != plan["deadlines"]["transmitter_s"]
        ):
            raise TransmitterLifecycleError("owned process contradicts resolved application")
        if (
            process["ownership_recorded_before_wait"] != (process["handle_id"] is not None)
            or process["output_enabled"]
            or process["rf_emitted"]
        ):
            raise TransmitterLifecycleError(
                "hardware-free process evidence violates ownership or RF boundary"
            )
        process_semantics = {
            "completed": (0, False, False, False),
            "launch_failed": (None, False, False, False),
            "nonzero_exit": (1, False, False, False),
            "timed_out": (None, True, False, False),
            "cancelled": (None, False, True, False),
            "disconnected": (None, False, False, True),
        }
        if (
            process["outcome"] not in process_semantics
            or (
                process["return_code"],
                process["timed_out"],
                process["cancelled"],
                process["disconnected"],
            )
            != process_semantics[process["outcome"]]
        ):
            raise TransmitterLifecycleError("owned process outcome is contradictory")
        if (process["outcome"] == "launch_failed") != (process["handle_id"] is None):
            raise TransmitterLifecycleError("launch outcome contradicts process ownership")
    cleanup_expected = "cleanup_registration" in reached
    if (cleanup is not None) != cleanup_expected:
        raise TransmitterLifecycleError("cleanup presence contradicts registration attempt")
    if cleanup is not None:
        _require_keys(
            cleanup,
            {
                "schema_version",
                "evidence_type",
                "plan_sha256",
                "handle_id",
                "process_absent",
                "helper_absent",
                "services",
                "quiescence",
                "cleanup_verified",
                "outcome",
            },
            "cleanup",
        )
        expected_handle = process["handle_id"] if process else None
        quiescence = cleanup["quiescence"]
        _require_keys(
            quiescence,
            {
                "backend",
                "verified",
                "read_only_fixture",
                "gpio_direction",
                "si5351_enabled_outputs",
            },
            "quiescence",
        )
        expected_verified = (
            cleanup["process_absent"]
            and cleanup["helper_absent"]
            and all(item["restoration_verified"] for item in cleanup["services"])
            and quiescence["verified"]
        )
        if (
            cleanup["plan_sha256"] != session["plan_sha256"]
            or cleanup["handle_id"] != expected_handle
            or [item["name"] for item in cleanup["services"]]
            != [item["name"] for item in plan["services"]]
            or quiescence["backend"] != plan["backend"]
            or not quiescence["read_only_fixture"]
            or cleanup["cleanup_verified"] != expected_verified
            or cleanup["outcome"] != ("verified" if expected_verified else "failed")
        ):
            raise TransmitterLifecycleError("cleanup evidence is internally contradictory")
        for policy, item in zip(plan["services"], cleanup["services"], strict=True):
            _require_keys(
                item,
                {
                    "name",
                    "initial_running",
                    "changed_by_harness",
                    "restored_running",
                    "restoration_verified",
                },
                "service cleanup",
            )
            if (
                item["initial_running"] != policy["initial_running"]
                or item["changed_by_harness"] != policy["change_for_exercise"]
                or item["restoration_verified"]
                != (item["restored_running"] == item["initial_running"])
                or (
                    not item["changed_by_harness"]
                    and item["restored_running"] != item["initial_running"]
                )
            ):
                raise TransmitterLifecycleError("service restoration contradicts resolved policy")
        if plan["backend"] == "gpio" and (
            quiescence["gpio_direction"] != ("input" if quiescence["verified"] else None)
            or quiescence["si5351_enabled_outputs"] is not None
        ):
            raise TransmitterLifecycleError("GPIO quiescence evidence is contradictory")
        if plan["backend"] == "si5351" and (
            quiescence["si5351_enabled_outputs"] != ([] if quiescence["verified"] else None)
            or quiescence["gpio_direction"] is not None
        ):
            raise TransmitterLifecycleError("Si5351 quiescence evidence is contradictory")
    expected_causes = _derive_causes(stages, process, cleanup)
    if session["failure_causes"] != expected_causes:
        raise TransmitterLifecycleError("failure causes contradict retained evidence")
    expected_status = (
        "cleanup_failed"
        if cleanup is not None and not cleanup["cleanup_verified"]
        else "fixture_blocked"
        if any(stage["outcome"] == "blocked" for stage in session["stages"].values())
        else "aborted"
        if process is not None and process["outcome"] != "completed"
        else "inconclusive"
    )
    if (
        session["final_status"] != expected_status
        or session["qualification_claim"]
        or session["rf_emitted"]
    ):
        raise TransmitterLifecycleError("final status or claims contradict lifecycle evidence")


def validate_transmitter_bundle(root: Path) -> None:
    root = root.resolve(strict=True)
    plan = json.loads((root / "resolved-plan.json").read_text(encoding="utf-8"))
    validate_transmitter_plan(plan)
    auth = validate_document(
        json.loads((root / "runtime-authorization.json").read_text(encoding="utf-8")),
        "transmitter-runtime-authorization.schema.json",
    )
    session = json.loads((root / "session.json").read_text(encoding="utf-8"))
    validate_transmitter_session(session, plan)
    if auth != session["authorization"] or root.name != plan["run_id"]:
        raise TransmitterLifecycleError("bundle authorization or run identity changed")
    result = validate_document(
        json.loads((root / "result.json").read_text(encoding="utf-8")),
        "transmitter-lifecycle-result.schema.json",
    )
    expected = {
        "schema_version": 1,
        "evidence_type": "transmitter_lifecycle_result",
        "run_id": session["run_id"],
        "status": session["final_status"],
        "failure_causes": session["failure_causes"],
        "cleanup_outcome": "verified"
        if session["cleanup"] and session["cleanup"]["cleanup_verified"]
        else "failed"
        if session["cleanup"]
        else "not_required",
        "qualification_claim": False,
        "rf_emitted": False,
    }
    if result != expected:
        raise TransmitterLifecycleError("result contradicts session")
    if (root / "SHA256SUMS").read_text(encoding="utf-8") != render_manifest(build_manifest(root)):
        raise TransmitterLifecycleError("manifest does not authenticate bundle")
    expected_files = {
        "resolved-plan.json",
        "runtime-authorization.json",
        "session.json",
        "result.json",
        "SHA256SUMS",
    }
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual != expected_files or any(path.is_symlink() for path in root.rglob("*")):
        raise TransmitterLifecycleError("bundle file set is incomplete or unexpected")


def _expected_preflight(name: str, plan: dict[str, Any], passed: bool) -> dict[str, object]:
    base: dict[str, object] = {
        "hardware_access": False,
        "external_process_started": False,
        "verified": passed,
    }
    values = {
        "capabilities": {"bindings": plan["capability_bindings"]},
        "host": {"host": plan["host"]},
        "helper": {"helper": plan["remote_helper"]},
        "ownership": {"conflicts": [] if passed else ["fixture"]},
        "rf_idle": {"backend": plan["backend"], "quiescent": passed},
        "cleanup_registration": {
            "installed": passed,
            "before_launch": True,
            "stopping_procedure": plan["stopping_procedure"],
        },
    }
    return {**base, **values[name]}


def _derive_causes(
    stages: dict[str, dict[str, Any]],
    process: dict[str, Any] | None,
    cleanup: dict[str, Any] | None,
) -> list[str]:
    causes: list[str] = []
    for name in ("capabilities", "host", "helper", "ownership", "rf_idle", "cleanup_registration"):
        if name in stages and stages[name]["outcome"] == "blocked":
            causes.append(
                {
                    "capabilities": "missing_capability",
                    "host": "wrong_host",
                    "helper": "helper_mismatch",
                    "ownership": "ownership_conflict",
                    "rf_idle": "rf_idle_unverified",
                    "cleanup_registration": "cleanup_registration_failed",
                }[name]
            )
            break
    if process is not None and process["outcome"] != "completed":
        causes.append(str(process["outcome"]))
    if cleanup is not None:
        if not cleanup["process_absent"]:
            causes.append("process_leak")
        if any(not item["restoration_verified"] for item in cleanup["services"]):
            causes.append("service_restore_failed")
        if not cleanup["quiescence"]["verified"]:
            causes.append("quiescence_unverified")
    return causes


def _stage(
    name: str, digest: str, deadline: float, outcome: str, details: dict[str, object]
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "evidence_type": "transmitter_lifecycle_stage",
        "stage": name,
        "plan_sha256": digest,
        "deadline_s": deadline,
        "elapsed_s": 0.001,
        "outcome": outcome,
        "details": details,
    }


def _require_keys(document: dict[str, Any], expected: set[str], label: str) -> None:
    if set(document) != expected:
        raise TransmitterLifecycleError(f"{label} evidence fields are not exact")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TransmitterLifecycleError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TransmitterLifecycleError("timestamp must use canonical UTC Z form")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise TransmitterLifecycleError("timestamp is invalid") from exc
    if _utc(parsed) != value:
        raise TransmitterLifecycleError("timestamp must use canonical UTC Z form")
    return parsed
