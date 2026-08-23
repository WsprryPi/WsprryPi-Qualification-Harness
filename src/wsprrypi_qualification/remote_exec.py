"""Minimal argv-preserving remote helper for the OpenSSH capability adapter."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path


def decode_arguments(encoded: str) -> tuple[str, ...]:
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid encoded argument vector") from exc
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and "\x00" not in item for item in value)
    ):
        raise ValueError("argument vector must be a nonempty string array")
    return tuple(value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--argv-base64")
    operation.add_argument("--identity", action="store_true")
    parser.add_argument("--timeout", type=float)
    options = parser.parse_args(argv)
    if options.identity:
        launcher = Path(sys.argv[0]).resolve()
        module = Path(__file__).resolve()

        def identity(path: Path) -> dict[str, object]:
            return {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }

        print(
            json.dumps(
                {
                    "launcher": identity(launcher),
                    "module": identity(module),
                },
                sort_keys=True,
            )
        )
        return 0
    if options.timeout is None or options.timeout <= 0:
        parser.error("--timeout must be positive")
    assert options.argv_base64 is not None
    arguments = decode_arguments(options.argv_base64)
    interrupted = threading.Event()

    def request_cleanup(signum: int, frame: object) -> None:
        del signum, frame
        interrupted.set()

    previous: dict[int, object] = {}
    for signal_name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, signal_name):
            number = int(getattr(signal, signal_name))
            previous[number] = signal.signal(number, request_cleanup)
    process = subprocess.Popen(arguments, shell=False)
    deadline = time.monotonic() + options.timeout
    timed_out = False
    try:
        while process.poll() is None:
            timed_out = time.monotonic() >= deadline
            if timed_out or interrupted.is_set():
                if hasattr(signal, "SIGINT"):
                    process.send_signal(signal.SIGINT)
                else:  # pragma: no cover - Windows always exposes SIGINT in supported Python
                    process.terminate()
                try:
                    process.wait(timeout=30.0)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5.0)
                break
            time.sleep(0.05)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)  # type: ignore[arg-type]
    if timed_out:
        return 124
    if interrupted.is_set():
        return 130
    return int(process.returncode)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
