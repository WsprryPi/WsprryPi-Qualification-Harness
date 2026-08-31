"""TONE command latency is diagnostic; pulse duration and RF stop remain required."""

import copy
import json

import numpy as np
import pytest

from tests.unit.test_cw_contracts import _artifact, _chain, _write
from wsprrypi_qualification.carrier import (
    CarrierParameters,
    _aligned_tone_intervals,
    analyze_carrier,
)
from wsprrypi_qualification.cw_contracts import CwContractError, _validate_observations
from wsprrypi_qualification.cw_iq import analyze_synthetic_iq


def analyze(tmp_path, defect="clean", delays=(0.429, 0.143, 0.348)):
    plan_path, expected_path, *_ = _chain(tmp_path, "tone")
    plan = json.loads(plan_path.read_text())
    expected = json.loads(expected_path.read_text())
    rate = 8000
    count = 10 * rate
    plan["capture_contract"].update(
        sample_rate_hz=rate, center_frequency_hz=10000, sample_count=count
    )
    plan["protocol"]["primary_frequency_hz"] = 11000
    plan["thresholds"].update(
        timing_tolerance_s=0.15,
        maximum_alignment_shift_s=0.01,
        frequency_acquisition_half_width_hz=200,
    )
    for event in expected["events"]:
        if event["rf_state"] != "off":
            event["frequency_hz"] = 11000
    _write(plan_path, plan)
    expected["plan"] = _artifact(plan_path)
    _write(expected_path, expected)
    t = np.arange(count) / rate
    rng = np.random.default_rng(442)
    iq = 0.001 * (rng.normal(size=count) + 1j * rng.normal(size=count))
    off = iq.copy()
    carrier = 0.2 * np.exp(2j * np.pi * 1000 * t)
    events = [e for e in expected["events"] if e["rf_state"] != "off"]
    for i, event in enumerate(events):
        start, end = event["start_s"] + delays[i], event["end_s"] + delays[i]
        if defect == "missing" and i == 1:
            continue
        if defect == "long" and i == 1:
            end += 0.3
        if defect == "short" and i == 1:
            end -= 0.3
        if defect == "stuck" and i == 2:
            end = count / rate
        mask = (t >= start) & (t < end)
        if defect == "interrupted" and i == 1:
            mask &= ~((t >= start + 0.3) & (t < start + 0.65))
        iq[mask] += carrier[mask]
    if defect in {"extra", "noise", "noise_with_carrier"}:
        mask = (t >= 8) & (t < 9)
        if defect != "extra":
            iq[mask] += 0.04 * (rng.normal(size=np.sum(mask)) + 1j * rng.normal(size=np.sum(mask)))
        if defect != "noise":
            iq[mask] += carrier[mask]
    capture = tmp_path / "input.cf32"
    iq.astype("<c8").tofile(capture)
    off.astype("<c8").tofile(tmp_path / "off.cf32")
    metadata = tmp_path / "input.json"
    _write(
        metadata,
        {
            "schema_version": 1,
            "evidence_type": "cw_synthetic_capture",
            "run_id": plan["run_id"],
            "mode": "tone",
            "plan": _artifact(plan_path),
            "expected_events": _artifact(expected_path),
            "capture": _artifact(capture),
            "seed": 442,
            "overflow_count": 0,
            "synthetic": True,
        },
    )
    observed, _ = analyze_synthetic_iq(
        plan_path,
        expected_path,
        metadata,
        tmp_path / "duration-observed.json",
        tmp_path / "duration-gate.json",
        source_revision="e" * 40,
    )
    return plan, expected, observed, capture


@pytest.mark.parametrize(
    "delays", [(0.429, 0.143, 0.348), (1.0, 1.2, 0.9), (0.0, 0.6, 0.1), (2.1, 2.2, 2.3)]
)
def test_variable_command_delays_do_not_gate_tone(tmp_path, delays):
    plan, expected, observed, path = analyze(tmp_path, delays=delays)
    assert observed["analysis_outcome"] == "passed", observed["failure_causes"]
    assert observed["measurement_summary"]["timing_alignment"] is None
    assert observed["measurement_summary"]["tone_timing"][
        "command_start_offsets_s"
    ] == pytest.approx(delays, abs=0.01)
    intervals = _aligned_tone_intervals(path, plan, expected)
    assert len(intervals) == 3
    carrier = analyze_carrier(
        tmp_path / "off.cf32",
        path,
        CarrierParameters(
            8000,
            10000,
            11000,
            fft_size=2048,
            dc_exclusion_hz=100,
            temporal_on_intervals_s=intervals,
        ),
        tmp_path / "carrier.json",
    )
    assert carrier["gate_outcome"] == "passed"


@pytest.mark.parametrize(
    "defect", ["missing", "long", "short", "stuck", "interrupted", "extra", "noise_with_carrier"]
)
def test_real_tone_defects_still_prevent_pass(tmp_path, defect):
    _, _, observed, _ = analyze(tmp_path, defect)
    assert observed["analysis_outcome"] != "passed"


def test_full_length_broadband_burst_is_not_an_extra_tone(tmp_path):
    _, _, observed, _ = analyze(tmp_path, "noise")
    assert observed["analysis_outcome"] == "passed", observed["failure_causes"]


@pytest.mark.parametrize("defect", ["offset", "duration", "policy"])
def test_independent_tone_timing_evidence_is_authenticated(tmp_path, defect):
    plan, expected, observed, _ = analyze(tmp_path)
    forged = copy.deepcopy(observed)
    if defect == "offset":
        forged["measurement_summary"]["tone_timing"]["command_start_offsets_s"][0] += 0.5
    elif defect == "policy":
        forged["measurement_summary"]["tone_timing"]["policy"] = "ignore_everything"
    else:
        forged["observations"][1]["measured_end_s"] += 0.3
    with pytest.raises(CwContractError, match="TONE"):
        _validate_observations(plan, expected, forged, tmp_path / "forged.json")


def test_missing_tone_is_a_measurement_result_not_alignment_exception(tmp_path):
    plan, expected, _, path = analyze(tmp_path, "missing")
    intervals = _aligned_tone_intervals(path, plan, expected)
    assert intervals == []
    result = analyze_carrier(
        tmp_path / "off.cf32",
        path,
        CarrierParameters(
            8000,
            10000,
            11000,
            fft_size=2048,
            dc_exclusion_hz=100,
            temporal_on_intervals_s=intervals,
        ),
        tmp_path / "carrier.json",
    )
    assert result["gate_outcome"] != "passed"
