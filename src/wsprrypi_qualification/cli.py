"""Safe Slice 1 command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from wsprrypi_qualification import __version__
from wsprrypi_qualification.capabilities import capability_report
from wsprrypi_qualification.profiles import ProfileError, load_profile

LIVE_COMMANDS = {"run", "capture", "transmit", "tone", "enable-rf"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wsprrypi-qualification")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version", help="print the package version")
    subparsers.add_parser("capabilities", help="emit a read-only capability report")
    validate = subparsers.add_parser("validate-profile", help="validate a bench or test profile")
    validate.add_argument("kind", choices=("bench", "test"))
    validate.add_argument("path", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--enable-rf" in arguments or LIVE_COMMANDS.intersection(arguments):
        print("live RF actions are unavailable in Slice 1", file=sys.stderr)
        return 2
    args = _parser().parse_args(arguments)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "capabilities":
        print(json.dumps(capability_report(), indent=2, sort_keys=True))
        return 0
    if args.command == "validate-profile":
        try:
            profile = load_profile(args.path, args.kind)
        except ProfileError as error:
            print(str(error), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "kind": args.kind,
                    "path": str(args.path),
                    "profile": asdict(profile),
                    "valid": True,
                },
                default=str,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    raise AssertionError("unreachable command")
