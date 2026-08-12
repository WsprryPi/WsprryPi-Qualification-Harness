"""Mock-only Slice 6 composition of reviewed qualification contracts."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from wsprrypi_qualification.adapters import (
    MockQuiescenceAdapter,
    MockReceiverAdapter,
    MockServiceAdapter,
    MockTransmitterAdapter,
)
from wsprrypi_qualification.application_shims import (
    ApplicationPlan,
    ApplicationPlanError,
    ProtocolMode,
    validate_application_plan,
)
from wsprrypi_qualification.carrier import load_acquired_carrier_evidence
from wsprrypi_qualification.decoder import (
    load_audio_evidence,
    load_decoder_evidence,
    summarize_decodes,
)
from wsprrypi_qualification.manifests import write_manifest
from wsprrypi_qualification.models import (
    BenchProfile,
    CleanupOutcome,
    FailureCause,
    GateOutcome,
    QualificationResult,
    ReceiverRunProfile,
    TestProfile,
)
from wsprrypi_qualification.offline import (
    OfflineAnalysisError,
    artifact,
    load_json_document,
    validate_document,
    write_json_new,
)
from wsprrypi_qualification.results import result_to_document, validate_result_document
from wsprrypi_qualification.supervisor import (
    OperationDeadlines,
    ResolvedPlan,
    Supervisor,
    validate_supervisor_document,
)
from wsprrypi_qualification.timing import consecutive_wspr_slots, exact_sample_count


class SessionError(ValueError):
    """A mock session violates a qualification safety/evidence invariant."""


class SessionPhase(StrEnum):
    REQUESTED = "requested"
    VALIDATED = "validated"
    CONFIRMED = "runtime_confirmed"
    PREFLIGHT = "preflight"
    CLEANUP_INSTALLED = "cleanup_installed"
    RF_IDLE = "rf_idle_verified"
    CARRIER = "carrier_gate"
    FRAMES = "wspr_frames"
    CLEANUP = "cleanup"
    QUIESCENCE = "quiescence"
    PUBLISHED = "published"


class Injection(StrEnum):
    NONE = "none"
    INVALID_PLAN = "invalid_plan"
    MISSING_CAPABILITY = "missing_capability"
    MISSING_DEPENDENCY = "missing_dependency"
    CONFIRMATION_MISMATCH = "confirmation_mismatch"
    UNSAFE_RF_PATH = "unsafe_rf_path"
    SOURCE_MISMATCH = "source_mismatch"
    RECEIVER_MISMATCH = "receiver_mismatch"
    OWNERSHIP_CONFLICT = "ownership_conflict"
    RF_IDLE_FAILURE = "rf_idle_failure"
    CANCELLED = "cancelled"
    CLEANUP_FAILED = "cleanup_failed"
    QUIESCENCE_FAILED = "quiescence_failed"
    SERVICE_RESTORE_FAILED = "service_restore_failed"
    RECEIVER_LAUNCH_FAILED = "receiver_launch_failed"
    TRANSMITTER_LAUNCH_FAILED = "transmitter_launch_failed"
    CHILD_TIMEOUT = "child_timeout"
    COPY_FAILED = "copy_failed"
    INDEX_FAILED = "index_failed"
    MANIFEST_FAILED = "manifest_failed"
    PROMOTION_FAILED = "promotion_failed"


@dataclass(frozen=True)
class RuntimeConfirmation:
    recorded_utc: datetime
    operator: str
    resolved_plan_sha256: str
    confirmed: bool


@dataclass(frozen=True)
class OfflineEvidenceSet:
    """Retained Slice 3 outputs consumed by the Slice 6 coordinator."""

    carrier_analysis: Path
    audio_conversions: tuple[Path, Path, Path]
    decoder_evidence: tuple[Path, Path, Path]
    decode_summary: Path


@dataclass(frozen=True)
class QualificationSessionPlan:
    run_id: str
    bench: BenchProfile
    test: TestProfile
    receiver_run: ReceiverRunProfile
    application: ApplicationPlan
    first_slot_utc: datetime
    transmitter_deadline_s: float
    receiver_deadline_s: float
    offline_evidence: OfflineEvidenceSet | None = None
    mock_only: bool = True

    def resolved_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "evidence_type": "slice6_session_plan",
            "run_id": self.run_id,
            "mock_only": self.mock_only,
            "bench": _jsonable(asdict(self.bench)),
            "test": _jsonable(asdict(self.test)),
            "receiver_run": _jsonable(asdict(self.receiver_run)),
            "application": self.application.to_document(),
            "first_slot_utc": _utc(self.first_slot_utc),
            "slots_utc": [_utc(slot) for slot in consecutive_wspr_slots(self.first_slot_utc, 3)],
            "coherent_capture": {
                "duration_s": 370,
                "sample_rate_hz": self.receiver_run.receiver.sample_rate_hz,
                "sample_count": exact_sample_count(self.receiver_run.receiver.sample_rate_hz, 370),
            },
            "deadlines": {
                "transmitter_s": self.transmitter_deadline_s,
                "receiver_s": self.receiver_deadline_s,
            },
            "offline_evidence": (
                None
                if self.offline_evidence is None
                else {
                    "carrier_analysis": str(self.offline_evidence.carrier_analysis.resolve()),
                    "audio_conversions": [
                        str(path.resolve()) for path in self.offline_evidence.audio_conversions
                    ],
                    "decoder_evidence": [
                        str(path.resolve()) for path in self.offline_evidence.decoder_evidence
                    ],
                    "decode_summary": str(self.offline_evidence.decode_summary.resolve()),
                }
            ),
        }


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SessionError("session timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, dict):
        return {key: _jsonable(child) for key, child in value.items() if child is not None}
    if isinstance(value, (list, tuple)):
        return [_jsonable(child) for child in value]
    return value


def resolved_plan_sha256(plan: QualificationSessionPlan) -> str:
    payload = json.dumps(
        plan.resolved_document(), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_session_plan(plan: QualificationSessionPlan) -> None:
    try:
        validate_application_plan(plan.application.to_document())
    except ApplicationPlanError as error:
        raise SessionError(f"application plan is invalid: {error}") from error
    if not plan.mock_only or plan.application.execution_authorized is not False:
        raise SessionError("Slice 6 preparation accepts mock-only non-authorized plans")
    if plan.application.protocol is not ProtocolMode.WSPR:
        raise SessionError("only WSPR has a prepared qualification workflow")
    if plan.application.backend != plan.test.transmitter.backend.value:
        raise SessionError("application and transmitter backend differ")
    if plan.application.backend_contract is None:
        raise SessionError("qualification application plan requires a resolved backend contract")
    if (
        plan.application.identity.source_revision != plan.test.transmitter.source_revision
        or plan.application.identity.submodule_revision != plan.test.transmitter.submodule_revision
    ):
        raise SessionError("application and test source identities differ")
    if plan.receiver_run.bench_id != plan.bench.bench_id:
        raise SessionError("receiver run and bench identifiers differ")
    if plan.receiver_run.receiver != plan.bench.receiver:
        raise SessionError("resolved receiver differs from the bench receiver")
    if plan.receiver_run.center_frequency_hz != plan.test.receiver_center_hz:
        raise SessionError("receiver center differs from the test profile")
    if plan.receiver_run.gain_db != plan.test.receiver_gain_db:
        raise SessionError("receiver gain differs from the test profile")
    if plan.receiver_run.rf_path != plan.bench.rf_path:
        raise SessionError("per-run RF path differs from the resolved bench path")
    if plan.test.frame_count != 3 or plan.test.gates.required_consecutive_decodes != 3:
        raise SessionError("prepared WSPR workflow requires exactly three frames and decodes")
    if plan.test.random_offset_enabled:
        raise SessionError("random WSPR offset must be disabled")
    contract = plan.application.protocol_contract
    if contract.get("requested_rf_frequency_hz") != plan.test.frequency_hz:
        raise SessionError("application RF frequency differs from the test profile")
    arguments = plan.application.arguments
    required_backend_arguments: dict[str, object] = (
        {
            "--si5351-i2c-bus": str(plan.test.transmitter.i2c_bus),
            "--si5351-i2c-address": str(plan.test.transmitter.i2c_address),
            "--si5351-reference-frequency": str(plan.test.transmitter.reference_frequency_hz),
            "--si5351-tx-output": plan.test.transmitter.output,
            "--si5351-power-level": str(
                {2: 1, 4: 2, 6: 3, 8: 4}.get(plan.test.transmitter.drive_ma or 0)
            ),
            "--si5351-ppm": plan.test.ppm,
        }
        if plan.application.backend == "si5351"
        else {
            "--transmit-gpio": str(plan.test.transmitter.gpio_pin),
            "--gpio-power-level": str(plan.test.transmitter.power_level),
            "--gpio-manual-ppm": plan.test.ppm,
        }
    )
    numeric_options = {"--si5351-ppm", "--gpio-manual-ppm"}
    for option, value in required_backend_arguments.items():
        try:
            index = arguments.index(option)
        except ValueError as error:
            raise SessionError(f"application plan omits resolved option {option}") from error
        if index + 1 >= len(arguments):
            raise SessionError(f"application plan contradicts resolved option {option}")
        actual = arguments[index + 1]
        if option in numeric_options:
            try:
                matches = float(actual) == float(str(value))
            except ValueError:
                matches = False
        else:
            matches = actual == str(value)
        if not matches:
            raise SessionError(f"application plan contradicts resolved option {option}")
    if plan.receiver_run.limits.sample_count != exact_sample_count(
        plan.receiver_run.receiver.sample_rate_hz, plan.receiver_run.duration_s
    ):
        raise SessionError("receiver-run exact sample count is inconsistent")
    if plan.receiver_run.duration_s != 370:
        raise SessionError("coherent three-frame receiver run must be 370 seconds")
    if plan.transmitter_deadline_s <= 0 or plan.receiver_deadline_s <= 370:
        raise SessionError("hard deadlines are incomplete")
    plan.resolved_document()


def _require_profile_binding(document: dict[str, Any], plan: QualificationSessionPlan) -> None:
    profiles = document.get("profiles")
    if not isinstance(profiles, dict):
        raise SessionError("offline evidence omits resolved profile binding")
    if (
        profiles.get("bench", {}).get("id") != plan.bench.bench_id
        or profiles.get("test", {}).get("id") != plan.test.test_id
    ):
        raise SessionError("offline evidence profile identifiers differ from the session")
    expected = {
        "receiver": asdict(plan.bench.receiver),
        "requested_frequency_hz": plan.test.frequency_hz,
        "receiver_center_hz": plan.test.receiver_center_hz,
        "receiver_gain_db": plan.test.receiver_gain_db,
        "identity": asdict(plan.test.identity),
        "gates": asdict(plan.test.gates),
        "frame_count": plan.test.frame_count,
        "random_offset_enabled": plan.test.random_offset_enabled,
    }
    if profiles.get("resolved") != expected:
        raise SessionError("offline evidence resolved profiles differ from the session")


def _load_carrier_evidence(evidence: OfflineEvidenceSet, plan: QualificationSessionPlan) -> str:
    """Authenticate retained carrier evidence without touching frame evidence."""
    carrier = load_acquired_carrier_evidence(evidence.carrier_analysis).document
    _require_profile_binding(carrier["contract"], plan)
    return str(carrier["gate_outcome"])


def _load_decode_evidence(evidence: OfflineEvidenceSet, plan: QualificationSessionPlan) -> str:
    """Authenticate retained audio, decoder, and summary evidence."""
    if len(evidence.audio_conversions) != 3 or len(evidence.decoder_evidence) != 3:
        raise SessionError("exactly three audio and decoder evidence files are required")
    planned_slots = [_utc(slot) for slot in consecutive_wspr_slots(plan.first_slot_utc, 3)]
    observed_audio_slots: list[str] = []
    for audio_path in evidence.audio_conversions:
        document = load_json_document(audio_path, "audio-conversion.schema.json")
        output = document.get("output")
        if not isinstance(output, dict) or not isinstance(output.get("path"), str):
            raise SessionError("audio evidence omits its retained WAV path")
        acquired_audio = load_audio_evidence(audio_path, Path(output["path"]))
        _require_profile_binding(acquired_audio.document, plan)
        observed_audio_slots.append(str(acquired_audio.document["slot_utc"]))
    observed_decoder_slots: list[str] = []
    for decoder_path in evidence.decoder_evidence:
        acquired_decoder = load_decoder_evidence(decoder_path)
        _require_profile_binding(acquired_decoder.document, plan)
        observed_decoder_slots.append(str(acquired_decoder.document["slot_utc"]))
    retained_summary = load_json_document(evidence.decode_summary, "decode-summary.schema.json")
    recomputed = summarize_decodes(list(evidence.decoder_evidence))
    # Publication location is absent from both documents; exact equality is intentional.
    if retained_summary != recomputed:
        raise SessionError("retained decode summary contradicts authenticated slot evidence")
    _require_profile_binding(retained_summary, plan)
    if (
        observed_audio_slots != planned_slots
        or observed_decoder_slots != planned_slots
        or retained_summary["slots"] != planned_slots
    ):
        raise SessionError("retained frame evidence UTC slots differ from the session plan")
    first_audio = load_json_document(evidence.audio_conversions[0], "audio-conversion.schema.json")
    capture = first_audio["capture"]
    if capture["retained_sample_count"] != plan.receiver_run.limits.sample_count:
        raise SessionError("coherent capture sample count differs from the session plan")
    expected_start = _utc(plan.first_slot_utc - timedelta(seconds=5))
    if capture["retained_capture_start_utc"] != expected_start:
        raise SessionError("coherent capture UTC start differs from the session plan")
    return str(retained_summary["gate_outcome"])


def _artifact_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if {"path", "size_bytes", "sha256"} <= value.keys():
            records.append(value)
        for child in value.values():
            records.extend(_artifact_records(child))
    elif isinstance(value, list):
        for child in value:
            records.extend(_artifact_records(child))
    return records


def _publish_offline_evidence(
    bundle: Path,
    evidence: OfflineEvidenceSet,
    *,
    include_frames: bool,
    injection: Injection,
) -> None:
    sources: list[tuple[str, Path, str]] = [
        ("carrier-analysis.json", evidence.carrier_analysis, "carrier-analysis.schema.json"),
    ]
    if include_frames:
        sources.append(
            ("decode-summary.json", evidence.decode_summary, "decode-summary.schema.json")
        )
        sources.extend(
            (f"audio-{index}.json", path, "audio-conversion.schema.json")
            for index, path in enumerate(evidence.audio_conversions)
        )
        sources.extend(
            (f"decoder-{index}.json", path, "decoder-evidence.schema.json")
            for index, path in enumerate(evidence.decoder_evidence)
        )
    records: list[dict[str, Any]] = []
    dependencies: dict[Path, dict[str, Any]] = {}
    for name, source, schema in sources:
        document = load_json_document(source, schema)
        write_json_new(bundle / name, document, schema_name=schema)
        if injection is Injection.COPY_FAILED:
            raise SessionError("injected derivative copy failure")
        source_record = artifact(source)
        retained_record = artifact(bundle / name)
        retained_record["path"] = name
        records.append(
            {
                "role": name.removesuffix(".json"),
                "source": source_record,
                "disposition": "bundled",
                "retained_path": name,
                "retained": retained_record,
            }
        )
        for record in _artifact_records(document):
            dependency = Path(record["path"])
            if dependency.exists():
                dependencies[dependency.resolve()] = record
    retained_root = bundle / "retained-artifacts"
    retained_root.mkdir()
    for index, (source, recorded) in enumerate(
        sorted(dependencies.items(), key=lambda item: str(item[0]))
    ):
        authenticated = artifact(source)
        if (
            authenticated["size_bytes"] != recorded["size_bytes"]
            or authenticated["sha256"] != recorded["sha256"]
        ):
            raise SessionError(f"source artifact changed before publication: {source}")
        if source.suffix.lower() == ".cf32":
            records.append(
                {
                    "role": "raw_iq",
                    "source": authenticated,
                    "disposition": "external",
                    "retained_path": None,
                    "format": "CF32",
                    "sample_count": authenticated["size_bytes"] // 8,
                }
            )
            continue
        destination = retained_root / f"{index:03d}-{source.name}"
        shutil.copyfile(source, destination)
        retained = artifact(destination)
        retained_relative = str(destination.relative_to(bundle).as_posix())
        retained["path"] = retained_relative
        records.append(
            {
                "role": "retained_dependency",
                "source": authenticated,
                "disposition": "bundled",
                "retained_path": retained_relative,
                "retained": retained,
            }
        )
    write_json_new(
        bundle / "offline-evidence-index.json",
        {
            "schema_version": 1,
            "evidence_type": "slice6_offline_evidence_index",
            "artifacts": records,
        },
        schema_name="slice6-offline-evidence-index.schema.json",
    )
    if injection is Injection.INDEX_FAILED:
        raise SessionError("injected evidence-index publication failure")


def validate_published_bundle(bundle: Path) -> None:
    """Authenticate bundled bytes and manifest without consulting fixture sources."""
    index_path = bundle / "offline-evidence-index.json"
    if not index_path.exists():
        # Preflight-only runs legitimately have no consumed offline measurement evidence.
        return
    index = load_json_document(
        index_path,
        "slice6-offline-evidence-index.schema.json",
    )
    for record in index["artifacts"]:
        if record["disposition"] != "bundled":
            continue
        retained_path = bundle / record["retained_path"]
        retained = artifact(retained_path)
        retained["path"] = record["retained_path"]
        if retained != record["retained"]:
            raise SessionError(f"bundled artifact contradicts its index: {retained_path}")
    manifest = bundle / "SHA256SUMS"
    if not manifest.is_file():
        raise SessionError("published bundle lacks SHA256SUMS")
    recorded = {
        line.split("  ", 1)[1]: line.split("  ", 1)[0]
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if "  " in line
    }
    for path in bundle.rglob("*"):
        if path.is_file() and path.name != "SHA256SUMS":
            relative = path.relative_to(bundle).as_posix()
            if recorded.get(relative) != artifact(path)["sha256"]:
                raise SessionError(f"manifest does not authenticate bundled artifact: {relative}")


def validate_session_document(document: dict[str, Any]) -> None:
    validate_document(document, "slice6-session.schema.json")
    if document["final_status"] == "qualified":
        raise SessionError("mock session evidence cannot be qualified")
    events = document["events"]
    if not isinstance(events, list) or [item["sequence"] for item in events] != list(
        range(1, len(events) + 1)
    ):
        raise SessionError("session event sequence is not contiguous")
    phases = [item["phase"] for item in events]
    if "rf_idle_verified" in phases and (
        "cleanup_installed" not in phases
        or phases.index("cleanup_installed") > phases.index("rf_idle_verified")
    ):
        raise SessionError("cleanup was not installed before the simulated enable boundary")
    carrier = [item for item in events if item["phase"] == "carrier_gate"]
    if document["frames_started"] and (len(carrier) != 1 or carrier[0]["outcome"] != "passed"):
        raise SessionError("frame evidence advanced without one passing carrier gate")
    supervisors = document["supervisors"]
    if not isinstance(supervisors, list):
        raise SessionError("supervisors must be an array")
    for supervisor in supervisors:
        validate_supervisor_document(supervisor)
    cleanup_states = [supervisor["cleanup_outcome"] for supervisor in supervisors]
    if document["final_status"] == "cleanup_failed" and "failed" not in cleanup_states:
        raise SessionError("cleanup_failed lacks a failed supervisor cleanup outcome")
    if document["final_status"] != "cleanup_failed" and "failed" in cleanup_states:
        raise SessionError("failed supervisor cleanup lacks cleanup_failed precedence")
    carrier_indices = [
        index for index, item in enumerate(events) if item["phase"] == "carrier_gate"
    ]
    frame_indices = [index for index, item in enumerate(events) if item["phase"] == "wspr_frames"]
    if carrier_indices:
        carrier_index = carrier_indices[0]
        prior = [item["phase"] for item in events[:carrier_index]]
        if "cleanup" not in prior or "quiescence" not in prior or len(supervisors) < 1:
            raise SessionError("carrier analysis lacks preceding lifecycle cleanup evidence")
    if document["frames_started"]:
        if len(supervisors) != 2 or not frame_indices:
            raise SessionError("frame analysis lacks its second supervisor lifecycle")
        frame_index = frame_indices[-1]
        prior = [item["phase"] for item in events[:frame_index]]
        if prior.count("cleanup") < 2 or prior.count("quiescence") < 2:
            raise SessionError("frame analysis lacks preceding cleanup and quiescence")
    confirmation = document["runtime_confirmation"]
    if confirmation is not None and "runtime_confirmed" in phases:
        payload = json.dumps(
            confirmation["resolved_plan"],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if (
            hashlib.sha256(payload.encode("utf-8")).hexdigest()
            != confirmation["resolved_plan_sha256"]
        ):
            raise SessionError("runtime confirmation digest contradicts its resolved plan")


class QualificationSession:
    """Single-use deterministic mock coordinator; it has no execution adapters."""

    def __init__(self, plan: QualificationSessionPlan, *, now: datetime) -> None:
        self.plan = plan
        self.now = now
        self._used = False

    def run(
        self,
        confirmation: RuntimeConfirmation | None,
        output_parent: Path,
        *,
        injection: Injection = Injection.NONE,
    ) -> dict[str, Any]:
        if self._used:
            raise SessionError("qualification session coordinators are single-use")
        self._used = True
        if output_parent.exists() and not output_parent.is_dir():
            raise SessionError("evidence parent must be a directory")
        output_parent.mkdir(parents=True, exist_ok=True)
        final_bundle = output_parent / self.plan.run_id
        bundle = output_parent / f".incomplete-{self.plan.run_id}"
        if final_bundle.exists() or bundle.exists():
            raise SessionError("refusing to reuse an evidence directory")
        bundle.mkdir()
        events: list[dict[str, object]] = []

        def event(phase: SessionPhase, outcome: str, detail: str) -> None:
            events.append(
                {
                    "sequence": len(events) + 1,
                    "timestamp_utc": _utc(self.now),
                    "phase": phase.value,
                    "outcome": outcome,
                    "detail": detail,
                }
            )

        event(SessionPhase.REQUESTED, "recorded", "mock-only session requested")
        preflight, carrier, decode = True, GateOutcome.NOT_RUN, GateOutcome.NOT_RUN
        cleanup = CleanupOutcome.VERIFIED
        causes: list[FailureCause] = []
        frames_started = False
        supervisor_documents: list[dict[str, object]] = []
        authenticated_carrier: str | None = None
        authenticated_decode: str | None = None

        def supervise(name: str, monitor_s: float) -> tuple[str, str]:
            cancel = threading.Event()
            if injection is Injection.CANCELLED:
                cancel.set()
            receiver = MockReceiverAdapter(
                f"mock_receiver_{name}",
                fail_at={"acquire"} if injection is Injection.RECEIVER_LAUNCH_FAILED else set(),
                block_at={"start"} if injection is Injection.CHILD_TIMEOUT else set(),
            )
            transmitter = MockTransmitterAdapter(
                f"mock_transmitter_{name}",
                fail_at=(
                    {"start"}
                    if injection is Injection.TRANSMITTER_LAUNCH_FAILED
                    else {"stop"}
                    if injection is Injection.CLEANUP_FAILED
                    else set()
                ),
            )
            service = MockServiceAdapter(
                True, fail_restore=injection is Injection.SERVICE_RESTORE_FAILED
            )
            service.set_running(False)
            quiescence = MockQuiescenceAdapter(
                self.plan.application.backend,
                verified=injection is not Injection.QUIESCENCE_FAILED,
            )
            deadlines = OperationDeadlines(monitor_s=monitor_s, overall_s=monitor_s + 12)
            result = Supervisor(
                receiver,
                transmitter,
                service=service,
                quiescence=quiescence,
                clock=lambda: self.now,
            ).run(ResolvedPlan(f"{self.plan.run_id}-{name}", deadlines), cancellation=cancel)
            document = result.to_document()
            validate_supervisor_document(document)
            supervisor_documents.append(document)
            return result.outcome, result.cleanup_outcome

        try:
            validate_session_plan(self.plan)
            if injection is Injection.INVALID_PLAN:
                raise SessionError("injected invalid plan")
            event(SessionPhase.VALIDATED, "passed", "profiles and application plan reconciled")
            digest = resolved_plan_sha256(self.plan)
            if confirmation is None or not confirmation.confirmed:
                raise SessionError("ephemeral runtime confirmation is required")
            if (
                confirmation.resolved_plan_sha256 != digest
                or injection is Injection.CONFIRMATION_MISMATCH
            ):
                raise SessionError("runtime confirmation does not match the resolved plan")
            event(SessionPhase.CONFIRMED, "passed", "ephemeral confirmation matches plan digest")
            fixture_preflight = {
                Injection.MISSING_CAPABILITY: FailureCause.UNSUPPORTED_CAPABILITY,
                Injection.MISSING_DEPENDENCY: FailureCause.DEPENDENCY_UNAVAILABLE,
                Injection.UNSAFE_RF_PATH: FailureCause.RF_PATH_UNSAFE,
                Injection.RECEIVER_MISMATCH: FailureCause.RECEIVER_UNAVAILABLE,
                Injection.OWNERSHIP_CONFLICT: FailureCause.OWNERSHIP_CONFLICT,
                Injection.SOURCE_MISMATCH: FailureCause.PREFLIGHT,
            }
            if injection in fixture_preflight:
                causes.append(fixture_preflight[injection])
                preflight = injection is not Injection.SOURCE_MISMATCH
                event(SessionPhase.PREFLIGHT, "blocked", injection.value)
            else:
                if self.plan.offline_evidence is None:
                    raise SessionError("retained Slice 3 evidence is required")
                authenticated_carrier = _load_carrier_evidence(
                    self.plan.offline_evidence, self.plan
                )
                event(SessionPhase.PREFLIGHT, "passed", "all mock capabilities available")
                event(SessionPhase.CLEANUP_INSTALLED, "passed", "cleanup precedes enable boundary")
                if injection is Injection.RF_IDLE_FAILURE:
                    causes.append(FailureCause.RF_PATH_UNSAFE)
                    carrier = GateOutcome.BLOCKED
                    event(SessionPhase.RF_IDLE, "blocked", injection.value)
                else:
                    event(SessionPhase.RF_IDLE, "passed", "mock backend and path idle")
                    supervisor_outcome, supervisor_cleanup = supervise(
                        "carrier", self.plan.transmitter_deadline_s
                    )
                    event(SessionPhase.CLEANUP, supervisor_cleanup, "carrier mock lifecycle")
                    event(
                        SessionPhase.QUIESCENCE,
                        "passed" if supervisor_cleanup == "verified" else "failed",
                        "carrier mock lifecycle",
                    )
                    if supervisor_cleanup == "failed":
                        cleanup = CleanupOutcome.FAILED
                        causes.append(FailureCause.CLEANUP)
                    if supervisor_outcome == "aborted":
                        causes.append(FailureCause.OPERATOR_ABORT)
                    elif supervisor_outcome == "failed":
                        causes.append(FailureCause.DEPENDENCY_UNAVAILABLE)
                    if supervisor_outcome == "completed" and supervisor_cleanup == "verified":
                        if authenticated_carrier is None:
                            raise SessionError(
                                "carrier evidence was not authenticated in preflight"
                            )
                        carrier = GateOutcome(authenticated_carrier)
                        if carrier is GateOutcome.BLOCKED:
                            causes.append(FailureCause.RECEIVER_UNAVAILABLE)
                        elif carrier is GateOutcome.FAILED:
                            causes.append(FailureCause.TRANSMITTER_CARRIER)
                        elif carrier is not GateOutcome.PASSED:
                            causes.append(FailureCause.INCOMPLETE_EVIDENCE)
                        event(
                            SessionPhase.CARRIER,
                            carrier.value,
                            "post-lifecycle authentication of retained carrier evidence",
                        )
                        if carrier is GateOutcome.PASSED and not causes:
                            authenticated_decode = _load_decode_evidence(
                                self.plan.offline_evidence, self.plan
                            )
                            frames_started = True
                            event(
                                SessionPhase.CLEANUP_INSTALLED,
                                "passed",
                                "fresh frame lifecycle cleanup precedes enable boundary",
                            )
                            frame_outcome, frame_cleanup = supervise(
                                "frames", self.plan.receiver_deadline_s
                            )
                            event(SessionPhase.CLEANUP, frame_cleanup, "frame mock lifecycle")
                            event(
                                SessionPhase.QUIESCENCE,
                                "passed" if frame_cleanup == "verified" else "failed",
                                "frame mock lifecycle",
                            )
                            if frame_cleanup == "failed":
                                cleanup = CleanupOutcome.FAILED
                                causes.append(FailureCause.CLEANUP)
                            if frame_outcome == "aborted":
                                causes.append(FailureCause.OPERATOR_ABORT)
                            elif frame_outcome == "failed":
                                causes.append(FailureCause.DEPENDENCY_UNAVAILABLE)
                            if frame_outcome == "completed" and frame_cleanup == "verified":
                                if authenticated_decode is None:
                                    raise SessionError("frame evidence was not authenticated")
                                decode = GateOutcome(authenticated_decode)
                                if decode is GateOutcome.BLOCKED:
                                    causes.append(FailureCause.DEPENDENCY_UNAVAILABLE)
                                elif decode is GateOutcome.FAILED:
                                    causes.append(FailureCause.TRANSMITTER_DECODE)
                                elif decode is not GateOutcome.PASSED:
                                    causes.append(FailureCause.CONTRADICTORY_EVIDENCE)
                                event(
                                    SessionPhase.FRAMES,
                                    decode.value,
                                    "post-lifecycle authentication of retained frame evidence",
                                )
                            else:
                                event(
                                    SessionPhase.FRAMES,
                                    "not_run",
                                    "frame lifecycle did not complete with verified cleanup",
                                )
            event(SessionPhase.CLEANUP, cleanup.value, injection.value)
            event(
                SessionPhase.QUIESCENCE,
                "passed" if cleanup is CleanupOutcome.VERIFIED else "failed",
                "mock backend inspection",
            )
        except (SessionError, OfflineAnalysisError, ValueError) as error:
            if carrier is GateOutcome.PASSED:
                decode = GateOutcome.INCONCLUSIVE
                causes.append(FailureCause.CONTRADICTORY_EVIDENCE)
                event(SessionPhase.FRAMES, "inconclusive", str(error))
            else:
                preflight = False
                causes.append(FailureCause.PREFLIGHT)
                event(SessionPhase.PREFLIGHT, "failed", str(error))
            event(SessionPhase.CLEANUP, cleanup.value, "no simulated output enabled")
        if carrier is not GateOutcome.PASSED and frames_started:
            raise SessionError("internal invariant: frames advanced without passing carrier")
        if carrier is GateOutcome.PASSED and decode is GateOutcome.PASSED and not causes:
            causes.append(
                FailureCause.INCOMPLETE_EVIDENCE
            )  # Mock evidence can never qualify hardware.
        result = QualificationResult(
            self.plan.run_id,
            self.now,
            self.now,
            preflight,
            carrier,
            decode,
            cleanup,
            tuple(dict.fromkeys(causes)),
            (),
            "hardware-free simulation only",
        )
        result_document = result_to_document(result)
        validate_result_document(result_document)
        plan_document = self.plan.resolved_document()
        confirmation_document = (
            None
            if confirmation is None
            else {
                "recorded_utc": _utc(confirmation.recorded_utc),
                "operator": confirmation.operator,
                "resolved_plan_sha256": confirmation.resolved_plan_sha256,
                "confirmed": confirmation.confirmed,
                "resolved_plan": plan_document,
            }
        )
        event(SessionPhase.PUBLISHED, "started", "new-file-only evidence publication")
        session_document = {
            "schema_version": 1,
            "evidence_type": "slice6_mock_session",
            "run_id": self.plan.run_id,
            "mock_only": True,
            "injection": injection.value,
            "events": events,
            "frames_started": frames_started,
            "runtime_confirmation": confirmation_document,
            "final_status": result.status.value,
            "supervisors": supervisor_documents,
        }
        validate_session_document(session_document)
        try:
            if self.plan.offline_evidence is not None:
                _publish_offline_evidence(
                    bundle,
                    self.plan.offline_evidence,
                    include_frames=frames_started,
                    injection=injection,
                )
            write_json_new(
                bundle / "resolved-session-plan.json",
                plan_document,
                schema_name="slice6-session-plan.schema.json",
            )
            write_json_new(
                bundle / "runtime-confirmation.json", {"confirmation": confirmation_document}
            )
            write_json_new(
                bundle / "session.json",
                session_document,
                schema_name="slice6-session.schema.json",
            )
            write_json_new(
                bundle / "result.json", result_document, schema_name="result.schema.json"
            )
            write_manifest(bundle)
            if injection is Injection.MANIFEST_FAILED:
                raise SessionError("injected manifest failure")
            if injection is Injection.PROMOTION_FAILED:
                raise SessionError("injected final promotion failure")
            bundle.replace(final_bundle)
            validate_published_bundle(final_bundle)
        except Exception:
            if bundle.exists():
                shutil.rmtree(bundle)
            raise
        return {
            "bundle": str(final_bundle.resolve()),
            "session": session_document,
            "result": result_document,
        }
