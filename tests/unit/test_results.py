from datetime import UTC, datetime

import pytest

from wsprrypi_qualification.models import (
    CleanupOutcome,
    FailureCause,
    FinalStatus,
    GateOutcome,
    QualificationResult,
)
from wsprrypi_qualification.results import ResultError, result_to_document, validate_result_document


def result(
    *,
    preflight: bool = True,
    carrier: GateOutcome = GateOutcome.PASSED,
    decode: GateOutcome = GateOutcome.PASSED,
    cleanup: CleanupOutcome = CleanupOutcome.VERIFIED,
    causes: tuple[FailureCause, ...] = (),
) -> QualificationResult:
    return QualificationResult(
        run_id="20260811T120000Z-test-id",
        started_utc=datetime(2026, 8, 11, 12, tzinfo=UTC),
        completed_utc=datetime(2026, 8, 11, 12, 10, tzinfo=UTC),
        preflight_passed=preflight,
        carrier_gate=carrier,
        decode_gate=decode,
        cleanup_outcome=cleanup,
        failure_causes=causes,
        artifacts=(),
    )


@pytest.mark.parametrize(
    "item",
    [
        result(),
        result(
            carrier=GateOutcome.FAILED,
            decode=GateOutcome.NOT_RUN,
            causes=(FailureCause.TRANSMITTER_CARRIER,),
        ),
        result(decode=GateOutcome.FAILED, causes=(FailureCause.TRANSMITTER_DECODE,)),
        result(
            carrier=GateOutcome.BLOCKED,
            decode=GateOutcome.NOT_RUN,
            causes=(FailureCause.RECEIVER_UNAVAILABLE,),
        ),
        result(
            preflight=False,
            carrier=GateOutcome.NOT_RUN,
            decode=GateOutcome.NOT_RUN,
            causes=(FailureCause.PREFLIGHT,),
        ),
        result(
            carrier=GateOutcome.NOT_RUN,
            decode=GateOutcome.NOT_RUN,
            causes=(FailureCause.OPERATOR_ABORT,),
        ),
        result(cleanup=CleanupOutcome.FAILED, causes=(FailureCause.CLEANUP,)),
        result(decode=GateOutcome.INCONCLUSIVE, causes=(FailureCause.INCOMPLETE_EVIDENCE,)),
    ],
)
def test_serialized_results_pass_schema_and_semantics(item: QualificationResult) -> None:
    document = result_to_document(item)
    assert validate_result_document(document) is item.status


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preflight_passed", False),
        ("carrier_gate", "failed"),
        ("cleanup_outcome", "unknown"),
        ("failure_causes", ["cleanup"]),
    ],
)
def test_contradictory_qualified_document_is_rejected(field: str, value: object) -> None:
    document = result_to_document(result())
    document[field] = value
    with pytest.raises(ResultError, match="contradict"):
        validate_result_document(document)


@pytest.mark.parametrize(
    "changes",
    [
        {
            "carrier_gate": "failed",
            "decode_gate": "passed",
            "failure_causes": ["transmitter_carrier"],
            "status": "unqualified_carrier",
        },
        {
            "cleanup_outcome": "verified",
            "failure_causes": ["cleanup"],
            "status": "cleanup_failed",
        },
        {
            "failure_causes": ["receiver_unavailable"],
            "status": "fixture_blocked",
        },
        {
            "preflight_passed": False,
            "failure_causes": ["preflight"],
            "status": "preflight_failed",
        },
        {
            "failure_causes": ["operator_abort"],
            "status": "aborted",
        },
        {
            "carrier_gate": "failed",
            "decode_gate": "not_run",
            "failure_causes": [],
            "status": "inconclusive",
        },
        {
            "carrier_gate": "passed",
            "decode_gate": "failed",
            "failure_causes": [],
            "status": "inconclusive",
        },
        {
            "preflight_passed": False,
            "carrier_gate": "not_run",
            "decode_gate": "not_run",
            "failure_causes": [],
            "status": "preflight_failed",
        },
        {
            "cleanup_outcome": "failed",
            "failure_causes": [],
            "status": "cleanup_failed",
        },
    ],
)
def test_matching_status_does_not_make_contradictory_evidence_valid(
    changes: dict[str, object],
) -> None:
    document = result_to_document(result())
    document.update(changes)
    with pytest.raises(ResultError, match="contradictory evidence"):
        validate_result_document(document)


def test_invalid_failure_cause_combination_is_structurally_rejected() -> None:
    document = result_to_document(result())
    document["failure_causes"] = ["none", "cleanup"]
    with pytest.raises(ResultError):
        validate_result_document(document)


def test_typed_status_is_always_derived() -> None:
    item = result(cleanup=CleanupOutcome.FAILED)
    assert item.status is FinalStatus.CLEANUP_FAILED
