"""Bounded, hardware-free, real-time qualification lifecycle simulator."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import numpy as np

from wsprrypi_qualification.audio import AudioParameters, create_slot_wav, slot_wav_name
from wsprrypi_qualification.carrier import CarrierParameters, analyze_carrier
from wsprrypi_qualification.cf32 import inspect_cf32
from wsprrypi_qualification.decoder import run_wsprd
from wsprrypi_qualification.manifests import build_manifest, render_manifest, write_manifest
from wsprrypi_qualification.models import WsprIdentity
from wsprrypi_qualification.offline import load_json_document, validate_document, write_json_new


class SimulationError(RuntimeError):
    """The simulator cannot produce a truthful bounded result."""


class ChildFailure(SimulationError):
    def __init__(self, document: dict[str, Any]) -> None:
        super().__init__(f"required simulator child failed: {document['name']}")
        self.document = document


MINIMUM_OVERALL_TIMEOUT_S = 2.0
CHILD_NAMES = ("receiver-rf-off", "transmitter-carrier", "transmitter-frames")
WORKER_CLEANUP_MARGIN_S = 0.5
RF_FIXTURE_SAMPLE_COUNT = 3_072
COHERENT_FIXTURE_SAMPLE_COUNT = 243_000
PHYSICAL_SAMPLE_RATE_HZ = 1_000
WAV_FRAME_COUNT = 1_000


@dataclass(frozen=True)
class SimulatorPlan:
    run_id: str
    output_parent: Path
    time_scale: float = 0.001
    child_timeout_s: float = 1.0
    overall_timeout_s: float = 15.0
    injection: str = "none"

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "plan_type": "qualification_simulator",
            "run_id": self.run_id,
            "output_parent": str(self.output_parent.resolve()),
            "time_scale": self.time_scale,
            "child_timeout_s": self.child_timeout_s,
            "overall_timeout_s": self.overall_timeout_s,
            "injection": self.injection,
            "simulated": True,
            "external_access": False,
            "rf_enabled": False,
        }


def validate_simulator_session(document: dict[str, Any]) -> None:
    validate_document(document, "simulator-session.schema.json")
    phases = [item["phase"] for item in document["events"]]
    success = [
        "preflight",
        "cleanup_registered",
        "rf_off",
        "carrier",
        "carrier_gate",
        "frames",
        "decode",
        "cleanup",
        "quiescence",
    ]
    carrier_failure = [
        "preflight",
        "cleanup_registered",
        "rf_off",
        "carrier",
        "carrier_gate",
        "cleanup",
        "quiescence",
    ]
    failure_sequences = {
        ("preflight", "cleanup_registered", "rf_off", "cleanup", "quiescence"): ["receiver-rf-off"],
        (
            "preflight",
            "cleanup_registered",
            "rf_off",
            "carrier",
            "cleanup",
            "quiescence",
        ): ["receiver-rf-off", "transmitter-carrier"],
        (
            "preflight",
            "cleanup_registered",
            "rf_off",
            "carrier",
            "carrier_gate",
            "frames",
            "cleanup",
            "quiescence",
        ): list(CHILD_NAMES),
    }
    if phases not in (success, carrier_failure) and tuple(phases) not in failure_sequences:
        raise SimulationError("simulator lifecycle phases are missing or reordered")
    if "frames" in phases and document["carrier_gate"] != "passed":
        raise SimulationError("simulator frames advanced without a passing carrier gate")
    if document["final_status"] == "qualified" or document["qualification_claim"]:
        raise SimulationError("simulator evidence cannot qualify hardware")
    if document["cleanup_outcome"] != "verified" and document["final_status"] != "cleanup_failed":
        raise SimulationError("simulator cleanup failure must override classification")
    if (
        document["carrier_gate"] == "passed"
        and document["decode_gate"] == "passed"
        and document["cleanup_outcome"] == "verified"
        and document["final_status"] != "inconclusive"
    ):
        raise SimulationError("successful simulation must remain inconclusive")
    if any(item["sequence"] != index for index, item in enumerate(document["events"], 1)):
        raise SimulationError("simulator event sequence is contradictory")
    expected_names = (
        list(CHILD_NAMES)
        if phases == success
        else list(CHILD_NAMES[:2])
        if phases == carrier_failure
        else failure_sequences[tuple(phases)]
    )
    if [child["name"] for child in document["children"]] != expected_names:
        raise SimulationError("simulator child set is missing, duplicated, or reordered")
    for child in document["children"]:
        _validate_child(child, document["timing"]["child_deadline_s"])
    failures = [
        child
        for child in document["children"]
        if child["timed_out"] or child["return_code"] != 0 or not child["cleanup_verified"]
    ]
    if tuple(phases) in failure_sequences:
        if failures != [document["children"][-1]]:
            raise SimulationError("simulator failure lifecycle does not identify one final child")
    elif failures:
        raise SimulationError("successful simulator lifecycle contains a failed child")
    outcomes = {item["phase"]: item["outcome"] for item in document["events"]}
    if (
        outcomes["cleanup"] != document["cleanup_outcome"]
        or outcomes["quiescence"] != document["cleanup_outcome"]
    ):
        raise SimulationError("simulator cleanup events contradict cleanup evidence")
    if phases == success and (document["carrier_gate"], document["decode_gate"]) != (
        "passed",
        "passed",
    ):
        raise SimulationError("successful simulator lifecycle contradicts its gates")
    if phases == carrier_failure and document["decode_gate"] != "not_run":
        raise SimulationError("carrier failure lifecycle cannot contain a decode gate")
    if tuple(phases) in failure_sequences:
        failed = document["children"][-1]
        expected_status = "cleanup_failed" if not failed["cleanup_verified"] else "aborted"
        if document["final_status"] != expected_status or document["decode_gate"] != "not_run":
            raise SimulationError("simulator child failure classification is contradictory")


def run_simulation(plan: SimulatorPlan) -> dict[str, Any]:
    """Run the simulation under a hard outer worker-process deadline."""

    plan = replace(plan, output_parent=plan.output_parent.resolve())
    requested = plan.document()
    validate_document(requested, "simulator-plan.schema.json")
    if plan.overall_timeout_s < MINIMUM_OVERALL_TIMEOUT_S:
        raise SimulationError("overall deadline is below the proven simulator minimum")
    if plan.output_parent.exists() and not plan.output_parent.is_dir():
        raise SimulationError("simulator output parent is not a directory")
    final = plan.output_parent / plan.run_id
    temporary = plan.output_parent / f".incomplete-{plan.run_id}"
    if final.exists() or temporary.exists():
        raise SimulationError("refusing to reuse simulator output directory")
    with tempfile.TemporaryDirectory(prefix="wspq-simulator-") as request_directory:
        request_path = Path(request_directory) / "request.json"
        request_path.write_text(json.dumps(requested, sort_keys=True), encoding="utf-8")
        arguments = [
            str(Path(sys.executable).absolute()),
            "-m",
            "wsprrypi_qualification.simulator",
            "--worker",
            str(request_path),
        ]
        try:
            completed = subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=plan.overall_timeout_s + WORKER_CLEANUP_MARGIN_S,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise SimulationError(
                "simulator worker exceeded the hard outer deadline; no run was promoted"
            ) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "worker failed"
        raise SimulationError(f"simulator worker failed: {detail}")
    try:
        result = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise SimulationError("simulator worker returned invalid completion evidence") from error
    if (
        not isinstance(result, dict)
        or Path(str(result.get("run_directory", ""))).resolve() != final.resolve()
    ):
        raise SimulationError("simulator worker completion contradicts the requested run")
    validate_simulator_bundle(final)
    return cast(dict[str, Any], result)


def _run_simulation_inner(plan: SimulatorPlan) -> dict[str, Any]:
    requested = plan.document()
    validate_document(requested, "simulator-plan.schema.json")
    plan.output_parent.mkdir(parents=True, exist_ok=True)
    final = plan.output_parent / plan.run_id
    temporary = plan.output_parent / f".incomplete-{plan.run_id}"
    if final.exists() or temporary.exists():
        raise SimulationError("refusing to reuse simulator output directory")
    temporary.mkdir()
    started = time.monotonic()
    events: list[dict[str, Any]] = []
    children: list[dict[str, Any]] = []

    def event(phase: str, outcome: str) -> None:
        events.append({"sequence": len(events) + 1, "phase": phase, "outcome": outcome})

    try:
        write_json_new(
            temporary / "requested-plan.json", requested, schema_name="simulator-plan.schema.json"
        )
        resolved = {
            **requested,
            "logical_duration_s": 370,
            "logical_sample_rate_hz": 250_000,
            "logical_sample_count": 92_500_000,
            "physical_fixture_sample_rate_hz": PHYSICAL_SAMPLE_RATE_HZ,
            "rf_off_fixture_sample_count": RF_FIXTURE_SAMPLE_COUNT,
            "rf_on_fixture_sample_count": RF_FIXTURE_SAMPLE_COUNT,
            "coherent_fixture_sample_count": COHERENT_FIXTURE_SAMPLE_COUNT,
            "cf32_bytes_per_sample": 8,
            "wav_sample_rate_hz": PHYSICAL_SAMPLE_RATE_HZ,
            "wav_channels": 1,
            "wav_sample_width_bytes": 2,
            "wav_frame_count": WAV_FRAME_COUNT,
            "slot_count": 3,
            "slot_spacing_s": 120,
            "qualification_claim": False,
        }
        write_json_new(
            temporary / "resolved-plan.json",
            resolved,
            schema_name="resolved-simulator-plan.schema.json",
        )
        write_json_new(
            temporary / "capabilities.json",
            {
                "simulated": True,
                "network": False,
                "physical_sdr": False,
                "service_manager": False,
                "gpio_i2c": False,
                "local_subprocesses": True,
            },
            schema_name="simulator-capabilities.schema.json",
        )
        write_json_new(
            temporary / "runtime-confirmation.json",
            {
                "simulated": True,
                "operator_confirmation_required": False,
                "rf_authorized": False,
                "plan_sha256": _sha256_json(resolved),
            },
            schema_name="simulator-runtime-confirmation.schema.json",
        )
        event("preflight", "passed")
        event("cleanup_registered", "verified")
        children.append(
            _bounded_child(
                "receiver-rf-off",
                _remaining_timeout(started, plan),
                plan.injection == "rf_off_timeout",
                plan.injection == "rf_off_nonzero",
            )
        )
        _require_child(children[-1])
        event("rf_off", "completed")
        _ensure_budget(started, plan, "RF fixture generation")

        rate, fft, center, offset = 4096, 1024, 10_000.0, 500.0
        samples = np.arange(fft * 3)
        off = temporary / "rf-off.cf32"
        on = temporary / "rf-on.cf32"
        np.zeros(fft * 3, dtype="<c8").tofile(off)
        tone_offset = 700.0 if plan.injection == "carrier_fail" else offset
        np.asarray(0.3 * np.exp(2j * np.pi * tone_offset * samples / rate), dtype="<c8").tofile(on)
        children.append(
            _bounded_child(
                "transmitter-carrier",
                _remaining_timeout(started, plan),
                plan.injection == "carrier_timeout",
                plan.injection == "carrier_nonzero",
            )
        )
        _require_child(children[-1])
        event("carrier", "completed")
        _ensure_budget(started, plan, "carrier analysis")
        _inject_stage_hang(plan, "carrier_analysis_hang")
        carrier = analyze_carrier(
            off,
            on,
            CarrierParameters(rate, center, center + offset, fft_size=fft, dc_exclusion_hz=100),
            temporary / "carrier-analysis.json",
        )
        carrier_gate = carrier["gate_outcome"]
        _ensure_budget(started, plan, "carrier gate")
        event("carrier_gate", carrier_gate)
        decode_gate = "not_run"
        decoder_documents: list[dict[str, Any]] = []
        if carrier_gate == "passed":
            children.append(
                _bounded_child(
                    "transmitter-frames",
                    _remaining_timeout(started, plan),
                    plan.injection == "frame_timeout",
                    plan.injection == "frame_nonzero",
                )
            )
            _require_child(children[-1])
            capture_start = datetime(2026, 8, 11, 23, 59, 59, tzinfo=UTC)
            slots = [datetime(2026, 8, 12, 0, minute, tzinfo=UTC) for minute in (0, 2, 4)]
            coherent = temporary / "coherent-compact.cf32"
            _ensure_budget(started, plan, "coherent fixture generation")
            n = np.arange(COHERENT_FIXTURE_SAMPLE_COUNT)
            np.asarray(0.2 * np.exp(2j * np.pi * 100 * n / 1_000), dtype="<c8").tofile(coherent)
            helper = _fake_wsprd(temporary / "tools")
            for slot in slots:
                _ensure_budget(started, plan, "WAV conversion")
                _inject_stage_hang(plan, "wav_hang")
                wav = temporary / slot_wav_name(slot)
                audio_evidence = temporary / f"audio-{slot.strftime('%H%M')}.json"
                create_slot_wav(
                    coherent,
                    capture_start,
                    slot,
                    wav,
                    audio_evidence,
                    AudioParameters(
                        1_000,
                        10_000,
                        10_100,
                        output_rate_hz=1_000,
                        target_audio_hz=100,
                        frame_duration_s=1,
                        filter_taps=16,
                        required_margin_s=1,
                    ),
                )
                decoder_path = temporary / f"decoder-{slot.strftime('%H%M')}.json"
                run_wsprd(
                    wav,
                    decoder_path,
                    WsprIdentity("AA0NT", "EM18", 20),
                    executable=Path(sys.executable),
                    extra_arguments=(str(helper),),
                    timeout_s=min(plan.child_timeout_s, _remaining_timeout(started, plan)),
                    slot_utc=slot,
                    target_audio_hz=1500,
                )
                _inject_stage_hang(plan, "decoder_hang")
                decoder_documents.append(_rebase_decoder_document(decoder_path, temporary, final))
                _ensure_budget(started, plan, "decoder invocation")
            decode_gate = (
                "passed"
                if len(decoder_documents) == 3
                and all(item["gate_outcome"] == "passed" for item in decoder_documents)
                else "failed"
            )
            slot_records = []
            for slot, decoder in zip(slots, decoder_documents, strict=True):
                label = slot.strftime("%H%M")
                wav_path = temporary / slot_wav_name(slot)
                decoder_path = temporary / f"decoder-{label}.json"
                slot_records.append(
                    {
                        "slot_utc": slot.isoformat().replace("+00:00", "Z"),
                        "wav": _relative_artifact(temporary, wav_path),
                        "decoder": _relative_artifact(temporary, decoder_path),
                        "gate_outcome": decoder["gate_outcome"],
                        "identity": {"callsign": "AA0NT", "grid": "EM18", "power_dbm": 20},
                        "intended_signal_found": decoder["expected_intended_signal_found"],
                    }
                )
            summary = {
                "schema_version": 1,
                "evidence_type": "simulator_decode_summary",
                "simulated": True,
                "gate_outcome": decode_gate,
                "decoder_invocations": 3,
                "slots": slot_records,
            }
            validate_simulator_decode_summary(summary, temporary)
            _ensure_budget(started, plan, "decode summary")
            write_json_new(
                temporary / "decode-summary.json",
                summary,
                schema_name="simulator-decode-summary.schema.json",
            )
            event("frames", "completed")
            event("decode", decode_gate)
        cleanup = "failed" if plan.injection == "cleanup_fail" else "verified"
        event("cleanup", cleanup)
        event("quiescence", cleanup)
        elapsed = time.monotonic() - started
        if elapsed > plan.overall_timeout_s:
            raise SimulationError("simulator overall deadline expired")
        status = (
            "cleanup_failed"
            if cleanup != "verified"
            else "unqualified_carrier"
            if carrier_gate == "failed"
            else "inconclusive"
        )
        session = {
            "schema_version": 1,
            "evidence_type": "qualification_simulator_session",
            "run_id": plan.run_id,
            "simulated": True,
            "qualification_claim": False,
            "plan_sha256": _sha256_json(resolved),
            "timing": {
                "logical_duration_s": 370,
                "actual_elapsed_s": elapsed,
                "time_scale": plan.time_scale,
                "overall_deadline_s": plan.overall_timeout_s,
                "child_deadline_s": plan.child_timeout_s,
            },
            "events": events,
            "children": children,
            "carrier_gate": carrier_gate,
            "decode_gate": decode_gate,
            "cleanup_outcome": cleanup,
            "final_status": status,
            "failure_causes": ["simulation_only"] if status == "inconclusive" else [],
        }
        validate_simulator_session(session)
        _ensure_budget(started, plan, "session validation")
        write_json_new(
            temporary / "simulator-session.json",
            session,
            schema_name="simulator-session.schema.json",
        )
        write_json_new(
            temporary / "result.json",
            _derive_result(session),
            schema_name="simulator-result.schema.json",
        )
        write_json_new(
            temporary / "quiescence.json",
            {
                "simulated": True,
                "gpio_inspection": "read_only_fixture",
                "si5351_inspection": "read_only_fixture",
                "verified": cleanup == "verified",
            },
            schema_name="simulator-quiescence.schema.json",
        )
        index = _artifact_index(temporary)
        write_json_new(
            temporary / "artifact-index.json",
            index,
            schema_name="simulator-artifact-index.schema.json",
        )
        write_manifest(temporary)
        _ensure_budget(started, plan, "bundle publication")
        _inject_stage_hang(plan, "publication_hang")
        validate_simulator_bundle(temporary)
        temporary.replace(final)
        return {"run_directory": str(final), "session": session}
    except ChildFailure as error:
        return _publish_child_failure(
            plan, temporary, final, resolved, started, events, children, error.document
        )
    except Exception:
        # Preserve incomplete evidence for diagnosis; never promote it as a run.
        raise


def _bounded_child(name: str, timeout_s: float, hang: bool, nonzero: bool) -> dict[str, Any]:
    duration = timeout_s * 5 if hang else min(0.02, timeout_s / 4)
    exit_code = 7 if nonzero else 0
    code = f"import time; print({name!r}); time.sleep({duration!r}); raise SystemExit({exit_code})"
    arguments = [str(Path(sys.executable).resolve()), "-c", code]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            arguments, capture_output=True, text=True, encoding="utf-8", timeout=timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "role": name,
            "executable": str(Path(sys.executable).resolve()),
            "executable_sha256": _sha256_file(Path(sys.executable)),
            "arguments": arguments,
            "return_code": None,
            "timed_out": True,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "elapsed_s": time.monotonic() - started,
            "terminated": True,
            "reaped": True,
            "descendant_cleanup": "unsupported",
            "cleanup_verified": False,
        }
    return {
        "name": name,
        "role": name,
        "executable": str(Path(sys.executable).resolve()),
        "executable_sha256": _sha256_file(Path(sys.executable)),
        "arguments": arguments,
        "return_code": completed.returncode,
        "timed_out": False,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "elapsed_s": time.monotonic() - started,
        "terminated": False,
        "reaped": True,
        "descendant_cleanup": "verified_no_descendants",
        "cleanup_verified": True,
    }


def _validate_child(document: dict[str, Any], deadline_s: float) -> None:
    executable = str(Path(sys.executable).resolve())
    if (
        document["name"] not in CHILD_NAMES
        or document["role"] != document["name"]
        or document["executable"] != executable
        or document["executable_sha256"] != _sha256_file(Path(sys.executable))
        or document["arguments"][:2] != [executable, "-c"]
        or len(document["arguments"]) != 3
        or document["name"] not in document["arguments"][2]
        or document["elapsed_s"] < 0
        or deadline_s <= 0
    ):
        raise SimulationError("simulator child command contract is invalid")
    if document["timed_out"]:
        if document["return_code"] is not None or document["cleanup_verified"]:
            raise SimulationError("timed-out simulator child has contradictory cleanup evidence")
    elif document["return_code"] is None or not document["reaped"]:
        raise SimulationError("simulator child completion evidence is contradictory")


def _require_child(document: dict[str, Any]) -> None:
    if document["timed_out"] or document["return_code"] != 0 or not document["cleanup_verified"]:
        raise ChildFailure(document)
    _validate_child(document, max(document["elapsed_s"], 0.000001))


def _remaining_timeout(started: float, plan: SimulatorPlan) -> float:
    remaining = plan.overall_timeout_s - (time.monotonic() - started)
    if remaining <= 0:
        raise SimulationError("simulator overall deadline expired")
    return min(plan.child_timeout_s, remaining)


def _ensure_budget(started: float, plan: SimulatorPlan, stage: str) -> None:
    if time.monotonic() >= started + plan.overall_timeout_s:
        raise SimulationError(f"simulator overall deadline expired before {stage}")


def _inject_stage_hang(plan: SimulatorPlan, injection: str) -> None:
    if plan.injection == injection:
        time.sleep(plan.overall_timeout_s * 2)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _publish_child_failure(
    plan: SimulatorPlan,
    temporary: Path,
    final: Path,
    resolved: dict[str, Any],
    started: float,
    events: list[dict[str, Any]],
    children: list[dict[str, Any]],
    failed: dict[str, Any],
) -> dict[str, Any]:
    phase = {
        "receiver-rf-off": "rf_off",
        "transmitter-carrier": "carrier",
        "transmitter-frames": "frames",
    }[failed["name"]]
    events.append({"sequence": len(events) + 1, "phase": phase, "outcome": "failed"})
    cleanup = "verified" if failed["cleanup_verified"] else "failed"
    for cleanup_phase in ("cleanup", "quiescence"):
        events.append({"sequence": len(events) + 1, "phase": cleanup_phase, "outcome": cleanup})
    carrier_gate = "passed" if failed["name"] == "transmitter-frames" else "not_run"
    status = "aborted" if cleanup == "verified" else "cleanup_failed"
    failure_cause = "child_timeout" if failed["timed_out"] else "child_nonzero"
    session = {
        "schema_version": 1,
        "evidence_type": "qualification_simulator_session",
        "run_id": plan.run_id,
        "simulated": True,
        "qualification_claim": False,
        "plan_sha256": _sha256_json(resolved),
        "timing": {
            "logical_duration_s": 370,
            "actual_elapsed_s": time.monotonic() - started,
            "time_scale": plan.time_scale,
            "overall_deadline_s": plan.overall_timeout_s,
            "child_deadline_s": plan.child_timeout_s,
        },
        "events": events,
        "children": children,
        "carrier_gate": carrier_gate,
        "decode_gate": "not_run",
        "cleanup_outcome": cleanup,
        "final_status": status,
        "failure_causes": [failure_cause],
    }
    validate_simulator_session(session)
    write_json_new(
        temporary / "simulator-session.json",
        session,
        schema_name="simulator-session.schema.json",
    )
    write_json_new(
        temporary / "result.json",
        _derive_result(session),
        schema_name="simulator-result.schema.json",
    )
    write_json_new(
        temporary / "quiescence.json",
        {
            "simulated": True,
            "gpio_inspection": "read_only_fixture",
            "si5351_inspection": "read_only_fixture",
            "verified": cleanup == "verified",
        },
        schema_name="simulator-quiescence.schema.json",
    )
    index = _artifact_index(temporary)
    write_json_new(
        temporary / "artifact-index.json",
        index,
        schema_name="simulator-artifact-index.schema.json",
    )
    write_manifest(temporary)
    validate_simulator_bundle(temporary)
    temporary.replace(final)
    return {"run_directory": str(final), "session": session}


def _fake_wsprd(root: Path) -> Path:
    root.mkdir()
    path = root / "fake wsprd.py"
    path.write_text(
        """import pathlib, sys
if '--version' in sys.argv: print('fake-wsprd 1'); raise SystemExit
stem=pathlib.Path(sys.argv[-1]).stem
print(f'{stem[9:13]} -10 0.1 0.001500 0 AA0NT EM18 20')
""",
        encoding="utf-8",
    )
    return path


def _sha256_json(document: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _derive_result(session: dict[str, Any]) -> dict[str, Any]:
    status = session["final_status"]
    if status == "inconclusive":
        cause = "simulation_only"
    elif status == "unqualified_carrier":
        cause = "carrier_gate"
    elif status == "cleanup_failed" and session["failure_causes"]:
        cause = session["failure_causes"][0]
    elif status == "cleanup_failed":
        cause = "cleanup_fail"
    elif session["failure_causes"]:
        cause = session["failure_causes"][0]
    else:
        raise SimulationError("simulator result cause cannot be derived")
    return {
        "schema_version": 1,
        "run_id": session["run_id"],
        "plan_sha256": session["plan_sha256"],
        "status": status,
        "simulated": True,
        "qualification_claim": False,
        "carrier_gate": session["carrier_gate"],
        "decode_gate": session["decode_gate"],
        "cleanup_outcome": session["cleanup_outcome"],
        "cause": cause,
    }


def _artifact_index(root: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evidence_type": "simulator_artifact_index",
        "simulated": True,
        "artifacts": [asdict(item) for item in build_manifest(root)],
    }


def _relative_artifact(root: Path, path: Path) -> dict[str, Any]:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    return {"path": relative, "size_bytes": path.stat().st_size, "sha256": _sha256_file(path)}


def _rebase_decoder_document(path: Path, temporary: Path, final: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    temporary_text, final_text = str(temporary.resolve()), str(final.resolve())
    document["arguments"] = [
        item.replace(temporary_text, final_text) for item in document["arguments"]
    ]
    document["wav"]["path"] = document["wav"]["path"].replace(temporary_text, final_text)
    replacement = path.with_name(f".{path.name}.rebased")
    replacement.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    replacement.replace(path)
    return cast(dict[str, Any], document)


def validate_simulator_decode_summary(document: dict[str, Any], root: Path) -> None:
    validate_document(document, "simulator-decode-summary.schema.json")
    slots = [
        datetime.fromisoformat(item["slot_utc"].replace("Z", "+00:00"))
        for item in document["slots"]
    ]
    if len(slots) != 3 or any(
        slot.tzinfo is None or slot.minute % 2 or slot.second for slot in slots
    ):
        raise SimulationError("simulator decode summary requires three even UTC slots")
    if any(right - left != timedelta(minutes=2) for left, right in pairwise(slots)):
        raise SimulationError("simulator decode slots are not consecutive")
    identities = {
        (item["identity"]["callsign"], item["identity"]["grid"], item["identity"]["power_dbm"])
        for item in document["slots"]
    }
    if identities != {("AA0NT", "EM18", 20)}:
        raise SimulationError("simulator decode identity is contradictory")
    gates: list[str] = []
    paths: set[str] = set()
    for item in document["slots"]:
        for key in ("wav", "decoder"):
            record = item[key]
            if (
                record["path"] in paths
                or Path(record["path"]).is_absolute()
                or ".." in Path(record["path"]).parts
            ):
                raise SimulationError("simulator decode artifact path is duplicate or unsafe")
            paths.add(record["path"])
            path = root / record["path"]
            if (
                not path.is_file()
                or path.stat().st_size != record["size_bytes"]
                or _sha256_file(path) != record["sha256"]
            ):
                raise SimulationError("simulator decode artifact identity mismatch")
        decoder = json.loads((root / item["decoder"]["path"]).read_text(encoding="utf-8"))
        if (
            decoder["slot_utc"] != item["slot_utc"]
            or decoder["gate_outcome"] != item["gate_outcome"]
            or decoder["expected_intended_signal_found"] != item["intended_signal_found"]
            or decoder["expected_identity"] != item["identity"]
            or decoder["return_code"] != 0
            or decoder["timed_out"]
            or len(decoder["arguments"]) != 3
            or decoder["arguments"][0] != decoder["tool"]["path"]
            or Path(decoder["arguments"][1]).name != "fake wsprd.py"
            or Path(decoder["arguments"][2]).name != Path(item["wav"]["path"]).name
            or decoder["wav_filename"] != Path(item["wav"]["path"]).name
            or decoder["wav"]["size_bytes"] != item["wav"]["size_bytes"]
            or decoder["wav"]["sha256"] != item["wav"]["sha256"]
            or not decoder["stdout"]
            or decoder["stderr"]
        ):
            raise SimulationError("simulator decoder evidence contradicts its summary")
        gates.append(item["gate_outcome"])
    expected = "passed" if all(gate == "passed" for gate in gates) else "failed"
    if document["decoder_invocations"] != 3 or document["gate_outcome"] != expected:
        raise SimulationError("simulator decode gate is not derived from three invocations")


def validate_simulator_bundle(root: Path) -> None:
    root = root.resolve()
    requested = load_json_document(root / "requested-plan.json", "simulator-plan.schema.json")
    resolved = load_json_document(
        root / "resolved-plan.json", "resolved-simulator-plan.schema.json"
    )
    session = load_json_document(root / "simulator-session.json", "simulator-session.schema.json")
    validate_simulator_session(session)
    if session["run_id"] != resolved["run_id"] or session["plan_sha256"] != _sha256_json(resolved):
        raise SimulationError("simulator bundle plan binding is contradictory")
    shared_fields = set(requested)
    if any(requested[field] != resolved[field] for field in shared_fields):
        raise SimulationError("simulator requested and resolved plans disagree")
    if Path(requested["output_parent"]).resolve() != Path(requested["output_parent"]):
        raise SimulationError("simulator output parent is not canonical")
    allowed_names = {requested["run_id"], f".incomplete-{requested['run_id']}"}
    if Path(requested["output_parent"]) != root.parent or root.name not in allowed_names:
        raise SimulationError("simulator bundle location contradicts its requested plan")
    load_json_document(root / "capabilities.json", "simulator-capabilities.schema.json")
    confirmation = load_json_document(
        root / "runtime-confirmation.json", "simulator-runtime-confirmation.schema.json"
    )
    if confirmation["plan_sha256"] != session["plan_sha256"]:
        raise SimulationError("simulator confirmation contradicts the resolved plan")
    quiescence = load_json_document(root / "quiescence.json", "simulator-quiescence.schema.json")
    if quiescence["verified"] != (session["cleanup_outcome"] == "verified"):
        raise SimulationError("simulator quiescence contradicts cleanup")
    phases = {item["phase"] for item in session["events"] if item["outcome"] == "completed"}
    if "rf_off" in phases:
        _validate_cf32_fixture(root / "rf-off.cf32", resolved["rf_off_fixture_sample_count"])
    if session["carrier_gate"] != "not_run":
        _validate_cf32_fixture(root / "rf-on.cf32", resolved["rf_on_fixture_sample_count"])
    if session["decode_gate"] != "not_run":
        _validate_cf32_fixture(
            root / "coherent-compact.cf32", resolved["coherent_fixture_sample_count"]
        )
        summary = load_json_document(
            root / "decode-summary.json", "simulator-decode-summary.schema.json"
        )
        validate_simulator_decode_summary(summary, root)
        for slot in summary["slots"]:
            _validate_wav_fixture(root / slot["wav"]["path"], resolved)
        if summary["gate_outcome"] != session["decode_gate"]:
            raise SimulationError("simulator session and decode summary disagree")
    if session["carrier_gate"] != "not_run":
        carrier = load_json_document(root / "carrier-analysis.json", "carrier-analysis.schema.json")
        metrics, contract = carrier["metrics"], carrier["contract"]
        measured_offset = (
            metrics["strongest_transmitter_added_frequency_hz"] - metrics["requested_frequency_hz"]
        )
        expected_gate = (
            "passed"
            if abs(measured_offset) <= contract["offset_gate_hz"]
            and metrics["best_20hz_resolved_power_share"] >= contract["share_gate"]
            else "failed"
        )
        if (
            metrics["strongest_offset_hz"] != measured_offset
            or carrier["gate_outcome"] != expected_gate
            or session["carrier_gate"] != expected_gate
        ):
            raise SimulationError("simulator carrier gate contradicts retained metrics")
    result = load_json_document(root / "result.json", "simulator-result.schema.json")
    if result != _derive_result(session):
        raise SimulationError("simulator result contradicts its session")
    manifest = root / "SHA256SUMS"
    if manifest.read_text(encoding="utf-8") != render_manifest(build_manifest(root)):
        raise SimulationError("simulator manifest does not authenticate the bundle")
    index = load_json_document(root / "artifact-index.json", "simulator-artifact-index.schema.json")
    expected = [
        asdict(item)
        for item in build_manifest(root)
        if item.path not in {"artifact-index.json", "SHA256SUMS"}
    ]
    if index["artifacts"] != expected:
        raise SimulationError("simulator artifact index is incomplete or contradictory")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_paths != _expected_artifact_paths(session):
        raise SimulationError("simulator lifecycle artifact set is incomplete or unexpected")


def _expected_artifact_paths(session: dict[str, Any]) -> set[str]:
    expected = {
        "requested-plan.json",
        "resolved-plan.json",
        "capabilities.json",
        "runtime-confirmation.json",
        "simulator-session.json",
        "result.json",
        "quiescence.json",
        "artifact-index.json",
        "SHA256SUMS",
    }
    child_names = [child["name"] for child in session["children"]]
    last_failed = bool(session["children"]) and (
        session["children"][-1]["timed_out"] or session["children"][-1]["return_code"] != 0
    )
    if "receiver-rf-off" in child_names and not (len(child_names) == 1 and last_failed):
        expected.update({"rf-off.cf32", "rf-on.cf32"})
    if session["carrier_gate"] != "not_run" or (
        "transmitter-frames" in child_names and last_failed
    ):
        expected.add("carrier-analysis.json")
    if session["decode_gate"] != "not_run":
        expected.update({"coherent-compact.cf32", "decode-summary.json", "tools/fake wsprd.py"})
        for label, timestamp in (
            ("0000", "20260812T000000Z"),
            ("0002", "20260812T000200Z"),
            ("0004", "20260812T000400Z"),
        ):
            expected.update({f"audio-{label}.json", f"decoder-{label}.json", f"{timestamp}.wav"})
    return expected


def _validate_cf32_fixture(path: Path, expected_samples: int) -> None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size != expected_samples * 8:
        raise SimulationError("simulator CF32 fixture size contradicts its resolved plan")
    inspection = inspect_cf32(path)
    if inspection.sample_count != expected_samples or inspection.clipped_samples != 0:
        raise SimulationError("simulator CF32 fixture contents contradict their contract")


def _validate_wav_fixture(path: Path, resolved: dict[str, Any]) -> None:
    if not path.is_file() or path.is_symlink():
        raise SimulationError("simulator WAV fixture is missing or unsafe")
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise SimulationError("simulator WAV fixture has an invalid RIFF/WAVE header")
    riff_size = struct.unpack_from("<I", data, 4)[0]
    if riff_size + 8 != len(data):
        raise SimulationError("simulator WAV RIFF size does not match the retained file")
    offset = 12
    format_chunks: list[bytes] = []
    data_chunks: list[bytes] = []
    while offset < len(data):
        if len(data) - offset < 8:
            raise SimulationError("simulator WAV contains a truncated chunk header")
        chunk_id = data[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        padded_end = payload_end + (chunk_size & 1)
        if payload_end > len(data) or padded_end > len(data):
            raise SimulationError("simulator WAV contains a truncated chunk payload")
        if chunk_size & 1 and data[payload_end:padded_end] != b"\0":
            raise SimulationError("simulator WAV contains invalid RIFF chunk padding")
        payload = data[payload_start:payload_end]
        if chunk_id == b"fmt ":
            format_chunks.append(payload)
        elif chunk_id == b"data":
            data_chunks.append(payload)
        offset = padded_end
    if offset != len(data) or len(format_chunks) != 1 or len(data_chunks) != 1:
        raise SimulationError("simulator WAV requires exactly one fmt and one data chunk")
    format_payload = format_chunks[0]
    if len(format_payload) != 16:
        raise SimulationError("simulator WAV requires the canonical PCM fmt chunk")
    audio_format, channels, sample_rate, byte_rate, block_align, bits_per_sample = struct.unpack(
        "<HHIIHH", format_payload
    )
    expected_channels = resolved["wav_channels"]
    expected_width = resolved["wav_sample_width_bytes"]
    expected_rate = resolved["wav_sample_rate_hz"]
    expected_block_align = expected_channels * expected_width
    expected_byte_rate = expected_rate * expected_block_align
    expected_data_size = resolved["wav_frame_count"] * expected_block_align
    if (
        audio_format != 1
        or channels != expected_channels
        or sample_rate != expected_rate
        or bits_per_sample != expected_width * 8
        or block_align != expected_block_align
        or byte_rate != expected_byte_rate
        or len(data_chunks[0]) != expected_data_size
    ):
        raise SimulationError("simulator WAV PCM contract contradicts its resolved plan")
    try:
        with wave.open(str(path), "rb") as handle:
            actual = (
                handle.getframerate(),
                handle.getnchannels(),
                handle.getsampwidth(),
                handle.getnframes(),
                handle.getcomptype(),
            )
    except (wave.Error, EOFError) as error:
        raise SimulationError("simulator WAV fixture is invalid") from error
    expected = (
        resolved["wav_sample_rate_hz"],
        resolved["wav_channels"],
        resolved["wav_sample_width_bytes"],
        resolved["wav_frame_count"],
        "NONE",
    )
    if actual != expected:
        raise SimulationError("simulator WAV fixture contradicts its resolved plan")


def _worker_main(arguments: list[str]) -> int:
    if len(arguments) != 2 or arguments[0] != "--worker":
        raise SimulationError("simulator worker requires one private request document")
    request_path = Path(arguments[1])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    validate_document(request, "simulator-plan.schema.json")
    plan = SimulatorPlan(
        run_id=request["run_id"],
        output_parent=Path(request["output_parent"]),
        time_scale=request["time_scale"],
        child_timeout_s=request["child_timeout_s"],
        overall_timeout_s=request["overall_timeout_s"],
        injection=request["injection"],
    )
    result = _run_simulation_inner(plan)
    sys.stdout.write(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main(sys.argv[1:]))
