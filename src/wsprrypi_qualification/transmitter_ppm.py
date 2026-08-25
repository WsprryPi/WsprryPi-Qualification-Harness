"""Deterministic transmitter-side PPM resolution and provenance."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any


class TransmitterPpmError(ValueError):
    """Transmitter correction inputs cannot be resolved safely."""


def _ppm(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise TransmitterPpmError(f"{label} must be numeric")
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise TransmitterPpmError(f"{label} must be numeric") from error
    if not math.isfinite(result) or not -200 <= result <= 200:
        raise TransmitterPpmError(f"{label} must be finite and within +/-200 ppm")
    return result


def resolve_transmitter_ppm(
    sources: list[dict[str, Any]],
    harness_offset_ppm: object,
    *,
    transmitter_host: str,
    backend: str,
    resolved_at: datetime,
) -> dict[str, Any]:
    """Resolve one absolute host correction plus one additive harness delta."""
    if not sources:
        raise TransmitterPpmError("transmitter PPM source is missing")
    normalized: list[dict[str, Any]] = []
    priority = {"tracked_host_ppm": 3, "manual_host_ppm": 2, "backend_native_ppm": 1}
    for index, source in enumerate(sources):
        if not isinstance(source, dict) or source.get("source_type") not in priority:
            raise TransmitterPpmError("transmitter PPM source type is unsupported")
        if source.get("host") != transmitter_host or source.get("backend") != backend:
            raise TransmitterPpmError("transmitter PPM source identity does not match the plan")
        kind = source["source_type"]
        value = _ppm(source.get("value_ppm"), f"transmitter PPM source {index}")
        acquired = source.get("acquired_utc")
        if kind == "tracked_host_ppm":
            if not isinstance(acquired, str) or not isinstance(source.get("maximum_age_s"), int):
                raise TransmitterPpmError("tracked transmitter PPM requires age provenance")
            try:
                instant = datetime.fromisoformat(acquired.replace("Z", "+00:00"))
            except ValueError as error:
                raise TransmitterPpmError("tracked transmitter PPM time is malformed") from error
            age = (resolved_at.astimezone(UTC) - instant.astimezone(UTC)).total_seconds()
            if age < 0 or age > source["maximum_age_s"]:
                raise TransmitterPpmError("tracked transmitter PPM is stale")
        normalized.append(
            {
                "source_type": kind,
                "source_location": source.get(
                    "source_location", "saved complete-test configuration"
                ),
                "raw_value": source.get("value_ppm"),
                "normalized_value_ppm": value,
                "units": "ppm",
                "sign_convention": "value passed unchanged to the selected WsprryPi backend",
                "host": transmitter_host,
                "backend": backend,
                "acquired_utc": acquired,
                "decision": "considered",
            }
        )
    winner_priority = max(priority[entry["source_type"]] for entry in normalized)
    winners = [entry for entry in normalized if priority[entry["source_type"]] == winner_priority]
    if len(winners) != 1:
        raise TransmitterPpmError("transmitter PPM sources are ambiguous or contradictory")
    winner = winners[0]
    for entry in normalized:
        entry["decision"] = "applied" if entry is winner else "superseded"
    offset = _ppm(harness_offset_ppm, "harness transmitter PPM offset")
    effective = winner["normalized_value_ppm"] + offset
    if not -200 <= effective <= 200:
        raise TransmitterPpmError("effective transmitter PPM is outside +/-200 ppm")
    normalized.append(
        {
            "source_type": "harness_residual_offset",
            "source_location": "complete-test --transmitter-ppm-offset",
            "raw_value": harness_offset_ppm,
            "normalized_value_ppm": offset,
            "units": "ppm",
            "sign_convention": "additive delta in WsprryPi backend sign convention",
            "host": transmitter_host,
            "backend": backend,
            "acquired_utc": resolved_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "decision": "applied",
        }
    )
    return {
        "algorithm": "host_absolute_precedence_plus_harness_delta_v1",
        "resolved_utc": resolved_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "transmitter_host": transmitter_host,
        "backend": backend,
        "contributors": normalized,
        "host_correction_ppm": winner["normalized_value_ppm"],
        "harness_offset_ppm": offset,
        "effective_correction_ppm": effective,
        "derivation": (
            f"{winner['normalized_value_ppm']:.15g} ppm host absolute + "
            f"{offset:.15g} ppm harness delta = {effective:.15g} ppm effective"
        ),
        "application": "exactly_once_as_backend_ppm_argument",
        "receiver_calibration_separate": True,
    }
