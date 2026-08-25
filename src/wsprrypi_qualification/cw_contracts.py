"""Fail-closed contracts for tone and CW-family document chains."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from wsprrypi_qualification.cw_reference import ReferenceEncoderError, generate_expected_events
from wsprrypi_qualification.offline import (
    FailureCause,
    OfflineAnalysisError,
    load_json_document,
    sha256_file,
)


class CwContractError(OfflineAnalysisError):
    """A CW contract chain is malformed, unbound, or contradictory."""


def _fail(message: str) -> None:
    raise CwContractError(message, cause=FailureCause.CONTRADICTORY_EVIDENCE)


def _resolved_reference(reference: dict[str, Any], owner: Path) -> Path:
    candidate = Path(reference["path"])
    if not candidate.is_absolute():
        candidate = owner.parent / candidate
    try:
        resolved = candidate.resolve(strict=True)
        stat = resolved.stat()
    except OSError as error:
        raise CwContractError(f"referenced artifact is unavailable: {candidate}") from error
    if not resolved.is_file():
        _fail(f"referenced artifact is not a file: {resolved}")
    if stat.st_size != reference["size_bytes"]:
        _fail(f"referenced artifact size does not match: {resolved}")
    if sha256_file(resolved) != reference["sha256"]:
        _fail(f"referenced artifact SHA-256 does not match: {resolved}")
    return resolved


def _bind(reference: dict[str, Any], owner: Path, expected: Path, label: str) -> None:
    resolved = _resolved_reference(reference, owner)
    try:
        expected_resolved = expected.resolve(strict=True)
    except OSError as error:
        raise CwContractError(f"{label} input is unavailable: {expected}") from error
    if resolved != expected_resolved:
        _fail(f"{label} reference does not bind the supplied document")


def _validate_plan(plan: dict[str, Any]) -> None:
    mode = plan["mode"]
    protocol = plan["protocol"]
    secondary = protocol["secondary_frequency_hz"]
    if mode in {"fskcw", "dfcw"}:
        assert isinstance(secondary, (int, float))
        spacing = abs(float(secondary) - float(protocol["primary_frequency_hz"]))
        if spacing <= plan["thresholds"]["spacing_tolerance_hz"]:
            _fail("shifted-CW tones must exceed the declared spacing tolerance")
    elif secondary is not None:
        _fail("secondary frequency is valid only for shifted-CW modes")
    definition_name = protocol["definition"].lower().rsplit("@v", 1)[0]
    if definition_name.rsplit("-", 1)[-1] != mode:
        _fail("protocol definition does not identify the resolved mode")
    center = plan["capture_contract"]["center_frequency_hz"]
    half_span = plan["capture_contract"]["sample_rate_hz"] / 2.0
    frequencies = [protocol["primary_frequency_hz"]]
    if secondary is not None:
        frequencies.append(secondary)
    if any(abs(frequency - center) >= half_span for frequency in frequencies):
        _fail("planned mode frequency falls outside the receiver Nyquist span")


def _validate_events(plan: dict[str, Any], expected: dict[str, Any]) -> None:
    events = expected["events"]
    mode = plan["mode"]
    allowed_roles = {
        "tone": {"quiet", "carrier"},
        "cw": {
            "quiet",
            "dot",
            "dash",
            "intra_element_gap",
            "inter_character_gap",
            "inter_word_gap",
            "transition",
        },
        "qrss": {
            "quiet",
            "dot",
            "dash",
            "intra_element_gap",
            "inter_character_gap",
            "inter_word_gap",
            "transition",
        },
        "fskcw": {
            "quiet",
            "mark",
            "space",
            "intra_element_gap",
            "inter_character_gap",
            "inter_word_gap",
            "transition",
        },
        "dfcw": {
            "quiet",
            "dot",
            "dash",
            "intra_element_gap",
            "inter_character_gap",
            "inter_word_gap",
            "transition",
        },
    }
    if [event["index"] for event in events] != list(range(len(events))):
        _fail("expected-event indexes must be contiguous and ordered from zero")
    previous_end = 0.0
    primary = plan["protocol"]["primary_frequency_hz"]
    secondary = plan["protocol"]["secondary_frequency_hz"]
    carrier_count = 0
    for event in events:
        if event["start_s"] < previous_end or event["end_s"] <= event["start_s"]:
            _fail("expected events must be ordered, non-overlapping, and positive duration")
        previous_end = float(event["end_s"])
        state = event["rf_state"]
        frequency = event["frequency_hz"]
        if state == "off":
            if frequency is not None or event["continuity_required"]:
                _fail("RF-off events cannot have a frequency or require continuity")
        elif state == "primary":
            carrier_count += 1
            if frequency != primary:
                _fail("primary expected-event frequency contradicts the resolved plan")
        else:
            carrier_count += 1
            if secondary is None or frequency != secondary:
                _fail("secondary expected-event frequency contradicts the resolved plan")
        if event["role"] not in allowed_roles[mode]:
            _fail(f"{mode} timeline contains an event role from another mode")
        if mode == "tone":
            if event["symbol"] is not None or event["role"] not in {"quiet", "carrier"}:
                _fail("tone events cannot contain keyed-mode symbols or roles")
        elif event["role"] == "carrier":
            _fail("keyed modes require protocol-specific event roles")
    if mode == "tone":
        cycles = plan["protocol"]["tone_cycles"]
        assert isinstance(cycles, int)
        if len(events) != cycles * 2 + 1:
            _fail("tone timeline must contain one leading/trailing quiet event per cycle set")
        for index, event in enumerate(events):
            expected_state = "off" if index % 2 == 0 else "primary"
            expected_role = "quiet" if index % 2 == 0 else "carrier"
            expected_duration = (
                plan["protocol"]["tone_off_seconds"]
                if index % 2 == 0
                else plan["protocol"]["tone_on_seconds"]
            )
            if event["rf_state"] != expected_state or event["role"] != expected_role:
                _fail("tone timeline must alternate quiet and carrier from quiet to quiet")
            if expected_state == "primary" and not event["continuity_required"]:
                _fail("tone carrier events must require continuity")
            if event["end_s"] - event["start_s"] != expected_duration:
                _fail("tone event duration contradicts the resolved plan")
        if carrier_count != cycles:
            _fail("tone timeline does not contain exactly the required carrier cycles")
    if expected["protocol_definition"] != plan["protocol"]["definition"]:
        _fail("expected-event protocol definition contradicts the resolved plan")
    capture_duration = (
        plan["capture_contract"]["sample_count"] / plan["capture_contract"]["sample_rate_hz"]
    )
    if events[-1]["end_s"] > capture_duration:
        _fail("expected timeline extends beyond the planned capture duration")
    try:
        regenerated = generate_expected_events(plan)
    except ReferenceEncoderError as error:
        _fail(f"expected timeline cannot be regenerated: {error}")
    if events != regenerated:
        _fail("expected timeline does not exactly match the independent reference encoder")


def _validate_observations(
    plan: dict[str, Any], expected: dict[str, Any], observations: dict[str, Any], path: Path
) -> None:
    _resolved_reference(observations["capture"], path)
    capture = observations["capture"]
    contract = plan["capture_contract"]
    if capture["size_bytes"] != capture["sample_count"] * 8:
        _fail("CF32 capture size must equal sample_count * 8")
    if capture["sample_count"] != contract["sample_count"]:
        _fail("capture sample count contradicts the resolved plan")
    if capture["sample_rate_hz"] != contract["sample_rate_hz"]:
        _fail("capture sample rate contradicts the resolved plan")
    if capture["overflow_count"] > contract["overflow_max"]:
        _fail("capture overflow exceeds the resolved plan")
    analyzer = observations["analyzer"]
    thresholds = plan["thresholds"]
    if thresholds["frequency_tolerance_hz"] < analyzer["frequency_resolution_hz"]:
        _fail("frequency tolerance is tighter than analyzer resolution")
    if thresholds["timing_tolerance_s"] < analyzer["time_resolution_s"]:
        _fail("timing tolerance is tighter than analyzer resolution")
    if plan["mode"] in {"fskcw", "dfcw"} and (
        thresholds["spacing_tolerance_hz"] < analyzer["frequency_resolution_hz"]
    ):
        _fail("spacing tolerance is tighter than analyzer resolution")
    if thresholds["maximum_transition_s"] < analyzer["time_resolution_s"]:
        _fail("transition threshold is tighter than analyzer resolution")
    measured = observations["observations"]
    if [item["event_index"] for item in measured] != list(range(len(expected["events"]))):
        _fail("generated observations must cover every expected event exactly once in order")
    for item in measured:
        start = item["measured_start_s"]
        end = item["measured_end_s"]
        if start is not None and end is not None and end <= start:
            _fail("measured event end must follow its start")
    outcomes = {item["outcome"] for item in measured}
    derived = (
        "failed"
        if "failed" in outcomes
        else "blocked"
        if "blocked" in outcomes
        else "inconclusive"
        if "inconclusive" in outcomes
        else "passed"
    )
    if observations["analysis_outcome"] != derived:
        _fail("analysis outcome contradicts generated event observations")
    if (derived == "passed") == bool(observations["failure_causes"]):
        _fail("analysis failure causes contradict the analysis outcome")
    shifted_model = observations.get("measurement_summary", {}).get("shifted_frequency_model")
    unshifted_model = observations.get("measurement_summary", {}).get("unshifted_frequency_model")
    timing_alignment = observations.get("measurement_summary", {}).get("timing_alignment")
    if capture["synthetic"]:
        if timing_alignment is not None:
            _fail("timing alignment is restricted to acquired evidence")
    elif timing_alignment is None:
        if derived == "passed":
            _fail("passing acquired tone observations require bounded timing alignment")
    else:
        boundary_offsets: list[float] = []
        active_pairs = [
            (event, item)
            for event, item in zip(expected["events"], measured, strict=True)
            if event["rf_state"] != "off"
        ]
        if plan["mode"] == "fskcw":
            first_event, first_item = active_pairs[0]
            last_event, last_item = active_pairs[-1]
            if first_item["measured_start_s"] is None or last_item["measured_end_s"] is None:
                _fail("timing alignment requires active sequence boundaries")
            boundary_offsets.extend(
                (
                    float(first_item["measured_start_s"]) - float(first_event["start_s"]),
                    float(last_item["measured_end_s"]) - float(last_event["end_s"]),
                )
            )
        else:
            for event, item in active_pairs:
                start = item["measured_start_s"]
                end = item["measured_end_s"]
                if start is None or end is None:
                    _fail("timing alignment requires every active event boundary")
                boundary_offsets.extend(
                    (
                        float(start) - float(event["start_s"]),
                        float(end) - float(event["end_s"]),
                    )
                )
        common_shift = float(np.median(np.asarray(boundary_offsets)))
        maximum_residual = max(abs(value - common_shift) for value in boundary_offsets)
        maximum_shift = float(thresholds["maximum_alignment_shift_s"])
        if (
            abs(float(timing_alignment["common_shift_s"]) - common_shift) > 1e-6
            or abs(float(timing_alignment["maximum_boundary_residual_s"]) - maximum_residual) > 1e-6
            or float(timing_alignment["maximum_shift_s"]) != maximum_shift
            or timing_alignment["observation_count"] != len(boundary_offsets)
            or abs(common_shift) > maximum_shift
            or maximum_residual > float(thresholds["timing_tolerance_s"])
        ):
            _fail("timing alignment contradicts measured active-event boundaries")
    if plan["mode"] in {"fskcw", "dfcw"} and unshifted_model is not None:
        _fail("shifted modes cannot contain an unshifted-frequency model")
    if plan["mode"] not in {"fskcw", "dfcw"}:
        if shifted_model is not None:
            _fail("unshifted modes cannot contain a shifted-frequency model")
        if unshifted_model is None:
            if derived == "passed":
                _fail("passing unshifted observations require a relative-frequency model")
        else:
            minimum_contrast = float(thresholds["minimum_contrast_db"])
            active_frequencies = [
                float(item["measured_frequency_hz"])
                for event, item in zip(expected["events"], measured, strict=True)
                if event["rf_state"] != "off"
                and item["measured_frequency_hz"] is not None
                and item["carrier_continuous"] is True
                and float(item["contrast_db"]) >= minimum_contrast
            ]
            if not active_frequencies:
                _fail("unshifted-frequency model requires reliable active events")
            measured_primary = float(np.median(np.asarray(active_frequencies)))
            commanded_primary = float(plan["protocol"]["primary_frequency_hz"])
            maximum_residual = max(
                abs(frequency - measured_primary) for frequency in active_frequencies
            )
            if (
                unshifted_model["commanded_primary_frequency_hz"] != commanded_primary
                or abs(unshifted_model["measured_primary_frequency_hz"] - measured_primary) > 1e-6
                or abs(unshifted_model["common_offset_hz"] - (measured_primary - commanded_primary))
                > 1e-6
                or abs(unshifted_model["maximum_residual_hz"] - maximum_residual) > 1e-6
                or unshifted_model["observation_count"] != len(active_frequencies)
                or unshifted_model["acquisition_offset_gate_hz"] != 500.0
            ):
                _fail("unshifted-frequency model contradicts measured active events")
    elif shifted_model is None:
        if derived == "passed":
            _fail("passing shifted-CW observations require a frequency model")
    else:
        active_rows = []
        for event, item in zip(expected["events"], measured, strict=True):
            if event["rf_state"] not in {"primary", "secondary"}:
                continue
            if any(
                item[field] is None
                for field in ("measured_start_s", "measured_end_s", "measured_frequency_hz")
            ):
                continue
            active_rows.append(
                (
                    (float(item["measured_start_s"]) + float(item["measured_end_s"])) / 2.0,
                    1.0 if event["rf_state"] == "secondary" else 0.0,
                    float(item["measured_frequency_hz"]),
                )
            )
        if not active_rows:
            _fail("shifted-frequency model requires measured active events")
        reference_s = sum(row[0] for row in active_rows) / len(active_rows)
        primary_hz = float(shifted_model["primary_frequency_hz"])
        spacing_hz = float(shifted_model["signed_spacing_hz"])
        drift_hz_per_s = float(shifted_model["drift_hz_per_s"])
        expected_excursion = max(
            abs(drift_hz_per_s * (time_s - reference_s)) for time_s, _, _ in active_rows
        )
        expected_residual = max(
            abs(
                frequency_hz
                - (primary_hz + drift_hz_per_s * (time_s - reference_s) + state * spacing_hz)
            )
            for time_s, state, frequency_hz in active_rows
        )
        expected_transition_count = 0
        expected_correct_transition_count = 0
        previous_row: tuple[float, float, float] | None = None
        for row in active_rows:
            if previous_row is not None and row[1] != previous_row[1]:
                expected_transition_count += 1
                corrected_jump = (row[2] - previous_row[2]) - drift_hz_per_s * (
                    row[0] - previous_row[0]
                )
                expected_direction = row[1] - previous_row[1]
                if corrected_jump * expected_direction * spacing_hz > 0:
                    expected_correct_transition_count += 1
            previous_row = row
        if not math.isclose(float(shifted_model["reference_s"]), reference_s, abs_tol=1e-9):
            _fail("shifted-frequency model reference contradicts measured events")
        if not math.isclose(
            float(shifted_model["secondary_frequency_hz"]),
            primary_hz + spacing_hz,
            abs_tol=1e-9,
        ):
            _fail("shifted-frequency model frequencies contradict signed spacing")
        if not math.isclose(
            float(shifted_model["maximum_drift_excursion_hz"]),
            expected_excursion,
            abs_tol=1e-9,
        ):
            _fail("shifted-frequency model drift contradicts measured events")
        if not math.isclose(
            float(shifted_model["maximum_residual_hz"]), expected_residual, abs_tol=1e-9
        ):
            _fail("shifted-frequency model residual contradicts measured events")
        if (
            shifted_model["transition_count"] != expected_transition_count
            or shifted_model["correct_transition_count"] != expected_correct_transition_count
        ):
            _fail("shifted-frequency model transition counts contradict measured events")
    if plan["mode"] in {"fskcw", "dfcw"} and derived == "passed":
        assert shifted_model is not None
        expected_spacing = float(plan["protocol"]["secondary_frequency_hz"]) - float(
            plan["protocol"]["primary_frequency_hz"]
        )
        if abs(float(shifted_model["signed_spacing_hz"]) - expected_spacing) > float(
            thresholds["spacing_tolerance_hz"]
        ):
            _fail("passing shifted-CW observations contradict planned tone spacing")
        if (
            abs(
                float(shifted_model["primary_frequency_hz"])
                - float(plan["protocol"]["primary_frequency_hz"])
            )
            > 500.0
        ):
            _fail("passing shifted-CW observations contradict planned frequency")
        if max(
            float(shifted_model["maximum_drift_excursion_hz"]),
            float(shifted_model["maximum_residual_hz"]),
        ) > float(thresholds["frequency_tolerance_hz"]):
            _fail("passing shifted-CW observations exceed the frequency tolerance")
        if shifted_model["transition_count"] <= 0 or (
            shifted_model["correct_transition_count"] != shifted_model["transition_count"]
        ):
            _fail("passing shifted-CW observations contradict transition direction")


def _validate_gate(mode: str, observations: dict[str, Any], gate: dict[str, Any]) -> None:
    if mode == "tone":
        if gate["mode_gate"] != "not_applicable":
            _fail("tone must keep the keyed mode gate not_applicable")
    elif gate["mode_gate"] == "not_applicable":
        _fail("keyed modes require a mode gate")
    outcome = observations["analysis_outcome"]
    carrier = gate["carrier_gate"]
    mode_gate = gate["mode_gate"]
    if outcome == "passed" and (carrier != "passed" or (mode != "tone" and mode_gate != "passed")):
        _fail("passing analysis contradicts the carrier or mode gate")
    if outcome == "failed" and "failed" not in {carrier, mode_gate}:
        _fail("failed analysis requires a failed carrier or mode gate")
    if outcome == "blocked" and "blocked" not in {carrier, mode_gate}:
        _fail("blocked analysis requires a blocked carrier or mode gate")
    if outcome == "inconclusive" and "inconclusive" not in {carrier, mode_gate}:
        _fail("inconclusive analysis requires an inconclusive carrier or mode gate")
    if (outcome == "passed") == bool(gate["failure_causes"]):
        _fail("gate failure causes contradict its outcome")


def _validate_session(
    session: dict[str, Any],
    session_path: Path,
) -> None:
    lifecycle = session["lifecycle"]
    for fact in ("live_session", "runtime_authorization", "cleanup", "quiescence"):
        verified = lifecycle[f"{fact}_verified"]
        evidence = lifecycle[f"{fact}_evidence"]
        if verified != (evidence is not None):
            _fail(f"{fact} verification must agree with its evidence reference")
        if evidence is not None:
            _resolved_reference(evidence, session_path)
    if session["final_status"] != "inconclusive":
        _fail("hardware-free contract final status must remain inconclusive")
    if session["qualification_claim"]:
        _fail("contract validation cannot authorize hardware qualification")
    if not session["failure_causes"]:
        _fail("non-qualifying hardware-free sessions require a failure cause")


def load_cw_contract_chain(
    plan_path: Path,
    expected_path: Path,
    observations_path: Path,
    gate_path: Path,
    session_path: Path,
) -> dict[str, Any]:
    """Load and cross-validate the complete CW document chain."""
    plan = load_json_document(plan_path, "cw-mode-plan.schema.json")
    expected = load_json_document(expected_path, "cw-expected-events.schema.json")
    observations = load_json_document(observations_path, "cw-generated-observations.schema.json")
    gate = load_json_document(gate_path, "cw-mode-gate.schema.json")
    session = load_json_document(session_path, "cw-final-session.schema.json")
    documents = (expected, observations, gate, session)
    if any(document["run_id"] != plan["run_id"] for document in documents):
        _fail("all CW contract documents must have the same run ID")
    if any(document["mode"] != plan["mode"] for document in documents):
        _fail("all CW contract documents must have the same mode")
    _bind(expected["plan"], expected_path, plan_path, "plan")
    _bind(observations["plan"], observations_path, plan_path, "plan")
    _bind(observations["expected_events"], observations_path, expected_path, "expected events")
    _bind(gate["plan"], gate_path, plan_path, "plan")
    _bind(gate["expected_events"], gate_path, expected_path, "expected events")
    _bind(gate["observations"], gate_path, observations_path, "observations")
    _bind(session["plan"], session_path, plan_path, "plan")
    _bind(session["expected_events"], session_path, expected_path, "expected events")
    _bind(session["observations"], session_path, observations_path, "observations")
    _bind(session["mode_gate"], session_path, gate_path, "mode gate")
    _validate_plan(plan)
    _validate_events(plan, expected)
    _validate_observations(plan, expected, observations, observations_path)
    _validate_gate(plan["mode"], observations, gate)
    _validate_session(session, session_path)
    return {
        "run_id": plan["run_id"],
        "mode": plan["mode"],
        "final_status": session["final_status"],
        "qualification_claim": False,
        "valid": True,
    }
