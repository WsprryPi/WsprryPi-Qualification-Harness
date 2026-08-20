"""Versioned, fail-closed server for remote capability operations."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from wsprrypi_qualification.bounded_tone_control import (
    BoundedToneEndpoint,
    run_bounded_tone_transaction,
)
from wsprrypi_qualification.offline import validate_document

PROTOCOL_VERSION = 1
OPERATIONS = frozenset(
    {
        "process-start",
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
    ) -> None:
        if not executable.is_absolute() or not executable.is_file() or timeout_s <= 0:
            raise HelperProtocolError("systemctl provider is not safely configured")
        if _sha256(executable) != executable_sha256:
            raise HelperProtocolError("systemctl executable hash does not match configuration")
        self.executable, self.executable_sha256 = executable, executable_sha256
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
            [str(self.executable), action, "--", name],
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
        if manager != "systemd" or name not in self.allowed:
            raise HelperProtocolError("service is outside the configured provider scope")


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
class OwnedChild:
    process: subprocess.Popen[bytes]
    stdout: Any
    stderr: Any
    deadline: float
    deadline_enforced: bool = False


class OwnedProcessRegistry:
    """Own children by opaque handle and clean them with bounded escalation."""

    def __init__(self) -> None:
        self._children: dict[str, OwnedChild] = {}
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._watchdog = threading.Thread(target=self._enforce_deadlines, daemon=True)
        self._watchdog.start()

    def start(self, payload: dict[str, object]) -> dict[str, object]:
        arguments = _string_list(payload, "arguments")
        executable = Path(arguments[0])
        expected_hash = _string(payload, "executable_sha256")
        timeout = _positive_number(payload, "hard_timeout_s")
        if not executable.is_absolute() or not executable.is_file():
            raise HelperProtocolError("executable must be an existing absolute file")
        if _sha256(executable) != expected_hash:
            raise HelperProtocolError("executable SHA-256 does not match resolved plan")
        environment = _environment(payload.get("environment", {}))
        out = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115
        err = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115
        process = subprocess.Popen(
            arguments,
            shell=False,
            env=environment,
            stdout=out,
            stderr=err,
        )
        handle = f"owned-{process.pid}-{time.monotonic_ns()}"
        with self._lock:
            self._children[handle] = OwnedChild(process, out, err, time.monotonic() + timeout)
        return {"handle_id": handle, "child_identity": str(process.pid), "cleanup_verified": False}

    def wait(self, payload: dict[str, object]) -> dict[str, object]:
        handle = _string(payload, "handle_id")
        requested = _positive_number(payload, "timeout_s")
        child = self._owned(handle)
        remaining = max(0.0, child.deadline - time.monotonic())
        timed_out = child.deadline_enforced or time.monotonic() >= child.deadline
        try:
            child.process.wait(timeout=min(requested, remaining))
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate(child)
        return self._finish(handle, child, timed_out=timed_out)

    def stop(self, payload: dict[str, object]) -> dict[str, object]:
        handle = _string(payload, "handle_id")
        child = self._owned(handle)
        running_before_stop = child.process.poll() is None
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
        self._watchdog.join(timeout=0.2)

    def _owned(self, handle: str) -> OwnedChild:
        try:
            with self._lock:
                return self._children[handle]
        except KeyError as exc:
            raise HelperProtocolError("unknown or already-finalized process handle") from exc

    @staticmethod
    def _terminate(child: OwnedChild) -> None:
        if child.process.poll() is None:
            child.process.terminate()
            try:
                child.process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                child.process.kill()
                try:
                    child.process.wait(timeout=0.25)
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
        return {
            "handle_id": handle,
            "return_code": child.process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "disconnected": False,
            "cleanup_verified": child.process.poll() is not None,
        }

    def _enforce_deadlines(self) -> None:
        while not self._closed.wait(0.01):
            now = time.monotonic()
            with self._lock:
                children = tuple(self._children.values())
            for child in children:
                if child.process.poll() is None and now >= child.deadline:
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
                running = self.services.set_running(name, manager, _boolean(payload, "running"))
            result = {"name": name, "manager": manager, "running": running}
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
        "process-start": {"arguments", "executable_sha256", "hard_timeout_s", "environment"},
        "process-wait": {"handle_id", "timeout_s"},
        "process-stop": {"handle_id"},
        "service-inspect": {"name", "manager"},
        "service-set": {"name", "manager", "running"},
        "gpio-inspect": {"pin"},
        "si5351-inspect": {"bus", "address"},
        "bounded-tone": {"frequency_hz", "duration_ms", "outer_timeout_s"},
    }
    operation = request["operation"]
    assert isinstance(operation, str)
    if set(payload) != fields[operation]:
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


def load_server_config(path: Path) -> CapabilityHelperServer:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HelperProtocolError("helper configuration cannot be loaded") from exc
    required = {"protocol_version", "helper_identity", "plan_sha256", "allowed_services"}
    optional = {
        "systemctl_path",
        "systemctl_sha256",
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
    identity, digest = document["helper_identity"], document["plan_sha256"]
    services = document["allowed_services"]
    if (
        not isinstance(identity, str)
        or not identity
        or not isinstance(digest, str)
        or len(digest) != 64
        or not isinstance(services, list)
        or not all(isinstance(item, str) and item for item in services)
    ):
        raise HelperProtocolError("helper configuration values are invalid")
    allowed = frozenset(cast(list[str], services))
    service_backend: ServiceBackend | None = None
    if "systemctl_path" in document:
        if "systemctl_sha256" not in document:
            raise HelperProtocolError("systemctl hash is required with its path")
        service_backend = SystemctlServiceBackend(
            Path(cast(str, document["systemctl_path"])),
            cast(str, document["systemctl_sha256"]),
            allowed,
            float(document.get("service_timeout_s", 5.0)),
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
    options = parser.parse_args(argv)
    return serve(load_server_config(options.config))


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
