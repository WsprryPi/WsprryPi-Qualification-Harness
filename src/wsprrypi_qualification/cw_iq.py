"""Deterministic, hardware-free CW-family IQ generation and analysis."""

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
ANALYZER_VERSION = "6"


class CwIqError(OfflineAnalysisError):
    """Synthetic IQ input or analysis is invalid or contradictory."""


def _fail(message: str, cause: FailureCause = FailureCause.CONTRADICTORY_EVIDENCE) -> None:
    raise CwIqError(message, cause=cause)


def _unmatched_event_cause(state: str, continuous: bool | None) -> str:
    """Distinguish absent RF from a present carrier with no resolved state boundary."""
    if state == "off":
        return "false_silence"
    if continuous is True:
        return "unresolved_frequency_transition"
    return "missing_carrier"


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


def _shifted_frequency_model(
    plan: dict[str, Any],
    expected: dict[str, Any],
    measured: list[dict[str, Any]],
    acquisition_offset_gate_hz: float,
) -> tuple[dict[str, float | int] | None, list[str]]:
    """Separate common linear drift from the commanded shifted-CW state."""
    if plan["mode"] not in {"fskcw", "dfcw"}:
        return None, []
    rows: list[tuple[float, float, float]] = []
    for event, observation in zip(expected["events"], measured, strict=True):
        frequency = observation["measured_frequency_hz"]
        start = observation["measured_start_s"]
        end = observation["measured_end_s"]
        if event["rf_state"] not in {"primary", "secondary"} or any(
            value is None for value in (frequency, start, end)
        ):
            continue
        rows.append(
            (
                (float(start) + float(end)) / 2.0,
                1.0 if event["rf_state"] == "secondary" else 0.0,
                float(frequency),
            )
        )
    if len(rows) < 3 or {row[1] for row in rows} != {0.0, 1.0}:
        return None, ["unresolvable_frequency_model", "wrong_frequency"]
    reference_s = sum(row[0] for row in rows) / len(rows)
    design = np.asarray([[1.0, time - reference_s, state] for time, state, _ in rows])
    frequencies = np.asarray([frequency for _, _, frequency in rows])
    coefficients, _, rank, singular = np.linalg.lstsq(design, frequencies, rcond=None)
    if (
        rank != 3
        or singular.size != 3
        or not np.all(np.isfinite(coefficients))
        or singular[-1] <= 0
        or singular[0] / singular[-1] > 1e8
    ):
        return None, ["unresolvable_frequency_model", "wrong_frequency"]
    primary, drift, signed_spacing = (float(value) for value in coefficients)
    predicted = design @ coefficients
    maximum_residual = float(np.max(np.abs(frequencies - predicted)))
    times = np.asarray([row[0] for row in rows])
    maximum_drift_excursion = float(np.max(np.abs(drift * (times - reference_s))))
    protocol = plan["protocol"]
    expected_primary = float(protocol["primary_frequency_hz"])
    expected_secondary = float(protocol["secondary_frequency_hz"])
    expected_signed_spacing = expected_secondary - expected_primary
    thresholds = plan["thresholds"]
    frequency_tolerance = float(thresholds["frequency_tolerance_hz"])
    spacing_tolerance = float(thresholds["spacing_tolerance_hz"])
    causes: list[str] = []
    if abs(primary - expected_primary) > acquisition_offset_gate_hz:
        causes.append("wrong_frequency")
    if abs(signed_spacing - expected_signed_spacing) > spacing_tolerance:
        causes.append("tone_spacing")
    if maximum_residual > frequency_tolerance:
        causes.append("frequency_model_residual")
    if maximum_drift_excursion > frequency_tolerance:
        causes.append("frequency_drift")
    transition_count = 0
    correct_transition_count = 0
    previous: tuple[float, float, float] | None = None
    for row in rows:
        if previous is not None and row[1] != previous[1]:
            transition_count += 1
            corrected_jump = (row[2] - previous[2]) - drift * (row[0] - previous[0])
            expected_direction = row[1] - previous[1]
            if corrected_jump * expected_direction * expected_signed_spacing > 0:
                correct_transition_count += 1
        previous = row
    if transition_count == 0 or correct_transition_count != transition_count:
        causes.append("transition_direction")
    return {
        "reference_s": reference_s,
        "primary_frequency_hz": primary,
        "secondary_frequency_hz": primary + signed_spacing,
        "signed_spacing_hz": signed_spacing,
        "drift_hz_per_s": drift,
        "maximum_drift_excursion_hz": maximum_drift_excursion,
        "maximum_residual_hz": maximum_residual,
        "transition_count": transition_count,
        "correct_transition_count": correct_transition_count,
        "acquisition_offset_gate_hz": acquisition_offset_gate_hz,
    }, sorted(set(causes))


def _unshifted_frequency_model(
    plan: dict[str, Any],
    expected: dict[str, Any],
    measured: list[dict[str, Any]],
    acquisition_offset_gate_hz: float,
) -> tuple[dict[str, float | int] | None, list[str]]:
    """Center unshifted modes on a bounded common receiver-frequency offset."""
    if plan["mode"] in {"fskcw", "dfcw"}:
        return None, []
    minimum_contrast = float(plan["thresholds"]["minimum_contrast_db"])
    rows = [
        float(observation["measured_frequency_hz"])
        for event, observation in zip(expected["events"], measured, strict=True)
        if event["rf_state"] != "off"
        and observation["measured_frequency_hz"] is not None
        and observation.get("carrier_continuous", True) is True
        and float(observation.get("contrast_db", minimum_contrast)) >= minimum_contrast
    ]
    if not rows:
        return None, ["unresolvable_frequency_model"]
    expected_primary = float(plan["protocol"]["primary_frequency_hz"])
    measured_primary = float(np.median(np.asarray(rows)))
    common_offset = measured_primary - expected_primary
    maximum_residual = max(abs(value - measured_primary) for value in rows)
    tolerance = float(plan["thresholds"]["frequency_tolerance_hz"])
    causes: list[str] = []
    if abs(common_offset) > acquisition_offset_gate_hz:
        causes.append("wrong_frequency")
    if maximum_residual > tolerance:
        causes.append("frequency_model_residual")
    return {
        "commanded_primary_frequency_hz": expected_primary,
        "measured_primary_frequency_hz": measured_primary,
        "common_offset_hz": common_offset,
        "maximum_residual_hz": maximum_residual,
        "observation_count": len(rows),
        "acquisition_offset_gate_hz": acquisition_offset_gate_hz,
    }, sorted(set(causes))


def _acquired_timing_alignment(
    plan: dict[str, Any],
    expected: dict[str, Any],
    detected_states: np.ndarray,
    rate: float,
) -> dict[str, float | int] | None:
    """Resolve one bounded common helper latency without relaxing cadence checks."""
    thresholds = plan["thresholds"]
    tolerance = float(thresholds["timing_tolerance_s"])
    maximum_shift = float(thresholds["maximum_alignment_shift_s"])
    expected_active = [event for event in expected["events"] if event["rf_state"] != "off"]
    minimum_duration = min(
        float(event["end_s"]) - float(event["start_s"]) for event in expected_active
    ) - (2.0 * tolerance)
    if minimum_duration <= 0:
        return None
    active = detected_states != 0
    runs: list[tuple[int, int]] = []
    cursor = 0
    while cursor < active.size:
        if not active[cursor]:
            cursor += 1
            continue
        end = cursor + 1
        while end < active.size and active[end]:
            end += 1
        if (end - cursor) / rate >= minimum_duration:
            runs.append((cursor, end))
        cursor = end
    boundary_offsets: list[float] = []
    if plan["mode"] == "fskcw":
        if len(runs) != 1:
            return None
        start, end = runs[0]
        boundary_offsets.extend(
            (
                (start / rate) - float(expected_active[0]["start_s"]),
                (end / rate) - float(expected_active[-1]["end_s"]),
            )
        )
    else:
        if len(runs) != len(expected_active):
            return None
        for event, (start, end) in zip(expected_active, runs, strict=True):
            boundary_offsets.extend(
                (
                    (start / rate) - float(event["start_s"]),
                    (end / rate) - float(event["end_s"]),
                )
            )
    common_shift = float(np.median(np.asarray(boundary_offsets)))
    maximum_residual = max(abs(value - common_shift) for value in boundary_offsets)
    if abs(common_shift) > maximum_shift or maximum_residual > tolerance:
        return None
    return {
        "common_shift_s": common_shift,
        "maximum_shift_s": maximum_shift,
        "maximum_boundary_residual_s": maximum_residual,
        "observation_count": len(boundary_offsets),
    }


def _acquired_shifted_centers(
    samples: np.ndarray,
    rate: float,
    center: float,
    plan: dict[str, Any],
    expected: dict[str, Any],
    common_shift_s: float,
    acquisition_offset_gate_hz: float,
) -> tuple[float, float, float, float] | None:
    """Acquire bounded common offset and drift from authenticated state interiors."""
    protocol = plan["protocol"]
    primary = float(protocol["primary_frequency_hz"])
    secondary = float(protocol["secondary_frequency_hz"])
    transition_guard_s = min(
        0.1,
        float(plan["thresholds"]["maximum_transition_s"]) / 2.0,
    )
    rows: list[tuple[float, float]] = []
    observed_states: set[str] = set()
    for event in expected["events"]:
        state = event["rf_state"]
        if state not in {"primary", "secondary"}:
            continue
        start = max(
            0,
            round((float(event["start_s"]) + common_shift_s + transition_guard_s) * rate),
        )
        end = min(
            samples.size,
            round((float(event["end_s"]) + common_shift_s - transition_guard_s) * rate),
        )
        if end - start < 4:
            continue
        measured = _estimate_frequency(samples[start:end], rate, center)
        requested = primary if state == "primary" else secondary
        offset = measured - requested
        if abs(offset) <= acquisition_offset_gate_hz:
            rows.append(((start + end) / (2.0 * rate), offset))
            observed_states.add(state)
    if len(rows) < 3 or observed_states != {"primary", "secondary"}:
        return None
    reference_s = float(np.median(np.asarray([time for time, _ in rows])))
    design = np.asarray([[1.0, time - reference_s] for time, _ in rows])
    offsets = np.asarray([offset for _, offset in rows])
    coefficients, _, rank, _ = np.linalg.lstsq(design, offsets, rcond=None)
    if rank != 2 or not np.all(np.isfinite(coefficients)):
        return None
    common_offset, drift_hz_per_s = (float(value) for value in coefficients)
    return (
        primary + common_offset,
        secondary + common_offset,
        drift_hz_per_s,
        reference_s,
    )


def _centered_complex_average(values: np.ndarray, window: int) -> np.ndarray:
    """Return an O(n) centered moving average without a large convolution kernel."""
    half = window // 2
    positions = np.arange(values.size)
    starts = np.maximum(0, positions - half)
    ends = np.minimum(values.size, positions + half + 1)
    cumulative = np.concatenate((np.asarray([0j]), np.cumsum(values, dtype=np.complex128)))
    averages: np.ndarray = (cumulative[ends] - cumulative[starts]) / (ends - starts)
    return averages


def analyze_synthetic_iq(
    plan_path: Path,
    expected_path: Path,
    metadata_path: Path,
    observations_path: Path,
    gate_path: Path,
    *,
    source_revision: str,
    _metadata_schema: str = "cw-synthetic-capture.schema.json",
    _synthetic: bool = True,
    _artifact_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Analyze authenticated synthetic IQ and write observations plus mode gate."""
    if observations_path.exists() or gate_path.exists():
        _fail("refusing to overwrite existing analysis output", FailureCause.OUTPUT_CONFLICT)
    if len(source_revision) != 40 or any(c not in "0123456789abcdef" for c in source_revision):
        _fail("source revision must be 40 lowercase hexadecimal characters")
    plan, expected = _load_inputs(plan_path, expected_path)
    metadata = load_json_document(metadata_path, _metadata_schema)
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
    frequency_resolution = rate / min(active_lengths)
    thresholds = plan["thresholds"]
    acquisition_offset_gate_hz = float(thresholds["frequency_acquisition_half_width_hz"])
    if not math.isfinite(acquisition_offset_gate_hz) or acquisition_offset_gate_hz <= 0:
        _fail("acquisition offset gate must be finite and positive")
    if float(thresholds["timing_tolerance_s"]) < time_resolution:
        _fail("timing tolerance is tighter than analyzer resolution")
    if float(thresholds["maximum_transition_s"]) < time_resolution:
        _fail("transition threshold is tighter than analyzer resolution")
    if float(thresholds["maximum_alignment_shift_s"]) < time_resolution:
        _fail("alignment threshold is tighter than analyzer resolution")
    if float(thresholds["frequency_tolerance_hz"]) < frequency_resolution:
        _fail("frequency tolerance is tighter than analyzer resolution")
    if (
        plan["mode"] in {"fskcw", "dfcw"}
        and float(thresholds["spacing_tolerance_hz"]) < frequency_resolution
    ):
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
    smoothing_samples = max(1, round(time_resolution * rate))
    kernel = np.full(smoothing_samples, 1.0 / smoothing_samples)
    smoothed_power = np.convolve(powers, kernel, mode="same")
    active_threshold = noise_power * 10.0 ** (float(thresholds["minimum_contrast_db"]) / 10.0)
    detected_states = np.zeros(count, dtype=np.int8)
    active = smoothed_power >= active_threshold
    detected_states[active] = 1
    secondary = plan["protocol"]["secondary_frequency_hz"]
    if secondary is not None:
        products = samples[1:] * np.conjugate(samples[:-1])
        classification_samples = max(
            4, round(min(0.1, float(thresholds["maximum_transition_s"]) / 2) * rate)
        )
        smoothed_products = _centered_complex_average(products, classification_samples)
        frequencies = center + np.angle(smoothed_products) * rate / (2.0 * math.pi)
        primary_center = float(plan["protocol"]["primary_frequency_hz"])
        secondary_center = float(secondary)
        if not _synthetic:
            active_only_states = np.zeros(count, dtype=np.int8)
            active_only_states[active] = 1
            preliminary_alignment = _acquired_timing_alignment(
                plan, expected, active_only_states, rate
            )
            preliminary_shift_s = (
                float(preliminary_alignment["common_shift_s"])
                if preliminary_alignment is not None
                else 0.0
            )
            acquired_centers = _acquired_shifted_centers(
                samples,
                rate,
                center,
                plan,
                expected,
                preliminary_shift_s,
                acquisition_offset_gate_hz,
            )
            if acquired_centers is not None:
                primary_center, secondary_center, acquired_drift, acquired_reference = (
                    acquired_centers
                )
            else:
                acquired_drift = 0.0
                acquired_reference = 0.0
        else:
            acquired_drift = 0.0
            acquired_reference = 0.0
        classification_times = np.arange(frequencies.size, dtype=np.float64) / rate
        drift_correction = acquired_drift * (classification_times - acquired_reference)
        secondary_samples = np.abs(frequencies - (secondary_center + drift_correction)) < np.abs(
            frequencies - (primary_center + drift_correction)
        )
        detected_states[1:][active[1:] & secondary_samples] = 2

    timing_alignment = (
        _acquired_timing_alignment(plan, expected, detected_states, rate)
        if not _synthetic
        else None
    )
    common_shift_s = (
        float(timing_alignment["common_shift_s"]) if timing_alignment is not None else 0.0
    )

    def aligned_time(value: float) -> float:
        return value + common_shift_s

    def measured_run(event: dict[str, Any]) -> tuple[int, int] | None:
        target = {"off": 0, "primary": 1, "secondary": 2}[event["rf_state"]]
        expected_start = round(aligned_time(float(event["start_s"])) * rate)
        expected_end = min(count, round(aligned_time(float(event["end_s"])) * rate))
        margin = max(smoothing_samples, round(float(thresholds["timing_tolerance_s"]) * rate) + 1)
        search_start = max(0, expected_start - margin)
        search_end = min(count, expected_end + margin)
        matches = detected_states[search_start:search_end] == target
        candidates: list[tuple[int, int]] = []
        cursor = 0
        while cursor < matches.size:
            if not matches[cursor]:
                cursor += 1
                continue
            run_end = cursor + 1
            while run_end < matches.size and matches[run_end]:
                run_end += 1
            candidates.append((search_start + cursor, search_start + run_end))
            cursor = run_end
        if not candidates:
            return None

        def score(candidate: tuple[int, int]) -> tuple[int, float]:
            start, end = candidate
            overlap = max(0, min(end, expected_end) - max(start, expected_start))
            midpoint_error = abs((start + end) / 2.0 - (expected_start + expected_end) / 2.0)
            return overlap, -midpoint_error

        return max(candidates, key=score)

    clipping_fraction = float(
        np.mean((np.abs(samples.real) >= 0.999) | (np.abs(samples.imag) >= 0.999))
    )
    blocked = clipping_fraction > float(thresholds["maximum_clipping_fraction"])
    measured: list[dict[str, Any]] = []
    causes: list[str] = []
    derived_symbols: dict[int, str] = {}
    for event_position, event in enumerate(expected["events"]):
        run = measured_run(event)
        start = run[0] if run is not None else round(float(event["start_s"]) * rate)
        end = run[1] if run is not None else min(count, round(float(event["end_s"]) * rate))
        if event_position == 0:
            start = round(float(event["start_s"]) * rate)
        if event_position == len(expected["events"]) - 1:
            end = min(count, round(float(event["end_s"]) * rate))
        segment = samples[start:end]
        power = float(np.mean(np.abs(segment.astype(np.complex128)) ** 2))
        contrast = 10.0 * math.log10(max(power, 1e-15) / noise_power)
        state = event["rf_state"]
        frequency: float | None = None
        continuous: bool | None = None
        outcome = "passed"
        expected_start = max(0, round(aligned_time(float(event["start_s"])) * rate))
        expected_end = min(count, round(aligned_time(float(event["end_s"])) * rate))
        expected_segment = samples[expected_start:expected_end]
        if not blocked and state != "off" and expected_segment.size >= 4:
            frequency = _estimate_frequency(expected_segment, rate, center)
            expected_chunks = [chunk for chunk in np.array_split(expected_segment, 8) if chunk.size]
            expected_chunk_contrasts = [
                10.0
                * math.log10(
                    max(float(np.mean(np.abs(chunk.astype(np.complex128)) ** 2)), 1e-15)
                    / noise_power
                )
                for chunk in expected_chunks
            ]
            continuous = bool(expected_chunk_contrasts) and min(expected_chunk_contrasts) >= float(
                thresholds["minimum_contrast_db"]
            )
            if not continuous:
                causes.append("missing_carrier")
            expected_off = detected_states[expected_start:expected_end] == 0
            longest_off = 0
            current_off = 0
            for is_off in expected_off:
                current_off = current_off + 1 if is_off else 0
                longest_off = max(longest_off, current_off)
            if longest_off / rate > float(thresholds["maximum_transition_s"]):
                continuous = False
                causes.append("carrier_interruption")
        if blocked:
            outcome = "blocked"
        elif run is None:
            outcome = "failed"
            causes.append(_unmatched_event_cause(state, continuous))
        elif max(
            abs(
                start / rate
                - (
                    float(event["start_s"])
                    if event_position == 0
                    else aligned_time(float(event["start_s"]))
                )
            ),
            abs(
                end / rate
                - (
                    float(event["end_s"])
                    if event_position == len(expected["events"]) - 1
                    else min(count / rate, aligned_time(float(event["end_s"])))
                ),
            ),
        ) > float(thresholds["timing_tolerance_s"]):
            outcome = "failed"
            causes.append("timing_error")
        elif state == "off":
            if contrast >= float(thresholds["minimum_contrast_db"]):
                outcome = "failed"
                causes.append("false_silence")
        else:
            if segment.size < 4:
                outcome = "inconclusive"
                causes.append("event_too_short")
            else:
                chunks = [chunk for chunk in np.array_split(segment, 8) if chunk.size]
                chunk_contrasts = [
                    10.0
                    * math.log10(
                        max(float(np.mean(np.abs(chunk.astype(np.complex128)) ** 2)), 1e-15)
                        / noise_power
                    )
                    for chunk in chunks
                ]
                measured_continuous = bool(chunk_contrasts) and min(chunk_contrasts) >= float(
                    thresholds["minimum_contrast_db"]
                )
                continuous = bool(continuous and measured_continuous)
                if not continuous:
                    outcome = "failed"
                    causes.append("missing_carrier")
                elif frequency is None:
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
                "measured_start_s": start / rate if run is not None else None,
                "measured_end_s": end / rate if run is not None else None,
                "measured_frequency_hz": frequency,
                "contrast_db": contrast,
                "carrier_continuous": continuous,
                "outcome": outcome,
            }
        )
    for position in range(len(expected["events"]) - 1):
        if blocked:
            break
        left_event = expected["events"][position]
        right_event = expected["events"][position + 1]
        if (
            left_event["rf_state"] == "off"
            or right_event["rf_state"] == "off"
            or not (left_event["continuity_required"] or right_event["continuity_required"])
        ):
            continue
        left_end = measured[position]["measured_end_s"]
        right_start = measured[position + 1]["measured_start_s"]
        if (
            left_end is not None
            and right_start is not None
            and float(right_start) - float(left_end) > float(thresholds["maximum_transition_s"])
        ):
            measured[position]["carrier_continuous"] = False
            measured[position + 1]["carrier_continuous"] = False
            measured[position]["outcome"] = "failed"
            measured[position + 1]["outcome"] = "failed"
            causes.append("carrier_interruption")
    shifted_model, shifted_causes = _shifted_frequency_model(
        plan, expected, measured, acquisition_offset_gate_hz
    )
    unshifted_model, unshifted_causes = _unshifted_frequency_model(
        plan, expected, measured, acquisition_offset_gate_hz
    )
    if not blocked and shifted_causes:
        causes.extend(shifted_causes)
        for event, observation in zip(expected["events"], measured, strict=True):
            if event["rf_state"] in {"primary", "secondary"}:
                observation["outcome"] = "failed"
    if not blocked and unshifted_causes:
        causes.extend(unshifted_causes)
        for event, observation in zip(expected["events"], measured, strict=True):
            if event["rf_state"] != "off":
                observation["outcome"] = "failed"
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

    def retained_artifact(path: Path) -> dict[str, Any]:
        reference = artifact(path.resolve())
        if _artifact_root is not None:
            reference["path"] = path.resolve().relative_to(_artifact_root.resolve()).as_posix()
        return reference

    observation_document = {
        "schema_version": 1,
        "evidence_type": "cw_generated_observations",
        "run_id": plan["run_id"],
        "mode": plan["mode"],
        "plan": retained_artifact(plan_path),
        "expected_events": retained_artifact(expected_path),
        "capture": {
            **retained_artifact(capture_path),
            "sample_count": count,
            "sample_rate_hz": rate,
            "overflow_count": 0,
            "synthetic": _synthetic,
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
            "shifted_frequency_model": shifted_model,
            "unshifted_frequency_model": unshifted_model,
            "timing_alignment": timing_alignment,
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
        "plan": retained_artifact(plan_path),
        "expected_events": retained_artifact(expected_path),
        "observations": retained_artifact(observations_path),
        "carrier_gate": carrier_gate,
        "mode_gate": mode_gate,
        "failure_causes": sorted(set(causes)),
    }
    write_json_new(gate_path, gate_document)
    return observation_document, gate_document
