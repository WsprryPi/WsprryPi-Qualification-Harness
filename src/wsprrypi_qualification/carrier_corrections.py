"""Validation for immutable retrospective bounded-carrier corrections."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from wsprrypi_qualification.manifests import build_manifest, render_manifest
from wsprrypi_qualification.offline import load_json_document, sha256_file
from wsprrypi_qualification.results import validate_result_document


class CarrierCorrectionError(ValueError):
    """A carrier correction bundle is incomplete or contradictory."""


OPERATOR_AUTHORIZATION_TEXT = (
    "You are authorized to set the Pis on fire if you want - "
    "do not stop for permission anymore for this prompt"
)
AUTHORIZED_RUN_IDS = [
    "20260813T131102Z-bounded-carrier-20m",
    "20260813T131505Z-bounded-carrier-20m-retry",
]
ANCHOR_RELATIVE_PATH = Path("evidence-anchors/bounded-carrier-original-anchors.json")


def _artifact(path: Path, relative: str) -> dict[str, object]:
    target = path / relative
    return {"path": relative, "size_bytes": target.stat().st_size, "sha256": sha256_file(target)}


def original_reference(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    manifest = root / "SHA256SUMS"
    if manifest.read_text(encoding="utf-8") != render_manifest(build_manifest(root)):
        raise CarrierCorrectionError("original manifest is invalid")
    return {
        "run_id": root.name,
        "path": str(root),
        "unchanged": True,
        "manifest": _artifact(root, "SHA256SUMS"),
        "artifacts": [
            {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
            for item in build_manifest(root)
        ],
    }


def _default_anchor_path() -> Path:
    repository_path = Path(__file__).resolve().parents[2] / ANCHOR_RELATIVE_PATH
    if repository_path.is_file():
        return repository_path
    packaged = files("wsprrypi_qualification").joinpath(
        "evidence_anchors/bounded-carrier-original-anchors.json"
    )
    return Path(str(packaged))


def _load_anchor(anchor_path: Path, run_id: str) -> tuple[dict[str, Any], str]:
    anchor_path = anchor_path.resolve(strict=True)
    document = load_json_document(anchor_path, "bounded-carrier-original-anchors.schema.json")
    if [item["run_id"] for item in document["runs"]] != AUTHORIZED_RUN_IDS:
        raise CarrierCorrectionError("anchor run set or order is invalid")
    matches = [item for item in document["runs"] if item["run_id"] == run_id]
    if len(matches) != 1:
        raise CarrierCorrectionError("anchor does not identify exactly one original run")
    return matches[0], sha256_file(anchor_path)


def _validate_original_against_anchor(root: Path, anchor: dict[str, Any]) -> None:
    if root.name != anchor["run_id"]:
        raise CarrierCorrectionError("original directory name differs from anchor")
    expected_locator = f"runs/{root.name}"
    if anchor["repository_relative_path"] != expected_locator:
        raise CarrierCorrectionError("original durable locator differs from anchor")
    if any(path.is_symlink() for path in root.rglob("*")):
        raise CarrierCorrectionError("original bundle contains a symlink")
    manifest_path = root / "SHA256SUMS"
    manifest_record = {
        "path": "SHA256SUMS",
        "size_bytes": manifest_path.stat().st_size,
        "sha256": sha256_file(manifest_path),
    }
    if manifest_record != anchor["manifest"]:
        raise CarrierCorrectionError("original manifest identity differs from external anchor")
    actual_records = [
        {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
        for item in build_manifest(root)
    ]
    if actual_records != anchor["artifacts"]:
        raise CarrierCorrectionError("original artifact set differs from external anchor")
    if manifest_path.read_text(encoding="utf-8") != render_manifest(build_manifest(root)):
        raise CarrierCorrectionError("original manifest bytes are invalid")


def _validate_no_frame_semantics(root: Path, anchor: dict[str, Any]) -> None:
    if anchor["frame_artifacts_absent"] is not True:
        raise CarrierCorrectionError("anchor does not assert frame suppression")
    result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    if (
        result["status"] != anchor["expected_result"]
        or result["carrier_gate"] != anchor["expected_carrier_gate"]
        or result["decode_gate"] != anchor["expected_decode_gate"]
        or result["cleanup_outcome"] != anchor["expected_cleanup_outcome"]
    ):
        raise CarrierCorrectionError("result gates or cleanup differ from anchor")
    plan = json.loads((root / "resolved-plan.json").read_text(encoding="utf-8"))
    if plan.get("frames_authorized") is not False:
        raise CarrierCorrectionError("resolved plan does not prohibit frames")
    session = json.loads((root / "session-progress.json").read_text(encoding="utf-8"))
    phases = [str(event.get("phase", "")) for event in session.get("events", [])]
    if any("frame" in phase.lower() or "decode" in phase.lower() for phase in phases):
        raise CarrierCorrectionError("session contains a frame or decode phase")
    forbidden_types = {"audio_conversion", "decoder_evidence", "decode_summary"}
    for item in anchor["artifacts"]:
        path = root / item["path"]
        if path.suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("evidence_type") in forbidden_types:
                raise CarrierCorrectionError("original JSON claims frame or decode evidence")


def validate_carrier_correction_bundle(
    correction_root: Path, original_root: Path, anchor_path: Path | None = None
) -> dict[str, Any]:
    correction_root = correction_root.resolve(strict=True)
    original_root = original_root.resolve(strict=True)
    expected_files = {"correction.json", "SHA256SUMS"}
    actual_files = {path.name for path in correction_root.iterdir() if path.is_file()}
    if actual_files != expected_files or any(
        path.is_symlink() for path in correction_root.iterdir()
    ):
        raise CarrierCorrectionError("correction artifact set is not exact")
    if (correction_root / "SHA256SUMS").read_text(encoding="utf-8") != render_manifest(
        build_manifest(correction_root)
    ):
        raise CarrierCorrectionError("correction manifest is invalid")
    document = load_json_document(
        correction_root / "correction.json", "carrier-run-correction.schema.json"
    )
    if document["correction_run_id"] != correction_root.name:
        raise CarrierCorrectionError("correction run ID differs from its directory")
    created = datetime.fromisoformat(document["created_utc"].replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise CarrierCorrectionError("correction timestamp is not UTC-aware")
    anchor, anchor_digest = _load_anchor(anchor_path or _default_anchor_path(), original_root.name)
    if document["anchor_sha256"] != anchor_digest:
        raise CarrierCorrectionError("correction anchor digest differs from maintained anchor")
    _validate_original_against_anchor(original_root, anchor)
    _validate_no_frame_semantics(original_root, anchor)
    expected_reference = {
        "run_id": original_root.name,
        "path": str(original_root),
        "unchanged": True,
    }
    if document["original"] != expected_reference:
        raise CarrierCorrectionError("original reference differs from immutable bytes")
    result = json.loads((original_root / "result.json").read_text(encoding="utf-8"))
    validate_result_document(result)
    authorization = document["authorization_evidence"]
    statement = authorization["operator_statement"]
    interpretation = authorization["harness_interpretation"]
    if statement["verbatim_text"] != OPERATOR_AUTHORIZATION_TEXT:
        raise CarrierCorrectionError("authorization text differs from the retained operator text")
    if interpretation["run_id"] != original_root.name:
        raise CarrierCorrectionError("interpretation run ID differs from original run")
    if interpretation["interpreted_run_ids"] != AUTHORIZED_RUN_IDS:
        raise CarrierCorrectionError("interpreted run set differs from the prompt transaction set")
    plan = json.loads((original_root / "resolved-plan.json").read_text(encoding="utf-8"))
    canonical_digest = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if interpretation["computed_plan_sha256"] != canonical_digest:
        raise CarrierCorrectionError("interpreted plan digest differs from resolved plan")
    preserved = document["preserved_result"]
    expected = {
        "status": result["status"],
        "carrier_gate": result["carrier_gate"],
        "decode_gate": result["decode_gate"],
        "frames_started": False,
    }
    if preserved != expected:
        raise CarrierCorrectionError("correction relabels the original result")
    cleanup = document["cleanup_evidence"]["in_run_cleanup_outcome"]
    if cleanup != result["cleanup_outcome"]:
        raise CarrierCorrectionError("correction cleanup outcome differs from result")
    return document
