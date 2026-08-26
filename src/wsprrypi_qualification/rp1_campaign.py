"""Sealed hardware-free RP1 five-mode complete-test rehearsal composition."""

from __future__ import annotations

import json
import math
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from wsprrypi_qualification.application_shims import (
    ApplicationIdentity,
    CwProtocol,
    ProtocolMode,
    ToneProtocol,
    WsprProtocol,
    WsprryPiBackendConfig,
    WsprryPiShim,
    validate_application_plan,
)
from wsprrypi_qualification.keyed_session_contracts import canonical_sha256
from wsprrypi_qualification.offline import validate_document, write_json_new
from wsprrypi_qualification.rp1_contracts import route_contract, validate_role_bindings

RP1_MODE_ORDER = ("TONE", "WSPR", "QRSS", "FSKCW", "DFCW")


class Rp1CampaignError(ValueError):
    """A hardware-free RP1 campaign cannot be composed safely."""


def _load_configuration(path: Path) -> dict[str, Any]:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise Rp1CampaignError("RP1 rehearsal configuration must be an existing absolute file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Rp1CampaignError("RP1 rehearsal configuration is unreadable") from error
    if not isinstance(document, dict):
        raise Rp1CampaignError("RP1 rehearsal configuration must be an object")
    validate_document(document, "rp1-complete-test-rehearsal-config.schema.json")
    return cast(dict[str, Any], document)


def _ppm(
    configuration: dict[str, Any],
    route: str,
    residual_ppm: float,
    manual_ppm: float | None,
) -> tuple[float, dict[str, Any]]:
    source = dict(configuration["transmitter_ppm_source"])
    if manual_ppm is not None:
        source.update(
            {
                "value_ppm": manual_ppm,
                "provenance": "operator-supplied measured RP1 source via --gpio-manual-ppm",
            }
        )
    expected = route_contract(route)
    if (
        source["host"] != configuration["host"]
        or source["backend"] != "rp1_gpclk"
        or source["route"] != route
        or source["compatibility_id"] != expected["compatibility_id"]
        or source["application_path"] != "--gpio-manual-ppm"
    ):
        raise Rp1CampaignError("RP1 transmitter PPM provenance is wrong-host or wrong-route")
    value = float(source["value_ppm"])
    if not math.isfinite(value) or not math.isfinite(residual_ppm):
        raise Rp1CampaignError("RP1 PPM values must be finite")
    effective = value + residual_ppm
    if not -200 <= effective <= 200:
        raise Rp1CampaignError("effective RP1 PPM is outside +/-200")
    resolution = {
        "source": source,
        "harness_residual_ppm": residual_ppm,
        "effective_ppm": effective,
        "application_count": 1,
        "dynamic_system_clock_estimate": False,
        "receiver_calibration_separate": True,
    }
    return effective, resolution


def _application_config(
    configuration: dict[str, Any], route: str, effective_ppm: float
) -> WsprryPiBackendConfig:
    expected = route_contract(route)
    gpio = expected["gpio"]
    if not isinstance(gpio, int):
        raise Rp1CampaignError("RP1 route contract GPIO must be an integer")
    return WsprryPiBackendConfig(
        output=str(expected["output"]),
        ppm=effective_ppm,
        drive_or_power_level=int(configuration["rp1_identity"]["power_level"]),
        gpio_pin=gpio,
        rp1_route=route,
        endpoint=str(expected["endpoint"]),
        compatibility_id=str(expected["compatibility_id"]),
        abi_version=3,
        finite_tone_required=True,
        development_enrollment="Experimental",
        live_output_required=True,
        rp1_drive_ma=int(configuration["rp1_identity"]["rp1_drive_ma"]),
    )


def _protocol(mode: str, configuration: dict[str, Any]) -> object:
    frequency = float(configuration["frequency_hz"])
    if mode == "TONE":
        return ToneProtocol(frequency)
    if mode == "WSPR":
        return WsprProtocol(
            configuration["callsign"],
            configuration["grid"],
            configuration["power_dbm"],
            frequency,
            3,
            1500.0,
        )
    separation = 0.0 if mode == "QRSS" else float(configuration["separation_hz"])
    secondary = None if mode == "QRSS" else frequency - separation
    return CwProtocol(
        ProtocolMode(mode.lower()),
        configuration["message"],
        float(configuration["dot_seconds"]),
        frequency,
        secondary,
    )


def compose_rp1_rehearsal(
    configuration_path: Path,
    route: str,
    *,
    residual_ppm: float = 0.0,
    manual_ppm: float | None = None,
    carrier_offset_max_hz: float = 100.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compose five authenticated plans without constructing any production adapter."""
    configuration = _load_configuration(configuration_path)
    expected = route_contract(route)
    if not math.isfinite(carrier_offset_max_hz) or carrier_offset_max_hz < 0:
        raise Rp1CampaignError("carrier tolerance must be finite and non-negative")
    rp1_identity = configuration["rp1_identity"]
    if any(rp1_identity.get(name) != value for name, value in expected.items()):
        raise Rp1CampaignError("RP1 configuration identity is incomplete or wrong-route")
    roles = {
        "topology": "same_host_roles",
        "transmitter_role": configuration["transmitter_role"],
        "receiver_role": configuration["receiver_role"],
    }
    validate_role_bindings(roles)
    if (
        roles["transmitter_role"]["host"] != configuration["host"]
        or roles["receiver_role"]["host"] != configuration["host"]
        or configuration["receiver"]["host"] != configuration["host"]
    ):
        raise Rp1CampaignError("RP1 same-host role or receiver identity differs from host")
    effective_ppm, ppm_resolution = _ppm(configuration, route, residual_ppm, manual_ppm)
    identity = ApplicationIdentity(
        "wsprrypi",
        configuration["wsprrypi"]["executable"],
        configuration["wsprrypi"]["source_revision"],
        configuration["wsprrypi"]["component_revision"],
    )
    shim = WsprryPiShim(
        identity,
        backend="rp1_gpclk",
        backend_config=_application_config(configuration, route, effective_ppm),
    )
    plans: list[dict[str, Any]] = []
    stamp = (now or datetime.now(UTC)).astimezone(UTC)
    campaign_id = f"rp1-{stamp.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}-{route}"
    for mode in RP1_MODE_ORDER:
        application = shim.resolve_plan(
            f"{campaign_id}-{mode.lower()}", cast(Any, _protocol(mode, configuration))
        ).to_document()
        validate_application_plan(application)
        lifecycle = {
            "endpoint": expected["endpoint"],
            "route": route,
            "compatibility_id": expected["compatibility_id"],
            "abi_version": 3,
            "finite_tone_required": True,
            "tone_operation": "FINITE" if mode == "TONE" else "NOT_APPLICABLE",
            "tone_duration_ns": 1_000_000_000 if mode == "TONE" else None,
            "live_output_required": True,
            "cleanup": "lease_release_endpoint_close_gpio_clock_dma_quiescence",
            "terminal_silence_required": True,
        }
        entry = {
            "mode": mode,
            "route": route,
            "route_mode_id": f"{route}:{mode}",
            "application_plan": application,
            "rp1_lifecycle_contract": lifecycle,
            "receiver": configuration["receiver"],
            "rf_path": configuration["rf_path"],
            "transmitter_ppm_resolution": ppm_resolution,
            "carrier_offset_max_hz": carrier_offset_max_hz,
            "authorization": "not_authorized_hardware_free_rehearsal",
            "qualification_claim": False,
        }
        entry["plan_sha256"] = canonical_sha256(entry)
        plans.append(entry)
    document = {
        "schema_version": 1,
        "evidence_type": "rp1_complete_test_rehearsal",
        "campaign_id": campaign_id,
        "execution_policy": "hardware_free",
        "production_adapters_constructed": False,
        "external_calls": 0,
        "rf_emitted": False,
        "host": configuration["host"],
        "topology": "same_host_roles",
        "roles": roles,
        "backend": "rp1_gpclk",
        "route": route,
        "rp1_identity": rp1_identity,
        "ppm_resolution": ppm_resolution,
        "carrier_offset_max_hz": carrier_offset_max_hz,
        "mode_order": list(RP1_MODE_ORDER),
        "plans": plans,
        "qualification_claim": False,
    }
    validate_rp1_rehearsal(document)
    return document


def validate_rp1_rehearsal(document: dict[str, Any]) -> dict[str, Any]:
    validate_document(document, "rp1-complete-test-rehearsal.schema.json")
    if document["mode_order"] != list(RP1_MODE_ORDER):
        raise Rp1CampaignError("RP1 rehearsal mode order changed")
    expected = route_contract(document["route"])
    if any(document["rp1_identity"].get(name) != value for name, value in expected.items()):
        raise Rp1CampaignError("RP1 rehearsal identity is wrong-route")
    validate_role_bindings(document["roles"])
    digests: set[str] = set()
    identities: set[str] = set()
    for entry in document["plans"]:
        if entry["route"] != document["route"] or entry["mode"] not in RP1_MODE_ORDER:
            raise Rp1CampaignError("RP1 subordinate plan route or mode changed")
        validate_application_plan(entry["application_plan"])
        backend = entry["application_plan"]["backend_contract"]
        expected_application = {
            "output": expected["output"],
            "gpio_pin": expected["gpio"],
            "rp1_route": document["route"],
            "endpoint": expected["endpoint"],
            "compatibility_id": expected["compatibility_id"],
            "abi_version": 3,
            "finite_tone_required": True,
            "development_enrollment": "Experimental",
            "live_output_required": True,
        }
        if any(backend.get(name) != value for name, value in expected_application.items()):
            raise Rp1CampaignError("RP1 subordinate application identity is wrong-route")
        if (
            entry["receiver"] != document["plans"][0]["receiver"]
            or entry["rf_path"] != document["plans"][0]["rf_path"]
            or entry["transmitter_ppm_resolution"] != document["ppm_resolution"]
            or entry["carrier_offset_max_hz"] != document["carrier_offset_max_hz"]
        ):
            raise Rp1CampaignError("RP1 subordinate campaign binding diverged")
        arguments = entry["application_plan"]["arguments"]
        if (
            arguments.count("--gpio-manual-ppm") != 1
            or arguments.count("--no-system-clock-frequency-estimate") != 1
        ):
            raise Rp1CampaignError("RP1 subordinate plan does not apply PPM exactly once")
        if entry["mode"] == "TONE" and (
            entry["rp1_lifecycle_contract"]["tone_operation"] != "FINITE"
            or entry["rp1_lifecycle_contract"]["tone_duration_ns"] != 1_000_000_000
        ):
            raise Rp1CampaignError("RP1 campaign entry TONE is not ABI-v3 finite")
        observed = entry["plan_sha256"]
        payload = dict(entry)
        payload.pop("plan_sha256")
        if observed != canonical_sha256(payload):
            raise Rp1CampaignError("RP1 subordinate plan digest changed")
        digests.add(observed)
        identities.add(entry["route_mode_id"])
    if len(digests) != 5 or len(identities) != 5:
        raise Rp1CampaignError("RP1 subordinate plans are reused or duplicated")
    return document


def write_rp1_rehearsal(document: dict[str, Any], destination: Path) -> Path:
    validate_rp1_rehearsal(document)
    destination.mkdir(parents=True, exist_ok=False)
    write_json_new(
        destination / "rehearsal.json",
        document,
        schema_name="rp1-complete-test-rehearsal.schema.json",
    )
    return destination


def configured_output_parent(configuration_path: Path) -> Path:
    configuration = _load_configuration(configuration_path)
    path = Path(configuration["output_parent"])
    if not path.is_absolute() or path.is_symlink():
        raise Rp1CampaignError("RP1 rehearsal output parent must be absolute and non-symlinked")
    return path
