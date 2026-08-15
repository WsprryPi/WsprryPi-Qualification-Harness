"""Portable Phase 4 acquired-IQ replay bundle composition and validation."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from wsprrypi_qualification.cw_contracts import (
    CwContractError,
    _bind,
    _resolved_reference,
    _validate_events,
)
from wsprrypi_qualification.cw_iq import analyze_synthetic_iq
from wsprrypi_qualification.manifests import build_manifest, render_manifest, write_manifest
from wsprrypi_qualification.offline import (
    FailureCause,
    OfflineAnalysisError,
    artifact,
    load_json_document,
    sha256_file,
    write_json_new,
)

REQUIRED_INDEX = {
    "plan": "plan.json",
    "expected_events": "expected-events.json",
    "capture_metadata": "capture-metadata.json",
    "capture_iq": "capture.cf32",
    "observations": "observations.json",
    "mode_gate": "mode-gate.json",
}
REQUIRED_FILES = {*REQUIRED_INDEX.values(), "evidence-index.json", "result.json", "SHA256SUMS"}


class CwReplayError(OfflineAnalysisError):
    """An acquired replay input or composed bundle is invalid."""


def _fail(message: str, cause: FailureCause = FailureCause.CONTRADICTORY_EVIDENCE) -> None:
    raise CwReplayError(message, cause=cause)


def _relative_artifact(path: Path) -> dict[str, Any]:
    reference = artifact(path)
    reference["path"] = path.name
    return reference


def _load_acquired_inputs(
    plan_path: Path, expected_path: Path, metadata_path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    plan = load_json_document(plan_path, "cw-mode-plan.schema.json")
    expected = load_json_document(expected_path, "cw-expected-events.schema.json")
    metadata = load_json_document(metadata_path, "cw-acquired-capture.schema.json")
    try:
        _bind(expected["plan"], expected_path, plan_path, "plan")
        _bind(metadata["plan"], metadata_path, plan_path, "plan")
        _bind(metadata["expected_events"], metadata_path, expected_path, "expected events")
        capture_path = _resolved_reference(metadata["capture"], metadata_path)
        _validate_events(plan, expected)
    except CwContractError as error:
        raise CwReplayError(str(error), cause=error.cause) from error
    if any(document["run_id"] != plan["run_id"] for document in (expected, metadata)):
        _fail("acquired replay inputs identify different runs")
    if any(document["mode"] != plan["mode"] for document in (expected, metadata)):
        _fail("acquired replay inputs identify different modes")
    contract = plan["capture_contract"]
    comparisons = {
        "format": contract["format"],
        "sample_count": contract["sample_count"],
        "sample_rate_hz": contract["sample_rate_hz"],
        "center_frequency_hz": contract["center_frequency_hz"],
        "acquired_sample_count": contract["sample_count"],
        "overflow_count": contract["overflow_max"],
        "fixed_gain": contract["fixed_gain"],
        "agc_enabled": contract["agc_enabled"],
        "bias_tee_enabled": contract["bias_tee_enabled"],
        "first_read_discarded": contract["first_read_discarded"],
        "receiver": plan["receiver"],
    }
    for field, expected_value in comparisons.items():
        if metadata[field] != expected_value:
            _fail(f"acquired capture {field} contradicts the resolved plan")
    if metadata["acquired_utc"] != plan["resolved_utc"] or not metadata["acquired_utc"].endswith(
        "Z"
    ):
        _fail("acquired capture timestamp must exactly bind the resolved plan UTC")
    if metadata["capture"]["size_bytes"] != int(contract["sample_count"]) * 8:
        _fail("acquired capture byte length is not exact-count CF32LE")
    return plan, expected, metadata, capture_path


def compose_acquired_replay(
    plan_path: Path,
    expected_path: Path,
    metadata_path: Path,
    output_directory: Path,
    *,
    source_revision: str,
) -> dict[str, Any]:
    """Compose a new deterministic, non-qualifying acquired-IQ replay bundle."""
    if output_directory.exists():
        _fail("refusing to overwrite an existing replay bundle", FailureCause.OUTPUT_CONFLICT)
    if not output_directory.parent.is_dir():
        _fail("replay bundle parent does not exist", FailureCause.INVALID_ARGUMENTS)
    plan, expected, metadata, capture_path = _load_acquired_inputs(
        plan_path, expected_path, metadata_path
    )
    try:
        output_directory.resolve().relative_to(capture_path.resolve())
    except ValueError:
        pass
    else:
        _fail("replay output cannot be inside the source capture path")
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}.incomplete-", dir=output_directory.parent
        )
    )
    try:
        retained_plan = temporary / REQUIRED_INDEX["plan"]
        retained_expected = temporary / REQUIRED_INDEX["expected_events"]
        retained_metadata = temporary / REQUIRED_INDEX["capture_metadata"]
        retained_capture = temporary / REQUIRED_INDEX["capture_iq"]
        write_json_new(retained_plan, plan, schema_name="cw-mode-plan.schema.json")
        expected["plan"] = _relative_artifact(retained_plan)
        write_json_new(retained_expected, expected, schema_name="cw-expected-events.schema.json")
        shutil.copyfile(capture_path, retained_capture)
        metadata["plan"] = _relative_artifact(retained_plan)
        metadata["expected_events"] = _relative_artifact(retained_expected)
        metadata["capture"] = _relative_artifact(retained_capture)
        write_json_new(retained_metadata, metadata, schema_name="cw-acquired-capture.schema.json")
        observations_path = temporary / REQUIRED_INDEX["observations"]
        gate_path = temporary / REQUIRED_INDEX["mode_gate"]
        observations, gate = analyze_synthetic_iq(
            retained_plan,
            retained_expected,
            retained_metadata,
            observations_path,
            gate_path,
            source_revision=source_revision,
            _metadata_schema="cw-acquired-capture.schema.json",
            _synthetic=False,
            _artifact_root=temporary,
        )
        artifacts = [
            {"role": role, **_relative_artifact(temporary / filename)}
            for role, filename in REQUIRED_INDEX.items()
        ]
        index = {
            "schema_version": 1,
            "evidence_type": "cw_replay_evidence_index",
            "run_id": plan["run_id"],
            "mode": plan["mode"],
            "artifacts": artifacts,
        }
        index_path = temporary / "evidence-index.json"
        write_json_new(index_path, index, schema_name="cw-replay-evidence-index.schema.json")
        result = {
            "schema_version": 1,
            "evidence_type": "cw_replay_result",
            "run_id": plan["run_id"],
            "mode": plan["mode"],
            "evidence_index": _relative_artifact(index_path),
            "measurement": {
                "carrier_gate": gate["carrier_gate"],
                "mode_gate": gate["mode_gate"],
            },
            "lifecycle": {
                "runtime_authorization_evidence": None,
                "live_session_evidence": None,
                "cleanup_evidence": None,
                "quiescence_evidence": None,
            },
            "failure_causes": sorted(
                set(observations["failure_causes"] + ["replay_lifecycle_evidence_absent"])
            ),
            "final_status": "inconclusive",
            "qualification_claim": False,
        }
        write_json_new(
            temporary / "result.json", result, schema_name="cw-replay-result.schema.json"
        )
        write_manifest(temporary)
        validate_replay_bundle(temporary, source_revision=source_revision, recompute=True)
        temporary.replace(output_directory)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return result


def validate_replay_bundle(
    bundle: Path, *, source_revision: str | None = None, recompute: bool = False
) -> dict[str, Any]:
    """Authenticate a complete Phase 4 replay bundle and optionally recompute IQ evidence."""
    if not bundle.is_dir() or bundle.is_symlink():
        _fail("replay bundle must be a regular directory")
    observed_files: set[str] = set()
    for path in bundle.iterdir():
        if path.is_symlink() or not path.is_file():
            _fail("replay bundles cannot contain symlinks or nested/non-regular entries")
        observed_files.add(path.name)
    if observed_files != REQUIRED_FILES:
        _fail("replay bundle required artifact set is incomplete or contains extras")
    plan, expected, metadata, _ = _load_acquired_inputs(
        bundle / "plan.json", bundle / "expected-events.json", bundle / "capture-metadata.json"
    )
    observations = load_json_document(
        bundle / "observations.json", "cw-generated-observations.schema.json"
    )
    gate = load_json_document(bundle / "mode-gate.json", "cw-mode-gate.schema.json")
    index = load_json_document(
        bundle / "evidence-index.json", "cw-replay-evidence-index.schema.json"
    )
    result = load_json_document(bundle / "result.json", "cw-replay-result.schema.json")
    bindings = (
        (expected["plan"], bundle / "plan.json", "expected-event plan"),
        (metadata["plan"], bundle / "plan.json", "capture-metadata plan"),
        (
            metadata["expected_events"],
            bundle / "expected-events.json",
            "capture-metadata events",
        ),
        (metadata["capture"], bundle / "capture.cf32", "capture-metadata IQ"),
        (observations["plan"], bundle / "plan.json", "observation plan"),
        (observations["expected_events"], bundle / "expected-events.json", "observation events"),
        (observations["capture"], bundle / "capture.cf32", "observation capture"),
        (gate["plan"], bundle / "plan.json", "gate plan"),
        (gate["expected_events"], bundle / "expected-events.json", "gate events"),
        (gate["observations"], bundle / "observations.json", "gate observations"),
        (result["evidence_index"], bundle / "evidence-index.json", "result index"),
    )
    for reference, expected_file, label in bindings:
        if reference["path"] != expected_file.name:
            _fail(f"{label} must use its canonical relative bundle path")
        try:
            _bind(reference, bundle / label.replace(" ", "-"), expected_file, label)
        except CwContractError as error:
            raise CwReplayError(str(error), cause=error.cause) from error
    if observations["capture"]["synthetic"] is not False:
        _fail("replay observations must identify acquired, not synthetic, IQ")
    for document in (observations, gate, index, result):
        if document["run_id"] != plan["run_id"] or document["mode"] != plan["mode"]:
            _fail("replay documents identify different runs or modes")
    expected_roles = {role: filename for role, filename in REQUIRED_INDEX.items()}
    indexed_roles = {entry["role"]: entry["path"] for entry in index["artifacts"]}
    if indexed_roles != expected_roles or len(indexed_roles) != len(index["artifacts"]):
        _fail("replay evidence index roles or canonical paths are invalid")
    for entry in index["artifacts"]:
        path = bundle / entry["path"]
        if entry["size_bytes"] != path.stat().st_size or entry["sha256"] != sha256_file(path):
            _fail("replay evidence index artifact authentication failed")
    if result["measurement"] != {
        "carrier_gate": gate["carrier_gate"],
        "mode_gate": gate["mode_gate"],
    }:
        _fail("replay result measurement contradicts the mode gate")
    expected_result_causes = sorted(
        set(observations["failure_causes"] + ["replay_lifecycle_evidence_absent"])
    )
    if result["failure_causes"] != expected_result_causes:
        _fail("replay result failure causes contradict generated observations")
    expected_manifest = render_manifest(build_manifest(bundle))
    if (bundle / "SHA256SUMS").read_text(encoding="utf-8") != expected_manifest:
        _fail("replay manifest is not canonical or does not authenticate the bundle")
    if recompute:
        revision = source_revision or observations["analyzer"]["source_revision"]
        with tempfile.TemporaryDirectory(prefix="wspq-replay-verify-") as directory:
            scratch = Path(directory)
            measured, derived_gate = analyze_synthetic_iq(
                bundle / "plan.json",
                bundle / "expected-events.json",
                bundle / "capture-metadata.json",
                scratch / "observations.json",
                scratch / "gate.json",
                source_revision=revision,
                _metadata_schema="cw-acquired-capture.schema.json",
                _synthetic=False,
            )
        for field in (
            "analyzer",
            "observations",
            "measurement_summary",
            "analysis_outcome",
            "failure_causes",
        ):
            if measured[field] != observations[field]:
                _fail("recomputed acquired-IQ observations contradict retained evidence")
        for field in ("carrier_gate", "mode_gate", "failure_causes"):
            if derived_gate[field] != gate[field]:
                _fail("recomputed acquired-IQ gate contradicts retained evidence")
    return {
        "run_id": plan["run_id"],
        "mode": plan["mode"],
        "final_status": "inconclusive",
        "qualification_claim": False,
        "valid": True,
    }
