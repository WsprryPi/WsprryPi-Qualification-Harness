"""Durable, transport-neutral progress events for complete-test execution."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from wsprrypi_qualification.transports import CommandPlan, sanitized_environment

PROGRESS_PREFIX = "WSPQ_PROGRESS "


class ProgressError(RuntimeError):
    """A progress stream could not be created or safely updated."""


class ProgressReporter:
    """Append complete JSON objects, flushing every event for live tailing."""

    def __init__(self, path: Path | None, *, mirror: TextIO | None = None) -> None:
        self.path = path.resolve() if path is not None else None
        self._mirror = mirror
        self._sequence = 0
        self._lock = threading.Lock()
        self._file: TextIO | None = None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                self._file = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
            except OSError as error:
                raise ProgressError(f"progress log cannot be created: {self.path}") from error

    def emit(
        self,
        stage: str,
        status: str,
        detail: str,
        *,
        campaign_id: str | None = None,
        mode: str | None = None,
        item: int | None = None,
        item_count: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            event = {
                "schema_version": 1,
                "evidence_type": "complete_test_progress",
                "sequence": self._sequence,
                "timestamp_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "campaign_id": campaign_id,
                "mode": mode,
                "stage": stage,
                "status": status,
                "detail": detail,
                "item": item,
                "item_count": item_count,
            }
            line = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            if self._file is not None:
                try:
                    self._file.write(line + "\n")
                    self._file.flush()
                    os.fsync(self._file.fileno())
                except OSError:
                    self._file.close()
                    self._file = None
                    sys.stderr.write(
                        "progress log became unavailable; execution continues to cleanup\n"
                    )
                    sys.stderr.flush()
            if self._mirror is not None:
                try:
                    self._mirror.write(PROGRESS_PREFIX + line + "\n")
                    self._mirror.flush()
                except OSError:
                    self._mirror = None
            return event

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> ProgressReporter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def default_progress_path() -> Path:
    return Path(tempfile.gettempdir()) / (
        f"complete-test-progress-{os.getpid()}-{secrets.token_hex(4)}.jsonl"
    )


def stderr_reporter(path: Path | None, *, stream: bool) -> ProgressReporter:
    return ProgressReporter(path, mirror=sys.stderr if stream else None)


def run_streaming(
    plan: CommandPlan, reporter: ProgressReporter
) -> subprocess.CompletedProcess[str]:
    """Run a command while importing only authenticated progress lines from stderr."""
    plan.validate()
    process = subprocess.Popen(
        [str(plan.executable), *plan.arguments],
        cwd=str(plan.working_directory) if plan.working_directory else None,
        env=sanitized_environment(plan.environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        assert process.stdout is not None
        stdout_lines.extend(process.stdout.readlines())

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            if line.startswith(PROGRESS_PREFIX):
                try:
                    event = json.loads(line[len(PROGRESS_PREFIX) :])
                    if event.get("evidence_type") != "complete_test_progress":
                        raise ValueError
                    reporter.emit(
                        str(event["stage"]),
                        str(event["status"]),
                        str(event["detail"]),
                        campaign_id=event.get("campaign_id"),
                        mode=event.get("mode"),
                        item=event.get("item"),
                        item_count=event.get("item_count"),
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    stderr_lines.append(line)
            else:
                stderr_lines.append(line)

    readers = [threading.Thread(target=read_stdout), threading.Thread(target=read_stderr)]
    for reader in readers:
        reader.start()
    try:
        return_code = process.wait(timeout=plan.timeout_s)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return_code = process.returncode
        stderr_lines.append("command timed out\n")
    for reader in readers:
        reader.join()
    return subprocess.CompletedProcess(
        [str(plan.executable), *plan.arguments],
        return_code,
        "".join(stdout_lines),
        "".join(stderr_lines),
    )
