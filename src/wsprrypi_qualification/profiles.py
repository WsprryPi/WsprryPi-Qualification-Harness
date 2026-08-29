"""JSON Schema and semantic profile loading."""

from __future__ import annotations

import json
import math
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Never, TypeAlias, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from wsprrypi_qualification.models import (
    AuthorizationScope,
    Backend,
    BenchProfile,
    PathType,
    QualificationGates,
    ReceiverConfig,
    ReceiverRunAuthorization,
    ReceiverRunLimits,
    ReceiverRunProfile,
    RfPathConfig,
    StoppingProcedure,
    TestProfile,
    TransmitterConfig,
    Transport,
    WsprIdentity,
)

Profile: TypeAlias = BenchProfile | TestProfile | ReceiverRunProfile
FORBIDDEN_CONFIRMATION_KEYS = {"confirmed", "operator_verified", "approved", "enable_rf"}


class ProfileError(ValueError):
    """Actionable profile loading or validation failure."""


def _reject_json_constant(value: str) -> Never:
    raise ValueError(f"non-standard JSON numeric constant is forbidden: {value}")


def _validate_finite_numbers(value: Any, source: Path, location: str = "$") -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ProfileError(f"{source}:{location}: numeric value must be finite")
    if isinstance(value, dict):
        for key, child in value.items():
            _validate_finite_numbers(child, source, f"{location}[{key!r}]")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_finite_numbers(child, source, f"{location}[{index}]")


def _schema(name: str) -> dict[str, Any]:
    resource = files("wsprrypi_qualification.schemas").joinpath(f"{name}-profile.schema.json")
    return cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except OSError as error:
        raise ProfileError(f"{path}: cannot read profile: {error}") from error
    except json.JSONDecodeError as error:
        raise ProfileError(
            f"{path}:{error.lineno}:{error.colno}: invalid JSON: {error.msg}"
        ) from error
    except ValueError as error:
        raise ProfileError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ProfileError(f"{path}: profile root must be a JSON object")
    _validate_finite_numbers(value, path)
    return cast(dict[str, Any], value)


def _find_forbidden(value: Any, location: tuple[str, ...] = ()) -> tuple[str, ...] | None:
    if isinstance(value, dict):
        for key, child in value.items():
            current = (*location, str(key))
            if key in FORBIDDEN_CONFIRMATION_KEYS:
                return current
            found = _find_forbidden(child, current)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_forbidden(child, (*location, str(index)))
            if found:
                return found
    return None


def _validate(document: dict[str, Any], schema_name: str, source: Path) -> None:
    forbidden = _find_forbidden(document)
    if forbidden:
        raise ProfileError(
            f"{source}:$.{'/'.join(forbidden)}: runtime confirmation is forbidden in profiles"
        )
    validator = Draft202012Validator(_schema(schema_name), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    if errors:
        error: ValidationError = errors[0]
        location = "$" + "".join(f"[{item!r}]" for item in error.absolute_path)
        raise ProfileError(f"{source}:{location}: {error.message}")


def load_bench_profile(path: Path) -> BenchProfile:
    document = _read_json(path)
    _validate(document, "bench", path)
    receiver = document["receiver"]
    rf_path = document["rf_path"]
    from wsprrypi_qualification.run_ids import validate_identifier

    try:
        validate_identifier(document["bench_id"], "bench_id")
    except ValueError as error:
        raise ProfileError(f"{path}: {error}") from error
    if receiver["bandwidth_hz"] > receiver["sample_rate_hz"]:
        raise ProfileError(f"{path}: receiver bandwidth must not exceed sample rate")
    return BenchProfile(
        schema_version=document["schema_version"],
        bench_id=document["bench_id"],
        receiver=ReceiverConfig(
            transport=Transport(receiver["transport"]),
            host=receiver.get("host"),
            driver=receiver["driver"],
            serial=receiver.get("serial"),
            channel=receiver.get("channel", 0),
            sample_rate_hz=receiver["sample_rate_hz"],
            bandwidth_hz=receiver["bandwidth_hz"],
            sample_format=receiver["sample_format"],
            agc=receiver["agc"],
            bias_tee=receiver.get("bias_tee", False),
        ),
        rf_path=RfPathConfig(
            path_type=PathType(rf_path["path_type"]),
            antenna_connected=rf_path["antenna_connected"],
            termination_ohms=rf_path.get("termination_ohms"),
            attenuation_db=rf_path.get("attenuation_db"),
            filter_description=rf_path["filter_description"],
            safe_input_description=rf_path["safe_input_description"],
        ),
    )


def load_test_profile(path: Path) -> TestProfile:
    document = _read_json(path)
    _validate(document, "test", path)
    transmitter = document["transmitter"]
    identity = document["identity"]
    gates = document["gates"]
    stopping = document["stopping_procedure"]
    from wsprrypi_qualification.run_ids import validate_identifier

    try:
        validate_identifier(document["test_id"], "test_id")
    except ValueError as error:
        raise ProfileError(f"{path}: {error}") from error
    if gates["required_consecutive_decodes"] > document["frame_count"]:
        raise ProfileError(f"{path}: required consecutive decodes must not exceed frame count")
    backend = transmitter["backend"]
    backend_requirements = {
        "si5351": {"i2c_bus", "i2c_address", "reference_frequency_hz"},
        "gpio": {"gpio_pin", "pacing_clocks"},
    }
    missing = sorted(backend_requirements.get(backend, set()) - transmitter.keys())
    if missing:
        raise ProfileError(f"{path}: {backend} transmitter is missing {', '.join(missing)}")
    return TestProfile(
        schema_version=document["schema_version"],
        test_id=document["test_id"],
        transmitter=TransmitterConfig(
            transport=Transport(transmitter["transport"]),
            host=transmitter["host"],
            backend=Backend(transmitter["backend"]),
            output=transmitter["output"],
            model=transmitter.get("model"),
            hardware_description=transmitter.get("hardware_description"),
            oscillator_description=transmitter.get("oscillator_description"),
            reference_frequency_hz=transmitter.get("reference_frequency_hz"),
            source_revision=transmitter.get("source_revision"),
            submodule_revision=transmitter.get("submodule_revision"),
            i2c_bus=transmitter.get("i2c_bus"),
            i2c_address=transmitter.get("i2c_address"),
            drive_ma=transmitter.get("drive_ma"),
            gpio_pin=transmitter.get("gpio_pin"),
            power_level=transmitter.get("power_level"),
            pacing_clocks=transmitter.get("pacing_clocks"),
        ),
        band=document["band"],
        frequency_hz=document["frequency_hz"],
        receiver_center_hz=document["receiver_center_hz"],
        receiver_gain_db=document["receiver_gain_db"],
        ppm=document.get("ppm"),
        identity=WsprIdentity(identity["callsign"], identity["grid"], identity["power_dbm"]),
        gates=QualificationGates(
            gates["carrier_offset_max_hz"],
            gates["frequency_acquisition_half_width_hz"],
            gates["best_20hz_share_min"],
            gates["required_consecutive_decodes"],
        ),
        stopping_procedure=StoppingProcedure(
            transmitter_termination=stopping["transmitter_termination"],
            receiver_termination=stopping["receiver_termination"],
            operator_abort=stopping["operator_abort"],
            cleanup_expectation=stopping["cleanup_expectation"],
            emergency_stop_note=stopping["emergency_stop_note"],
        ),
        frame_count=document.get("frame_count", 3),
        bounded_duration_s=document.get("bounded_duration_s"),
        random_offset_enabled=document.get("random_offset_enabled", False),
    )


def load_receiver_run_profile(path: Path) -> ReceiverRunProfile:
    document = _read_json(path)
    _validate(document, "receiver-run", path)
    receiver = document["receiver"]
    rf_path = document["rf_path"]
    limits = document["limits"]
    authorization = document["authorization"]
    from wsprrypi_qualification.run_ids import validate_identifier, validate_run_id

    try:
        validate_run_id(document["run_id"])
        validate_identifier(document["bench_id"], "bench_id")
    except ValueError as error:
        raise ProfileError(f"{path}: {error}") from error
    if receiver["bandwidth_hz"] > receiver["sample_rate_hz"]:
        raise ProfileError(f"{path}: receiver bandwidth must not exceed sample rate")
    if limits["sample_count"] != receiver["sample_rate_hz"] * document["duration_s"]:
        raise ProfileError(f"{path}: sample count must equal sample rate times duration")
    if limits["helper_deadline_s"] <= document["duration_s"]:
        raise ProfileError(f"{path}: helper deadline must exceed capture duration")
    if limits["external_deadline_s"] <= limits["helper_deadline_s"]:
        raise ProfileError(f"{path}: external deadline must exceed helper deadline")

    return ReceiverRunProfile(
        schema_version=document["schema_version"],
        run_id=document["run_id"],
        bench_id=document["bench_id"],
        receiver=ReceiverConfig(
            transport=Transport(receiver["transport"]),
            host=receiver.get("host"),
            driver=receiver["driver"],
            serial=receiver.get("serial"),
            channel=receiver["channel"],
            sample_rate_hz=receiver["sample_rate_hz"],
            bandwidth_hz=receiver["bandwidth_hz"],
            sample_format=receiver["sample_format"],
            agc=receiver["agc"],
            bias_tee=receiver["bias_tee"],
        ),
        center_frequency_hz=document["center_frequency_hz"],
        gain_db=document["gain_db"],
        duration_s=document["duration_s"],
        rf_path=RfPathConfig(
            path_type=PathType(rf_path["path_type"]),
            antenna_connected=rf_path["antenna_connected"],
            termination_ohms=rf_path.get("termination_ohms"),
            attenuation_db=rf_path.get("attenuation_db"),
            filter_description=rf_path["filter_description"],
            safe_input_description=rf_path["safe_input_description"],
        ),
        limits=ReceiverRunLimits(
            sample_count=limits["sample_count"],
            read_timeout_us=limits["read_timeout_us"],
            helper_deadline_s=limits["helper_deadline_s"],
            external_deadline_s=limits["external_deadline_s"],
        ),
        authorization=ReceiverRunAuthorization(
            scope=AuthorizationScope(authorization["scope"]),
            reference=authorization["reference"],
            recorded_utc=datetime.fromisoformat(
                authorization["recorded_utc"].replace("Z", "+00:00")
            ),
        ),
        ownership_and_cleanup=document["ownership_and_cleanup"],
    )


def load_profile(path: Path, kind: str) -> Profile:
    if kind == "bench":
        return load_bench_profile(path)
    if kind == "test":
        return load_test_profile(path)
    if kind == "receiver-run":
        return load_receiver_run_profile(path)
    raise ProfileError(f"unsupported profile kind: {kind}")
