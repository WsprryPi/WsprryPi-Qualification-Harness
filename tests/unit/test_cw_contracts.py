import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from wsprrypi_qualification.cli import main
from wsprrypi_qualification.cw_contracts import CwContractError, load_cw_contract_chain
from wsprrypi_qualification.cw_iq import CwIqError, analyze_synthetic_iq, generate_synthetic_iq
from wsprrypi_qualification.cw_reference import generate_expected_events


def _write(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")


def _artifact(path: Path) -> dict:
    content = path.read_bytes()
    return {
        "path": path.name,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _refresh_chain(paths: tuple[Path, Path, Path, Path, Path]) -> None:
    plan, expected_path, observations_path, gate_path, session_path = paths
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["plan"] = _artifact(plan)
    _write(expected_path, expected)
    observations = json.loads(observations_path.read_text(encoding="utf-8"))
    observations["plan"] = _artifact(plan)
    observations["expected_events"] = _artifact(expected_path)
    _write(observations_path, observations)
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gate["plan"] = _artifact(plan)
    gate["expected_events"] = _artifact(expected_path)
    gate["observations"] = _artifact(observations_path)
    _write(gate_path, gate)
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["plan"] = _artifact(plan)
    session["expected_events"] = _artifact(expected_path)
    session["observations"] = _artifact(observations_path)
    session["mode_gate"] = _artifact(gate_path)
    _write(session_path, session)


def _chain(tmp_path: Path, mode: str) -> tuple[Path, Path, Path, Path, Path]:
    run_id = f"20260815T120000Z-{mode}"
    shifted = mode in {"fskcw", "dfcw"}
    tone = mode == "tone"
    plan_path = tmp_path / "plan.json"
    expected_path = tmp_path / "expected.json"
    observations_path = tmp_path / "observations.json"
    gate_path = tmp_path / "gate.json"
    session_path = tmp_path / "session.json"
    capture_path = tmp_path / "capture.cf32"
    capture_path.write_bytes(b"\0" * 800000)
    plan = {
        "schema_version": 1,
        "evidence_type": "resolved_cw_mode_plan",
        "run_id": run_id,
        "mode": mode,
        "backend": "gpio",
        "hardware_profile": "test-profile",
        "band": "2200m",
        "source": {"parent_revision": "a" * 40, "submodule_revision": "b" * 40},
        "transmitter": {
            "host": "fixture-transmitter",
            "output": "GPIO4",
            "model": "fixture-board",
            "drive_value": 2.0,
            "drive_unit": "mA",
            "clock_reference": "fixture-clock",
        },
        "receiver": {
            "host": "fixture-receiver",
            "driver": "mock",
            "device_identity": "fixture-device",
        },
        "rf_path": {
            "attenuation_db": 60.0,
            "filter_state": "fixture-filter",
            "termination": "conducted",
            "antenna_state": "disconnected",
            "safe_input_basis": "synthetic fixture",
        },
        "protocol": {
            "definition": f"wspq-{mode}@v1",
            "message": None if tone else "TEST",
            "dot_seconds": None if tone else 1.0,
            "repetitions": None if tone else 3,
            "primary_frequency_hz": 137500.0,
            "secondary_frequency_hz": 137490.0 if shifted else None,
            "pre_quiet_seconds": None if tone else 1.0,
            "post_quiet_seconds": None if tone else 1.0,
            "intra_element_gap_units": None if tone else 0.333333 if mode == "dfcw" else 1.0,
            "inter_character_gap_units": None if tone else 1.0 if mode == "dfcw" else 3.0,
            "inter_word_gap_units": None if tone else 3.0 if mode == "dfcw" else 7.0,
            "tone_cycles": 3 if tone else None,
            "tone_on_seconds": 1.0 if tone else None,
            "tone_off_seconds": 1.0 if tone else None,
        },
        "capture_contract": {
            "format": "CF32LE",
            "sample_rate_hz": 100.0,
            "center_frequency_hz": 137500.0,
            "sample_count": 100000,
            "overflow_max": 0,
            "fixed_gain": True,
            "agc_enabled": False,
            "bias_tee_enabled": False,
            "first_read_discarded": True,
        },
        "thresholds": {
            "frequency_tolerance_hz": 1.0,
            "spacing_tolerance_hz": 1.0,
            "minimum_contrast_db": 10.0,
            "timing_tolerance_s": 0.1,
            "maximum_transition_s": 0.2,
            "maximum_clipping_fraction": 0.01,
        },
        "resolved_utc": "2026-08-15T12:00:00Z",
    }
    _write(plan_path, plan)
    if mode == "dfcw":
        plan["protocol"]["definition"] = "wsprrypi-dfcw@v1"
        _write(plan_path, plan)
    events = generate_expected_events(plan)
    event_count = len(events)
    expected = {
        "schema_version": 1,
        "evidence_type": "cw_expected_events",
        "run_id": run_id,
        "mode": mode,
        "plan": _artifact(plan_path),
        "generator": {
            "origin": "harness_generated",
            "name": "fixture",
            "version": "1",
            "source_revision": "c" * 40,
        },
        "protocol_definition": plan["protocol"]["definition"],
        "events": events,
    }
    _write(expected_path, expected)
    observations = {
        "schema_version": 1,
        "evidence_type": "cw_generated_observations",
        "run_id": run_id,
        "mode": mode,
        "plan": _artifact(plan_path),
        "expected_events": _artifact(expected_path),
        "capture": {
            **_artifact(capture_path),
            "sample_count": 100000,
            "sample_rate_hz": 100.0,
            "overflow_count": 0,
            "synthetic": True,
        },
        "analyzer": {
            "origin": "harness_generated",
            "name": "fixture",
            "version": "1",
            "source_revision": "d" * 40,
            "time_resolution_s": 0.1,
            "frequency_resolution_hz": 1.0,
        },
        "observations": [
            {
                "event_index": index,
                "measured_start_s": None,
                "measured_end_s": None,
                "measured_frequency_hz": None,
                "contrast_db": None,
                "carrier_continuous": None,
                "outcome": "inconclusive",
            }
            for index in range(event_count)
        ],
        "analysis_outcome": "inconclusive",
        "failure_causes": ["phase_2_analyzer_unavailable"],
    }
    _write(observations_path, observations)
    gate = {
        "schema_version": 1,
        "evidence_type": "cw_mode_gate",
        "run_id": run_id,
        "mode": mode,
        "plan": _artifact(plan_path),
        "expected_events": _artifact(expected_path),
        "observations": _artifact(observations_path),
        "carrier_gate": "inconclusive",
        "mode_gate": "not_applicable" if tone else "inconclusive",
        "failure_causes": ["phase_2_analyzer_unavailable"],
    }
    _write(gate_path, gate)
    session = {
        "schema_version": 1,
        "evidence_type": "cw_final_session",
        "run_id": run_id,
        "mode": mode,
        "plan": _artifact(plan_path),
        "expected_events": _artifact(expected_path),
        "observations": _artifact(observations_path),
        "mode_gate": _artifact(gate_path),
        "lifecycle": {
            "live_session_verified": False,
            "live_session_evidence": None,
            "runtime_authorization_verified": False,
            "runtime_authorization_evidence": None,
            "cleanup_verified": False,
            "cleanup_evidence": None,
            "quiescence_verified": False,
            "quiescence_evidence": None,
        },
        "failure_causes": ["phase_2_analyzer_unavailable"],
        "final_status": "inconclusive",
        "qualification_claim": False,
    }
    _write(session_path, session)
    return plan_path, expected_path, observations_path, gate_path, session_path


@pytest.mark.parametrize("mode", ["tone", "cw", "qrss", "fskcw", "dfcw"])
def test_phase3_deterministic_iq_passes_measurement_gate_but_never_qualifies(
    tmp_path: Path, mode: str
) -> None:
    plan, expected, *_ = _chain(tmp_path, mode)
    capture = tmp_path / "synthetic.cf32"
    metadata = tmp_path / "synthetic.json"
    observations = tmp_path / "phase3-observations.json"
    gate = tmp_path / "phase3-gate.json"
    first = generate_synthetic_iq(plan, expected, capture, metadata, seed=7)
    measured, derived_gate = analyze_synthetic_iq(
        plan,
        expected,
        metadata,
        observations,
        gate,
        source_revision="d" * 40,
    )
    assert first["synthetic"] is True
    assert measured["capture"]["synthetic"] is True
    assert measured["analysis_outcome"] == "passed"
    assert derived_gate["carrier_gate"] == "passed"
    assert derived_gate["mode_gate"] == ("not_applicable" if mode == "tone" else "passed")
    if mode != "tone":
        assert measured["measurement_summary"]["reconstructed_repetitions"] == ["TEST"] * 3
    assert "qualification_claim" not in measured


def test_phase3_fixture_is_byte_deterministic_and_refuses_overwrite(tmp_path: Path) -> None:
    plan, expected, *_ = _chain(tmp_path, "cw")
    left = tmp_path / "left.cf32"
    right = tmp_path / "right.cf32"
    generate_synthetic_iq(plan, expected, left, tmp_path / "left.json", seed=42)
    generate_synthetic_iq(plan, expected, right, tmp_path / "right.json", seed=42)
    assert left.read_bytes() == right.read_bytes()
    with pytest.raises(CwIqError, match="overwrite"):
        generate_synthetic_iq(plan, expected, left, tmp_path / "other.json", seed=42)


def test_phase3_capture_tampering_and_clipping_fail_closed(tmp_path: Path) -> None:
    plan, expected, *_ = _chain(tmp_path, "cw")
    capture = tmp_path / "synthetic.cf32"
    metadata = tmp_path / "synthetic.json"
    generate_synthetic_iq(plan, expected, capture, metadata, seed=9)
    capture.write_bytes(capture.read_bytes()[:-8])
    with pytest.raises(CwIqError, match=r"size|SHA-256"):
        analyze_synthetic_iq(
            plan,
            expected,
            metadata,
            tmp_path / "phase3-observations.json",
            tmp_path / "phase3-gate.json",
            source_revision="d" * 40,
        )


def test_phase3_clipping_is_fixture_blockage_not_a_pass(tmp_path: Path) -> None:
    plan, expected, *_ = _chain(tmp_path, "tone")
    capture = tmp_path / "synthetic.cf32"
    metadata = tmp_path / "synthetic.json"
    generate_synthetic_iq(plan, expected, capture, metadata, seed=3)
    capture.write_bytes(b"\0\0\x80?\0\0\x80?" * 100000)
    document = json.loads(metadata.read_text(encoding="utf-8"))
    document["capture"] = _artifact(capture)
    _write(metadata, document)
    measured, gate = analyze_synthetic_iq(
        plan,
        expected,
        metadata,
        tmp_path / "phase3-observations.json",
        tmp_path / "phase3-gate.json",
        source_revision="d" * 40,
    )
    assert measured["analysis_outcome"] == "blocked"
    assert measured["failure_causes"] == ["clipping"]
    assert gate["carrier_gate"] == "blocked"


def test_phase3_rejects_thresholds_tighter_than_resolution(tmp_path: Path) -> None:
    plan, expected, *_ = _chain(tmp_path, "cw")
    plan_document = json.loads(plan.read_text(encoding="utf-8"))
    plan_document["thresholds"]["frequency_tolerance_hz"] = 0.1
    _write(plan, plan_document)
    expected_document = json.loads(expected.read_text(encoding="utf-8"))
    expected_document["plan"] = _artifact(plan)
    _write(expected, expected_document)
    capture = tmp_path / "synthetic.cf32"
    metadata = tmp_path / "synthetic.json"
    generate_synthetic_iq(plan, expected, capture, metadata, seed=4)
    with pytest.raises(CwIqError, match="tighter than analyzer resolution"):
        analyze_synthetic_iq(
            plan,
            expected,
            metadata,
            tmp_path / "phase3-observations.json",
            tmp_path / "phase3-gate.json",
            source_revision="d" * 40,
        )


def test_phase3_conjugate_image_is_detected_from_iq(tmp_path: Path) -> None:
    plan, expected, *_ = _chain(tmp_path, "fskcw")
    capture = tmp_path / "synthetic.cf32"
    metadata = tmp_path / "synthetic.json"
    generate_synthetic_iq(plan, expected, capture, metadata, seed=11)
    samples = np.fromfile(capture, dtype="<c8")
    np.conjugate(samples).astype("<c8").tofile(capture)
    document = json.loads(metadata.read_text(encoding="utf-8"))
    document["capture"] = _artifact(capture)
    _write(metadata, document)
    measured, _ = analyze_synthetic_iq(
        plan,
        expected,
        metadata,
        tmp_path / "phase3-observations.json",
        tmp_path / "phase3-gate.json",
        source_revision="d" * 40,
    )
    assert measured["analysis_outcome"] == "failed"
    assert "wrong_frequency" in measured["failure_causes"]


def test_phase3_interrupted_required_carrier_is_detected(tmp_path: Path) -> None:
    plan, expected, *_ = _chain(tmp_path, "tone")
    capture = tmp_path / "synthetic.cf32"
    metadata = tmp_path / "synthetic.json"
    generate_synthetic_iq(plan, expected, capture, metadata, seed=12)
    samples = np.fromfile(capture, dtype="<c8")
    samples[150:175] = 0
    samples.astype("<c8").tofile(capture)
    document = json.loads(metadata.read_text(encoding="utf-8"))
    document["capture"] = _artifact(capture)
    _write(metadata, document)
    measured, _ = analyze_synthetic_iq(
        plan,
        expected,
        metadata,
        tmp_path / "phase3-observations.json",
        tmp_path / "phase3-gate.json",
        source_revision="d" * 40,
    )
    assert measured["analysis_outcome"] == "failed"
    assert "missing_carrier" in measured["failure_causes"]


@pytest.mark.parametrize("mode", ["tone", "cw", "qrss", "fskcw", "dfcw"])
def test_phase1_chain_models_every_first_class_mode_without_qualifying(
    tmp_path: Path, mode: str
) -> None:
    result = load_cw_contract_chain(*_chain(tmp_path, mode))
    assert result == {
        "run_id": f"20260815T120000Z-{mode}",
        "mode": mode,
        "final_status": "inconclusive",
        "qualification_claim": False,
        "valid": True,
    }


def test_post_selected_plan_threshold_breaks_hash_chain(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "cw")
    plan = json.loads(paths[0].read_text(encoding="utf-8"))
    plan["thresholds"]["timing_tolerance_s"] = 99.0
    _write(paths[0], plan)
    with pytest.raises(CwContractError, match=r"size|SHA-256"):
        load_cw_contract_chain(*paths)


def test_downstream_thresholds_and_manual_origin_are_rejected(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "qrss")
    observations = json.loads(paths[2].read_text(encoding="utf-8"))
    observations["thresholds"] = {"timing_tolerance_s": 9.0}
    _write(paths[2], observations)
    with pytest.raises(Exception, match="thresholds"):
        load_cw_contract_chain(*paths)

    observations.pop("thresholds")
    observations["analyzer"]["origin"] = "manual"
    _write(paths[2], observations)
    with pytest.raises(Exception, match="harness_generated"):
        load_cw_contract_chain(*paths)


def test_mode_confusion_and_false_positive_claim_fail_closed(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "tone")
    session = json.loads(paths[4].read_text(encoding="utf-8"))
    session["mode"] = "cw"
    _write(paths[4], session)
    with pytest.raises(CwContractError, match="same mode"):
        load_cw_contract_chain(*paths)

    session["mode"] = "tone"
    session["final_status"] = "qualified"
    session["qualification_claim"] = True
    session["failure_causes"] = []
    _write(paths[4], session)
    with pytest.raises(CwContractError, match=r"final status|cannot authorize"):
        load_cw_contract_chain(*paths)


def test_tone_cannot_carry_keyed_symbols_or_mode_gate(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "tone")
    expected = json.loads(paths[1].read_text(encoding="utf-8"))
    expected["events"][0]["symbol"] = "."
    _write(paths[1], expected)
    observations = json.loads(paths[2].read_text(encoding="utf-8"))
    observations["expected_events"] = _artifact(paths[1])
    _write(paths[2], observations)
    gate = json.loads(paths[3].read_text(encoding="utf-8"))
    gate["expected_events"] = _artifact(paths[1])
    gate["observations"] = _artifact(paths[2])
    _write(paths[3], gate)
    session = json.loads(paths[4].read_text(encoding="utf-8"))
    session["expected_events"] = _artifact(paths[1])
    session["observations"] = _artifact(paths[2])
    session["mode_gate"] = _artifact(paths[3])
    _write(paths[4], session)
    with pytest.raises(CwContractError, match="tone events"):
        load_cw_contract_chain(*paths)


def test_tolerance_cannot_exceed_analyzer_resolution_claim(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "dfcw")
    observations = json.loads(paths[2].read_text(encoding="utf-8"))
    observations["analyzer"]["frequency_resolution_hz"] = 2.0
    _write(paths[2], observations)
    gate = json.loads(paths[3].read_text(encoding="utf-8"))
    gate["observations"] = _artifact(paths[2])
    _write(paths[3], gate)
    session = json.loads(paths[4].read_text(encoding="utf-8"))
    session["observations"] = _artifact(paths[2])
    session["mode_gate"] = _artifact(paths[3])
    _write(paths[4], session)
    with pytest.raises(CwContractError, match="tighter than analyzer resolution"):
        load_cw_contract_chain(*paths)


def test_expected_events_must_declare_harness_generated_origin(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "cw")
    expected = json.loads(paths[1].read_text(encoding="utf-8"))
    expected["generator"]["origin"] = "manual"
    _write(paths[1], expected)
    with pytest.raises(Exception, match="harness_generated"):
        load_cw_contract_chain(*paths)


def test_non_live_chain_cannot_issue_transmitter_unqualification(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "cw")
    observations = json.loads(paths[2].read_text(encoding="utf-8"))
    observations["observations"][0]["outcome"] = "failed"
    observations["analysis_outcome"] = "failed"
    observations["failure_causes"] = ["symbol_failure"]
    _write(paths[2], observations)
    gate = json.loads(paths[3].read_text(encoding="utf-8"))
    gate["observations"] = _artifact(paths[2])
    gate["carrier_gate"] = "passed"
    gate["mode_gate"] = "failed"
    gate["failure_causes"] = ["symbol_failure"]
    _write(paths[3], gate)
    session = json.loads(paths[4].read_text(encoding="utf-8"))
    session["observations"] = _artifact(paths[2])
    session["mode_gate"] = _artifact(paths[3])
    session["final_status"] = "unqualified_decode"
    session["failure_causes"] = ["symbol_failure"]
    _write(paths[4], session)
    with pytest.raises(CwContractError, match="final status"):
        load_cw_contract_chain(*paths)


def test_lifecycle_boolean_requires_authenticated_evidence(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "tone")
    session = json.loads(paths[4].read_text(encoding="utf-8"))
    session["lifecycle"]["cleanup_verified"] = True
    _write(paths[4], session)
    with pytest.raises(CwContractError, match="cleanup verification"):
        load_cw_contract_chain(*paths)


def test_cli_validates_chain_and_rejects_tampering(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _chain(tmp_path, "tone")
    arguments = ["validate-cw-contract-chain", *(str(path) for path in paths)]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["qualification_claim"] is False
    paths[0].write_text(paths[0].read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert main(arguments) == 2
    assert "does not match" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda plan: plan["protocol"].update(definition="wspq-qrss@v1"), "resolved mode"),
        (
            lambda plan: plan["thresholds"].update(spacing_tolerance_hz=10.0),
            "spacing tolerance",
        ),
        (
            lambda plan: plan["capture_contract"].update(sample_rate_hz=10.0),
            "Nyquist span",
        ),
        (
            lambda plan: plan["thresholds"].update(maximum_transition_s=0.01),
            "transition threshold",
        ),
    ],
)
def test_plan_feasibility_is_semantically_enforced(tmp_path: Path, mutation, message: str) -> None:
    paths = _chain(tmp_path, "fskcw")
    plan = json.loads(paths[0].read_text(encoding="utf-8"))
    mutation(plan)
    _write(paths[0], plan)
    _refresh_chain(paths)
    with pytest.raises(CwContractError, match=message):
        load_cw_contract_chain(*paths)


def test_expected_timeline_must_fit_capture(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "cw")
    expected = json.loads(paths[1].read_text(encoding="utf-8"))
    expected["events"][-1]["end_s"] = 1001.0
    _write(paths[1], expected)
    _refresh_chain(paths)
    with pytest.raises(CwContractError, match="capture duration"):
        load_cw_contract_chain(*paths)


def test_plausible_but_mutated_timeline_is_rejected_by_regeneration(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "cw")
    expected = json.loads(paths[1].read_text(encoding="utf-8"))
    event = next(item for item in expected["events"] if item["role"] == "dot")
    event["message_position"] += 1
    _write(paths[1], expected)
    _refresh_chain(paths)
    with pytest.raises(CwContractError, match="independent reference encoder"):
        load_cw_contract_chain(*paths)
