from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from tests.unit.test_keyed_session_contracts import plan as keyed_plan
from tests.unit.test_real_session import plan_document
from wsprrypi_qualification.keyed_coordinator import _receiver_interpretations
from wsprrypi_qualification.keyed_session_contracts import resolved_keyed_plan_sha256
from wsprrypi_qualification.real_session import (
    RealSessionError,
    resolved_real_plan_sha256,
    validate_real_session_plan,
)
from wsprrypi_qualification.receiver_calibration import (
    ReceiverCalibrationError,
    compose_binding,
    disabled_binding,
    interpret_frequency,
    synthetic_profile,
    synthetic_request,
    validate_binding,
    write_synthetic_fixture,
)


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _binding(tmp_path: Path, *, directory: str = "calibration with spaces") -> dict:
    root = tmp_path / directory
    root.mkdir()
    profile = synthetic_profile()
    request = synthetic_request(profile)
    profile_path = root / "profile.json"
    request_path = root / "request.json"
    _write(profile_path, profile)
    _write(request_path, request)
    return compose_binding(profile_path, request_path)


def _synthetic_receiver() -> dict:
    return {
        "driver": "synthetic-sdrplay",
        "serial": "SYNTHETIC-0001",
        "channel": 0,
        "sample_rate_hz": 250000,
        "bandwidth_hz": 200000,
        "clock_source": "synthetic-internal",
        "frequency_correction_ppm": 0.0,
        "driver_version": "synthetic-1",
        "firmware_version": None,
        "antenna_port": "SYNTHETIC-A",
        "tuner_path": None,
        "binding_extension": {"channel": 0, "synthetic_non_hardware": True},
    }


def test_synthetic_fixture_is_deterministic_unsigned_and_non_hardware(tmp_path: Path) -> None:
    assert synthetic_profile() == synthetic_profile()
    assert "signature" not in synthetic_profile()["integrity"]
    assert "SYNTHETIC" in synthetic_profile()["profile_id"]
    paths = write_synthetic_fixture(tmp_path / "new fixture")
    assert set(paths) == {"profile", "request"}
    binding = compose_binding(paths["profile"], paths["request"])
    assert binding["applied"] is True
    assert binding["synthetic"] is True
    assert "receiver-side" in binding["limitation"]


@pytest.mark.parametrize("policy", ["optional", "disabled"])
def test_absent_policy_is_explicit_and_uncalibrated(policy: str) -> None:
    binding = disabled_binding(policy)
    interpretation = interpret_frequency(binding, 14_097_100.0)
    assert interpretation["calibration_applied"] is False
    assert interpretation["estimated_true_frequency_hz"] is None


def test_non_applied_binding_cannot_claim_synthetic() -> None:
    binding = disabled_binding()
    binding["synthetic"] = True
    with pytest.raises(ReceiverCalibrationError, match="cannot be synthetic"):
        validate_binding(binding)


def test_required_absence_and_disabled_supplied_profile_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ReceiverCalibrationError, match="cannot be required"):
        disabled_binding("required")
    paths = write_synthetic_fixture(tmp_path / "fixture")
    with pytest.raises(ReceiverCalibrationError, match="required/optional"):
        compose_binding(paths["profile"], paths["request"], policy="disabled")


def test_binding_rechecks_receiver_and_artifact_identity(tmp_path: Path) -> None:
    binding = _binding(tmp_path)
    validate_binding(binding, receiver=_synthetic_receiver())
    wrong = _synthetic_receiver()
    wrong["sample_rate_hz"] = 192000
    with pytest.raises(ReceiverCalibrationError, match="sample_rate_hz"):
        validate_binding(binding, receiver=wrong)
    wrong = _synthetic_receiver()
    wrong["firmware_version"] = "unexpected-firmware"
    with pytest.raises(ReceiverCalibrationError, match="firmware_version"):
        validate_binding(binding, receiver=wrong)
    binding_without_channel = deepcopy(binding)
    del binding_without_channel["application_request"]["document"]["configuration"][
        "binding_extension"
    ]["channel"]
    with pytest.raises(ReceiverCalibrationError, match="required channel"):
        validate_binding(binding_without_channel, receiver=_synthetic_receiver())
    Path(binding["profile"]["artifact"]["path"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ReceiverCalibrationError, match="artifact changed"):
        validate_binding(binding)


def test_interpretation_preserves_indicated_value_and_receiver_only_scope(tmp_path: Path) -> None:
    binding = _binding(tmp_path)
    result = interpret_frequency(binding, 14_097_100.0)
    assert result["indicated_frequency_hz"] == 14_097_100.0
    assert result["estimated_true_frequency_hz"] != result["indicated_frequency_hz"]
    assert result["expanded_uncertainty_hz"] > 0
    assert "transmitter PPM unchanged" in result["limitation"]


def test_calibration_changes_live_plan_digest_without_changing_transmitter_ppm(
    tmp_path: Path,
) -> None:
    binding = _binding(tmp_path)
    real = plan_document()
    original_real_digest = resolved_real_plan_sha256(real)
    ppm = real["calibration"]["ppm"]
    real["receiver"].update(_synthetic_receiver())
    real["receiver_calibration"] = binding
    assert resolved_real_plan_sha256(real) != original_real_digest
    assert real["calibration"]["ppm"] == ppm

    keyed = keyed_plan("FSKCW")
    original_keyed_digest = resolved_keyed_plan_sha256(keyed)
    keyed_receiver = _synthetic_receiver()
    keyed_receiver.pop("serial")
    keyed["receiver"].update(keyed_receiver)
    keyed["receiver"]["device"] = "SYNTHETIC-0001"
    keyed["receiver"]["identity_sha256"] = (
        __import__("hashlib").sha256(b"SYNTHETIC-0001").hexdigest()
    )
    transmitter_arguments = deepcopy(keyed["application_plan"]["arguments"])
    keyed["receiver_calibration"] = binding
    assert resolved_keyed_plan_sha256(keyed) != original_keyed_digest
    assert keyed["application_plan"]["arguments"] == transmitter_arguments
    interpreted = _receiver_interpretations(keyed)
    expected_spacing = (
        keyed["application_plan"]["protocol_contract"]["secondary_frequency_hz"]
        - keyed["application_plan"]["protocol_contract"]["primary_frequency_hz"]
    )
    assert interpreted["indicated_separation_hz"] == pytest.approx(expected_spacing)
    assert interpreted["estimated_true_separation_hz"] == pytest.approx(expected_spacing, abs=0.001)


def test_synthetic_binding_is_rejected_by_live_execution_plan(tmp_path: Path) -> None:
    real = plan_document(execution_mode="live")
    real["receiver"].update(_synthetic_receiver())
    real["receiver_calibration"] = _binding(tmp_path)
    with pytest.raises(RealSessionError, match="synthetic"):
        validate_real_session_plan(real)
