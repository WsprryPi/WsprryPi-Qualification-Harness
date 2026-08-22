"""Bounded, ownership-aware, single-use operation supervisor."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib.resources import files
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator, FormatChecker

from wsprrypi_qualification.adapters import (
    BoundedOperation,
    LifecycleAdapter,
    MockOperation,
    MockQuiescenceAdapter,
    MockReceiverAdapter,
    MockServiceAdapter,
    MockTransmitterAdapter,
    OperationOutcome,
    OperationResult,
)
from wsprrypi_qualification.classification import classify_result
from wsprrypi_qualification.models import (
    CleanupOutcome,
    FailureCause,
    GateOutcome,
    QualificationEvidence,
)
from wsprrypi_qualification.transports import LocalProcessOperation


class SupervisorError(RuntimeError):
    pass


class LifecyclePhase(StrEnum):
    PREPARE = "prepare"
    ACQUIRE = "acquire"
    START = "start"
    MONITOR = "monitor"
    CLEANUP = "cleanup"
    VERIFY = "verify"


@dataclass(frozen=True)
class OperationDeadlines:
    receiver_acquire_s: float = 1.0
    transmitter_acquire_s: float = 1.0
    receiver_start_s: float = 1.0
    transmitter_start_s: float = 1.0
    monitor_s: float = 1.0
    receiver_stop_s: float = 1.0
    transmitter_stop_s: float = 1.0
    receiver_release_s: float = 1.0
    transmitter_release_s: float = 1.0
    service_restore_s: float = 1.0
    leak_verify_s: float = 1.0
    quiescence_s: float = 1.0
    overall_s: float = 10.0

    def validate(self) -> None:
        if any(value <= 0 for value in asdict(self).values()):
            raise SupervisorError("every operation deadline must be positive")


@dataclass(frozen=True)
class ResolvedPlan:
    plan_id: str
    deadlines: OperationDeadlines = OperationDeadlines()
    mock_only: bool = True
    receiver_adapter: str = "mock_receiver"
    receiver_transport: str = "mock"
    transmitter_adapter: str = "mock_transmitter"
    transmitter_transport: str = "mock"
    service_policy: str = "restore_only_if_changed"
    backend: str = "mock"
    quiescence_adapter: str = "mock_quiescence"
    cancellation_policy: str = "observable_all_phases"
    cleanup_order: tuple[str, ...] = (
        "transmitter_stop",
        "receiver_stop",
        "transmitter_release",
        "receiver_release",
        "service_restore",
        "leak_verify",
        "quiescence",
    )
    helper_leak_policy: str = "no_owned_live_handles"

    def validate(self) -> None:
        if not self.mock_only:
            raise SupervisorError("mock supervisor plans must be mock-only")
        if not self.plan_id or not all(
            (
                self.receiver_adapter,
                self.receiver_transport,
                self.transmitter_adapter,
                self.transmitter_transport,
                self.backend,
                self.quiescence_adapter,
            )
        ):
            raise SupervisorError("resolved plan is incomplete")
        self.deadlines.validate()
        expected_order = (
            "transmitter_stop",
            "receiver_stop",
            "transmitter_release",
            "receiver_release",
            "service_restore",
            "leak_verify",
            "quiescence",
        )
        if self.cleanup_order != expected_order:
            raise SupervisorError("cleanup order differs from the maintained safety contract")


@dataclass(frozen=True)
class SupervisorEvent:
    sequence: int
    timestamp_utc: str
    phase: str
    component: str
    action: str
    outcome: str
    detail: str


@dataclass(frozen=True)
class OwnershipRecord:
    handle_id: str
    component: str
    operation: str
    transport: str
    started_utc: str
    stopped_utc: str | None
    owned_by_harness: bool
    final_alive: bool
    return_code: int | None
    outcome: str
    stdout: str
    stderr: str


@dataclass(frozen=True)
class CleanupAction:
    sequence: int
    timestamp_utc: str
    component: str
    handle_id: str
    action: str
    outcome: str
    detail: str


@dataclass
class _Owned:
    adapter: LifecycleAdapter
    acquire_handle: str
    start_handle: str | None = None
    acquired: bool = True
    started: bool = False


@dataclass(frozen=True)
class SupervisorResult:
    plan: ResolvedPlan
    outcome: Literal["completed", "aborted", "failed"]
    cleanup_outcome: Literal["verified", "failed"]
    final_status: Literal["inconclusive", "aborted", "fixture_blocked", "cleanup_failed"]
    failure_causes: tuple[str, ...]
    cleanup_installed_utc: str
    events: tuple[SupervisorEvent, ...]
    ownership: tuple[OwnershipRecord, ...]
    cleanup_actions: tuple[CleanupAction, ...]
    service_delta: dict[str, object] | None
    leak_verification: dict[str, object]
    quiescence: dict[str, object] | None

    def to_document(self) -> dict[str, object]:
        plan = asdict(self.plan)
        plan["cleanup_order"] = list(self.plan.cleanup_order)
        return {
            "schema_version": 1,
            "evidence_type": "slice4_supervisor",
            "plan": plan,
            "outcome": self.outcome,
            "cleanup_outcome": self.cleanup_outcome,
            "final_status": self.final_status,
            "failure_causes": list(self.failure_causes),
            "cleanup_installed_utc": self.cleanup_installed_utc,
            "events": [asdict(x) for x in self.events],
            "ownership": [asdict(x) for x in self.ownership],
            "cleanup_actions": [asdict(x) for x in self.cleanup_actions],
            "service_delta": self.service_delta,
            "leak_verification": self.leak_verification,
            "quiescence": self.quiescence,
        }


class Supervisor:
    def __init__(
        self,
        receiver: LifecycleAdapter,
        transmitter: LifecycleAdapter,
        *,
        service: MockServiceAdapter | None = None,
        quiescence: MockQuiescenceAdapter | None = None,
        leak_operation: BoundedOperation | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if service is not None and type(service) is not MockServiceAdapter:
            raise SupervisorError("unreviewed service adapter refused")
        if quiescence is not None and type(quiescence) is not MockQuiescenceAdapter:
            raise SupervisorError("unreviewed quiescence adapter refused")
        if leak_operation is not None and type(leak_operation) is not MockOperation:
            raise SupervisorError("unreviewed leak operation refused")
        self.receiver, self.transmitter = receiver, transmitter
        self.service, self.quiescence = service, quiescence
        self.leak_operation = leak_operation
        self._clock, self._monotonic = (
            clock or (lambda: datetime.now(UTC)),
            monotonic or time.monotonic,
        )
        self._events: list[SupervisorEvent] = []
        self._actions: list[CleanupAction] = []
        self._records: list[OwnershipRecord] = []
        self._owned: dict[str, _Owned] = {}
        self._used = False
        self.cleanup_installed_utc = self._utc()

    @staticmethod
    def _begin(adapter: LifecycleAdapter, action: str) -> BoundedOperation:
        try:
            return adapter.begin(action)
        except Exception as error:
            return MockOperation(
                f"launch-failed-{adapter.name}-{action}",
                adapter.name,
                action,
                OperationResult(OperationOutcome.LAUNCH_FAILED, None, str(error)),
            )

    def _utc(self) -> str:
        return self._clock().astimezone(UTC).isoformat().replace("+00:00", "Z")

    def _event(
        self, phase: LifecyclePhase, component: str, action: str, outcome: str, detail: str = ""
    ) -> None:
        self._events.append(
            SupervisorEvent(
                len(self._events) + 1, self._utc(), phase.value, component, action, outcome, detail
            )
        )

    def _execute(
        self,
        operation: BoundedOperation,
        timeout_s: float,
        phase: LifecyclePhase,
        cancellation: threading.Event | None,
        overall_deadline: float,
        *,
        honor_cancellation: bool = True,
    ) -> OperationResult:
        if type(operation) not in {MockOperation, LocalProcessOperation}:
            raise SupervisorError("unreviewed bounded-operation implementation refused")
        started_utc = self._utc()
        deadline = min(self._monotonic() + timeout_s, overall_deadline)
        result: OperationResult | None = None
        while result is None:
            if honor_cancellation and cancellation is not None and cancellation.is_set():
                operation.request_stop()
                result = (
                    operation.finalize_after_stop(
                        OperationOutcome.CANCELLED, "cancellation observed"
                    )
                    if type(operation) is LocalProcessOperation
                    else OperationResult(OperationOutcome.CANCELLED, None, "cancellation observed")
                )
                break
            if self._monotonic() >= deadline:
                operation.request_stop()
                if operation.is_alive():
                    operation.force_stop()
                result = (
                    operation.finalize_after_stop(
                        OperationOutcome.TIMED_OUT, "operation deadline exceeded"
                    )
                    if type(operation) is LocalProcessOperation
                    else OperationResult(
                        OperationOutcome.TIMED_OUT, None, "operation deadline exceeded"
                    )
                )
                break
            try:
                result = operation.poll()
            except Exception as error:
                operation.force_stop()
                result = OperationResult(OperationOutcome.UNEXPECTED_FAILURE, None, str(error))
            if result is None:
                time.sleep(0.001)
        stopped = self._utc()
        self._records.append(
            OwnershipRecord(
                operation.handle_id,
                operation.component,
                operation.action,
                operation.transport,
                started_utc,
                stopped,
                result.outcome is OperationOutcome.COMPLETED
                and operation.action in {"acquire", "start", "monitor"},
                operation.is_alive(),
                result.return_code,
                result.outcome.value,
                result.stdout,
                result.stderr,
            )
        )
        self._event(
            phase,
            operation.component,
            operation.action,
            "passed"
            if result.outcome is OperationOutcome.COMPLETED
            else "aborted"
            if result.outcome is OperationOutcome.CANCELLED
            else "failed",
            result.detail,
        )
        return result

    def run(
        self,
        plan: ResolvedPlan | None = None,
        *,
        cancellation: threading.Event | None = None,
        execution: BoundedOperation | None = None,
    ) -> SupervisorResult:
        if self._used:
            raise SupervisorError("Supervisor instances are single-use")
        self._used = True
        plan = plan or ResolvedPlan("slice4-mock")
        plan.validate()
        if (
            type(self.receiver) is not MockReceiverAdapter
            or type(self.transmitter) is not MockTransmitterAdapter
        ):
            raise SupervisorError("mock supervision accepts only reviewed mock lifecycle adapters")
        overall_deadline = self._monotonic() + plan.deadlines.overall_s
        self._event(
            LifecyclePhase.PREPARE,
            "supervisor",
            "cleanup_installed",
            "passed",
            self.cleanup_installed_utc,
        )
        outcome: Literal["completed", "aborted", "failed"] = "completed"
        causes: list[FailureCause] = []
        failure_phase = LifecyclePhase.ACQUIRE
        try:
            for adapter, timeout_s in (
                (self.receiver, plan.deadlines.receiver_acquire_s),
                (self.transmitter, plan.deadlines.transmitter_acquire_s),
            ):
                failure_phase = LifecyclePhase.ACQUIRE
                result = self._execute(
                    self._begin(adapter, "acquire"),
                    timeout_s,
                    LifecyclePhase.ACQUIRE,
                    cancellation,
                    overall_deadline,
                )
                if result.outcome is OperationOutcome.CANCELLED:
                    raise InterruptedError
                if result.outcome is not OperationOutcome.COMPLETED:
                    raise SupervisorError(f"{adapter.name} acquisition {result.outcome.value}")
                adapter.acquired = True
                self._owned[adapter.name] = _Owned(adapter, self._records[-1].handle_id)
            for adapter, timeout_s in (
                (self.receiver, plan.deadlines.receiver_start_s),
                (self.transmitter, plan.deadlines.transmitter_start_s),
            ):
                failure_phase = LifecyclePhase.START
                result = self._execute(
                    self._begin(adapter, "start"),
                    timeout_s,
                    LifecyclePhase.START,
                    cancellation,
                    overall_deadline,
                )
                if result.outcome is OperationOutcome.CANCELLED:
                    raise InterruptedError
                if result.outcome is not OperationOutcome.COMPLETED:
                    raise SupervisorError(f"{adapter.name} start {result.outcome.value}")
                adapter.started = True
                self._owned[adapter.name].started = True
                self._owned[adapter.name].start_handle = self._records[-1].handle_id
            monitored = execution or MockOperation("mock-execution-1", "execution", "monitor")
            failure_phase = LifecyclePhase.MONITOR
            result = self._execute(
                monitored,
                plan.deadlines.monitor_s,
                LifecyclePhase.MONITOR,
                cancellation,
                overall_deadline,
            )
            if result.outcome is OperationOutcome.CANCELLED:
                raise InterruptedError
            if result.outcome is not OperationOutcome.COMPLETED:
                raise SupervisorError(f"execution {result.outcome.value}")
        except InterruptedError:
            outcome = "aborted"
            causes.append(FailureCause.EXTERNAL_ABORT)
        except Exception:
            outcome = "failed"
            causes.append(
                FailureCause.OWNERSHIP_CONFLICT
                if failure_phase is LifecyclePhase.ACQUIRE
                else FailureCause.DEPENDENCY_UNAVAILABLE
            )
        cleanup_ok, leak, quiescence, cleanup_cancelled = self._cleanup(
            plan, cancellation, overall_deadline
        )
        if cleanup_cancelled and outcome == "completed":
            outcome = "aborted"
            causes.append(FailureCause.EXTERNAL_ABORT)
        if not cleanup_ok:
            causes.append(FailureCause.CLEANUP)
        final = cast(
            Literal["inconclusive", "aborted", "fixture_blocked", "cleanup_failed"],
            classify_result(
                QualificationEvidence(
                    True,
                    GateOutcome.NOT_RUN,
                    GateOutcome.NOT_RUN,
                    CleanupOutcome.VERIFIED if cleanup_ok else CleanupOutcome.FAILED,
                    tuple(causes),
                    outcome == "aborted",
                )
            ).value,
        )
        service_delta: dict[str, object] | None = (
            None
            if self.service is None
            else {
                "initial_running": self.service.initial_running,
                "current_running": self.service.running,
                "changed_by_harness": self.service.changed_by_harness,
                "restored": not self.service.changed_by_harness
                or self.service.running == self.service.initial_running,
            }
        )
        result_doc = SupervisorResult(
            plan,
            outcome,
            "verified" if cleanup_ok else "failed",
            final,
            tuple(x.value for x in causes),
            self.cleanup_installed_utc,
            tuple(self._events),
            tuple(self._records),
            tuple(self._actions),
            service_delta,
            leak,
            quiescence,
        )
        validate_supervisor_document(result_doc.to_document())
        return result_doc

    def _cleanup(
        self, plan: ResolvedPlan, cancellation: threading.Event | None, overall_deadline: float
    ) -> tuple[bool, dict[str, object], dict[str, object] | None, bool]:
        failed = False
        cancellation_recorded = False
        for adapter, action, timeout_s in (
            (self.transmitter, "stop", plan.deadlines.transmitter_stop_s),
            (self.receiver, "stop", plan.deadlines.receiver_stop_s),
            (self.transmitter, "release", plan.deadlines.transmitter_release_s),
            (self.receiver, "release", plan.deadlines.receiver_release_s),
        ):
            if cancellation is not None and cancellation.is_set() and not cancellation_recorded:
                self._event(
                    LifecyclePhase.CLEANUP, "supervisor", "cancellation_observed", "aborted"
                )
                cancellation_recorded = True
            owned = self._owned.get(adapter.name)
            applicable = owned is not None and (
                owned.started if action == "stop" else owned.acquired
            )
            if not applicable:
                continue
            assert owned is not None
            result = self._execute(
                self._begin(adapter, action),
                timeout_s,
                LifecyclePhase.CLEANUP,
                cancellation,
                overall_deadline,
                honor_cancellation=False,
            )
            self._actions.append(
                CleanupAction(
                    len(self._actions) + 1,
                    self._utc(),
                    adapter.name,
                    self._records[-1].handle_id,
                    action,
                    "passed" if result.outcome is OperationOutcome.COMPLETED else "failed",
                    result.detail,
                )
            )
            if result.outcome is OperationOutcome.COMPLETED:
                if action == "stop":
                    owned.started = False
                    adapter.started = False
                else:
                    owned.acquired = False
                    adapter.acquired = False
                    adapter.released = True
            else:
                failed = True
            if cancellation is not None and cancellation.is_set() and not cancellation_recorded:
                self._event(
                    LifecyclePhase.CLEANUP, "supervisor", "cancellation_observed", "aborted"
                )
                cancellation_recorded = True
        if self.service is not None and self.service.changed_by_harness:
            result = self._execute(
                self.service.begin_restore(),
                plan.deadlines.service_restore_s,
                LifecyclePhase.CLEANUP,
                cancellation,
                overall_deadline,
                honor_cancellation=False,
            )
            self._actions.append(
                CleanupAction(
                    len(self._actions) + 1,
                    self._utc(),
                    "service",
                    self._records[-1].handle_id,
                    "restore",
                    "passed" if result.outcome is OperationOutcome.COMPLETED else "failed",
                    result.detail,
                )
            )
            failed |= result.outcome is not OperationOutcome.COMPLETED
            if result.outcome is OperationOutcome.COMPLETED:
                self.service.running = self.service.initial_running
            if cancellation is not None and cancellation.is_set() and not cancellation_recorded:
                self._event(
                    LifecyclePhase.CLEANUP, "supervisor", "cancellation_observed", "aborted"
                )
                cancellation_recorded = True
        leak_operation = self.leak_operation or MockOperation(
            "leak-verification", "helpers", "leak_verify"
        )
        leak_result = self._execute(
            leak_operation,
            plan.deadlines.leak_verify_s,
            LifecyclePhase.VERIFY,
            cancellation,
            overall_deadline,
            honor_cancellation=False,
        )
        remaining = sorted(
            item.start_handle or item.acquire_handle
            for item in self._owned.values()
            if item.started or item.acquired or item.adapter.child_alive()
        )
        leak = {
            "verified": not remaining and leak_result.outcome is OperationOutcome.COMPLETED,
            "remaining": remaining,
        }
        self._actions.append(
            CleanupAction(
                len(self._actions) + 1,
                self._utc(),
                "helpers",
                self._records[-1].handle_id,
                "leak_verify",
                "passed" if leak["verified"] else "failed",
                "" if not remaining else "owned handles remain",
            )
        )
        failed |= bool(remaining) or leak_result.outcome is not OperationOutcome.COMPLETED
        if cancellation is not None and cancellation.is_set() and not cancellation_recorded:
            self._event(LifecyclePhase.CLEANUP, "supervisor", "cancellation_observed", "aborted")
            cancellation_recorded = True
        quiescence = None
        if self.quiescence is not None:
            result = self._execute(
                self.quiescence.begin_inspection(),
                plan.deadlines.quiescence_s,
                LifecyclePhase.VERIFY,
                cancellation,
                overall_deadline,
                honor_cancellation=False,
            )
            verified = result.outcome is OperationOutcome.COMPLETED
            quiescence = {
                "backend": self.quiescence.backend,
                "verified": verified,
                "handle_id": self._records[-1].handle_id,
            }
            self._actions.append(
                CleanupAction(
                    len(self._actions) + 1,
                    self._utc(),
                    self.quiescence.backend,
                    self._records[-1].handle_id,
                    "quiescence",
                    "passed" if verified else "failed",
                    result.detail,
                )
            )
            failed |= not verified
            if cancellation is not None and cancellation.is_set() and not cancellation_recorded:
                self._event(
                    LifecyclePhase.CLEANUP, "supervisor", "cancellation_observed", "aborted"
                )
                cancellation_recorded = True
        return not failed, leak, quiescence, cancellation_recorded


def validate_supervisor_document(document: dict[str, object]) -> None:
    schema = json.loads(
        files("wsprrypi_qualification.schemas")
        .joinpath("slice4-supervisor.schema.json")
        .read_text(encoding="utf-8")
    )
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document)
    )
    if errors:
        raise ValueError(errors[0].message)
    for schema_name, field_name in (
        ("slice4-plan.schema.json", "plan"),
        ("slice4-ownership.schema.json", "ownership"),
        ("slice4-cleanup.schema.json", "cleanup_actions"),
        ("slice4-quiescence.schema.json", "quiescence"),
        ("slice4-service.schema.json", "service_delta"),
        ("slice4-leak.schema.json", "leak_verification"),
        ("slice4-events.schema.json", "events"),
    ):
        fragment = json.loads(
            files("wsprrypi_qualification.schemas")
            .joinpath(schema_name)
            .read_text(encoding="utf-8")
        )
        fragment_errors = list(
            Draft202012Validator(fragment, format_checker=FormatChecker()).iter_errors(
                document[field_name]
            )
        )
        if fragment_errors:
            raise ValueError(f"{field_name}: {fragment_errors[0].message}")
    events = cast(list[dict[str, Any]], document["events"])
    if [x["sequence"] for x in events] != list(range(1, len(events) + 1)):
        raise ValueError("supervisor event sequence is not contiguous")
    timestamps = [datetime.fromisoformat(x["timestamp_utc"].replace("Z", "+00:00")) for x in events]
    if timestamps != sorted(timestamps):
        raise ValueError("supervisor event timestamps decrease")
    if not events or events[0]["action"] != "cleanup_installed":
        raise ValueError("cleanup responsibility was not installed first")
    actions = cast(list[dict[str, Any]], document["cleanup_actions"])
    if [x["sequence"] for x in actions] != list(range(1, len(actions) + 1)):
        raise ValueError("cleanup action sequence is not contiguous")
    installed = datetime.fromisoformat(
        cast(str, document["cleanup_installed_utc"]).replace("Z", "+00:00")
    )
    action_times = [
        datetime.fromisoformat(cast(str, item["timestamp_utc"]).replace("Z", "+00:00"))
        for item in actions
    ]
    if action_times != sorted(action_times) or any(item < installed for item in action_times):
        raise ValueError("cleanup action timestamps are invalid or precede installation")
    plan = cast(dict[str, Any], document["plan"])
    cleanup_order = cast(list[str], plan["cleanup_order"])
    expected_cleanup_order = [
        "transmitter_stop",
        "receiver_stop",
        "transmitter_release",
        "receiver_release",
        "service_restore",
        "leak_verify",
        "quiescence",
    ]
    if cleanup_order != expected_cleanup_order:
        raise ValueError("resolved cleanup order differs from the safety contract")
    tokens: list[str] = []
    acquired_components = [
        event["component"]
        for event in events
        if event["phase"] == "acquire" and event["outcome"] == "passed"
    ]
    role_by_component = {
        component: role
        for component, role in zip(
            acquired_components[:2], ("receiver", "transmitter"), strict=False
        )
    }
    for action in actions:
        if action["action"] in {"stop", "release"}:
            role = role_by_component.get(action["component"])
            if role is None:
                raise ValueError("cleanup action targets an unacquired component")
            tokens.append(f"{role}_{action['action']}")
        elif action["action"] == "restore":
            tokens.append("service_restore")
        elif action["action"] == "leak_verify":
            tokens.append("leak_verify")
        elif action["action"] == "quiescence":
            tokens.append("quiescence")
    try:
        ranks = [cleanup_order.index(token) for token in tokens]
    except ValueError as error:
        raise ValueError("cleanup action is absent from the resolved cleanup order") from error
    if ranks != sorted(ranks):
        raise ValueError("cleanup actions violate the resolved safety order")
    ownership = cast(list[dict[str, Any]], document["ownership"])
    handle_ids = [cast(str, item["handle_id"]) for item in ownership]
    if len(handle_ids) != len(set(handle_ids)):
        raise ValueError("ownership handle IDs are not unique")
    previous_start: datetime | None = None
    terminal = {item.value for item in OperationOutcome}
    for item in ownership:
        start = datetime.fromisoformat(cast(str, item["started_utc"]).replace("Z", "+00:00"))
        stop_text = cast(str | None, item["stopped_utc"])
        stop = datetime.fromisoformat(stop_text.replace("Z", "+00:00")) if stop_text else None
        if start.utcoffset() != UTC.utcoffset(start) or stop is None or stop < start:
            raise ValueError("ownership timestamps are invalid or reversed")
        if previous_start is not None and start < previous_start:
            raise ValueError("ownership records are not time ordered")
        previous_start = start
        if item["outcome"] in terminal and item["final_alive"]:
            raise ValueError("terminal ownership record remains alive")
        outcome = item["outcome"]
        code = item["return_code"]
        if outcome == "completed" and code != 0:
            raise ValueError("completed operation lacks successful return code")
        if outcome in {"timed_out", "cancelled", "launch_failed"} and code == 0:
            raise ValueError("failed operation reports successful return code")
    leak = cast(dict[str, Any], document["leak_verification"])
    quiescence = cast(dict[str, Any] | None, document["quiescence"])
    if document["cleanup_outcome"] == "verified" and (
        not leak["verified"]
        or any(x["final_alive"] for x in ownership)
        or (quiescence is not None and not quiescence["verified"])
    ):
        raise ValueError("verified cleanup contradicts liveness, leak, or quiescence evidence")
    completed: dict[str, set[str]] = {}
    handles = {item["handle_id"]: item for item in ownership}
    for item in ownership:
        component = cast(str, item["component"])
        operation = cast(str, item["operation"])
        prior = completed.setdefault(component, set())
        if operation == "start" and "acquire" not in prior:
            raise ValueError("child start lacks prior acquisition")
        if operation == "stop" and "start" not in prior:
            raise ValueError("child stop lacks prior start")
        if operation == "release" and "acquire" not in prior:
            raise ValueError("resource release lacks prior acquisition")
        if item["outcome"] == "completed":
            prior.add(operation)
    for action in actions:
        if action["action"] in {"stop", "release", "restore", "leak_verify", "quiescence"}:
            record = handles.get(action["handle_id"])
            if record is None or record["operation"] != action["action"]:
                raise ValueError("cleanup action lacks matching owned operation")
            operation_stop = datetime.fromisoformat(
                cast(str, record["stopped_utc"]).replace("Z", "+00:00")
            )
            action_time = datetime.fromisoformat(
                cast(str, action["timestamp_utc"]).replace("Z", "+00:00")
            )
            if action_time < operation_stop:
                raise ValueError("cleanup action predates its operation evidence")
    service = cast(dict[str, Any] | None, document["service_delta"])
    if (
        service is not None
        and service.get("restored")
        and service["current_running"] != service["initial_running"]
    ):
        raise ValueError("service restoration contradicts final state")
    causes = tuple(FailureCause(x) for x in cast(list[str], document["failure_causes"]))
    expected = classify_result(
        QualificationEvidence(
            True,
            GateOutcome.NOT_RUN,
            GateOutcome.NOT_RUN,
            CleanupOutcome(cast(str, document["cleanup_outcome"])),
            causes,
            document["outcome"] == "aborted",
        )
    ).value
    if document["final_status"] != expected:
        raise ValueError(f"final status contradicts evidence; expected {expected}")
    if (
        document["final_status"] == "qualified"
        or cast(dict[str, Any], document["plan"])["mock_only"] is not True
    ):
        raise ValueError("mock-only evidence cannot claim qualification")
