"""Fail-closed consumer for SDR Calibration Profile 1.0.0 artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Never, cast

from jsonschema import Draft202012Validator, FormatChecker

PROFILE_SCHEMA_NAME = "sdr-calibration-profile"
PROFILE_SCHEMA_VERSION = "1.0.0"
UPSTREAM_REVISION = "faae3ea76ee9611e379fa2b3c99fb92bebd48041"
UPSTREAM_SCHEMA_SHA256 = "2a2ef74f783e6962159c41283a70fc5dced70e7cfc2f6ae2eb4bbc5ff52b9930"
_MAX_EXACT_INTEGER = 9_007_199_254_740_991
_REFERENCE_CEILINGS = {
    "authority_confirmed": 100,
    "derived_traceable": 90,
    "locally_characterized": 75,
    "ad_hoc": 50,
    "unknown": 0,
}


class SdrCalibrationError(ValueError):
    """An SDR calibration profile or evaluation request is unusable."""


def _reject_constant(value: str) -> Never:
    raise ValueError(f"non-standard JSON numeric constant is forbidden: {value}")


def _read_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise SdrCalibrationError(f"{path}: invalid {description}: {error}") from error
    if not isinstance(value, dict):
        raise SdrCalibrationError(f"{path}: {description} root must be an object")
    _finite(value, "$")
    return cast(dict[str, Any], value)


def _finite(value: Any, location: str) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise SdrCalibrationError(f"{location}: numeric value must be finite")
    if isinstance(value, dict):
        for key, child in value.items():
            _finite(child, f"{location}[{key!r}]")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _finite(child, f"{location}[{index}]")


def _schema() -> dict[str, Any]:
    resource = files("wsprrypi_qualification.schemas").joinpath(
        "sdr-calibration-profile.schema.json"
    )
    raw = resource.read_bytes()
    if hashlib.sha256(raw).hexdigest() != UPSTREAM_SCHEMA_SHA256:
        raise SdrCalibrationError("packaged SDR calibration schema differs from the frozen pin")
    return cast(dict[str, Any], json.loads(raw))


def _application_schema() -> dict[str, Any]:
    resource = files("wsprrypi_qualification.schemas").joinpath(
        "sdr-calibration-application-request.schema.json"
    )
    return cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))


def _timestamp(value: str, location: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SdrCalibrationError(f"{location}: invalid date-time") from error
    if parsed.tzinfo is None:
        raise SdrCalibrationError(f"{location}: date-time must include an offset")
    return parsed


def _number(value: int | float) -> str:
    if isinstance(value, int):
        if abs(value) > _MAX_EXACT_INTEGER:
            raise SdrCalibrationError("integer exceeds exact IEEE-754 range")
        return str(value)
    if not math.isfinite(value):
        raise SdrCalibrationError("non-finite JSON number")
    if value == 0.0:
        return "0"
    raw = repr(value).lower()
    if "e" not in raw:
        return raw
    mantissa, exponent_text = raw.split("e", 1)
    negative = mantissa.startswith("-")
    unsigned = mantissa[1:] if negative else mantissa
    before = unsigned.find(".")
    if before < 0:
        before = len(unsigned)
    digits = unsigned.replace(".", "")
    decimal_exponent = int(exponent_text) + before - 1
    prefix = "-" if negative else ""
    if -6 <= decimal_exponent < 21:
        position = decimal_exponent + 1
        if position <= 0:
            return prefix + "0." + ("0" * -position) + digits
        if position >= len(digits):
            return prefix + digits + ("0" * (position - len(digits)))
        return prefix + digits[:position] + "." + digits[position:]
    fraction = "" if len(digits) == 1 else "." + digits[1:]
    sign = "+" if decimal_exponent >= 0 else "-"
    return f"{prefix}{digits[0]}{fraction}e{sign}{abs(decimal_exponent)}"


def canonicalize(value: Any) -> bytes:
    """Return the RFC 8785 bytes used by the frozen upstream profile contract."""
    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, (int, float)):
        return _number(value).encode("ascii")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if isinstance(value, list):
        return b"[" + b",".join(canonicalize(item) for item in value) + b"]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise SdrCalibrationError("JSON object keys must be strings")
        keys = sorted(value, key=lambda key: key.encode("utf-16-be", errors="surrogatepass"))
        members = (canonicalize(key) + b":" + canonicalize(value[key]) for key in keys)
        return b"{" + b",".join(members) + b"}"
    raise SdrCalibrationError(f"unsupported JSON value: {type(value).__name__}")


def _semantic_validate(profile: dict[str, Any]) -> None:
    schema = profile["schema"]
    if schema["name"] != PROFILE_SCHEMA_NAME or schema["version"] != PROFILE_SCHEMA_VERSION:
        raise SdrCalibrationError(
            f"profile must use exactly {PROFILE_SCHEMA_NAME} {PROFILE_SCHEMA_VERSION}"
        )
    segments = profile["frequency_model"]["segments"]
    previous_maximum: float | None = None
    segment_ids: set[str] = set()
    for segment in segments:
        if segment["segment_id"] in segment_ids:
            raise SdrCalibrationError("frequency-model segment identifiers must be unique")
        segment_ids.add(segment["segment_id"])
        minimum = float(segment["minimum_frequency_hz"])
        maximum = float(segment["maximum_frequency_hz"])
        if maximum <= minimum:
            raise SdrCalibrationError("frequency-model segment bounds are invalid")
        if previous_maximum is not None and minimum <= previous_maximum:
            raise SdrCalibrationError(
                "frequency-model segments must be ordered and non-overlapping"
            )
        previous_maximum = maximum
    assurance = profile["assurance"]
    component_scores = [int(item["score"]) for item in assurance["components"]]
    quotient = int(assurance["reliability_quotient"])
    ceiling = int(assurance["reference_score_ceiling"])
    expected_quotient = min([ceiling, *component_scores])
    if quotient != expected_quotient:
        raise SdrCalibrationError("reliability quotient does not match weakest-component policy")
    if profile["profile_status"] == "qualification_capable" and quotient < 90:
        raise SdrCalibrationError("qualification-capable profile requires quotient of at least 90")
    expected_limiting = {
        item["name"] for item in assurance["components"] if int(item["score"]) == quotient
    }
    if ceiling == quotient:
        expected_limiting.add("reference_class_ceiling")
    if set(assurance["limiting_components"]) != expected_limiting:
        raise SdrCalibrationError("limiting components do not match reliability quotient")
    references = profile["provenance"]["reference_set"]
    weakest_reference = min(int(item["assurance_score_ceiling"]) for item in references)
    if weakest_reference != ceiling:
        raise SdrCalibrationError("reference ceiling does not match weakest reference")
    for reference in references:
        if int(reference["assurance_score_ceiling"]) != _REFERENCE_CEILINGS[reference["kind"]]:
            raise SdrCalibrationError("reference assurance ceiling contradicts its class")
    validity = profile["validity"]
    if _timestamp(validity["calibrated_at"], "validity.calibrated_at") >= _timestamp(
        validity["not_valid_after"], "validity.not_valid_after"
    ):
        raise SdrCalibrationError("profile expiration must follow calibration time")
    temperature = validity["temperature"]
    if not (
        float(temperature["minimum_c"])
        <= float(temperature["reference_c"])
        <= float(temperature["maximum_c"])
    ):
        raise SdrCalibrationError("temperature reference must be inside the validity domain")
    if profile.get("supersedes_profile_id") == profile["profile_id"]:
        raise SdrCalibrationError("profile cannot supersede itself")


def validate_profile_document(
    profile: dict[str, Any], *, verify_integrity: bool = True, source: str = "profile"
) -> dict[str, Any]:
    """Validate an in-memory frozen profile for authenticated plan bindings."""
    errors = sorted(
        Draft202012Validator(_schema(), format_checker=FormatChecker()).iter_errors(profile),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = "$" + "".join(f"[{item!r}]" for item in error.absolute_path)
        raise SdrCalibrationError(f"{source}:{location}: {error.message}")
    _semantic_validate(profile)
    if verify_integrity:
        payload = dict(profile)
        del payload["integrity"]
        digest = hashlib.sha256(canonicalize(payload)).hexdigest()
        if digest != profile["integrity"]["sha256"]:
            raise SdrCalibrationError("profile SHA-256 does not match its canonical payload")
        if "signature" in profile["integrity"]:
            raise SdrCalibrationError(
                "signed profiles require a configured Ed25519 verifier and trust-store policy"
            )
    return profile


def load_profile(path: Path, *, verify_integrity: bool = True) -> dict[str, Any]:
    """Load and validate exactly one frozen native SDR Calibration Profile."""
    return validate_profile_document(
        _read_object(path, "SDR calibration profile"),
        verify_integrity=verify_integrity,
        source=str(path),
    )


def validate_application_request_document(
    request: dict[str, Any], *, source: str = "application request"
) -> dict[str, Any]:
    """Validate an in-memory run-specific receiver application request."""
    errors = sorted(
        Draft202012Validator(_application_schema(), format_checker=FormatChecker()).iter_errors(
            request
        ),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = "$" + "".join(f"[{item!r}]" for item in error.absolute_path)
        raise SdrCalibrationError(f"{source}:{location}: {error.message}")
    _timestamp(request["evaluated_at"], "evaluated_at")
    return request


def load_application_request(path: Path) -> dict[str, Any]:
    """Load the harness-owned, run-specific application request."""
    return validate_application_request_document(
        _read_object(path, "SDR calibration application request"), source=str(path)
    )


def _failed(profile: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evidence_type": "sdr_calibration_application",
        "status": status,
        "reason": reason,
        "profile_id": profile["profile_id"],
        "profile_schema_version": profile["schema"]["version"],
        "profile_integrity_sha256": profile["integrity"]["sha256"],
        "upstream_revision": UPSTREAM_REVISION,
        "upstream_schema_sha256": UPSTREAM_SCHEMA_SHA256,
        "qualification_usable": False,
    }


def _normalized_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(configuration)
    for key in (
        "bandwidth_hz",
        "driver_version",
        "firmware_version",
        "antenna_port",
        "tuner_path",
    ):
        normalized.setdefault(key, None)
    normalized.setdefault("binding_extension", {})
    return normalized


def evaluate_profile(profile: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Apply the frozen upstream evaluation semantics without touching hardware."""
    evaluated_at = _timestamp(request["evaluated_at"], "evaluated_at")
    validity = profile["validity"]
    calibrated_at = _timestamp(validity["calibrated_at"], "validity.calibrated_at")
    expires_at = _timestamp(validity["not_valid_after"], "validity.not_valid_after")
    if profile["profile_status"] == "revoked":
        return _failed(profile, "revoked", "profile is revoked")
    if evaluated_at < calibrated_at:
        return _failed(profile, "invalid_request", "evaluation predates calibration")
    if evaluated_at >= expires_at:
        return _failed(profile, "profile_expired", "profile has expired")
    if request["device"] != profile["device"]:
        return _failed(profile, "identity_mismatch", "device identity does not match")
    if _normalized_configuration(request["configuration"]) != _normalized_configuration(
        profile["configuration"]
    ):
        return _failed(profile, "configuration_mismatch", "binding configuration does not match")
    temperature = float(request["temperature_c"])
    domain = validity["temperature"]
    if temperature < float(domain["minimum_c"]) or temperature > float(domain["maximum_c"]):
        return _failed(profile, "outside_temperature_domain", "temperature is outside domain")
    if int(request["warmup_seconds"]) < int(validity["minimum_warmup_seconds"]):
        return _failed(profile, "insufficient_warmup", "minimum warm-up is not satisfied")
    indicated = float(request["indicated_frequency_hz"])
    segment = next(
        (
            item
            for item in profile["frequency_model"]["segments"]
            if float(item["minimum_frequency_hz"])
            <= indicated
            <= float(item["maximum_frequency_hz"])
        ),
        None,
    )
    if segment is None:
        return _failed(profile, "outside_frequency_domain", "frequency is outside every segment")
    if int(profile["assurance"]["reliability_quotient"]) < int(
        request["required_reliability_quotient"]
    ):
        return _failed(
            profile, "assurance_requirement_not_met", "reliability quotient is below requirement"
        )
    model = segment["model"]
    if model["type"] == "constant_ppm":
        indicated_error = float(model["error_ppm"]) * indicated / 1_000_000.0
    elif model["type"] == "linear":
        indicated_error = (
            float(model["intercept_error_hz"])
            + float(model["slope_ppm"])
            * (indicated - float(model["reference_frequency_hz"]))
            / 1_000_000.0
        )
    else:
        return _failed(profile, "unsupported_model", "segment model is unsupported")
    uncertainty = segment["uncertainty"]
    expanded = (
        float(uncertainty["model"]["base_hz"])
        + float(uncertainty["model"]["ppm_component"]) * indicated / 1_000_000.0
    )
    if uncertainty["kind"] == "standard":
        expanded *= float(uncertainty["coverage_factor"])
    maximum = request.get("maximum_expanded_uncertainty_hz")
    if maximum is not None and expanded > float(maximum):
        return _failed(
            profile, "uncertainty_requirement_not_met", "expanded uncertainty exceeds requirement"
        )
    estimated_true = indicated - indicated_error
    target = request.get("target_frequency_hz")
    status = profile["profile_status"]
    return {
        "schema_version": 1,
        "evidence_type": "sdr_calibration_application",
        "status": status,
        "reason": "profile evaluated within its validated domain",
        "profile_id": profile["profile_id"],
        "profile_schema_version": profile["schema"]["version"],
        "profile_integrity_sha256": profile["integrity"]["sha256"],
        "upstream_revision": UPSTREAM_REVISION,
        "upstream_schema_sha256": UPSTREAM_SCHEMA_SHA256,
        "segment_id": segment["segment_id"],
        "indicated_frequency_hz": indicated,
        "indicated_error_hz": indicated_error,
        "estimated_true_frequency_hz": estimated_true,
        "expanded_uncertainty_hz": expanded,
        "target_frequency_hz": target,
        "target_offset_hz": None if target is None else estimated_true - float(target),
        "reliability_quotient": profile["assurance"]["reliability_quotient"],
        "qualification_usable": status == "qualification_capable",
    }
