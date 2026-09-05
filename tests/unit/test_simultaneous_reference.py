import json
from pathlib import Path

import numpy as np
import pytest

from tests.unit.test_capture_metadata import capture_document
from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.simultaneous_reference import analyze, compose, validate


def fixture(tmp_path: Path, defect: str = "", offset: float = 12):
    rate = 16000
    t = np.arange(rate * 4) / rate
    rng = np.random.default_rng(172)
    x = 0.0002 * (rng.normal(size=len(t)) + 1j * rng.normal(size=len(t)))
    signal_mask = (t >= 1) & (t < 3)
    if defect != "missing_signal":
        x += 0.05 * np.exp(2j * np.pi * (2000 + offset) * t) * signal_mask
    reference_mask = np.ones(len(t))
    if defect == "short_dropout":
        reference_mask[(t >= 2.3) & (t < 2.38)] = 0
    if defect == "dropout":
        reference_mask[(t >= 2) & (t < 3)] = 0
    if defect != "missing_reference":
        phase = (5000 + offset) * t + (2 * t**2 if defect == "drift" else 0)
        x += 0.2 * np.exp(2j * np.pi * phase) * reference_mask
    if defect == "ambiguous":
        x += 0.2 * np.exp(2j * np.pi * 5032 * t)
    if defect == "clipping":
        x[100] = 1.1
    capture = tmp_path / "capture with spaces.cf32"
    x.astype("<c8").tofile(capture)
    metadata = capture_document()
    for name in ("actual_settings", "requested_settings"):
        metadata[name].update(sample_rate_hz=rate, bandwidth_hz=rate, center_frequency_hz=100000)
    metadata.update(requested_sample_count=len(x), retained_sample_count=len(x))
    metadata["output"].update(artifact(capture))
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps(metadata))
    request = {
        "signal_frequency_hz": 102000,
        "reference_frequency_hz": 105000,
        "channel_half_width_hz": 100,
        "window_seconds": 1,
        "minimum_contrast_db": 10,
        "maximum_reference_excursion_hz": 2,
        "reference_uncertainty_hz": 0.1,
        "transfer_uncertainty_hz": 0.2,
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request))
    return metadata_path, capture, request_path


def test_common_receiver_offset_removed_without_transmitter_change(tmp_path):
    args = fixture(tmp_path)
    report = compose(*args, tmp_path / "report.json")
    assert report["outcome"] == "usable_diagnostic"
    assert report["qualification_claim"] is False
    for window in report["windows"][1:3]:
        assert window["corrected_signal_frequency_hz"] == pytest.approx(102000, abs=0.02)
        assert window["signal_minus_reference_db"] == pytest.approx(-12.0412, abs=0.03)
        assert window["frequency_error_budget_hz"] == pytest.approx(2.3)
    assert report["windows"][0]["corrected_signal_frequency_hz"] is None
    assert validate(tmp_path / "report.json") == report


@pytest.mark.parametrize(
    "defect",
    ["dropout", "short_dropout", "missing_reference", "drift", "ambiguous", "missing_signal"],
)
def test_bad_reference_or_absent_signal_never_usable(tmp_path, defect):
    report = analyze(*fixture(tmp_path, defect))
    assert report["outcome"] == "inconclusive"
    assert all(w["corrected_signal_frequency_hz"] is None for w in report["windows"])


def test_actual_clipping_rejected_even_if_metadata_claims_none(tmp_path):
    with pytest.raises(ValueError, match="clipped"):
        analyze(*fixture(tmp_path, "clipping"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference_frequency_hz", 102100),
        ("signal_frequency_hz", 100000),
        ("window_seconds", float("nan")),
        ("reference_uncertainty_hz", -1),
    ],
)
def test_invalid_request(tmp_path, field, value):
    args = fixture(tmp_path)
    p = args[2]
    d = json.loads(p.read_text())
    d[field] = value
    p.write_text(json.dumps(d))
    with pytest.raises((ValueError, RuntimeError)):
        analyze(*args)


def test_tampered_report_and_input_rejected(tmp_path):
    args = fixture(tmp_path)
    p = tmp_path / "report.json"
    d = compose(*args, p)
    d["windows"][1]["corrected_signal_frequency_hz"] += 1
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="recomputation"):
        validate(p)
    args[1].write_bytes(b"bad")
    with pytest.raises(ValueError, match="artifact changed"):
        validate(p)


@pytest.mark.parametrize("field", ["overflow_count", "timeout_count"])
def test_capture_loss_rejected(tmp_path, field):
    args = fixture(tmp_path)
    d = json.loads(args[0].read_text())
    d[field] = 1
    args[0].write_text(json.dumps(d))
    with pytest.raises(ValueError):
        analyze(*args)


def test_cli_and_no_overwrite(tmp_path, capsys):
    from wsprrypi_qualification.cli import main

    args = fixture(tmp_path)
    output = tmp_path / "report.json"
    assert main(["analyze-simultaneous-reference", *map(str, args), str(output)]) == 0
    assert main(["validate-simultaneous-reference", str(output)]) == 0
    assert main(["analyze-simultaneous-reference", *map(str, args), str(output)]) == 2


def test_channel_noise_bandwidth_independently_matches_convolution():
    from wsprrypi_qualification.noise import channel_referred_noise

    for n in (1, 3, 5, 31, 501):
        h = np.ones(n) / n
        impulse = np.convolve(np.convolve(h, h), h)
        assert channel_referred_noise(
            {"boxcar_samples": n, "channel_noise_power": 1.0}
        ) == pytest.approx(1 / np.sum(impulse**2))


@pytest.mark.parametrize("offset", [-25.25, 12.25])
def test_reference_correction_sign_and_fractional_bins(tmp_path, offset):
    report = analyze(*fixture(tmp_path, offset=offset))
    assert report["outcome"] == "usable_diagnostic"
    for window in report["windows"][1:3]:
        assert window["corrected_signal_frequency_hz"] == pytest.approx(102000, abs=0.02)
        assert window["reference"]["indicated_frequency_hz"] == pytest.approx(
            105000 + offset, abs=0.05
        )
