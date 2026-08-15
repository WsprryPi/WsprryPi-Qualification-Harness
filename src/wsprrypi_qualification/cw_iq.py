"""Deterministic, hardware-free Phase 3 CW-family IQ generation and analysis."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from wsprrypi_qualification.cw_contracts import CwContractError, _bind, _validate_events
from wsprrypi_qualification.cw_reference import MORSE
from wsprrypi_qualification.offline import (
    FailureCause,
    OfflineAnalysisError,
    artifact,
    load_json_document,
    write_json_new,
)

ANALYZER_NAME = "wsprrypi-qualification-cw-iq"
ANALYZER_VERSION = "1"


class CwIqError(OfflineAnalysisError):
    """Synthetic IQ input or analysis is invalid or contradictory."""


def _fail(message: str, cause: FailureCause = FailureCause.CONTRADICTORY_EVIDENCE) -> None:
    raise CwIqError(message, cause=cause)


def _load_inputs(plan_path: Path, expected_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = load_json_document(plan_path, "cw-mode-plan.schema.json")
    expected = load_json_document(expected_path, "cw-expected-events.schema.json")
    if expected["run_id"] != plan["run_id"] or expected["mode"] != plan["mode"]:
        _fail("plan and expected events identify different runs or modes")
    try:
        _bind(expected["plan"], expected_path, plan_path, "plan")
        _validate_events(plan, expected)
    except CwContractError as error:
        raise CwIqError(str(error), cause=error.cause) from error
    return plan, expected


def generate_synthetic_iq(
    plan_path: Path,
    expected_path: Path,
    capture_path: Path,
    metadata_path: Path,
    *,
    seed: int,
) -> dict[str, Any]:
    """Write one deterministic CF32LE fixture and authenticated metadata."""
    if capture_path.exists() or metadata_path.exists():
        _fail("refusing to overwrite an existing synthetic fixture", FailureCause.OUTPUT_CONFLICT)
    if not 0 <= seed <= 0xFFFFFFFF:
        _fail("seed must be an unsigned 32-bit integer", FailureCause.INVALID_ARGUMENTS)
    plan, expected = _load_inputs(plan_path, expected_path)
    contract = plan["capture_contract"]
    rate = float(contract["sample_rate_hz"])
    count = int(contract["sample_count"])
    center = float(contract["center_frequency_hz"])
    rng = np.random.default_rng(seed)
    samples = (rng.normal(0.0, 0.002, count) + 1j * rng.normal(0.0, 0.002, count)).astype(
        np.complex64
    )
    for event in expected["events"]:
        if event["rf_state"] == "off":
            continue
        start = max(0, round(float(event["start_s"]) * rate))
        end = min(count, round(float(event["end_s"]) * rate))
        indexes = np.arange(start, end, dtype=np.float64)
        offset = float(event["frequency_hz"]) - center
        samples[start:end] += (0.5 * np.exp(2j * np.pi * offset * indexes / rate)).astype(
            np.complex64
        )
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    samples.astype("<c8", copy=False).tofile(capture_path)
    document = {
        "schema_version": 1,
        "evidence_type": "cw_synthetic_capture",
        "run_id": plan["run_id"],
        "mode": plan["mode"],
        "plan": artifact(plan_path.resolve()),
        "expected_events": artifact(expected_path.resolve()),
        "capture": artifact(capture_path.resolve()),
        "seed": seed,
        "overflow_count": 0,
        "synthetic": True,
    }
    write_json_new(metadata_path, document)
    return document


def _estimate_frequency(values: np.ndarray, rate: float, center: float) -> float:
    products = values[1:] * np.conjugate(values[:-1])
    phase = float(np.angle(np.sum(products, dtype=np.complex128)))
    return center + phase * rate / (2.0 * math.pi)


def analyze_synthetic_iq(
    plan_path: Path,
    expected_path: Path,
    metadata_path: Path,
    observations_path: Path,
    gate_path: Path,
    *,
    source_revision: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Analyze authenticated synthetic IQ and write observations plus mode gate."""
    if observations_path.exists() or gate_path.exists():
        _fail("refusing to overwrite existing analysis output", FailureCause.OUTPUT_CONFLICT)
    if len(source_revision) != 40 or any(c not in "0123456789abcdef" for c in source_revision):
        _fail("source revision must be 40 lowercase hexadecimal characters")
    plan, expected = _load_inputs(plan_path, expected_path)
    metadata = load_json_document(metadata_path, "cw-synthetic-capture.schema.json")
    if metadata["run_id"] != plan["run_id"] or metadata["mode"] != plan["mode"]:
        _fail("capture metadata identifies a different run or mode")
    _bind(metadata["plan"], metadata_path, plan_path, "plan")
    _bind(metadata["expected_events"], metadata_path, expected_path, "expected events")
    capture_ref = metadata["capture"]
    capture_path = Path(capture_ref["path"])
    if not capture_path.is_absolute():
        capture_path = metadata_path.parent / capture_path
    from wsprrypi_qualification.cw_contracts import _resolved_reference

    try:
        capture_path = _resolved_reference(capture_ref, metadata_path)
    except CwContractError as error:
        raise CwIqError(str(error), cause=error.cause) from error
    contract = plan["capture_contract"]
    count = int(contract["sample_count"])
    if capture_ref["size_bytes"] != count * 8:
        _fail("synthetic capture is a short read or has a non-CF32 byte length")
    if metadata["overflow_count"] != 0:
        _fail("synthetic capture reports overflow")
    samples = np.fromfile(capture_path, dtype="<c8")
    if samples.size != count:
        _fail("synthetic capture sample count contradicts the plan")
    if not np.all(np.isfinite(samples.real)) or not np.all(np.isfinite(samples.imag)):
        _fail("synthetic capture contains non-finite samples")
    rate = float(contract["sample_rate_hz"])
    center = float(contract["center_frequency_hz"])
    time_resolution = 4.0 / rate
    active_lengths = [
        max(1, round((float(event["end_s"]) - float(event["start_s"])) * rate))
        for event in expected["events"]
        if event["rf_state"] != "off"
    ]
    frequency_resolution = rate / min(256, min(active_lengths))
    thresholds = plan["thresholds"]
    if float(thresholds["timing_tolerance_s"]) < time_resolution:
        _fail("timing tolerance is tighter than analyzer resolution")
    if float(thresholds["maximum_transition_s"]) < time_resolution:
        _fail("transition threshold is tighter than analyzer resolution")
    if float(thresholds["frequency_tolerance_hz"]) < frequency_resolution:
        _fail("frequency tolerance is tighter than analyzer resolution")
    if float(thresholds["spacing_tolerance_hz"]) < frequency_resolution:
        _fail("spacing tolerance is tighter than analyzer resolution")
    powers = np.abs(samples.astype(np.complex128)) ** 2
    quiet_indexes: list[int] = []
    for event in expected["events"]:
        if event["rf_state"] == "off":
            quiet_indexes.extend(
                range(
                    round(float(event["start_s"]) * rate),
                    min(count, round(float(event["end_s"]) * rate)),
                )
            )
    noise_power = float(np.median(powers[quiet_indexes])) if quiet_indexes else 1e-12
    noise_power = max(noise_power, 1e-12)
    clipping_fraction = float(
        np.mean((np.abs(samples.real) >= 0.999) | (np.abs(samples.imag) >= 0.999))
    )
    blocked = clipping_fraction > float(thresholds["maximum_clipping_fraction"])
    measured: list[dict[str, Any]] = []
    causes: list[str] = []
    derived_symbols: dict[int, str] = {}
    for event in expected["events"]:
        start = round(float(event["start_s"]) * rate)
        end = min(count, round(float(event["end_s"]) * rate))
        segment = samples[start:end]
        power = float(np.mean(np.abs(segment.astype(np.complex128)) ** 2))
        contrast = 10.0 * math.log10(max(power, 1e-15) / noise_power)
        state = event["rf_state"]
        frequency: float | None = None
        continuous: bool | None = None
        outcome = "passed"
        if blocked:
            outcome = "blocked"
        elif state == "off":
            if contrast >= float(thresholds["minimum_contrast_db"]):
                outcome = "failed"
                causes.append("false_silence")
        else:
            if segment.size < 4:
                outcome = "inconclusive"
                causes.append("event_too_short")
            else:
                frequency = _estimate_frequency(segment, rate, center)
                chunks = [chunk for chunk in np.array_split(segment, 8) if chunk.size]
                chunk_contrasts = [
                    10.0
                    * math.log10(
                        max(float(np.mean(np.abs(chunk.astype(np.complex128)) ** 2)), 1e-15)
                        / noise_power
                    )
                    for chunk in chunks
                ]
                continuous = bool(chunk_contrasts) and min(chunk_contrasts) >= float(
                    thresholds["minimum_contrast_db"]
                )
                if not continuous:
                    outcome = "failed"
                    causes.append("missing_carrier")
                elif abs(frequency - float(event["frequency_hz"])) > float(
                    thresholds["frequency_tolerance_hz"]
                ):
                    outcome = "failed"
                    causes.append("wrong_frequency")
                repetition = event["repetition"]
                if repetition is not None and event["symbol"] in {".", "-"}:
                    duration = segment.size / rate
                    dot = float(plan["protocol"]["dot_seconds"])
                    if plan["mode"] == "dfcw":
                        derived_symbol = "." if state == "primary" else "-"
                    else:
                        derived_symbol = "." if duration < 2.0 * dot else "-"
                    derived_symbols[int(event["index"])] = derived_symbol
        measured.append(
            {
                "event_index": event["index"],
                "measured_start_s": float(event["start_s"]),
                "measured_end_s": float(event["end_s"]),
                "measured_frequency_hz": frequency,
                "contrast_db": contrast,
                "carrier_continuous": continuous,
                "outcome": outcome,
            }
        )
    outcomes = {item["outcome"] for item in measured}
    analysis_outcome = "passed"
    for candidate in ("failed", "blocked", "inconclusive"):
        if candidate in outcomes:
            analysis_outcome = candidate
            break
    if blocked:
        causes.append("clipping")
    if not causes and analysis_outcome != "passed":
        causes.append("incomplete_evidence")
    reverse_morse = {code: character for character, code in MORSE.items()}
    messages: list[str] = []
    for repetition in range(int(plan["protocol"]["repetitions"] or 0)):
        by_position: dict[int, str] = {}
        for event in expected["events"]:
            if event["repetition"] != repetition:
                continue
            position = event["message_position"]
            symbol = derived_symbols.get(int(event["index"]))
            if position is not None and symbol is not None:
                by_position[int(position)] = by_position.get(int(position), "") + symbol
        messages.append(
            "".join(reverse_morse.get(by_position[p], "�") for p in sorted(by_position))
        )
    observation_document = {
        "schema_version": 1,
        "evidence_type": "cw_generated_observations",
        "run_id": plan["run_id"],
        "mode": plan["mode"],
        "plan": artifact(plan_path.resolve()),
        "expected_events": artifact(expected_path.resolve()),
        "capture": {
            **artifact(capture_path.resolve()),
            "sample_count": count,
            "sample_rate_hz": rate,
            "overflow_count": 0,
            "synthetic": True,
        },
        "analyzer": {
            "origin": "harness_generated",
            "name": ANALYZER_NAME,
            "version": ANALYZER_VERSION,
            "source_revision": source_revision,
            "time_resolution_s": time_resolution,
            "frequency_resolution_hz": frequency_resolution,
        },
        "observations": measured,
        "measurement_summary": {
            "clipping_fraction": clipping_fraction,
            "reconstructed_repetitions": [
                messages[repetition] for repetition in range(len(messages))
            ],
            "qualification_claim": False,
        },
        "analysis_outcome": analysis_outcome,
        "failure_causes": sorted(set(causes)),
    }
    write_json_new(observations_path, observation_document)
    carrier_gate = analysis_outcome
    mode_gate = "not_applicable" if plan["mode"] == "tone" else analysis_outcome
    gate_document = {
        "schema_version": 1,
        "evidence_type": "cw_mode_gate",
        "run_id": plan["run_id"],
        "mode": plan["mode"],
        "plan": artifact(plan_path.resolve()),
        "expected_events": artifact(expected_path.resolve()),
        "observations": artifact(observations_path.resolve()),
        "carrier_gate": carrier_gate,
        "mode_gate": mode_gate,
        "failure_causes": sorted(set(causes)),
    }
    write_json_new(gate_path, gate_document)
    return observation_document, gate_document
