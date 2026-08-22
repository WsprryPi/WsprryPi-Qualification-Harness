"""Portable, non-qualifying intake for preserved evidence archives."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from wsprrypi_qualification.cw_defaults import (
    KEYED_MESSAGES_PER_TRANSACTION,
    KEYED_QUALIFICATION_TRANSACTION_COUNT,
)
from wsprrypi_qualification.offline import (
    FailureCause,
    OfflineAnalysisError,
    artifact,
    load_json_document,
    sha256_file,
    write_json_new,
)

_MANIFEST_LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")
_ARCHIVE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ArchiveIntakeError(OfflineAnalysisError):
    """An archive inventory or multi-capture relationship is unsafe or contradictory."""


def _fail(message: str, cause: FailureCause = FailureCause.CONTRADICTORY_EVIDENCE) -> NoReturn:
    raise ArchiveIntakeError(message, cause=cause)


def _safe_relative(value: str) -> PurePosixPath:
    if "\\" in value:
        _fail("archive paths must use portable forward slashes")
    relative = PurePosixPath(value.removeprefix("./"))
    if (
        not relative.parts
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        _fail("archive path is absolute, empty, or contains traversal")
    return relative


def _classification(path: PurePosixPath) -> tuple[str, list[str]]:
    text = path.as_posix().lower()
    name = path.name.lower()
    if ".incomplete" in name or name.endswith((".pid", ".failure.json")):
        return "incomplete_artifact", ["filename_records_incomplete_or_failed_acquisition"]
    if path.parts[0] == "repositories":
        return "repository_snapshot", ["preserved_repository_snapshot_not_run_evidence"]
    if path.parts[0] == "baseline":
        return "historical_ad_hoc_evidence", ["preserved_host_baseline_not_harness_session"]
    if any(part in {"decode", "decoded"} for part in path.parts) or name.endswith(
        (".wav", ".f32", ".csv")
    ):
        return "generated_derivative", ["derived_or_decoder_generated_product"]
    if path.parts[0] == "evidence":
        return "historical_ad_hoc_evidence", ["retained_ad_hoc_evidence_requires_normalization"]
    if text in {"readme.txt"}:
        return "complete_regular_artifact", ["archive_control_document"]
    return "unsupported_entry", ["entry_role_not_supported_for_evidence_composition"]


def inventory_archive(
    archive_root: Path, manifest_path: Path, output_path: Path, *, archive_id: str
) -> dict[str, Any]:
    """Authenticate every manifest entry and emit a deterministic non-qualifying inventory."""
    if _ARCHIVE_ID.fullmatch(archive_id) is None:
        _fail("archive ID is not portable", FailureCause.INVALID_ARGUMENTS)
    if archive_root.is_symlink() or manifest_path.is_symlink():
        _fail("archive root and manifest cannot be symlinks")
    try:
        root = archive_root.resolve(strict=True)
        manifest = manifest_path.resolve(strict=True)
        output = output_path.resolve()
    except OSError as error:
        raise ArchiveIntakeError(f"archive input is unavailable: {error}") from error
    if not root.is_dir() or root.is_symlink():
        _fail("archive root must be a regular directory")
    try:
        manifest.relative_to(root)
    except ValueError:
        _fail("archive manifest must be inside the archive root")
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        _fail("inventory output cannot be inside the source archive")
    if manifest.is_symlink() or not manifest.is_file():
        _fail("archive manifest must be a regular file")
    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            _fail(f"malformed archive manifest line {line_number}")
        expected_sha, raw_path = match.groups()
        relative = _safe_relative(raw_path)
        portable = relative.as_posix()
        if portable in seen:
            _fail(f"duplicate archive manifest path: {portable}")
        seen.add(portable)
        candidate = root.joinpath(*relative.parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            stat = candidate.lstat()
        except (OSError, ValueError) as error:
            raise ArchiveIntakeError(
                f"archive entry is unavailable or escapes root: {portable}"
            ) from error
        if candidate.is_symlink() or not candidate.is_file():
            _fail(f"archive entry is not a regular non-symlink file: {portable}")
        observed_sha = sha256_file(candidate)
        if observed_sha != expected_sha:
            _fail(f"archive entry SHA-256 mismatch: {portable}")
        classification, reasons = _classification(relative)
        entries.append(
            {
                "path": portable,
                "size_bytes": stat.st_size,
                "sha256": observed_sha,
                "classification": classification,
                "reasons": reasons,
            }
        )
    entries.sort(key=lambda item: item["path"])
    if not entries:
        _fail("archive manifest must contain at least one artifact")
    counts = Counter(item["classification"] for item in entries)
    manifest_record = artifact(manifest)
    manifest_record["path"] = manifest.name
    document = {
        "schema_version": 1,
        "evidence_type": "archive_inventory",
        "archive_id": archive_id,
        "manifest": manifest_record,
        "entries": entries,
        "summary": {
            "entry_count": len(entries),
            "total_size_bytes": sum(item["size_bytes"] for item in entries),
            "classification_counts": dict(sorted(counts.items())),
        },
        "qualification_claim": False,
    }
    write_json_new(output_path, document, schema_name="archive-inventory.schema.json")
    return document


def _resolve_artifact(reference: dict[str, Any], owner: Path) -> Path:
    relative = _safe_relative(reference["path"])
    candidate = owner.parent.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(owner.parent.resolve(strict=True))
        stat = candidate.lstat()
    except (OSError, ValueError) as error:
        raise ArchiveIntakeError(f"multi-capture artifact is unavailable: {relative}") from error
    if candidate.is_symlink() or not candidate.is_file():
        _fail(f"multi-capture artifact is not a regular non-symlink file: {relative}")
    if stat.st_size != reference["size_bytes"] or sha256_file(candidate) != reference["sha256"]:
        _fail(f"multi-capture artifact identity mismatch: {relative}")
    return resolved


def _load_untyped_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArchiveIntakeError(f"multi-capture JSON is unreadable: {path.name}") from error
    if not isinstance(value, dict):
        _fail(f"multi-capture JSON must be an object: {path.name}")
    return value


def validate_multi_capture_session(path: Path) -> dict[str, Any]:
    """Authenticate a relationship among distinct acquired repetitions."""
    if path.is_symlink() or not path.is_file():
        _fail("multi-capture session must be a regular non-symlink file")
    document = load_json_document(path, "cw-multi-capture-session.schema.json")
    plan_path = _resolve_artifact(document["plan"], path)
    plan = load_json_document(plan_path, "cw-mode-plan.schema.json")
    if plan["mode"] != document["mode"]:
        _fail("multi-capture session mode contradicts its normalized plan")
    if plan["mode"] in {"qrss", "fskcw", "dfcw"} and (
        plan["protocol"]["repetitions"] != KEYED_MESSAGES_PER_TRANSACTION
    ):
        _fail("multi-capture plans must contain one keyed message per acquisition")
    repetitions = document["repetitions"]
    if len(repetitions) != KEYED_QUALIFICATION_TRANSACTION_COUNT:
        _fail("multi-capture sessions require exactly three independent acquisitions")
    numbers = [item["repetition"] for item in repetitions]
    if numbers != list(range(1, len(repetitions) + 1)):
        _fail("multi-capture repetitions must be contiguous and ordered from one")
    acquisition_ids = [item["acquisition_id"] for item in repetitions]
    if len(set(acquisition_ids)) != len(acquisition_ids):
        _fail("multi-capture acquisition identities must be distinct")
    role_paths: dict[str, set[Path]] = {
        role: set() for role in ("capture", "metadata", "observations")
    }
    all_role_paths: set[Path] = set()
    capture_hashes: set[str] = set()
    for repetition in repetitions:
        resolved_roles: dict[str, Path] = {}
        for role in role_paths:
            resolved = _resolve_artifact(repetition[role], path)
            if resolved in role_paths[role]:
                _fail(f"one {role} artifact cannot satisfy multiple repetitions")
            if resolved in all_role_paths:
                _fail("one artifact cannot satisfy multiple multi-capture roles")
            role_paths[role].add(resolved)
            all_role_paths.add(resolved)
            resolved_roles[role] = resolved
        capture_hash = repetition["capture"]["sha256"]
        if capture_hash in capture_hashes:
            _fail("multi-capture repetitions must bind distinct capture content")
        capture_hashes.add(capture_hash)
        for role in ("metadata", "observations"):
            record = _load_untyped_json(resolved_roles[role])
            if record.get("mode") != document["mode"]:
                _fail(f"multi-capture {role} mode contradicts the session")
            plan_reference = record.get("plan")
            if (
                not isinstance(plan_reference, dict)
                or plan_reference.get("sha256") != document["plan"]["sha256"]
            ):
                _fail(f"multi-capture {role} does not bind the normalized plan")
        metadata = _load_untyped_json(resolved_roles["metadata"])
        if metadata.get("capture", {}).get("sha256") != capture_hash:
            _fail("multi-capture metadata does not bind its repetition capture")
        if metadata.get("acquisition_id") != repetition["acquisition_id"]:
            _fail("multi-capture metadata contradicts its acquisition identity")
        observations = _load_untyped_json(resolved_roles["observations"])
        if observations.get("capture", {}).get("sha256") != capture_hash:
            _fail("multi-capture observations do not bind their repetition capture")
    return {
        "session_id": document["session_id"],
        "mode": document["mode"],
        "repetition_count": len(repetitions),
        "final_status": "inconclusive",
        "qualification_claim": False,
        "valid": True,
    }
