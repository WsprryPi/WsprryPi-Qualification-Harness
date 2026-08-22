import json
from pathlib import Path
from typing import Any

import pytest

from wsprrypi_qualification.models import AuthorizationScope, Backend, PathType
from wsprrypi_qualification.profiles import (
    ProfileError,
    load_bench_profile,
    load_receiver_run_profile,
    load_test_profile,
)

ROOT = Path(__file__).resolve().parents[2]


def load_example(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def receiver_run_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": "20260812T122910Z-slice5-rsp1b-receiver",
        "bench_id": "wspr5-rsp1b",
        "receiver": {
            "transport": "ssh",
            "host": "wspr5",
            "driver": "sdrplay",
            "serial": "2404058C60",
            "channel": 0,
            "sample_rate_hz": 250000,
            "bandwidth_hz": 200000,
            "sample_format": "CF32",
            "agc": False,
            "bias_tee": False,
        },
        "center_frequency_hz": 1863100,
        "gain_db": 10,
        "duration_s": 10,
        "rf_path": {
            "path_type": "radiated",
            "antenna_connected": True,
            "attenuation_db": 0,
            "filter_description": "No inline filter; receiver-only ambient capture.",
            "safe_input_description": "No local transmitter operated; zero clipping verified.",
        },
        "limits": {
            "sample_count": 2500000,
            "read_timeout_us": 2000000,
            "helper_deadline_s": 15,
            "external_deadline_s": 20,
        },
        "authorization": {
            "scope": "single_run",
            "reference": "interactive operator authorization",
            "recorded_utc": "2026-08-12T12:00:00Z",
        },
        "ownership_and_cleanup": "Stop and restore only soapyremote-server.service.",
    }


def test_valid_bench_profile() -> None:
    profile = load_bench_profile(ROOT / "examples" / "bench-wspr5-rsp1b.json")
    assert profile.rf_path.path_type is PathType.CONDUCTED
    assert profile.receiver.sample_rate_hz == 250_000


def test_radiated_profile_preserves_not_applicable_attenuation(tmp_path: Path) -> None:
    document = load_example("bench-wspr5-rsp1b.json")
    document["rf_path"] = {
        "path_type": "radiated",
        "antenna_connected": True,
        "attenuation_db": None,
        "filter_description": "None",
        "safe_input_description": "N/A",
    }
    profile = load_bench_profile(write_json(tmp_path / "radiated.json", document))
    assert profile.rf_path.attenuation_db is None
    assert profile.rf_path.termination_ohms is None


def test_conducted_profile_rejects_null_attenuation(tmp_path: Path) -> None:
    document = load_example("bench-wspr5-rsp1b.json")
    document["rf_path"]["attenuation_db"] = None
    with pytest.raises(ProfileError, match="attenuation_db"):
        load_bench_profile(write_json(tmp_path / "conducted-null.json", document))


@pytest.mark.parametrize(
    ("field", "value"),
    [("antenna_connected", True), ("termination_ohms", None), ("attenuation_db", None)],
)
def test_receiver_run_conducted_invariants(tmp_path: Path, field: str, value: object) -> None:
    document = receiver_run_document()
    document["rf_path"].update(
        {
            "path_type": "conducted",
            "antenna_connected": False,
            "termination_ohms": 50,
            "attenuation_db": 30,
        }
    )
    document["rf_path"][field] = value
    with pytest.raises(ProfileError, match=r"antenna_connected|termination_ohms|attenuation_db"):
        load_receiver_run_profile(write_json(tmp_path / f"conducted-{field}.json", document))


def test_valid_test_profile() -> None:
    profile = load_test_profile(ROOT / "examples" / "test-si5351-160m.json")
    assert profile.transmitter.backend is Backend.SI5351
    assert profile.identity.callsign == "Q0QQQ"
    assert profile.identity.grid == "JJ00"
    assert profile.identity.power_dbm == 0
    assert "quiescence" in profile.stopping_procedure.cleanup_expectation.lower()


@pytest.mark.parametrize("scope", ["single_run", "universal"])
def test_valid_runtime_receiver_run_profile(tmp_path: Path, scope: str) -> None:
    document = receiver_run_document()
    document["authorization"]["scope"] = scope
    profile = load_receiver_run_profile(write_json(tmp_path / f"{scope}.json", document))
    assert profile.authorization.scope is AuthorizationScope(scope)
    assert profile.limits.sample_count == 2_500_000


def test_receiver_run_requires_current_rf_path(tmp_path: Path) -> None:
    document = receiver_run_document()
    del document["rf_path"]["safe_input_description"]
    with pytest.raises(ProfileError, match="safe_input_description"):
        load_receiver_run_profile(write_json(tmp_path / "missing-path.json", document))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sample_count", 1, "sample count"),
        ("helper_deadline_s", 10, "helper deadline"),
        ("external_deadline_s", 15, "external deadline"),
    ],
)
def test_receiver_run_limit_consistency(
    tmp_path: Path, field: str, value: int, message: str
) -> None:
    document = receiver_run_document()
    document["limits"][field] = value
    with pytest.raises(ProfileError, match=message):
        load_receiver_run_profile(write_json(tmp_path / f"bad-{field}.json", document))


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


def test_tone_profile_records_zero_wspr_frames(tmp_path: Path) -> None:
    document = load_example("test-si5351-160m.json")
    document["mode"] = "TONE"
    document["frame_count"] = 0
    document["gates"]["required_consecutive_decodes"] = 0
    profile = load_test_profile(write_json(tmp_path / "tone.json", document))
    assert profile.frame_count == 0


def test_tone_profile_rejects_wspr_frame_claim(tmp_path: Path) -> None:
    document = load_example("test-si5351-160m.json")
    document["mode"] = "TONE"
    with pytest.raises(ProfileError, match=r"0 was expected"):
        load_test_profile(write_json(tmp_path / "tone-with-frames.json", document))


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
