"""Fail-closed validation for mode-specific CW qualification evidence."""

from __future__ import annotations

import hashlib
import json
import math
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class CwQualificationError(ValueError):
    """CW evidence is malformed, inconsistent, or overclaims qualification."""


def _schema() -> dict[str, Any]:
    document = json.loads(
        files("wsprrypi_qualification.schemas")
        .joinpath("cw-qualification-analysis.schema.json")
        .read_text(encoding="utf-8")
    )
    assert isinstance(document, dict)
    return document


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CwQualificationError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise CwQualificationError(f"{field} must be a finite number")
    return result


def _artifact_matches(capture: dict[str, Any], evidence_path: Path) -> None:
    artifact = Path(capture["path"])
    if not artifact.is_absolute():
        artifact = evidence_path.parent / artifact
    try:
        stat = artifact.stat()
    except OSError as error:
        raise CwQualificationError(f"capture artifact is unavailable: {artifact}") from error
    if not artifact.is_file() or stat.st_size != capture["size_bytes"]:
        raise CwQualificationError("capture artifact size does not match evidence")
    digest = hashlib.sha256()
    with artifact.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != capture["sha256"]:
        raise CwQualificationError("capture artifact SHA-256 does not match evidence")


def _derive_status(document: dict[str, Any]) -> tuple[str, list[str]]:
    mode = document["mode"]
    thresholds = document["thresholds"]
    _finite(document["capture"]["sample_rate_hz"], "capture.sample_rate_hz")
    primary = _finite(thresholds["primary_frequency_hz"], "primary_frequency_hz")
    secondary_raw = thresholds["secondary_frequency_hz"]
    secondary = None if secondary_raw is None else _finite(secondary_raw, "secondary_frequency_hz")
    frequency_tolerance = _finite(thresholds["frequency_tolerance_hz"], "frequency_tolerance_hz")
    spacing_tolerance = _finite(thresholds["spacing_tolerance_hz"], "spacing_tolerance_hz")
    contrast_minimum = _finite(thresholds["minimum_key_contrast_db"], "minimum_key_contrast_db")
    timing_tolerance = _finite(thresholds["timing_tolerance_s"], "timing_tolerance_s")

    shifted = mode in {"fskcw", "dfcw"}
    if shifted != (secondary is not None):
        raise CwQualificationError("secondary frequency must exist exactly for shifted-CW modes")
    if shifted:
        assert secondary is not None
        if abs(primary - secondary) <= spacing_tolerance:
            raise CwQualificationError(
                "shifted-CW requested tones are not distinctly representable"
            )

    observations: dict[str, list[dict[str, Any]]] = {}
    for observation in document["observations"]:
        observations.setdefault(observation["kind"], []).append(observation)

    required = {"primary"}
    if mode == "qrss":
        required |= {"key_down", "key_up", "transition"}
    elif shifted:
        required |= {"secondary", "transition"}
    allowed = required
    unexpected = sorted(set(observations) - allowed)
    if unexpected:
        raise CwQualificationError(
            "observations are not valid for this mode: " + ", ".join(unexpected)
        )
    duplicates = sorted(kind for kind, values in observations.items() if len(values) != 1)
    if duplicates:
        raise CwQualificationError(
            "each observation kind must appear exactly once: " + ", ".join(duplicates)
        )
    missing = sorted(kind for kind in required if kind not in observations)
    causes: list[str] = [f"missing_{kind}_observation" for kind in missing]

    for kind, values in observations.items():
        for observation in values:
            if (
                abs(_finite(observation["timing_error_s"], f"{kind}.timing_error_s"))
                > timing_tolerance
            ):
                causes.append(f"{kind}_timing")

    expected_frequencies = {"primary": primary, "key_down": primary}
    if secondary is not None:
        expected_frequencies["secondary"] = secondary
    for kind, expected in expected_frequencies.items():
        for observation in observations.get(kind, []):
            measured = observation["measured_frequency_hz"]
            if (
                measured is None
                or abs(_finite(measured, f"{kind}.measured_frequency_hz") - expected)
                > frequency_tolerance
            ):
                causes.append(f"{kind}_frequency")
    for kind in {"primary", "secondary"} if shifted else {"primary"}:
        for observation in observations.get(kind, []):
            contrast = observation["key_contrast_db"]
            if contrast is None or _finite(contrast, f"{kind}.key_contrast_db") < contrast_minimum:
                causes.append(f"{kind}_contrast")
            if not observation["carrier_continuous"]:
                causes.append(f"{kind}_carrier_missing")

    if mode == "qrss":
        for observation in observations.get("key_down", []):
            contrast = observation["key_contrast_db"]
            if contrast is None or _finite(contrast, "key_down.key_contrast_db") < contrast_minimum:
                causes.append("key_contrast")
            if not observation["carrier_continuous"]:
                causes.append("key_down_carrier_missing")
        for observation in observations.get("key_up", []):
            if (
                observation["measured_frequency_hz"] is not None
                or observation["carrier_continuous"]
            ):
                causes.append("key_up_not_silent")
    elif shifted:
        assert secondary is not None
        measured_primary = observations.get("primary", [{}])[0].get("measured_frequency_hz")
        measured_secondary = observations.get("secondary", [{}])[0].get("measured_frequency_hz")
        if measured_primary is not None and measured_secondary is not None:
            measured_spacing = abs(
                _finite(measured_primary, "primary frequency")
                - _finite(measured_secondary, "secondary frequency")
            )
            if abs(measured_spacing - abs(primary - secondary)) > spacing_tolerance:
                causes.append("tone_spacing")
        for observation in observations.get("transition", []):
            if not observation["carrier_continuous"]:
                causes.append("carrier_interruption")

    causes = sorted(set(causes))
    if not document["cleanup_verified"]:
        return "cleanup_failed", sorted(set([*causes, "cleanup_unverified"]))
    if document["capture"]["synthetic"]:
        return "inconclusive", sorted(set([*causes, "synthetic_capture"]))
    if causes:
        return "unqualified", causes
    return "inconclusive", ["raw_iq_analysis_unimplemented"]


def load_cw_qualification(path: Path) -> dict[str, Any]:
    """Load, authenticate, and semantically validate a CW evidence document."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CwQualificationError(f"cannot load CW qualification evidence: {error}") from error
    if not isinstance(document, dict):
        raise CwQualificationError("CW qualification evidence must be a JSON object")
    errors = sorted(
        Draft202012Validator(_schema()).iter_errors(document), key=lambda e: list(e.path)
    )
    if errors:
        raise CwQualificationError("invalid CW qualification evidence: " + errors[0].message)
    _artifact_matches(document["capture"], path)
    derived_status, derived_causes = _derive_status(document)
    if document["final_status"] != derived_status:
        raise CwQualificationError("final_status contradicts the measured evidence")
    if sorted(document["failure_causes"]) != derived_causes:
        raise CwQualificationError("failure_causes contradict the measured evidence")
    if document["qualification_claim"] != (derived_status == "qualified"):
        raise CwQualificationError("qualification_claim contradicts the derived status")
    return document
