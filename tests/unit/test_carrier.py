import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from wsprrypi_qualification.carrier import CarrierParameters, analyze_carrier
from wsprrypi_qualification.cf32 import inspect_cf32
from wsprrypi_qualification.offline import OfflineAnalysisError


def write_cf32(path: Path, values: np.ndarray) -> Path:
    np.asarray(values, dtype="<c8").tofile(path)
    return path


def tone(count: int, rate: int, offset: float, amplitude: float = 0.5) -> np.ndarray:
    n = np.arange(count)
    return amplitude * np.exp(2j * np.pi * offset * n / rate)


def test_cf32_rejects_malformed_nonfinite_and_clipped(tmp_path: Path) -> None:
    malformed = tmp_path / "bad.cf32"
    malformed.write_bytes(b"123")
    with pytest.raises(OfflineAnalysisError, match="multiple of 8"):
        inspect_cf32(malformed)
    write_cf32(malformed, np.array([complex(float("nan"), 0)]))
    with pytest.raises(OfflineAnalysisError, match="non-finite"):
        inspect_cf32(malformed)
    write_cf32(malformed, np.array([1 + 1j]))
    assert inspect_cf32(malformed).clipped_samples == 1


def test_clipping_uses_component_threshold_not_complex_magnitude(tmp_path: Path) -> None:
    path = tmp_path / "threshold.cf32"
    write_cf32(path, np.array([0.8 + 0.8j, 0.998 + 0j, 0.999 + 0j, 0 + 1j]))
    inspection = inspect_cf32(path, clipping_threshold=0.999)
    assert inspection.clipped_samples == 2
    assert inspection.peak_component == 1.0
    assert inspection.clipping_threshold == 0.999


def test_carrier_gate_pass_fail_full_span_and_determinism(tmp_path: Path) -> None:
    rate, size, center = 4096, 1024, 10_000.0
    off = write_cf32(tmp_path / "rf off.cf32", np.zeros(size * 3))
    on = write_cf32(tmp_path / "rf on.cf32", tone(size * 3, rate, 500))
    parameters = CarrierParameters(rate, center, center + 500, fft_size=size, dc_exclusion_hz=100)
    first = analyze_carrier(off, on, parameters, tmp_path / "one.json")
    second = analyze_carrier(off, on, parameters, tmp_path / "two.json")
    assert first["gate_outcome"] == "passed"
    assert first["metrics"] == second["metrics"]
    assert first["metrics"]["strongest_transmitter_added_frequency_hz"] == center + 500
    failed = analyze_carrier(
        off,
        on,
        CarrierParameters(rate, center, center + 700, fft_size=size, dc_exclusion_hz=100),
        tmp_path / "fail.json",
    )
    assert failed["gate_outcome"] == "failed"
    assert failed["metrics"]["strongest_features"]


def test_silence_is_inconclusive_and_outputs_are_immutable(tmp_path: Path) -> None:
    values = np.zeros(1024, dtype=np.complex64)
    off = write_cf32(tmp_path / "off.cf32", values)
    on = write_cf32(tmp_path / "on.cf32", values)
    output = tmp_path / "analysis.json"
    result = analyze_carrier(
        off, on, CarrierParameters(4096, 10_000, 10_500, fft_size=1024, dc_exclusion_hz=100), output
    )
    assert result["gate_outcome"] == "inconclusive"
    with pytest.raises(OfflineAnalysisError, match="overwrite"):
        analyze_carrier(
            off,
            on,
            CarrierParameters(4096, 10_000, 10_500, fft_size=1024, dc_exclusion_hz=100),
            output,
        )


def test_dc_artifact_is_excluded_but_unexpected_feature_is_found(tmp_path: Path) -> None:
    rate, size, center = 4096, 1024, 10_000.0
    off = write_cf32(tmp_path / "off.cf32", np.zeros(size))
    on_values = tone(size, rate, 0, 0.5) + tone(size, rate, -700, 0.3)
    on = write_cf32(tmp_path / "on.cf32", on_values)
    result = analyze_carrier(
        off,
        on,
        CarrierParameters(rate, center, center + 500, fft_size=size, dc_exclusion_hz=100),
        tmp_path / "result.json",
    )
    assert result["metrics"]["strongest_transmitter_added_frequency_hz"] == center - 700
    assert result["gate_outcome"] == "failed"


def test_capture_metadata_pair_must_match_artifacts_and_settings(tmp_path: Path) -> None:
    from tests.unit.test_capture_metadata import capture_document

    rate, size, center = 4096, 1024, 10_000.0
    off = write_cf32(tmp_path / "off.cf32", np.zeros(size))
    on = write_cf32(tmp_path / "on.cf32", tone(size, rate, 500))

    def metadata(path: Path, iq: Path) -> Path:
        document = capture_document()
        for key in ("requested_settings", "actual_settings"):
            settings = document[key]
            assert isinstance(settings, dict)
            settings.update(
                {"sample_rate_hz": rate, "bandwidth_hz": rate, "center_frequency_hz": center}
            )
        document["requested_sample_count"] = size
        document["retained_sample_count"] = size
        output = document["output"]
        assert isinstance(output, dict)
        output["size_bytes"] = iq.stat().st_size
        output["sha256"] = hashlib.sha256(iq.read_bytes()).hexdigest()
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    off_meta = metadata(tmp_path / "off.json", off)
    on_meta = metadata(tmp_path / "on.json", on)
    parameters = CarrierParameters(rate, center, center + 500, fft_size=size, dc_exclusion_hz=100)
    result = analyze_carrier(
        off,
        on,
        parameters,
        tmp_path / "result.json",
        rf_off_metadata_path=off_meta,
        rf_on_metadata_path=on_meta,
    )
    assert result["contract"]["capture_metadata_validation"]["outcome"] == "passed"
    changed = json.loads(on_meta.read_text(encoding="utf-8"))
    changed["actual_settings"]["gain_db"] = 20
    changed["requested_settings"]["gain_db"] = 20
    on_meta.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="settings differ"):
        analyze_carrier(
            off,
            on,
            parameters,
            tmp_path / "mismatch.json",
            rf_off_metadata_path=off_meta,
            rf_on_metadata_path=on_meta,
        )


def test_carrier_averages_unequal_exact_counts_independently(tmp_path: Path) -> None:
    from tests.unit.test_capture_metadata import capture_document

    off = write_cf32(tmp_path / "off.cf32", np.zeros(1024))
    on = write_cf32(tmp_path / "on.cf32", np.zeros(2048))

    def metadata(path: Path, iq: Path, count: int) -> Path:
        document = capture_document()
        for key in ("requested_settings", "actual_settings"):
            document[key].update(  # type: ignore[union-attr]
                {"sample_rate_hz": 4096, "bandwidth_hz": 4096, "center_frequency_hz": 10_000}
            )
        document.update(requested_sample_count=count, retained_sample_count=count)
        document["output"].update(  # type: ignore[union-attr]
            size_bytes=iq.stat().st_size, sha256=hashlib.sha256(iq.read_bytes()).hexdigest()
        )
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    result = analyze_carrier(
        off,
        on,
        CarrierParameters(4096, 10_000, 10_500, fft_size=1024, dc_exclusion_hz=100),
        tmp_path / "result.json",
        rf_off_metadata_path=metadata(tmp_path / "off.json", off, 1024),
        rf_on_metadata_path=metadata(tmp_path / "on.json", on, 2048),
    )
    assert result["contract"]["rf_off_blocks"] == 1
    assert result["contract"]["rf_on_blocks"] == 2
    assert "without truncation or repetition" in result["contract"]["unequal_capture_policy"]
