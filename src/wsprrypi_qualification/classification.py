"""Pure final-result classification with explicit precedence."""

from wsprrypi_qualification.models import (
    CleanupOutcome,
    FailureCause,
    FinalStatus,
    GateOutcome,
    QualificationEvidence,
)

FIXTURE_CAUSES = {
    FailureCause.RECEIVER_UNAVAILABLE,
    FailureCause.RECEIVER_OVERLOAD,
    FailureCause.RF_PATH_UNSAFE,
    FailureCause.OWNERSHIP_CONFLICT,
    FailureCause.UNSUPPORTED_CAPABILITY,
    FailureCause.DEPENDENCY_UNAVAILABLE,
}


def classify_result(evidence: QualificationEvidence) -> FinalStatus:
    causes = set(evidence.failure_causes)
    if evidence.cleanup is CleanupOutcome.FAILED or FailureCause.CLEANUP in causes:
        return FinalStatus.CLEANUP_FAILED
    if evidence.aborted or causes & {FailureCause.OPERATOR_ABORT, FailureCause.EXTERNAL_ABORT}:
        return FinalStatus.ABORTED
    if causes & FIXTURE_CAUSES or GateOutcome.BLOCKED in {
        evidence.carrier_gate,
        evidence.decode_gate,
    }:
        return FinalStatus.FIXTURE_BLOCKED
    if not evidence.preflight_passed or FailureCause.PREFLIGHT in causes:
        return FinalStatus.PREFLIGHT_FAILED
    if evidence.carrier_gate is GateOutcome.FAILED and FailureCause.TRANSMITTER_CARRIER in causes:
        return FinalStatus.UNQUALIFIED_CARRIER
    if (
        evidence.carrier_gate is GateOutcome.PASSED
        and evidence.decode_gate is GateOutcome.FAILED
        and FailureCause.TRANSMITTER_DECODE in causes
    ):
        return FinalStatus.UNQUALIFIED_DECODE
    if (
        evidence.carrier_gate is GateOutcome.PASSED
        and evidence.decode_gate is GateOutcome.PASSED
        and evidence.cleanup is CleanupOutcome.VERIFIED
        and not causes
    ):
        return FinalStatus.QUALIFIED
    return FinalStatus.INCONCLUSIVE
