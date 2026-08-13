from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from wsprrypi_qualification.carrier_corrections import (
    AUTHORIZED_RUN_IDS,
    CarrierCorrectionError,
    validate_carrier_correction_bundle,
)
from wsprrypi_qualification.manifests import build_manifest, write_manifest
from wsprrypi_qualification.offline import sha256_file


def write_json(path: Path, document: object) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def rewrite_manifest(root: Path) -> None:
    (root / "SHA256SUMS").unlink(missing_ok=True)
    write_manifest(root)


def anchor_record(root: Path) -> dict[str, Any]:
    result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    return {
        "run_id": root.name,
        "repository_relative_path": f"runs/{root.name}",
        "manifest": {
            "path": "SHA256SUMS",
            "size_bytes": (root / "SHA256SUMS").stat().st_size,
            "sha256": sha256_file(root / "SHA256SUMS"),
        },
        "artifacts": [
            {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
            for item in build_manifest(root)
        ],
        "expected_result": result["status"],
        "expected_carrier_gate": result["carrier_gate"],
        "expected_decode_gate": "not_run",
        "expected_cleanup_outcome": result["cleanup_outcome"],
        "frame_artifacts_absent": True,
    }


def make_original(parent: Path, run_id: str, *, retry: bool) -> Path:
    root = parent / run_id
    root.mkdir()
    write_json(
        root / "resolved-plan.json",
        {"frames_authorized": False, "rf_path": {"path_type": "radiated"}},
    )
    write_json(root / "session-progress.json", {"events": [{"phase": "cleanup_complete"}]})
    (root / "rf-off.cf32").write_bytes(b"\x00" * 16)
    status = "unqualified_carrier" if retry else "cleanup_failed"
    gate = "failed" if retry else "not_run"
    cleanup = "verified" if retry else "failed"
    write_json(
        root / "result.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "status": status,
            "started_utc": "2026-08-13T13:15:47Z",
            "preflight_passed": True,
            "carrier_gate": gate,
            "decode_gate": "not_run",
            "cleanup_outcome": cleanup,
            "failure_causes": ["transmitter_carrier"] if retry else ["cleanup"],
            "artifacts": [],
        },
    )
    if retry:
        write_json(root / "carrier-analysis.json", {"gate_outcome": "failed"})
    write_manifest(root)
    return root


def make_bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    first = make_original(tmp_path, AUTHORIZED_RUN_IDS[0], retry=False)
    original = make_original(tmp_path, AUTHORIZED_RUN_IDS[1], retry=True)
    anchor = tmp_path / "anchors.json"
    write_json(
        anchor,
        {
            "schema_version": 1,
            "evidence_type": "bounded_carrier_original_anchors",
            "runs": [anchor_record(first), anchor_record(original)],
        },
    )
    correction = tmp_path / f"{original.name}-correction-2"
    correction.mkdir()
    plan = json.loads((original / "resolved-plan.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    write_json(
        correction / "correction.json",
        {
            "schema_version": 1,
            "evidence_type": "bounded_carrier_run_correction",
            "correction_run_id": correction.name,
            "created_utc": "2026-08-13T14:00:00Z",
            "anchor_sha256": sha256_file(anchor),
            "original": {"run_id": original.name, "path": str(original), "unchanged": True},
            "authorization_evidence": {
                "operator_statement": {
                    "evidence_timing": "retrospective_reconstruction",
                    "recorded_utc": None,
                    "verbatim_text": (
                        "You are authorized to set the Pis on fire if you want - "
                        "do not stop for permission anymore for this prompt"
                    ),
                    "prompt_boundary": "remaining bounded carrier qualification prompt work",
                    "explicit_run_ids_supplied": False,
                    "explicit_plan_digest_supplied": False,
                },
                "harness_interpretation": {
                    "run_id": original.name,
                    "interpreted_run_ids": AUTHORIZED_RUN_IDS,
                    "computed_plan_sha256": digest,
                    "receiver_access_interpreted": True,
                    "transmitter_operation_interpreted": True,
                    "retry_interpreted": True,
                    "reasons": [
                        "The statement applied to the remaining bounded carrier prompt.",
                        "The retry remained inside that prompt boundary.",
                    ],
                    "operator_authenticated": False,
                    "qualification_grade_runtime_confirmation": False,
                    "audit_limitation": (
                        "The standalone run cannot prove exact contemporaneous "
                        "digest-bound operator confirmation."
                    ),
                },
            },
            "rf_path": {
                "path_type": "radiated",
                "antenna_connected": True,
                "termination": None,
                "attenuation_db": None,
                "filter": "None",
                "safe_input_basis": "N/A",
            },
            "cleanup_evidence": {
                "in_run_cleanup_outcome": "verified",
                "retrospective_annotation_authority": (
                    "unsupported_summary_not_used_for_classification"
                ),
                "later_recovery_changes_result": False,
            },
            "preserved_result": {
                "status": "unqualified_carrier",
                "carrier_gate": "failed",
                "decode_gate": "not_run",
                "frames_started": False,
            },
        },
    )
    write_manifest(correction)
    return correction, original, anchor


def validate(correction: Path, original: Path, anchor: Path) -> dict[str, Any]:
    return validate_carrier_correction_bundle(correction, original, anchor)


def test_correction_bundle_validates(tmp_path: Path) -> None:
    correction, original, anchor = make_bundle(tmp_path)
    assert validate(correction, original, anchor)["preserved_result"]["status"] == (
        "unqualified_carrier"
    )


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("operator_statement", "recorded_utc", "2026-08-13T13:15:00Z"),
        ("operator_statement", "evidence_timing", "contemporaneous"),
        ("operator_statement", "explicit_run_ids_supplied", True),
        ("operator_statement", "explicit_plan_digest_supplied", True),
        ("harness_interpretation", "operator_authenticated", True),
        ("harness_interpretation", "qualification_grade_runtime_confirmation", True),
        ("harness_interpretation", "retry_interpreted", False),
        ("harness_interpretation", "computed_plan_sha256", "0" * 64),
    ],
)
def test_authorization_overclaim_rejected(
    tmp_path: Path, section: str, field: str, value: object
) -> None:
    correction, original, anchor = make_bundle(tmp_path)
    path = correction / "correction.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["authorization_evidence"][section][field] = value
    write_json(path, document)
    rewrite_manifest(correction)
    with pytest.raises((CarrierCorrectionError, ValueError)):
        validate(correction, original, anchor)


@pytest.mark.parametrize(
    ("name", "contents"),
    [
        ("injected-frame.log", "frame started"),
        ("slot.wav", "not a wave"),
        ("decoder.json", '{"evidence_type":"decoder_evidence"}'),
        ("innocent.json", '{"evidence_type":"decode_summary"}'),
    ],
)
def test_external_anchor_rejects_regenerated_original_and_correction(
    tmp_path: Path, name: str, contents: str
) -> None:
    correction, original, anchor = make_bundle(tmp_path)
    (original / name).write_text(contents, encoding="utf-8")
    rewrite_manifest(original)
    correction_path = correction / "correction.json"
    document = json.loads(correction_path.read_text(encoding="utf-8"))
    document["original"] = {"run_id": original.name, "path": str(original), "unchanged": True}
    write_json(correction_path, document)
    rewrite_manifest(correction)
    with pytest.raises(CarrierCorrectionError, match="anchor"):
        validate(correction, original, anchor)


@pytest.mark.parametrize(
    "target",
    ["result.json", "resolved-plan.json", "session-progress.json", "carrier-analysis.json"],
)
def test_anchor_rejects_replaced_original_artifact(tmp_path: Path, target: str) -> None:
    correction, original, anchor = make_bundle(tmp_path)
    (original / target).write_text("{}\n", encoding="utf-8")
    rewrite_manifest(original)
    with pytest.raises(CarrierCorrectionError, match="anchor"):
        validate(correction, original, anchor)


def test_anchor_rejects_deleted_artifact_and_changed_iq_identity(tmp_path: Path) -> None:
    correction, original, anchor = make_bundle(tmp_path)
    (original / "carrier-analysis.json").unlink()
    rewrite_manifest(original)
    with pytest.raises(CarrierCorrectionError, match="anchor"):
        validate(correction, original, anchor)


def test_anchor_rejects_changed_iq_byte_after_both_manifests_refresh(tmp_path: Path) -> None:
    correction, original, anchor = make_bundle(tmp_path)
    iq = original / "rf-off.cf32"
    value = bytearray(iq.read_bytes())
    value[0] = 1
    iq.write_bytes(value)
    rewrite_manifest(original)
    correction_path = correction / "correction.json"
    document = json.loads(correction_path.read_text(encoding="utf-8"))
    document["original"] = {"run_id": original.name, "path": str(original), "unchanged": True}
    write_json(correction_path, document)
    rewrite_manifest(correction)
    with pytest.raises(CarrierCorrectionError, match="anchor"):
        validate(correction, original, anchor)
