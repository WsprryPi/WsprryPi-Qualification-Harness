"""Versioned, fail-closed server for remote capability operations."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from wsprrypi_qualification.bounded_tone_control import (
    BoundedToneEndpoint,
    run_bounded_tone_transaction,
)
from wsprrypi_qualification.offline import validate_document
from wsprrypi_qualification.repository_protection import (
    RepositoryProtectionError,
    RepositorySnapshot,
    RuntimeArtifactBinding,
    SourceArtifactBinding,
    bind_source,
    capture_repository_snapshot,
    compare_repository_snapshot,
    discover_protected_roots,
    validate_process_boundary,
)

PROTOCOL_VERSION = 1
OPERATIONS = frozenset(
    {
        "process-start",
        "process-prepare",
        "process-arm",
        "process-wait",
        "process-stop",
        "service-inspect",
        "service-set",
        "gpio-inspect",
        "si5351-inspect",
        "bounded-tone",
    }
)


class HelperProtocolError(RuntimeError):
    """A helper request cannot be handled safely."""


class ServiceBackend(Protocol):
    def inspect(self, name: str, manager: str) -> bool: ...
    def set_running(self, name: str, manager: str, running: bool) -> bool: ...


class GpioBackend(Protocol):
    def inspect(self, pin: int) -> dict[str, object]: ...


class Si5351Backend(Protocol):
    def inspect(self, bus: int, address: str) -> dict[str, object]: ...


class BoundedToneBackend(Protocol):
    def run(
        self, request_id: str, frequency_hz: int, duration_ms: int, outer_timeout_s: float
    ) -> dict[str, object]: ...


class LoopbackBoundedToneBackend:
    def __init__(self, endpoint: BoundedToneEndpoint, wsprrypi_revision: str) -> None:
        self.endpoint, self.wsprrypi_revision = endpoint, wsprrypi_revision

    def run(
        self, request_id: str, frequency_hz: int, duration_ms: int, outer_timeout_s: float
    ) -> dict[str, object]:
        result = run_bounded_tone_transaction(
            self.endpoint,
            request_id=request_id,
            frequency_hz=frequency_hz,
            duration_ms=duration_ms,
            outer_timeout_s=outer_timeout_s,
        )
        result["wsprrypi_revision"] = self.wsprrypi_revision
        return result


class UnsupportedBackend:
    def __getattr__(self, name: str) -> Any:
        raise HelperProtocolError("requested production capability is not configured")


class SystemctlServiceBackend:
    """Narrow Raspberry Pi OS service provider using a pinned executable."""

    def __init__(
        self,
        executable: Path,
        executable_sha256: str,
        allowed_names: frozenset[str],
        timeout_s: float,
        privilege_wrapper: Path | None = None,
        privilege_wrapper_sha256: str | None = None,
    ) -> None:
        if not executable.is_absolute() or not executable.is_file() or timeout_s <= 0:
            raise HelperProtocolError("systemctl provider is not safely configured")
        if _sha256(executable) != executable_sha256:
            raise HelperProtocolError("systemctl executable hash does not match configuration")
        if (privilege_wrapper is None) is not (privilege_wrapper_sha256 is None):
            raise HelperProtocolError("service privilege wrapper binding is incomplete")
        if privilege_wrapper is not None:
            if not privilege_wrapper.is_absolute() or not privilege_wrapper.is_file():
                raise HelperProtocolError("service privilege wrapper is not safely configured")
            if _sha256(privilege_wrapper) != privilege_wrapper_sha256:
                raise HelperProtocolError(
                    "service privilege wrapper hash does not match configuration"
                )
        self.executable, self.executable_sha256 = executable, executable_sha256
        self.privilege_wrapper = privilege_wrapper
        self.privilege_wrapper_sha256 = privilege_wrapper_sha256
        self.allowed, self.timeout_s = allowed_names, timeout_s

    def inspect(self, name: str, manager: str) -> bool:
        self._check(name, manager)
        result = subprocess.run(
            [str(self.executable), "is-active", "--quiet", "--", name],
            shell=False,
            check=False,
            timeout=self.timeout_s,
            capture_output=True,
        )
        if result.returncode not in {0, 3}:
            raise HelperProtocolError("service inspection failed")
        return result.returncode == 0

    def set_running(self, name: str, manager: str, running: bool) -> bool:
        self._check(name, manager)
        action = "start" if running else "stop"
        result = subprocess.run(
            [*self._prefix(), str(self.executable), action, "--", name],
            shell=False,
            check=False,
            timeout=self.timeout_s,
            capture_output=True,
        )
        if result.returncode != 0:
            raise HelperProtocolError("service state change failed")
        return self.inspect(name, manager)

    def _check(self, name: str, manager: str) -> None:
        if _sha256(self.executable) != self.executable_sha256:
            raise HelperProtocolError("systemctl executable identity changed")
        if self.privilege_wrapper is not None and (
            _sha256(self.privilege_wrapper) != self.privilege_wrapper_sha256
        ):
            raise HelperProtocolError("service privilege wrapper identity changed")
        if manager != "systemd" or name not in self.allowed:
            raise HelperProtocolError("service is outside the configured provider scope")

    def _prefix(self) -> list[str]:
        if self.privilege_wrapper is None:
            return []
        return [str(self.privilege_wrapper), "-n", "--"]


class JsonInspectionBackend:
    """Pinned read-only platform helper for GPIO or Si5351 inspection."""

    def __init__(
        self, executable: Path, executable_sha256: str, operation: str, timeout_s: float
    ) -> None:
        if not executable.is_absolute() or not executable.is_file() or timeout_s <= 0:
            raise HelperProtocolError("inspection provider is not safely configured")
        if _sha256(executable) != executable_sha256:
            raise HelperProtocolError("inspection executable hash does not match configuration")
        self.executable, self.executable_sha256 = executable, executable_sha256
        self.operation, self.timeout_s = operation, timeout_s

    def request(self, payload: dict[str, object]) -> dict[str, object]:
        if _sha256(self.executable) != self.executable_sha256:
            raise HelperProtocolError("inspection executable identity changed")
        result = subprocess.run(
            [
                str(self.executable),
                self.operation,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ],
            shell=False,
            check=False,
            timeout=self.timeout_s,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode != 0:
            raise HelperProtocolError("read-only inspection provider failed")
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HelperProtocolError("inspection provider returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise HelperProtocolError("inspection provider response must be an object")
        return cast(dict[str, object], response)


class CommandGpioBackend:
    def __init__(self, backend: JsonInspectionBackend) -> None:
        self.backend = backend

    def inspect(self, pin: int) -> dict[str, object]:
        return self.backend.request({"pin": pin})


class CommandSi5351Backend:
    def __init__(self, backend: JsonInspectionBackend) -> None:
        self.backend = backend

    def inspect(self, bus: int, address: str) -> dict[str, object]:
        return self.backend.request({"bus": bus, "address": address})


@dataclass
class RepositoryGuardState:
    git: Path
    roots: tuple[Any, ...]
    snapshots: tuple[RepositorySnapshot, ...]
    inspection_timeout_s: float


@dataclass
class OwnedChild:
    process: subprocess.Popen[bytes] | None
    stdout: Any
    stderr: Any
    deadline: float
    cleanup_timeout_s: float
    launch_arguments: list[str]
    environment: dict[str, str]
    working_directory: Path | None = None
    repository_guard: RepositoryGuardState | None = None
    scheduled_start_utc: str | None = None
    scheduled_monotonic: float | None = None
    actual_start_utc: str | None = None
    schedule_error_ms: float | None = None
    cancelled_before_launch: bool = False
    launch_error: str | None = None
    deadline_enforced: bool = False
    state_changed: threading.Condition = field(default_factory=threading.Condition)


class OwnedProcessRegistry:
    """Own children by opaque handle and clean them with bounded escalation."""

    def __init__(
        self,
        privilege_wrapper: Path | None = None,
        privilege_wrapper_sha256: str | None = None,
    ) -> None:
        if (privilege_wrapper is None) is not (privilege_wrapper_sha256 is None):
            raise HelperProtocolError("process privilege wrapper binding is incomplete")
        if privilege_wrapper is not None:
            if not privilege_wrapper.is_absolute() or not privilege_wrapper.is_file():
                raise HelperProtocolError("process privilege wrapper is not safely configured")
            if _sha256(privilege_wrapper) != privilege_wrapper_sha256:
                raise HelperProtocolError(
                    "process privilege wrapper hash does not match configuration"
                )
        self.privilege_wrapper = privilege_wrapper
        self.privilege_wrapper_sha256 = privilege_wrapper_sha256
        self._children: dict[str, OwnedChild] = {}
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._watchdog = threading.Thread(target=self._enforce_deadlines, daemon=True)
        self._watchdog.start()

    def start(self, payload: dict[str, object], *, deferred: bool = False) -> dict[str, object]:
        arguments = _string_list(payload, "arguments")
        expected_wrapper_path = payload["privilege_wrapper_path"]
        expected_wrapper_hash = payload["privilege_wrapper_sha256"]
        configured_wrapper_path = (
            str(self.privilege_wrapper) if self.privilege_wrapper is not None else None
        )
        if (
            expected_wrapper_path != configured_wrapper_path
            or expected_wrapper_hash != self.privilege_wrapper_sha256
        ):
            raise HelperProtocolError("process privilege wrapper does not match resolved plan")
        executable = Path(arguments[0])
        expected_hash = _string(payload, "executable_sha256")
        timeout = _positive_number(payload, "hard_timeout_s")
        cleanup_timeout = (
            _positive_number(payload, "cleanup_timeout_s")
            if "cleanup_timeout_s" in payload
            else timeout
        )
        if not executable.is_absolute() or not executable.is_file():
            raise HelperProtocolError("executable must be an existing absolute file")
        if _sha256(executable) != expected_hash:
            raise HelperProtocolError("executable SHA-256 does not match resolved plan")
        if self.privilege_wrapper is not None and (
            _sha256(self.privilege_wrapper) != self.privilege_wrapper_sha256
        ):
            raise HelperProtocolError("process privilege wrapper identity changed")
        pinned_arguments = payload.get("pinned_arguments")
        if not isinstance(pinned_arguments, dict):
            raise HelperProtocolError("pinned process arguments must be an object")
        for raw_path, raw_digest in pinned_arguments.items():
            if (
                not isinstance(raw_path, str)
                or not isinstance(raw_digest, str)
                or raw_path not in arguments
            ):
                raise HelperProtocolError("pinned process argument is not in the argument vector")
            path = Path(raw_path)
            if not path.is_absolute() or not path.is_file() or _sha256(path) != raw_digest:
                raise HelperProtocolError("pinned process argument identity changed")
        environment = _environment(payload.get("environment", {}))
        working_directory, repository_guard = self._prepare_repository_guard(
            payload.get("repository_guard"), arguments, pinned_arguments
        )
        if pinned_arguments and repository_guard is None:
            raise HelperProtocolError("pinned process inputs require a repository mutation guard")
        scheduled_text = payload.get("scheduled_start_utc")
        schedule_after_arm = payload.get("schedule_after_arm_s")
        minimum_margin = payload.get("minimum_arm_margin_s", 0.0)
        if not isinstance(minimum_margin, (int, float)) or isinstance(minimum_margin, bool):
            raise HelperProtocolError("minimum arm margin must be a finite nonnegative number")
        minimum_margin = float(minimum_margin)
        if not math.isfinite(minimum_margin) or minimum_margin < 0:
            raise HelperProtocolError("minimum arm margin must be a finite nonnegative number")
        observed_utc = datetime.now(UTC)
        scheduled_utc: datetime | None = None
        scheduled_monotonic: float | None = None
        arm_margin_s: float | None = None
        if deferred and (scheduled_text is not None or schedule_after_arm is not None):
            raise HelperProtocolError("prepared process cannot also specify a schedule")
        if scheduled_text is not None and schedule_after_arm is not None:
            raise HelperProtocolError("scheduled process start has conflicting time bases")
        if schedule_after_arm is not None:
            if not isinstance(schedule_after_arm, (int, float)) or isinstance(
                schedule_after_arm, bool
            ):
                raise HelperProtocolError("relative scheduled start must be finite and positive")
            arm_margin_s = float(schedule_after_arm)
            if not math.isfinite(arm_margin_s) or arm_margin_s <= 0:
                raise HelperProtocolError("relative scheduled start must be finite and positive")
            if arm_margin_s < minimum_margin:
                raise HelperProtocolError("scheduled process start has insufficient arm margin")
            if arm_margin_s > timeout:
                raise HelperProtocolError("scheduled process start exceeds the hard deadline")
            scheduled_utc = observed_utc + timedelta(seconds=arm_margin_s)
            scheduled_text = scheduled_utc.isoformat().replace("+00:00", "Z")
            scheduled_monotonic = time.monotonic() + arm_margin_s
        elif scheduled_text is not None:
            if not isinstance(scheduled_text, str) or not scheduled_text.endswith("Z"):
                raise HelperProtocolError("scheduled process start must be an absolute UTC time")
            try:
                scheduled_utc = datetime.fromisoformat(scheduled_text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HelperProtocolError("scheduled process start is malformed") from exc
            if scheduled_utc.tzinfo != UTC:
                raise HelperProtocolError("scheduled process start must be UTC")
            arm_margin_s = (scheduled_utc - observed_utc).total_seconds()
            if not math.isfinite(arm_margin_s) or arm_margin_s < minimum_margin:
                raise HelperProtocolError("scheduled process start has insufficient arm margin")
            if arm_margin_s > timeout:
                raise HelperProtocolError("scheduled process start exceeds the hard deadline")
            scheduled_monotonic = time.monotonic() + arm_margin_s
        out = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115
        err = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115
        launch_arguments = arguments
        if self.privilege_wrapper is not None:
            launch_arguments = [str(self.privilege_wrapper), "-n", "--", *arguments]
        handle = f"owned-{uuid.uuid4()}"
        child = OwnedChild(
            process=None,
            stdout=out,
            stderr=err,
            deadline=time.monotonic() + timeout,
            cleanup_timeout_s=cleanup_timeout,
            launch_arguments=launch_arguments,
            environment=environment,
            working_directory=working_directory,
            repository_guard=repository_guard,
            scheduled_start_utc=scheduled_text if scheduled_utc is not None else None,
            scheduled_monotonic=scheduled_monotonic,
        )
        with self._lock:
            self._children[handle] = child
        if deferred:
            pass
        elif scheduled_utc is None:
            self._launch(child)
            if child.process is None:
                with self._lock:
                    self._children.pop(handle, None)
                out.close()
                err.close()
                raise HelperProtocolError(f"owned process launch failed: {child.launch_error}")
        else:
            threading.Thread(
                target=self._wait_and_launch,
                args=(child,),
                name=f"wspq-scheduled-{handle}",
                daemon=True,
            ).start()
        return {
            "handle_id": handle,
            "child_identity": str(child.process.pid) if child.process is not None else "armed",
            "cleanup_verified": False,
            "scheduled_start_utc": scheduled_text,
            "helper_observed_utc": observed_utc.isoformat().replace("+00:00", "Z"),
            "arm_margin_s": arm_margin_s,
        }

    def arm(self, payload: dict[str, object]) -> dict[str, object]:
        handle = _string(payload, "handle_id")
        arm_margin_s = _positive_number(payload, "schedule_after_arm_s")
        minimum_margin_s = _positive_number(payload, "minimum_arm_margin_s")
        if arm_margin_s < minimum_margin_s:
            raise HelperProtocolError("scheduled process start has insufficient arm margin")
        child = self._owned(handle)
        with child.state_changed:
            if child.process is not None or child.scheduled_monotonic is not None:
                raise HelperProtocolError("process is already launched or armed")
            remaining = child.deadline - time.monotonic()
            if arm_margin_s > remaining:
                raise HelperProtocolError("scheduled process start exceeds the hard deadline")
            observed_utc = datetime.now(UTC)
            scheduled_utc = observed_utc + timedelta(seconds=arm_margin_s)
            scheduled_text = scheduled_utc.isoformat().replace("+00:00", "Z")
            child.scheduled_start_utc = scheduled_text
            child.scheduled_monotonic = time.monotonic() + arm_margin_s
            threading.Thread(
                target=self._wait_and_launch,
                args=(child,),
                name=f"wspq-scheduled-{handle}",
                daemon=True,
            ).start()
        return {
            "handle_id": handle,
            "child_identity": "armed",
            "cleanup_verified": False,
            "scheduled_start_utc": scheduled_text,
            "helper_observed_utc": observed_utc.isoformat().replace("+00:00", "Z"),
            "arm_margin_s": arm_margin_s,
        }

    def _wait_and_launch(self, child: OwnedChild) -> None:
        assert child.scheduled_monotonic is not None
        with child.state_changed:
            while True:
                remaining = child.scheduled_monotonic - time.monotonic()
                if child.cancelled_before_launch or self._closed.is_set():
                    child.state_changed.notify_all()
                    return
                if remaining <= 0:
                    break
                child.state_changed.wait(timeout=remaining)
            if child.cancelled_before_launch or self._closed.is_set():
                child.state_changed.notify_all()
                return
            self._launch(child)
            child.state_changed.notify_all()

    @staticmethod
    def _launch(child: OwnedChild) -> None:
        actual = datetime.now(UTC)
        try:
            child.process = subprocess.Popen(
                child.launch_arguments,
                shell=False,
                env=child.environment,
                cwd=child.working_directory,
                stdout=child.stdout,
                stderr=child.stderr,
            )
            child.actual_start_utc = actual.isoformat().replace("+00:00", "Z")
            if child.scheduled_start_utc is not None:
                assert child.scheduled_monotonic is not None
                child.schedule_error_ms = (time.monotonic() - child.scheduled_monotonic) * 1000.0
        except Exception as exc:
            child.launch_error = f"{type(exc).__name__}: {exc}"

    def wait(self, payload: dict[str, object]) -> dict[str, object]:
        handle = _string(payload, "handle_id")
        requested = _positive_number(payload, "timeout_s")
        child = self._owned(handle)
        requested_deadline = time.monotonic() + requested
        remaining = max(0.0, min(child.deadline, requested_deadline) - time.monotonic())
        timed_out = child.deadline_enforced or time.monotonic() >= child.deadline
        with child.state_changed:
            while (
                child.process is None
                and not child.cancelled_before_launch
                and child.launch_error is None
                and not timed_out
            ):
                child.state_changed.wait(timeout=min(requested, remaining))
                remaining = max(0.0, min(child.deadline, requested_deadline) - time.monotonic())
                timed_out = child.deadline_enforced or time.monotonic() >= child.deadline
                if child.process is None and remaining <= 0:
                    timed_out = True
            if child.process is None:
                if timed_out:
                    self._terminate(child)
                return self._finish(handle, child, timed_out=timed_out)
        assert child.process is not None
        try:
            child.process.wait(timeout=min(requested, remaining))
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate(child)
        else:
            # The watchdog may enforce the same monotonic deadline and reap the
            # child before this waiter wakes. Preserve that authoritative
            # outcome instead of misclassifying the terminated process as a
            # normal exit based on thread scheduling order.
            timed_out = timed_out or child.deadline_enforced
        return self._finish(handle, child, timed_out=timed_out)

    def stop(self, payload: dict[str, object]) -> dict[str, object]:
        handle = _string(payload, "handle_id")
        child = self._owned(handle)
        running_before_stop = child.process is not None and child.process.poll() is None
        self._terminate(child)
        result = self._finish(handle, child, cancelled=True)
        result["stop_requested"] = True
        result["running_before_stop"] = running_before_stop
        return result

    def shutdown(self) -> None:
        self._closed.set()
        with self._lock:
            handles = tuple(self._children)
        for handle in handles:
            child = self._owned(handle)
            self._terminate(child)
            self._finish(handle, child, cancelled=True)
        self._watchdog.join()

    def _owned(self, handle: str) -> OwnedChild:
        try:
            with self._lock:
                return self._children[handle]
        except KeyError as exc:
            raise HelperProtocolError("unknown or already-finalized process handle") from exc

    @staticmethod
    def _terminate(child: OwnedChild) -> None:
        with child.state_changed:
            if child.process is None:
                child.cancelled_before_launch = True
                child.state_changed.notify_all()
                return
        if child.process.poll() is None:
            deadline = time.monotonic() + child.cleanup_timeout_s
            stage_budget = child.cleanup_timeout_s / 2
            child.process.terminate()
            try:
                child.process.wait(timeout=min(stage_budget, max(0.0, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                child.process.kill()
                try:
                    child.process.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired as exc:
                    raise HelperProtocolError("owned child could not be stopped") from exc

    def _finish(
        self, handle: str, child: OwnedChild, *, timed_out: bool = False, cancelled: bool = False
    ) -> dict[str, object]:
        child.stdout.seek(0)
        child.stderr.seek(0)
        stdout = child.stdout.read().decode("utf-8", errors="replace")
        stderr = child.stderr.read().decode("utf-8", errors="replace")
        child.stdout.close()
        child.stderr.close()
        with self._lock:
            self._children.pop(handle, None)
        repository_integrity: list[dict[str, object]] = []
        if child.repository_guard is not None:
            guard = child.repository_guard
            for before, root in zip(guard.snapshots, guard.roots, strict=True):
                integrity = compare_repository_snapshot(
                    before,
                    root,
                    git_executable=guard.git,
                    timeout_s=guard.inspection_timeout_s,
                )
                integrity_document = integrity.document()
                validate_document(integrity_document, "repository-integrity.schema.json")
                repository_integrity.append(integrity_document)
        integrity_clean = all(item["outcome"] == "unchanged" for item in repository_integrity)
        result: dict[str, object] = {
            "handle_id": handle,
            "return_code": child.process.returncode if child.process is not None else None,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "disconnected": False,
            "cleanup_verified": (child.process is None or child.process.poll() is not None)
            and integrity_clean,
            "scheduled_start_utc": child.scheduled_start_utc,
            "actual_start_utc": child.actual_start_utc,
            "schedule_error_ms": child.schedule_error_ms,
            "launch_error": child.launch_error,
        }
        if child.repository_guard is not None:
            result["repository_integrity"] = repository_integrity
        return result

    @staticmethod
    def _prepare_repository_guard(
        raw_guard: object,
        arguments: list[str],
        pinned_arguments: dict[object, object],
        *,
        require_pinned_runtime: bool = True,
    ) -> tuple[Path | None, RepositoryGuardState | None]:
        if raw_guard is None:
            return None, None
        if not isinstance(raw_guard, dict):
            raise HelperProtocolError("repository guard must be an object")
        try:
            git = Path(_string(raw_guard, "git_path"))
            if not git.is_absolute() or not git.is_file():
                raise RepositoryProtectionError("Git executable is unavailable")
            if _sha256(git) != _string(raw_guard, "git_sha256"):
                raise RepositoryProtectionError("Git executable identity changed")
            root_values = raw_guard.get("protected_source_roots")
            mutable_values = raw_guard.get("mutable_inputs")
            writable_values = raw_guard.get("writable_paths")
            inspection_timeout_s = raw_guard.get("inspection_timeout_s")
            if (
                not isinstance(root_values, list)
                or not root_values
                or not all(isinstance(value, str) for value in root_values)
                or not isinstance(mutable_values, list)
                or not isinstance(writable_values, list)
                or not all(isinstance(value, str) for value in writable_values)
                or not isinstance(inspection_timeout_s, (int, float))
                or isinstance(inspection_timeout_s, bool)
                or not math.isfinite(float(inspection_timeout_s))
                or float(inspection_timeout_s) <= 0
            ):
                raise RepositoryProtectionError("repository guard fields are incomplete")
            roots = discover_protected_roots(
                [Path(value) for value in root_values],
                git_executable=git,
                timeout_s=float(inspection_timeout_s),
            )
            if not {str(Path(value).resolve(strict=True)) for value in root_values}.issubset(
                {str(root.path) for root in roots}
            ):
                raise RepositoryProtectionError("protected source discovery is ambiguous")
            runtime_bindings: list[RuntimeArtifactBinding] = []
            for raw in mutable_values:
                if not isinstance(raw, dict):
                    raise RepositoryProtectionError("mutable runtime binding is invalid")
                source_path = Path(_string(raw, "source_path"))
                runtime_path = Path(_string(raw, "runtime_path"))
                if any(
                    source_path.resolve(strict=True).is_relative_to(root.path) for root in roots
                ):
                    source = bind_source(source_path, roots)
                else:
                    if source_path.is_symlink() or not source_path.is_file():
                        raise RepositoryProtectionError("external source input is unsafe")
                    metadata = source_path.stat()
                    source = SourceArtifactBinding(
                        source_path.parent.resolve(strict=True),
                        source_path.name,
                        source_path.resolve(strict=True),
                        metadata.st_size,
                        stat.S_IMODE(metadata.st_mode),
                        _sha256(source_path),
                    )
                if source.sha256 != _string(raw, "source_sha256"):
                    raise RepositoryProtectionError("protected source identity changed")
                runtime_digest = _string(raw, "runtime_sha256")
                if (
                    not runtime_path.is_absolute()
                    or not runtime_path.is_file()
                    or runtime_path.is_symlink()
                    or _sha256(runtime_path) != runtime_digest
                    or (
                        require_pinned_runtime
                        and pinned_arguments.get(str(runtime_path)) != runtime_digest
                    )
                ):
                    raise RepositoryProtectionError("staged mutable input identity changed")
                metadata = runtime_path.stat()
                runtime_bindings.append(
                    RuntimeArtifactBinding(
                        runtime_path.resolve(strict=True),
                        runtime_path.parent.resolve(strict=True),
                        "process-start",
                        metadata.st_size,
                        stat.S_IMODE(metadata.st_mode),
                        runtime_digest,
                        source,
                    )
                )
            working_directory = Path(_string(raw_guard, "working_directory"))
            validate_process_boundary(
                arguments=(
                    arguments
                    if require_pinned_runtime
                    else [*arguments, *(str(binding.path) for binding in runtime_bindings)]
                ),
                working_directory=working_directory,
                mutable_inputs=runtime_bindings,
                writable_paths=[Path(value) for value in writable_values],
                roots=roots,
            )
            snapshots = tuple(
                capture_repository_snapshot(
                    root, git_executable=git, timeout_s=float(inspection_timeout_s)
                )
                for root in roots
            )
            return working_directory.resolve(strict=True), RepositoryGuardState(
                git, roots, snapshots, float(inspection_timeout_s)
            )
        except (OSError, RepositoryProtectionError) as error:
            raise HelperProtocolError(
                f"repository guard rejected process start: {error}"
            ) from error

    def _enforce_deadlines(self) -> None:
        while not self._closed.wait(0.01):
            now = time.monotonic()
            with self._lock:
                children = tuple(self._children.values())
            for child in children:
                running = child.process is None or child.process.poll() is None
                if running and now >= child.deadline:
                    child.deadline_enforced = True
                    self._terminate(child)


class CapabilityHelperServer:
    def __init__(
        self,
        helper_identity: str,
        plan_sha256: str,
        allowed_services: frozenset[str] = frozenset(),
        processes: OwnedProcessRegistry | None = None,
        services: ServiceBackend | None = None,
        gpio: GpioBackend | None = None,
        si5351: Si5351Backend | None = None,
        bounded_tone: BoundedToneBackend | None = None,
    ) -> None:
        self.identity, self.plan_sha256 = helper_identity, plan_sha256
        self.allowed_services = allowed_services
        self.processes = processes or OwnedProcessRegistry()
        self.services = services or cast(ServiceBackend, UnsupportedBackend())
        self.gpio = gpio or cast(GpioBackend, UnsupportedBackend())
        self.si5351 = si5351 or cast(Si5351Backend, UnsupportedBackend())
        self.bounded_tone = bounded_tone or cast(BoundedToneBackend, UnsupportedBackend())

    def dispatch(self, request: dict[str, object]) -> dict[str, object]:
        try:
            validate_document(request, "helper-request.schema.json")
        except ValueError as exc:
            raise HelperProtocolError(f"helper request is invalid: {exc}") from exc
        _validate_envelope(request)
        operation = cast(str, request["operation"])
        if request["plan_sha256"] != self.plan_sha256:
            raise HelperProtocolError("request plan digest does not match configured plan")
        payload = cast(dict[str, object], request["payload"])
        if operation == "process-start":
            result = self.processes.start(payload)
        elif operation == "process-prepare":
            result = self.processes.start(payload, deferred=True)
        elif operation == "process-arm":
            result = self.processes.arm(payload)
        elif operation == "process-wait":
            result = self.processes.wait(payload)
        elif operation == "process-stop":
            result = self.processes.stop(payload)
        elif operation.startswith("service-"):
            name, manager = _string(payload, "name"), _string(payload, "manager")
            if name not in self.allowed_services:
                raise HelperProtocolError("service is not in the resolved allowlist")
            if operation == "service-inspect":
                running = self.services.inspect(name, manager)
            else:
                _working_directory, repository_guard = self.processes._prepare_repository_guard(
                    payload.get("repository_guard"),
                    ["/service-action"],
                    {},
                    require_pinned_runtime=False,
                )
                running = self.services.set_running(name, manager, _boolean(payload, "running"))
            result = {"name": name, "manager": manager, "running": running}
            if operation == "service-set" and repository_guard is not None:
                integrity_documents = []
                for before, root in zip(
                    repository_guard.snapshots, repository_guard.roots, strict=True
                ):
                    integrity = compare_repository_snapshot(
                        before,
                        root,
                        git_executable=repository_guard.git,
                        timeout_s=repository_guard.inspection_timeout_s,
                    ).document()
                    validate_document(integrity, "repository-integrity.schema.json")
                    integrity_documents.append(integrity)
                result["repository_integrity"] = integrity_documents
                result["cleanup_verified"] = all(
                    item["outcome"] == "unchanged" for item in integrity_documents
                )
        elif operation == "gpio-inspect":
            result = self.gpio.inspect(_integer(payload, "pin"))
        elif operation == "si5351-inspect":
            result = self.si5351.inspect(_integer(payload, "bus"), _string(payload, "address"))
        else:
            result = self.bounded_tone.run(
                cast(str, request["request_id"]),
                _integer(payload, "frequency_hz"),
                _integer(payload, "duration_ms"),
                _positive_number(payload, "outer_timeout_s"),
            )
        result_schemas = {
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
        }
        validate_document(result, result_schemas[operation])
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request["request_id"],
            "operation": operation,
            "plan_sha256": self.plan_sha256,
            "helper_identity": self.identity,
            "outcome": "completed",
            "result": result,
        }
        validate_document(response, "helper-response.schema.json")
        return response


def _validate_envelope(request: dict[str, object]) -> None:
    expected = {"protocol_version", "request_id", "operation", "plan_sha256", "payload"}
    if set(request) != expected:
        raise HelperProtocolError("helper request fields are incomplete or unexpected")
    if request["protocol_version"] != PROTOCOL_VERSION:
        raise HelperProtocolError("unsupported helper protocol version")
    if not isinstance(request["request_id"], str) or not request["request_id"]:
        raise HelperProtocolError("request ID is required")
    if request["operation"] not in OPERATIONS:
        raise HelperProtocolError("unknown helper operation")
    if not isinstance(request["plan_sha256"], str) or len(request["plan_sha256"]) != 64:
        raise HelperProtocolError("plan digest is invalid")
    if not isinstance(request["payload"], dict) or not _finite(request["payload"]):
        raise HelperProtocolError("helper payload is invalid")
    payload = cast(dict[str, object], request["payload"])
    fields = {
        "process-start": {
            "arguments",
            "executable_sha256",
            "privilege_wrapper_path",
            "privilege_wrapper_sha256",
            "pinned_arguments",
            "hard_timeout_s",
            "cleanup_timeout_s",
            "environment",
            "repository_guard",
        },
        "process-prepare": {
            "arguments",
            "executable_sha256",
            "privilege_wrapper_path",
            "privilege_wrapper_sha256",
            "pinned_arguments",
            "hard_timeout_s",
            "cleanup_timeout_s",
            "environment",
            "repository_guard",
        },
        "process-arm": {"handle_id", "schedule_after_arm_s", "minimum_arm_margin_s"},
        "process-wait": {"handle_id", "timeout_s"},
        "process-stop": {"handle_id"},
        "service-inspect": {"name", "manager"},
        "service-set": {"name", "manager", "running", "repository_guard"},
        "gpio-inspect": {"pin"},
        "si5351-inspect": {"bus", "address"},
        "bounded-tone": {"frequency_hz", "duration_ms", "outer_timeout_s"},
    }
    operation = request["operation"]
    assert isinstance(operation, str)
    permitted = fields[operation]
    if operation in {"process-start", "process-prepare"}:
        absolute_scheduled_fields = {"scheduled_start_utc", "minimum_arm_margin_s"}
        relative_scheduled_fields = {"schedule_after_arm_s", "minimum_arm_margin_s"}
        base_fields = permitted - {"repository_guard"}
        if operation == "process-prepare":
            valid_fields = frozenset(payload) in {frozenset(base_fields), frozenset(permitted)}
        else:
            valid_fields = frozenset(payload) in {
                frozenset(base_fields),
                frozenset(permitted),
                frozenset(base_fields | absolute_scheduled_fields),
                frozenset(permitted | absolute_scheduled_fields),
                frozenset(base_fields | relative_scheduled_fields),
                frozenset(permitted | relative_scheduled_fields),
            }
    elif operation == "service-set":
        base_fields = permitted - {"repository_guard"}
        valid_fields = frozenset(payload) in {frozenset(base_fields), frozenset(permitted)}
    else:
        valid_fields = set(payload) == permitted
    if not valid_fields:
        raise HelperProtocolError("operation payload fields are incomplete or unexpected")


def encode_request(request: dict[str, object]) -> str:
    raw = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_request(value: str) -> dict[str, object]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        result = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HelperProtocolError("encoded helper request is invalid") from exc
    if not isinstance(result, dict):
        raise HelperProtocolError("helper request root must be an object")
    return cast(dict[str, object], result)


def load_server_config(
    path: Path, runtime_plan_sha256: str | None = None
) -> CapabilityHelperServer:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HelperProtocolError("helper configuration cannot be loaded") from exc
    required = {"protocol_version", "helper_identity", "allowed_services"}
    optional = {
        "plan_sha256",
        "systemctl_path",
        "systemctl_sha256",
        "service_privilege_wrapper_path",
        "service_privilege_wrapper_sha256",
        "process_privilege_wrapper_path",
        "process_privilege_wrapper_sha256",
        "service_timeout_s",
        "gpio_helper_path",
        "gpio_helper_sha256",
        "si5351_helper_path",
        "si5351_helper_sha256",
        "inspection_timeout_s",
        "bounded_tone_endpoint",
        "wsprrypi_revision",
    }
    if not isinstance(document, dict) or not required <= set(document) <= required | optional:
        raise HelperProtocolError("helper configuration fields are invalid")
    validate_document(document, "helper-config.schema.json")
    if document["protocol_version"] != PROTOCOL_VERSION:
        raise HelperProtocolError("helper configuration protocol version is unsupported")
    identity = document["helper_identity"]
    configured_digest = document.get("plan_sha256")
    if runtime_plan_sha256 is not None and configured_digest is not None:
        raise HelperProtocolError(
            "runtime-bound helper configuration must not contain a plan digest"
        )
    digest = runtime_plan_sha256 or configured_digest
    services = document["allowed_services"]
    if (
        not isinstance(identity, str)
        or not identity
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or not isinstance(services, list)
        or not all(isinstance(item, str) and item for item in services)
    ):
        raise HelperProtocolError("helper configuration values are invalid")
    allowed = frozenset(cast(list[str], services))
    process_wrapper = (
        Path(cast(str, document["process_privilege_wrapper_path"]))
        if "process_privilege_wrapper_path" in document
        else None
    )
    process_registry = OwnedProcessRegistry(
        process_wrapper,
        cast(str | None, document.get("process_privilege_wrapper_sha256")),
    )
    service_backend: ServiceBackend | None = None
    if "systemctl_path" in document:
        if "systemctl_sha256" not in document:
            raise HelperProtocolError("systemctl hash is required with its path")
        service_backend = SystemctlServiceBackend(
            Path(cast(str, document["systemctl_path"])),
            cast(str, document["systemctl_sha256"]),
            allowed,
            float(document.get("service_timeout_s", 5.0)),
            (
                Path(cast(str, document["service_privilege_wrapper_path"]))
                if "service_privilege_wrapper_path" in document
                else None
            ),
            cast(str | None, document.get("service_privilege_wrapper_sha256")),
        )
    inspection_timeout = float(document.get("inspection_timeout_s", 5.0))
    gpio_backend: GpioBackend | None = None
    if "gpio_helper_path" in document:
        if "gpio_helper_sha256" not in document:
            raise HelperProtocolError("GPIO helper hash is required with its path")
        gpio_backend = CommandGpioBackend(
            JsonInspectionBackend(
                Path(cast(str, document["gpio_helper_path"])),
                cast(str, document["gpio_helper_sha256"]),
                "gpio-inspect",
                inspection_timeout,
            )
        )
    si5351_backend: Si5351Backend | None = None
    if "si5351_helper_path" in document:
        if "si5351_helper_sha256" not in document:
            raise HelperProtocolError("Si5351 helper hash is required with its path")
        si5351_backend = CommandSi5351Backend(
            JsonInspectionBackend(
                Path(cast(str, document["si5351_helper_path"])),
                cast(str, document["si5351_helper_sha256"]),
                "si5351-inspect",
                inspection_timeout,
            )
        )
    bounded_tone_backend: BoundedToneBackend | None = None
    if "bounded_tone_endpoint" in document or "wsprrypi_revision" in document:
        if "bounded_tone_endpoint" not in document or "wsprrypi_revision" not in document:
            raise HelperProtocolError("bounded Tone endpoint and WsprryPi revision are inseparable")
        endpoint = cast(dict[str, object], document["bounded_tone_endpoint"])
        bounded_tone_backend = LoopbackBoundedToneBackend(
            BoundedToneEndpoint(
                cast(str, endpoint["host"]),
                cast(int, endpoint["port"]),
                cast(str, endpoint["path"]),
                cast(int, endpoint["maximum_frame_bytes"]),
            ),
            cast(str, document["wsprrypi_revision"]),
        )
    return CapabilityHelperServer(
        identity,
        digest,
        allowed,
        processes=process_registry,
        services=service_backend,
        gpio=gpio_backend,
        si5351=si5351_backend,
        bounded_tone=bounded_tone_backend,
    )


def serve(server: CapabilityHelperServer) -> int:
    try:
        for line in sys.stdin:
            try:
                response = server.dispatch(decode_request(line.strip()))
                print(json.dumps(response, sort_keys=True), flush=True)
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "protocol_version": PROTOCOL_VERSION,
                            "outcome": "rejected",
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    finally:
        server.processes.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--plan-sha256", help="runtime authorized plan digest; never write it into this config"
    )
    parser.add_argument("--helper-sha256", help="expected SHA-256 of this helper executable")
    parser.add_argument("--config-sha256", help="expected SHA-256 of the immutable configuration")
    options = parser.parse_args(argv)
    runtime_identity = (options.helper_sha256, options.config_sha256)
    if options.plan_sha256 is not None:
        if any(value is None for value in runtime_identity):
            parser.error("runtime plan binding requires helper and configuration SHA-256")
        executable = Path(sys.argv[0]).resolve()
        if not executable.is_file() or _sha256(executable) != options.helper_sha256:
            raise HelperProtocolError("helper executable SHA-256 does not match runtime binding")
        if not options.config.is_file() or _sha256(options.config) != options.config_sha256:
            raise HelperProtocolError("helper configuration SHA-256 does not match runtime binding")
    elif any(value is not None for value in runtime_identity):
        parser.error("helper identity bindings require a runtime plan digest")
    return serve(load_server_config(options.config, options.plan_sha256))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(child) for child in value.values())
    if isinstance(value, list):
        return all(_finite(child) for child in value)
    return True


def _string(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise HelperProtocolError(f"{name} must be nonempty text")
    return value


def _integer(payload: dict[str, object], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HelperProtocolError(f"{name} must be a nonnegative integer")
    return value


def _boolean(payload: dict[str, object], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise HelperProtocolError(f"{name} must be boolean")
    return value


def _positive_number(payload: dict[str, object], name: str) -> float:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise HelperProtocolError(f"{name} must be positive")
    return float(value)


def _string_list(payload: dict[str, object], name: str) -> list[str]:
    value = payload.get(name)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and "\x00" not in item for item in value)
    ):
        raise HelperProtocolError(f"{name} must be a nonempty string array")
    return cast(list[str], value)


def _environment(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise HelperProtocolError("environment must be an object")
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP"}
    result: dict[str, str] = {}
    for key, item in value.items():
        if key not in allowed or not isinstance(item, str) or "\x00" in item:
            raise HelperProtocolError("environment contains a forbidden entry")
        result[key] = item
    return result or {key: os.environ[key] for key in allowed if key in os.environ}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
