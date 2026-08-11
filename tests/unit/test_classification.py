from datetime import UTC, datetime

import pytest

from wsprrypi_qualification.classification import classify_result
from wsprrypi_qualification.models import (
    CleanupOutcome,
    FailureCause,
    FinalStatus,
    GateOutcome,
    QualificationEvidence,
    QualificationResult,
)


def evidence(
    *,
    preflight: bool = True,
    carrier: GateOutcome = GateOutcome.PASSED,
    decode: GateOutcome = GateOutcome.PASSED,
    cleanup: CleanupOutcome = CleanupOutcome.VERIFIED,
    causes: tuple[FailureCause, ...] = (),
    aborted: bool = False,
) -> QualificationEvidence:
    return QualificationEvidence(preflight, carrier, decode, cleanup, causes, aborted)


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (evidence(), FinalStatus.QUALIFIED),
        (
            evidence(
                carrier=GateOutcome.FAILED,
                decode=GateOutcome.NOT_RUN,
                causes=(FailureCause.TRANSMITTER_CARRIER,),
            ),
            FinalStatus.UNQUALIFIED_CARRIER,
        ),
        (
            evidence(decode=GateOutcome.FAILED, causes=(FailureCause.TRANSMITTER_DECODE,)),
            FinalStatus.UNQUALIFIED_DECODE,
        ),
        (
            evidence(
                carrier=GateOutcome.BLOCKED,
                decode=GateOutcome.NOT_RUN,
                causes=(FailureCause.RECEIVER_UNAVAILABLE,),
            ),
            FinalStatus.FIXTURE_BLOCKED,
        ),
        (evidence(preflight=False, causes=(FailureCause.PREFLIGHT,)), FinalStatus.PREFLIGHT_FAILED),
        (evidence(aborted=True), FinalStatus.ABORTED),
        (evidence(cleanup=CleanupOutcome.FAILED), FinalStatus.CLEANUP_FAILED),
        (
            evidence(decode=GateOutcome.INCONCLUSIVE, causes=(FailureCause.INCOMPLETE_EVIDENCE,)),
            FinalStatus.INCONCLUSIVE,
        ),
    ],
)
def test_all_final_states(item: QualificationEvidence, expected: FinalStatus) -> None:
    assert classify_result(item) is expected


def test_cleanup_failure_overrides_qualification() -> None:
    assert classify_result(evidence(cleanup=CleanupOutcome.FAILED)) is FinalStatus.CLEANUP_FAILED


def test_fixture_blockage_is_not_transmitter_failure() -> None:
    item = evidence(
        carrier=GateOutcome.BLOCKED,
        decode=GateOutcome.NOT_RUN,
        causes=(FailureCause.RF_PATH_UNSAFE,),
    )
    assert classify_result(item) is FinalStatus.FIXTURE_BLOCKED


def test_fixture_blockage_discovered_during_preflight_remains_fixture_blocked() -> None:
    item = evidence(
        preflight=False,
        carrier=GateOutcome.NOT_RUN,
        decode=GateOutcome.NOT_RUN,
        causes=(FailureCause.RECEIVER_UNAVAILABLE,),
    )
    assert classify_result(item) is FinalStatus.FIXTURE_BLOCKED


def test_typed_result_keeps_outcomes_separate() -> None:
    result = QualificationResult(
        run_id="20260811T120000Z-test-id",
        started_utc=datetime(2026, 8, 11, 12, tzinfo=UTC),
        completed_utc=None,
        preflight_passed=True,
        carrier_gate=GateOutcome.PASSED,
        decode_gate=GateOutcome.INCONCLUSIVE,
        cleanup_outcome=CleanupOutcome.VERIFIED,
        failure_causes=(FailureCause.INCOMPLETE_EVIDENCE,),
        artifacts=(),
    )
    assert result.status is FinalStatus.INCONCLUSIVE
    assert result.cleanup_outcome is CleanupOutcome.VERIFIED
