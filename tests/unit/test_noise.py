"""Independent noise/edge truth, deliberately separate from reference encoders."""

import json
from pathlib import Path

import numpy as np
import pytest

from tests.unit.test_cw_contracts import _artifact, _chain, _write
from wsprrypi_qualification.carrier import CarrierParameters, analyze_carrier
from wsprrypi_qualification.cw_iq import analyze_synthetic_iq
from wsprrypi_qualification.noise import detect, quiet_evidence, runs


def waveform(rate: int, seed: int = 913) -> tuple[np.ndarray, np.ndarray]:
    t = np.arange(4 * rate) / rate
    rng = np.random.default_rng(seed)
    noise = 0.002 * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    signal = 0.2 * np.exp(2j * np.pi * 100 * t)
    return noise + signal * ((t >= 1) & (t < 1.7)), t


@pytest.mark.parametrize("rate", [1000, 8000, 250000])
@pytest.mark.parametrize("seed", [17, 61, 913])
def test_channel_edges_have_bounded_error_without_persistence_latency(rate: int, seed: int) -> None:
    samples, _ = waveform(rate, seed)
    _, active, evidence = detect(samples, rate, 10000, 10100, None, 300, 1, 10)
    assert evidence["issues"] == []
    found = runs(active)
    assert len(found) == 1
    assert (
        max(abs(found[0][0] / rate - 1), abs(found[0][1] / rate - 1.7))
        <= evidence["edge_uncertainty_s"]
    )
    assert evidence["edge_uncertainty_s"] < 0.025
    assert evidence["edges"][0]["confirmation_s"] > evidence["edges"][0]["onset_s"]


@pytest.mark.parametrize("duration", [0.000004, 0.0005, 0.005, 0.010, 0.020, 0.3])
def test_short_real_pulses_are_not_erased_by_edge_persistence(duration: float) -> None:
    rate = 250000
    samples, t = waveform(rate)
    begin = round(2.2 * rate)
    end = max(begin + 1, begin + round(duration * rate))
    samples[begin:end] += 0.2 * np.exp(2j * np.pi * 100 * t[begin:end])
    evidence = quiet_evidence(samples, rate, 10000, 10100, 2 * rate, 3 * rate, 8e-6, 10)
    assert evidence["issues"]
    assert evidence["bursts"]
    if duration >= 0.0005:
        assert "false_silence" in evidence["issues"]


@pytest.mark.parametrize("kind", ["zero", "tone", "step", "noise_only", "competitor"])
def test_uncertain_reference_or_acquisition_never_looks_like_clean_carrier(kind: str) -> None:
    rate = 8000
    samples, t = waveform(rate)
    if kind == "zero":
        samples[:rate] = 0
    elif kind == "tone":
        samples[:rate] += 0.1 * np.exp(2j * np.pi * 100 * t[:rate])
    elif kind == "step":
        samples[: rate // 2] *= 100
    elif kind == "noise_only":
        rng = np.random.default_rng(38)
        samples = 0.002 * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    else:
        samples += 0.2 * np.exp(2j * np.pi * 220 * t) * ((t >= 1) & (t < 1.7))
    _, active, evidence = detect(samples, rate, 10000, 10100, None, 300, 1, 10)
    assert evidence["issues"] or not np.any(active)


@pytest.mark.parametrize("width", [1, 4, 20, 80])
def test_impulse_does_not_displace_original_falling_edge(width: int) -> None:
    rate = 8000
    samples, _ = waveform(rate)
    samples[2 * rate : 2 * rate + width] += 0.5
    _, active, evidence = detect(samples, rate, 10000, 10100, None, 300, 1, 10)
    original = next((a, b) for a, b in runs(active) if a < rate * 1.1 and b > rate * 1.6)
    assert abs(original[1] / rate - 1.7) <= evidence["edge_uncertainty_s"]


def analyze_case(tmp_path: Path, mode: str, defect: str, edge_delta: float = 0.225) -> dict:
    plan_path, expected_path, *_ = _chain(tmp_path, mode)
    plan = json.loads(plan_path.read_text())
    rate = (
        8000
        if defect.startswith("external_reference") and defect != "external_reference_near_guard"
        else 2000
    )
    plan["capture_contract"]["sample_rate_hz"] = rate
    plan["capture_contract"]["center_frequency_hz"] = 137400
    plan["thresholds"]["frequency_acquisition_half_width_hz"] = 200
    plan["thresholds"]["timing_tolerance_s"] = 0.15
    # Preserve reference-encoder event data; independent oscillator and envelope
    # construction below do not call the production synthetic-IQ generator.
    expected = json.loads(expected_path.read_text())
    end = expected["events"][-1]["end_s"]
    count = int((end + 1) * rate)
    plan["capture_contract"]["sample_count"] = count
    _write(plan_path, plan)
    expected["plan"] = _artifact(plan_path)
    _write(expected_path, expected)
    rng = np.random.default_rng(801)
    t = np.arange(count) / rate
    samples = 0.002 * (rng.normal(size=count) + 1j * rng.normal(size=count))
    for event in expected["events"]:
        if event["rf_state"] == "off":
            continue
        start, stop = event["start_s"], event["end_s"]
        if defect == "late" and event["index"] == 1:
            stop += edge_delta
        if defect == "missing":
            continue
        frequency = event["frequency_hz"] - 137400
        if defect == "wrong_spacing" and event["rf_state"] == "secondary":
            frequency -= 8
        mask = (t >= start) & (t < stop)
        samples[mask] += 0.2 * np.exp(2j * np.pi * frequency * t[mask])
    if defect.startswith("external_reference"):
        ref_offset = -650 if defect == "external_reference_near_guard" else -2500
        samples += 0.5 * np.exp(2j * np.pi * ref_offset * t)
        if defect == "external_reference_missing":
            for event in expected["events"]:
                if event["rf_state"] != "off":
                    mask = (t >= event["start_s"]) & (t < event["end_s"])
                    frequency = event["frequency_hz"] - 137400
                    samples[mask] -= 0.2 * np.exp(2j * np.pi * frequency * t[mask])
        if defect == "external_reference_interrupted":
            event = next(e for e in expected["events"] if e["rf_state"] != "off")
            midpoint = (event["start_s"] + event["end_s"]) / 2
            mask = (t >= midpoint - 0.1) & (t < midpoint + 0.1)
            frequency = event["frequency_hz"] - 137400
            samples[mask] -= 0.2 * np.exp(2j * np.pi * frequency * t[mask])
        if defect == "external_reference_clipped":
            samples[::20] = 1.1
        if defect == "external_reference_stuck":
            mask = t >= expected["events"][-1]["start_s"] + 0.2
            samples[mask] += 0.2 * np.exp(2j * np.pi * 100 * t[mask])
    last_off = expected["events"][-1]["start_s"]
    if defect in {"extra_pulse", "tail_pulse", "brief_pulse"}:
        start = last_off + 0.3 if defect == "extra_pulse" else end + 0.3
        mask = (t >= start) & (t < start + (0.002 if defect == "brief_pulse" else 0.020))
        samples[mask] += 0.2 * np.exp(2j * np.pi * 100 * t[mask])
    if defect in {"quiet_noise", "quiet_noise_carrier"}:
        mask = (t >= end + 0.15) & (t < end + 0.85)
        samples[mask] += 0.04 * (rng.normal(size=np.sum(mask)) + 1j * rng.normal(size=np.sum(mask)))
        if defect == "quiet_noise_carrier":
            samples[mask] += 0.2 * np.exp(2j * np.pi * 100 * t[mask])
    if defect == "stuck":
        mask = t >= last_off
        samples[mask] += 0.2 * np.exp(2j * np.pi * 100 * t[mask])
    capture = tmp_path / "independent.cf32"
    samples.astype("<c8").tofile(capture)
    metadata = tmp_path / "independent.json"
    _write(
        metadata,
        {
            "schema_version": 1,
            "evidence_type": "cw_synthetic_capture",
            "run_id": plan["run_id"],
            "mode": mode,
            "plan": _artifact(plan_path),
            "expected_events": _artifact(expected_path),
            "capture": _artifact(capture),
            "seed": 801,
            "overflow_count": 0,
            "synthetic": True,
        },
    )
    observations, _ = analyze_synthetic_iq(
        plan_path,
        expected_path,
        metadata,
        tmp_path / "new-observations.json",
        tmp_path / "new-gate.json",
        source_revision="e" * 40,
    )
    return observations


@pytest.mark.parametrize("mode", ["tone", "qrss", "fskcw", "dfcw"])
@pytest.mark.parametrize(
    "defect", ["clean", "late", "missing", "extra_pulse", "tail_pulse", "stuck"]
)
def test_all_cw_modes_preserve_real_failures(tmp_path: Path, mode: str, defect: str) -> None:
    observations = analyze_case(tmp_path, mode, defect)
    if defect == "clean":
        assert observations["analysis_outcome"] == "passed", observations["failure_causes"]
    else:
        assert observations["analysis_outcome"] != "passed"
    if defect == "late":
        assert "timing_error" in observations["failure_causes"]
    if defect in {"extra_pulse", "tail_pulse"}:
        assert "false_silence" in observations["failure_causes"]


@pytest.mark.parametrize("mode", ["fskcw", "dfcw"])
def test_shifted_noise_filter_cannot_force_commanded_spacing(tmp_path: Path, mode: str) -> None:
    obs = analyze_case(tmp_path, mode, "wrong_spacing")
    assert obs["analysis_outcome"] != "passed"
    assert "tone_spacing" in obs["failure_causes"]


@pytest.mark.parametrize("defect", ["impulse", "dropout", "noise", "clean", "remote_interferer"])
def test_tone_and_wspr_gate_require_sustained_local_carrier(tmp_path: Path, defect: str) -> None:
    rate, size = 8000, 4096
    t = np.arange(size * 2 + 200) / rate
    rng = np.random.default_rng(302)
    off = 0.001 * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    on = off.copy()
    if defect != "noise":
        on += 0.2 * np.exp(2j * np.pi * 1000 * t)
    if defect == "impulse":
        on = off.copy()
        on[size // 2] += 0.7
    elif defect == "dropout":
        on[size : size + 400] = off[size : size + 400]
    elif defect == "remote_interferer":
        on += 0.5 * np.exp(2j * np.pi * 2700 * t)
    for name, samples in [("off", off), ("on", on)]:
        samples.astype("<c8").tofile(tmp_path / f"{name}.cf32")
    result = analyze_carrier(
        tmp_path / "off.cf32",
        tmp_path / "on.cf32",
        CarrierParameters(
            rate,
            10000,
            11000,
            fft_size=size,
            dc_exclusion_hz=100,
            relative_acquisition_offset_gate_hz=200,
        ),
        tmp_path / "result.json",
    )
    assert (result["gate_outcome"] == "passed") == (defect in {"clean", "remote_interferer"})


@pytest.mark.parametrize("seed", range(40, 60))
def test_held_out_noise_has_no_confirmed_message_edges(seed: int) -> None:
    rng = np.random.default_rng(seed)
    rate = 8000
    values = 0.002 * (rng.normal(size=rate * 3) + 1j * rng.normal(size=rate * 3))
    _, active, _ = detect(values, rate, 10000, 10100, None, 300, 1, 10)
    assert not np.any(active)


@pytest.mark.parametrize("amplitude", [0.012, 0.03, 0.2, 0.7])
@pytest.mark.parametrize("drift", [0.0, 0.5, -0.5])
def test_held_out_weak_fading_and_drift_edges(amplitude: float, drift: float) -> None:
    rate = 8000
    _, t = waveform(rate)
    rng = np.random.default_rng(7812)
    values = 0.002 * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    mask = (t >= 1) & (t < 1.7)
    fading = 0.8 + 0.2 * np.cos(2 * np.pi * t)
    values += amplitude * fading * np.exp(2j * np.pi * (100 * t + drift * t * t / 2)) * mask
    _, active, evidence = detect(values, rate, 10000, 10100, None, 300, 1, 10)
    found = runs(active)
    assert len(found) == 1
    assert (
        max(abs(found[0][0] / rate - 1), abs(found[0][1] / rate - 1.7))
        <= evidence["edge_uncertainty_s"]
    )


@pytest.mark.parametrize("gap", [0.001, 0.003, 0.009, 0.015])
def test_extra_pulse_next_to_edge_is_not_hidden_by_filter_guard(tmp_path: Path, gap: float) -> None:
    from wsprrypi_qualification.noise import raw_quiet_bounds

    rate = 8000
    values, t = waveform(rate)
    values += 0.2 * np.exp(2j * np.pi * 100 * t) * ((t >= 1.7 + gap) & (t < 1.7 + gap + 0.005))
    _, active, evidence = detect(values, rate, 10000, 10100, None, 300, 1, 10)
    edge = runs(active)[0][1]
    start, end = raw_quiet_bounds(
        values,
        rate,
        edge,
        len(values),
        round(evidence["edge_uncertainty_s"] * rate),
        evidence["confirmation_seconds"],
        evidence["raw_noise_power"],
        10,
        False,
        True,
    )
    quiet = quiet_evidence(values, rate, 10000, 10100, start, end, evidence["raw_noise_power"], 10)
    assert quiet["issues"]


def test_tone_cadence_uses_explicit_on_intervals(tmp_path: Path) -> None:
    rate, size = 8000, 4096
    t = np.arange(rate * 3) / rate
    rng = np.random.default_rng(67)
    off = 0.001 * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    on = off + 0.2 * np.exp(2j * np.pi * 1000 * t) * ((t >= 1) & (t < 2))
    off.astype("<c8").tofile(tmp_path / "off.cf32")
    on.astype("<c8").tofile(tmp_path / "on.cf32")
    params = CarrierParameters(
        rate,
        10000,
        11000,
        fft_size=size,
        dc_exclusion_hz=100,
        relative_acquisition_offset_gate_hz=200,
        temporal_on_intervals_s=[[1.15, 1.85]],
    )
    result = analyze_carrier(
        tmp_path / "off.cf32", tmp_path / "on.cf32", params, tmp_path / "result.json"
    )
    assert result["gate_outcome"] == "passed"
    assert result["metrics"]["noise_guard"]["includes_fft_tail"] is False


def test_version_eight_noise_budget_tampering_is_rejected(tmp_path: Path) -> None:
    from wsprrypi_qualification.cw_contracts import CwContractError, _validate_observations

    obs = analyze_case(tmp_path, "qrss", "clean")
    obs["analyzer"]["time_resolution_s"] = 1e-9
    with pytest.raises(CwContractError, match="timing budget"):
        _validate_observations(
            json.loads((tmp_path / "plan.json").read_text()),
            json.loads((tmp_path / "expected.json").read_text()),
            obs,
            tmp_path / "new-observations.json",
        )


@pytest.mark.parametrize(
    "delta,outcome",
    [
        (0.13, "passed"),
        (0.149, "inconclusive"),
        (0.151, "inconclusive"),
        (0.225, "failed"),
        (-0.13, "passed"),
        (-0.149, "inconclusive"),
        (-0.151, "inconclusive"),
        (-0.225, "failed"),
    ],
)
def test_timing_limit_is_not_widened_near_150ms(tmp_path: Path, delta: float, outcome: str) -> None:
    obs = analyze_case(tmp_path, "qrss", "late", edge_delta=delta)
    assert obs["analysis_outcome"] == outcome, obs["failure_causes"]
    if outcome == "inconclusive":
        assert "timing_boundary_uncertain" in obs["failure_causes"]


@pytest.mark.parametrize("noise_amplitude", [0.0, 0.02])
def test_wspr_audio_preserves_all_four_symbol_frequencies(noise_amplitude: float) -> None:
    from wsprrypi_qualification.audio import AudioParameters, _resample_mixed

    rate = 48000
    samples_per_symbol = 32768
    t = np.arange(samples_per_symbol * 4) / rate
    symbols = np.repeat(np.arange(4), samples_per_symbol)
    spacing = 12000 / 8192
    rng = np.random.default_rng(528)
    iq = 0.2 * np.exp(2j * np.pi * (1000 + symbols * spacing) * t)
    iq += noise_amplitude * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    parameters = AudioParameters(rate, 10000, 11000)
    # This is independent four-frequency symbol truth, not a valid encoded WSPR
    # message and not evidence of decoder sensitivity or correct identity.
    for symbol in range(4):
        audio = _resample_mixed(iq, symbol * samples_per_symbol, samples_per_symbol, parameters)
        spectrum = abs(np.fft.rfft(audio)) ** 2
        assert int(np.argmax(spectrum)) == 1024 + symbol


def test_comparable_carrier_candidates_are_inconclusive(tmp_path: Path) -> None:
    rate, size = 8000, 4096
    t = np.arange(size * 2) / rate
    rng = np.random.default_rng(22)
    off = 0.001 * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    on = off + 0.2 * np.exp(2j * np.pi * 1000 * t) + 0.2 * np.exp(2j * np.pi * 1100 * t)
    off.astype("<c8").tofile(tmp_path / "off.cf32")
    on.astype("<c8").tofile(tmp_path / "on.cf32")
    result = analyze_carrier(
        tmp_path / "off.cf32",
        tmp_path / "on.cf32",
        CarrierParameters(
            rate,
            10000,
            11000,
            fft_size=size,
            dc_exclusion_hz=100,
            relative_acquisition_offset_gate_hz=200,
        ),
        tmp_path / "result.json",
    )
    assert result["gate_outcome"] == "inconclusive"
    assert result["metrics"]["noise_guard"]["ambiguous_in_window_feature"] is True


def test_insufficient_low_rate_timing_evidence_is_inconclusive(tmp_path: Path) -> None:
    from tests.unit.test_cw_contracts import _acquired_inputs
    from wsprrypi_qualification.cw_replay import compose_acquired_replay

    plan, expected, metadata = _acquired_inputs(tmp_path, "dfcw")
    result = compose_acquired_replay(
        plan, expected, metadata, tmp_path / "replay", source_revision="e" * 40
    )
    assert result["measurement"]["carrier_gate"] == "inconclusive"
    assert "excessive_timing_uncertainty" in result["failure_causes"]
    assert result["qualification_claim"] is False


@pytest.mark.parametrize("defect", ["reference", "timing", "tone_interior"])
def test_unsupported_detector_plans_reject_before_live_access(tmp_path: Path, defect: str) -> None:
    from wsprrypi_qualification.noise import validate_live_detector_plan

    plan_path, *_ = _chain(tmp_path, "tone")
    plan = json.loads(plan_path.read_text())
    if defect == "reference":
        plan["protocol"]["pre_quiet_seconds"] = 0.01
    elif defect == "timing":
        plan["thresholds"]["timing_tolerance_s"] = 0.000001
    else:
        plan["protocol"]["tone_on_seconds"] = 0.1
    with pytest.raises(ValueError):
        validate_live_detector_plan(plan)


def test_confirmation_wait_is_included_in_edge_uncertainty() -> None:
    # Held-out seed 1049 exposed an early noise crossing preceding the sustained
    # carrier. The actual confirmation wait must be budgeted, not just FIR delay.
    rate = 8000
    t = np.arange(rate * 3) / rate
    rng = np.random.default_rng(1049)
    samples = 0.002 * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    samples += 0.02 * np.exp(2j * np.pi * (100 * t + 0.25 * t * t)) * ((t >= 1) & (t < 1.7))
    _, active, d = detect(samples, rate, 10000, 10100, None, 300, 1, 10)
    run = runs(active)[0]
    error = max(abs(run[0] / rate - 1), abs(run[1] / rate - 1.7))
    assert error <= d["edge_uncertainty_s"] + 1e-12
    assert d["edge_uncertainty_s"] > d["filter_support_seconds"] + 1 / rate


@pytest.mark.parametrize("common_offset", [-50.0, 0.0, 50.0])
def test_separated_shifted_states_acquire_inside_either_authenticated_window(
    common_offset: float,
) -> None:
    rate = 4000
    t = np.arange(rate * 4) / rate
    rng = np.random.default_rng(32)
    samples = 0.002 * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    samples += 0.2 * np.exp(2j * np.pi * (100 + common_offset) * t) * ((t >= 1) & (t < 2))
    samples += 0.2 * np.exp(2j * np.pi * (600 + common_offset) * t) * ((t >= 2) & (t < 3))
    _, active, d = detect(samples, rate, 10000, 10100, 10600, 50, 1, 10)
    assert d["issues"] == []
    assert min(abs(d["acquired_frequency_hz"] - f) for f in [10100, 10600]) <= 50
    assert np.all(active[int(1.1 * rate) : int(2.9 * rate)])


@pytest.mark.parametrize("shift", [-0.25, 0.0, 0.194, 0.6, 0.9])
def test_tone_temporal_interiors_follow_detected_pulses(tmp_path: Path, shift: float):
    from wsprrypi_qualification.carrier import _aligned_tone_intervals, _temporal_carrier_guard

    rate = 8000
    t = np.arange(14 * rate) / rate
    rng = np.random.default_rng(492)
    values = 0.001 * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    for start in (2, 6, 10):
        values += (
            0.2 * np.exp(2j * np.pi * 1000 * t) * ((t >= start + shift) & (t < start + 2 + shift))
        )
    path = tmp_path / "tone.cf32"
    values.astype("<c8").tofile(path)
    plan = {
        "mode": "tone",
        "capture_contract": {"sample_rate_hz": rate, "center_frequency_hz": 10000},
        "protocol": {"primary_frequency_hz": 11000, "pre_quiet_seconds": 2, "tone_on_seconds": 2},
        "thresholds": {
            "timing_tolerance_s": 0.15,
            "maximum_alignment_shift_s": 0.75,
            "frequency_acquisition_half_width_hz": 200,
            "minimum_contrast_db": 10,
        },
    }
    expected = {"events": [{"start_s": s, "end_s": s + 2, "rf_state": "on"} for s in (2, 6, 10)]}
    intervals = _aligned_tone_intervals(path, plan, expected)
    assert intervals[0][0] == pytest.approx(2 + shift + 0.15, abs=0.01)
    params = CarrierParameters(rate, 10000, 11000, temporal_on_intervals_s=intervals)
    assert _temporal_carrier_guard(path, params, 11000)["below_contrast_window_count"] == 0
    # A real interior dropout must not disappear through independently fitted edges.
    values[(t >= 6.8 + shift) & (t < 6.85 + shift)] = 0
    values.astype("<c8").tofile(path)
    assert _temporal_carrier_guard(path, params, 11000)["below_contrast_window_count"] > 0
    assert _aligned_tone_intervals(path, plan, expected) == []


@pytest.mark.parametrize("bound", [1.0, 1.1])
@pytest.mark.parametrize(
    "case", ["delayed", "immediate", "late", "absent", "dropout", "early_dropout", "tail", "short"]
)
def test_bounded_carrier_startup_never_reacquires_or_trims_tail(
    tmp_path: Path, case: str, bound: float
):
    rate = 8000
    t = np.arange(int((1.3 if case == "short" else 3) * rate)) / rate
    rng = np.random.default_rng(807)
    off = 0.001 * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    start = 0 if case in {"immediate", "early_dropout"} else 1.2 if case == "late" else 0.8
    active = t >= start
    if case == "absent":
        active[:] = False
    if case == "dropout":
        active &= ~((t >= 1.4) & (t < 1.5))
    if case == "early_dropout":
        active &= ~((t >= 0.2) & (t < 0.3))
    if case == "tail":
        active &= t < 2.8
    on = off + 0.2 * np.exp(2j * np.pi * 1000 * t) * active
    off.astype("<c8").tofile(tmp_path / "off.cf32")
    on.astype("<c8").tofile(tmp_path / "on.cf32")
    params = CarrierParameters(
        rate,
        10000,
        11000,
        fft_size=2048,
        dc_exclusion_hz=100,
        relative_acquisition_offset_gate_hz=200,
        startup_acquisition_max_s=bound,
    )
    result = analyze_carrier(
        tmp_path / "off.cf32", tmp_path / "on.cf32", params, tmp_path / "result.json"
    )
    guard = result["metrics"]["noise_guard"]
    assert result["gate_outcome"] == (
        "passed" if case in {"delayed", "immediate"} else "inconclusive"
    )
    assert guard["version"] == 2
    assert guard["includes_fft_tail"] is True
    if case == "delayed":
        assert guard["startup_acquired"] is True
        assert guard["startup_excluded_windows"] == 40
        assert guard["below_contrast_window_count"] == 0
    if case in {"dropout", "early_dropout", "tail"}:
        assert guard["startup_acquired"] is True
        assert guard["below_contrast_window_count"] > 0
    if case in {"late", "absent", "short"}:
        assert guard["startup_acquired"] is False


@pytest.mark.parametrize("bound", [-1, 1.1001, float("nan"), float("inf")])
def test_carrier_startup_bound_rejected_before_iq_access(tmp_path: Path, bound: float):
    from wsprrypi_qualification.offline import OfflineAnalysisError

    with pytest.raises(OfflineAnalysisError, match="startup acquisition"):
        analyze_carrier(
            tmp_path / "missing",
            tmp_path / "missing",
            CarrierParameters(8000, 10000, 11000, startup_acquisition_max_s=bound),
            tmp_path / "result.json",
        )


def test_startup_acquisition_cannot_mask_tone_cadence(tmp_path: Path):
    from wsprrypi_qualification.offline import OfflineAnalysisError

    with pytest.raises(OfflineAnalysisError, match="continuous input"):
        analyze_carrier(
            tmp_path / "missing",
            tmp_path / "missing",
            CarrierParameters(
                8000, 10000, 11000, temporal_on_intervals_s=[[1, 2]], startup_acquisition_max_s=1
            ),
            tmp_path / "result.json",
        )


@pytest.mark.parametrize(
    "duration, material", [(16e-6, False), (0.001, False), (0.010, True), (0.1, True)]
)
def test_slow_cw_retains_short_events_without_failing_silence(duration, material):
    rate = 250000
    samples, t = waveform(rate)
    begin = round(2.2 * rate)
    samples[begin : begin + round(duration * rate)] += 0.2 * np.exp(
        2j * np.pi * 100 * t[begin : begin + round(duration * rate)]
    )
    strict = quiet_evidence(samples, rate, 10000, 10100, 2 * rate, 3 * rate, 8e-6, 10)
    slow = quiet_evidence(
        samples, rate, 10000, 10100, 2 * rate, 3 * rate, 8e-6, 10, dot_seconds=0.7
    )
    assert slow["bursts"]
    assert [
        {k: v for k, v in b.items() if k != "qualification_effect"} for b in slow["bursts"]
    ] == strict["bursts"]
    assert slow["occupancy"] == strict["occupancy"]
    assert bool(slow["issues"]) is material
    if not material:
        assert all(b["qualification_effect"] == "diagnostic_only" for b in slow["bursts"])


def test_repeated_short_events_accumulate_across_fixed_window_boundaries():
    from wsprrypi_qualification.noise import assess_quiet_significance

    rate = 250000
    bursts = []
    for i in range(10):
        start = 0.691 + 0.002 * i
        bursts.append(
            {
                "start_s": start,
                "end_s": start + 0.001,
                "duration_s": 0.001,
                "classification": "coherent_in_band",
            }
        )
    evidence = {
        "start_s": 0.0,
        "end_s": 1.4,
        "occupancy": 0.010 / 1.4,
        "bursts": bursts,
        "issues": [],
    }
    assessed = assess_quiet_significance(evidence, rate, 0.7)
    assert assessed["issues"] == ["ambiguous_quiet_contamination"]
    assert assessed["qualification_assessment"]["maximum_rolling_occupancy"] > 0.01
    assert all(b["qualification_effect"] == "diagnostic_only" for b in assessed["bursts"])


@pytest.mark.parametrize("mode", ["qrss", "tone"])
def test_slow_quiet_policy_tampering_is_rejected(tmp_path: Path, mode: str):
    from wsprrypi_qualification.cw_contracts import CwContractError, _validate_observations

    obs = analyze_case(tmp_path, mode, "clean")
    obs["measurement_summary"]["quiet_windows"][0]["qualification_policy"]["occupancy_limit"] = 0.5
    with pytest.raises(CwContractError, match="quiet significance"):
        _validate_observations(
            json.loads((tmp_path / "plan.json").read_text()),
            json.loads((tmp_path / "expected.json").read_text()),
            obs,
            tmp_path / "new-observations.json",
        )


@pytest.mark.parametrize("rate", [1000, 8000, 250000])
@pytest.mark.parametrize("duration, expected", [(0.006, []), (0.007, ["false_silence"])])
def test_significance_boundary_tracks_dot_duration_in_samples(rate, duration, expected):
    from wsprrypi_qualification.noise import assess_quiet_significance

    evidence = {
        "start_s": 0.0,
        "end_s": 0.7,
        "occupancy": duration / 0.7,
        "issues": [],
        "bursts": [
            {
                "start_s": 0.1,
                "end_s": 0.1 + duration,
                "duration_s": duration,
                "classification": "coherent_in_band",
            }
        ],
    }
    result = assess_quiet_significance(evidence, rate, 0.7)
    assert result["issues"] == expected


@pytest.mark.parametrize("bound", [1.0, 1.1])
@pytest.mark.parametrize("onset", [0.90, 0.94, 1.0, 1.02])
def test_carrier_confirmation_deadline_boundary(tmp_path: Path, bound: float, onset: float):
    rate = 8000
    t = np.arange(3 * rate) / rate
    rng = np.random.default_rng(219)
    off = 0.001 * (rng.normal(size=t.size) + 1j * rng.normal(size=t.size))
    on = off + 0.2 * np.exp(2j * np.pi * 1000 * t) * (t >= onset)
    off.astype("<c8").tofile(tmp_path / "off.cf32")
    on.astype("<c8").tofile(tmp_path / "on.cf32")
    result = analyze_carrier(
        tmp_path / "off.cf32",
        tmp_path / "on.cf32",
        CarrierParameters(
            rate,
            10000,
            11000,
            fft_size=2048,
            dc_exclusion_hz=100,
            relative_acquisition_offset_gate_hz=200,
            startup_acquisition_max_s=bound,
        ),
        tmp_path / "result.json",
    )
    passed = onset + 0.1 <= bound + 1e-12
    assert result["gate_outcome"] == ("passed" if passed else "inconclusive")
    guard = result["metrics"]["noise_guard"]
    assert guard["startup_acquired"] is passed
    assert result["contract"]["startup_acquisition_max_s"] == bound
    if passed:
        assert guard["startup_excluded_windows"] == round(onset / 0.02)
        assert guard["below_contrast_window_count"] == 0


def test_tone_retains_brief_off_period_event_without_failing(tmp_path: Path):
    obs = analyze_case(tmp_path, "tone", "brief_pulse")
    assert obs["analyzer"]["version"] == "13"
    assert obs["analysis_outcome"] == "passed"
    quiet = obs["measurement_summary"]["quiet_windows"]
    bursts = [b for q in quiet for b in q["bursts"]]
    assert bursts
    assert all(b["qualification_effect"] == "diagnostic_only" for b in bursts)
    for q in quiet:
        assert q["qualification_policy"]["name"] == "tone_quiet_significance"
        assert "tone_on_seconds" in q["qualification_policy"]
        assert "dot_seconds" not in q["qualification_policy"]


@pytest.mark.parametrize("duration, issues", [(0.009, []), (0.01, ["false_silence"])])
def test_tone_material_boundary_and_accumulation(duration, issues):
    from wsprrypi_qualification.noise import assess_quiet_significance

    rate = 250000
    burst = {
        "start_s": 0.1,
        "end_s": 0.1 + duration,
        "duration_s": duration,
        "classification": "coherent_in_band",
    }
    evidence = {
        "start_s": 0,
        "end_s": 2,
        "occupancy": duration / 2,
        "bursts": [burst],
        "issues": [],
    }
    result = assess_quiet_significance(evidence, rate, 2, timing_basis="tone_on_seconds")
    assert result["issues"] == issues
    assert result["qualification_policy"]["material_samples"] == 2500
    evidence["bursts"] = [
        {**burst, "start_s": 0.05 + i * 0.1, "end_s": 0.05 + i * 0.1 + 0.001, "duration_s": 0.001}
        for i in range(20)
    ]
    evidence["occupancy"] = 0.01
    result = assess_quiet_significance(evidence, rate, 2, timing_basis="tone_on_seconds")
    assert result["issues"] == ["ambiguous_quiet_contamination"]
    assert result["qualification_assessment"]["significant_burst_count"] == 0


@pytest.mark.parametrize("mode", ["tone", "qrss", "fskcw", "dfcw"])
@pytest.mark.parametrize(
    "defect",
    [
        "external_reference",
        "external_reference_missing",
        "external_reference_stuck",
        "external_reference_interrupted",
        "external_reference_clipped",
        "external_reference_near_guard",
    ],
)
def test_external_reference_never_substitutes_for_transmitter(
    tmp_path: Path, mode: str, defect: str
):
    obs = analyze_case(tmp_path, mode, defect)
    assert (obs["analysis_outcome"] == "passed") == (defect == "external_reference")
    assert obs["measurement_summary"]["continuity_power_domain"] == "filtered_carrier_channel"
