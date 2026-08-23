"""First-class, receiver-only bindings for the frozen SDR calibration contract."""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wsprrypi_qualification.offline import artifact, validate_document
from wsprrypi_qualification.sdr_calibration import (
    PROFILE_SCHEMA_NAME,
    PROFILE_SCHEMA_VERSION,
    UPSTREAM_REVISION,
    UPSTREAM_SCHEMA_SHA256,
    canonicalize,
    evaluate_profile,
    load_application_request,
    load_profile,
    validate_application_request_document,
    validate_profile_document,
)

POLICIES = frozenset({"required", "optional", "disabled"})


class ReceiverCalibrationError(ValueError):
    """A receiver calibration binding is missing, contradictory, or unauthenticated."""


def _file_artifact(path: Path) -> dict[str, Any]:
    value = artifact(path.resolve(strict=True))
    value["path"] = str(path)
    return value


def _document_sha256(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonicalize(document)).hexdigest()


def _is_synthetic_profile(profile: dict[str, Any]) -> bool:
    """Recognize every conspicuous marker emitted by the maintained fixture."""
    device = profile["device"]
    configuration = profile["configuration"]
    provenance = profile["provenance"]
    return any(
        (
            profile["profile_id"].startswith("SYNTHETIC-NON-HARDWARE-"),
            device["manufacturer"] == "SYNTHETIC",
            str(device["model"]).startswith("NON-HARDWARE-"),
            str(device["identifier"]).startswith("SYNTHETIC-"),
            configuration.get("binding_extension", {}).get("synthetic_non_hardware") is True,
            str(provenance["calibration_run_id"]).startswith("SYNTHETIC-NON-HARDWARE-"),
            provenance["software"]["name"] == "WsprryPi Qualification Harness synthetic fixture",
        )
    )


def _bound_document(path: Path, document: dict[str, Any]) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "artifact": _file_artifact(path),
        "artifact_bytes_base64": base64.b64encode(payload).decode("ascii"),
        "document_sha256": _document_sha256(document),
        "document": document,
    }


def disabled_binding(policy: str = "disabled") -> dict[str, Any]:
    """Return an explicit non-applied calibration policy binding."""
    if policy not in {"optional", "disabled"}:
        raise ReceiverCalibrationError("an absent receiver calibration cannot be required")
    document = {
        "schema_version": 1,
        "evidence_type": "receiver_calibration_binding",
        "policy": policy,
        "applied": False,
        "synthetic": False,
        "profile": None,
        "application_request": None,
        "application_result": None,
        "frozen_contract": {
            "schema_name": PROFILE_SCHEMA_NAME,
            "schema_version": PROFILE_SCHEMA_VERSION,
            "upstream_revision": UPSTREAM_REVISION,
            "upstream_schema_sha256": UPSTREAM_SCHEMA_SHA256,
        },
        "limitation": (
            "receiver frequency interpretation is explicitly uncalibrated"
            if policy == "optional"
            else "receiver calibration is explicitly disabled"
        ),
    }
    validate_document(document, "receiver-calibration-binding.schema.json")
    return document


def compose_binding(
    profile_path: Path,
    request_path: Path,
    *,
    policy: str = "required",
) -> dict[str, Any]:
    """Authenticate and bind one frozen profile evaluation without device access."""
    if policy not in {"required", "optional"}:
        raise ReceiverCalibrationError("a supplied receiver calibration requires required/optional")
    profile = load_profile(profile_path)
    request = load_application_request(request_path)
    result = evaluate_profile(profile, request)
    if not result["qualification_usable"]:
        raise ReceiverCalibrationError(
            "receiver calibration is not qualification usable: "
            f"{result['status']}: {result['reason']}"
        )
    document = {
        "schema_version": 1,
        "evidence_type": "receiver_calibration_binding",
        "policy": policy,
        "applied": True,
        "synthetic": _is_synthetic_profile(profile),
        "profile": _bound_document(profile_path, profile),
        "application_request": _bound_document(request_path, request),
        "application_result": result,
        "frozen_contract": {
            "schema_name": PROFILE_SCHEMA_NAME,
            "schema_version": PROFILE_SCHEMA_VERSION,
            "upstream_revision": UPSTREAM_REVISION,
            "upstream_schema_sha256": UPSTREAM_SCHEMA_SHA256,
        },
        "limitation": "receiver-side frequency interpretation only; never transmitter PPM",
    }
    return validate_binding(document)


def validate_binding(
    document: dict[str, Any], *, receiver: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Validate policy, hashes, frozen evaluation, and optional resolved receiver facts."""
    validate_document(document, "receiver-calibration-binding.schema.json")
    if document["policy"] not in POLICIES:
        raise ReceiverCalibrationError("receiver calibration policy is unsupported")
    frozen = document["frozen_contract"]
    if frozen != {
        "schema_name": PROFILE_SCHEMA_NAME,
        "schema_version": PROFILE_SCHEMA_VERSION,
        "upstream_revision": UPSTREAM_REVISION,
        "upstream_schema_sha256": UPSTREAM_SCHEMA_SHA256,
    }:
        raise ReceiverCalibrationError("receiver calibration frozen-contract identity differs")
    if not document["applied"]:
        if document["policy"] == "required":
            raise ReceiverCalibrationError("required receiver calibration is absent")
        if document["synthetic"]:
            raise ReceiverCalibrationError("non-applied receiver calibration cannot be synthetic")
        if any(
            document[field] is not None
            for field in ("profile", "application_request", "application_result")
        ):
            raise ReceiverCalibrationError("non-applied receiver calibration contains artifacts")
        return deepcopy(document)
    if document["policy"] == "disabled":
        raise ReceiverCalibrationError("disabled receiver calibration cannot be applied")
    profile_binding = document["profile"]
    request_binding = document["application_request"]
    profile = profile_binding["document"]
    request = request_binding["document"]
    validate_profile_document(profile)
    expected_synthetic = _is_synthetic_profile(profile)
    if document["synthetic"] is not expected_synthetic:
        raise ReceiverCalibrationError("receiver calibration synthetic marker is inconsistent")
    validate_application_request_document(request)
    configuration = request["configuration"]
    if receiver is not None:
        required_configuration = {
            "bandwidth_hz",
            "driver_version",
            "firmware_version",
            "antenna_port",
            "tuner_path",
            "binding_extension",
        }
        missing = sorted(required_configuration - configuration.keys())
        if missing:
            raise ReceiverCalibrationError(
                "receiver calibration lacks required compatibility facts: " + ", ".join(missing)
            )
        if "channel" not in configuration["binding_extension"]:
            raise ReceiverCalibrationError(
                "receiver calibration binding_extension lacks required channel"
            )
    for binding, label in ((profile_binding, "profile"), (request_binding, "application request")):
        try:
            retained_payload = base64.b64decode(binding["artifact_bytes_base64"], validate=True)
            retained_document = json.loads(
                retained_payload.decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-standard JSON numeric constant: {value}")
                ),
            )
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReceiverCalibrationError(
                f"receiver calibration {label} retained artifact is invalid"
            ) from error
        if (
            len(retained_payload) != binding["artifact"]["size_bytes"]
            or hashlib.sha256(retained_payload).hexdigest() != binding["artifact"]["sha256"]
            or retained_document != binding["document"]
        ):
            raise ReceiverCalibrationError(
                f"receiver calibration {label} retained artifact differs from its binding"
            )
        if binding["document_sha256"] != _document_sha256(binding["document"]):
            raise ReceiverCalibrationError(
                f"receiver calibration {label} embedded document changed"
            )
        path = Path(binding["artifact"]["path"])
        if path.exists():
            payload = path.read_bytes()
            if (
                len(payload) != binding["artifact"]["size_bytes"]
                or hashlib.sha256(payload).hexdigest() != binding["artifact"]["sha256"]
            ):
                raise ReceiverCalibrationError(f"receiver calibration {label} artifact changed")
    expected = evaluate_profile(profile, request)
    if expected != document["application_result"] or not expected["qualification_usable"]:
        raise ReceiverCalibrationError("receiver calibration application result is inconsistent")
    if receiver is not None:
        device = request["device"]
        comparisons = {
            "driver": device["driver"],
            "sample_rate_hz": configuration["sample_rate_hz"],
            "bandwidth_hz": configuration["bandwidth_hz"],
            "clock_source": configuration["clock_source"],
            "frequency_correction_ppm": configuration["frequency_correction_ppm"],
            "driver_version": configuration["driver_version"],
            "firmware_version": configuration["firmware_version"],
            "antenna_port": configuration["antenna_port"],
            "tuner_path": configuration["tuner_path"],
            "binding_extension": configuration["binding_extension"],
            "channel": configuration["binding_extension"]["channel"],
        }
        if receiver.get("serial", receiver.get("device")) != device["identifier"]:
            raise ReceiverCalibrationError("receiver calibration device identifier differs")
        for field, expected_value in comparisons.items():
            if receiver.get(field) != expected_value:
                raise ReceiverCalibrationError(f"receiver calibration {field} differs")
    return deepcopy(document)


def validate_live_binding(
    document: dict[str, Any],
    *,
    receiver: dict[str, Any],
    indicated_frequencies_hz: list[float],
    execution_time: datetime | None = None,
) -> dict[str, Any]:
    """Fail before live adapter construction if run facts are stale or inapplicable."""
    binding = validate_binding(document, receiver=receiver)
    if binding["synthetic"]:
        raise ReceiverCalibrationError("synthetic receiver calibration cannot enter live execution")
    if not binding["applied"]:
        return binding
    now = execution_time or datetime.now(UTC)
    request = binding["application_request"]["document"]
    evaluated_at = datetime.fromisoformat(request["evaluated_at"].replace("Z", "+00:00"))
    age_seconds = (now - evaluated_at).total_seconds()
    if age_seconds < 0 or age_seconds > request["maximum_application_age_seconds"]:
        raise ReceiverCalibrationError("receiver calibration application facts are stale")
    expires_at = datetime.fromisoformat(
        binding["profile"]["document"]["validity"]["not_valid_after"].replace("Z", "+00:00")
    )
    if now >= expires_at:
        raise ReceiverCalibrationError(
            "receiver calibration profile has expired before live access"
        )
    for frequency_hz in indicated_frequencies_hz:
        interpret_frequency(binding, frequency_hz)
    if "signature" not in binding["profile"]["document"]["integrity"]:
        raise ReceiverCalibrationError(
            "unsigned receiver calibration cannot enter live execution without a trust policy"
        )
    return binding


def interpret_frequency(document: dict[str, Any], indicated_frequency_hz: float) -> dict[str, Any]:
    """Derive an authenticated receiver-only interpretation at an indicated frequency."""
    binding = validate_binding(document)
    if not binding["applied"]:
        return {
            "calibration_applied": False,
            "indicated_frequency_hz": indicated_frequency_hz,
            "estimated_true_frequency_hz": None,
            "expanded_uncertainty_hz": None,
            "profile_id": None,
            "segment_id": None,
            "qualification_usable": False,
            "limitation": binding["limitation"],
        }
    request = deepcopy(binding["application_request"]["document"])
    request["indicated_frequency_hz"] = indicated_frequency_hz
    request.pop("target_frequency_hz", None)
    result = evaluate_profile(binding["profile"]["document"], request)
    if not result["qualification_usable"]:
        raise ReceiverCalibrationError(
            f"receiver frequency is outside the bound calibration: {result['status']}"
        )
    return {
        "calibration_applied": True,
        "indicated_frequency_hz": indicated_frequency_hz,
        "indicated_error_hz": result["indicated_error_hz"],
        "estimated_true_frequency_hz": result["estimated_true_frequency_hz"],
        "expanded_uncertainty_hz": result["expanded_uncertainty_hz"],
        "profile_id": result["profile_id"],
        "profile_integrity_sha256": result["profile_integrity_sha256"],
        "segment_id": result["segment_id"],
        "reliability_quotient": result["reliability_quotient"],
        "qualification_usable": True,
        "limitation": "receiver-side frequency interpretation only; transmitter PPM unchanged",
    }


def synthetic_profile() -> dict[str, Any]:
    """Build a deterministic, unsigned, conspicuously synthetic frozen-contract fixture."""
    document: dict[str, Any] = {
        "schema": {"name": PROFILE_SCHEMA_NAME, "version": PROFILE_SCHEMA_VERSION},
        "profile_id": "SYNTHETIC-NON-HARDWARE-RSP1B-0001",
        "profile_status": "qualification_capable",
        "created_at": "2026-01-01T00:00:00Z",
        "device": {
            "driver": "synthetic-sdrplay",
            "manufacturer": "SYNTHETIC",
            "model": "NON-HARDWARE-RSP1B",
            "identifier": "SYNTHETIC-0001",
            "identity_strength": "operator_assigned",
        },
        "configuration": {
            "clock_source": "synthetic-internal",
            "sample_rate_hz": 250000,
            "bandwidth_hz": 200000,
            "frequency_correction_ppm": 0.0,
            "driver_version": "synthetic-1",
            "firmware_version": None,
            "antenna_port": "SYNTHETIC-A",
            "tuner_path": None,
            "binding_extension": {"channel": 0, "synthetic_non_hardware": True},
        },
        "frequency_model": {
            "error_definition": "indicated_minus_true",
            "segments": [
                {
                    "segment_id": "synthetic-hf",
                    "minimum_frequency_hz": 1000000.0,
                    "maximum_frequency_hz": 30000000.0,
                    "model": {"type": "constant_ppm", "error_ppm": 1.25},
                    "uncertainty": {
                        "kind": "expanded",
                        "coverage_factor": 2.0,
                        "model": {"type": "linear", "base_hz": 0.5, "ppm_component": 0.1},
                        "included_components": ["synthetic-reference", "synthetic-estimator"],
                        "excluded_components": ["all-real-hardware-effects"],
                    },
                }
            ],
        },
        "assurance": {
            "scoring_policy_version": "reliability-quotient-v1",
            "reliability_quotient": 90,
            "reference_class": "authority_confirmed",
            "reference_score_ceiling": 100,
            "limiting_components": ["synthetic_fixture_ceiling"],
            "components": [
                {
                    "name": "synthetic_fixture_ceiling",
                    "score": 90,
                    "basis": "hardware-free contract exercise only",
                }
            ],
        },
        "validity": {
            "calibrated_at": "2026-01-01T00:00:00Z",
            "not_valid_after": "2030-01-01T00:00:00Z",
            "minimum_warmup_seconds": 0,
            "temperature": {
                "reference_c": 20.0,
                "minimum_c": -40.0,
                "maximum_c": 85.0,
                "measurement_location": "synthetic fixture",
            },
        },
        "provenance": {
            "calibration_run_id": "SYNTHETIC-NON-HARDWARE-RUN",
            "software": {
                "name": "WsprryPi Qualification Harness synthetic fixture",
                "version": "1",
            },
            "reference_set": [
                {
                    "reference_id": "SYNTHETIC-REFERENCE",
                    "kind": "authority_confirmed",
                    "nominal_frequency_hz": 10000000.0,
                    "assurance_score_ceiling": 100,
                    "evidence_sha256": "0" * 64,
                }
            ],
            "observation_ids": ["SYNTHETIC-OBSERVATION"],
        },
        "integrity": {"canonicalization": "RFC8785", "sha256": "0" * 64},
    }
    payload = deepcopy(document)
    del payload["integrity"]
    document["integrity"]["sha256"] = hashlib.sha256(canonicalize(payload)).hexdigest()
    return validate_profile_document(document)


def synthetic_request(profile: dict[str, Any]) -> dict[str, Any]:
    """Build the matching deterministic hardware-free application request."""
    request = {
        "schema_version": 1,
        "indicated_frequency_hz": 14097100.0,
        "target_frequency_hz": 14097100.0,
        "device": deepcopy(profile["device"]),
        "configuration": deepcopy(profile["configuration"]),
        "temperature_c": 20.0,
        "warmup_seconds": 0,
        "evaluated_at": "2026-08-23T00:00:00Z",
        "maximum_application_age_seconds": 300,
        "required_reliability_quotient": 90,
        "maximum_expanded_uncertainty_hz": 3.0,
    }
    return validate_application_request_document(request)


def write_synthetic_fixture(output_directory: Path) -> dict[str, Path]:
    """Write a new deterministic profile/request pair without overwriting files."""
    if output_directory.exists():
        raise ReceiverCalibrationError("refusing to overwrite a synthetic calibration fixture")
    output_directory.mkdir(parents=True)
    profile = synthetic_profile()
    request = synthetic_request(profile)
    paths = {
        "profile": output_directory / "synthetic-profile.json",
        "request": output_directory / "synthetic-request.json",
    }
    for key, document in (("profile", profile), ("request", request)):
        paths[key].write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return paths
