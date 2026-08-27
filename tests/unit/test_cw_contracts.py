import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

import wsprrypi_qualification.cw_replay as cw_replay_module
from wsprrypi_qualification.cli import main
from wsprrypi_qualification.cw_contracts import CwContractError, load_cw_contract_chain
from wsprrypi_qualification.cw_iq import (
    CwIqError,
    _acquired_shifted_centers,
    _acquired_timing_alignment,
    _shifted_frequency_model,
    _unmatched_event_cause,
    _unshifted_frequency_model,
    analyze_synthetic_iq,
    generate_synthetic_iq,
)
from wsprrypi_qualification.cw_lifecycle import (
    INJECTIONS,
    CwLifecycleError,
    run_mock_lifecycle,
    validate_mock_lifecycle,
)
from wsprrypi_qualification.cw_reference import generate_expected_events
from wsprrypi_qualification.cw_replay import (
    CwReplayError,
    compose_acquired_replay,
    validate_replay_bundle,
)
from wsprrypi_qualification.manifests import write_manifest
from wsprrypi_qualification.offline import OfflineAnalysisError, validate_document
from wsprrypi_qualification.receiver_calibration import disabled_binding


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
            "pre_quiet_seconds": 1.0,
            "post_quiet_seconds": 1.0,
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
            "frequency_acquisition_half_width_hz": 500.0,
            "frequency_tolerance_hz": 1.0,
            "spacing_tolerance_hz": 1.0,
            "minimum_contrast_db": 10.0,
            "timing_tolerance_s": 0.1,
            "maximum_transition_s": 0.2,
            "maximum_alignment_shift_s": 0.5,
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
        "failure_causes": ["analyzer_unavailable"],
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
        "failure_causes": ["analyzer_unavailable"],
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
        "failure_causes": ["analyzer_unavailable"],
        "final_status": "inconclusive",
        "qualification_claim": False,
    }
    _write(session_path, session)
    return plan_path, expected_path, observations_path, gate_path, session_path


def test_single_repetition_mode_plan_is_valid_for_independent_live_transaction(
    tmp_path: Path,
) -> None:
    plan_path = _chain(tmp_path, "qrss")[0]
    document = json.loads(plan_path.read_text(encoding="utf-8"))
    document["protocol"]["repetitions"] = 1
    validate_document(document, "cw-mode-plan.schema.json")


def _acquired_inputs(tmp_path: Path, mode: str) -> tuple[Path, Path, Path]:
    plan, expected, *_ = _chain(tmp_path, mode)
    capture = tmp_path / "source acquired.cf32"
    synthetic_metadata = tmp_path / "discarded-synthetic-metadata.json"
    generate_synthetic_iq(plan, expected, capture, synthetic_metadata, seed=71)
    plan_document = json.loads(plan.read_text(encoding="utf-8"))
    metadata = {
        "schema_version": 1,
        "evidence_type": "cw_acquired_capture",
        "run_id": plan_document["run_id"],
        "mode": mode,
        "plan": _artifact(plan),
        "expected_events": _artifact(expected),
        "capture": _artifact(capture),
        "format": "CF32LE",
        "sample_count": 100000,
        "sample_rate_hz": 100.0,
        "center_frequency_hz": 137500.0,
        "acquired_sample_count": 100000,
        "overflow_count": 0,
        "fixed_gain": True,
        "agc_enabled": False,
        "bias_tee_enabled": False,
        "first_read_discarded": True,
        "receiver": plan_document["receiver"],
        "acquired_utc": "2026-08-15T12:00:00Z",
        "synthetic": False,
    }
    acquired_metadata = tmp_path / "acquired.json"
    _write(acquired_metadata, metadata)
    return plan, expected, acquired_metadata


@pytest.mark.parametrize("mode", ["tone", "cw", "qrss", "fskcw", "dfcw"])
def test_acquired_replay_passes_measurement_but_stays_inconclusive(
    tmp_path: Path, mode: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, mode)
    bundle = tmp_path / f"replay-{mode}"
    result = compose_acquired_replay(plan, expected, metadata, bundle, source_revision="e" * 40)
    assert result["measurement"]["carrier_gate"] == "passed"
    assert result["measurement"]["mode_gate"] == ("not_applicable" if mode == "tone" else "passed")
    assert result["final_status"] == "inconclusive"
    assert result["qualification_claim"] is False
    assert result["lifecycle"] == {
        "runtime_authorization_evidence": None,
        "live_session_evidence": None,
        "cleanup_evidence": None,
        "quiescence_evidence": None,
    }
    assert validate_replay_bundle(bundle, recompute=True)["valid"] is True


@pytest.mark.parametrize("mode", ["fskcw", "dfcw"])
def test_acquired_shifted_modes_resolve_one_common_frequency_offset(
    tmp_path: Path, mode: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, mode)
    metadata_document = json.loads(metadata.read_text(encoding="utf-8"))
    capture = source / metadata_document["capture"]["path"]
    samples = np.fromfile(capture, dtype="<c8")
    rate = float(metadata_document["sample_rate_hz"])
    times = np.arange(samples.size, dtype=np.float64) / rate
    samples *= np.exp(2j * np.pi * 20.0 * times).astype(np.complex64)
    samples.astype("<c8").tofile(capture)
    metadata_document["capture"] = _artifact(capture)
    _write(metadata, metadata_document)

    bundle = tmp_path / "bundle"
    result = compose_acquired_replay(plan, expected, metadata, bundle, source_revision="e" * 40)
    observations = json.loads((bundle / "observations.json").read_text(encoding="utf-8"))
    model = observations["measurement_summary"]["shifted_frequency_model"]
    assert result["measurement"] == {"carrier_gate": "passed", "mode_gate": "passed"}
    assert model is not None
    assert model["primary_frequency_hz"] == pytest.approx(137520.0, abs=0.25)
    assert abs(model["signed_spacing_hz"]) == pytest.approx(10.0, abs=0.25)


def test_acquired_fskcw_centers_use_one_common_offset_under_drift(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, *_ = _chain(source, "fskcw")
    plan_document = json.loads(plan.read_text(encoding="utf-8"))
    plan_document["protocol"].update(
        {
            "message": "ETE",
            "dot_seconds": 0.7,
            "repetitions": 1,
            "secondary_frequency_hz": 137495.0,
            "pre_quiet_seconds": 2.0,
            "post_quiet_seconds": 2.0,
        }
    )
    plan_document["thresholds"].update(
        {
            "frequency_tolerance_hz": 2.0,
            "spacing_tolerance_hz": 2.0,
            "timing_tolerance_s": 0.15,
            "maximum_transition_s": 0.25,
            "maximum_alignment_shift_s": 0.75,
        }
    )
    _write(plan, plan_document)
    expected_document = json.loads(expected.read_text(encoding="utf-8"))
    expected_document["plan"] = _artifact(plan)
    expected_document["events"] = generate_expected_events(plan_document)
    _write(expected, expected_document)
    capture = source / "source acquired.cf32"
    discarded_metadata = source / "discarded-synthetic-metadata.json"
    generate_synthetic_iq(plan, expected, capture, discarded_metadata, seed=73)
    metadata = source / "acquired.json"
    metadata_document = {
        "schema_version": 1,
        "evidence_type": "cw_acquired_capture",
        "run_id": plan_document["run_id"],
        "mode": "fskcw",
        "plan": _artifact(plan),
        "expected_events": _artifact(expected),
        "capture": _artifact(capture),
        "format": "CF32LE",
        "sample_count": 100000,
        "sample_rate_hz": 100.0,
        "center_frequency_hz": 137500.0,
        "acquired_sample_count": 100000,
        "overflow_count": 0,
        "fixed_gain": True,
        "agc_enabled": False,
        "bias_tee_enabled": False,
        "first_read_discarded": True,
        "receiver": plan_document["receiver"],
        "acquired_utc": "2026-08-15T12:00:00Z",
        "synthetic": False,
    }
    _write(metadata, metadata_document)
    samples = np.fromfile(capture, dtype="<c8")
    rate = float(metadata_document["sample_rate_hz"])
    times = np.arange(samples.size, dtype=np.float64) / rate
    common_offset_hz = -20.0
    drift_hz_per_s = 0.35
    samples *= np.exp(
        2j * np.pi * (common_offset_hz * times + 0.5 * drift_hz_per_s * times**2)
    ).astype(np.complex64)
    acquired = _acquired_shifted_centers(
        samples,
        rate,
        float(metadata_document["center_frequency_hz"]),
        plan_document,
        expected_document,
        0.0,
        float(plan_document["thresholds"]["frequency_acquisition_half_width_hz"]),
    )
    assert acquired is not None
    primary, secondary, acquired_drift, _ = acquired
    assert primary is not None and secondary is not None
    assert primary - secondary == pytest.approx(
        float(plan_document["protocol"]["primary_frequency_hz"])
        - float(plan_document["protocol"]["secondary_frequency_hz"]),
        abs=1e-9,
    )
    assert acquired_drift == pytest.approx(drift_hz_per_s, abs=0.02)

    samples.astype("<c8").tofile(capture)
    metadata_document["capture"] = _artifact(capture)
    _write(metadata, metadata_document)
    bundle = tmp_path / "bundle"
    result = compose_acquired_replay(plan, expected, metadata, bundle, source_revision="e" * 40)
    observations = json.loads((bundle / "observations.json").read_text(encoding="utf-8"))
    assert result["measurement"]["mode_gate"] == "passed"
    assert "carrier_interruption" not in observations["failure_causes"]
    assert all(
        observation["carrier_continuous"] is not False
        for observation in observations["observations"]
    )


@pytest.mark.parametrize("mode", ["fskcw", "dfcw"])
def test_acquired_shifted_modes_use_authenticated_nondefault_window(
    tmp_path: Path, mode: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, mode)
    plan_document = json.loads(plan.read_text(encoding="utf-8"))
    plan_document["thresholds"]["frequency_acquisition_half_width_hz"] = 25.0
    _write(plan, plan_document)
    expected_document = json.loads(expected.read_text(encoding="utf-8"))
    expected_document["plan"] = _artifact(plan)
    _write(expected, expected_document)
    metadata_document = json.loads(metadata.read_text(encoding="utf-8"))
    capture = source / metadata_document["capture"]["path"]
    rate = float(metadata_document["sample_rate_hz"])
    samples = np.fromfile(capture, dtype="<c8")
    times = np.arange(samples.size, dtype=np.float64) / rate
    samples *= np.exp(2j * np.pi * -20.0 * times).astype(np.complex64)
    samples.astype("<c8").tofile(capture)
    metadata_document["plan"] = _artifact(plan)
    metadata_document["expected_events"] = _artifact(expected)
    metadata_document["capture"] = _artifact(capture)
    _write(metadata, metadata_document)

    bundle = tmp_path / "bundle"
    result = compose_acquired_replay(plan, expected, metadata, bundle, source_revision="e" * 40)
    observations = json.loads((bundle / "observations.json").read_text(encoding="utf-8"))
    model = observations["measurement_summary"]["shifted_frequency_model"]
    assert result["measurement"]["mode_gate"] == "passed"
    assert model["primary_frequency_hz"] == pytest.approx(137_480.0, abs=0.3)
    assert model["acquisition_offset_gate_hz"] == 25.0
    assert validate_replay_bundle(bundle, recompute=True)["valid"] is True


def test_acquired_replay_is_byte_deterministic_and_portable(tmp_path: Path) -> None:
    source = tmp_path / "source with spaces"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, "cw")
    left = tmp_path / "left parent"
    right = tmp_path / "right parent"
    left.mkdir()
    right.mkdir()
    compose_acquired_replay(plan, expected, metadata, left / "bundle", source_revision="e" * 40)
    compose_acquired_replay(plan, expected, metadata, right / "other", source_revision="e" * 40)
    assert {path.name: path.read_bytes() for path in (left / "bundle").iterdir()} == {
        path.name: path.read_bytes() for path in (right / "other").iterdir()
    }
    observations = json.loads((left / "bundle" / "observations.json").read_text(encoding="utf-8"))
    assert all(
        not Path(observations[field]["path"]).is_absolute()
        for field in ("plan", "expected_events", "capture")
    )


def test_acquired_replay_rejects_acquisition_contract_conflict_and_existing_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, "tone")
    document = json.loads(metadata.read_text(encoding="utf-8"))
    document["agc_enabled"] = True
    _write(metadata, document)
    with pytest.raises((CwReplayError, OfflineAnalysisError), match=r"agc_enabled|schema"):
        compose_acquired_replay(
            plan, expected, metadata, tmp_path / "bundle", source_revision="e" * 40
        )
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(CwReplayError, match="overwrite"):
        compose_acquired_replay(plan, expected, metadata, existing, source_revision="e" * 40)


def test_acquired_replay_rejects_result_index_and_manifest_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, "qrss")
    bundle = tmp_path / "bundle"
    compose_acquired_replay(plan, expected, metadata, bundle, source_revision="e" * 40)
    result_path = bundle / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["qualification_claim"] = True
    result["final_status"] = "qualified"
    _write(result_path, result)
    write_manifest(bundle)
    with pytest.raises(OfflineAnalysisError, match="schema"):
        validate_replay_bundle(bundle)


def test_acquired_replay_rejects_unexpected_file_and_capture_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, "fskcw")
    bundle = tmp_path / "bundle"
    compose_acquired_replay(plan, expected, metadata, bundle, source_revision="e" * 40)
    (bundle / "unexpected.txt").write_text("not manifested", encoding="utf-8")
    with pytest.raises(CwReplayError, match="extras"):
        validate_replay_bundle(bundle)
    (bundle / "unexpected.txt").unlink()
    capture = bundle / "capture.cf32"
    capture.write_bytes(capture.read_bytes()[:-8])
    with pytest.raises((CwReplayError, OfflineAnalysisError), match=r"size|SHA-256"):
        validate_replay_bundle(bundle)


def test_acquired_replay_rejects_absolute_internal_reference_even_when_rehashed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, "cw")
    bundle = tmp_path / "bundle"
    compose_acquired_replay(plan, expected, metadata, bundle, source_revision="e" * 40)
    observations_path = bundle / "observations.json"
    observations = json.loads(observations_path.read_text(encoding="utf-8"))
    observations["plan"]["path"] = str((bundle / "plan.json").resolve())
    _write(observations_path, observations)
    write_manifest(bundle)
    with pytest.raises(CwReplayError, match="canonical relative"):
        validate_replay_bundle(bundle)


def test_acquired_replay_requires_canonical_utc_but_allows_capture_after_plan_resolution(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, "tone")
    document = json.loads(metadata.read_text(encoding="utf-8"))
    document["acquired_utc"] = "2026-08-15T12:00:00+01:00"
    _write(metadata, document)
    with pytest.raises(CwReplayError, match="UTC"):
        compose_acquired_replay(
            plan, expected, metadata, tmp_path / "bundle", source_revision="e" * 40
        )

    document["acquired_utc"] = "2026-08-15T12:01:00Z"
    _write(metadata, document)
    result = compose_acquired_replay(
        plan, expected, metadata, tmp_path / "other-bundle", source_revision="e" * 40
    )
    assert result["qualification_claim"] is False

    before = tmp_path / "before"
    source_before = tmp_path / "source-before"
    source_before.mkdir()
    plan_before, expected_before, metadata_before = _acquired_inputs(source_before, "tone")
    before_document = json.loads(metadata_before.read_text(encoding="utf-8"))
    before_document["acquired_utc"] = "2026-08-15T11:59:59Z"
    _write(metadata_before, before_document)
    with pytest.raises(CwReplayError, match="cannot precede"):
        compose_acquired_replay(
            plan_before, expected_before, metadata_before, before, source_revision="e" * 40
        )


def test_acquired_replay_accepts_hash_identical_local_copies_with_stale_origin_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, "tone")
    expected_document = json.loads(expected.read_text(encoding="utf-8"))
    expected_document["plan"]["path"] = "/original/pi/path/tone-plan.json"
    _write(expected, expected_document)
    metadata_document = json.loads(metadata.read_text(encoding="utf-8"))
    metadata_document["expected_events"] = _artifact(expected)
    metadata_document["expected_events"]["path"] = "/original/pi/path/tone-events.json"
    metadata_document["plan"]["path"] = "/original/pi/path/tone-plan.json"
    _write(metadata, metadata_document)
    result = compose_acquired_replay(
        plan, expected, metadata, tmp_path / "bundle", source_revision="e" * 40
    )
    assert result["qualification_claim"] is False


def test_acquired_replay_rejects_semantically_rewritten_result_causes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, "qrss")
    bundle = tmp_path / "bundle"
    compose_acquired_replay(plan, expected, metadata, bundle, source_revision="e" * 40)
    result_path = bundle / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["failure_causes"] = ["invented_cause"]
    _write(result_path, result)
    write_manifest(bundle)
    with pytest.raises(CwReplayError, match="failure causes"):
        validate_replay_bundle(bundle)


def test_acquired_replay_final_validation_failure_does_not_publish_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, "tone")
    bundle = tmp_path / "bundle"

    def reject(*args: object, **kwargs: object) -> dict:
        raise CwReplayError("injected final validation failure")

    monkeypatch.setattr(cw_replay_module, "validate_replay_bundle", reject)
    with pytest.raises(CwReplayError, match="injected"):
        compose_acquired_replay(plan, expected, metadata, bundle, source_revision="e" * 40)
    assert not bundle.exists()
    assert not list(tmp_path.glob(".bundle.incomplete-*"))


def test_acquired_replay_aligns_bounded_common_cw_latency(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, "cw")
    metadata_document = json.loads(metadata.read_text(encoding="utf-8"))
    capture = source / metadata_document["capture"]["path"]
    samples = np.fromfile(capture, dtype="<c8")
    shifted = np.roll(samples, 31)
    shifted[:31] = 0
    shifted.astype("<c8").tofile(capture)
    metadata_document["capture"] = _artifact(capture)
    _write(metadata, metadata_document)
    bundle = tmp_path / "bundle"
    result = compose_acquired_replay(plan, expected, metadata, bundle, source_revision="e" * 40)
    observations = json.loads((bundle / "observations.json").read_text(encoding="utf-8"))
    assert result["measurement"]["carrier_gate"] == "passed"
    assert "timing_error" not in observations["failure_causes"]
    alignment = observations["measurement_summary"]["timing_alignment"]
    assert alignment["common_shift_s"] == pytest.approx(
        31 / metadata_document["sample_rate_hz"], abs=0.02
    )
    expected_document = json.loads((bundle / "expected-events.json").read_text(encoding="utf-8"))
    assert any(
        measured["measured_start_s"] != event["start_s"]
        for measured, event in zip(
            observations["observations"], expected_document["events"], strict=True
        )
        if measured["measured_start_s"] is not None
    )


def test_acquired_replay_aligns_one_bounded_common_acquired_tone_latency(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, "tone")
    metadata_document = json.loads(metadata.read_text(encoding="utf-8"))
    capture = source / metadata_document["capture"]["path"]
    samples = np.fromfile(capture, dtype="<c8")
    shifted = np.roll(samples, 22)
    shifted[:22] = 0
    shifted.astype("<c8").tofile(capture)
    metadata_document["capture"] = _artifact(capture)
    _write(metadata, metadata_document)
    bundle = tmp_path / "bundle"
    result = compose_acquired_replay(plan, expected, metadata, bundle, source_revision="e" * 40)
    observations = json.loads((bundle / "observations.json").read_text(encoding="utf-8"))
    alignment = observations["measurement_summary"]["timing_alignment"]
    assert result["measurement"]["carrier_gate"] == "passed"
    assert alignment["common_shift_s"] == pytest.approx(0.22, abs=0.02)
    assert observations["failure_causes"] == []


def test_acquired_replay_cli_composes_and_validates_non_qualifying_replay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    plan, expected, metadata = _acquired_inputs(source, "dfcw")
    bundle = tmp_path / "bundle"
    assert (
        main(
            [
                "compose-cw-acquired-replay",
                str(plan),
                str(expected),
                str(metadata),
                str(bundle),
                "--source-revision",
                "e" * 40,
            ]
        )
        == 0
    )
    composed = json.loads(capsys.readouterr().out)
    assert composed["final_status"] == "inconclusive"
    assert composed["qualification_claim"] is False
    assert main(["validate-cw-acquired-replay", str(bundle)]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["valid"] is True
    assert validated["qualification_claim"] is False

    invalid_calibration = disabled_binding()
    invalid_calibration["policy"] = "required"
    calibration_path = tmp_path / "invalid-calibration.json"
    _write(calibration_path, invalid_calibration)
    assert (
        main(
            [
                "compose-cw-acquired-replay",
                str(plan),
                str(expected),
                str(metadata),
                str(tmp_path / "rejected-bundle"),
                "--source-revision",
                "e" * 40,
                "--receiver-calibration-binding",
                str(calibration_path),
            ]
        )
        == 2
    )
    assert "required receiver calibration is absent" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["tone", "cw", "qrss", "fskcw", "dfcw"])
def test_synthetic_iq_deterministic_passes_measurement_gate_but_never_qualifies(
    tmp_path: Path, mode: str
) -> None:
    plan, expected, *_ = _chain(tmp_path, mode)
    capture = tmp_path / "synthetic.cf32"
    metadata = tmp_path / "synthetic.json"
    observations = tmp_path / "synthetic-iq-observations.json"
    gate = tmp_path / "synthetic-iq-gate.json"
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


def test_synthetic_iq_fixture_is_byte_deterministic_and_refuses_overwrite(tmp_path: Path) -> None:
    plan, expected, *_ = _chain(tmp_path, "cw")
    left = tmp_path / "left.cf32"
    right = tmp_path / "right.cf32"
    generate_synthetic_iq(plan, expected, left, tmp_path / "left.json", seed=42)
    generate_synthetic_iq(plan, expected, right, tmp_path / "right.json", seed=42)
    assert left.read_bytes() == right.read_bytes()
    with pytest.raises(CwIqError, match="overwrite"):
        generate_synthetic_iq(plan, expected, left, tmp_path / "other.json", seed=42)


def test_synthetic_iq_capture_tampering_and_clipping_fail_closed(tmp_path: Path) -> None:
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
            tmp_path / "synthetic-iq-observations.json",
            tmp_path / "synthetic-iq-gate.json",
            source_revision="d" * 40,
        )


def test_synthetic_iq_clipping_is_fixture_blockage_not_a_pass(tmp_path: Path) -> None:
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
        tmp_path / "synthetic-iq-observations.json",
        tmp_path / "synthetic-iq-gate.json",
        source_revision="d" * 40,
    )
    assert measured["analysis_outcome"] == "blocked"
    assert measured["failure_causes"] == ["clipping"]
    assert gate["carrier_gate"] == "blocked"


def test_synthetic_iq_rejects_thresholds_tighter_than_resolution(tmp_path: Path) -> None:
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
            tmp_path / "synthetic-iq-observations.json",
            tmp_path / "synthetic-iq-gate.json",
            source_revision="d" * 40,
        )


def test_synthetic_iq_conjugate_image_is_detected_from_iq(tmp_path: Path) -> None:
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
        tmp_path / "synthetic-iq-observations.json",
        tmp_path / "synthetic-iq-gate.json",
        source_revision="d" * 40,
    )
    assert measured["analysis_outcome"] == "failed"
    assert "wrong_frequency" in measured["failure_causes"]


def _shifted_model_inputs(
    tmp_path: Path, *, spacing_hz: float = -10.0, drift_hz_per_s: float = 0.005
) -> tuple[dict, dict, list[dict]]:
    plan_path, expected_path, *_ = _chain(tmp_path, "fskcw")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    active_midpoints = [
        (float(event["start_s"]) + float(event["end_s"])) / 2
        for event in expected["events"]
        if event["rf_state"] != "off"
    ]
    reference = sum(active_midpoints) / len(active_midpoints)
    measured = []
    for event in expected["events"]:
        start = float(event["start_s"])
        end = float(event["end_s"])
        state = event["rf_state"]
        frequency = None
        if state != "off":
            midpoint = (start + end) / 2
            frequency = 137500.0 + drift_hz_per_s * (midpoint - reference)
            if state == "secondary":
                frequency += spacing_hz
        measured.append(
            {
                "measured_start_s": start,
                "measured_end_s": end,
                "measured_frequency_hz": frequency,
            }
        )
    return plan, expected, measured


def test_shifted_frequency_model_separates_bounded_common_drift(tmp_path: Path) -> None:
    plan, expected, measured = _shifted_model_inputs(tmp_path)
    _, causes = _shifted_frequency_model(plan, expected, measured, 500.0)
    assert causes == []


def test_shifted_frequency_model_accepts_observed_minus_704_85_hz_with_bound_window(
    tmp_path: Path,
) -> None:
    plan, expected, measured = _shifted_model_inputs(tmp_path)
    for observation in measured:
        if observation["measured_frequency_hz"] is not None:
            observation["measured_frequency_hz"] -= 704.85
    model, causes = _shifted_frequency_model(plan, expected, measured, 1_000.0)
    assert model is not None
    assert model["primary_frequency_hz"] == pytest.approx(136_795.15, abs=0.05)
    assert model["acquisition_offset_gate_hz"] == 1_000.0
    assert causes == []
    assert model is not None
    assert model["signed_spacing_hz"] == pytest.approx(-10.0)
    assert model["drift_hz_per_s"] == pytest.approx(0.005)
    assert model["correct_transition_count"] == model["transition_count"]


@pytest.mark.parametrize(
    ("offset_hz", "jitter_hz", "expected_cause"),
    [(169.0, 0.2, None), (501.0, 0.2, "wrong_frequency"), (169.0, 2.0, "frequency_model_residual")],
)
def test_unshifted_frequency_model_uses_bounded_relative_centering(
    tmp_path: Path, offset_hz: float, jitter_hz: float, expected_cause: str | None
) -> None:
    plan_path, expected_path, *_ = _chain(tmp_path, "tone")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    measured = []
    active_index = 0
    for event in expected["events"]:
        frequency = None
        if event["rf_state"] != "off":
            frequency = float(event["frequency_hz"]) + offset_hz
            frequency += jitter_hz if active_index % 2 else -jitter_hz
            active_index += 1
        measured.append({"measured_frequency_hz": frequency})
    model, causes = _unshifted_frequency_model(plan, expected, measured, 500.0)
    assert model is not None
    assert model["common_offset_hz"] == pytest.approx(offset_hz, abs=jitter_hz + 1e-6)
    if expected_cause is None:
        assert causes == []
    else:
        assert expected_cause in causes


def _tone_detected_states(
    tmp_path: Path, *, shift_s: float, inconsistent_s: float = 0.0, extra: bool = False
) -> tuple[dict, dict, np.ndarray, float]:
    plan_path, expected_path, *_ = _chain(tmp_path, "tone")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    rate = float(plan["capture_contract"]["sample_rate_hz"])
    count = int(plan["capture_contract"]["sample_count"])
    states = np.zeros(count, dtype=np.int8)
    active_index = 0
    for event in expected["events"]:
        if event["rf_state"] == "off":
            continue
        event_shift = shift_s + (inconsistent_s if active_index == 1 else 0.0)
        start = round((float(event["start_s"]) + event_shift) * rate)
        end = round((float(event["end_s"]) + event_shift) * rate)
        states[start:end] = 1
        active_index += 1
    if extra:
        states[0 : round(1.85 * rate)] = 1
    return plan, expected, states, rate


def test_acquired_tone_timing_alignment_accepts_only_one_bounded_common_shift(
    tmp_path: Path,
) -> None:
    plan, expected, states, rate = _tone_detected_states(tmp_path, shift_s=0.22)
    model = _acquired_timing_alignment(plan, expected, states, rate)
    assert model is not None
    assert model["common_shift_s"] == pytest.approx(0.22)
    assert model["maximum_boundary_residual_s"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    "case",
    ["excessive_shift", "inconsistent_latency", "extra_tone", "missing_tone", "interruption"],
)
def test_acquired_tone_timing_alignment_fails_closed(case: str, tmp_path: Path) -> None:
    plan, expected, states, rate = _tone_detected_states(
        tmp_path,
        shift_s=0.51 if case == "excessive_shift" else 0.15,
        inconsistent_s=0.11 if case == "inconsistent_latency" else 0.0,
        extra=case == "extra_tone",
    )
    if case == "missing_tone":
        active = np.flatnonzero(states)
        split = np.flatnonzero(np.diff(active) > 1)
        start = active[split[0] + 1]
        end_index = split[1] + 1 if len(split) > 1 else len(active)
        states[start : active[end_index - 1] + 1] = 0
    if case == "interruption":
        states[round(3.55 * rate) : round(3.85 * rate)] = 0
    assert _acquired_timing_alignment(plan, expected, states, rate) is None


def test_synthetic_iq_shifted_iq_with_bounded_common_drift_passes(tmp_path: Path) -> None:
    plan, expected, *_ = _chain(tmp_path, "fskcw")
    capture = tmp_path / "synthetic.cf32"
    metadata = tmp_path / "synthetic.json"
    generate_synthetic_iq(plan, expected, capture, metadata, seed=41)
    samples = np.fromfile(capture, dtype="<c8")
    rate = 100.0
    times = np.arange(samples.size, dtype=np.float64) / rate
    reference = times[-1] / 2.0
    drift_rate = 0.001
    samples *= np.exp(1j * np.pi * drift_rate * (times - reference) ** 2).astype(np.complex64)
    samples.astype("<c8").tofile(capture)
    metadata_document = json.loads(metadata.read_text(encoding="utf-8"))
    metadata_document["capture"] = _artifact(capture)
    _write(metadata, metadata_document)
    observations, gate = analyze_synthetic_iq(
        plan,
        expected,
        metadata,
        tmp_path / "drift-observations.json",
        tmp_path / "drift-gate.json",
        source_revision="d" * 40,
    )
    assert observations["analysis_outcome"] == "passed"
    model = observations["measurement_summary"]["shifted_frequency_model"]
    assert model is not None
    assert model["drift_hz_per_s"] == pytest.approx(drift_rate, abs=0.0002)
    assert gate["mode_gate"] == "passed"


@pytest.mark.parametrize(
    ("spacing_hz", "drift_hz_per_s", "expected_cause"),
    [(-7.5, 0.0, "tone_spacing"), (10.0, 0.0, "tone_spacing"), (-10.0, 0.05, "frequency_drift")],
)
def test_shifted_frequency_model_rejects_wrong_state_spacing_or_excessive_drift(
    tmp_path: Path, spacing_hz: float, drift_hz_per_s: float, expected_cause: str
) -> None:
    plan, expected, measured = _shifted_model_inputs(
        tmp_path, spacing_hz=spacing_hz, drift_hz_per_s=drift_hz_per_s
    )
    _, causes = _shifted_frequency_model(plan, expected, measured, 500.0)
    assert expected_cause in causes
    if spacing_hz > 0:
        assert "transition_direction" in causes


def test_shifted_frequency_model_fails_closed_without_both_states(tmp_path: Path) -> None:
    plan, expected, measured = _shifted_model_inputs(tmp_path)
    for event, observation in zip(expected["events"], measured, strict=True):
        if event["rf_state"] == "secondary":
            observation["measured_frequency_hz"] = None
    model, causes = _shifted_frequency_model(plan, expected, measured, 500.0)
    assert model is None
    assert causes == ["unresolvable_frequency_model", "wrong_frequency"]


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("reference_s", 0.0, "reference contradicts"),
        ("secondary_frequency_hz", 140000.0, "frequencies contradict"),
        ("maximum_drift_excursion_hz", 0.5, "drift contradicts"),
        ("maximum_residual_hz", 0.5, "residual contradicts"),
        ("correct_transition_count", 100000, "transition counts contradict"),
    ],
)
def test_contract_chain_rejects_tampered_shifted_frequency_summary(
    tmp_path: Path, field: str, replacement: float | int, message: str
) -> None:
    paths = _chain(tmp_path, "fskcw")
    plan, expected, observations, gate, _ = paths
    capture = tmp_path / "tamper-source.cf32"
    metadata = tmp_path / "tamper-source.json"
    generate_synthetic_iq(plan, expected, capture, metadata, seed=43)
    observations.unlink()
    gate.unlink()
    analyze_synthetic_iq(
        plan,
        expected,
        metadata,
        observations,
        gate,
        source_revision="d" * 40,
    )
    _refresh_chain(paths)
    document = json.loads(observations.read_text(encoding="utf-8"))
    document["measurement_summary"]["shifted_frequency_model"][field] = replacement
    _write(observations, document)
    _refresh_chain(paths)
    with pytest.raises(CwContractError, match=message):
        load_cw_contract_chain(*paths)


def test_synthetic_iq_interrupted_required_carrier_is_detected(tmp_path: Path) -> None:
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
        tmp_path / "synthetic-iq-observations.json",
        tmp_path / "synthetic-iq-gate.json",
        source_revision="d" * 40,
    )
    assert measured["analysis_outcome"] == "failed"
    assert "missing_carrier" in measured["failure_causes"]


@pytest.mark.parametrize(
    ("state", "continuous", "expected"),
    (
        ("primary", False, "missing_carrier"),
        ("secondary", None, "missing_carrier"),
        ("primary", True, "unresolved_frequency_transition"),
        ("secondary", True, "unresolved_frequency_transition"),
        ("off", None, "false_silence"),
    ),
)
def test_unmatched_event_classifies_presence_independently_from_transition_resolution(
    state: str, continuous: bool | None, expected: str
) -> None:
    assert _unmatched_event_cause(state, continuous) == expected


def test_acquired_replay_enforces_transition_limit_independently_of_timing_tolerance(
    tmp_path: Path,
) -> None:
    plan, expected, *_ = _chain(tmp_path, "fskcw")
    plan_document = json.loads(plan.read_text(encoding="utf-8"))
    plan_document["thresholds"]["timing_tolerance_s"] = 0.5
    plan_document["thresholds"]["maximum_transition_s"] = 0.2
    _write(plan, plan_document)
    expected_document = json.loads(expected.read_text(encoding="utf-8"))
    expected_document["plan"] = _artifact(plan)
    _write(expected, expected_document)
    capture = tmp_path / "synthetic.cf32"
    metadata = tmp_path / "synthetic.json"
    generate_synthetic_iq(plan, expected, capture, metadata, seed=29)
    adjacent_boundary = next(
        float(right["start_s"])
        for left, right in zip(
            expected_document["events"], expected_document["events"][1:], strict=False
        )
        if left["rf_state"] != "off"
        and right["rf_state"] != "off"
        and left["rf_state"] != right["rf_state"]
    )
    samples = np.fromfile(capture, dtype="<c8")
    center = round(adjacent_boundary * 100.0)
    samples[center - 15 : center + 15] = 0
    samples.astype("<c8").tofile(capture)
    metadata_document = json.loads(metadata.read_text(encoding="utf-8"))
    metadata_document["capture"] = _artifact(capture)
    _write(metadata, metadata_document)
    observations, _ = analyze_synthetic_iq(
        plan,
        expected,
        metadata,
        tmp_path / "transition-observations.json",
        tmp_path / "transition-gate.json",
        source_revision="e" * 40,
    )
    assert observations["analysis_outcome"] == "failed"
    assert "carrier_interruption" in observations["failure_causes"]
    assert any(item["carrier_continuous"] is False for item in observations["observations"])


def test_acquired_replay_clipping_blockage_precedes_transition_failure(tmp_path: Path) -> None:
    plan, expected, *_ = _chain(tmp_path, "fskcw")
    plan_document = json.loads(plan.read_text(encoding="utf-8"))
    plan_document["thresholds"]["timing_tolerance_s"] = 0.5
    _write(plan, plan_document)
    expected_document = json.loads(expected.read_text(encoding="utf-8"))
    expected_document["plan"] = _artifact(plan)
    _write(expected, expected_document)
    capture = tmp_path / "synthetic.cf32"
    metadata = tmp_path / "synthetic.json"
    generate_synthetic_iq(plan, expected, capture, metadata, seed=31)
    samples = np.fromfile(capture, dtype="<c8")
    samples *= np.complex64(2.2)
    samples[385:415] = 0
    samples.astype("<c8").tofile(capture)
    metadata_document = json.loads(metadata.read_text(encoding="utf-8"))
    metadata_document["capture"] = _artifact(capture)
    _write(metadata, metadata_document)
    observations, gate = analyze_synthetic_iq(
        plan,
        expected,
        metadata,
        tmp_path / "clipped-observations.json",
        tmp_path / "clipped-gate.json",
        source_revision="e" * 40,
    )
    assert observations["analysis_outcome"] == "blocked"
    assert observations["failure_causes"] == ["clipping"]
    assert gate["carrier_gate"] == "blocked"


@pytest.mark.parametrize("mode", ["tone", "cw", "qrss", "fskcw", "dfcw"])
def test_contract_chain_models_every_first_class_mode_without_qualifying(
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


@pytest.mark.parametrize("mode", ["tone", "cw", "qrss", "fskcw", "dfcw"])
def test_mock_lifecycle_models_every_mode_without_qualifying(tmp_path: Path, mode: str) -> None:
    paths = _chain(tmp_path, mode)
    output = tmp_path / "lifecycle.json"
    result = run_mock_lifecycle(*paths[:4], output)
    assert result["mode"] == mode
    assert result["lifecycle_gate"] == "passed"
    assert result["final_status"] == "inconclusive"
    assert result["qualification_claim"] is False
    assert result["supervisor"]["leak_verification"] == {"verified": True, "remaining": []}


@pytest.mark.parametrize("injection", sorted(INJECTIONS - {"none"}))
def test_mock_lifecycle_every_lifecycle_boundary_is_failure_injected(
    tmp_path: Path, injection: str
) -> None:
    paths = _chain(tmp_path, "cw")
    result = run_mock_lifecycle(*paths[:4], tmp_path / "lifecycle.json", injection=injection)
    assert result["qualification_claim"] is False
    if any(
        token in injection
        for token in ("stop_", "release_", "service_restore", "leak_verify", "quiescence")
    ) and not injection.endswith("_cancel"):
        assert result["final_status"] == "cleanup_failed"
    elif injection.endswith("_cancel"):
        assert result["final_status"] in {"aborted", "cleanup_failed"}
    else:
        assert result["final_status"] == "fixture_blocked"


def test_mock_lifecycle_cleanup_failure_overrides_passing_measurement(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "cw")
    gate = json.loads(paths[3].read_text(encoding="utf-8"))
    gate["carrier_gate"] = gate["mode_gate"] = "passed"
    _write(paths[3], gate)
    result = run_mock_lifecycle(
        *paths[:4], tmp_path / "lifecycle.json", injection="transmitter_stop_fail"
    )
    assert result["measurement"] == {"carrier_gate": "passed", "mode_gate": "passed"}
    assert result["final_status"] == "cleanup_failed"


def test_mock_lifecycle_rejects_tampering_positive_claim_and_unknown_injection(
    tmp_path: Path,
) -> None:
    paths = _chain(tmp_path, "cw")
    output = tmp_path / "lifecycle.json"
    run_mock_lifecycle(*paths[:4], output)
    document = json.loads(output.read_text(encoding="utf-8"))
    document["final_status"] = "cleanup_failed"
    _write(output, document)
    with pytest.raises(CwLifecycleError, match="final status"):
        validate_mock_lifecycle(output)
    output.unlink()
    with pytest.raises(CwLifecycleError, match="unsupported"):
        run_mock_lifecycle(*paths[:4], output, injection="execute-anything")


def test_mock_lifecycle_rejects_relabelled_injection_and_broken_upstream_chain(
    tmp_path: Path,
) -> None:
    paths = _chain(tmp_path, "cw")
    output = tmp_path / "lifecycle.json"
    run_mock_lifecycle(*paths[:4], output, injection="monitor_fail")
    document = json.loads(output.read_text(encoding="utf-8"))
    document["injection"] = "receiver_start_fail"
    _write(output, document)
    with pytest.raises(CwLifecycleError, match="declared mock injection"):
        validate_mock_lifecycle(output)

    output.unlink()
    observations = json.loads(paths[2].read_text(encoding="utf-8"))
    observations["expected_events"]["sha256"] = "0" * 64
    _write(paths[2], observations)
    with pytest.raises(CwLifecycleError, match="SHA-256"):
        run_mock_lifecycle(*paths[:4], output)


def test_mock_lifecycle_cli_create_and_validate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _chain(tmp_path, "tone")
    output = tmp_path / "lifecycle.json"
    assert main(["run-cw-mock-lifecycle", *(str(path) for path in paths[:4]), str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["qualification_claim"] is False
    assert main(["validate-cw-mock-lifecycle", str(output)]) == 0


def test_post_selected_plan_threshold_breaks_hash_chain(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "cw")
    plan = json.loads(paths[0].read_text(encoding="utf-8"))
    plan["thresholds"]["timing_tolerance_s"] = 99.0
    _write(paths[0], plan)
    with pytest.raises(CwContractError, match=r"size|SHA-256"):
        load_cw_contract_chain(*paths)


def test_tone_requires_explicit_leading_and_closing_quiet(tmp_path: Path) -> None:
    paths = _chain(tmp_path, "tone")
    plan = json.loads(paths[0].read_text(encoding="utf-8"))
    plan["protocol"]["pre_quiet_seconds"] = None
    plan["protocol"]["post_quiet_seconds"] = None
    _write(paths[0], plan)
    with pytest.raises(Exception, match=r"pre_quiet_seconds|post_quiet_seconds"):
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
