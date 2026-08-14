"""Portable command-line interface through Slice 5."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
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
from wsprrypi_qualification.cw_qualification import CwQualificationError, load_cw_qualification
from wsprrypi_qualification.decoder import run_wsprd_acquired, summarize_decodes
from wsprrypi_qualification.deployment import DeploymentError, load_deployment_config
from wsprrypi_qualification.offline import OfflineAnalysisError, write_offline_failure
from wsprrypi_qualification.profiles import ProfileError, load_profile
from wsprrypi_qualification.real_session import (
    RealQualificationSession,
    RealRuntimeAuthorization,
    RealSessionError,
    ResolvedRealSessionPlan,
)
from wsprrypi_qualification.simulator import SimulationError, SimulatorPlan, run_simulation

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
    cw_evidence = subparsers.add_parser(
        "validate-cw-qualification", help="authenticate and validate offline CW evidence"
    )
    cw_evidence.add_argument("path", type=Path)
    deployment = subparsers.add_parser(
        "validate-helper-deployment", help="validate helper deployment configuration offline"
    )
    deployment.add_argument("path", type=Path)
    real_session = subparsers.add_parser(
        "real-session", help="validate and display a real-session plan without executing it"
    )
    real_session.add_argument("plan", type=Path)
    real_session.add_argument("--plan-only", action="store_true", required=True)
    live_session = subparsers.add_parser(
        "run-live-session", help="run the fail-closed split-host qualification lifecycle"
    )
    live_session.add_argument("plan", type=Path)
    live_session.add_argument("output_parent", type=Path)
    live_session.add_argument("--work-directory", type=Path, required=True)
    live_session.add_argument("--ssh", type=Path, required=True)
    live_session.add_argument("--operator", required=True)
    live_session.add_argument("--enable-live-session", action="store_true", required=True)
    live_session.add_argument("--enable-rf", action="store_true", required=True)
    simulator = subparsers.add_parser(
        "simulate-qualification", help="run the bounded hardware-free lifecycle simulator"
    )
    simulator.add_argument("output_parent", type=Path)
    simulator.add_argument("--run-id", required=True)
    simulator.add_argument(
        "--injection",
        choices=(
            "none",
            "carrier_fail",
            "cleanup_fail",
            "rf_off_timeout",
            "rf_off_nonzero",
            "carrier_timeout",
            "carrier_nonzero",
            "frame_timeout",
            "frame_nonzero",
            "carrier_analysis_hang",
            "wav_hang",
            "decoder_hang",
            "publication_hang",
        ),
        default="none",
    )
    simulator.add_argument("--child-timeout", type=float, default=1.0)
    simulator.add_argument("--overall-timeout", type=float, default=15.0)
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
    carrier.add_argument("--relocation-bundle", type=Path)
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
    if (
        LIVE_COMMANDS.intersection(arguments) or "--enable-rf" in arguments
    ) and "run-live-session" not in arguments:
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
    if args.command == "validate-cw-qualification":
        try:
            document = load_cw_qualification(args.path)
        except CwQualificationError as error:
            print(str(error), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "path": str(args.path),
                    "valid": True,
                    "mode": document["mode"],
                    "final_status": document["final_status"],
                    "qualification_claim": document["qualification_claim"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "validate-helper-deployment":
        try:
            document = load_deployment_config(args.path)
        except (DeploymentError, OfflineAnalysisError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {"path": str(args.path), "valid": True, "target_host": document["target_host"]},
                indent=2,
                sort_keys=True,
            )
        )
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
    if args.command == "run-live-session":
        try:
            document = json.loads(args.plan.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise RealSessionError("real-session plan must be a JSON object")
            plan = ResolvedRealSessionPlan(document)
            resolved = plan.validated()
            if resolved["execution_mode"] != "live":
                raise RealSessionError("live command requires a live resolved plan")
            print(json.dumps(resolved, indent=2, sort_keys=True))
            response = input(
                f"Type the resolved plan SHA-256 {plan.sha256} to authorize this run: "
            )
            confirmed_at = datetime.now(UTC)
            if response.strip() != plan.sha256:
                raise RealSessionError("runtime operator confirmation did not match the plan")
            from wsprrypi_qualification.live_adapters import build_production_adapters

            adapters = build_production_adapters(
                resolved,
                ssh_executable=args.ssh.resolve(),
                work_directory=args.work_directory.resolve(),
            )
            external = RealRuntimeAuthorization(
                "external_access", args.operator, confirmed_at, plan.sha256, True
            )
            rf = RealRuntimeAuthorization("rf", args.operator, confirmed_at, plan.sha256, True)
            result = RealQualificationSession(plan, adapters, now=confirmed_at).run(
                external, rf, args.output_parent
            )
        except (OSError, json.JSONDecodeError, RealSessionError, OfflineAnalysisError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["final_status"] == "qualified" else 1
    if args.command == "simulate-qualification":
        try:
            result = run_simulation(
                SimulatorPlan(
                    args.run_id,
                    args.output_parent,
                    child_timeout_s=args.child_timeout,
                    overall_timeout_s=args.overall_timeout,
                    injection=args.injection,
                )
            )
        except (SimulationError, OfflineAnalysisError, OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["session"]["final_status"] == "inconclusive" else 1
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
                relocation_bundle=args.relocation_bundle,
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
