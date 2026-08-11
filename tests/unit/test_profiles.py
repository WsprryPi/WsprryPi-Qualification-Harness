import json
from pathlib import Path
from typing import Any

import pytest

from wsprrypi_qualification.models import Backend, PathType
from wsprrypi_qualification.profiles import ProfileError, load_bench_profile, load_test_profile

ROOT = Path(__file__).resolve().parents[2]


def load_example(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_valid_bench_profile() -> None:
    profile = load_bench_profile(ROOT / "examples" / "bench-wspr5-rsp1b.json")
    assert profile.rf_path.path_type is PathType.CONDUCTED
    assert profile.receiver.sample_rate_hz == 250_000


def test_valid_test_profile() -> None:
    profile = load_test_profile(ROOT / "examples" / "test-si5351-160m.json")
    assert profile.transmitter.backend is Backend.SI5351
    assert profile.identity.callsign == "AA0NT"
    assert "quiescence" in profile.stopping_procedure.cleanup_expectation.lower()


def test_invalid_profile_has_source_and_location(tmp_path: Path) -> None:
    document = load_example("bench-wspr5-rsp1b.json")
    document["receiver"]["sample_rate_hz"] = 0
    path = write_json(tmp_path / "invalid bench.json", document)
    with pytest.raises(ProfileError) as caught:
        load_bench_profile(path)
    assert str(path) in str(caught.value)
    assert "sample_rate_hz" in str(caught.value)


def test_unknown_field_rejected(tmp_path: Path) -> None:
    document = load_example("test-si5351-160m.json")
    document["unexpected"] = True
    with pytest.raises(ProfileError, match="unexpected"):
        load_test_profile(write_json(tmp_path / "unknown.json", document))


@pytest.mark.parametrize("field", ["confirmed", "operator_verified", "approved", "enable_rf"])
def test_runtime_confirmation_fields_rejected(tmp_path: Path, field: str) -> None:
    document = load_example("test-si5351-160m.json")
    document[field] = True
    with pytest.raises(ProfileError, match="runtime confirmation"):
        load_test_profile(write_json(tmp_path / f"{field}.json", document))


def test_random_offset_must_be_disabled(tmp_path: Path) -> None:
    document = load_example("test-si5351-160m.json")
    document["random_offset_enabled"] = True
    with pytest.raises(ProfileError, match="False was expected"):
        load_test_profile(write_json(tmp_path / "offset.json", document))


def test_conducted_path_requires_no_antenna(tmp_path: Path) -> None:
    document = load_example("bench-wspr5-rsp1b.json")
    document["rf_path"]["antenna_connected"] = True
    with pytest.raises(ProfileError, match="False was expected"):
        load_bench_profile(write_json(tmp_path / "unsafe path.json", document))


def test_receiver_bandwidth_must_not_exceed_sample_rate(tmp_path: Path) -> None:
    document = load_example("bench-wspr5-rsp1b.json")
    document["receiver"]["bandwidth_hz"] = 300_000
    with pytest.raises(ProfileError, match="bandwidth"):
        load_bench_profile(write_json(tmp_path / "bandwidth.json", document))


def test_required_decodes_must_fit_frame_count(tmp_path: Path) -> None:
    document = load_example("test-si5351-160m.json")
    document["frame_count"] = 2
    with pytest.raises(ProfileError, match="frame count"):
        load_test_profile(write_json(tmp_path / "frame count.json", document))


def test_backend_specific_fields_are_required_semantically(tmp_path: Path) -> None:
    document = load_example("test-si5351-160m.json")
    del document["transmitter"]["i2c_address"]
    with pytest.raises(ProfileError, match="i2c_address"):
        load_test_profile(write_json(tmp_path / "missing i2c.json", document))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_numeric_constants_are_rejected(tmp_path: Path, constant: str) -> None:
    source = (ROOT / "examples" / "test-si5351-160m.json").read_text(encoding="utf-8")
    source = source.replace('"ppm": 2.353615654', f'"ppm": {constant}')
    path = tmp_path / f"nonfinite-{constant}.json"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ProfileError) as caught:
        load_test_profile(path)
    assert str(path) in str(caught.value)
    assert "non-standard JSON" in str(caught.value)


def test_overflowed_finite_literal_is_rejected_with_location(tmp_path: Path) -> None:
    source = (ROOT / "examples" / "test-si5351-160m.json").read_text(encoding="utf-8")
    source = source.replace('"receiver_gain_db": 10', '"receiver_gain_db": 1e999')
    path = tmp_path / "overflow.json"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ProfileError) as caught:
        load_test_profile(path)
    assert "receiver_gain_db" in str(caught.value)
    assert "finite" in str(caught.value)


def test_nested_nonfinite_value_is_rejected(tmp_path: Path) -> None:
    source = (ROOT / "examples" / "test-si5351-160m.json").read_text(encoding="utf-8")
    source = source.replace('"best_20hz_share_min": 0.5', '"best_20hz_share_min": 1e999')
    path = tmp_path / "nested-overflow.json"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ProfileError, match="best_20hz_share_min"):
        load_test_profile(path)


def test_finite_negative_values_remain_valid_where_allowed(tmp_path: Path) -> None:
    document = load_example("test-si5351-160m.json")
    document["ppm"] = -2.5
    document["receiver_gain_db"] = -10
    profile = load_test_profile(write_json(tmp_path / "finite-negative.json", document))
    assert profile.ppm == -2.5


def test_stopping_procedure_is_required(tmp_path: Path) -> None:
    document = load_example("test-si5351-160m.json")
    del document["stopping_procedure"]
    with pytest.raises(ProfileError, match="stopping_procedure"):
        load_test_profile(write_json(tmp_path / "no-stop.json", document))


@pytest.mark.parametrize("identifier", ["test.", "con", "nul.txt", "bad..path"])
def test_portability_unsafe_test_ids_are_rejected(tmp_path: Path, identifier: str) -> None:
    document = load_example("test-si5351-160m.json")
    document["test_id"] = identifier
    with pytest.raises(ProfileError):
        load_test_profile(write_json(tmp_path / "unsafe-id.json", document))
