"""Portable local transport and sealed in-process fake SSH."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Protocol, cast

from jsonschema import Draft202012Validator, FormatChecker

from wsprrypi_qualification.adapters import OperationOutcome, OperationResult


class TransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandPlan:
    executable: Path
    arguments: tuple[str, ...] = ()
    working_directory: Path | None = None
    timeout_s: float = 60.0
    environment: Mapping[str, str] | None = None

    def validate(self) -> None:
        if (
            self.timeout_s <= 0
            or not self.executable.is_absolute()
            or not self.executable.is_file()
        ):
            raise TransportError("invalid local command plan")
        if self.working_directory is not None and not self.working_directory.is_dir():
            raise TransportError("command working directory does not exist")
        if any("\x00" in item for item in self.arguments):
            raise TransportError("command arguments may not contain NUL")


@dataclass(frozen=True)
class ExecutionRecord:
    transport: str
    executable: str
    executable_sha256: str
    arguments: tuple[str, ...]
    working_directory: str | None
    environment_keys: tuple[str, ...]
    started_utc: str
    completed_utc: str
    timeout_s: float
    stdout: str
    stderr: str
    return_code: int | None
    timed_out: bool
    cancelled: bool
    disconnected: bool
    cleanup_verified: bool
    child_identity: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["arguments"] = list(self.arguments)
        value["environment_keys"] = list(self.environment_keys)
        return value


class CommandTransport(Protocol):
    def execute(
        self, plan: CommandPlan, *, cancellation: threading.Event | None = None
    ) -> ExecutionRecord: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitized_environment(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP")
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    for key, value in (overrides or {}).items():
        if not key or "=" in key or "\x00" in key or "\x00" in value:
            raise TransportError("invalid environment override")
        result[key] = value
    return result


class LocalProcessOperation:
    """Reviewed nonblocking child handle with regular-file output."""

    def __init__(self, plan: CommandPlan, component: str, action: str) -> None:
        plan.validate()
        self.component, self.action, self.transport = component, action, "local"
        self._stdout = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115
        self._stderr = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115
        self.process = subprocess.Popen(
            [str(plan.executable), *plan.arguments],
            cwd=str(plan.working_directory) if plan.working_directory else None,
            env=sanitized_environment(plan.environment),
            stdout=self._stdout,
            stderr=self._stderr,
            shell=False,
        )
        self.handle_id = f"local-pid-{self.process.pid}"
        self._result: OperationResult | None = None

    def poll(self) -> OperationResult | None:
        if self._result is not None:
            return self._result
        code = self.process.poll()
        if code is None:
            return None
        self._stdout.seek(0)
        self._stderr.seek(0)
        stdout = self._stdout.read().decode("utf-8", errors="replace")
        stderr = self._stderr.read().decode("utf-8", errors="replace")
        self._stdout.close()
        self._stderr.close()
        self._result = OperationResult(
            OperationOutcome.COMPLETED if code == 0 else OperationOutcome.NONZERO_EXIT,
            code,
            stdout=stdout,
            stderr=stderr,
        )
        return self._result

    def request_stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()

    def force_stop(self) -> None:
        if self.process.poll() is None:
            self.process.kill()

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def collect_result(self) -> OperationResult | None:
        return self.poll()

    def finalize_after_stop(
        self, outcome: OperationOutcome, detail: str, timeout_s: float = 0.25
    ) -> OperationResult:
        try:
            self.process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self.process.kill()
            try:
                self.process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired as error:
                raise TransportError("local child could not be finalized") from error
        self._stdout.seek(0)
        self._stderr.seek(0)
        stdout = self._stdout.read().decode("utf-8", errors="replace")
        stderr = self._stderr.read().decode("utf-8", errors="replace")
        self._stdout.close()
        self._stderr.close()
        self._result = OperationResult(
            outcome, self.process.returncode, detail, stdout=stdout, stderr=stderr
        )
        return self._result


class LocalCommandTransport:
    def execute(
        self, plan: CommandPlan, *, cancellation: threading.Event | None = None
    ) -> ExecutionRecord:
        plan.validate()
        environment = sanitized_environment(plan.environment)
        started = datetime.now(UTC)
        with tempfile.TemporaryFile(mode="w+b") as out, tempfile.TemporaryFile(mode="w+b") as err:
            process = subprocess.Popen(
                [str(plan.executable), *plan.arguments],
                cwd=str(plan.working_directory) if plan.working_directory else None,
                env=environment,
                stdout=out,
                stderr=err,
                shell=False,
            )
            deadline = time.monotonic() + plan.timeout_s
            timed_out = cancelled = False
            while process.poll() is None:
                if cancellation is not None and cancellation.is_set():
                    cancelled = True
                elif time.monotonic() >= deadline:
                    timed_out = True
                if timed_out or cancelled:
                    process.terminate()
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=min(0.25, plan.timeout_s))
                    if process.poll() is None:
                        process.kill()
                        with suppress(subprocess.TimeoutExpired):
                            process.wait(timeout=min(0.25, plan.timeout_s))
                    break
                time.sleep(min(0.01, plan.timeout_s))
            out.seek(0)
            err.seek(0)
            stdout = out.read().decode("utf-8", errors="replace")
            stderr = err.read().decode("utf-8", errors="replace")
        record = ExecutionRecord(
            "local",
            str(plan.executable.resolve()),
            _sha256(plan.executable),
            plan.arguments,
            str(plan.working_directory.resolve()) if plan.working_directory else None,
            tuple(sorted(environment)),
            started.isoformat().replace("+00:00", "Z"),
            datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            plan.timeout_s,
            stdout,
            stderr,
            process.returncode,
            timed_out,
            cancelled,
            False,
            process.poll() is not None,
            str(process.pid),
        )
        validate_execution_record(record.to_dict())
        return record


@dataclass(frozen=True)
class SshPlan:
    host: str
    remote_arguments: tuple[str, ...]
    timeout_s: float = 60.0


@dataclass(frozen=True)
class FakeSshRequest:
    destination_host: str
    intended_remote_arguments: tuple[str, ...]
    encoding_contract: str
    encoded_remote_command: str
    version_arguments: tuple[str, ...]
    timeout_s: float
    cancellation_requested: bool


@dataclass(frozen=True)
class FakeSshResponse:
    version_return_code: int | None = 0
    version_stdout: str = "fake-ssh 1"
    version_stderr: str = ""
    return_code: int | None = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    disconnected: bool = False
    cleanup_verified: bool = True
    handle_id: str = "fake-ssh-1"


class FakeSshExecutor(Protocol):
    def execute(self, request: FakeSshRequest) -> FakeSshResponse: ...


@dataclass(frozen=True)
class DeterministicFakeSshExecutor:
    response: FakeSshResponse = FakeSshResponse()

    def execute(self, request: FakeSshRequest) -> FakeSshResponse:
        if request.cancellation_requested:
            return FakeSshResponse(
                version_return_code=None,
                version_stdout="",
                return_code=None,
                cancelled=True,
                handle_id=self.response.handle_id,
            )
        return self.response


@dataclass(frozen=True)
class SshExecutionRecord:
    transport: str
    mock_only: bool
    destination_host: str
    version_arguments: tuple[str, ...]
    version_return_code: int | None
    version_stdout: str
    version_stderr: str
    intended_remote_arguments: tuple[str, ...]
    encoding_contract: str
    encoded_remote_command: str
    started_utc: str
    completed_utc: str
    timeout_s: float
    stdout: str
    stderr: str
    return_code: int | None
    timed_out: bool
    cancelled: bool
    disconnected: bool
    cleanup_verified: bool
    child_identity: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["version_arguments"] = list(self.version_arguments)
        value["intended_remote_arguments"] = list(self.intended_remote_arguments)
        return value


class SshCommandTransport:
    def __init__(self, executor: FakeSshExecutor) -> None:
        if type(executor) is not DeterministicFakeSshExecutor:
            raise TransportError("unreviewed fake SSH executor refused")
        self.executor = executor

    def execute(
        self, plan: SshPlan, *, cancellation: threading.Event | None = None
    ) -> SshExecutionRecord:
        if plan.timeout_s <= 0:
            raise TransportError("fake SSH timeout must be positive")
        remote = self.validate_remote_arguments(plan.remote_arguments)
        encoded = (
            "wspq-argv-v1:"
            + base64.urlsafe_b64encode(
                json.dumps(list(remote), ensure_ascii=False, separators=(",", ":")).encode()
            ).decode()
        )
        started = datetime.now(UTC)
        response = self.executor.execute(
            FakeSshRequest(
                plan.host,
                remote,
                "wspq-argv-v1",
                encoded,
                ("fake-ssh", "--version"),
                plan.timeout_s,
                cancellation is not None and cancellation.is_set(),
            )
        )
        record = SshExecutionRecord(
            "fake_ssh",
            True,
            plan.host,
            ("fake-ssh", "--version"),
            response.version_return_code,
            response.version_stdout,
            response.version_stderr,
            remote,
            "wspq-argv-v1",
            encoded,
            started.isoformat().replace("+00:00", "Z"),
            datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            plan.timeout_s,
            response.stdout,
            response.stderr,
            response.return_code,
            response.timed_out,
            response.cancelled,
            response.disconnected,
            response.cleanup_verified,
            response.handle_id,
        )
        validate_ssh_execution_record(record.to_dict())
        return record

    @staticmethod
    def decode_remote_command(command: str) -> tuple[str, ...]:
        prefix = "wspq-argv-v1:"
        if not command.startswith(prefix):
            raise TransportError("unsupported remote command encoding")
        try:
            value = json.loads(base64.urlsafe_b64decode(command[len(prefix) :]).decode())
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            raise TransportError("invalid remote command encoding") from error
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise TransportError("remote command is not an argument array")
        return tuple(value)

    @staticmethod
    def validate_remote_arguments(arguments: Sequence[str]) -> tuple[str, ...]:
        if not arguments or any(any(c in x for c in {"\x00", "\n", "\r"}) for x in arguments):
            raise TransportError("unsupported remote arguments")
        return tuple(arguments)


def _schema(document: dict[str, object], name: str) -> None:
    schema = json.loads(
        files("wsprrypi_qualification.schemas").joinpath(name).read_text(encoding="utf-8")
    )
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document)
    )
    if errors:
        raise TransportError(errors[0].message)


def _utc_pair(document: dict[str, object]) -> None:
    started = datetime.fromisoformat(cast(str, document["started_utc"]).replace("Z", "+00:00"))
    completed = datetime.fromisoformat(cast(str, document["completed_utc"]).replace("Z", "+00:00"))
    if (
        started.utcoffset() != UTC.utcoffset(started)
        or completed.utcoffset() != UTC.utcoffset(completed)
        or completed < started
    ):
        raise TransportError("transport UTC timestamps are invalid or reversed")


def validate_execution_record(document: dict[str, object]) -> None:
    _schema(document, "transport-execution.schema.json")
    _utc_pair(document)
    if document["transport"] != "local" or document["disconnected"] is not False:
        raise TransportError("local transport evidence has contradictory transport state")
    if document["timed_out"] and (document["cancelled"] or document["return_code"] == 0):
        raise TransportError("local timeout evidence is contradictory")
    if document["cancelled"] and document["return_code"] == 0:
        raise TransportError("local cancellation evidence is contradictory")
    if not document["timed_out"] and not document["cancelled"] and document["return_code"] is None:
        raise TransportError("completed transport lacks return code")
    if document["return_code"] == 0 and document["cleanup_verified"] is not True:
        raise TransportError("successful local transport lacks verified cleanup")


def validate_ssh_execution_record(document: dict[str, object]) -> None:
    _schema(document, "ssh-execution.schema.json")
    _utc_pair(document)
    if any(key in document for key in ("executable", "executable_sha256", "process_id")):
        raise TransportError("fake SSH evidence contains executable identity")
    encoded = cast(str, document["encoded_remote_command"])
    intended = tuple(cast(list[str], document["intended_remote_arguments"]))
    if SshCommandTransport.decode_remote_command(encoded) != intended:
        raise TransportError("fake SSH encoding contradicts intended arguments")
    if document["timed_out"] and (document["cancelled"] or document["return_code"] == 0):
        raise TransportError("fake SSH timeout evidence is contradictory")
    if document["cancelled"] and document["return_code"] == 0:
        raise TransportError("fake SSH cancellation evidence is contradictory")
    if (
        not document["timed_out"]
        and not document["cancelled"]
        and not document["disconnected"]
        and document["return_code"] is None
    ):
        raise TransportError("fake SSH test operation lacks a terminal outcome")
    if document["disconnected"] != (document["return_code"] == 255):
        raise TransportError("fake SSH disconnect state contradicts return code")
    if document["return_code"] == 0 and document["cleanup_verified"] is not True:
        raise TransportError("successful fake SSH lacks verified cleanup")
    if document["version_return_code"] is None and (
        document["version_stdout"] or document["return_code"] is not None
    ):
        raise TransportError("fake SSH execution contradicts unavailable version evidence")
    if document["version_return_code"] not in {None, 0} and document["return_code"] is not None:
        raise TransportError("fake SSH execution proceeded after version failure")
