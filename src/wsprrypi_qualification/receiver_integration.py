"""Hardware-free receiver-only integration lifecycle and durable evidence."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from wsprrypi_qualification.manifests import build_manifest, render_manifest, write_manifest
from wsprrypi_qualification.offline import artifact, validate_document, write_json_new


class ReceiverIntegrationError(RuntimeError):
    """Receiver-only integration evidence or lifecycle is invalid."""


class ReceiverFixtureBlocked(ReceiverIntegrationError):
    """A receiver, RF path, ownership, or coordination fixture is unavailable."""


class ReceiverCancelled(ReceiverIntegrationError):
    """The sealed hardware-free operator cancelled the lifecycle."""


class ReceiverInjection(StrEnum):
    NONE = "none"
    CAPABILITY = "missing_capability"
    CAPTURE_HOST = "wrong_capture_host"
    COORDINATION = "coordination_disconnect"
    HELPER = "helper_mismatch"
    OWNERSHIP = "ownership_conflict"
    RF_PATH = "unsafe_rf_path"
    ACQUIRE = "receiver_absent"
    SHORT_READ = "short_read"
    OVERFLOW = "overflow"
    TIMEOUT = "capture_timeout"
    CANCEL = "capture_cancelled"
    HELPER_EXIT = "helper_nonzero"
    RECEIVER_DISCONNECT = "receiver_disconnect"
    CLIPPING = "clipping"
    CLEANUP_REGISTRATION = "cleanup_registration_partial"
    STOP = "receiver_stop_failure"
    HELPER_SHUTDOWN = "helper_shutdown_failure"
    COORDINATION_CLOSE = "coordination_close_failure"
    RELEASE = "receiver_release_failure"


_PREFLIGHT_CAUSES = {
    "capabilities": "missing_capability",
    "capture_host": "wrong_capture_host",
    "coordination": "coordination_disconnect",
    "helper": "helper_mismatch",
    "ownership": "ownership_conflict",
    "rf_path": "unsafe_rf_path",
    "cleanup_registration": "cleanup_registration_partial",
    "receiver_acquired": "receiver_absent",
}


@dataclass(frozen=True)
class ResolvedReceiverIntegrationPlan:
    document: dict[str, Any]

    def validated(self) -> dict[str, Any]:
        validate_receiver_plan(self.document)
        return self.document

    @property
    def sha256(self) -> str:
        return _digest(self.validated())


@dataclass(frozen=True)
class ReceiverRuntimeAuthorization:
    operator: str
    recorded_utc: datetime
    resolved_plan_sha256: str
    scope: str
    authorized: bool = True
    profile_derived: bool = False
    transmitter_authorized: bool = False

    def document(self) -> dict[str, object]:
        document: dict[str, object] = {
            "schema_version": 1,
            "evidence_type": "receiver_runtime_authorization",
            "execution_mode": "hardware_free_validation",
            "kind": "receiver_access",
            "operator": self.operator,
            "recorded_utc": _utc(self.recorded_utc),
            "resolved_plan_sha256": self.resolved_plan_sha256,
            "authorized": self.authorized,
            "scope": self.scope,
            "profile_derived": self.profile_derived,
            "transmitter_authorized": self.transmitter_authorized,
        }
        validate_document(document, "receiver-runtime-authorization.schema.json")
        return document


class SealedFakeReceiverAdapters:
    """Exact fake adapter boundary; it cannot execute commands or access devices."""

    __slots__ = ("cleanup_attempted", "injection", "release_checked")

    def __init__(self, injection: ReceiverInjection = ReceiverInjection.NONE) -> None:
        if type(self) is not SealedFakeReceiverAdapters:
            raise TypeError("receiver fake adapter is sealed")
        self.injection = injection
        self.cleanup_attempted = False
        self.release_checked = False

    def stage(
        self, name: str, plan: dict[str, Any], digest: str, deadline: float
    ) -> dict[str, object]:
        blocked = {
            "capabilities": ReceiverInjection.CAPABILITY,
            "capture_host": ReceiverInjection.CAPTURE_HOST,
            "coordination": ReceiverInjection.COORDINATION,
            "helper": ReceiverInjection.HELPER,
            "ownership": ReceiverInjection.OWNERSHIP,
            "rf_path": ReceiverInjection.RF_PATH,
            "cleanup_registration": ReceiverInjection.CLEANUP_REGISTRATION,
            "receiver_acquired": ReceiverInjection.ACQUIRE,
        }
        outcome = "blocked" if blocked.get(name) is self.injection else "passed"
        details = _expected_preflight_details(
            name,
            plan,
            outcome,
            None if outcome == "passed" else self.injection.value,
        )
        return _stage(name, digest, deadline, outcome, details)

    def capture(
        self, plan: dict[str, Any], digest: str, root: Path, now: datetime
    ) -> dict[str, object]:
        capture = cast(dict[str, Any], plan["capture"])
        count = cast(int, capture["sample_count"])
        retained = count - 1 if self.injection is ReceiverInjection.SHORT_READ else count
        overflow = 1 if self.injection is ReceiverInjection.OVERFLOW else 0
        timeout = 1 if self.injection is ReceiverInjection.TIMEOUT else 0
        clipped = 1 if self.injection is ReceiverInjection.CLIPPING else 0
        disconnected = self.injection is ReceiverInjection.RECEIVER_DISCONNECT
        return_code = 7 if self.injection is ReceiverInjection.HELPER_EXIT else 0
        outcome = "cancelled" if self.injection is ReceiverInjection.CANCEL else "completed"
        if any((overflow, timeout, clipped, disconnected, return_code, retained != count)):
            outcome = "blocked"
        iq_path = root / "rf-off.cf32"
        iq_path.write_bytes(
            b"".join(
                struct.pack("<ff", 1.0 if clipped and index == 0 else 0.125, -0.125)
                for index in range(retained)
            )
        )
        metadata_path = root / "capture-metadata.json"
        settings = {
            **capture,
            "manufacturer": plan["receiver"]["manufacturer"],
            "model": plan["receiver"]["model"],
            "driver": plan["receiver"]["driver"],
            "serial": plan["receiver"]["serial"],
            "channel": plan["receiver"]["channel"],
            "module": plan["receiver"]["module"],
        }
        metadata = {
            "schema_version": 1,
            "evidence_type": "receiver_capture_metadata",
            "run_id": plan["run_id"],
            "plan_sha256": digest,
            "capture_host": plan["capture_host"]["name"],
            "receiver": plan["receiver"],
            "requested_settings": settings,
            "actual_settings": settings,
            "first_read_discarded": True,
            "requested_sample_count": count,
            "retained_sample_count": retained,
            "expected_byte_count": capture["expected_byte_count"],
            "retained_byte_count": retained * 8,
            "overflow_count": overflow,
            "timeout_count": timeout,
            "short_read_count": int(retained != count),
            "clipping_threshold": capture["clipping_threshold"],
            "clipped_sample_count": clipped,
            "started_utc": _utc(now),
            "completed_utc": _utc(now),
            "elapsed_s": 0.001,
            "helper_return_code": return_code,
            "disconnected": disconnected,
            "cleanup_verified": True,
            "iq": artifact(iq_path),
        }
        validate_document(metadata, "receiver-capture-metadata.schema.json")
        metadata_path.write_text(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        document: dict[str, object] = {
            "schema_version": 1,
            "evidence_type": "receiver_capture_evidence",
            "plan_sha256": digest,
            "run_id": plan["run_id"],
            "capture_host": plan["capture_host"]["name"],
            "receiver": plan["receiver"],
            "requested_settings": settings,
            "actual_settings": settings,
            "first_read_discarded": True,
            "requested_sample_count": count,
            "retained_sample_count": retained,
            "expected_byte_count": capture["expected_byte_count"],
            "retained_byte_count": retained * 8,
            "overflow_count": overflow,
            "timeout_count": timeout,
            "short_read_count": int(retained != count),
            "clipping_threshold": capture["clipping_threshold"],
            "clipped_sample_count": clipped,
            "started_utc": _utc(now),
            "completed_utc": _utc(now),
            "elapsed_s": 0.001,
            "deadline_s": plan["deadlines"]["capture_s"],
            "helper_return_code": return_code,
            "helper_stdout": "hardware-free fixture",
            "helper_stderr": "",
            "helper_identity": plan["remote_helper"]["identity"],
            "metadata": artifact(metadata_path),
            "iq": artifact(iq_path),
            "disconnected": disconnected,
            "cleanup_verified": True,
            "outcome": outcome,
        }
        validate_document(document, "receiver-capture-evidence.schema.json")
        return document

    def cleanup_stage(
        self, name: str, plan: dict[str, Any], digest: str, deadline: float
    ) -> dict[str, object]:
        self.cleanup_attempted = True
        failures = {
            "receiver_stopped": ReceiverInjection.STOP,
            "helper_stopped": ReceiverInjection.HELPER_SHUTDOWN,
            "coordination_closed": ReceiverInjection.COORDINATION_CLOSE,
        }
        outcome = "failed" if failures.get(name) is self.injection else "verified"
        details: dict[str, object] = {"owned_resources_absent": outcome == "verified"}
        if name == "receiver_stopped":
            details.update({"receiver": plan["receiver"], "stop_verified": outcome == "verified"})
        elif name == "helper_stopped":
            details.update(
                {
                    "helper_identity": plan["remote_helper"]["identity"],
                    "absence_verified": outcome == "verified",
                }
            )
        else:
            details.update(
                {
                    "coordination_host": plan["coordination_host"],
                    "channel_closed": outcome == "verified",
                    "unrelated_sessions_affected": False,
                }
            )
        return _stage(name, digest, deadline, outcome, details)

    def release(self, plan: dict[str, Any], digest: str, deadline: float) -> dict[str, object]:
        self.release_checked = True
        outcome = "failed" if self.injection is ReceiverInjection.RELEASE else "verified"
        return _stage(
            "receiver_release",
            digest,
            deadline,
            outcome,
            {
                "receiver_released": outcome == "verified",
                "receiver": plan["receiver"],
                "independent_check": True,
                "ownership_conflict": False,
            },
        )


class ReceiverIntegrationSession:
    """Single-use transactionally published hardware-free receiver lifecycle."""

    def __init__(
        self,
        plan: ResolvedReceiverIntegrationPlan,
        adapters: SealedFakeReceiverAdapters,
        *,
        now: datetime,
    ) -> None:
        if type(adapters) is not SealedFakeReceiverAdapters:
            raise TypeError("only the sealed hardware-free receiver adapter is supported")
        self.plan, self.adapters, self.now = plan, adapters, now
        self._used = False

    def run(
        self, authorization: ReceiverRuntimeAuthorization | None, output_parent: Path
    ) -> dict[str, Any]:
        if self._used:
            raise ReceiverIntegrationError("receiver integration coordinators are single-use")
        self._used = True
        plan = self.plan.validated()
        digest = self.plan.sha256
        session_started = self.now.astimezone(UTC)
        if authorization is None:
            raise ReceiverIntegrationError("ephemeral receiver authorization is required")
        auth = authorization.document()
        if auth["resolved_plan_sha256"] != digest:
            raise ReceiverIntegrationError("receiver authorization does not bind the plan")
        recorded = authorization.recorded_utc.astimezone(UTC)
        if (
            recorded > session_started
            or (session_started - recorded).total_seconds() > plan["deadlines"]["overall_s"]
        ):
            raise ReceiverIntegrationError("receiver authorization is stale or future-dated")
        parent = output_parent.resolve()
        final = parent / cast(str, plan["run_id"])
        temporary = parent / f".incomplete-{plan['run_id']}"
        if final.parent != parent or temporary.parent != parent:
            raise ReceiverIntegrationError("run ID escapes evidence parent")
        if final.exists() or temporary.exists():
            raise ReceiverIntegrationError("refusing to reuse an evidence directory")
        parent.mkdir(parents=True, exist_ok=True)
        temporary.mkdir()
        events: list[dict[str, object]] = []
        stages: dict[str, dict[str, object]] = {}
        capture_doc: dict[str, object] | None = None
        cleanup_registered = False
        cleanup_doc: dict[str, object] | None = None
        release_doc: dict[str, object] | None = None
        status = "inconclusive"
        overall_started = time.monotonic()

        def check_overall() -> None:
            if time.monotonic() - overall_started > plan["deadlines"]["overall_s"]:
                raise ReceiverIntegrationError("receiver integration overall deadline expired")

        def event(phase: str, outcome: str) -> None:
            events.append({"sequence": len(events) + 1, "phase": phase, "outcome": outcome})

        event("requested", "recorded")
        try:
            event("validated", "passed")
            sequence = (
                ("capabilities", "helper_s"),
                ("capture_host", "helper_s"),
                ("coordination", "coordination_s"),
                ("helper", "helper_s"),
                ("ownership", "helper_s"),
                ("rf_path", "helper_s"),
                ("cleanup_registration", "cleanup_s"),
                ("receiver_acquired", "capture_s"),
            )
            for name, deadline_name in sequence:
                check_overall()
                if name == "coordination" and plan["coordination_host"] is None:
                    continue
                if name == "cleanup_registration":
                    cleanup_registered = True
                stage = self.adapters.stage(
                    name, plan, digest, cast(float, plan["deadlines"][deadline_name])
                )
                _validate_stage(stage, name, digest, cast(float, plan["deadlines"][deadline_name]))
                stages[name] = stage
                event(name, cast(str, stage["outcome"]))
                if stage["outcome"] != "passed":
                    raise ReceiverFixtureBlocked(f"{name} reports an unavailable fixture")
                check_overall()
            capture_doc = self.adapters.capture(plan, digest, temporary, self.now)
            check_overall()
            validate_capture_evidence(capture_doc, plan, digest)
            metadata_path = Path(
                cast(str, cast(dict[str, object], capture_doc["metadata"])["path"])
            )
            metadata_document = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata_document["iq"]["path"] = str(final / "rf-off.cf32")
            metadata_path.write_text(
                json.dumps(metadata_document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            capture_doc["metadata"] = artifact(metadata_path)
            for artifact_name in ("metadata", "iq"):
                capture_artifact = cast(dict[str, object], capture_doc[artifact_name])
                capture_artifact["path"] = str(
                    final / Path(cast(str, capture_artifact["path"])).name
                )
            event("rf_off_capture", cast(str, capture_doc["outcome"]))
            if capture_doc["outcome"] == "cancelled":
                raise ReceiverCancelled("operator cancelled receiver capture")
            if capture_doc["outcome"] != "completed":
                raise ReceiverFixtureBlocked("capture contract was not satisfied")
        except Exception as exc:
            if isinstance(exc, ReceiverCancelled):
                status = "aborted"
            elif isinstance(exc, ReceiverFixtureBlocked):
                status = "fixture_blocked"
            else:
                status = "aborted" if cleanup_registered else "preflight_failed"
        finally:
            if cleanup_registered:
                cleanup_ok = True
                for name in ("receiver_stopped", "helper_stopped", "coordination_closed"):
                    if name == "coordination_closed" and plan["coordination_host"] is None:
                        continue
                    item = self.adapters.cleanup_stage(
                        name, plan, digest, cast(float, plan["deadlines"]["cleanup_s"])
                    )
                    stages[name] = item
                    event(name, cast(str, item["outcome"]))
                    cleanup_ok &= item["outcome"] == "verified"
                cleanup_doc = _stage(
                    "cleanup",
                    digest,
                    cast(float, plan["deadlines"]["cleanup_s"]),
                    "verified" if cleanup_ok else "failed",
                    {
                        "actions_complete": cleanup_ok,
                        "helper_absent": stages["helper_stopped"]["outcome"] == "verified",
                        "receiver_absent": stages["receiver_stopped"]["outcome"] == "verified",
                        "coordination_closed": (
                            stages["coordination_closed"]["outcome"] == "verified"
                            if plan["coordination_host"] is not None
                            else True
                        ),
                    },
                )
                release_doc = self.adapters.release(
                    plan, digest, cast(float, plan["deadlines"]["cleanup_s"])
                )
                event("cleanup", cast(str, cleanup_doc["outcome"]))
                event("receiver_release", cast(str, release_doc["outcome"]))
                if not cleanup_ok or release_doc["outcome"] != "verified":
                    status = "cleanup_failed"
        causes = _derive_failure_causes(stages, capture_doc, release_doc, status)
        document: dict[str, Any] = {
            "schema_version": 1,
            "evidence_type": "receiver_integration_session",
            "run_id": plan["run_id"],
            "plan_sha256": digest,
            "chronology": {
                "started_utc": _utc(session_started),
                "authorization_freshness_s": plan["deadlines"]["overall_s"],
                "run_id": plan["run_id"],
                "plan_sha256": digest,
            },
            "authorization": auth,
            "events": events,
            "stages": stages,
            "capture": capture_doc,
            "cleanup": cleanup_doc,
            "release": release_doc,
            "failure_causes": causes,
            "final_status": status,
            "qualification_claim": False,
        }
        validate_receiver_session(document)
        result = {
            "schema_version": 1,
            "evidence_type": "receiver_integration_result",
            "run_id": plan["run_id"],
            "status": status,
            "cleanup_outcome": (
                "failed"
                if status == "cleanup_failed"
                else "verified"
                if cleanup_registered
                else "not_required"
            ),
            "failure_causes": causes,
            "qualification_claim": False,
            "transmitter_operated": False,
        }
        validate_document(result, "receiver-integration-result.schema.json")
        try:
            write_json_new(
                temporary / "resolved-plan.json",
                plan,
                schema_name="resolved-receiver-integration-plan.schema.json",
            )
            write_json_new(
                temporary / "runtime-authorization.json",
                auth,
                schema_name="receiver-runtime-authorization.schema.json",
            )
            write_json_new(
                temporary / "session.json",
                document,
                schema_name="receiver-integration-session.schema.json",
            )
            write_json_new(
                temporary / "result.json",
                result,
                schema_name="receiver-integration-result.schema.json",
            )
            index = {
                "schema_version": 1,
                "evidence_type": "receiver_integration_artifact_index",
                "run_id": plan["run_id"],
                "artifacts": [
                    {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
                    for item in build_manifest(temporary)
                ],
            }
            write_json_new(
                temporary / "artifact-index.json",
                index,
                schema_name="receiver-integration-artifact-index.schema.json",
            )
            write_manifest(temporary)
            temporary.replace(final)
            validate_receiver_bundle(final)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            shutil.rmtree(final, ignore_errors=True)
            raise
        return {"bundle": str(final), "session": document, "result": result}


def validate_receiver_plan(document: dict[str, Any]) -> None:
    validate_document(document, "resolved-receiver-integration-plan.schema.json")
    capture = document["capture"]
    if capture["expected_byte_count"] != capture["sample_count"] * 8:
        raise ReceiverIntegrationError("CF32 sample and byte counts contradict")
    deadlines = document["deadlines"]
    if any(deadlines[name] > deadlines["overall_s"] for name in deadlines if name != "overall_s"):
        raise ReceiverIntegrationError("component deadline exceeds overall deadline")
    if document["coordination_required"] != (document["coordination_host"] is not None):
        raise ReceiverIntegrationError("coordination requirement contradicts its host")
    if document["remote_helper"]["host"] != document["capture_host"]["name"]:
        raise ReceiverIntegrationError("helper host contradicts capture host")


def validate_capture_evidence(
    document: dict[str, object], plan: dict[str, Any], digest: str
) -> None:
    validate_document(document, "receiver-capture-evidence.schema.json")
    capture = plan["capture"]
    expected_settings = {
        **capture,
        "manufacturer": plan["receiver"]["manufacturer"],
        "model": plan["receiver"]["model"],
        "driver": plan["receiver"]["driver"],
        "serial": plan["receiver"]["serial"],
        "channel": plan["receiver"]["channel"],
        "module": plan["receiver"]["module"],
    }
    if (
        document["plan_sha256"] != digest
        or document["run_id"] != plan["run_id"]
        or document["capture_host"] != plan["capture_host"]["name"]
        or document["receiver"] != plan["receiver"]
        or document["requested_settings"] != expected_settings
        or document["actual_settings"] != expected_settings
        or document["deadline_s"] != plan["deadlines"]["capture_s"]
    ):
        raise ReceiverIntegrationError("capture evidence contradicts the resolved plan")
    _validate_capture_timing(document, cast(float, plan["deadlines"]["capture_s"]))
    retained_samples = cast(int, document["retained_sample_count"])
    retained_bytes = cast(int, document["retained_byte_count"])
    overflow_count = cast(int, document["overflow_count"])
    timeout_count = cast(int, document["timeout_count"])
    short_read_count = cast(int, document["short_read_count"])
    clipped_count = cast(int, document["clipped_sample_count"])
    contract_issue = (
        retained_samples != capture["sample_count"]
        or retained_bytes != capture["expected_byte_count"]
        or overflow_count > 0
        or timeout_count > 0
        or short_read_count > 0
        or clipped_count > 0
        or document["helper_return_code"] not in (0, None)
        or document["disconnected"] is True
        or document["cleanup_verified"] is False
    )
    success = (
        document["retained_sample_count"] == capture["sample_count"]
        and document["retained_byte_count"] == capture["expected_byte_count"]
        and document["overflow_count"] == 0
        and document["timeout_count"] == 0
        and document["short_read_count"] == 0
        and document["clipped_sample_count"] == 0
        and document["helper_return_code"] == 0
        and document["disconnected"] is False
        and document["cleanup_verified"] is True
    )
    if retained_bytes != retained_samples * 8:
        raise ReceiverIntegrationError("capture sample and byte counts contradict")
    if document["short_read_count"] != int(
        document["retained_sample_count"] != document["requested_sample_count"]
    ):
        raise ReceiverIntegrationError("capture short-read count contradicts retained samples")
    expected_outcome = (
        "cancelled"
        if document["outcome"] == "cancelled"
        else "blocked"
        if contract_issue
        else "completed"
    )
    if document["outcome"] != expected_outcome or (expected_outcome == "completed" and not success):
        raise ReceiverIntegrationError("capture outcome contradicts exact-count evidence")
    for name in ("metadata", "iq"):
        record = cast(dict[str, object], document[name])
        path = Path(cast(str, record["path"])).resolve(strict=True)
        actual = artifact(path)
        if actual != record:
            raise ReceiverIntegrationError(f"capture {name} artifact identity changed")


def validate_receiver_session(document: dict[str, Any]) -> None:
    validate_document(document, "receiver-integration-session.schema.json")
    chronology = document["chronology"]
    started = _parse_canonical_utc(chronology["started_utc"])
    recorded = _parse_canonical_utc(document["authorization"]["recorded_utc"])
    try:
        run_started = datetime.strptime(document["run_id"][:16], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=UTC
        )
    except (TypeError, ValueError) as exc:
        raise ReceiverIntegrationError("receiver run ID has no canonical UTC start") from exc
    if started != run_started:
        raise ReceiverIntegrationError("receiver session start contradicts its run ID")
    age = (started - recorded).total_seconds()
    if age < 0 or age > chronology["authorization_freshness_s"]:
        raise ReceiverIntegrationError("receiver runtime authorization is stale or future-dated")
    if (
        chronology["run_id"] != document["run_id"]
        or chronology["plan_sha256"] != document["plan_sha256"]
    ):
        raise ReceiverIntegrationError("receiver chronology identity contradicts the session")
    sequences = [item["sequence"] for item in document["events"]]
    if sequences != list(range(1, len(sequences) + 1)):
        raise ReceiverIntegrationError("receiver events are not exactly ordered")
    digest = document["plan_sha256"]
    for name, stage in document["stages"].items():
        if stage["stage"] != name or stage["plan_sha256"] != digest:
            raise ReceiverIntegrationError("receiver stage identity contradicts session")
        if stage["elapsed_s"] > stage["deadline_s"]:
            raise ReceiverIntegrationError("receiver stage exceeded its hard deadline")
    event_by_phase = {item["phase"]: item for item in document["events"]}
    if len(event_by_phase) != len(document["events"]):
        raise ReceiverIntegrationError("receiver lifecycle contains duplicate phases")
    for name, stage in document["stages"].items():
        if name not in event_by_phase or event_by_phase[name]["outcome"] != stage["outcome"]:
            raise ReceiverIntegrationError("receiver event outcome contradicts stage evidence")
    capture = document["capture"]
    if (
        capture is not None
        and event_by_phase.get("rf_off_capture", {}).get("outcome") != capture["outcome"]
    ):
        raise ReceiverIntegrationError("receiver capture event contradicts capture evidence")
    cleanup = document["cleanup"]
    release = document["release"]
    cleanup_failed = cleanup is not None and cleanup["outcome"] != "verified"
    release_failed = release is not None and release["outcome"] != "verified"
    if (cleanup_failed or release_failed) != (document["final_status"] == "cleanup_failed"):
        raise ReceiverIntegrationError("receiver cleanup precedence is contradictory")
    if (
        cleanup is not None
        and event_by_phase.get("cleanup", {}).get("outcome") != cleanup["outcome"]
    ):
        raise ReceiverIntegrationError("receiver cleanup event contradicts cleanup evidence")
    if (
        release is not None
        and event_by_phase.get("receiver_release", {}).get("outcome") != release["outcome"]
    ):
        raise ReceiverIntegrationError("receiver release event contradicts release evidence")
    if document["final_status"] == "inconclusive" and document["capture"] is None:
        raise ReceiverIntegrationError("receiver success has no capture evidence")
    blocked = any(stage["outcome"] == "blocked" for stage in document["stages"].values())
    expected = (
        "cleanup_failed"
        if cleanup_failed or release_failed
        else "aborted"
        if capture is not None and capture["outcome"] == "cancelled"
        else "fixture_blocked"
        if blocked or (capture is not None and capture["outcome"] == "blocked")
        else "inconclusive"
        if capture is not None and capture["outcome"] == "completed"
        else "aborted"
        if cleanup is not None
        else "preflight_failed"
    )
    if document["final_status"] != expected:
        raise ReceiverIntegrationError("receiver final status contradicts lifecycle evidence")
    expected_causes = _derive_failure_causes(
        document["stages"], document["capture"], document["release"], document["final_status"]
    )
    if document["failure_causes"] != expected_causes:
        raise ReceiverIntegrationError("receiver failure causes contradict lifecycle evidence")


def validate_receiver_bundle(root: Path) -> None:
    root = root.resolve(strict=True)
    plan = validate_document(
        json.loads((root / "resolved-plan.json").read_text(encoding="utf-8")),
        "resolved-receiver-integration-plan.schema.json",
    )
    validate_receiver_plan(plan)
    authorization = validate_document(
        json.loads((root / "runtime-authorization.json").read_text(encoding="utf-8")),
        "receiver-runtime-authorization.schema.json",
    )
    session = validate_document(
        json.loads((root / "session.json").read_text(encoding="utf-8")),
        "receiver-integration-session.schema.json",
    )
    validate_receiver_session(session)
    digest = _digest(plan)
    if (
        root.name != plan["run_id"]
        or session["run_id"] != plan["run_id"]
        or session["plan_sha256"] != digest
        or authorization["resolved_plan_sha256"] != digest
        or session["authorization"] != authorization
        or session["chronology"]["authorization_freshness_s"] != plan["deadlines"]["overall_s"]
    ):
        raise ReceiverIntegrationError("receiver bundle plan or authorization binding changed")
    _validate_lifecycle(session, plan)
    _validate_cleanup_semantics(session, plan)
    capture = session["capture"]
    if capture is not None:
        if (
            Path(capture["metadata"]["path"]).resolve() != root / "capture-metadata.json"
            or Path(capture["iq"]["path"]).resolve() != root / "rf-off.cf32"
        ):
            raise ReceiverIntegrationError("receiver capture artifacts escape their bundle")
        validate_capture_evidence(capture, plan, digest)
        _validate_capture_metadata_and_iq(root, capture, plan, digest)
    deadline_map = {
        "capabilities": "helper_s",
        "capture_host": "helper_s",
        "coordination": "coordination_s",
        "helper": "helper_s",
        "ownership": "helper_s",
        "rf_path": "helper_s",
        "cleanup_registration": "cleanup_s",
        "receiver_acquired": "capture_s",
        "receiver_stopped": "cleanup_s",
        "helper_stopped": "cleanup_s",
        "coordination_closed": "cleanup_s",
    }
    for name, stage in session["stages"].items():
        if name not in deadline_map or stage["deadline_s"] != plan["deadlines"][deadline_map[name]]:
            raise ReceiverIntegrationError("receiver stage deadline is not bound to the plan")
    for stage in (session["cleanup"], session["release"]):
        if stage is not None and stage["deadline_s"] != plan["deadlines"]["cleanup_s"]:
            raise ReceiverIntegrationError("receiver cleanup deadline is not bound to the plan")
    result = validate_document(
        json.loads((root / "result.json").read_text(encoding="utf-8")),
        "receiver-integration-result.schema.json",
    )
    expected_cleanup = (
        "failed"
        if session["final_status"] == "cleanup_failed"
        else "verified"
        if session["cleanup"] is not None
        else "not_required"
    )
    if result != {
        "schema_version": 1,
        "evidence_type": "receiver_integration_result",
        "run_id": session["run_id"],
        "status": session["final_status"],
        "cleanup_outcome": expected_cleanup,
        "failure_causes": session["failure_causes"],
        "qualification_claim": False,
        "transmitter_operated": False,
    }:
        raise ReceiverIntegrationError("receiver result contradicts its session")
    manifest = root / "SHA256SUMS"
    if manifest.read_text(encoding="utf-8") != render_manifest(build_manifest(root)):
        raise ReceiverIntegrationError("receiver manifest does not authenticate the bundle")
    index = validate_document(
        json.loads((root / "artifact-index.json").read_text(encoding="utf-8")),
        "receiver-integration-artifact-index.schema.json",
    )
    expected_index = [
        {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
        for item in build_manifest(root)
        if item.path not in {"artifact-index.json", "SHA256SUMS"}
    ]
    if index["run_id"] != plan["run_id"] or index["artifacts"] != expected_index:
        raise ReceiverIntegrationError("receiver artifact index is incomplete or contradictory")
    expected_paths = {
        "resolved-plan.json",
        "runtime-authorization.json",
        "session.json",
        "result.json",
        "artifact-index.json",
        "SHA256SUMS",
    }
    if capture is not None:
        expected_paths.update({"capture-metadata.json", "rf-off.cf32"})
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_paths != expected_paths or any(path.is_symlink() for path in root.rglob("*")):
        raise ReceiverIntegrationError("receiver bundle artifact set is incomplete or unexpected")


def _validate_lifecycle(session: dict[str, Any], plan: dict[str, Any]) -> None:
    preflight = ["capabilities", "capture_host"]
    if plan["coordination_host"] is not None:
        preflight.append("coordination")
    preflight.extend(
        ["helper", "ownership", "rf_path", "cleanup_registration", "receiver_acquired"]
    )
    stages = session["stages"]
    expected_stages: list[str] = []
    expected_events = ["requested", "validated"]
    for name in preflight:
        if name not in stages:
            raise ReceiverIntegrationError(f"receiver lifecycle omits required {name} stage")
        expected_stages.append(name)
        expected_events.append(name)
        if stages[name]["outcome"] != "passed":
            break
    cleanup_required = "cleanup_registration" in expected_stages
    if session["capture"] is not None:
        if (
            expected_stages[-1] != "receiver_acquired"
            or stages["receiver_acquired"]["outcome"] != "passed"
        ):
            raise ReceiverIntegrationError("receiver capture precedes completed acquisition")
        expected_events.append("rf_off_capture")
    elif (
        expected_stages[-1] == "receiver_acquired"
        and stages["receiver_acquired"]["outcome"] == "passed"
    ):
        raise ReceiverIntegrationError("completed receiver acquisition omits capture evidence")
    if cleanup_required:
        cleanup_names = ["receiver_stopped", "helper_stopped"]
        if plan["coordination_host"] is not None:
            cleanup_names.append("coordination_closed")
        expected_stages.extend(cleanup_names)
        expected_events.extend([*cleanup_names, "cleanup", "receiver_release"])
        if session["cleanup"] is None or session["release"] is None:
            raise ReceiverIntegrationError("registered cleanup omits retained cleanup evidence")
    elif session["cleanup"] is not None or session["release"] is not None:
        raise ReceiverIntegrationError("unregistered cleanup has retained cleanup evidence")
    if set(stages) != set(expected_stages):
        raise ReceiverIntegrationError("receiver stage sequence is incomplete or unexpected")
    if [event["phase"] for event in session["events"]] != expected_events:
        raise ReceiverIntegrationError("receiver event sequence is incomplete or unexpected")
    for name in preflight:
        if name not in stages:
            break
        stage = stages[name]
        cause = stage["details"].get("cause")
        expected = _expected_preflight_details(name, plan, stage["outcome"], cause)
        if stage["details"] != expected:
            raise ReceiverIntegrationError(f"{name} stage details contradict the resolved plan")


def _validate_cleanup_semantics(session: dict[str, Any], plan: dict[str, Any]) -> None:
    stages = session["stages"]
    if "receiver_stopped" not in stages:
        return
    receiver = stages["receiver_stopped"]
    receiver_ok = receiver["outcome"] == "verified"
    if receiver["details"] != {
        "owned_resources_absent": receiver_ok,
        "receiver": plan["receiver"],
        "stop_verified": receiver_ok,
    }:
        raise ReceiverIntegrationError("receiver-stop details contradict cleanup outcome")
    helper = stages["helper_stopped"]
    helper_ok = helper["outcome"] == "verified"
    if helper["details"] != {
        "owned_resources_absent": helper_ok,
        "helper_identity": plan["remote_helper"]["identity"],
        "absence_verified": helper_ok,
    }:
        raise ReceiverIntegrationError("helper-stop details contradict cleanup outcome")
    coordination_ok = True
    if plan["coordination_host"] is not None:
        coordination = stages["coordination_closed"]
        coordination_ok = coordination["outcome"] == "verified"
        if coordination["details"] != {
            "owned_resources_absent": coordination_ok,
            "coordination_host": plan["coordination_host"],
            "channel_closed": coordination_ok,
            "unrelated_sessions_affected": False,
        }:
            raise ReceiverIntegrationError("coordination-close details contradict outcome")
    cleanup_ok = receiver_ok and helper_ok and coordination_ok
    cleanup = session["cleanup"]
    expected_cleanup_details = {
        "actions_complete": cleanup_ok,
        "helper_absent": helper_ok,
        "receiver_absent": receiver_ok,
        "coordination_closed": coordination_ok,
    }
    if (
        cleanup is None
        or cleanup["outcome"] != ("verified" if cleanup_ok else "failed")
        or cleanup["details"] != expected_cleanup_details
    ):
        raise ReceiverIntegrationError("aggregate cleanup contradicts component cleanup")
    release = session["release"]
    if release is None:
        raise ReceiverIntegrationError("receiver release evidence is absent")
    release_ok = release["outcome"] == "verified"
    if release["details"] != {
        "receiver_released": release_ok,
        "receiver": plan["receiver"],
        "independent_check": True,
        "ownership_conflict": False,
    }:
        raise ReceiverIntegrationError("receiver-release details contradict outcome")


def _validate_capture_metadata_and_iq(
    root: Path, capture: dict[str, Any], plan: dict[str, Any], digest: str
) -> None:
    metadata = validate_document(
        json.loads((root / "capture-metadata.json").read_text(encoding="utf-8")),
        "receiver-capture-metadata.schema.json",
    )
    expected = {
        "schema_version": 1,
        "evidence_type": "receiver_capture_metadata",
        "run_id": plan["run_id"],
        "plan_sha256": digest,
        "capture_host": plan["capture_host"]["name"],
        "receiver": plan["receiver"],
        "requested_settings": capture["requested_settings"],
        "actual_settings": capture["actual_settings"],
        "first_read_discarded": True,
        "requested_sample_count": plan["capture"]["sample_count"],
        "retained_sample_count": capture["retained_sample_count"],
        "expected_byte_count": plan["capture"]["expected_byte_count"],
        "retained_byte_count": capture["retained_byte_count"],
        "overflow_count": capture["overflow_count"],
        "timeout_count": capture["timeout_count"],
        "short_read_count": capture["short_read_count"],
        "clipped_sample_count": capture["clipped_sample_count"],
        "clipping_threshold": plan["capture"]["clipping_threshold"],
        "started_utc": capture["started_utc"],
        "completed_utc": capture["completed_utc"],
        "elapsed_s": capture["elapsed_s"],
        "helper_return_code": capture["helper_return_code"],
        "disconnected": capture["disconnected"],
        "cleanup_verified": capture["cleanup_verified"],
        "iq": capture["iq"],
    }
    if metadata != expected:
        raise ReceiverIntegrationError("capture metadata contradicts retained capture evidence")
    _validate_capture_timing(metadata, float(plan["deadlines"]["capture_s"]))
    iq_path = root / "rf-off.cf32"
    if iq_path.stat().st_size != capture["retained_byte_count"]:
        raise ReceiverIntegrationError("retained CF32 byte count contradicts capture evidence")
    samples = clipped = 0
    threshold = float(plan["capture"]["clipping_threshold"])
    with iq_path.open("rb") as handle:
        while block := handle.read(8 * 65536):
            if len(block) % 8:
                raise ReceiverIntegrationError("retained CF32 has a partial complex sample")
            for i_value, q_value in struct.iter_unpack("<ff", block):
                if not math.isfinite(i_value) or not math.isfinite(q_value):
                    raise ReceiverIntegrationError("retained CF32 contains non-finite samples")
                samples += 1
                clipped += int(abs(i_value) >= threshold or abs(q_value) >= threshold)
    if samples != capture["retained_sample_count"] or clipped != capture["clipped_sample_count"]:
        raise ReceiverIntegrationError(
            "retained CF32 sample or clipping evidence contradicts bytes"
        )


def _expected_preflight_details(
    name: str,
    plan: dict[str, Any],
    outcome: str,
    cause: object,
) -> dict[str, object]:
    verified = outcome == "passed"
    if verified and cause is not None:
        raise ReceiverIntegrationError(f"passed {name} stage retains a failure cause")
    if not verified and cause != _PREFLIGHT_CAUSES.get(name):
        raise ReceiverIntegrationError(f"blocked {name} stage has the wrong typed cause")
    common: dict[str, object] = {
        "hardware_access": False,
        "verification_mode": "sealed_hardware_free_fixture",
        "verified": verified,
        "cause": cause,
    }
    if name == "capabilities":
        return {
            **common,
            "execution_mode": plan["execution_mode"],
            "required_capabilities": sorted(plan["capability_bindings"]),
            "capability_bindings": plan["capability_bindings"],
            "live_capability_used": False,
        }
    if name == "capture_host":
        return {**common, "host": plan["capture_host"]}
    if name == "coordination":
        return {
            **common,
            "host": plan["coordination_host"],
            "required": plan["coordination_required"],
            "purpose": "read_only_non_interference_check",
        }
    if name == "helper":
        return {
            **common,
            "helper": plan["remote_helper"],
            "simulated": True,
            "external_process_started": False,
        }
    if name == "ownership":
        return {
            **common,
            "receiver": plan["receiver"],
            "conflicts": [] if verified else ["fixture"],
            "competing_owner": None if verified else "fixture",
            "check_mode": "simulated_read_only",
        }
    if name == "rf_path":
        return {
            **common,
            "rf_path": plan["rf_path"],
            "safety_accepted": verified,
            "acceptance_basis": "simulated_validation_only",
            "transmitter_operated": False,
        }
    if name == "cleanup_registration":
        return {
            **common,
            "registration_attempted": True,
            "installed": True,
            "before_receiver_acquisition": True,
            "stopping_procedure": plan["stopping_procedure"],
            "receiver_release_contract": plan["receiver_release_contract"],
        }
    if name == "receiver_acquired":
        return {
            **common,
            "receiver": plan["receiver"],
            "capture_settings": plan["capture"],
            "simulated_acquisition": verified,
            "physical_sdr_opened": False,
            "physical_sdr_configured": False,
        }
    raise ReceiverIntegrationError(f"unknown receiver preflight stage {name}")


def _derive_failure_causes(
    stages: dict[str, dict[str, Any]],
    capture: dict[str, Any] | None,
    release: dict[str, Any] | None,
    status: str,
) -> list[str]:
    causes: list[str] = []
    for name in _PREFLIGHT_CAUSES:
        stage = stages.get(name)
        if stage is not None and stage["outcome"] == "blocked":
            causes.append(_PREFLIGHT_CAUSES[name])
            break
    if capture is not None:
        if (
            capture["retained_sample_count"] != capture["requested_sample_count"]
            or capture["retained_byte_count"] != capture["expected_byte_count"]
            or capture["short_read_count"] > 0
        ):
            causes.append("short_read")
        if capture["overflow_count"] > 0:
            causes.append("overflow")
        if capture["timeout_count"] > 0:
            causes.append("capture_timeout")
        if capture["outcome"] == "cancelled":
            causes.append("capture_cancelled")
        if capture["helper_return_code"] not in (0, None):
            causes.append("helper_nonzero")
        if capture["disconnected"]:
            causes.append("receiver_disconnect")
        if capture["clipped_sample_count"] > 0:
            causes.append("clipping")
        if capture["cleanup_verified"] is False:
            causes.append("capture_cleanup_unverified")
    cleanup_causes = {
        "receiver_stopped": "receiver_stop_failed",
        "helper_stopped": "helper_shutdown_failed",
        "coordination_closed": "coordination_close_failed",
    }
    for name, cause in cleanup_causes.items():
        stage = stages.get(name)
        if stage is not None and stage["outcome"] != "verified":
            causes.append(cause)
    if release is not None and release["outcome"] != "verified":
        causes.append("receiver_release_failed")
    if not causes and status in {"preflight_failed", "aborted"}:
        causes.append("internal_error")
    return causes


def _parse_canonical_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReceiverIntegrationError("capture timestamp is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReceiverIntegrationError("capture timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ReceiverIntegrationError("capture timestamp is not UTC-aware")
    if _utc(parsed) != value:
        raise ReceiverIntegrationError("capture timestamp is not canonical UTC")
    return parsed


def _validate_capture_timing(document: dict[str, object], deadline: float) -> None:
    elapsed = document["elapsed_s"]
    if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool):
        raise ReceiverIntegrationError("capture elapsed time is not numeric")
    elapsed_value = float(elapsed)
    if not math.isfinite(elapsed_value) or elapsed_value < 0 or elapsed_value > deadline:
        raise ReceiverIntegrationError("capture elapsed time exceeds its resolved deadline")
    started = _parse_canonical_utc(document["started_utc"])
    completed = _parse_canonical_utc(document["completed_utc"])
    interval = (completed - started).total_seconds()
    if interval < 0:
        raise ReceiverIntegrationError("capture timestamps are reversed")
    # JSON timestamps and monotonic elapsed time may differ slightly at their read boundaries.
    if abs(interval - elapsed_value) > 0.1:
        raise ReceiverIntegrationError("capture timestamps contradict elapsed time")


def _stage(
    name: str, digest: str, deadline: float, outcome: str, details: dict[str, object]
) -> dict[str, object]:
    document = {
        "schema_version": 1,
        "evidence_type": "receiver_integration_stage",
        "stage": name,
        "plan_sha256": digest,
        "deadline_s": deadline,
        "elapsed_s": 0.001,
        "outcome": outcome,
        "details": details,
    }
    validate_document(document, "receiver-integration-stage.schema.json")
    return document


def _validate_stage(document: dict[str, object], name: str, digest: str, deadline: float) -> None:
    validate_document(document, "receiver-integration-stage.schema.json")
    if (
        document["stage"] != name
        or document["plan_sha256"] != digest
        or document["deadline_s"] != deadline
        or not math.isfinite(cast(float, document["elapsed_s"]))
        or cast(float, document["elapsed_s"]) > deadline
    ):
        raise ReceiverIntegrationError(f"{name} evidence contradicts its resolved deadline")


def _digest(document: dict[str, Any]) -> str:
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReceiverIntegrationError("runtime authorization time must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
