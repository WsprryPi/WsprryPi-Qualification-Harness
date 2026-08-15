"""Phase 1 fail-closed contracts for tone and CW-family evidence chains."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wsprrypi_qualification.cw_reference import ReferenceEncoderError, generate_expected_events
from wsprrypi_qualification.offline import (
    FailureCause,
    OfflineAnalysisError,
    load_json_document,
    sha256_file,
)


class CwContractError(OfflineAnalysisError):
    """A Phase 1 CW contract chain is malformed, unbound, or contradictory."""


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
    if thresholds["spacing_tolerance_hz"] < analyzer["frequency_resolution_hz"]:
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
        _fail("Phase 1 final status must remain inconclusive")
    if session["qualification_claim"]:
        _fail("Phase 1 contract validation cannot authorize hardware qualification")
    if not session["failure_causes"]:
        _fail("non-qualifying Phase 1 sessions require a failure cause")


def load_cw_contract_chain(
    plan_path: Path,
    expected_path: Path,
    observations_path: Path,
    gate_path: Path,
    session_path: Path,
) -> dict[str, Any]:
    """Load and cross-validate the complete Phase 1 document chain."""
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
