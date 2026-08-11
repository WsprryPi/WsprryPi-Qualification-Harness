"""Shared fail-closed helpers for immutable offline evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


class FailureCause(StrEnum):
    INVALID_ARGUMENTS = "invalid_arguments"
    INVALID_FIXTURE = "invalid_fixture"
    CAPTURE_INCOMPATIBLE = "capture_incompatible"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    DECODER_FAILURE = "decoder_failure"
    OUTPUT_CONFLICT = "output_conflict"
    INCOMPLETE_EVIDENCE = "incomplete_evidence"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    FILESYSTEM_FAILURE = "filesystem_failure"


class OfflineAnalysisError(ValueError):
    """Input or evidence cannot satisfy an offline measurement contract."""

    def __init__(
        self,
        message: str,
        *,
        cause: FailureCause = FailureCause.INVALID_FIXTURE,
        gate_outcome: str = "inconclusive",
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.gate_outcome = gate_outcome


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact(path: Path) -> dict[str, Any]:
    canonical = path.resolve(strict=True)
    stat = canonical.stat()
    return {"path": str(canonical), "size_bytes": stat.st_size, "sha256": sha256_file(canonical)}


def require_new_file(path: Path) -> None:
    if path.exists():
        raise OfflineAnalysisError(
            f"refusing to overwrite existing output: {path}",
            cause=FailureCause.OUTPUT_CONFLICT,
        )
    if not path.parent.is_dir():
        raise OfflineAnalysisError(f"output parent does not exist: {path.parent}")


def validate_document(document: Any, schema_name: str) -> dict[str, Any]:
    if not isinstance(document, dict) or not _finite(document):
        raise OfflineAnalysisError(
            "evidence is not a finite JSON object", cause=FailureCause.INCOMPLETE_EVIDENCE
        )
    schema = json.loads(
        files("wsprrypi_qualification.schemas").joinpath(schema_name).read_text(encoding="utf-8")
    )
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = "$" + "".join(f"[{item!r}]" for item in error.absolute_path)
        raise OfflineAnalysisError(
            f"evidence violates schema at {location}: {error.message}",
            cause=FailureCause.INCOMPLETE_EVIDENCE,
        )
    return document


def load_json_document(path: Path, schema_name: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OfflineAnalysisError(
            f"cannot read JSON evidence {path}: {error}", cause=FailureCause.INCOMPLETE_EVIDENCE
        ) from error
    return validate_document(document, schema_name)


def write_json_new(path: Path, document: dict[str, Any], *, schema_name: str | None = None) -> None:
    require_new_file(path)
    if not _finite(document):
        raise OfflineAnalysisError("evidence contains a non-finite numeric value")
    if schema_name is not None:
        validate_document(document, schema_name)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.incomplete-",
            dir=path.parent,
            delete=False,
        ) as handle:
            json.dump(document, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(child) for child in value)
    return True


def write_offline_failure(path: Path, operation: str, error: Exception) -> dict[str, Any]:
    message = str(error)
    cause = (
        error.cause.value
        if isinstance(error, OfflineAnalysisError)
        else FailureCause.FILESYSTEM_FAILURE.value
        if isinstance(error, OSError)
        else FailureCause.INVALID_ARGUMENTS.value
    )
    gate_outcome = error.gate_outcome if isinstance(error, OfflineAnalysisError) else "inconclusive"
    document = {
        "schema_version": 1,
        "evidence_type": "offline_failure",
        "operation": operation,
        "outcome": "failed",
        "gate_outcome": gate_outcome,
        "failure_cause": cause,
        "message": message,
        "publication": {"primary_output_complete": False, "cleanup": "verified"},
    }
    write_json_new(path, document, schema_name="offline-failure.schema.json")
    return document
