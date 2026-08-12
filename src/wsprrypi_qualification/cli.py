"""Portable command-line interface through Slice 5."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from wsprrypi_qualification import __version__
from wsprrypi_qualification.application_shims import (
    ApplicationPlanError,
    validate_application_plan,
)
from wsprrypi_qualification.audio import create_slot_wav_acquired
from wsprrypi_qualification.capabilities import capability_report
from wsprrypi_qualification.capture_metadata import CaptureMetadataError, load_capture_metadata
from wsprrypi_qualification.carrier import analyze_carrier_acquired
from wsprrypi_qualification.decoder import run_wsprd_acquired, summarize_decodes
from wsprrypi_qualification.offline import OfflineAnalysisError, write_offline_failure
from wsprrypi_qualification.profiles import ProfileError, load_profile
from wsprrypi_qualification.real_session import (
    RealSessionError,
    ResolvedRealSessionPlan,
)

LIVE_COMMANDS = {"run", "capture", "transmit", "tone", "enable-rf"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wsprrypi-qualification")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version", help="print the package version")
    subparsers.add_parser("capabilities", help="emit a read-only capability report")
    validate = subparsers.add_parser(
        "validate-profile", help="validate a bench, test, or runtime receiver-run profile"
    )
    validate.add_argument("kind", choices=("bench", "test", "receiver-run"))
    validate.add_argument("path", type=Path)
    capture_metadata = subparsers.add_parser(
        "validate-capture-metadata", help="validate exact-count capture metadata"
    )
    capture_metadata.add_argument("path", type=Path)
    application_plan = subparsers.add_parser(
        "validate-application-plan", help="validate a hardware-free application plan"
    )
    application_plan.add_argument("path", type=Path)
    real_session = subparsers.add_parser(
        "real-session", help="validate and display a real-session plan without executing it"
    )
    real_session.add_argument("plan", type=Path)
    real_session.add_argument("--plan-only", action="store_true", required=True)
    carrier = subparsers.add_parser("analyze-carrier", help="analyze offline RF-off/RF-on CF32")
    carrier.add_argument("rf_off", type=Path)
    carrier.add_argument("rf_on", type=Path)
    carrier.add_argument("evidence", type=Path)
    carrier.add_argument("--bench-profile", type=Path, required=True)
    carrier.add_argument("--test-profile", type=Path, required=True)
    carrier.add_argument("--fft-size", type=int, default=262_144)
    carrier.add_argument("--dc-exclusion-hz", type=float, default=1_000.0)
    carrier.add_argument("--rf-off-metadata", type=Path, required=True)
    carrier.add_argument("--rf-on-metadata", type=Path, required=True)
    audio = subparsers.add_parser("make-slot-wav", help="translate offline CF32 to a UTC-slot WAV")
    audio.add_argument("iq", type=Path)
    audio.add_argument("capture_metadata", type=Path)
    audio.add_argument("output_directory", type=Path)
    audio.add_argument("evidence", type=Path)
    audio.add_argument("--slot", required=True)
    audio.add_argument("--bench-profile", type=Path, required=True)
    audio.add_argument("--test-profile", type=Path, required=True)
    decode = subparsers.add_parser("decode-wspr", help="run wsprd on an offline WAV")
    decode.add_argument("wav", type=Path)
    decode.add_argument("audio_evidence", type=Path)
    decode.add_argument("evidence", type=Path)
    decode.add_argument("--wsprd", type=Path)
    decode.add_argument("--timeout", type=float, default=60.0)
    summary = subparsers.add_parser(
        "summarize-decodes", help="validate consecutive decoder evidence"
    )
    summary.add_argument("evidence", type=Path)
    summary.add_argument("slot_documents", nargs="+", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--enable-rf" in arguments or LIVE_COMMANDS.intersection(arguments):
        print("live RF and hardware actions are unavailable in the portable CLI", file=sys.stderr)
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
    if args.command == "validate-capture-metadata":
        try:
            metadata = load_capture_metadata(args.path)
        except CaptureMetadataError as error:
            print(str(error), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {"path": str(args.path), "capture": asdict(metadata), "valid": True},
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
        return 0
    if args.command == "validate-application-plan":
        try:
            document = json.loads(args.path.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ApplicationPlanError("application plan must be a JSON object")
            validate_application_plan(document)
        except (ApplicationPlanError, OSError, json.JSONDecodeError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps({"path": str(args.path), "valid": True}, indent=2, sort_keys=True))
        return 0
    if args.command == "real-session":
        try:
            document = json.loads(args.plan.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise RealSessionError("real-session plan must be a JSON object")
            plan = ResolvedRealSessionPlan(document)
            resolved = plan.validated()
        except (OSError, json.JSONDecodeError, RealSessionError, OfflineAnalysisError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "plan_only": True,
                    "external_calls": 0,
                    "resolved_plan_sha256": plan.sha256,
                    "resolved_plan": resolved,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    try:
        if args.command == "analyze-carrier":
            document = analyze_carrier_acquired(
                args.rf_off,
                args.rf_on,
                args.rf_off_metadata,
                args.rf_on_metadata,
                args.bench_profile,
                args.test_profile,
                args.evidence,
                fft_size=args.fft_size,
                dc_exclusion_hz=args.dc_exclusion_hz,
            )
        elif args.command == "make-slot-wav":
            document = create_slot_wav_acquired(
                args.iq,
                args.capture_metadata,
                args.bench_profile,
                args.test_profile,
                datetime.fromisoformat(args.slot.replace("Z", "+00:00")),
                args.output_directory,
                args.evidence,
            )
        elif args.command == "decode-wspr":
            document = run_wsprd_acquired(
                args.wav,
                args.audio_evidence,
                args.evidence,
                executable=args.wsprd,
                timeout_s=args.timeout,
            )
        elif args.command == "summarize-decodes":
            document = summarize_decodes(args.slot_documents, args.evidence)
        else:
            raise AssertionError("unreachable command")
    except (OfflineAnalysisError, OSError, ValueError) as error:
        evidence = getattr(args, "evidence", None)
        if isinstance(evidence, Path) and not evidence.exists():
            with suppress(OSError, OfflineAnalysisError):
                write_offline_failure(evidence, args.command, error)
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(document, indent=2, sort_keys=True, default=str))
    return 0
