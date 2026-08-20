from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from wsprrypi_qualification.cli import main
from wsprrypi_qualification.sdr_calibration import (
    PROFILE_SCHEMA_NAME,
    PROFILE_SCHEMA_VERSION,
    UPSTREAM_REVISION,
    UPSTREAM_SCHEMA_SHA256,
    SdrCalibrationError,
    canonicalize,
    evaluate_profile,
    load_application_request,
    load_profile,
)


def profile_document() -> dict[str, object]:
    document: dict[str, object] = {
        "schema": {"name": PROFILE_SCHEMA_NAME, "version": PROFILE_SCHEMA_VERSION},
        "profile_id": "rsp1b-0001",
        "profile_status": "qualification_capable",
        "created_at": "2026-08-20T12:00:00Z",
        "device": {
            "driver": "sdrplay",
            "manufacturer": "SDRplay",
            "model": "RSP1B",
            "identifier": "0001",
            "identity_strength": "hardware_serial",
        },
        "configuration": {
            "clock_source": "internal",
            "sample_rate_hz": 250000,
            "bandwidth_hz": 200000,
            "frequency_correction_ppm": 0.0,
            "driver_version": "3.15",
            "firmware_version": None,
            "antenna_port": "A",
            "tuner_path": None,
            "binding_extension": {},
        },
        "frequency_model": {
            "error_definition": "indicated_minus_true",
            "segments": [
                {
                    "segment_id": "20m",
                    "minimum_frequency_hz": 14000000.0,
                    "maximum_frequency_hz": 14350000.0,
                    "model": {
                        "type": "linear",
                        "reference_frequency_hz": 14000000.0,
                        "intercept_error_hz": 5.0,
                        "slope_ppm": 2.0,
                    },
                    "uncertainty": {
                        "kind": "expanded",
                        "coverage_factor": 2.0,
                        "model": {"type": "linear", "base_hz": 0.5, "ppm_component": 0.1},
                        "included_components": ["reference", "estimator"],
                    },
                }
            ],
        },
        "assurance": {
            "scoring_policy_version": "reliability-quotient-v1",
            "reliability_quotient": 92,
            "reference_class": "authority_confirmed",
            "reference_score_ceiling": 100,
            "limiting_components": ["reference_provenance"],
            "components": [
                {"name": "reference_provenance", "score": 92, "basis": "fixture"},
                {"name": "device_binding", "score": 96, "basis": "serial"},
            ],
        },
        "validity": {
            "calibrated_at": "2026-08-20T11:00:00Z",
            "not_valid_after": "2027-08-20T11:00:00Z",
            "minimum_warmup_seconds": 1200,
            "temperature": {
                "reference_c": 20.0,
                "minimum_c": 10.0,
                "maximum_c": 30.0,
                "measurement_location": "SDR enclosure",
            },
        },
        "provenance": {
            "calibration_run_id": "run-1",
            "software": {"name": "SDR Calibration", "version": "0.1.1"},
            "reference_set": [
                {
                    "reference_id": "ref-1",
                    "kind": "authority_confirmed",
                    "nominal_frequency_hz": 10000000.0,
                    "assurance_score_ceiling": 100,
                    "evidence_sha256": "a" * 64,
                }
            ],
            "observation_ids": ["obs-1", "obs-2"],
        },
        "integrity": {"canonicalization": "RFC8785", "sha256": "0" * 64},
    }
    payload = deepcopy(document)
    del payload["integrity"]
    document["integrity"]["sha256"] = hashlib.sha256(canonicalize(payload)).hexdigest()  # type: ignore[index]
    return document


def request_document(profile: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "indicated_frequency_hz": 14097010.0,
        "target_frequency_hz": 14096999.80598,
        "device": deepcopy(profile["device"]),
        "configuration": deepcopy(profile["configuration"]),
        "temperature_c": 20.0,
        "warmup_seconds": 1200,
        "evaluated_at": "2026-08-20T13:00:00Z",
        "required_reliability_quotient": 90,
        "maximum_expanded_uncertainty_hz": 2.0,
    }


def write_json(path: Path, document: dict[str, object]) -> None:
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def test_loads_exact_frozen_profile_and_evaluates_linear_model(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    request_path = tmp_path / "request.json"
    expected = profile_document()
    write_json(profile_path, expected)
    write_json(request_path, request_document(expected))

    profile = load_profile(profile_path)
    request = load_application_request(request_path)
    result = evaluate_profile(profile, request)

    assert result["status"] == "qualification_capable"
    assert result["qualification_usable"] is True
    assert result["segment_id"] == "20m"
    assert result["indicated_error_hz"] == pytest.approx(5.19402)
    assert result["estimated_true_frequency_hz"] == pytest.approx(14097004.80598)
    assert result["target_offset_hz"] == pytest.approx(5.0)
    assert result["expanded_uncertainty_hz"] == pytest.approx(1.909701)
    assert result["profile_integrity_sha256"] == profile["integrity"]["sha256"]
    assert result["upstream_revision"] == UPSTREAM_REVISION
    assert result["upstream_schema_sha256"] == UPSTREAM_SCHEMA_SHA256


def test_cli_emits_qualification_usable_application(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile_path = tmp_path / "profile.json"
    request_path = tmp_path / "request.json"
    profile = profile_document()
    write_json(profile_path, profile)
    write_json(request_path, request_document(profile))

    assert main(["evaluate-sdr-calibration", str(profile_path), str(request_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["qualification_usable"] is True
    assert output["profile_schema_version"] == "1.0.0"


def test_evaluates_constant_ppm_and_normalizes_absent_optional_settings() -> None:
    profile = profile_document()
    segment = profile["frequency_model"]["segments"][0]  # type: ignore[index]
    segment["model"] = {"type": "constant_ppm", "error_ppm": 1.5}
    configuration = profile["configuration"]
    for key in ("driver_version", "firmware_version", "antenna_port", "tuner_path"):
        del configuration[key]  # type: ignore[index]
    request = request_document(profile)
    request["configuration"].update(  # type: ignore[union-attr]
        driver_version=None,
        firmware_version=None,
        antenna_port=None,
        tuner_path=None,
    )

    result = evaluate_profile(profile, request)

    assert result["status"] == "qualification_capable"
    assert result["indicated_error_hz"] == pytest.approx(21.145515)


def test_rejects_other_profile_version_even_with_valid_schema_shape(tmp_path: Path) -> None:
    profile = profile_document()
    profile["schema"]["version"] = "1.0.1"  # type: ignore[index]
    path = tmp_path / "profile.json"
    write_json(path, profile)
    with pytest.raises(SdrCalibrationError, match=r"1\.0\.0"):
        load_profile(path)


def test_rejects_tampered_profile(tmp_path: Path) -> None:
    profile = profile_document()
    profile["profile_id"] = "tampered"
    path = tmp_path / "profile.json"
    write_json(path, profile)
    with pytest.raises(SdrCalibrationError, match="SHA-256"):
        load_profile(path)


def test_rejects_signed_profile_until_verifier_exists(tmp_path: Path) -> None:
    profile = profile_document()
    profile["integrity"]["signature"] = {  # type: ignore[index]
        "algorithm": "ed25519",
        "key_id": "key-1",
        "value": "opaque",
    }
    path = tmp_path / "profile.json"
    write_json(path, profile)
    with pytest.raises(SdrCalibrationError, match="Ed25519 verifier"):
        load_profile(path)


@pytest.mark.parametrize(
    ("change", "status"),
    [
        (lambda request: request["device"].update(identifier="other"), "identity_mismatch"),
        (
            lambda request: request["configuration"].update(sample_rate_hz=768000),
            "configuration_mismatch",
        ),
        (lambda request: request.update(temperature_c=31.0), "outside_temperature_domain"),
        (lambda request: request.update(warmup_seconds=1199), "insufficient_warmup"),
        (
            lambda request: request.update(indicated_frequency_hz=15000000.0),
            "outside_frequency_domain",
        ),
        (
            lambda request: request.update(required_reliability_quotient=93),
            "assurance_requirement_not_met",
        ),
        (
            lambda request: request.update(maximum_expanded_uncertainty_hz=1.0),
            "uncertainty_requirement_not_met",
        ),
    ],
)
def test_evaluation_fails_closed(change: object, status: str) -> None:
    profile = profile_document()
    request = request_document(profile)
    change(request)  # type: ignore[operator]
    result = evaluate_profile(profile, request)
    assert result["status"] == status
    assert result["qualification_usable"] is False


def test_canonicalization_matches_upstream_numeric_and_key_vectors() -> None:
    assert canonicalize([333333333.33333329, 1e30, 4.50, 2e-3, 1e-27]) == (
        b"[333333333.3333333,1e+30,4.5,0.002,1e-27]"
    )
    assert canonicalize({"\U00010000": 1, "\ue000": 2}).startswith(b'{"\xf0\x90\x80\x80"')
