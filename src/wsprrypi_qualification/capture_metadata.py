"""Schema and semantic validation for exact-count capture evidence."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Never, cast

from jsonschema import Draft202012Validator, FormatChecker


class CaptureMetadataError(ValueError):
    """Invalid or internally inconsistent capture evidence."""


@dataclass(frozen=True)
class CaptureArtifact:
    path: str
    present: bool
    complete: bool
    size_bytes: int
    sha256: str | None
    removed_incomplete_size_bytes: int
    removed_incomplete_sha256: str | None


@dataclass(frozen=True)
class CaptureMetadata:
    evidence_type: str
    capture_id: str
    requested_sample_count: int
    retained_sample_count: int
    overflow_count: int
    timeout_count: int
    clipped_samples: int
    output: CaptureArtifact
    primary_outcome: str
    primary_failure_cause: str | None
    failure_causes: tuple[str, ...]
    cleanup_outcome: str
    cleanup_failed_steps: tuple[str, ...]
    process_exit_code: int


def _reject_constant(value: str) -> Never:
    raise CaptureMetadataError(f"non-standard numeric constant is forbidden: {value}")


def _finite(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(child) for child in value.values())
    if isinstance(value, list):
        return all(_finite(child) for child in value)
    return True


def _settings_match(requested: dict[str, Any], actual: dict[str, Any]) -> bool:
    numeric = {"sample_rate_hz", "bandwidth_hz", "center_frequency_hz", "gain_db"}
    if set(requested) != set(actual):
        return False
    for key, requested_value in requested.items():
        actual_value = actual[key]
        if key in numeric:
            if not math.isclose(float(requested_value), float(actual_value), rel_tol=1e-9):
                return False
        elif requested_value != actual_value:
            return False
    return True


def _timestamp_semantics(document: dict[str, Any], contradictions: list[str]) -> None:
    timestamps = document["timestamps"]
    if timestamps["helper_start_utc"] is None or timestamps["helper_complete_utc"] is None:
        contradictions.append("helper UTC bounds are missing")
        return
    observed: list[datetime] = []
    for value in timestamps.values():
        if value is not None:
            observed.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
    if observed != sorted(observed):
        contradictions.append("observed UTC timestamps are out of order")
    if document["evidence_type"] == "capture_success" and any(
        value is None for value in timestamps.values()
    ):
        contradictions.append("successful capture is missing a phase timestamp")


def _semantic_contradictions(document: dict[str, Any]) -> list[str]:
    contradictions: list[str] = []
    success = document["evidence_type"] == "capture_success"
    requested_device = document["requested_device"]
    resolved_device = document["resolved_device"]
    requested_settings = document["requested_settings"]
    actual_settings = document["actual_settings"]
    output = document["output"]
    cleanup = document["cleanup"]
    causes = set(document["failure_causes"])
    primary = document["primary_failure_cause"]

    _timestamp_semantics(document, contradictions)
    if document["first_read"]["attempted"] is False and (
        document["first_read"]["discarded"] or document["first_read"]["sample_count"] != 0
    ):
        contradictions.append("unattempted first read claims discarded samples")
    if document["first_read"]["discarded"] != (document["first_read"]["sample_count"] > 0):
        contradictions.append("first-read discard flag and count disagree")

    if success:
        if (
            resolved_device != requested_device
            or actual_settings is None
            or not _settings_match(requested_settings, actual_settings)
        ):
            contradictions.append("successful capture receiver identity/settings differ")
        if document["requested_sample_count"] != document["retained_sample_count"]:
            contradictions.append("retained sample count is not exact")
        if output["size_bytes"] != document["retained_sample_count"] * 8:
            contradictions.append("CF32 output byte size is inconsistent")
        if document["clipping"]["sample_count"] != 0:
            contradictions.append("successful capture contains clipping")
        if output["removed_incomplete_size_bytes"] != 0 or (
            output["removed_incomplete_sha256"] is not None
        ):
            contradictions.append("successful capture claims a removed incomplete artifact")
    else:
        if primary not in causes:
            contradictions.append("primary failure cause is absent from failure causes")
        if output["present"] or output["complete"] or output["size_bytes"] != 0:
            contradictions.append("failed capture claims retained complete output")
        if (
            primary == "wrong_device"
            and resolved_device is not None
            and (resolved_device == requested_device)
        ):
            contradictions.append("wrong-device failure has matching resolved identity")
        if primary in {"settings_mismatch", "non_finite_actual_settings"} and (
            actual_settings is not None and _settings_match(requested_settings, actual_settings)
        ):
            contradictions.append("setting-mismatch failure has matching actual settings")

    cleanup_failed = cleanup["outcome"] == "failed"
    if not set(cleanup["failed_steps"]).issubset(cleanup["attempted_steps"]):
        contradictions.append("cleanup failed steps were not attempted")
    if cleanup_failed != bool(cleanup["failed_steps"]):
        contradictions.append("cleanup outcome and failed steps disagree")
    if cleanup_failed != (document["process_exit_code"] == 9):
        contradictions.append("cleanup failure and cleanup-failed exit code disagree")
    if cleanup_failed and "cleanup" not in causes:
        contradictions.append("cleanup failure is absent from failure causes")
    return contradictions


def validate_capture_metadata(document: dict[str, Any]) -> CaptureMetadata:
    resource = files("wsprrypi_qualification.schemas").joinpath("capture-metadata.schema.json")
    schema = cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = "$" + "".join(f"[{item!r}]" for item in error.absolute_path)
        raise CaptureMetadataError(f"{location}: {error.message}")
    if not _finite(document):
        raise CaptureMetadataError("$: numeric values must be finite")
    contradictions = _semantic_contradictions(document)
    if contradictions:
        raise CaptureMetadataError(
            "$: contradictory capture evidence: " + "; ".join(contradictions)
        )
    cleanup = document["cleanup"]
    output = document["output"]
    return CaptureMetadata(
        evidence_type=document["evidence_type"],
        capture_id=document["capture_id"],
        requested_sample_count=document["requested_sample_count"],
        retained_sample_count=document["retained_sample_count"],
        overflow_count=document["overflow_count"],
        timeout_count=document["timeout_count"],
        clipped_samples=document["clipping"]["sample_count"],
        output=CaptureArtifact(
            output["path"],
            output["present"],
            output["complete"],
            output["size_bytes"],
            output["sha256"],
            output["removed_incomplete_size_bytes"],
            output["removed_incomplete_sha256"],
        ),
        primary_outcome=document["primary_outcome"],
        primary_failure_cause=document["primary_failure_cause"],
        failure_causes=tuple(document["failure_causes"]),
        cleanup_outcome=cleanup["outcome"],
        cleanup_failed_steps=tuple(cleanup["failed_steps"]),
        process_exit_code=document["process_exit_code"],
    )


def load_capture_metadata(path: Path) -> CaptureMetadata:
    try:
        document = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, json.JSONDecodeError) as error:
        raise CaptureMetadataError(f"{path}: cannot load capture evidence: {error}") from error
    if not isinstance(document, dict):
        raise CaptureMetadataError(f"{path}: capture evidence root must be an object")
    return validate_capture_metadata(cast(dict[str, Any], document))
