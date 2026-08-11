"""Serialization and semantic validation for final result documents."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

from wsprrypi_qualification.classification import FIXTURE_CAUSES, classify_result
from wsprrypi_qualification.models import (
    CleanupOutcome,
    FailureCause,
    FinalStatus,
    GateOutcome,
    QualificationEvidence,
    QualificationResult,
)


class ResultError(ValueError):
    """Structurally or semantically invalid final result."""


def result_to_document(result: QualificationResult) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": 1,
        "run_id": result.run_id,
        "status": result.status.value,
        "started_utc": result.started_utc.isoformat().replace("+00:00", "Z"),
        "preflight_passed": result.preflight_passed,
        "carrier_gate": result.carrier_gate.value,
        "decode_gate": result.decode_gate.value,
        "cleanup_outcome": result.cleanup_outcome.value,
        "failure_causes": [cause.value for cause in result.failure_causes],
        "artifacts": [
            {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
            for item in result.artifacts
        ],
    }
    if result.completed_utc is not None:
        document["completed_utc"] = result.completed_utc.isoformat().replace("+00:00", "Z")
    if result.reason is not None:
        document["reason"] = result.reason
    return document


def _validate_evidence_consistency(evidence: QualificationEvidence) -> None:
    """Reject evidence combinations that cannot describe one valid run."""
    causes = set(evidence.failure_causes)
    carrier = evidence.carrier_gate
    decode = evidence.decode_gate

    contradictions: list[str] = []
    if decode is not GateOutcome.NOT_RUN and carrier is not GateOutcome.PASSED:
        contradictions.append("decode gate ran without a passing carrier gate")
    if not evidence.preflight_passed and (
        carrier is not GateOutcome.NOT_RUN or decode is not GateOutcome.NOT_RUN
    ):
        contradictions.append("qualification gates ran after failed preflight")

    has_cleanup_cause = FailureCause.CLEANUP in causes
    if has_cleanup_cause != (evidence.cleanup is CleanupOutcome.FAILED):
        contradictions.append("cleanup cause and cleanup outcome disagree")

    if causes & FIXTURE_CAUSES and carrier is GateOutcome.PASSED and decode is GateOutcome.PASSED:
        contradictions.append("fixture blockage coexists with passing qualification gates")

    has_carrier_cause = FailureCause.TRANSMITTER_CARRIER in causes
    if has_carrier_cause != (carrier is GateOutcome.FAILED):
        contradictions.append("transmitter carrier cause and carrier gate disagree")

    has_decode_cause = FailureCause.TRANSMITTER_DECODE in causes
    decode_failure = carrier is GateOutcome.PASSED and decode is GateOutcome.FAILED
    if has_decode_cause != decode_failure:
        contradictions.append("transmitter decode cause and gate outcomes disagree")

    has_preflight_cause = FailureCause.PREFLIGHT in causes
    if has_preflight_cause != (not evidence.preflight_passed):
        contradictions.append("preflight cause and preflight outcome disagree")

    abort_causes = {FailureCause.OPERATOR_ABORT, FailureCause.EXTERNAL_ABORT}
    if causes & abort_causes and carrier is GateOutcome.PASSED and decode is GateOutcome.PASSED:
        contradictions.append("abort cause coexists with passing qualification gates")

    if contradictions:
        raise ResultError("$: contradictory evidence: " + "; ".join(contradictions))


def validate_result_document(document: dict[str, Any]) -> FinalStatus:
    schema_resource = files("wsprrypi_qualification.schemas").joinpath("result.schema.json")
    schema = cast(dict[str, Any], json.loads(schema_resource.read_text(encoding="utf-8")))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = "$" + "".join(f"[{item!r}]" for item in error.absolute_path)
        raise ResultError(f"{location}: {error.message}")
    causes = tuple(FailureCause(value) for value in document["failure_causes"])
    evidence = QualificationEvidence(
        preflight_passed=document["preflight_passed"],
        carrier_gate=GateOutcome(document["carrier_gate"]),
        decode_gate=GateOutcome(document["decode_gate"]),
        cleanup=CleanupOutcome(document["cleanup_outcome"]),
        failure_causes=causes,
        aborted=bool(set(causes) & {FailureCause.OPERATOR_ABORT, FailureCause.EXTERNAL_ABORT}),
    )
    _validate_evidence_consistency(evidence)
    expected = classify_result(evidence)
    actual = FinalStatus(document["status"])
    if actual is not expected:
        raise ResultError(
            f"$.status: {actual.value!r} contradicts evidence; expected {expected.value!r}"
        )
    return actual
