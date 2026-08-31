"""Independent carrier/noise discrimination and authenticated evidence tests."""

import copy
import json

import numpy as np
import pytest

from tests.unit.test_noise import analyze_case
from wsprrypi_qualification.noise import quiet_evidence
from wsprrypi_qualification.quiet_carrier import assess, measure


def evaluate(rate, kind, seed=92, secondary=None):
    rng = np.random.default_rng(seed)
    t = np.arange(rate) / rate
    values = 0.002 * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    region = (t >= 0.2) & (t < 0.8)
    if kind in {"broadband", "mixed", "impulses"}:
        mask = region if kind != "impulses" else region & ((np.arange(rate) % 25) < 5)
        values[mask] += 0.04 * (rng.normal(size=np.sum(mask)) + 1j * rng.normal(size=np.sum(mask)))
    if kind in {"carrier", "mixed", "repeated", "secondary", "tail", "boundary"}:
        mask = region
        if kind == "repeated":
            mask = region & ((t % 0.02) < max(0.002, 6 / rate))
        elif kind == "tail":
            mask = t >= 0.98
        elif kind == "boundary":
            mask = t < 0.02
        frequency = secondary if kind == "secondary" else 10100
        values[mask] += 0.2 * np.exp(2j * np.pi * (frequency - 10000) * t[mask])
    raw = quiet_evidence(values, rate, 10000, 10100, 0, rate, 8e-6, 10, dot_seconds=0.7)
    result = measure(
        raw, values, rate, 10000, 10100, 10100, secondary, 0.7, 10, 8e-6, timing_basis="dot_seconds"
    )
    return raw, result


@pytest.mark.parametrize("rate", [2000, 8000, 250000])
@pytest.mark.parametrize("seed", [92, 173, 491])
@pytest.mark.parametrize("kind", ["clean", "broadband", "impulses"])
def test_noise_does_not_fail_quiet(rate, seed, kind):
    raw, result = evaluate(rate, kind, seed)
    assert result["issues"] == []
    assert [{k: v for k, v in b.items() if k != "qualification_effect"} for b in raw["bursts"]] == [
        {k: v for k, v in b.items() if k != "qualification_effect"} for b in result["bursts"]
    ]
    assert all(b["qualification_effect"] == "diagnostic_only" for b in result["bursts"])
    if kind != "clean":
        assert raw["occupancy"] > 0.01


@pytest.mark.parametrize("rate", [2000, 8000, 250000])
@pytest.mark.parametrize("kind", ["carrier", "mixed", "repeated", "secondary", "tail", "boundary"])
def test_real_carrier_activity_survives_noise_and_boundaries(rate, kind):
    _, result = evaluate(rate, kind, secondary=10105)
    assert result["issues"] == ["false_silence"]


@pytest.mark.parametrize("mode", ["tone", "cw", "qrss", "fskcw", "dfcw"])
@pytest.mark.parametrize("defect", ["quiet_noise", "quiet_noise_carrier"])
def test_complete_analyzer_distinguishes_quiet_noise(tmp_path, mode, defect):
    result = analyze_case(tmp_path, mode, defect)
    assert (result["analysis_outcome"] == "passed") == (defect == "quiet_noise"), result[
        "failure_causes"
    ]


@pytest.mark.parametrize(
    "mutation", ["missing_window", "duplicate_window", "mask", "policy", "occupancy", "raw_effect"]
)
def test_carrier_assessment_tampering_rejected(tmp_path, mutation):
    from wsprrypi_qualification.cw_contracts import CwContractError, _validate_observations

    result = analyze_case(tmp_path, "qrss", "clean")
    quiet = result["measurement_summary"]["quiet_windows"][0]
    record = quiet["carrier_assessment"]
    if mutation == "missing_window":
        record["windows"].pop()
    elif mutation == "duplicate_window":
        record["windows"].append(copy.deepcopy(record["windows"][-1]))
    elif mutation == "mask":
        record["windows"][0]["carrier_present"] = True
    elif mutation == "policy":
        record["policy"]["minimum_contrast_db"] = 50
    elif mutation == "occupancy":
        record["maximum_rolling_occupancy"] = 0.75
    else:
        quiet["qualification_policy"]["occupancy_limit"] = 0.5
    with pytest.raises(CwContractError, match="quiet significance"):
        _validate_observations(
            json.loads((tmp_path / "plan.json").read_text()),
            json.loads((tmp_path / "expected.json").read_text()),
            result,
            tmp_path / "new-observations.json",
        )


def test_invalid_power_and_geometry_are_not_silent_passes():
    _, result = evaluate(8000, "clean")
    result["carrier_assessment"]["windows"][0]["target_powers"][0] = float("nan")
    with pytest.raises(ValueError, match="powers"):
        assess(result, 8000, 10000, 10100, 10100, None, 0.7, 10, 8e-6, timing_basis="dot_seconds")


@pytest.mark.parametrize("seed", [42, 157, 843])
def test_carrier_below_total_broadband_power_is_still_detected(seed):
    rate = 250000
    rng = np.random.default_rng(seed)
    t = np.arange(rate) / rate
    values = 0.04 * (rng.normal(size=rate) + 1j * rng.normal(size=rate))
    values += 0.02 * np.exp(2j * np.pi * 100 * t)
    raw = quiet_evidence(values, rate, 10000, 10100, 0, rate, 8e-6, 10, dot_seconds=0.7)
    result = measure(
        raw, values, rate, 10000, 10100, 10100, None, 0.7, 10, 8e-6, timing_basis="dot_seconds"
    )
    assert result["issues"] == ["false_silence"]


@pytest.mark.parametrize("rate", [8000, 250000])
def test_strong_reference_line_cannot_hide_unwanted_carrier(rate):
    rng = np.random.default_rng(942)
    t = np.arange(rate) / rate
    values = 0.002 * (rng.normal(size=rate) + 1j * rng.normal(size=rate))
    values += 0.02 * np.exp(2j * np.pi * 100 * t) + 0.2 * np.exp(2j * np.pi * 850 * t)
    raw = quiet_evidence(values, rate, 10000, 10100, 0, rate, 8e-6, 10, dot_seconds=0.7)
    result = measure(
        raw, values, rate, 10000, 10100, 10100, None, 0.7, 10, 8e-6, timing_basis="dot_seconds"
    )
    assert result["issues"] == ["ambiguous_quiet_carrier_interference"]


@pytest.mark.parametrize("case", ["geometry", "short"])
def test_unusable_carrier_window_is_inconclusive(case):
    rate = 8000
    size = 16 if case == "short" else rate
    values = np.full(size, 0.002 + 0j)
    raw = quiet_evidence(values, rate, 10000, 10100, 0, size, 8e-6, 10, dot_seconds=0.7)
    acquired = 14100 if case == "geometry" else 10100
    result = measure(
        raw,
        values,
        rate,
        10000,
        acquired,
        acquired,
        None,
        0.7,
        10,
        8e-6,
        timing_basis="dot_seconds",
    )
    assert result["issues"] == ["unusable_quiet_carrier_geometry"]


def test_long_low_rate_gaussian_quiet_does_not_promote_single_window_peaks():
    rate = 100
    rng = np.random.default_rng(71)
    samples = 0.002 * (rng.normal(size=100000) + 1j * rng.normal(size=100000))
    raw = quiet_evidence(samples, rate, 137500, 137490, 0, samples.size, 8e-6, 10, dot_seconds=1)
    result = measure(
        raw, samples, rate, 137500, 137490, 137500, 137490, 1, 10, 8e-6, timing_basis="dot_seconds"
    )
    assert result["issues"] == []
