"""Portable bounded-operation contracts and deterministic Slice 4 mocks."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from threading import Event
from typing import Protocol


class AdapterError(RuntimeError):
    pass


class OperationOutcome(StrEnum):
    COMPLETED = "completed"
    NONZERO_EXIT = "nonzero_exit"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    DISCONNECTED = "disconnected"
    LAUNCH_FAILED = "launch_failed"
    UNEXPECTED_FAILURE = "unexpected_failure"
    CLEANUP_FAILED = "cleanup_failed"


@dataclass(frozen=True)
class OperationResult:
    outcome: OperationOutcome
    return_code: int | None = 0
    detail: str = ""
    stdout: str = ""
    stderr: str = ""


class BoundedOperation(Protocol):
    handle_id: str
    component: str
    action: str
    transport: str

    def poll(self) -> OperationResult | None: ...
    def request_stop(self) -> None: ...
    def force_stop(self) -> None: ...
    def is_alive(self) -> bool: ...
    def collect_result(self) -> OperationResult | None: ...


@dataclass(slots=True)
class MockOperation:
    handle_id: str
    component: str
    action: str
    result: OperationResult = field(
        default_factory=lambda: OperationResult(OperationOutcome.COMPLETED)
    )
    polls_before_result: int = 0
    transport: str = "mock"
    cancel_event: Event | None = None
    cancel_at_poll: int | None = None
    _alive: bool = True
    _poll_count: int = 0

    def poll(self) -> OperationResult | None:
        self._poll_count += 1
        if self.cancel_event is not None and self.cancel_at_poll == self._poll_count:
            self.cancel_event.set()
        if not self._alive:
            return self.result
        if self.polls_before_result > 0:
            self.polls_before_result -= 1
            return None
        self._alive = False
        return self.result

    def request_stop(self) -> None:
        self._alive = False

    def force_stop(self) -> None:
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    def collect_result(self) -> OperationResult | None:
        return self.poll()


class LifecycleAdapter(Protocol):
    name: str
    transport: str
    acquired: bool
    started: bool
    released: bool

    def begin(self, action: str) -> BoundedOperation: ...
    def child_alive(self) -> bool: ...


@dataclass(slots=True)
class MockLifecycleAdapter:
    name: str
    fail_at: set[str] = field(default_factory=set)
    block_at: set[str] = field(default_factory=set)
    raise_at: set[str] = field(default_factory=set)
    cancellation_event: Event | None = None
    cancel_at: set[str] = field(default_factory=set)
    transport: str = "mock"
    acquired: bool = False
    started: bool = False
    released: bool = False
    calls: list[str] = field(default_factory=list)
    _counter: int = 0

    def begin(self, action: str) -> MockOperation:
        if action in self.raise_at:
            raise RuntimeError(f"injected {action} begin failure")
        self.calls.append(action)
        self._counter += 1
        outcome = (
            OperationOutcome.CLEANUP_FAILED
            if action in {"stop", "release", "quiescence"} and action in self.fail_at
            else OperationOutcome.UNEXPECTED_FAILURE
            if action in self.fail_at
            else OperationOutcome.COMPLETED
        )
        return MockOperation(
            f"{self.name}-{action}-{self._counter}",
            self.name,
            action,
            OperationResult(
                outcome,
                0 if outcome is OperationOutcome.COMPLETED else None,
                f"injected {action}" if action in self.fail_at else "",
            ),
            1_000_000 if action in self.block_at else 0,
            self.transport,
            self.cancellation_event,
            1 if action in self.cancel_at else None,
        )

    def child_alive(self) -> bool:
        return self.started


@dataclass(slots=True)
class MockTransmitterAdapter(MockLifecycleAdapter):
    pass


@dataclass(slots=True)
class MockReceiverAdapter(MockLifecycleAdapter):
    pass


@dataclass(slots=True)
class MockServiceAdapter:
    initial_running: bool
    fail_restore: bool = False
    block_restore: bool = False
    cancellation_event: Event | None = None
    cancel_on_restore: bool = False
    running: bool = field(init=False)
    changed_by_harness: bool = False
    _counter: int = 0

    def __post_init__(self) -> None:
        self.running = self.initial_running

    def set_running(self, running: bool) -> None:
        if self.running != running:
            self.running = running
            self.changed_by_harness = True

    def begin_restore(self) -> MockOperation:
        self._counter += 1
        outcome = (
            OperationOutcome.CLEANUP_FAILED if self.fail_restore else OperationOutcome.COMPLETED
        )
        return MockOperation(
            f"service-restore-{self._counter}",
            "service",
            "restore",
            OperationResult(
                outcome,
                0 if not self.fail_restore else None,
                "injected restore" if self.fail_restore else "",
            ),
            1_000_000 if self.block_restore else 0,
            cancel_event=self.cancellation_event,
            cancel_at_poll=1 if self.cancel_on_restore else None,
        )


@dataclass(slots=True)
class MockQuiescenceAdapter:
    backend: str
    verified: bool = True
    blocked: bool = False
    cancellation_event: Event | None = None
    cancel_on_inspection: bool = False
    _counter: int = 0

    def begin_inspection(self) -> MockOperation:
        self._counter += 1
        outcome = OperationOutcome.COMPLETED if self.verified else OperationOutcome.CLEANUP_FAILED
        return MockOperation(
            f"{self.backend}-quiescence-{self._counter}",
            self.backend,
            "quiescence",
            OperationResult(
                outcome, 0 if self.verified else None, "not quiescent" if not self.verified else ""
            ),
            1_000_000 if self.blocked else 0,
            cancel_event=self.cancellation_event,
            cancel_at_poll=1 if self.cancel_on_inspection else None,
        )
