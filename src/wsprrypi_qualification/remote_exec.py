"""Minimal argv-preserving remote helper for the OpenSSH capability adapter."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import subprocess
from collections.abc import Sequence


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
    parser.add_argument("--argv-base64", required=True)
    parser.add_argument("--timeout", type=float, required=True)
    options = parser.parse_args(argv)
    if options.timeout <= 0:
        parser.error("--timeout must be positive")
    arguments = decode_arguments(options.argv_base64)
    try:
        return subprocess.run(
            arguments, shell=False, check=False, timeout=options.timeout
        ).returncode
    except subprocess.TimeoutExpired:
        return 124


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
