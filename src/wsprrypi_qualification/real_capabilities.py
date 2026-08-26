"""Fail-closed production capability contracts with sealed hardware-free providers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

from wsprrypi_qualification.adapters import OperationOutcome
from wsprrypi_qualification.application_shims import ApplicationPlan, validate_application_plan
from wsprrypi_qualification.capability_helper import PROTOCOL_VERSION, encode_request
from wsprrypi_qualification.capture_metadata import load_capture_metadata
from wsprrypi_qualification.offline import artifact, validate_document, write_json_new
from wsprrypi_qualification.transports import CommandPlan, LocalCommandTransport


class CapabilityError(RuntimeError):
    """A production capability cannot satisfy its safety/evidence contract."""


class CaptureCapabilityError(CapabilityError):
    """A capture failed while retaining a bounded execution diagnostic."""

    def __init__(self, message: str, diagnostic_path: Path | None = None) -> None:
        super().__init__(message)
        self.diagnostic_path = diagnostic_path


class CapabilityKind(StrEnum):
    SSH = "ssh"
    SOAPY_CAPTURE = "soapy_capture"
    WSPRRYPI = "wsprrypi"
    SERVICE = "service"
    GPIO_QUIESCENCE = "gpio_quiescence"
    SI5351_QUIESCENCE = "si5351_quiescence"


@dataclass(frozen=True)
class ResolvedCapabilityPlan:
    """Immutable cross-adapter plan; runtime authorization is intentionally absent."""

    run_id: str
    transport: str
    receiver_enabled: bool
    transmitter_enabled: bool
    service_names: tuple[str, ...]
    quiescence_backend: str
    overall_timeout_s: float
    capability_bindings: tuple[str, ...] = ()
    external_access_enabled: bool = False
    rf_enabled: bool = False

    def document(self) -> dict[str, object]:
        document = {
            "schema_version": 1,
            "plan_type": "resolved_real_capability_plan",
            "run_id": self.run_id,
            "transport": self.transport,
            "receiver_enabled": self.receiver_enabled,
            "transmitter_enabled": self.transmitter_enabled,
            "service_names": list(self.service_names),
            "quiescence_backend": self.quiescence_backend,
            "overall_timeout_s": self.overall_timeout_s,
            "capability_bindings": list(self.capability_bindings),
            "external_access_enabled": self.external_access_enabled,
            "rf_enabled": self.rf_enabled,
        }
        validate_resolved_capability_plan(document)
        return document


def validate_resolved_capability_plan(document: dict[str, object]) -> None:
    validate_document(document, "resolved-capability-plan.schema.json")
    if document["transmitter_enabled"] is True and document["rf_enabled"] is not True:
        raise CapabilityError("transmitter planning requires explicit RF enablement")
    if (document["receiver_enabled"] is True or document["transmitter_enabled"] is True) and (
        document["external_access_enabled"] is not True
    ):
        raise CapabilityError("external capability planning requires explicit enablement")
    if document["quiescence_backend"] == "none" and document["transmitter_enabled"] is True:
        raise CapabilityError("transmitter planning requires backend quiescence")
    bindings = cast(list[str], document["capability_bindings"])
    prefixes = [item.split(":", 1)[0] for item in bindings]
    expected: list[str] = []
    if document["transport"] == "ssh":
        expected.append("ssh_capability_execution")
    if document["receiver_enabled"] is True:
        expected.append("soapy_capture_capability")
    if document["transmitter_enabled"] is True:
        expected.append("wsprrypi_process_capability")
    expected.extend(["service_capability"] * len(cast(list[str], document["service_names"])))
    if document["quiescence_backend"] == "gpio":
        expected.append("gpio_quiescence_capability")
    elif document["quiescence_backend"] == "si5351":
        expected.append("si5351_quiescence_capability")
    if sorted(prefixes) != sorted(expected):
        raise CapabilityError("resolved capability bindings are incomplete or unexpected")


def compose_capability_session(
    plan: ResolvedCapabilityPlan, evidence: tuple[dict[str, object], ...]
) -> dict[str, object]:
    """Compose already acquired adapter evidence without executing any provider."""

    plan_document = plan.document()
    expected: set[str] = set()
    if plan.transport == "ssh":
        expected.add("ssh_capability_execution")
    if plan.receiver_enabled:
        expected.add("soapy_capture_capability")
    if plan.transmitter_enabled:
        expected.add("wsprrypi_process_capability")
    if plan.service_names:
        expected.add("service_capability")
    if plan.quiescence_backend == "gpio":
        expected.add("gpio_quiescence_capability")
    elif plan.quiescence_backend == "si5351":
        expected.add("si5351_quiescence_capability")
    observed: set[str] = set()
    observed_bindings: list[str] = []
    cleanup_verified = True
    for item in evidence:
        validate_capability_semantics(item)
        evidence_type = item.get("evidence_type")
        if not isinstance(evidence_type, str) or evidence_type not in expected:
            raise CapabilityError("unexpected capability evidence for resolved plan")
        if evidence_type in observed and evidence_type != "service_capability":
            raise CapabilityError("duplicate capability evidence")
        observed.add(evidence_type)
        observed_bindings.append(f"{evidence_type}:{_evidence_plan_sha256(item)}")
        cleanup_verified = cleanup_verified and item.get("outcome") not in {
            "cleanup_failed",
            "failed",
        }
    if observed != expected:
        raise CapabilityError("capability evidence is incomplete for resolved plan")
    if sorted(observed_bindings) != sorted(plan.capability_bindings):
        raise CapabilityError("capability evidence does not match resolved plan bindings")
    document = {
        "schema_version": 1,
        "evidence_type": "real_capability_session",
        "plan": plan_document,
        "plan_sha256": capability_plan_sha256(plan_document),
        "capabilities": list(evidence),
        "cleanup_verified": cleanup_verified,
        "qualification_status": "inconclusive",
    }
    validate_document(document, "real-capability-session.schema.json")
    validate_capability_session_document(document)
    return document


def validate_capability_session_document(document: dict[str, object]) -> None:
    validate_document(document, "real-capability-session.schema.json")
    plan = document.get("plan")
    capabilities = document.get("capabilities")
    if not isinstance(plan, dict) or not isinstance(capabilities, list):
        raise CapabilityError("capability session is malformed")
    validate_resolved_capability_plan(cast(dict[str, object], plan))
    if document.get("plan_sha256") != capability_plan_sha256(plan):
        raise CapabilityError("capability session plan digest is invalid")
    bindings = plan.get("capability_bindings")
    if not isinstance(bindings, list):
        raise CapabilityError("capability session bindings are malformed")
    actual = []
    cleanup_verified = True
    for item in capabilities:
        if not isinstance(item, dict):
            raise CapabilityError("capability session evidence is malformed")
        typed = cast(dict[str, object], item)
        validate_capability_semantics(typed)
        actual.append(f"{typed['evidence_type']}:{_evidence_plan_sha256(typed)}")
        cleanup_verified = cleanup_verified and typed.get("outcome") not in {
            "cleanup_failed",
            "failed",
        }
    if sorted(actual) != sorted(bindings):
        raise CapabilityError("capability session evidence bindings are invalid")
    if document.get("cleanup_verified") is not cleanup_verified:
        raise CapabilityError("capability session cleanup summary is contradictory")


@dataclass(frozen=True)
class RuntimeAuthorization:
    plan_sha256: str
    operator: str
    recorded_utc: datetime
    external_access_authorized: bool
    rf_authorized: bool = False


def _authorize_rf(plan: object, authorization: RuntimeAuthorization | None) -> None:
    _authorize(plan, authorization)
    if authorization is None or not authorization.rf_authorized:
        raise CapabilityError("ephemeral RF authorization is required for transmitter launch")


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def capability_plan_sha256(value: object) -> str:
    """Return the canonical digest used by ephemeral runtime authorization."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _authorize(plan: object, authorization: RuntimeAuthorization | None) -> None:
    if authorization is None or not authorization.external_access_authorized:
        raise CapabilityError("ephemeral external-access authorization is required")
    if authorization.plan_sha256 != capability_plan_sha256(plan):
        raise CapabilityError("runtime authorization does not match the resolved plan")


@dataclass(frozen=True)
class LaunchResult:
    return_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    disconnected: bool = False
    cleanup_verified: bool = True
    handle_id: str = "fake-handle"
    stop_requested: bool = False
    running_before_stop: bool | None = None
    scheduled_start_utc: str | None = None
    actual_start_utc: str | None = None
    schedule_error_ms: float | None = None
    launch_error: str | None = None
    repository_integrity: tuple[dict[str, object], ...] = ()


class ExternalLauncher(Protocol):
    def launch(
        self, arguments: tuple[str, ...], timeout_s: float, cancellation: threading.Event | None
    ) -> LaunchResult: ...


class OwnedProcess(Protocol):
    handle_id: str

    def wait(self, timeout_s: float, cancellation: threading.Event | None) -> LaunchResult: ...

    def stop(self) -> LaunchResult: ...


class OwnedProcessLauncher(Protocol):
    def begin(self, arguments: tuple[str, ...]) -> OwnedProcess: ...


@dataclass
class SealedFakeOwnedProcess:
    result: LaunchResult = LaunchResult(0)
    handle_id: str = "fake-owned-process"
    stopped: bool = False

    def wait(self, timeout_s: float, cancellation: threading.Event | None) -> LaunchResult:
        if cancellation is not None and cancellation.is_set():
            return LaunchResult(None, cancelled=True, handle_id=self.handle_id)
        return LaunchResult(
            self.result.return_code,
            self.result.stdout,
            self.result.stderr,
            self.result.timed_out,
            self.result.cancelled,
            self.result.disconnected,
            self.result.cleanup_verified,
            self.handle_id,
        )

    def stop(self) -> LaunchResult:
        self.stopped = True
        return LaunchResult(0, cleanup_verified=True, handle_id=self.handle_id)


@dataclass(frozen=True)
class SealedFakeOwnedLauncher:
    process: SealedFakeOwnedProcess

    def begin(self, arguments: tuple[str, ...]) -> OwnedProcess:
        return self.process


@dataclass(frozen=True)
class SealedFakeLauncher:
    result: LaunchResult = LaunchResult(0)

    def launch(
        self, arguments: tuple[str, ...], timeout_s: float, cancellation: threading.Event | None
    ) -> LaunchResult:
        if cancellation is not None and cancellation.is_set():
            return LaunchResult(None, cancelled=True)
        return self.result


class LocalTransportLauncher:
    """Production launcher backed by the reviewed bounded local transport."""

    def launch(
        self, arguments: tuple[str, ...], timeout_s: float, cancellation: threading.Event | None
    ) -> LaunchResult:
        if not arguments:
            raise CapabilityError("external launch arguments are empty")
        record = LocalCommandTransport().execute(
            CommandPlan(Path(arguments[0]), arguments[1:], timeout_s=timeout_s),
            cancellation=cancellation,
        )
        return LaunchResult(
            record.return_code,
            record.stdout,
            record.stderr,
            record.timed_out,
            record.cancelled,
            record.disconnected,
            record.cleanup_verified,
            record.child_identity,
        )


class _LocalOwnedProcess:
    def __init__(self, arguments: tuple[str, ...]) -> None:
        from wsprrypi_qualification.transports import LocalProcessOperation

        self._operation = LocalProcessOperation(
            CommandPlan(Path(arguments[0]), arguments[1:]), "wsprrypi", "transmit"
        )
        self.handle_id = self._operation.handle_id

    def wait(self, timeout_s: float, cancellation: threading.Event | None) -> LaunchResult:
        import time

        deadline = time.monotonic() + timeout_s
        while (result := self._operation.poll()) is None:
            cancelled = cancellation is not None and cancellation.is_set()
            if cancelled or time.monotonic() >= deadline:
                outcome = "cancelled" if cancelled else "timed_out"
                self._operation.request_stop()
                finalized = self._operation.finalize_after_stop(
                    OperationOutcome.CANCELLED if cancelled else OperationOutcome.TIMED_OUT,
                    outcome,
                )
                return LaunchResult(
                    finalized.return_code,
                    finalized.stdout,
                    finalized.stderr,
                    timed_out=not cancelled,
                    cancelled=cancelled,
                    cleanup_verified=not self._operation.is_alive(),
                    handle_id=self.handle_id,
                )
            time.sleep(min(0.01, timeout_s))
        return LaunchResult(
            result.return_code,
            result.stdout,
            result.stderr,
            cleanup_verified=not self._operation.is_alive(),
            handle_id=self.handle_id,
        )

    def stop(self) -> LaunchResult:
        self._operation.request_stop()
        result = self._operation.finalize_after_stop(OperationOutcome.CANCELLED, "cleanup")
        return LaunchResult(
            result.return_code,
            result.stdout,
            result.stderr,
            cleanup_verified=not self._operation.is_alive(),
            handle_id=self.handle_id,
        )


class LocalOwnedProcessLauncher:
    def begin(self, arguments: tuple[str, ...]) -> OwnedProcess:
        return _LocalOwnedProcess(arguments)


class _SshOwnedProcess:
    def __init__(
        self,
        client: JsonHelperClient,
        handle_id: str,
        arming_acknowledgement: dict[str, object],
        cleanup_timeout_s: float,
    ) -> None:
        self.client, self.handle_id = client, handle_id
        self.arming_acknowledgement = arming_acknowledgement
        self.cleanup_timeout_s = cleanup_timeout_s

    def wait(self, timeout_s: float, cancellation: threading.Event | None) -> LaunchResult:
        if cancellation is not None and cancellation.is_set():
            return self.stop()
        response = self.client.request(
            "process-wait",
            {"handle_id": self.handle_id, "timeout_s": timeout_s},
            response_timeout_s=timeout_s + self.cleanup_timeout_s,
        )
        return _launch_result_from_helper(response, self.handle_id)

    def arm(self, schedule_after_arm_s: float, minimum_arm_margin_s: float) -> None:
        response = self.client.request(
            "process-arm",
            {
                "handle_id": self.handle_id,
                "schedule_after_arm_s": schedule_after_arm_s,
                "minimum_arm_margin_s": minimum_arm_margin_s,
            },
            response_timeout_s=self.cleanup_timeout_s,
        )
        if response.get("handle_id") != self.handle_id:
            raise CapabilityError("remote process arm response has wrong ownership handle")
        self.arming_acknowledgement = response

    def stop(self) -> LaunchResult:
        response = self.client.request(
            "process-stop",
            {"handle_id": self.handle_id},
            response_timeout_s=self.cleanup_timeout_s,
        )
        return _launch_result_from_helper(response, self.handle_id)


class SshOwnedProcessLauncher:
    """Remote start/wait/stop protocol; the remote helper owns the child deadline."""

    def __init__(
        self,
        client: JsonHelperClient,
        hard_timeout_s: float,
        executable_sha256: str,
        pinned_arguments: dict[str, str] | None = None,
        repository_guard: dict[str, object] | None = None,
        privilege_wrapper_path: str | None = None,
        privilege_wrapper_sha256: str | None = None,
        cleanup_timeout_s: float | None = None,
    ) -> None:
        if hard_timeout_s <= 0:
            raise CapabilityError("remote process hard deadline must be positive")
        self.client, self.hard_timeout_s = client, hard_timeout_s
        self.cleanup_timeout_s = (
            client.timeout_s if cleanup_timeout_s is None else cleanup_timeout_s
        )
        if (
            isinstance(self.cleanup_timeout_s, bool)
            or not math.isfinite(self.cleanup_timeout_s)
            or self.cleanup_timeout_s <= 0
        ):
            raise CapabilityError("remote process cleanup deadline must be positive")
        self.executable_sha256 = executable_sha256
        self.pinned_arguments = dict(pinned_arguments or {})
        if self.pinned_arguments and repository_guard is None:
            raise CapabilityError(
                "mutable pinned process inputs require repository protection metadata"
            )
        self.repository_guard = repository_guard
        if (privilege_wrapper_path is None) is not (privilege_wrapper_sha256 is None):
            raise CapabilityError("remote process privilege wrapper binding is incomplete")
        self.privilege_wrapper_path = privilege_wrapper_path
        self.privilege_wrapper_sha256 = privilege_wrapper_sha256

    def begin(self, arguments: tuple[str, ...]) -> OwnedProcess:
        return self.begin_scheduled(arguments)

    def prepare(self, arguments: tuple[str, ...]) -> _SshOwnedProcess:
        payload = self._start_payload(arguments)
        inspection_timeout_s = self._inspection_timeout_s()
        response = self.client.request(
            "process-prepare",
            payload,
            response_timeout_s=self.client.timeout_s + inspection_timeout_s,
        )
        handle = response.get("handle_id")
        if not isinstance(handle, str) or not handle:
            raise CapabilityError("remote process helper omitted ownership handle")
        return _SshOwnedProcess(self.client, handle, response, self.cleanup_timeout_s)

    def _start_payload(self, arguments: tuple[str, ...]) -> dict[str, object]:
        payload: dict[str, object] = {
            "arguments": list(arguments),
            "executable_sha256": self.executable_sha256,
            "privilege_wrapper_path": self.privilege_wrapper_path,
            "privilege_wrapper_sha256": self.privilege_wrapper_sha256,
            "pinned_arguments": self.pinned_arguments,
            "hard_timeout_s": self.hard_timeout_s,
            "cleanup_timeout_s": self.cleanup_timeout_s,
            "environment": {},
        }
        if self.repository_guard is not None:
            payload["repository_guard"] = self.repository_guard
        return payload

    def _inspection_timeout_s(self) -> float:
        inspection_timeout_s = 0.0
        if self.repository_guard is not None:
            raw_timeout = self.repository_guard.get("inspection_timeout_s")
            if (
                not isinstance(raw_timeout, (int, float))
                or isinstance(raw_timeout, bool)
                or not math.isfinite(float(raw_timeout))
                or float(raw_timeout) <= 0
            ):
                raise CapabilityError("repository inspection deadline is invalid")
            inspection_timeout_s = float(raw_timeout)
        return inspection_timeout_s

    def begin_scheduled(
        self,
        arguments: tuple[str, ...],
        *,
        scheduled_start_utc: str | None = None,
        schedule_after_arm_s: float | None = None,
        minimum_arm_margin_s: float = 0.0,
    ) -> OwnedProcess:
        payload = self._start_payload(arguments)
        if scheduled_start_utc is not None and schedule_after_arm_s is not None:
            raise CapabilityError("remote process schedule must use exactly one time basis")
        if scheduled_start_utc is not None:
            payload["scheduled_start_utc"] = scheduled_start_utc
            payload["minimum_arm_margin_s"] = minimum_arm_margin_s
        elif schedule_after_arm_s is not None:
            payload["schedule_after_arm_s"] = schedule_after_arm_s
            payload["minimum_arm_margin_s"] = minimum_arm_margin_s
        inspection_timeout_s = self._inspection_timeout_s()
        response = self.client.request(
            "process-start",
            payload,
            response_timeout_s=self.client.timeout_s + inspection_timeout_s,
        )
        handle = response.get("handle_id")
        if not isinstance(handle, str) or not handle:
            raise CapabilityError("remote process helper omitted ownership handle")
        return _SshOwnedProcess(self.client, handle, response, self.cleanup_timeout_s)


def _launch_result_from_helper(document: dict[str, object], handle: str) -> LaunchResult:
    if document.get("handle_id") != handle:
        raise CapabilityError("remote process response has wrong ownership handle")
    return_code = document.get("return_code")
    if return_code is not None and (
        not isinstance(return_code, int) or isinstance(return_code, bool)
    ):
        raise CapabilityError("remote process return code is invalid")
    return LaunchResult(
        return_code=return_code,
        stdout=str(document.get("stdout", "")),
        stderr=str(document.get("stderr", "")),
        timed_out=document.get("timed_out") is True,
        cancelled=document.get("cancelled") is True,
        disconnected=document.get("disconnected") is True,
        cleanup_verified=document.get("cleanup_verified") is True,
        handle_id=handle,
        stop_requested=document.get("stop_requested") is True,
        running_before_stop=(
            cast(bool, document.get("running_before_stop"))
            if isinstance(document.get("running_before_stop"), bool)
            else None
        ),
        scheduled_start_utc=(
            cast(str, document.get("scheduled_start_utc"))
            if isinstance(document.get("scheduled_start_utc"), str)
            else None
        ),
        actual_start_utc=(
            cast(str, document.get("actual_start_utc"))
            if isinstance(document.get("actual_start_utc"), str)
            else None
        ),
        schedule_error_ms=(
            float(cast(float, document["schedule_error_ms"]))
            if isinstance(document.get("schedule_error_ms"), (int, float))
            and not isinstance(document.get("schedule_error_ms"), bool)
            else None
        ),
        launch_error=(
            cast(str, document.get("launch_error"))
            if isinstance(document.get("launch_error"), str)
            else None
        ),
        repository_integrity=tuple(
            cast(list[dict[str, object]], document.get("repository_integrity", []))
        ),
    )


@dataclass(frozen=True)
class SshCapabilityPlan:
    executable: Path
    host: str
    remote_helper: str
    remote_arguments: tuple[str, ...]
    connect_timeout_s: float
    command_timeout_s: float
    overall_timeout_s: float

    def document(self) -> dict[str, object]:
        return {
            **asdict(self),
            "executable": str(self.executable),
            "remote_arguments": list(self.remote_arguments),
        }


class OpenSshCapability:
    def __init__(self, launcher: ExternalLauncher) -> None:
        self._launcher = launcher

    @staticmethod
    def encode_remote_arguments(arguments: tuple[str, ...]) -> str:
        payload = json.dumps(list(arguments), ensure_ascii=True, separators=(",", ":"))
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")

    def execute(
        self,
        plan: SshCapabilityPlan,
        authorization: RuntimeAuthorization | None,
        cancellation: threading.Event | None = None,
    ) -> dict[str, object]:
        if not plan.host.strip() or plan.host.startswith("-") or not plan.remote_arguments:
            raise CapabilityError("SSH destination and remote arguments are required")
        if re.fullmatch(r"/[A-Za-z0-9._/+:-]+", plan.remote_helper) is None:
            raise CapabilityError("SSH remote helper path contains unsafe shell characters")
        if min(plan.connect_timeout_s, plan.command_timeout_s, plan.overall_timeout_s) <= 0:
            raise CapabilityError("SSH deadlines must be positive")
        cleanup_timeout_s = plan.overall_timeout_s - plan.command_timeout_s
        if cleanup_timeout_s <= 0:
            raise CapabilityError("SSH overall deadline must contain command and cleanup")
        if not plan.executable.is_absolute() or not plan.executable.is_file():
            raise CapabilityError("SSH executable must be an existing absolute file")
        _authorize(plan.document(), authorization)
        started = _utc()
        encoded = self.encode_remote_arguments(plan.remote_arguments)
        remote_command = (
            f"{plan.remote_helper} --timeout {plan.command_timeout_s:g} "
            f"--cleanup-timeout {cleanup_timeout_s:g} --argv-base64 {encoded}"
        )
        arguments = (
            str(plan.executable),
            "-o",
            f"ConnectTimeout={plan.connect_timeout_s:g}",
            "--",
            plan.host,
            remote_command,
        )
        result = self._launcher.launch(arguments, plan.overall_timeout_s, cancellation)
        outcome = _execution_outcome(result)
        document = {
            "schema_version": 1,
            "evidence_type": "ssh_capability_execution",
            "started_utc": started,
            "completed_utc": _utc(),
            "host": plan.host,
            "executable": artifact(plan.executable),
            "arguments": list(arguments),
            "intended_remote_arguments": list(plan.remote_arguments),
            "remote_helper": plan.remote_helper,
            "encoding_contract": "base64url-json-array-utf8",
            "encoded_remote_command": encoded,
            "remote_command": remote_command,
            "deadlines": {
                "connect_s": plan.connect_timeout_s,
                "command_s": plan.command_timeout_s,
                "overall_s": plan.overall_timeout_s,
            },
            "return_code": result.return_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "cancelled": result.cancelled,
            "disconnected": result.disconnected,
            "cleanup_verified": result.cleanup_verified,
            "handle_id": result.handle_id,
            "outcome": outcome,
        }
        validate_document(document, "ssh-capability.schema.json")
        validate_capability_semantics(document)
        return document


@dataclass(frozen=True)
class CaptureCapabilityPlan:
    helper: Path
    metadata_path: Path
    output_path: Path
    driver: str
    serial: str
    channel: int
    sample_rate_hz: int
    bandwidth_hz: int
    center_frequency_hz: float
    gain_db: float
    sample_count: int
    read_timeout_us: int
    maximum_elapsed_s: float
    clipping_threshold: float
    agc: bool = False
    bias_tee: bool = False
    sample_format: str = "CF32"

    def document(self) -> dict[str, object]:
        value = asdict(self)
        for name in ("helper", "metadata_path", "output_path"):
            value[name] = str(value[name])
        return value


class SoapyCaptureCapability:
    def __init__(self, launcher: ExternalLauncher) -> None:
        self._launcher = launcher

    def execute(
        self,
        plan: CaptureCapabilityPlan,
        authorization: RuntimeAuthorization | None,
        cancellation: threading.Event | None = None,
    ) -> dict[str, object]:
        if plan.sample_format != "CF32" or plan.sample_count <= 0:
            raise CapabilityError("capture requires a positive exact CF32 sample count")
        if plan.agc or plan.bias_tee or not 0 < plan.clipping_threshold <= 1:
            raise CapabilityError("capture AGC/bias tee/clipping contract is unsafe")
        if plan.output_path.exists() or plan.metadata_path.exists():
            raise CapabilityError("capture outputs must be new")
        if not plan.helper.is_absolute() or not plan.helper.is_file():
            raise CapabilityError("capture helper must be an existing absolute file")
        _authorize(plan.document(), authorization)
        arguments = (
            str(plan.helper),
            "--enable-physical-sdr",
            plan.driver,
            plan.serial,
            f"{plan.center_frequency_hz:g}",
            str(plan.sample_count),
            f"{plan.gain_db:g}",
            str(plan.sample_rate_hz),
            str(plan.bandwidth_hz),
            str(plan.channel),
            str(plan.agc).lower(),
            str(plan.bias_tee).lower(),
            str(plan.read_timeout_us),
            f"{plan.maximum_elapsed_s:g}",
            str(plan.output_path),
            str(plan.metadata_path),
            plan.output_path.stem,
        )
        result = self._launcher.launch(arguments, plan.maximum_elapsed_s, cancellation)
        if result.return_code != 0 or result.timed_out or result.cancelled:
            diagnostic = _retain_capture_failure(plan, arguments, result, "helper_execution_failed")
            raise CaptureCapabilityError(
                f"capture helper failed: {_execution_outcome(result)}", diagnostic
            )
        try:
            metadata = load_capture_metadata(plan.metadata_path)
        except Exception as exc:
            diagnostic = _retain_capture_failure(
                plan, arguments, result, "capture_metadata_unavailable_or_invalid"
            )
            raise CaptureCapabilityError(
                "capture metadata is unavailable or invalid", diagnostic
            ) from exc
        if (
            metadata.retained_sample_count != plan.sample_count
            or metadata.requested_sample_count != plan.sample_count
            or metadata.overflow_count != 0
            or metadata.timeout_count != 0
            or metadata.clipped_samples != 0
            or metadata.cleanup_outcome != "verified"
        ):
            diagnostic = _retain_capture_failure(
                plan, arguments, result, "exact_count_contract_violated"
            )
            raise CaptureCapabilityError(
                "capture metadata violates the exact-count contract", diagnostic
            )
        expected_device = {"driver": plan.driver, "serial": plan.serial}
        expected_settings = {
            "format": plan.sample_format,
            "sample_rate_hz": plan.sample_rate_hz,
            "bandwidth_hz": plan.bandwidth_hz,
            "center_frequency_hz": plan.center_frequency_hz,
            "gain_db": plan.gain_db,
            "channel": plan.channel,
            "agc": plan.agc,
            "bias_tee": plan.bias_tee,
        }
        if (
            metadata.requested_device != expected_device
            or metadata.resolved_device != expected_device
            or metadata.requested_settings != expected_settings
            or metadata.actual_settings != expected_settings
            or not math.isclose(
                metadata.clipping_threshold,
                plan.clipping_threshold,
                rel_tol=0.0,
                abs_tol=5e-8,
            )
        ):
            diagnostic = _retain_capture_failure(
                plan, arguments, result, "identity_or_settings_contradict_plan"
            )
            raise CaptureCapabilityError(
                "capture identity or settings contradict the resolved plan", diagnostic
            )
        try:
            output = artifact(plan.output_path)
        except (OSError, ValueError) as exc:
            diagnostic = _retain_capture_failure(
                plan, arguments, result, "capture_output_unavailable_or_invalid"
            )
            raise CaptureCapabilityError(
                "capture output is unavailable or invalid", diagnostic
            ) from exc
        if output["size_bytes"] != plan.sample_count * 8:
            diagnostic = _retain_capture_failure(
                plan, arguments, result, "capture_output_size_not_exact"
            )
            raise CaptureCapabilityError("capture output byte size is not exact CF32", diagnostic)
        if (
            Path(metadata.output.path).resolve() != plan.output_path.resolve()
            or metadata.output.sha256 != output["sha256"]
            or not metadata.output.present
            or not metadata.output.complete
        ):
            diagnostic = _retain_capture_failure(
                plan, arguments, result, "capture_metadata_does_not_authenticate_iq"
            )
            raise CaptureCapabilityError(
                "capture metadata does not authenticate the retained IQ", diagnostic
            )
        if result.disconnected or not result.cleanup_verified:
            diagnostic = _retain_capture_failure(
                plan, arguments, result, "capture_transport_or_cleanup_failed"
            )
            raise CaptureCapabilityError(
                "capture transport cleanup or connection failed", diagnostic
            )
        document = {
            "schema_version": 1,
            "evidence_type": "soapy_capture_capability",
            "plan": plan.document(),
            "arguments": list(arguments),
            "helper": artifact(plan.helper),
            "return_code": result.return_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "cleanup_verified": result.cleanup_verified,
            "capture_metadata": artifact(plan.metadata_path),
            "output": output,
            "outcome": "completed",
        }
        try:
            validate_document(document, "soapy-capability.schema.json")
            validate_capability_semantics(document)
        except Exception as exc:
            diagnostic = _retain_capture_failure(
                plan, arguments, result, "capture_capability_evidence_rejected"
            )
            raise CaptureCapabilityError(
                "capture capability evidence is invalid", diagnostic
            ) from exc
        return document


class WsprryPiProcessCapability:
    def __init__(self, launcher: OwnedProcessLauncher) -> None:
        self._launcher = launcher
        self._owned: set[str] = set()

    def execute(
        self,
        plan: ApplicationPlan,
        resolved_plan: ResolvedCapabilityPlan,
        authorization: RuntimeAuthorization | None,
        timeout_s: float,
        cancellation: threading.Event | None = None,
    ) -> dict[str, object]:
        document = plan.to_document()
        validate_application_plan(document)
        if plan.execution_authorized:
            raise CapabilityError("committed application authorization is forbidden")
        resolved_document = resolved_plan.document()
        binding = f"wsprrypi_process_capability:{capability_plan_sha256(document)}"
        if binding not in resolved_plan.capability_bindings:
            raise CapabilityError("WsprryPi application is absent from the resolved session plan")
        _authorize_rf(resolved_document, authorization)
        if timeout_s <= 0:
            raise CapabilityError("WsprryPi hard deadline must be positive")
        process = self._launcher.begin(plan.arguments)
        if process.handle_id in self._owned:
            process.stop()
            raise CapabilityError("duplicate WsprryPi process ownership")
        self._owned.add(process.handle_id)
        try:
            result = process.wait(min(timeout_s, resolved_plan.overall_timeout_s), cancellation)
        except Exception:
            result = process.stop()
            if result.cleanup_verified:
                self._owned.discard(process.handle_id)
            raise
        if not result.cleanup_verified:
            stopped = process.stop()
            result = LaunchResult(
                result.return_code,
                result.stdout + stopped.stdout,
                result.stderr + stopped.stderr,
                result.timed_out,
                result.cancelled,
                result.disconnected,
                stopped.cleanup_verified,
                process.handle_id,
            )
        if result.cleanup_verified:
            self._owned.discard(process.handle_id)
        evidence = {
            "schema_version": 1,
            "evidence_type": "wsprrypi_process_capability",
            "application_plan": document,
            "arguments": list(plan.arguments),
            "handle_id": result.handle_id,
            "return_code": result.return_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "cancelled": result.cancelled,
            "disconnected": result.disconnected,
            "cleanup_verified": result.cleanup_verified,
            "outcome": _execution_outcome(result),
        }
        validate_document(evidence, "wsprrypi-process-capability.schema.json")
        validate_capability_semantics(evidence)
        return evidence


@dataclass(frozen=True)
class ServiceState:
    name: str
    manager: str
    running: bool


class ServiceProvider(Protocol):
    def inspect(self, name: str) -> ServiceState: ...
    def set_running(self, name: str, running: bool) -> None: ...


class JsonHelperClient:
    """Production boundary for a pinned local or remote capability helper."""

    def __init__(
        self,
        executable: Path,
        transport: HelperExchange,
        timeout_s: float,
        plan_sha256: str,
        helper_identity: str,
        executable_sha256: str | None = None,
    ) -> None:
        if not executable.is_absolute() or not executable.is_file() or timeout_s <= 0:
            raise CapabilityError("helper client requires a pinned executable and deadline")
        self.executable, self.transport, self.timeout_s = executable, transport, timeout_s
        self.plan_sha256, self.helper_identity = plan_sha256, helper_identity
        self.executable_sha256 = executable_sha256 or artifact(executable)["sha256"]

    def request(
        self,
        operation: str,
        payload: dict[str, object],
        *,
        response_timeout_s: float | None = None,
    ) -> dict[str, object]:
        return cast(
            dict[str, object],
            self.request_evidence(operation, payload, response_timeout_s=response_timeout_s)[
                "result"
            ],
        )

    def request_evidence(
        self,
        operation: str,
        payload: dict[str, object],
        *,
        response_timeout_s: float | None = None,
    ) -> dict[str, object]:
        if artifact(self.executable)["sha256"] != self.executable_sha256:
            raise CapabilityError("capability helper executable identity changed")
        request_id = str(uuid.uuid4())
        request = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "operation": operation,
            "plan_sha256": self.plan_sha256,
            "payload": payload,
        }
        encoded = encode_request(request)
        response_timeout = self.timeout_s if response_timeout_s is None else response_timeout_s
        if (
            isinstance(response_timeout, bool)
            or not math.isfinite(response_timeout)
            or response_timeout <= 0
        ):
            raise CapabilityError("helper response deadline must be positive")
        raw_response = self.transport.exchange(encoded, response_timeout)
        try:
            document = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise CapabilityError("capability helper returned invalid JSON") from exc
        if not isinstance(document, dict):
            raise CapabilityError("capability helper response must be an object")
        if document.get("outcome") == "rejected" and isinstance(document.get("error"), str):
            raise CapabilityError(f"capability helper rejected {operation}: {document['error']}")
        validate_document(document, "helper-response.schema.json")
        expected = {
            "protocol_version",
            "request_id",
            "operation",
            "plan_sha256",
            "helper_identity",
            "outcome",
            "result",
        }
        if (
            set(document) != expected
            or document.get("protocol_version") != PROTOCOL_VERSION
            or document.get("request_id") != request_id
            or document.get("operation") != operation
            or document.get("plan_sha256") != self.plan_sha256
            or document.get("helper_identity") != self.helper_identity
            or document.get("outcome") != "completed"
            or not isinstance(document.get("result"), dict)
        ):
            raise CapabilityError("capability helper response contradicts its request")
        result = cast(dict[str, object], document["result"])
        result_schema = {
            "process-start": "process-start-result.schema.json",
            "process-prepare": "process-start-result.schema.json",
            "process-arm": "process-start-result.schema.json",
            "process-wait": "process-wait-result.schema.json",
            "process-stop": "process-stop-result.schema.json",
            "service-inspect": "service-helper-result.schema.json",
            "service-set": "service-helper-result.schema.json",
            "gpio-inspect": "gpio-helper-result.schema.json",
            "si5351-inspect": "si5351-helper-result.schema.json",
            "bounded-tone": "bounded-tone-helper-result.schema.json",
        }[operation]
        validate_document(result, result_schema)
        return cast(dict[str, object], document)


class HelperExchange(Protocol):
    def exchange(self, encoded_request: str, timeout_s: float) -> str: ...


class PersistentHelperTransport:
    """One persistent local or SSH process carrying JSON-lines helper traffic."""

    def __init__(self, command: tuple[str, ...], cleanup_timeout_s: float = 5.0) -> None:
        if not command or not Path(command[0]).is_absolute() or not Path(command[0]).is_file():
            raise CapabilityError("persistent helper command requires a pinned executable")
        if cleanup_timeout_s <= 0:
            raise CapabilityError("helper cleanup deadline must be positive")
        self.cleanup_timeout_s = cleanup_timeout_s
        self._process = subprocess.Popen(
            command,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self._lock = threading.Lock()

    def exchange(self, encoded_request: str, timeout_s: float) -> str:
        if timeout_s <= 0:
            raise CapabilityError("persistent helper session is unavailable")
        return_code = self._process.poll()
        if return_code is not None:
            detail = ""
            if self._process.stderr is not None:
                detail = (
                    self._process.stderr.read(2048).strip().replace("\r", " ").replace("\n", " ")
                )
            suffix = f" (exit {return_code}{f': {detail}' if detail else ''})"
            raise CapabilityError(f"persistent helper session is unavailable{suffix}")
        deadline = time.monotonic() + timeout_s
        assert self._process.stdin is not None and self._process.stdout is not None
        stdout_stream = self._process.stdout
        with self._lock:
            self._process.stdin.write(encoded_request + "\n")
            self._process.stdin.flush()
            # The server owns every operation deadline. This local guard detects
            # a dead helper without inventing success or losing its owned state.
            completed = threading.Event()
            response: list[str] = []

            def read_line() -> None:
                response.append(stdout_stream.readline())
                completed.set()

            reader = threading.Thread(target=read_line, daemon=True)
            reader.start()
            if (
                not completed.wait(max(0.0, deadline - time.monotonic()))
                or not response
                or not response[0]
            ):
                self._close_input()
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    self._process.wait(timeout=min(self.cleanup_timeout_s, remaining))
                except subprocess.TimeoutExpired as exc:
                    raise CapabilityError(
                        "helper response timed out; cleanup remains owned but is not yet verified"
                    ) from exc
                raise CapabilityError("persistent helper response deadline expired after cleanup")
            return response[0]

    def close(self) -> None:
        self._close_input()
        try:
            self._process.wait(timeout=self.cleanup_timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise CapabilityError("helper cleanup could not be verified before deadline") from exc
        if self._process.stdout is not None:
            self._process.stdout.close()
        if self._process.stderr is not None:
            self._process.stderr.close()

    def _close_input(self) -> None:
        if self._process.stdin is not None and not self._process.stdin.closed:
            self._process.stdin.close()


class HelperServiceProvider:
    def __init__(
        self,
        client: JsonHelperClient,
        manager: str,
        repository_guard: dict[str, object] | None = None,
        response_timeout_s: float | None = None,
    ) -> None:
        self.client, self.manager = client, manager
        self.repository_guard = repository_guard
        self.response_timeout_s = response_timeout_s

    def set_repository_guard(self, guard: dict[str, object]) -> None:
        self.repository_guard = guard

    def set_response_timeout(self, timeout_s: float) -> None:
        if isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise CapabilityError("service response deadline must be positive and finite")
        self.response_timeout_s = timeout_s

    def inspect(self, name: str) -> ServiceState:
        response = self.client.request(
            "service-inspect",
            {"name": name, "manager": self.manager},
            response_timeout_s=self.response_timeout_s,
        )
        if response.get("name") != name or not isinstance(response.get("running"), bool):
            raise CapabilityError("service helper response contradicts request")
        return ServiceState(name, self.manager, response["running"] is True)

    def set_running(self, name: str, running: bool) -> None:
        payload: dict[str, object] = {
            "name": name,
            "manager": self.manager,
            "running": running,
        }
        if self.repository_guard is not None:
            payload["repository_guard"] = self.repository_guard
        response = self.client.request(
            "service-set", payload, response_timeout_s=self.response_timeout_s
        )
        if response.get("cleanup_verified") is False:
            raise CapabilityError("service action changed protected repository state")
        if response.get("name") != name or response.get("running") is not running:
            raise CapabilityError("service helper did not reach requested state")


@dataclass
class SealedFakeServiceProvider:
    states: dict[str, bool]
    manager: str = "fake"

    def inspect(self, name: str) -> ServiceState:
        if name not in self.states:
            raise CapabilityError("unnamed service refused")
        return ServiceState(name, self.manager, self.states[name])

    def set_running(self, name: str, running: bool) -> None:
        if name not in self.states:
            raise CapabilityError("unnamed service refused")
        self.states[name] = running


class NarrowServiceCapability:
    def __init__(self, provider: ServiceProvider, allowed_names: frozenset[str]) -> None:
        self._provider, self._allowed = provider, allowed_names

    def apply_and_restore(
        self, name: str, requested_running: bool, authorization: RuntimeAuthorization | None
    ) -> dict[str, object]:
        plan = {"name": name, "requested_running": requested_running}
        _authorize(plan, authorization)
        if name not in self._allowed:
            raise CapabilityError("service is not explicitly named in the resolved plan")
        initial = self._provider.inspect(name)
        changed = initial.running != requested_running
        actual = initial
        operation_error: str | None = None
        restoration_error: str | None = None
        try:
            if changed:
                self._provider.set_running(name, requested_running)
            actual = self._provider.inspect(name)
        except Exception as exc:
            operation_error = f"{type(exc).__name__}: {exc}"
        finally:
            if changed:
                try:
                    self._provider.set_running(name, initial.running)
                except Exception as exc:  # provider failure is retained as cleanup evidence
                    restoration_error = f"{type(exc).__name__}: {exc}"
        try:
            restored = self._provider.inspect(name)
        except Exception as exc:
            restoration_error = restoration_error or f"{type(exc).__name__}: {exc}"
            restored = actual
        verified = restoration_error is None and restored.running == initial.running
        failure = restoration_error or operation_error
        document = {
            "schema_version": 1,
            "evidence_type": "service_capability",
            "name": name,
            "manager": initial.manager,
            "initial_running": initial.running,
            "requested_running": requested_running,
            "actual_running": actual.running,
            "changed_by_harness": changed,
            "restored_running": restored.running,
            "restoration_verified": verified,
            "failure_cause": failure,
            "outcome": (
                "cleanup_failed"
                if not verified
                else "operation_failed"
                if operation_error is not None
                else "completed"
            ),
        }
        validate_document(document, "service-capability.schema.json")
        validate_capability_semantics(document)
        return document


@dataclass(frozen=True)
class GpioObservation:
    pin: int
    direction: str
    owner: str | None = None


@dataclass(frozen=True)
class Si5351Observation:
    bus: int
    address: str
    enabled_outputs: tuple[str, ...]
    owner: str | None = None


class GpioProvider(Protocol):
    def inspect(self, pin: int) -> GpioObservation: ...


class Si5351Provider(Protocol):
    def inspect(self, bus: int, address: str) -> Si5351Observation: ...


class HelperGpioProvider:
    def __init__(self, client: JsonHelperClient) -> None:
        self.client = client

    def inspect(self, pin: int) -> GpioObservation:
        response = self.client.request("gpio-inspect", {"pin": pin})
        if response.get("pin") != pin or not isinstance(response.get("direction"), str):
            raise CapabilityError("GPIO helper response contradicts request")
        owner = response.get("owner")
        if owner is not None and not isinstance(owner, str):
            raise CapabilityError("GPIO helper owner is invalid")
        return GpioObservation(pin, cast(str, response["direction"]), owner)


class HelperSi5351Provider:
    def __init__(self, client: JsonHelperClient) -> None:
        self.client = client

    def inspect(self, bus: int, address: str) -> Si5351Observation:
        response = self.client.request("si5351-inspect", {"bus": bus, "address": address})
        outputs = response.get("enabled_outputs")
        if (
            response.get("bus") != bus
            or response.get("address") != address
            or not isinstance(outputs, list)
            or not all(isinstance(item, str) for item in outputs)
        ):
            raise CapabilityError("Si5351 helper response contradicts request")
        owner = response.get("owner")
        if owner is not None and not isinstance(owner, str):
            raise CapabilityError("Si5351 helper owner is invalid")
        return Si5351Observation(bus, address, tuple(outputs), owner)


@dataclass(frozen=True)
class SealedFakeGpioProvider:
    observation: GpioObservation

    def inspect(self, pin: int) -> GpioObservation:
        return self.observation


@dataclass(frozen=True)
class SealedFakeSi5351Provider:
    observation: Si5351Observation

    def inspect(self, bus: int, address: str) -> Si5351Observation:
        return self.observation


class GpioQuiescenceCapability:
    def __init__(self, provider: GpioProvider) -> None:
        self._provider = provider

    def inspect(
        self,
        pin: int,
        authorization: RuntimeAuthorization | None,
        expected_direction: str = "input",
    ) -> dict[str, object]:
        plan = {"pin": pin, "expected_direction": expected_direction, "read_only": True}
        _authorize(plan, authorization)
        observed = self._provider.inspect(pin)
        verified = (
            observed.pin == pin
            and observed.direction == expected_direction
            and observed.owner is None
        )
        document = {
            "schema_version": 1,
            "evidence_type": "gpio_quiescence_capability",
            "pin": pin,
            "observed_pin": observed.pin,
            "expected_direction": expected_direction,
            "observed_direction": observed.direction,
            "owner": observed.owner,
            "verified": verified,
            "outcome": "verified" if verified else "failed",
        }
        validate_document(document, "gpio-quiescence-capability.schema.json")
        validate_capability_semantics(document)
        return document


class Si5351QuiescenceCapability:
    def __init__(self, provider: Si5351Provider) -> None:
        self._provider = provider

    def inspect(
        self,
        bus: int,
        address: str,
        required_outputs: tuple[str, ...],
        authorization: RuntimeAuthorization | None,
    ) -> dict[str, object]:
        plan = {
            "bus": bus,
            "address": address,
            "required_outputs": list(required_outputs),
            "read_only": True,
        }
        _authorize(plan, authorization)
        observed = self._provider.inspect(bus, address)
        enabled = set(observed.enabled_outputs) & set(required_outputs)
        verified = (
            observed.bus == bus
            and observed.address == address
            and not enabled
            and observed.owner is None
        )
        document = {
            "schema_version": 1,
            "evidence_type": "si5351_quiescence_capability",
            "bus": bus,
            "address": address,
            "observed_bus": observed.bus,
            "observed_address": observed.address,
            "required_outputs": list(required_outputs),
            "enabled_outputs": list(observed.enabled_outputs),
            "owner": observed.owner,
            "verified": verified,
            "outcome": "verified" if verified else "failed",
        }
        validate_document(document, "si5351-quiescence-capability.schema.json")
        validate_capability_semantics(document)
        return document


def _execution_outcome(result: LaunchResult) -> str:
    if result.cancelled:
        return "cancelled"
    if result.timed_out:
        return "timed_out"
    if result.disconnected:
        return "disconnected"
    if not result.cleanup_verified:
        return "cleanup_failed"
    return "completed" if result.return_code == 0 else "nonzero_exit"


def _evidence_plan_sha256(document: dict[str, object]) -> str:
    evidence_type = document.get("evidence_type")
    plan: object
    if evidence_type == "ssh_capability_execution":
        executable = document.get("executable")
        deadlines = document.get("deadlines")
        if not isinstance(executable, dict) or not isinstance(deadlines, dict):
            raise CapabilityError("SSH evidence lacks its resolved subplan")
        plan = {
            "executable": executable.get("path"),
            "host": document.get("host"),
            "remote_helper": document.get("remote_helper"),
            "remote_arguments": document.get("intended_remote_arguments"),
            "connect_timeout_s": deadlines.get("connect_s"),
            "command_timeout_s": deadlines.get("command_s"),
            "overall_timeout_s": deadlines.get("overall_s"),
        }
    elif evidence_type == "soapy_capture_capability":
        plan = document.get("plan")
    elif evidence_type == "wsprrypi_process_capability":
        plan = document.get("application_plan")
    elif evidence_type == "service_capability":
        plan = {
            "name": document.get("name"),
            "requested_running": document.get("requested_running"),
        }
    elif evidence_type == "gpio_quiescence_capability":
        plan = {
            "pin": document.get("pin"),
            "expected_direction": document.get("expected_direction"),
            "read_only": True,
        }
    elif evidence_type == "si5351_quiescence_capability":
        plan = {
            "bus": document.get("bus"),
            "address": document.get("address"),
            "required_outputs": document.get("required_outputs"),
            "read_only": True,
        }
    else:
        raise CapabilityError("unknown capability evidence type")
    if not isinstance(plan, dict):
        raise CapabilityError("capability evidence lacks its resolved subplan")
    return capability_plan_sha256(plan)


def _remove_untrusted_capture_output(plan: CaptureCapabilityPlan) -> None:
    for path in (
        plan.output_path,
        Path(f"{plan.output_path}.incomplete"),
        Path(f"{plan.metadata_path}.incomplete"),
        Path(f"{plan.metadata_path}.failure.json.incomplete"),
    ):
        path.unlink(missing_ok=True)


def _retain_capture_failure(
    plan: CaptureCapabilityPlan,
    arguments: tuple[str, ...],
    result: LaunchResult,
    reason: str,
) -> Path | None:
    """Remove untrusted IQ while preserving hash-bound helper diagnostics."""
    _remove_untrusted_capture_output(plan)
    native_failure = Path(f"{plan.metadata_path}.failure.json")
    rejected_metadata = plan.metadata_path
    document: dict[str, object] = {
        "schema_version": 1,
        "evidence_type": "soapy_capture_failure_diagnostic",
        "capability_plan_sha256": capability_plan_sha256(plan.document()),
        "arguments": list(arguments),
        "helper": artifact(plan.helper),
        "reason": reason,
        "execution": {
            "return_code": result.return_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "cancelled": result.cancelled,
            "disconnected": result.disconnected,
            "cleanup_verified": result.cleanup_verified,
        },
        "native_failure_metadata": artifact(native_failure)
        if native_failure.is_file() and not native_failure.is_symlink()
        else None,
        "rejected_capture_metadata": artifact(rejected_metadata)
        if rejected_metadata.is_file() and not rejected_metadata.is_symlink()
        else None,
        "iq_retained": False,
    }
    path = Path(f"{plan.metadata_path}.execution.json")
    try:
        if path.exists():
            return None
        write_json_new(path, document)
        return path
    except (OSError, ValueError):
        return None


def validate_capability_semantics(document: dict[str, object]) -> None:
    """Reject structurally valid but contradictory capability evidence."""
    evidence_type = document.get("evidence_type")
    schema_by_type = {
        "ssh_capability_execution": "ssh-capability.schema.json",
        "soapy_capture_capability": "soapy-capability.schema.json",
        "wsprrypi_process_capability": "wsprrypi-process-capability.schema.json",
        "service_capability": "service-capability.schema.json",
        "gpio_quiescence_capability": "gpio-quiescence-capability.schema.json",
        "si5351_quiescence_capability": "si5351-quiescence-capability.schema.json",
    }
    if not isinstance(evidence_type, str) or evidence_type not in schema_by_type:
        raise CapabilityError("unknown capability evidence type")
    validate_document(document, schema_by_type[evidence_type])
    if evidence_type in {"ssh_capability_execution", "wsprrypi_process_capability"}:
        result = LaunchResult(
            document["return_code"] if isinstance(document["return_code"], int) else None,
            timed_out=document["timed_out"] is True,
            cancelled=document["cancelled"] is True,
            disconnected=document["disconnected"] is True,
            cleanup_verified=document["cleanup_verified"] is True,
        )
        if document["outcome"] != _execution_outcome(result):
            raise CapabilityError("capability outcome contradicts execution fields")
        if sum((result.timed_out, result.cancelled, result.disconnected)) > 1:
            raise CapabilityError("execution terminal causes are mutually exclusive")
        if document["outcome"] == "completed" and result.return_code != 0:
            raise CapabilityError("completed execution requires return code zero")
        if document["outcome"] == "cleanup_failed" and result.cleanup_verified:
            raise CapabilityError("cleanup failure requires failed cleanup evidence")
        if evidence_type == "ssh_capability_execution":
            encoded = document["encoded_remote_command"]
            try:
                if not isinstance(encoded, str):
                    raise ValueError("encoded payload is not text")
                padded = encoded + "=" * (-len(encoded) % 4)
                decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CapabilityError("SSH remote command encoding is invalid") from exc
            if decoded != document["intended_remote_arguments"]:
                raise CapabilityError("SSH encoded command contradicts intended arguments")
            arguments = document["arguments"]
            executable = document["executable"]
            if not isinstance(arguments, list) or not isinstance(executable, dict):
                raise CapabilityError("SSH execution evidence is malformed")
            timeout = cast(dict[str, object], document["deadlines"])["command_s"]
            overall = cast(dict[str, object], document["deadlines"])["overall_s"]
            cleanup = float(cast(float, overall)) - float(cast(float, timeout))
            expected_remote = (
                f"{document['remote_helper']} --timeout {float(cast(float, timeout)):g} "
                f"--cleanup-timeout {cleanup:g} "
                f"--argv-base64 {encoded}"
            )
            if (
                arguments[0] != executable["path"]
                or arguments[-1] != expected_remote
                or document["remote_command"] != expected_remote
            ):
                raise CapabilityError("SSH command contradicts executable or encoding")
        else:
            application = document["application_plan"]
            if not isinstance(application, dict) or document["arguments"] != application.get(
                "arguments"
            ):
                raise CapabilityError("WsprryPi arguments contradict the application plan")
    elif evidence_type == "soapy_capture_capability":
        plan = document["plan"]
        output = document["output"]
        metadata = document["capture_metadata"]
        helper = document["helper"]
        if not all(isinstance(value, dict) for value in (plan, output, metadata, helper)):
            raise CapabilityError("capture capability evidence is malformed")
        plan_map = cast(dict[str, Any], plan)
        output_map = cast(dict[str, Any], output)
        metadata_map = cast(dict[str, Any], metadata)
        helper_map = cast(dict[str, Any], helper)
        if (
            output_map["path"] != plan_map["output_path"]
            or metadata_map["path"] != plan_map["metadata_path"]
            or helper_map["path"] != plan_map["helper"]
            or output_map["size_bytes"] != plan_map["sample_count"] * 8
        ):
            raise CapabilityError("capture artifacts contradict the resolved plan")
        expected_arguments = [
            str(plan_map["helper"]),
            "--enable-physical-sdr",
            str(plan_map["driver"]),
            str(plan_map["serial"]),
            f"{float(plan_map['center_frequency_hz']):g}",
            str(plan_map["sample_count"]),
            f"{float(plan_map['gain_db']):g}",
            str(plan_map["sample_rate_hz"]),
            str(plan_map["bandwidth_hz"]),
            str(plan_map["channel"]),
            str(plan_map["agc"]).lower(),
            str(plan_map["bias_tee"]).lower(),
            str(plan_map["read_timeout_us"]),
            f"{float(plan_map['maximum_elapsed_s']):g}",
            str(plan_map["output_path"]),
            str(plan_map["metadata_path"]),
            Path(str(plan_map["output_path"])).stem,
        ]
        if document["arguments"] != expected_arguments:
            raise CapabilityError("capture arguments contradict the resolved plan")
    elif evidence_type == "service_capability":
        if (document["outcome"] == "cleanup_failed") != (document["restoration_verified"] is False):
            raise CapabilityError("service outcome contradicts restoration verification")
        if (document["failure_cause"] is None) != (document["outcome"] == "completed"):
            raise CapabilityError("service failure cause contradicts operation outcome")
        if document["changed_by_harness"] is not (
            document["initial_running"] != document["requested_running"]
        ):
            raise CapabilityError("service change evidence contradicts initial state")
        if document["restoration_verified"] is True and (
            document["restored_running"] != document["initial_running"]
        ):
            raise CapabilityError("service restoration evidence contradicts initial state")
        if document["outcome"] == "completed" and (
            document["actual_running"] != document["requested_running"]
        ):
            raise CapabilityError("service did not reach its requested temporary state")
    elif evidence_type in {"gpio_quiescence_capability", "si5351_quiescence_capability"}:
        if (document["outcome"] == "verified") != (document["verified"] is True):
            raise CapabilityError("quiescence outcome contradicts verification")
        if evidence_type == "gpio_quiescence_capability":
            expected = document["observed_pin"] == document["pin"] and (
                document["observed_direction"] == document["expected_direction"]
            )
        else:
            required = set(cast(list[str], document["required_outputs"]))
            enabled = set(cast(list[str], document["enabled_outputs"]))
            expected = (
                document["observed_bus"] == document["bus"]
                and document["observed_address"] == document["address"]
                and not required.intersection(enabled)
            )
        expected = expected and document["owner"] is None
        if document["verified"] is not expected:
            raise CapabilityError("quiescence verification contradicts observed state")
