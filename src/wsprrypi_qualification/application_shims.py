"""Hardware-free application plans for transmitter-specific command interfaces."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Protocol

from jsonschema import Draft202012Validator


class ApplicationPlanError(ValueError):
    """A requested application/protocol plan cannot be constructed safely."""


class ProtocolMode(StrEnum):
    WSPR = "wspr"
    QRSS = "qrss"
    FSKCW = "fskcw"
    DFCW = "dfcw"
    HELLSCHREIBER = "hellschreiber"


@dataclass(frozen=True)
class ApplicationIdentity:
    application: str
    executable: Path
    source_revision: str
    submodule_revision: str


@dataclass(frozen=True)
class WsprProtocol:
    callsign: str
    grid: str
    power_dbm: int
    requested_rf_frequency_hz: float
    frame_count: int
    audio_offset_hz: float


@dataclass(frozen=True)
class CwProtocol:
    mode: ProtocolMode
    message: str
    dot_seconds: float
    primary_frequency_hz: float
    secondary_frequency_hz: float | None = None


ProtocolPlan = WsprProtocol | CwProtocol


@dataclass(frozen=True)
class ApplicationPlan:
    plan_id: str
    identity: ApplicationIdentity
    backend: str
    protocol: ProtocolMode
    protocol_contract: dict[str, object]
    arguments: tuple[str, ...]
    self_terminating_request: bool
    supervisor_required: bool
    random_offset_enabled: bool
    execution_authorized: bool
    stopping_contract: str
    cleanup_contract: str

    def to_document(self) -> dict[str, object]:
        identity = asdict(self.identity)
        identity["executable"] = str(self.identity.executable)
        return {
            "schema_version": 1,
            "evidence_type": "application_plan",
            "plan_id": self.plan_id,
            "identity": identity,
            "backend": self.backend,
            "protocol": self.protocol.value,
            "protocol_contract": self.protocol_contract,
            "arguments": list(self.arguments),
            "self_terminating_request": self.self_terminating_request,
            "supervisor_required": self.supervisor_required,
            "random_offset_enabled": self.random_offset_enabled,
            "execution_authorized": self.execution_authorized,
            "stopping_contract": self.stopping_contract,
            "cleanup_contract": self.cleanup_contract,
        }


class ApplicationShim(Protocol):
    """Interface implemented by application-specific plan builders."""

    def supported_protocols(self) -> frozenset[ProtocolMode]: ...

    def resolve_plan(self, plan_id: str, protocol: ProtocolPlan) -> ApplicationPlan: ...


class WsprryPiShim:
    """Translate protocol intent into reviewed WsprryPi argv without executing it."""

    _SUPPORTED = frozenset(
        {ProtocolMode.WSPR, ProtocolMode.QRSS, ProtocolMode.FSKCW, ProtocolMode.DFCW}
    )
    _WSPR_POWER_LEVELS = frozenset(
        {0, 3, 7, 10, 13, 17, 20, 23, 27, 30, 33, 37, 40, 43, 47, 50, 53, 57, 60}
    )

    def __init__(self, identity: ApplicationIdentity, *, backend: str) -> None:
        if identity.application != "wsprrypi":
            raise ApplicationPlanError("WsprryPi shim requires application identity 'wsprrypi'")
        if backend not in {"gpio", "si5351"}:
            raise ApplicationPlanError("WsprryPi backend must be 'gpio' or 'si5351'")
        self.identity = identity
        self.backend = backend

    def supported_protocols(self) -> frozenset[ProtocolMode]:
        return self._SUPPORTED

    @staticmethod
    def _number(value: float) -> str:
        if not math.isfinite(value) or value <= 0:
            raise ApplicationPlanError("frequencies and durations must be positive")
        return format(value, ".15g")

    def resolve_plan(self, plan_id: str, protocol: ProtocolPlan) -> ApplicationPlan:
        if not plan_id.strip():
            raise ApplicationPlanError("plan_id must not be empty")
        common = (str(self.identity.executable), "--backend", self.backend, "--no-offset")
        arguments: tuple[str, ...]
        if isinstance(protocol, WsprProtocol):
            if protocol.frame_count <= 0:
                raise ApplicationPlanError("WSPR frame_count must be positive")
            if not protocol.callsign.strip() or not protocol.grid.strip():
                raise ApplicationPlanError("WSPR identity must be explicit")
            if (
                protocol.callsign != protocol.callsign.upper()
                or protocol.grid != protocol.grid.upper()
            ):
                raise ApplicationPlanError("WSPR callsign and grid must use canonical uppercase")
            if protocol.power_dbm not in self._WSPR_POWER_LEVELS:
                raise ApplicationPlanError("WSPR power must be a standard encoded dBm value")
            if protocol.audio_offset_hz != 1500.0:
                raise ApplicationPlanError(
                    "direct-CLI WsprryPi requires a 1500 Hz WSPR audio offset"
                )
            mode = ProtocolMode.WSPR
            dial_frequency_hz = protocol.requested_rf_frequency_hz - protocol.audio_offset_hz
            if dial_frequency_hz <= 0 or protocol.audio_offset_hz < 0:
                raise ApplicationPlanError("WSPR RF and audio-offset frequency contract is invalid")
            protocol_contract: dict[str, object] = {
                "callsign": protocol.callsign,
                "grid": protocol.grid,
                "power_dbm": protocol.power_dbm,
                "frame_count": protocol.frame_count,
                "dial_frequency_hz": dial_frequency_hz,
                "audio_offset_hz": protocol.audio_offset_hz,
                "requested_rf_frequency_hz": protocol.requested_rf_frequency_hz,
            }
            arguments = (
                *common,
                "--terminate",
                str(protocol.frame_count),
                protocol.callsign,
                protocol.grid,
                str(protocol.power_dbm),
                self._number(dial_frequency_hz),
            )
        else:
            mode = protocol.mode
            if mode not in self._SUPPORTED:
                raise ApplicationPlanError(f"WsprryPi protocol is unsupported: {mode.value}")
            if not protocol.message.strip():
                raise ApplicationPlanError("CW message must be explicit")
            dot = self._number(protocol.dot_seconds)
            primary = self._number(protocol.primary_frequency_hz)
            protocol_contract = {
                "message": protocol.message,
                "dot_seconds": protocol.dot_seconds,
                "primary_frequency_hz": protocol.primary_frequency_hz,
                "secondary_frequency_hz": protocol.secondary_frequency_hz,
            }
            if mode is ProtocolMode.QRSS:
                arguments = (
                    *common,
                    "--qrss-message",
                    protocol.message,
                    "--qrss-frequency",
                    primary,
                    "--qrss-dot-seconds",
                    dot,
                )
            elif mode is ProtocolMode.FSKCW:
                secondary = self._number(protocol.secondary_frequency_hz or 0.0)
                if protocol.primary_frequency_hz <= (protocol.secondary_frequency_hz or 0.0):
                    raise ApplicationPlanError("FSKCW mark frequency must be greater than space")
                arguments = (
                    *common,
                    "--fskcw-message",
                    protocol.message,
                    "--fskcw-mark-frequency",
                    primary,
                    "--fskcw-space-frequency",
                    secondary,
                    "--fskcw-dot-seconds",
                    dot,
                )
            elif mode is ProtocolMode.DFCW:
                secondary = self._number(protocol.secondary_frequency_hz or 0.0)
                if protocol.primary_frequency_hz == protocol.secondary_frequency_hz:
                    raise ApplicationPlanError("DFCW dot and dash frequencies must differ")
                arguments = (
                    *common,
                    "--dfcw-message",
                    protocol.message,
                    "--dfcw-dot-frequency",
                    primary,
                    "--dfcw-dash-frequency",
                    secondary,
                    "--dfcw-dot-seconds",
                    dot,
                )
            else:
                raise ApplicationPlanError(f"WsprryPi protocol is unsupported: {mode.value}")
        return ApplicationPlan(
            plan_id=plan_id,
            identity=self.identity,
            backend=self.backend,
            protocol=mode,
            protocol_contract=protocol_contract,
            arguments=arguments,
            self_terminating_request=True,
            supervisor_required=True,
            random_offset_enabled=False,
            execution_authorized=False,
            stopping_contract="future supervisor deadline and application termination",
            cleanup_contract="future backend-specific disable and verified quiescence",
        )


def validate_application_plan(document: dict[str, object]) -> None:
    """Validate a serialized plan; validation never executes the application."""
    schema = json.loads(
        files("wsprrypi_qualification.schemas")
        .joinpath("application-plan.schema.json")
        .read_text(encoding="utf-8")
    )
    errors = sorted(Draft202012Validator(schema).iter_errors(document), key=lambda e: list(e.path))
    if errors:
        raise ApplicationPlanError("invalid application plan: " + errors[0].message)
    arguments = document["arguments"]
    identity = document["identity"]
    if not isinstance(arguments, list) or not isinstance(identity, dict):
        raise ApplicationPlanError("application plan has invalid structured fields")
    expected_prefix = [
        identity["executable"],
        "--backend",
        document["backend"],
        "--no-offset",
    ]
    if arguments[:4] != expected_prefix:
        raise ApplicationPlanError("arguments do not match executable/backend safety contract")
    protocol = document["protocol"]
    contract = document["protocol_contract"]
    if not isinstance(contract, dict):
        raise ApplicationPlanError("protocol contract must be an object")
    if protocol == "wspr" and (
        type(contract.get("frame_count")) is not int or type(contract.get("power_dbm")) is not int
    ):
        raise ApplicationPlanError("WSPR frame_count and power_dbm must be JSON integers")
    try:
        application_identity = ApplicationIdentity(
            str(identity["application"]),
            Path(str(identity["executable"])),
            str(identity["source_revision"]),
            str(identity["submodule_revision"]),
        )
        if protocol == "wspr":
            offset = float(contract["audio_offset_hz"])
            requested = float(contract["requested_rf_frequency_hz"])
            if abs((float(contract["dial_frequency_hz"]) + offset) - requested) > 1e-9:
                raise ApplicationPlanError("WSPR dial/audio/RF frequency contract is inconsistent")
            requested_protocol: ProtocolPlan = WsprProtocol(
                str(contract["callsign"]),
                str(contract["grid"]),
                int(contract["power_dbm"]),
                requested,
                int(contract["frame_count"]),
                offset,
            )
        else:
            requested_protocol = CwProtocol(
                ProtocolMode(str(protocol)),
                str(contract["message"]),
                float(contract["dot_seconds"]),
                float(contract["primary_frequency_hz"]),
                None
                if contract["secondary_frequency_hz"] is None
                else float(contract["secondary_frequency_hz"]),
            )
        reconstructed = WsprryPiShim(
            application_identity, backend=str(document["backend"])
        ).resolve_plan(str(document["plan_id"]), requested_protocol)
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ApplicationPlanError):
            raise
        raise ApplicationPlanError(f"invalid protocol contract: {error}") from error
    if list(reconstructed.arguments) != arguments:
        raise ApplicationPlanError("arguments do not match the declared protocol contract")
    if reconstructed.protocol_contract != contract:
        raise ApplicationPlanError("protocol contract contains lossy or contradictory values")
