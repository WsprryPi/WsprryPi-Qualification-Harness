"""Portable command-line interface for qualification-harness capabilities."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from wsprrypi_qualification import __version__, sdr_calibration
from wsprrypi_qualification.application_shims import (
    ApplicationPlanError,
    validate_application_plan,
)
from wsprrypi_qualification.archive_intake import (
    ArchiveIntakeError,
    inventory_archive,
    validate_multi_capture_session,
)
from wsprrypi_qualification.audio import create_slot_wav_acquired
from wsprrypi_qualification.capabilities import capability_report
from wsprrypi_qualification.capture_metadata import CaptureMetadataError, load_capture_metadata
from wsprrypi_qualification.carrier import analyze_carrier_acquired
from wsprrypi_qualification.cw_contracts import CwContractError, load_cw_contract_chain
from wsprrypi_qualification.cw_host_preflight import (
    CwHostPreflightError,
    run_cw_actual_host_preflight,
    validate_cw_actual_host_preflight_bundle,
)
from wsprrypi_qualification.cw_iq import CwIqError, analyze_synthetic_iq, generate_synthetic_iq
from wsprrypi_qualification.cw_lifecycle import (
    INJECTIONS,
    CwLifecycleError,
    run_mock_lifecycle,
    validate_mock_lifecycle,
)
from wsprrypi_qualification.cw_qualification import CwQualificationError, load_cw_qualification
from wsprrypi_qualification.cw_reference import ReferenceEncoderError, write_expected_events
from wsprrypi_qualification.cw_replay import (
    CwReplayError,
    compose_acquired_replay,
    validate_replay_bundle,
)
from wsprrypi_qualification.decoder import run_wsprd_acquired, summarize_decodes
from wsprrypi_qualification.deployment import DeploymentError, load_deployment_config
from wsprrypi_qualification.keyed_session_contracts import (
    KeyedSessionContractError,
    compose_keyed_runtime_authorization,
    resolved_keyed_plan_sha256,
    validate_resolved_keyed_plan,
)
from wsprrypi_qualification.live_keyed import LiveKeyedError, run_live_keyed_session
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
    sdr_calibration = subparsers.add_parser(
        "evaluate-sdr-calibration",
        help="validate and apply a frozen SDR Calibration Profile 1.0.0 offline",
    )
    sdr_calibration.add_argument("profile", type=Path)
    sdr_calibration.add_argument("request", type=Path)
    capture_metadata = subparsers.add_parser(
        "validate-capture-metadata", help="validate exact-count capture metadata"
    )
    capture_metadata.add_argument("path", type=Path)
    application_plan = subparsers.add_parser(
        "validate-application-plan", help="validate a hardware-free application plan"
    )
    application_plan.add_argument("path", type=Path)
    archive_inventory = subparsers.add_parser(
        "inventory-archive", help="authenticate a preserved offline archive manifest"
    )
    archive_inventory.add_argument("archive_root", type=Path)
    archive_inventory.add_argument("manifest", type=Path)
    archive_inventory.add_argument("output", type=Path)
    archive_inventory.add_argument("--archive-id", required=True)
    multi_capture = subparsers.add_parser(
        "validate-cw-multi-capture", help="validate a non-qualifying multi-capture session"
    )
    multi_capture.add_argument("path", type=Path)
    cw_evidence = subparsers.add_parser(
        "validate-cw-qualification", help="authenticate and validate offline CW evidence"
    )
    cw_evidence.add_argument("path", type=Path)
    cw_contract = subparsers.add_parser(
        "validate-cw-contract-chain",
        help="validate the tone/CW-family document chain",
    )
    cw_contract.add_argument("plan", type=Path)
    cw_contract.add_argument("expected_events", type=Path)
    cw_contract.add_argument("observations", type=Path)
    cw_contract.add_argument("mode_gate", type=Path)
    cw_contract.add_argument("session", type=Path)
    cw_reference = subparsers.add_parser(
        "generate-cw-expected-events",
        help="generate a reference timeline without hardware access",
    )
    cw_reference.add_argument("plan", type=Path)
    cw_reference.add_argument("output", type=Path)
    cw_reference.add_argument("--source-revision", required=True)
    cw_fixture = subparsers.add_parser(
        "generate-cw-synthetic-iq",
        help="generate deterministic synthetic CF32LE without hardware",
    )
    cw_fixture.add_argument("plan", type=Path)
    cw_fixture.add_argument("expected_events", type=Path)
    cw_fixture.add_argument("capture", type=Path)
    cw_fixture.add_argument("metadata", type=Path)
    cw_fixture.add_argument("--seed", type=int, required=True)
    cw_analyzer = subparsers.add_parser(
        "analyze-cw-synthetic-iq",
        help="analyze synthetic IQ; output can never qualify hardware",
    )
    cw_analyzer.add_argument("plan", type=Path)
    cw_analyzer.add_argument("expected_events", type=Path)
    cw_analyzer.add_argument("metadata", type=Path)
    cw_analyzer.add_argument("observations", type=Path)
    cw_analyzer.add_argument("mode_gate", type=Path)
    cw_analyzer.add_argument("--source-revision", required=True)
    cw_replay = subparsers.add_parser(
        "compose-cw-acquired-replay",
        help="compose an acquired-IQ replay bundle; never qualifies hardware",
    )
    cw_replay.add_argument("plan", type=Path)
    cw_replay.add_argument("expected_events", type=Path)
    cw_replay.add_argument("capture_metadata", type=Path)
    cw_replay.add_argument("output_directory", type=Path)
    cw_replay.add_argument("--source-revision", required=True)
    cw_replay_validate = subparsers.add_parser(
        "validate-cw-acquired-replay",
        help="authenticate and recompute a non-qualifying replay bundle",
    )
    cw_replay_validate.add_argument("bundle", type=Path)
    cw_lifecycle = subparsers.add_parser(
        "run-cw-mock-lifecycle",
        help="run a bounded mock-only lifecycle; never qualifies hardware",
    )
    cw_lifecycle.add_argument("plan", type=Path)
    cw_lifecycle.add_argument("expected_events", type=Path)
    cw_lifecycle.add_argument("observations", type=Path)
    cw_lifecycle.add_argument("mode_gate", type=Path)
    cw_lifecycle.add_argument("output", type=Path)
    cw_lifecycle.add_argument("--injection", choices=tuple(sorted(INJECTIONS)), default="none")
    cw_lifecycle_validate = subparsers.add_parser(
        "validate-cw-mock-lifecycle",
        help="authenticate mock-only lifecycle evidence",
    )
    cw_lifecycle_validate.add_argument("path", type=Path)
    host_preflight = subparsers.add_parser(
        "run-cw-actual-host-preflight",
        help="run the read-only actual-host preflight; never transmits or qualifies",
    )
    host_preflight.add_argument("plan", type=Path)
    host_preflight.add_argument("output_parent", type=Path)
    host_preflight.add_argument("--ssh", type=Path, required=True)
    host_preflight.add_argument("--confirm-plan-sha256", required=True)
    host_preflight.add_argument(
        "--enable-read-only-host-preflight", action="store_true", required=True
    )
    host_preflight_validate = subparsers.add_parser(
        "validate-cw-actual-host-preflight",
        help="authenticate a read-only host-preflight bundle",
    )
    host_preflight_validate.add_argument("bundle", type=Path)
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
    live_tone = subparsers.add_parser(
        "run-cw-live-tone", help="run the digest-bound carrier-only live-tone lifecycle"
    )
    live_tone.add_argument("plan", type=Path)
    live_tone.add_argument("output_parent", type=Path)
    live_tone.add_argument("--work-directory", type=Path, required=True)
    live_tone.add_argument("--ssh", type=Path, required=True)
    live_tone.add_argument("--operator", required=True)
    live_tone.add_argument("--enable-live-tone", action="store_true", required=True)
    live_tone.add_argument("--enable-rf", action="store_true", required=True)
    live_keyed = subparsers.add_parser(
        "run-cw-live-keyed",
        help="run the digest-bound three-transaction QRSS/FSKCW/DFCW lifecycle",
    )
    live_keyed.add_argument("plan", type=Path)
    live_keyed.add_argument("output_parent", type=Path)
    live_keyed.add_argument("--work-directory", type=Path, required=True)
    live_keyed.add_argument("--ssh", type=Path, required=True)
    live_keyed.add_argument("--operator", required=True)
    live_keyed.add_argument(
        "--confirm-plan-sha256",
        required=True,
        help=(
            "exact canonical digest of the resolved plan, supplied separately "
            "from helper configuration"
        ),
    )
    live_keyed.add_argument("--enable-live-keyed", action="store_true", required=True)
    live_keyed.add_argument("--enable-rf", action="store_true", required=True)
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
    carrier.add_argument(
        "--plot",
        type=Path,
        help="write an authenticated relative-spectrum plot (.png or .svg)",
    )
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
    if (LIVE_COMMANDS.intersection(arguments) or "--enable-rf" in arguments) and not {
        "run-live-session",
        "run-cw-live-tone",
        "run-cw-live-keyed",
    }.intersection(arguments):
        print("live RF and hardware actions are unavailable in the portable CLI", file=sys.stderr)
        return 2
    args = _parser().parse_args(arguments)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "capabilities":
        print(json.dumps(capability_report(), indent=2, sort_keys=True))
        return 0
    if args.command == "inventory-archive":
        try:
            document = inventory_archive(
                args.archive_root, args.manifest, args.output, archive_id=args.archive_id
            )
        except (ArchiveIntakeError, OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "archive_id": document["archive_id"],
                    "inventory_path": str(args.output),
                    "summary": document["summary"],
                    "qualification_claim": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "validate-cw-multi-capture":
        try:
            document = validate_multi_capture_session(args.path)
        except (ArchiveIntakeError, OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(document, indent=2, sort_keys=True))
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
    if args.command == "evaluate-sdr-calibration":
        try:
            sdr_profile = sdr_calibration.load_profile(args.profile)
            request = sdr_calibration.load_application_request(args.request)
            result = sdr_calibration.evaluate_profile(sdr_profile, request)
        except sdr_calibration.SdrCalibrationError as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["qualification_usable"] else 1
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
    if args.command == "validate-cw-contract-chain":
        try:
            result = load_cw_contract_chain(
                args.plan,
                args.expected_events,
                args.observations,
                args.mode_gate,
                args.session,
            )
        except (CwContractError, OfflineAnalysisError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "generate-cw-expected-events":
        try:
            document = write_expected_events(
                args.plan, args.output, source_revision=args.source_revision
            )
        except (ReferenceEncoderError, OfflineAnalysisError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(document, indent=2, sort_keys=True))
        return 0
    if args.command == "generate-cw-synthetic-iq":
        try:
            document = generate_synthetic_iq(
                args.plan,
                args.expected_events,
                args.capture,
                args.metadata,
                seed=args.seed,
            )
        except (CwIqError, OfflineAnalysisError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(document, indent=2, sort_keys=True))
        return 0
    if args.command == "analyze-cw-synthetic-iq":
        try:
            observations, gate = analyze_synthetic_iq(
                args.plan,
                args.expected_events,
                args.metadata,
                args.observations,
                args.mode_gate,
                source_revision=args.source_revision,
            )
        except (CwIqError, OfflineAnalysisError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "analysis_outcome": observations["analysis_outcome"],
                    "carrier_gate": gate["carrier_gate"],
                    "mode_gate": gate["mode_gate"],
                    "qualification_claim": False,
                    "synthetic": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "compose-cw-acquired-replay":
        try:
            result = compose_acquired_replay(
                args.plan,
                args.expected_events,
                args.capture_metadata,
                args.output_directory,
                source_revision=args.source_revision,
            )
        except (CwReplayError, CwIqError, OfflineAnalysisError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "validate-cw-acquired-replay":
        try:
            result = validate_replay_bundle(args.bundle, recompute=True)
        except (CwReplayError, CwIqError, OfflineAnalysisError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "run-cw-mock-lifecycle":
        try:
            result = run_mock_lifecycle(
                args.plan,
                args.expected_events,
                args.observations,
                args.mode_gate,
                args.output,
                injection=args.injection,
            )
        except (CwLifecycleError, OfflineAnalysisError, OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "validate-cw-mock-lifecycle":
        try:
            result = validate_mock_lifecycle(args.path)
        except (CwLifecycleError, OfflineAnalysisError, OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "run-cw-actual-host-preflight":
        try:
            result = run_cw_actual_host_preflight(
                args.plan,
                args.output_parent,
                ssh_path=args.ssh,
                confirmation_sha256=args.confirm_plan_sha256,
                enabled=args.enable_read_only_host_preflight,
            )
        except (CwHostPreflightError, OfflineAnalysisError, OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "validate-cw-actual-host-preflight":
        try:
            result = validate_cw_actual_host_preflight_bundle(args.bundle)
        except (CwHostPreflightError, OfflineAnalysisError, OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
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
    if args.command in {"run-live-session", "run-cw-live-tone"}:
        try:
            document = json.loads(args.plan.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise RealSessionError("real-session plan must be a JSON object")
            plan = ResolvedRealSessionPlan(document)
            resolved = plan.validated()
            if resolved["execution_mode"] != "live":
                raise RealSessionError("live command requires a live resolved plan")
            session_kind = resolved.get("session_kind", "wspr_qualification")
            expected_kind = (
                "cw_live_tone" if args.command == "run-cw-live-tone" else "wspr_qualification"
            )
            if session_kind != expected_kind:
                raise RealSessionError(f"{args.command} requires session_kind {expected_kind}")
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
        if args.command == "run-cw-live-tone":
            cleanup = result.get("cleanup")
            quiescence = result.get("quiescence")
            return (
                0
                if result["carrier_gate"] == "passed"
                and isinstance(cleanup, dict)
                and cleanup.get("outcome") == "verified"
                and isinstance(quiescence, dict)
                and quiescence.get("outcome") == "verified"
                else 1
            )
        return 0 if result["final_status"] == "qualified" else 1
    if args.command == "run-cw-live-keyed":
        try:
            document = json.loads(args.plan.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise LiveKeyedError("live keyed plan must be a JSON object")
            resolved = validate_resolved_keyed_plan(document)
            digest = resolved_keyed_plan_sha256(resolved)
            if args.confirm_plan_sha256 != digest:
                raise LiveKeyedError("typed plan digest confirmation did not match")
            if not args.operator.strip():
                raise LiveKeyedError("operator identity must not be empty")
            confirmed_at = datetime.now(UTC)
            authorization = compose_keyed_runtime_authorization(
                resolved,
                operator=args.operator,
                authorized_utc=confirmed_at.isoformat().replace("+00:00", "Z"),
            )
            from wsprrypi_qualification.live_keyed import build_production_keyed_adapter

            adapter = build_production_keyed_adapter(
                resolved,
                ssh_executable=args.ssh.resolve(),
                work_directory=args.work_directory.resolve(),
            )
            result = run_live_keyed_session(resolved, authorization, args.output_parent, adapter)
        except (
            OSError,
            json.JSONDecodeError,
            KeyedSessionContractError,
            LiveKeyedError,
            OfflineAnalysisError,
        ) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["result"]["final_status"] == "qualified" else 1
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
                plot_path=args.plot,
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
