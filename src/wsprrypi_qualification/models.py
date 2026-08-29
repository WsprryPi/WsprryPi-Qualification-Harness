"""Typed domain models for profiles, evidence, and result classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePath
from typing import Any


class Transport(StrEnum):
    LOCAL = "local"
    SSH = "ssh"


class Backend(StrEnum):
    GPIO = "gpio"
    SI5351 = "si5351"
    RP1_GPCLK = "rp1_gpclk"


class PathType(StrEnum):
    CONDUCTED = "conducted"
    RADIATED = "radiated"
    UNKNOWN = "unknown"
    OTHER = "other"


class AuthorizationScope(StrEnum):
    SINGLE_RUN = "single_run"
    UNIVERSAL = "universal"


class CapabilityState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    NOT_IMPLEMENTED = "not_implemented"


class GateOutcome(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"


class CleanupOutcome(StrEnum):
    NOT_REQUIRED = "not_required"
    VERIFIED = "verified"
    FAILED = "failed"
    UNKNOWN = "unknown"


class FailureCause(StrEnum):
    TRANSMITTER_CARRIER = "transmitter_carrier"
    TRANSMITTER_DECODE = "transmitter_decode"
    RECEIVER_UNAVAILABLE = "receiver_unavailable"
    RECEIVER_OVERLOAD = "receiver_overload"
    RF_PATH_UNSAFE = "rf_path_unsafe"
    OWNERSHIP_CONFLICT = "ownership_conflict"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    PREFLIGHT = "preflight"
    OPERATOR_ABORT = "operator_abort"
    EXTERNAL_ABORT = "external_abort"
    INCOMPLETE_EVIDENCE = "incomplete_evidence"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    CLEANUP = "cleanup"


class FinalStatus(StrEnum):
    QUALIFIED = "qualified"
    UNQUALIFIED_CARRIER = "unqualified_carrier"
    UNQUALIFIED_DECODE = "unqualified_decode"
    FIXTURE_BLOCKED = "fixture_blocked"
    PREFLIGHT_FAILED = "preflight_failed"
    ABORTED = "aborted"
    CLEANUP_FAILED = "cleanup_failed"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class ReceiverConfig:
    transport: Transport
    driver: str
    sample_rate_hz: int
    bandwidth_hz: int
    sample_format: str
    agc: bool
    host: str | None = None
    serial: str | None = None
    channel: int = 0
    bias_tee: bool = False


@dataclass(frozen=True)
class RfPathConfig:
    path_type: PathType
    antenna_connected: bool | None
    termination_ohms: float | None
    attenuation_db: float | None
    filter_description: str
    safe_input_description: str


@dataclass(frozen=True)
class BenchProfile:
    schema_version: int
    bench_id: str
    receiver: ReceiverConfig
    rf_path: RfPathConfig


@dataclass(frozen=True)
class ReceiverRunAuthorization:
    scope: AuthorizationScope
    reference: str
    recorded_utc: datetime


@dataclass(frozen=True)
class ReceiverRunLimits:
    sample_count: int
    read_timeout_us: int
    helper_deadline_s: float
    external_deadline_s: float


@dataclass(frozen=True)
class ReceiverRunProfile:
    schema_version: int
    run_id: str
    bench_id: str
    receiver: ReceiverConfig
    center_frequency_hz: float
    gain_db: float
    duration_s: int
    rf_path: RfPathConfig
    limits: ReceiverRunLimits
    authorization: ReceiverRunAuthorization
    ownership_and_cleanup: str


@dataclass(frozen=True)
class TransmitterConfig:
    transport: Transport
    host: str
    backend: Backend
    output: str
    model: str | None = None
    hardware_description: str | None = None
    oscillator_description: str | None = None
    reference_frequency_hz: int | None = None
    source_revision: str | None = None
    submodule_revision: str | None = None
    i2c_bus: int | None = None
    i2c_address: str | None = None
    drive_ma: int | None = None
    gpio_pin: int | None = None
    power_level: int | None = None
    pacing_clocks: int | None = None


@dataclass(frozen=True)
class WsprIdentity:
    callsign: str
    grid: str
    power_dbm: int


@dataclass(frozen=True)
class QualificationGates:
    carrier_offset_max_hz: float
    frequency_acquisition_half_width_hz: float
    best_20hz_share_min: float
    required_consecutive_decodes: int


@dataclass(frozen=True)
class StoppingProcedure:
    transmitter_termination: str
    receiver_termination: str
    operator_abort: str
    cleanup_expectation: str
    emergency_stop_note: str


@dataclass(frozen=True)
class TestProfile:
    schema_version: int
    test_id: str
    transmitter: TransmitterConfig
    band: str
    frequency_hz: float
    receiver_center_hz: float
    receiver_gain_db: float
    identity: WsprIdentity
    gates: QualificationGates
    stopping_procedure: StoppingProcedure
    ppm: float | None = None
    frame_count: int = 3
    bounded_duration_s: int | None = None
    random_offset_enabled: bool = False


@dataclass(frozen=True)
class CapabilityResult:
    name: str
    state: CapabilityState
    reason: str
    path: PurePath | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "state": self.state.value,
            "reason": self.reason,
        }
        if self.path is not None:
            result["path"] = str(self.path)
        return result


@dataclass(frozen=True)
class ArtifactRecord:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class QualificationEvidence:
    preflight_passed: bool
    carrier_gate: GateOutcome
    decode_gate: GateOutcome
    cleanup: CleanupOutcome
    failure_causes: tuple[FailureCause, ...] = ()
    aborted: bool = False


@dataclass(frozen=True)
class QualificationResult:
    run_id: str
    started_utc: datetime
    completed_utc: datetime | None
    preflight_passed: bool
    carrier_gate: GateOutcome
    decode_gate: GateOutcome
    cleanup_outcome: CleanupOutcome
    failure_causes: tuple[FailureCause, ...]
    artifacts: tuple[ArtifactRecord, ...]
    reason: str | None = None

    @property
    def status(self) -> FinalStatus:
        from wsprrypi_qualification.classification import classify_result

        return classify_result(
            QualificationEvidence(
                preflight_passed=self.preflight_passed,
                carrier_gate=self.carrier_gate,
                decode_gate=self.decode_gate,
                cleanup=self.cleanup_outcome,
                failure_causes=self.failure_causes,
                aborted=bool(
                    set(self.failure_causes)
                    & {FailureCause.OPERATOR_ABORT, FailureCause.EXTERNAL_ABORT}
                ),
            )
        )
