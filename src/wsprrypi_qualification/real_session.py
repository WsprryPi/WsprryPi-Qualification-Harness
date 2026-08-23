"""Fail-closed real-session composition for hardware-free and explicitly live adapters."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol, cast

from wsprrypi_qualification.manifests import write_manifest
from wsprrypi_qualification.offline import artifact, validate_document, write_json_new
from wsprrypi_qualification.real_capabilities import RuntimeAuthorization
from wsprrypi_qualification.receiver_calibration import (
    ReceiverCalibrationError,
    interpret_frequency,
    validate_live_binding,
)
from wsprrypi_qualification.receiver_calibration import (
    validate_binding as validate_receiver_calibration,
)
from wsprrypi_qualification.results import validate_result_document


class RealSessionError(RuntimeError):
    """A real-session invariant failed before qualification could be established."""


class RealFixtureBlocked(RealSessionError):
    """The receiver, RF path, ownership, or required fixture is unavailable."""


HELPER_VERIFICATION_OPERATIONS = (
    "transmitter_service_inspect",
    "receiver_service_inspect",
    "parent_revision_inspect",
    "submodule_revision_inspect",
)


def helper_verification_deadline(plan: dict[str, Any]) -> float:
    """Return the explicit aggregate bound for sequential helper verification."""
    return cast(float, plan["deadlines"]["helper_s"]) * len(HELPER_VERIFICATION_OPERATIONS)


def helper_verification_contract(plan: dict[str, Any]) -> dict[str, object]:
    return {
        "operations": list(HELPER_VERIFICATION_OPERATIONS),
        "per_operation_deadline_s": plan["deadlines"]["helper_s"],
        "aggregate_deadline_s": helper_verification_deadline(plan),
    }


class RealPhase(StrEnum):
    REQUESTED = "requested"
    VALIDATED = "validated"
    CAPABILITIES = "capabilities_discovered"
    CONFIRMED = "runtime_confirmed"
    HELPER = "helper_verified"
    SERVICES = "services_and_ownership_verified"
    RF_IDLE = "rf_idle_verified"
    CLEANUP_INSTALLED = "cleanup_installed"
    RF_OFF = "rf_off_captured"
    CARRIER = "carrier_transmitted_and_captured"
    CARRIER_GATE = "carrier_gate"
    FRAMES = "wspr_frames"
    DERIVATIVES = "wav_and_decode"
    CLEANUP = "cleanup"
    QUIESCENCE = "quiescence"
    PUBLISHED = "published"


@dataclass(frozen=True)
class RealRuntimeAuthorization:
    kind: str
    operator: str
    recorded_utc: datetime
    resolved_plan_sha256: str
    authorized: bool

    def document(self) -> dict[str, object]:
        document = {
            "schema_version": 1,
            "evidence_type": "real_runtime_authorization",
            "kind": self.kind,
            "operator": self.operator,
            "recorded_utc": _utc(self.recorded_utc),
            "resolved_plan_sha256": self.resolved_plan_sha256,
            "authorized": self.authorized,
        }
        validate_document(document, "real-runtime-authorization.schema.json")
        return document


@dataclass(frozen=True)
class ResolvedRealSessionPlan:
    document: dict[str, Any]

    def validated(self) -> dict[str, Any]:
        validate_real_session_plan(self.document)
        return self.document

    @property
    def sha256(self) -> str:
        return resolved_real_plan_sha256(self.validated())


class RealSessionAdapters(Protocol):
    """Granular production-adapter composition boundary."""

    def discover_capabilities(self, plan: dict[str, Any]) -> dict[str, object]: ...
    def verify_helper(self, plan: dict[str, Any]) -> dict[str, object]: ...
    def inspect_services_and_ownership(self, plan: dict[str, Any]) -> dict[str, object]: ...
    def verify_rf_idle(self, plan: dict[str, Any]) -> dict[str, object]: ...
    def install_cleanup(self, plan: dict[str, Any]) -> dict[str, object]: ...
    def capture_rf_off(self, plan: dict[str, Any]) -> dict[str, object]: ...
    def transmit_carrier_and_capture_rf_on(
        self, plan: dict[str, Any], authorization: RuntimeAuthorization
    ) -> dict[str, object]: ...
    def analyze_carrier(
        self, plan: dict[str, Any], rf_off: dict[str, object], rf_on: dict[str, object]
    ) -> dict[str, object]: ...
    def transmit_frames_and_capture(
        self, plan: dict[str, Any], authorization: RuntimeAuthorization
    ) -> dict[str, object]: ...
    def create_wavs_and_decode(
        self, plan: dict[str, Any], coherent_capture: dict[str, object]
    ) -> dict[str, object]: ...
    def cleanup(self, plan: dict[str, Any]) -> dict[str, object]: ...
    def verify_quiescence(self, plan: dict[str, Any]) -> dict[str, object]: ...
    def close(self) -> bool: ...

    @property
    def execution_mode(self) -> str: ...


class RealQualificationSession:
    """Single-use lifecycle coordinator with an execution-mode-bound adapter."""

    def __init__(
        self,
        plan: ResolvedRealSessionPlan,
        adapters: RealSessionAdapters,
        *,
        now: datetime,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.plan, self.adapters, self.now = plan, adapters, now
        self.clock = clock or (lambda: datetime.now(UTC))
        self._used = False

    def run(
        self,
        external_authorization: RealRuntimeAuthorization | None,
        rf_authorization: RealRuntimeAuthorization | None,
        output_parent: Path,
    ) -> dict[str, Any]:
        if self._used:
            raise RealSessionError("real qualification coordinators are single-use")
        self._used = True
        plan = self.plan.validated()
        adapter_mode = getattr(self.adapters, "execution_mode", "hardware_free_validation")
        if adapter_mode != plan["execution_mode"]:
            raise RealSessionError("adapter execution mode differs from the resolved plan")
        if plan["execution_mode"] == "live":
            from wsprrypi_qualification.live_adapters import ProductionRealSessionAdapters

            if type(self.adapters) is not ProductionRealSessionAdapters:
                raise RealSessionError("live execution requires the sealed production adapter")
            try:
                validate_live_binding(
                    plan["receiver_calibration"],
                    receiver=plan["receiver"],
                    indicated_frequencies_hz=[float(plan["frequency_hz"])],
                    execution_time=self.now,
                )
            except ReceiverCalibrationError as error:
                raise RealSessionError(str(error)) from error
            self.adapters.begin_session(plan)
        run_id = cast(str, plan["run_id"])
        parent = output_parent.resolve()
        final = parent / run_id
        temporary = parent / f".incomplete-{run_id}"
        if final.parent != parent or temporary.parent != parent:
            if plan["execution_mode"] == "live" and not self.adapters.close():
                raise RealSessionError("run ID invalid and helper cleanup was not verified")
            raise RealSessionError("run ID escapes the evidence parent")
        if final.exists() or temporary.exists():
            if plan["execution_mode"] == "live" and not self.adapters.close():
                raise RealSessionError("output conflict and helper cleanup was not verified")
            raise RealSessionError("refusing to reuse an evidence directory")
        parent.mkdir(parents=True, exist_ok=True)
        temporary.mkdir()
        events: list[dict[str, object]] = []
        cleanup_required = False
        cleanup_document: dict[str, object] | None = None
        quiescence_document: dict[str, object] | None = None
        final_status = "inconclusive"
        carrier_gate = "not_run"
        decode_gate = "not_run"
        failure_causes: list[str] = []
        preflight_passed = False
        evidence: dict[str, object] = {}
        run_started = self.clock()

        def event(phase: RealPhase, outcome: str, detail: str) -> None:
            events.append(
                {
                    "sequence": len(events) + 1,
                    "timestamp_utc": _utc(self.clock()),
                    "phase": phase.value,
                    "outcome": outcome,
                    "detail": detail,
                }
            )

        event(RealPhase.REQUESTED, "recorded", "real session requested")
        try:
            event(RealPhase.VALIDATED, "passed", "resolved plan is schema-valid")
            _require_authorization(external_authorization, "external_access", self.plan.sha256)
            _require_authorization(rf_authorization, "rf", self.plan.sha256)
            assert rf_authorization is not None
            event(RealPhase.CONFIRMED, "passed", "ephemeral authorizations match plan")
            capabilities = self.adapters.discover_capabilities(plan)
            _validate_stage(
                capabilities,
                "capabilities",
                self.plan.sha256,
                "passed",
                plan["deadlines"]["helper_s"],
            )
            if capabilities["details"] != {"bindings": plan["capability_bindings"]}:
                raise RealSessionError("capability evidence differs from resolved bindings")
            evidence["capabilities"] = capabilities
            event(RealPhase.CAPABILITIES, "passed", "all required capabilities discovered")
            helper = self.adapters.verify_helper(plan)
            _validate_stage(
                helper,
                "helper",
                self.plan.sha256,
                "passed",
                helper_verification_deadline(plan),
            )
            helper_details = cast(dict[str, object], helper["details"])
            expected_helpers: dict[str, object] = {
                "transmitter": {
                    key: plan["remote_helper"][key]
                    for key in (
                        "host",
                        "path",
                        "sha256",
                        "identity",
                        "config_path",
                        "config_sha256",
                    )
                },
                "receiver": {
                    key: plan["receiver_helper"][key]
                    for key in (
                        "host",
                        "path",
                        "sha256",
                        "identity",
                        "config_path",
                        "config_sha256",
                    )
                },
            }
            expected_helpers["verification_contract"] = helper_verification_contract(plan)
            if helper_details != expected_helpers:
                raise RealSessionError("helper evidence differs from the resolved helper")
            evidence["helper"] = helper
            event(RealPhase.HELPER, "passed", "remote helper identity verified")
            ownership = self.adapters.inspect_services_and_ownership(plan)
            _validate_stage(
                ownership, "ownership", self.plan.sha256, "passed", plan["deadlines"]["helper_s"]
            )
            expected_ownership = {
                "transmitter": {
                    "host": plan["host"],
                    "services": plan["services"]["transmitter"],
                    "conflicts": [],
                },
                "receiver": {
                    "host": plan["receiver"]["host"],
                    "services": plan["services"]["receiver"],
                    "conflicts": [],
                },
            }
            if ownership["details"] != expected_ownership:
                raise RealSessionError("ownership evidence differs from the resolved host/services")
            evidence["ownership"] = ownership
            event(RealPhase.SERVICES, "passed", "service and ownership preflight passed")
            idle = self.adapters.verify_rf_idle(plan)
            _validate_stage(
                idle, "rf_idle", self.plan.sha256, "passed", plan["deadlines"]["helper_s"]
            )
            _validate_quiescence_details(idle, plan)
            evidence["initial_rf_idle"] = idle
            event(RealPhase.RF_IDLE, "passed", "backend-specific RF idle verified")
            cleanup_required = True
            installed = self.adapters.install_cleanup(plan)
            _validate_stage(
                installed,
                "cleanup_registration",
                self.plan.sha256,
                "passed",
                plan["deadlines"]["cleanup_s"],
            )
            if installed["details"] != {
                "installed": True,
                "deadline_s": plan["deadlines"]["cleanup_s"],
            }:
                raise RealSessionError("cleanup registration differs from the resolved deadline")
            evidence["cleanup_registration"] = installed
            event(RealPhase.CLEANUP_INSTALLED, "passed", "cleanup installed before RF enable")
            preflight_passed = True
            rf_off = self.adapters.capture_rf_off(plan)
            _validate_capture(rf_off, plan, "rf_off", plan["carrier"]["rf_off_sample_count"])
            evidence["rf_off"] = rf_off
            event(RealPhase.RF_OFF, "completed", "RF-off exact-count capture retained")
            runtime_rf = RuntimeAuthorization(
                self.plan.sha256, rf_authorization.operator, self.now, True, True
            )
            rf_on = self.adapters.transmit_carrier_and_capture_rf_on(plan, runtime_rf)
            _validate_capture(rf_on, plan, "rf_on", plan["carrier"]["rf_on_sample_count"])
            evidence["rf_on"] = rf_on
            event(RealPhase.CARRIER, "completed", "bounded carrier lifecycle completed")
            carrier = self.adapters.analyze_carrier(plan, rf_off, rf_on)
            carrier_gate = _validate_carrier(carrier, plan, self.plan.sha256)
            evidence["carrier"] = carrier
            event(RealPhase.CARRIER_GATE, carrier_gate, "authenticated carrier analysis")
            if carrier_gate == "passed" and plan.get("session_kind") == "cw_live_tone":
                final_status = "inconclusive"
                failure_causes.append("phase7_live_tone_only")
            elif carrier_gate == "passed":
                coherent = self.adapters.transmit_frames_and_capture(plan, runtime_rf)
                _validate_capture(
                    coherent, plan, "coherent", plan["coherent_capture"]["sample_count"]
                )
                evidence["coherent_capture"] = coherent
                event(RealPhase.FRAMES, "completed", "three bounded frames and coherent capture")
                decode = self.adapters.create_wavs_and_decode(plan, coherent)
                decode_gate = _validate_decode(decode, plan, self.plan.sha256)
                evidence["decode"] = decode
                event(RealPhase.DERIVATIVES, decode_gate, "three independent decoder results")
                if decode_gate == "passed":
                    final_status = "qualified"
                elif decode_gate == "failed":
                    final_status = "unqualified_decode"
                elif decode_gate == "blocked":
                    final_status = "fixture_blocked"
                else:
                    final_status = "inconclusive"
            elif carrier_gate == "failed":
                final_status = "unqualified_carrier"
            elif carrier_gate == "blocked":
                final_status = "fixture_blocked"
            else:
                final_status = "inconclusive"
        except Exception as exc:
            failure_causes.append(f"{type(exc).__name__}: {exc}")
            if isinstance(exc, RealFixtureBlocked):
                final_status = "fixture_blocked"
            else:
                final_status = "preflight_failed" if not preflight_passed else "aborted"
        finally:
            if cleanup_required:
                try:
                    cleanup_document = self.adapters.cleanup(plan)
                    if cleanup_document["outcome"] == "failed":
                        validate_document(
                            cleanup_document, "real-session-stage-evidence.schema.json"
                        )
                        cleanup_ok = False
                    else:
                        _validate_stage(
                            cleanup_document,
                            "cleanup",
                            self.plan.sha256,
                            "verified",
                            plan["deadlines"]["cleanup_s"],
                        )
                        cleanup_ok = True
                    if cleanup_ok and cleanup_document["details"] != {
                        "actions_complete": True,
                        "helper_absent": True,
                    }:
                        raise RealSessionError("cleanup evidence is not complete")
                except Exception as exc:
                    cleanup_document = _failed_stage(
                        "cleanup", self.plan.sha256, exc, plan["deadlines"]["cleanup_s"], plan
                    )
                    cleanup_ok = False
                event(RealPhase.CLEANUP, "verified" if cleanup_ok else "failed", "final cleanup")
                try:
                    quiescence_document = self.adapters.verify_quiescence(plan)
                    _validate_stage(
                        quiescence_document,
                        "quiescence",
                        self.plan.sha256,
                        "verified",
                        plan["deadlines"]["cleanup_s"],
                    )
                    _validate_quiescence_details(quiescence_document, plan)
                    quiescence_ok = True
                except Exception as exc:
                    quiescence_document = _failed_stage(
                        "quiescence",
                        self.plan.sha256,
                        exc,
                        plan["deadlines"]["cleanup_s"],
                        plan,
                    )
                    quiescence_ok = False
                event(
                    RealPhase.QUIESCENCE,
                    "verified" if quiescence_ok else "failed",
                    "backend-specific final quiescence",
                )
                if not cleanup_ok or not quiescence_ok:
                    final_status = "cleanup_failed"
            elif plan["execution_mode"] == "live" and not self.adapters.close():
                failure_causes.append("persistent helper preflight cleanup was not verified")
                cleanup_document = _failed_stage(
                    "cleanup",
                    self.plan.sha256,
                    RealSessionError(failure_causes[-1]),
                    plan["deadlines"]["cleanup_s"],
                    plan,
                )
                quiescence_document = _failed_stage(
                    "quiescence",
                    self.plan.sha256,
                    RealSessionError("quiescence unavailable after helper cleanup failure"),
                    plan["deadlines"]["cleanup_s"],
                    plan,
                )
                event(RealPhase.CLEANUP, "failed", "preflight helper cleanup")
                event(RealPhase.QUIESCENCE, "failed", "preflight quiescence unavailable")
                final_status = "cleanup_failed"
        if plan["execution_mode"] == "hardware_free_validation" and final_status == "qualified":
            final_status = "inconclusive"
            failure_causes.append("incomplete_evidence")
        document = {
            "schema_version": 1,
            "evidence_type": "real_qualification_session",
            "run_id": run_id,
            "resolved_plan": plan,
            "resolved_plan_sha256": self.plan.sha256,
            "external_authorization": (
                None if external_authorization is None else external_authorization.document()
            ),
            "rf_authorization": None if rf_authorization is None else rf_authorization.document(),
            "events": events,
            "evidence": evidence,
            "carrier_gate": carrier_gate,
            "decode_gate": decode_gate,
            "cleanup": cleanup_document,
            "quiescence": quiescence_document,
            "failure_causes": failure_causes,
            "final_status": final_status,
        }
        validate_real_session_document(document)
        try:
            write_json_new(
                temporary / "resolved-real-session-plan.json",
                plan,
                schema_name="resolved-real-session-plan.schema.json",
            )
            write_json_new(
                temporary / "session.json",
                document,
                schema_name="real-qualification-session.schema.json",
            )
            calibration_files = [temporary / "receiver-calibration.json"]
            write_json_new(
                calibration_files[0],
                plan["receiver_calibration"],
                schema_name="receiver-calibration-binding.schema.json",
            )
            if plan["receiver_calibration"]["applied"]:
                for field, name in (
                    ("profile", "receiver-calibration-profile.json"),
                    ("application_request", "receiver-calibration-request.json"),
                ):
                    binding = plan["receiver_calibration"][field]
                    source = Path(binding["artifact"]["path"])
                    if source.is_symlink() or not source.is_file():
                        raise RealSessionError(
                            "receiver calibration source artifact is unavailable"
                        )
                    source_identity = artifact(source)
                    if any(
                        source_identity[key] != binding["artifact"][key]
                        for key in ("size_bytes", "sha256")
                    ):
                        raise RealSessionError("receiver calibration source artifact changed")
                    target = temporary / name
                    shutil.copyfile(source, target)
                    calibration_files.append(target)
            result_causes = _result_causes(final_status, failure_causes, preflight_passed)
            retained_artifacts: list[dict[str, object]] = []
            if plan["execution_mode"] == "live":
                publish = getattr(self.adapters, "publish_artifacts", None)
                if publish is None:
                    raise RealSessionError("live adapter cannot publish retained evidence")
                retained_artifacts = publish(temporary)
                validate_published = getattr(self.adapters, "validate_published_artifacts", None)
                if validate_published is None:
                    raise RealSessionError("live adapter cannot validate retained evidence")
                validate_published(temporary)
            retained_artifacts.extend(
                {
                    **artifact(path),
                    "path": path.name,
                }
                for path in calibration_files
            )
            result_document = {
                "schema_version": 1,
                "run_id": run_id,
                "status": final_status,
                "started_utc": _utc(run_started),
                "completed_utc": _utc(self.clock()),
                "preflight_passed": preflight_passed,
                "carrier_gate": carrier_gate,
                "decode_gate": decode_gate,
                "cleanup_outcome": (
                    "failed"
                    if final_status == "cleanup_failed"
                    else "verified"
                    if cleanup_required
                    else "not_required"
                ),
                "failure_causes": result_causes,
                "artifacts": retained_artifacts,
            }
            validate_result_document(result_document)
            write_json_new(
                temporary / "result.json", result_document, schema_name="result.schema.json"
            )
            write_manifest(temporary)
            temporary.rename(final)
        except Exception as exc:
            if temporary.exists() and plan["execution_mode"] != "live":
                shutil.rmtree(temporary)
            if temporary.exists():
                marker = temporary / "publication-failure.json"
                if not marker.exists():
                    write_json_new(
                        marker,
                        {
                            "schema_version": 1,
                            "run_id": run_id,
                            "status": "inconclusive",
                            "failure": f"{type(exc).__name__}: {exc}",
                            "recorded_utc": _utc(self.clock()),
                            "quarantined": True,
                        },
                    )
                with suppress(Exception):
                    write_manifest(temporary)
                raise RealSessionError(
                    "live evidence publication failed; quarantined bundle retained at "
                    f"{temporary}: {exc}"
                ) from exc
            raise RealSessionError(
                f"evidence publication failed and was rolled back: {exc}"
            ) from exc
        return document


def validate_real_session_plan(document: dict[str, Any]) -> None:
    validate_document(document, "resolved-real-session-plan.schema.json")
    try:
        validate_receiver_calibration(
            document["receiver_calibration"], receiver=document["receiver"]
        )
    except ReceiverCalibrationError as error:
        raise RealSessionError(str(error)) from error
    if document["execution_mode"] == "live" and document["receiver_calibration"]["synthetic"]:
        raise RealSessionError("synthetic receiver calibration cannot enter live execution")
    try:
        interpret_frequency(document["receiver_calibration"], float(document["frequency_hz"]))
    except ReceiverCalibrationError as error:
        raise RealSessionError(str(error)) from error
    session_kind = document.get("session_kind", "wspr_qualification")
    if session_kind == "wspr_qualification":
        if document["mode"] != "WSPR" or document["frame_count"] != 3:
            raise RealSessionError("WSPR qualification requires exactly three WSPR frames")
        if "tone_schedule" in document:
            raise RealSessionError("WSPR qualification cannot contain a live-tone schedule")
        if "cw_contract" in document:
            raise RealSessionError("WSPR qualification cannot contain a CW analyzer contract")
        capture = document["coherent_capture"]
        if (
            capture["duration_s"] != 370
            or capture["sample_rate_hz"] != 250000
            or capture["sample_count"] != 92500000
            or not 0 < capture["margin_before_first_slot_s"] <= 10
        ):
            raise RealSessionError(
                "coherent WSPR capture must be 370 s and 92,500,000 CF32 samples"
            )
        if document["deadlines"]["receiver_s"] <= 370:
            raise RealSessionError("WSPR receiver deadline must exceed its coherent capture")
    elif session_kind == "cw_live_tone":
        if document["mode"] != "TONE" or document["frame_count"] != 0:
            raise RealSessionError("carrier-only live sessions require TONE mode and zero frames")
        schedule = document.get("tone_schedule")
        if not isinstance(schedule, dict):
            raise RealSessionError("carrier-only live sessions require an exact tone schedule")
        if not isinstance(document.get("cw_contract"), dict):
            raise RealSessionError("carrier-only live sessions require a pinned analyzer contract")
        rf_on_seconds = schedule["cycles"] * schedule["on_seconds"]
        if abs(rf_on_seconds - schedule["maximum_rf_on_seconds"]) > 1e-9:
            raise RealSessionError("tone schedule exceeds its resolved RF-on bound")
        capture_seconds = (
            schedule["cycles"] * (schedule["off_seconds"] + schedule["on_seconds"])
            + schedule["off_seconds"]
        )
        expected_samples = round(capture_seconds * document["receiver"]["sample_rate_hz"])
        if document["carrier"]["rf_on_sample_count"] != expected_samples:
            raise RealSessionError("tone-pattern capture count differs from its exact schedule")
        if document["deadlines"]["overall_s"] <= capture_seconds:
            raise RealSessionError("overall deadline cannot contain the tone schedule")
    else:
        raise RealSessionError("unsupported real-session kind")
    if document["random_offset_enabled"] is not False:
        raise RealSessionError("random WSPR offset must be disabled")
    if document["external_access_enabled"] is not True or document["rf_enabled"] is not True:
        raise RealSessionError("real session plan must explicitly enable external access and RF")
    if document["backend"] not in {"gpio", "si5351"}:
        raise RealSessionError("real session requires supported backend quiescence")
    if (
        document["backend_contract"]["backend"] != document["backend"]
        or document["backend_contract"]["output"] != document["output"]
    ):
        raise RealSessionError("backend contract differs from resolved backend/output")
    if document["receiver_helper"]["host"] != document["receiver"]["host"]:
        raise RealSessionError("receiver helper differs from the resolved receiver host")
    if (
        document["transport_identity"]["controller_hostname"]
        != document["receiver"]["observed_local_hostname"]
    ):
        raise RealSessionError("controller identity differs from receiver host evidence")
    observed = document["receiver"]["observed_local_hostname"]
    if document["receiver"]["host"] not in {observed, f"{observed}.local"}:
        raise RealSessionError("resolved receiver host is not an allowed observed-host alias")
    if document["receiver"]["sample_rate_hz"] != 250000:
        raise RealSessionError("preserved receiver contract requires 250,000 samples/s")
    receiver_services = document["services"]["receiver"]
    required_receiver_services = document["services"].get("receiver_required", [])
    if not set(required_receiver_services).issubset(receiver_services):
        raise RealSessionError(
            "required receiver services must be included in the receiver service allowlist"
        )
    if set(document["capability_bindings"]) != {
        "transmitter_ssh",
        "receiver_transport",
        "soapy",
        "wsprrypi",
        "transmitter_service",
        "receiver_service",
        "quiescence",
        "decoder",
    }:
        raise RealSessionError("complete capability bindings are required")
    if document["capability_bindings"]["wsprrypi"] != document["wsprrypi"]["sha256"]:
        raise RealSessionError("WsprryPi capability binding differs from its executable")
    if document["capability_bindings"]["soapy"] != document["capture_helper"]["sha256"]:
        raise RealSessionError("Soapy capability binding differs from its executable")
    if document["capability_bindings"]["decoder"] != document["wsprd"]["sha256"]:
        raise RealSessionError("decoder capability binding differs from its executable")
    if (
        document["capability_bindings"]["quiescence"]
        != document["backend_contract"]["quiescence_provider_sha256"]
    ):
        raise RealSessionError("quiescence capability binding differs from its provider")
    if any(
        executable["host"] != document["host"]
        for executable in (document["remote_helper"], document["wsprrypi"])
    ):
        raise RealSessionError("helper or WsprryPi host differs from the resolved host")
    if document.get("session_kind") == "cw_live_tone":
        helper = document["remote_helper"]
        if not {"bounded_tone_endpoint", "wsprrypi_revision"} <= set(helper):
            raise RealSessionError("CW live Tone requires bounded Tone helper bindings")
        if helper["wsprrypi_revision"] != document["source"]["parent_revision"]:
            raise RealSessionError("bounded Tone WsprryPi revision differs from source provenance")
        on_ms = document["tone_schedule"]["on_seconds"] * 1000
        if (
            isinstance(document["frequency_hz"], bool)
            or int(document["frequency_hz"]) != document["frequency_hz"]
            or not 1 <= on_ms <= 60_000
            or int(on_ms) != on_ms
        ):
            raise RealSessionError("bounded Tone requires exact integer-Hz and millisecond bounds")
        transaction_s = document["tone_schedule"]["on_seconds"] + min(
            1.0, document["tone_schedule"]["off_seconds"] / 2
        )
        if document["deadlines"]["helper_s"] <= transaction_s:
            raise RealSessionError("helper deadline must exceed the bounded Tone transaction")
        tone_server = document.get("tone_server")
        if not isinstance(tone_server, dict):
            raise RealSessionError("CW live Tone requires a pinned loopback server process")
        endpoint = helper["bounded_tone_endpoint"]
        expected_arguments = [
            document["wsprrypi"]["path"],
            "-i",
            tone_server["configuration"]["path"],
            "--socket-port",
            str(endpoint["port"]),
            "--socket-loopback-only",
        ]
        if endpoint["host"] != "::1" or tone_server["arguments"] != expected_arguments:
            raise RealSessionError("bounded Tone server arguments differ from its endpoint")
        if tone_server["startup_seconds"] >= document["tone_schedule"]["off_seconds"]:
            raise RealSessionError(
                "bounded Tone server startup must fit inside leading RF-off time"
            )
    if any(
        executable["host"] != document["receiver"]["host"]
        for executable in (
            document["receiver_helper"],
            document["capture_helper"],
            document["wsprd"],
        )
    ):
        raise RealSessionError("receiver tool host differs from the resolved receiver host")
    slots = [
        datetime.fromisoformat(value.replace("Z", "+00:00")) for value in document["slots_utc"]
    ]
    if any(slot.minute % 2 or slot.second or slot.microsecond for slot in slots) or any(
        (later - earlier).total_seconds() != 120 for earlier, later in pairwise(slots)
    ):
        raise RealSessionError("WSPR slots must be three consecutive even UTC boundaries")
    deadlines = document["deadlines"]
    if deadlines["overall_s"] <= max(
        helper_verification_deadline(document),
        deadlines["transmitter_s"],
        deadlines["receiver_s"],
    ):
        raise RealSessionError("overall deadline must exceed every component deadline")
    digest = helper_configuration_plan_sha256(document)
    if any(
        executable["plan_sha256"] != digest
        for executable in (
            document["remote_helper"],
            document["receiver_helper"],
            document["capture_helper"],
            document["wsprd"],
            document["wsprrypi"],
        )
    ):
        raise RealSessionError("executable configuration digest differs from the plan")


def validate_real_session_document(document: dict[str, Any]) -> None:
    validate_document(document, "real-qualification-session.schema.json")
    validate_real_session_plan(document["resolved_plan"])
    if document["resolved_plan_sha256"] != resolved_real_plan_sha256(document["resolved_plan"]):
        raise RealSessionError("real session plan digest is contradictory")
    if document["run_id"] != document["resolved_plan"]["run_id"]:
        raise RealSessionError("session run ID differs from the resolved plan")
    plan = document["resolved_plan"]
    digest = document["resolved_plan_sha256"]
    evidence = document["evidence"]
    helper_evidence = evidence.get("helper")
    helper_has_aggregate_contract = (
        isinstance(helper_evidence, dict)
        and isinstance(helper_evidence.get("details"), dict)
        and "verification_contract" in helper_evidence["details"]
    )
    simple_stages = {
        "capabilities": ("capabilities", "passed", plan["deadlines"]["helper_s"]),
        "helper": (
            "helper",
            "passed",
            helper_verification_deadline(plan)
            if helper_has_aggregate_contract
            else plan["deadlines"]["helper_s"],
        ),
        "ownership": ("ownership", "passed", plan["deadlines"]["helper_s"]),
        "initial_rf_idle": ("rf_idle", "passed", plan["deadlines"]["helper_s"]),
        "cleanup_registration": (
            "cleanup_registration",
            "passed",
            plan["deadlines"]["cleanup_s"],
        ),
    }
    for key, (evidence_type, outcome, deadline) in simple_stages.items():
        if key in evidence:
            _validate_stage(evidence[key], evidence_type, digest, outcome, deadline)
    if "capabilities" in evidence and evidence["capabilities"]["details"] != {
        "bindings": plan["capability_bindings"]
    }:
        raise RealSessionError("retained capability evidence differs from the plan")
    expected_helpers: dict[str, object] = {
        side: {
            key: plan[field][key]
            for key in ("host", "path", "sha256", "identity", "config_path", "config_sha256")
        }
        for side, field in (("transmitter", "remote_helper"), ("receiver", "receiver_helper"))
    }
    if helper_has_aggregate_contract:
        expected_helpers["verification_contract"] = helper_verification_contract(plan)
    if "helper" in evidence and evidence["helper"]["details"] != expected_helpers:
        raise RealSessionError("retained helper evidence differs from the plan")
    expected_ownership = {
        "transmitter": {
            "host": plan["host"],
            "services": plan["services"]["transmitter"],
            "conflicts": [],
        },
        "receiver": {
            "host": plan["receiver"]["host"],
            "services": plan["services"]["receiver"],
            "conflicts": [],
        },
    }
    if "ownership" in evidence and evidence["ownership"]["details"] != expected_ownership:
        raise RealSessionError("retained ownership evidence differs from the plan")
    if "initial_rf_idle" in evidence:
        _validate_quiescence_details(evidence["initial_rf_idle"], plan)
    if "rf_off" in evidence:
        _validate_capture(
            evidence["rf_off"], plan, "rf_off", plan["carrier"]["rf_off_sample_count"]
        )
    if "rf_on" in evidence:
        _validate_capture(evidence["rf_on"], plan, "rf_on", plan["carrier"]["rf_on_sample_count"])
    if (
        "carrier" in evidence
        and _validate_carrier(evidence["carrier"], plan, digest) != document["carrier_gate"]
    ):
        raise RealSessionError("retained carrier evidence differs from the session gate")
    if "coherent_capture" in evidence:
        _validate_capture(
            evidence["coherent_capture"], plan, "coherent", plan["coherent_capture"]["sample_count"]
        )
    if (
        "decode" in evidence
        and _validate_decode(evidence["decode"], plan, digest) != document["decode_gate"]
    ):
        raise RealSessionError("retained decode evidence differs from the session gate")
    if document["cleanup"] is not None:
        _validate_stage(
            document["cleanup"],
            "cleanup",
            digest,
            document["cleanup"]["outcome"],
            plan["deadlines"]["cleanup_s"],
        )
    if document["quiescence"] is not None:
        _validate_stage(
            document["quiescence"],
            "quiescence",
            digest,
            document["quiescence"]["outcome"],
            plan["deadlines"]["cleanup_s"],
        )
        if document["quiescence"]["outcome"] == "verified":
            _validate_quiescence_details(document["quiescence"], plan)
    events = document["events"]
    if [item["sequence"] for item in events] != list(range(1, len(events) + 1)):
        raise RealSessionError("real session events are not contiguous")
    phases = [item["phase"] for item in events]
    lifecycle = [
        "requested",
        "validated",
        "runtime_confirmed",
        "capabilities_discovered",
        "helper_verified",
        "services_and_ownership_verified",
        "rf_idle_verified",
        "cleanup_installed",
        "rf_off_captured",
        "carrier_transmitted_and_captured",
        "carrier_gate",
        "wspr_frames",
        "wav_and_decode",
        "cleanup",
        "quiescence",
    ]
    if len(phases) != len(set(phases)) or phases != sorted(
        phases, key=lambda phase: lifecycle.index(phase)
    ):
        raise RealSessionError("real session phases are duplicated or out of order")
    phase_evidence = {
        "capabilities_discovered": "capabilities",
        "helper_verified": "helper",
        "services_and_ownership_verified": "ownership",
        "rf_idle_verified": "initial_rf_idle",
        "cleanup_installed": "cleanup_registration",
        "rf_off_captured": "rf_off",
        "carrier_transmitted_and_captured": "rf_on",
        "carrier_gate": "carrier",
        "wspr_frames": "coherent_capture",
        "wav_and_decode": "decode",
    }
    for phase, key in phase_evidence.items():
        if (phase in phases) != (key in evidence):
            raise RealSessionError(f"{phase} event and {key} evidence presence disagree")
        if phase in phases:
            event_outcome = next(item["outcome"] for item in events if item["phase"] == phase)
            retained_outcome = (
                evidence[key]["details"]["gate_outcome"]
                if key in {"carrier", "decode"}
                else evidence[key]["outcome"]
            )
            if event_outcome != retained_outcome:
                raise RealSessionError(f"{phase} event outcome differs from retained evidence")
    if ("cleanup" in phases) != (document["cleanup"] is not None) or ("quiescence" in phases) != (
        document["quiescence"] is not None
    ):
        raise RealSessionError("cleanup/quiescence events and retained evidence disagree")
    for phase, retained in (
        ("cleanup", document["cleanup"]),
        ("quiescence", document["quiescence"]),
    ):
        if (
            retained is not None
            and next(item["outcome"] for item in events if item["phase"] == phase)
            != retained["outcome"]
        ):
            raise RealSessionError(f"{phase} event outcome differs from retained evidence")
    if "runtime_confirmed" in phases:
        for kind, field in (
            ("external_access", "external_authorization"),
            ("rf", "rf_authorization"),
        ):
            authorization = document[field]
            if authorization is None or (
                authorization["kind"] != kind
                or authorization["resolved_plan_sha256"] != document["resolved_plan_sha256"]
                or authorization["authorized"] is not True
            ):
                raise RealSessionError(f"{kind} authorization evidence is contradictory")
    if "carrier_transmitted_and_captured" in phases and (
        "cleanup_installed" not in phases
        or phases.index("cleanup_installed") > phases.index("carrier_transmitted_and_captured")
    ):
        raise RealSessionError("RF was enabled before cleanup registration")
    if "cleanup_installed" in phases and not {"cleanup", "quiescence"}.issubset(phases):
        raise RealSessionError("cleanup registration lacks final cleanup or quiescence evidence")
    if "cleanup" in phases and phases.index("cleanup") > phases.index("quiescence"):
        raise RealSessionError("quiescence was recorded before cleanup")
    if "wspr_frames" in phases and document["carrier_gate"] != "passed":
        raise RealSessionError("frames advanced without a passing carrier gate")
    if document["decode_gate"] != "not_run" and document["carrier_gate"] != "passed":
        raise RealSessionError("decode gate ran without passing carrier evidence")
    if document["resolved_plan"]["execution_mode"] == "hardware_free_validation" and (
        document["final_status"] == "qualified"
    ):
        raise RealSessionError("hardware-free evidence cannot qualify")
    expected_gates = {
        "qualified": ("passed", "passed"),
        "unqualified_carrier": ("failed", "not_run"),
        "unqualified_decode": ("passed", "failed"),
    }
    if (
        document["final_status"] in expected_gates
        and (document["carrier_gate"], document["decode_gate"])
        != expected_gates[document["final_status"]]
    ):
        raise RealSessionError("final status contradicts carrier/decode gates")
    cleanup_failed = (
        document["cleanup"] is not None and document["cleanup"].get("outcome") != "verified"
    ) or (
        document["quiescence"] is not None and document["quiescence"].get("outcome") != "verified"
    )
    if cleanup_failed != (document["final_status"] == "cleanup_failed"):
        raise RealSessionError("cleanup failure precedence is contradictory")
    status = document["final_status"]
    causes = document["failure_causes"]
    carrier_gate = document["carrier_gate"]
    decode_gate = document["decode_gate"]
    has_fixture_cause = any("RealFixtureBlocked" in cause for cause in causes)
    if not cleanup_failed:
        if has_fixture_cause and status != "fixture_blocked":
            raise RealSessionError("typed fixture blockage requires fixture-blocked status")
        if carrier_gate == "failed" and status != "unqualified_carrier":
            raise RealSessionError("failed carrier gate requires unqualified-carrier status")
        if carrier_gate == "passed" and decode_gate == "failed" and status != "unqualified_decode":
            raise RealSessionError("failed decode gate requires unqualified-decode status")
        if "blocked" in {carrier_gate, decode_gate} and status != "fixture_blocked":
            raise RealSessionError("blocked gate requires fixture-blocked status")
        if (carrier_gate, decode_gate) == ("passed", "passed") and status != "inconclusive":
            raise RealSessionError("passed hardware-free gates require inconclusive status")
    if status == "fixture_blocked" and not (
        "blocked" in {document["carrier_gate"], document["decode_gate"]} or has_fixture_cause
    ):
        raise RealSessionError("fixture-blocked status lacks blocked fixture evidence")
    if status == "preflight_failed" and ("cleanup_installed" in phases or not causes):
        raise RealSessionError("preflight-failed status contradicts phases or causes")
    if status == "aborted" and (
        "cleanup" not in phases
        or not causes
        or carrier_gate in {"failed", "blocked"}
        or decode_gate != "not_run"
    ):
        raise RealSessionError("aborted status lacks post-registration failure evidence")
    hardware_free_complete = (
        document["carrier_gate"] == "passed"
        and document["decode_gate"] == "passed"
        and "incomplete_evidence" in causes
    )
    carrier_only_complete = (
        plan.get("session_kind") == "cw_live_tone"
        and document["carrier_gate"] == "passed"
        and document["decode_gate"] == "not_run"
        and "phase7_live_tone_only" in causes
    )
    if status == "inconclusive" and not (hardware_free_complete or carrier_only_complete):
        raise RealSessionError("inconclusive status lacks its required bounded evidence")


def _require_authorization(
    authorization: RealRuntimeAuthorization | None, kind: str, digest: str
) -> None:
    if (
        authorization is None
        or authorization.kind != kind
        or not authorization.authorized
        or authorization.resolved_plan_sha256 != digest
    ):
        raise RealSessionError(f"ephemeral {kind} authorization is required")


def _validate_stage(
    document: dict[str, object],
    evidence_type: str,
    digest: str,
    expected_outcome: str,
    expected_deadline: float,
) -> None:
    validate_document(document, "real-session-stage-evidence.schema.json")
    if cast(float, document["deadline_s"]) != expected_deadline:
        raise RealSessionError(f"{evidence_type} deadline differs from the resolved plan")
    deadline_overrun = cast(float, document["elapsed_s"]) > cast(float, document["deadline_s"])
    retainable_cleanup_overrun = (
        evidence_type in {"cleanup", "quiescence"} and document["outcome"] == "failed"
    )
    if deadline_overrun and not retainable_cleanup_overrun:
        raise RealSessionError(f"{evidence_type} exceeded its recorded hard deadline")
    if document["outcome"] == "blocked":
        raise RealFixtureBlocked(f"{evidence_type} reports an unavailable fixture")
    if (
        document["evidence_type"] != evidence_type
        or document["plan_sha256"] != digest
        or document["outcome"] != expected_outcome
    ):
        raise RealSessionError(f"{evidence_type} evidence contradicts the resolved plan")


def _failed_stage(
    evidence_type: str,
    digest: str,
    exc: Exception,
    deadline_s: float,
    plan: dict[str, Any],
) -> dict[str, object]:
    if evidence_type == "cleanup":
        details: dict[str, object] = {
            "actions_complete": False,
            "helper_absent": False,
            "failure": f"{type(exc).__name__}: {exc}",
        }
    elif evidence_type == "quiescence":
        details = {
            "backend": plan["backend"],
            "output": plan["output"],
            "verified": False,
            "failure": f"{type(exc).__name__}: {exc}",
        }
    else:
        details = {"failure": f"{type(exc).__name__}: {exc}"}
    return {
        "schema_version": 1,
        "evidence_type": evidence_type,
        "plan_sha256": digest,
        "outcome": "failed",
        "elapsed_s": 0,
        "deadline_s": deadline_s,
        "details": details,
    }


def _validate_quiescence_details(document: dict[str, object], plan: dict[str, Any]) -> None:
    if document["details"] != {
        "backend": plan["backend"],
        "output": plan["output"],
        "verified": True,
    }:
        raise RealSessionError("quiescence evidence differs from the resolved backend/output")


def _validate_capture(
    document: dict[str, object], plan: dict[str, Any], capture_kind: str, sample_count: int
) -> None:
    _validate_stage(
        document,
        "capture",
        resolved_real_plan_sha256(plan),
        "completed",
        plan["deadlines"]["receiver_s"],
    )
    details = cast(dict[str, object], document["details"])
    receiver = plan["receiver"]
    if (
        details["capture_kind"] != capture_kind
        or details["sample_count"] != sample_count
        or details["receiver_host"] != receiver["host"]
        or details["driver"] != receiver["driver"]
        or details["serial"] != receiver["serial"]
    ):
        raise RealSessionError(f"{capture_kind} capture evidence contradicts the plan")


def _validate_carrier(document: dict[str, object], plan: dict[str, Any], digest: str) -> str:
    _validate_stage(
        document, "carrier_analysis", digest, "completed", plan["deadlines"]["overall_s"]
    )
    details = cast(dict[str, object], document["details"])
    offset = cast(float, details["offset_hz"])
    requested = cast(float, details["requested_frequency_hz"])
    strongest = cast(float, details["strongest_frequency_hz"])
    policy = details["carrier_gate_policy"]
    contrast = cast(float, details["strongest_contrast_db"])
    relative_offset_gate = cast(float, details["relative_acquisition_offset_gate_hz"])
    relative_contrast_gate = cast(float, details["relative_acquisition_contrast_gate_db"])
    if requested != plan["frequency_hz"] or abs((strongest - requested) - offset) > 1e-6:
        raise RealSessionError("carrier evidence frequency contradicts the plan")
    claimed = cast(str, details["gate_outcome"])
    if policy != "bounded_relative_carrier_acquisition":
        raise RealSessionError("carrier evidence uses an unsupported gate policy")
    carrier_derived = (
        "passed"
        if abs(offset) <= relative_offset_gate and contrast >= relative_contrast_gate
        else "failed"
    )
    mode_gate = details.get("mode_gate", "not_applicable")
    if plan.get("session_kind") == "cw_live_tone":
        if mode_gate != "not_applicable":
            raise RealSessionError("tone carrier evidence contradicts its maintained mode gate")
        derived = carrier_derived
    else:
        if mode_gate != "not_applicable":
            raise RealSessionError("WSPR carrier evidence cannot claim a CW mode gate")
        derived = carrier_derived
    if claimed in {"passed", "failed"} and claimed != derived:
        raise RealSessionError("carrier gate contradicts the maintained relative acquisition")
    return claimed


def _validate_decode(document: dict[str, object], plan: dict[str, Any], digest: str) -> str:
    _validate_stage(document, "decode_summary", digest, "completed", plan["deadlines"]["overall_s"])
    details = cast(dict[str, object], document["details"])
    slots = cast(list[dict[str, object]], details["slots"])
    identity = plan["identity"]
    if [slot["slot_utc"] for slot in slots] != plan["slots_utc"] or any(
        slot["callsign"] != identity["callsign"]
        or slot["grid"] != identity["grid"]
        or slot["power_dbm"] != identity["power_dbm"]
        for slot in slots
    ):
        raise RealSessionError("decoder evidence identity or UTC slots contradict the plan")
    gate = cast(str, details["gate_outcome"])
    if (gate == "passed") != all(slot["matched"] is True for slot in slots):
        raise RealSessionError("decoder gate contradicts the three slot results")
    return gate


def resolved_real_plan_sha256(document: dict[str, Any]) -> str:
    """Digest the complete operator-confirmed plan, including helper config bytes."""
    normalized = json.loads(json.dumps(document, default=str))
    for field in ("remote_helper", "receiver_helper"):
        normalized[field]["plan_sha256"] = ""
    normalized["capture_helper"]["plan_sha256"] = ""
    normalized["wsprd"]["plan_sha256"] = ""
    normalized["wsprrypi"]["plan_sha256"] = ""
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def helper_configuration_plan_sha256(document: dict[str, Any]) -> str:
    """Digest the helper subplan without its recursively dependent config hashes."""
    normalized = json.loads(json.dumps(document, default=str))
    for field in ("remote_helper", "receiver_helper"):
        normalized[field]["plan_sha256"] = ""
        normalized[field]["config_sha256"] = ""
    for field in ("capture_helper", "wsprd", "wsprrypi"):
        normalized[field]["plan_sha256"] = ""
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _result_causes(
    final_status: str, recorded: list[str], preflight_passed: bool = True
) -> list[str]:
    if final_status == "cleanup_failed":
        return (["preflight"] if not preflight_passed else []) + ["cleanup"]
    if final_status == "preflight_failed":
        return ["preflight"]
    if final_status == "aborted":
        return ["external_abort"]
    if final_status == "unqualified_carrier":
        return ["transmitter_carrier"]
    if final_status == "unqualified_decode":
        return ["transmitter_decode"]
    if final_status == "fixture_blocked":
        return ["dependency_unavailable"]
    if final_status == "inconclusive" and "incomplete_evidence" in recorded:
        return ["incomplete_evidence"]
    return []


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RealSessionError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
