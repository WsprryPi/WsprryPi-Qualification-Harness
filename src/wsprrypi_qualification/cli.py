"""Portable command-line interface for qualification-harness capabilities."""

from __future__ import annotations

import argparse
import base64
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
from wsprrypi_qualification.automatic_deployment import (
    AutomaticDeploymentError,
    delegate_automatic_complete_test,
)
from wsprrypi_qualification.capabilities import capability_report
from wsprrypi_qualification.capture_metadata import CaptureMetadataError, load_capture_metadata
from wsprrypi_qualification.carrier import analyze_carrier_acquired
from wsprrypi_qualification.complete_test import (
    CompleteTestError,
    CompleteTestOverrides,
    compose_complete_test_plan,
    delegate_complete_test,
    receiver_is_local,
    rehearse_complete_test,
    resolve_local_sdr,
    run_complete_test,
    validate_complete_test_bundle,
)
from wsprrypi_qualification.cw_contracts import CwContractError, load_cw_contract_chain
from wsprrypi_qualification.cw_defaults import CANONICAL_KEYED_TEST_MESSAGE
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
from wsprrypi_qualification.offline import (
    OfflineAnalysisError,
    artifact,
    load_json_document,
    write_json_new,
    write_offline_failure,
)
from wsprrypi_qualification.profiles import ProfileError, load_profile
from wsprrypi_qualification.progress import default_progress_path, stderr_reporter
from wsprrypi_qualification.progress_viewer import tracking_command
from wsprrypi_qualification.real_session import (
    RealQualificationSession,
    RealRuntimeAuthorization,
    RealSessionError,
    ResolvedRealSessionPlan,
)
from wsprrypi_qualification.receiver_calibration import (
    ReceiverCalibrationError,
    write_synthetic_fixture,
)
from wsprrypi_qualification.receiver_calibration import (
    compose_binding as compose_receiver_calibration,
)
from wsprrypi_qualification.receiver_calibration import (
    disabled_binding as disabled_receiver_calibration,
)
from wsprrypi_qualification.rp1_campaign import (
    Rp1CampaignError,
    compose_rp1_rehearsal,
    configured_output_parent,
    write_rp1_rehearsal,
)
from wsprrypi_qualification.turnkey_campaign import (
    TurnkeyCampaignError,
    compose_resolved_campaign_plan,
    resolved_campaign_sha256,
    run_hardware_free_campaign,
    run_live_campaign,
    validate_resolved_campaign_plan,
)

LIVE_COMMANDS = {"run", "capture", "transmit", "tone", "enable-rf"}


def _complete_test_summary(outcome: dict[str, object]) -> dict[str, object]:
    result = outcome["result"]
    if not isinstance(result, dict):
        raise CompleteTestError("complete-test returned an invalid result")
    return {
        "status": result["final_status"],
        "transmitter": result["transmitter_host"],
        "receiver": result["receiver_host"],
        "sdr": result["sdr_selector"],
        "bundle": outcome["bundle"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wsprrypi-qualification")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("runtime-identity", help=argparse.SUPPRESS)
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
    synthetic_calibration = subparsers.add_parser(
        "generate-synthetic-sdr-calibration",
        help="write a deterministic non-hardware frozen-contract profile fixture",
    )
    synthetic_calibration.add_argument("output_directory", type=Path)
    compose_calibration = subparsers.add_parser(
        "compose-receiver-calibration",
        help="bind a frozen receiver profile and application request for recorded/live plans",
    )
    compose_calibration.add_argument("profile", type=Path)
    compose_calibration.add_argument("request", type=Path)
    compose_calibration.add_argument("output", type=Path)
    compose_calibration.add_argument(
        "--policy", choices=("required", "optional"), default="required"
    )
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
    cw_replay.add_argument("--receiver-calibration-binding", type=Path)
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
        help=(
            "run the digest-bound three-transaction QRSS/FSKCW/DFCW lifecycle; "
            "Raspberry Pi transmitters require a plan-bound noninteractive privilege wrapper"
        ),
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
    carrier = subparsers.add_parser("analyze-carrier", help="analyze offline RF-off/RF-on CF32")
    carrier.add_argument("rf_off", type=Path)
    carrier.add_argument("rf_on", type=Path)
    carrier.add_argument("evidence", type=Path)
    carrier.add_argument("--bench-profile", type=Path, required=True)
    carrier.add_argument("--test-profile", type=Path, required=True)
    carrier.add_argument("--cw-mode-plan", type=Path, help="authenticated TONE cadence plan")
    carrier.add_argument(
        "--cw-expected-events", type=Path, help="authenticated TONE cadence events"
    )
    carrier.add_argument("--fft-size", type=int, default=262_144)
    carrier.add_argument("--dc-exclusion-hz", type=float, default=1_000.0)
    carrier.add_argument("--rf-off-metadata", type=Path, required=True)
    carrier.add_argument("--rf-on-metadata", type=Path, required=True)
    carrier.add_argument("--relocation-bundle", type=Path)
    carrier.add_argument(
        "--receiver-calibration-policy",
        choices=("required", "optional", "disabled"),
        default="disabled",
    )
    carrier.add_argument("--receiver-calibration-profile", type=Path)
    carrier.add_argument("--receiver-calibration-request", type=Path)
    carrier.add_argument("--receiver-calibration-binding", type=Path)
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
    audio.add_argument("--selected-frequency-hz", type=float)
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
    turnkey = subparsers.add_parser(
        "turnkey-campaign",
        help="plan, rehearse, or dispatch one typed campaign through maintained coordinators",
    )
    turnkey_subcommands = turnkey.add_subparsers(dest="turnkey_action", required=True)
    turnkey_plan = turnkey_subcommands.add_parser(
        "plan", help="compose one hardware-free route plan"
    )
    turnkey_plan.add_argument("request", type=Path)
    turnkey_plan.add_argument("mode_plan", type=Path)
    turnkey_plan.add_argument("output", type=Path)
    turnkey_validate = turnkey_subcommands.add_parser(
        "validate", help="validate and digest a resolved campaign without external access"
    )
    turnkey_validate.add_argument("plan", type=Path)
    turnkey_rehearse = turnkey_subcommands.add_parser(
        "rehearse", help="verify campaign routing in a deterministic hardware-free bundle"
    )
    turnkey_rehearse.add_argument("plan", type=Path)
    turnkey_rehearse.add_argument("output_parent", type=Path)
    turnkey_execute = turnkey_subcommands.add_parser(
        "execute", help="dispatch an exactly confirmed live plan to its production coordinator"
    )
    turnkey_execute.add_argument("plan", type=Path)
    turnkey_execute.add_argument("output_parent", type=Path)
    turnkey_execute.add_argument("--operator", required=True)
    turnkey_execute.add_argument("--work-directory", type=Path, required=True)
    turnkey_execute.add_argument("--ssh", type=Path, required=True)
    turnkey_execute.add_argument("--confirm-plan-sha256", required=True)
    turnkey_execute.add_argument("--enable-turnkey-live", action="store_true", required=True)
    turnkey_execute.add_argument("--enable-rf", action="store_true", required=True)
    complete = subparsers.add_parser(
        "complete-test",
        help="run the bounded TONE/WSPR/QRSS/FSKCW/DFCW campaign",
    )
    complete.add_argument("transmitter_host", metavar="TRANSMITTER_HOST")
    complete.add_argument("receiver_host", metavar="RECEIVER_HOST")
    complete.add_argument(
        "--sdr",
        required=True,
        help="exact SoapySDR device selector to resolve on the receiver host",
    )
    complete.add_argument(
        "--enable-rf",
        action="store_true",
        help=("authorize one bounded RF run; unspecified physical-path facts remain unknown"),
    )
    complete.add_argument("--receiver-local", action="store_true", help=argparse.SUPPRESS)
    complete.add_argument("--delegated-output", action="store_true", help=argparse.SUPPRESS)
    complete.add_argument("--delegation-receipt-base64", help=argparse.SUPPRESS)
    complete.add_argument(
        "--rehearse", action="store_true", help="hardware-free plan and routing rehearsal"
    )
    complete.add_argument("--configuration", type=Path)
    complete.add_argument(
        "--allow-unqualified-frequency",
        action="store_true",
        help="explicitly opt in to experimental amateur-band RP1 qualification",
    )
    complete.add_argument(
        "--rf-path",
        type=Path,
        help="JSON physical-path observations for automatic deployment; not RF authorization",
    )
    wsprrypi_runtime = complete.add_mutually_exclusive_group()
    wsprrypi_runtime.add_argument(
        "--wsprrypi-binary",
        default="/usr/local/bin/wsprrypi",
        metavar="REMOTE_PATH",
        help=(
            "installed transmitter executable to copy into the campaign deployment "
            "(default: /usr/local/bin/wsprrypi)"
        ),
    )
    wsprrypi_runtime.add_argument(
        "--wsprrypi-source",
        type=Path,
        metavar="LOCAL_CHECKOUT",
        help="explicitly opt in to packaging and compiling this WsprryPi checkout",
    )
    complete.add_argument(
        "--wsprrypi-config",
        default="/usr/local/etc/wsprrypi.ini",
        metavar="REMOTE_PATH",
        help=(
            "installed transmitter configuration to copy with --wsprrypi-binary "
            "(default: /usr/local/etc/wsprrypi.ini)"
        ),
    )
    complete.add_argument(
        "--progress-log",
        type=Path,
        help="append-only JSON Lines progress log (default: a new durable user-state file)",
    )
    complete.add_argument("--progress-stream", action="store_true", help=argparse.SUPPRESS)
    complete.add_argument(
        "--transmitter-backend",
        choices=("gpio", "si5351", "rp1_gpclk"),
        default="gpio",
        help="transmitter backend for automatically composed plans (default: gpio)",
    )
    complete.add_argument(
        "--rp1-route",
        choices=("gpio4", "gpio20"),
        help="route-compatible spelling retained for existing RP1 rehearsal inputs",
    )
    complete.add_argument(
        "--transmit-gpio",
        type=int,
        choices=(4, 20),
        help="explicit RP1 GPCLK transmitter route; only GPIO4 and GPIO20 are supported",
    )
    complete.add_argument("--band", default="20m")
    complete.add_argument("--frequency-hz", type=int, default=14_097_100)
    complete.add_argument(
        "--requested-transmit-frequency-offset-hz",
        type=int,
        default=0,
        help=(
            "intentional offset added once to the nominal --frequency-hz for every mode "
            "(default: 0)"
        ),
    )
    complete.add_argument("--callsign", default="Q0QQQ")
    complete.add_argument("--grid", default="JJ00")
    complete.add_argument("--power-dbm", type=int, default=0)
    complete.add_argument("--message", default=CANONICAL_KEYED_TEST_MESSAGE)
    complete.add_argument("--qrss-dot-seconds", type=float, default=0.7)
    complete.add_argument("--fskcw-dot-seconds", type=float, default=0.7)
    complete.add_argument("--dfcw-dot-seconds", type=float, default=0.7)
    complete.add_argument("--fskcw-separation-hz", type=float, default=5.0)
    complete.add_argument("--dfcw-separation-hz", type=float, default=5.0)
    complete.add_argument(
        "--carrier-offset-max-hz",
        type=float,
        default=100.0,
        help=(
            "maximum absolute offset in Hz of the strongest acquired transmitter-added "
            "frequency (default: 100; zero is valid)"
        ),
    )
    complete.add_argument(
        "--frequency-acquisition-half-width-hz",
        type=float,
        default=1_000.0,
        help=(
            "receiver/analyzer acquisition half-width applied to every mode "
            "(default: 1000; distinct from pass/fail carrier tolerance)"
        ),
    )
    complete.add_argument(
        "--carrier-best-20hz-share-min",
        type=float,
        default=0.5,
        help="minimum resolved carrier power share in the best 20 Hz (default: 0.5)",
    )
    complete.add_argument(
        "--gpio-manual-ppm",
        type=float,
        help=(
            "fixed measured GPIO/RP1 host correction applied exactly once through "
            "WsprryPi --gpio-manual-ppm"
        ),
    )
    complete.add_argument(
        "--transmitter-ppm-offset",
        type=float,
        default=0.0,
        help="additive harness residual correction in transmitter-backend ppm (default: 0)",
    )
    validate_complete = subparsers.add_parser(
        "validate-complete-test",
        help="semantically validate an authenticated complete-test aggregate bundle",
    )
    validate_complete.add_argument("bundle", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (LIVE_COMMANDS.intersection(arguments) or "--enable-rf" in arguments) and not {
        "run-live-session",
        "run-cw-live-tone",
        "run-cw-live-keyed",
        "turnkey-campaign",
        "complete-test",
    }.intersection(arguments):
        print("live RF and hardware actions are unavailable in the portable CLI", file=sys.stderr)
        return 2
    args = _parser().parse_args(arguments)
    if args.command == "runtime-identity":
        print(
            json.dumps(
                {
                    "launcher": artifact(Path(sys.argv[0]).resolve()),
                    "module": artifact(Path(__file__).resolve()),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "capabilities":
        print(json.dumps(capability_report(), indent=2, sort_keys=True))
        return 0
    if args.command == "validate-complete-test":
        try:
            validated_complete = validate_complete_test_bundle(args.bundle)
        except (CompleteTestError, OfflineAnalysisError, OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(validated_complete, indent=2, sort_keys=True))
        return 0
    if args.command == "complete-test":
        progress_path = (
            None
            if args.progress_stream and args.progress_log is None
            else args.progress_log or default_progress_path()
        )
        reporter = stderr_reporter(progress_path, stream=args.progress_stream)
        if not args.progress_stream:
            print(f"Progress log: {reporter.path}", file=sys.stderr, flush=True)
            if reporter.path is not None:
                print(
                    f"Track progress: {tracking_command(reporter.path)}",
                    file=sys.stderr,
                    flush=True,
                )
        try:
            reporter.emit("command", "started", "complete-test command accepted")
            if args.rehearse and args.enable_rf:
                raise CompleteTestError("--rehearse conflicts with --enable-rf")
            if args.allow_unqualified_frequency and (
                args.transmitter_backend != "rp1_gpclk"
                or args.configuration is not None
                or args.rehearse
            ):
                raise CompleteTestError(
                    "--allow-unqualified-frequency requires automatic live RP1 deployment"
                )
            if args.rf_path is not None and (args.configuration is not None or args.rehearse):
                raise CompleteTestError("--rf-path applies only to automatic live deployment")
            selected_rp1_route = None if args.transmit_gpio is None else f"gpio{args.transmit_gpio}"
            if (
                selected_rp1_route is not None
                and args.rp1_route is not None
                and selected_rp1_route != args.rp1_route
            ):
                raise CompleteTestError("--transmit-gpio and --rp1-route disagree")
            selected_rp1_route = selected_rp1_route or args.rp1_route
            if (args.transmitter_backend == "rp1_gpclk") != (selected_rp1_route is not None):
                raise CompleteTestError(
                    "--transmit-gpio is required exactly for --transmitter-backend rp1_gpclk"
                )
            if args.transmitter_backend == "rp1_gpclk":
                assert selected_rp1_route is not None
                if args.rehearse and args.configuration is None:
                    raise CompleteTestError("RP1 rehearsal requires --configuration")
                if args.rehearse and args.transmitter_host != args.receiver_host:
                    raise CompleteTestError("RP1 same-host rehearsal requires identical host names")
                if args.rehearse:
                    rehearsal = compose_rp1_rehearsal(
                        args.configuration.absolute(),
                        selected_rp1_route,
                        residual_ppm=args.transmitter_ppm_offset,
                        manual_ppm=args.gpio_manual_ppm,
                        carrier_offset_max_hz=args.carrier_offset_max_hz,
                    )
                    if rehearsal["host"] != args.transmitter_host:
                        raise CompleteTestError("RP1 rehearsal host differs from command host")
                    destination = (
                        configured_output_parent(args.configuration.absolute())
                        / rehearsal["campaign_id"]
                    )
                    write_rp1_rehearsal(rehearsal, destination)
                    reporter.emit("command", "completed", "RP1 hardware-free rehearsal composed")
                    print(
                        json.dumps(
                            {
                                "bundle": str(destination),
                                "campaign_id": rehearsal["campaign_id"],
                                "backend": "rp1_gpclk",
                                "route": rehearsal["route"],
                                "mode_count": 5,
                                "qualification_claim": False,
                            },
                            indent=2,
                            sort_keys=True,
                        )
                    )
                    reporter.close()
                    return 0
            rf_path = (
                None
                if args.rf_path is None
                else load_json_document(args.rf_path, "rf-path-observation.schema.json")
            )
            if (
                args.wsprrypi_source is not None
                and args.wsprrypi_config != "/usr/local/etc/wsprrypi.ini"
            ):
                raise CompleteTestError(
                    "--wsprrypi-config applies only to an installed WsprryPi binary"
                )
            if args.configuration is not None and (
                args.wsprrypi_source is not None
                or args.wsprrypi_binary != "/usr/local/bin/wsprrypi"
                or args.wsprrypi_config != "/usr/local/etc/wsprrypi.ini"
            ):
                raise CompleteTestError(
                    "WsprryPi runtime selectors apply only to automatic deployment"
                )
            if not args.rehearse and not args.enable_rf:
                raise CompleteTestError("live complete-test requires explicit --enable-rf")
            receiver_local = receiver_is_local(args.receiver_host)
            if receiver_local and (args.rf_path is not None or args.allow_unqualified_frequency):
                raise CompleteTestError("automatic deployment inputs require a remote receiver")
            if args.receiver_local and not receiver_local:
                raise CompleteTestError(
                    "remote receiver delegation landed on a host with the wrong identity"
                )
            if args.delegation_receipt_base64 is not None and not args.receiver_local:
                raise CompleteTestError("delegation evidence is accepted only on the receiver")
            delegation_receipt = None
            if args.delegation_receipt_base64 is not None:
                try:
                    delegation_receipt = json.loads(
                        base64.urlsafe_b64decode(
                            args.delegation_receipt_base64
                            + "=" * (-len(args.delegation_receipt_base64) % 4)
                        ).decode("utf-8")
                    )
                except (ValueError, UnicodeError, json.JSONDecodeError) as error:
                    raise CompleteTestError("delegation evidence is invalid") from error
                if not isinstance(delegation_receipt, dict):
                    raise CompleteTestError("delegation evidence must be an object")
            if not args.rehearse and not args.receiver_local and not receiver_local:
                forwarded = ["--enable-rf"]
                forwarded.append("--progress-stream")
                if args.configuration is not None:
                    forwarded.extend(("--configuration", str(args.configuration)))
                forwarded.extend(
                    (
                        "--transmitter-backend",
                        args.transmitter_backend,
                        "--band",
                        args.band,
                        "--frequency-hz",
                        str(args.frequency_hz),
                        "--requested-transmit-frequency-offset-hz",
                        str(args.requested_transmit_frequency_offset_hz),
                        "--callsign",
                        args.callsign,
                        "--grid",
                        args.grid,
                        "--power-dbm",
                        str(args.power_dbm),
                        "--message",
                        args.message,
                        "--qrss-dot-seconds",
                        str(args.qrss_dot_seconds),
                        "--fskcw-dot-seconds",
                        str(args.fskcw_dot_seconds),
                        "--dfcw-dot-seconds",
                        str(args.dfcw_dot_seconds),
                        "--fskcw-separation-hz",
                        str(args.fskcw_separation_hz),
                        "--dfcw-separation-hz",
                        str(args.dfcw_separation_hz),
                        "--carrier-offset-max-hz",
                        str(args.carrier_offset_max_hz),
                        "--frequency-acquisition-half-width-hz",
                        str(args.frequency_acquisition_half_width_hz),
                        "--carrier-best-20hz-share-min",
                        str(args.carrier_best_20hz_share_min),
                        *(
                            []
                            if args.gpio_manual_ppm is None
                            else ["--gpio-manual-ppm", str(args.gpio_manual_ppm)]
                        ),
                        "--transmitter-ppm-offset",
                        str(args.transmitter_ppm_offset),
                    )
                )
                if selected_rp1_route is not None:
                    forwarded.extend(("--transmit-gpio", selected_rp1_route.removeprefix("gpio")))
                if args.configuration is None:
                    delegated = delegate_automatic_complete_test(
                        args.transmitter_host,
                        args.receiver_host,
                        args.sdr,
                        forwarded,
                        wsprrypi_binary=(
                            None if args.wsprrypi_source is not None else args.wsprrypi_binary
                        ),
                        wsprrypi_configuration=args.wsprrypi_config,
                        rf_path=rf_path,
                        allow_unqualified_frequency=args.allow_unqualified_frequency,
                        wsprrypi_source=args.wsprrypi_source,
                        transmitter_backend=args.transmitter_backend,
                        transmit_gpio=(
                            None
                            if selected_rp1_route is None
                            else int(selected_rp1_route.removeprefix("gpio"))
                        ),
                        progress=reporter,
                    )
                else:
                    delegated = delegate_complete_test(
                        args.transmitter_host,
                        args.receiver_host,
                        args.sdr,
                        forwarded,
                        configuration=args.configuration,
                        progress=reporter,
                    )
                print(json.dumps(_complete_test_summary(delegated), indent=2, sort_keys=True))
                status = delegated["result"]["final_status"]
                reporter.close()
                return {
                    "qualified": 0,
                    "fixture_blocked": 3,
                    "preflight_failed": 3,
                    "inconclusive": 3,
                    "unqualified_carrier": 4,
                    "unqualified_decode": 4,
                    "unqualified_keyed": 4,
                    "aborted": 5,
                    "cleanup_failed": 6,
                }[status]
            discovered_sdr = None if args.rehearse else resolve_local_sdr(args.sdr)
            overrides = CompleteTestOverrides(
                band=args.band,
                frequency_hz=args.frequency_hz,
                requested_transmit_frequency_offset_hz=(
                    args.requested_transmit_frequency_offset_hz
                ),
                callsign=args.callsign,
                grid=args.grid,
                power_dbm=args.power_dbm,
                message=args.message,
                qrss_dot_seconds=args.qrss_dot_seconds,
                fskcw_dot_seconds=args.fskcw_dot_seconds,
                dfcw_dot_seconds=args.dfcw_dot_seconds,
                fskcw_separation_hz=args.fskcw_separation_hz,
                dfcw_separation_hz=args.dfcw_separation_hz,
                carrier_offset_max_hz=args.carrier_offset_max_hz,
                frequency_acquisition_half_width_hz=(args.frequency_acquisition_half_width_hz),
                carrier_best_20hz_share_min=args.carrier_best_20hz_share_min,
                gpio_manual_ppm=args.gpio_manual_ppm,
                transmitter_ppm_offset=args.transmitter_ppm_offset,
            )
            complete_plan = compose_complete_test_plan(
                args.transmitter_host,
                args.receiver_host,
                args.sdr,
                configuration=args.configuration,
                overrides=overrides,
                discovered_sdr=discovered_sdr,
                delegation_receipt=delegation_receipt,
                live=not args.rehearse,
            )
            execution = complete_plan["execution_paths"]
            output_parent = Path(execution["output_parent"])
            if args.rehearse:
                complete_result = rehearse_complete_test(complete_plan, output_parent)
            else:
                complete_result = run_complete_test(
                    complete_plan,
                    output_parent,
                    ssh_executable=Path(execution["ssh_executable"]["path"]),
                    work_directory=Path(execution["work_directory"]),
                    progress=reporter.emit,
                )
        except (
            AutomaticDeploymentError,
            CompleteTestError,
            Rp1CampaignError,
            RealSessionError,
            LiveKeyedError,
            OfflineAnalysisError,
            OSError,
            ValueError,
        ) as error:
            reporter.emit("command", "failed", f"{type(error).__name__}: {error}")
            print(str(error), file=sys.stderr)
            reporter.close()
            return 2
        rendered_complete = (
            complete_result if args.delegated_output else _complete_test_summary(complete_result)
        )
        print(json.dumps(rendered_complete, indent=2, sort_keys=True))
        reporter.close()
        if args.rehearse:
            return 0
        status = complete_result["result"]["final_status"]
        if status == "qualified":
            return 0
        if status in {"fixture_blocked", "preflight_failed", "inconclusive"}:
            return 3
        if status in {"unqualified_carrier", "unqualified_decode", "unqualified_keyed"}:
            return 4
        if status == "aborted":
            return 5
        if status == "cleanup_failed":
            return 6
        raise AssertionError("unreachable complete-test status")
    if args.command == "turnkey-campaign":
        try:
            if args.turnkey_action == "plan":
                document = compose_resolved_campaign_plan(
                    args.request,
                    args.mode_plan,
                    args.output,
                )
                turnkey_result: dict[str, object] = {
                    "plan_only": True,
                    "external_calls": 0,
                    "production_adapters_constructed": False,
                    "resolved_plan_sha256": resolved_campaign_sha256(document),
                    "path": str(args.output),
                }
            elif args.turnkey_action == "validate":
                document = load_json_document(
                    args.plan, "resolved-turnkey-campaign-plan.schema.json"
                )
                validate_resolved_campaign_plan(document)
                turnkey_result = {
                    "valid": True,
                    "external_calls": 0,
                    "resolved_plan_sha256": resolved_campaign_sha256(document),
                }
            elif args.turnkey_action == "rehearse":
                document = load_json_document(
                    args.plan, "resolved-turnkey-campaign-plan.schema.json"
                )
                turnkey_result = run_hardware_free_campaign(document, args.output_parent)
            elif args.turnkey_action == "execute":
                document = load_json_document(
                    args.plan, "resolved-turnkey-campaign-plan.schema.json"
                )
                digest = resolved_campaign_sha256(document)
                if args.confirm_plan_sha256 != digest:
                    raise TurnkeyCampaignError("typed campaign digest confirmation did not match")
                turnkey_result = run_live_campaign(
                    document,
                    args.output_parent,
                    operator=args.operator,
                    confirmed_plan_sha256=digest,
                    ssh_executable=args.ssh,
                    work_directory=args.work_directory,
                )
            else:
                raise AssertionError("unreachable turnkey campaign action")
        except (
            TurnkeyCampaignError,
            RealSessionError,
            LiveKeyedError,
            OfflineAnalysisError,
            OSError,
            ValueError,
        ) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(turnkey_result, indent=2, sort_keys=True))
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
    if args.command == "generate-synthetic-sdr-calibration":
        try:
            paths = write_synthetic_fixture(args.output_directory)
        except (ReceiverCalibrationError, OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(
            json.dumps({key: str(value) for key, value in paths.items()}, indent=2, sort_keys=True)
        )
        return 0
    if args.command == "compose-receiver-calibration":
        try:
            binding = compose_receiver_calibration(args.profile, args.request, policy=args.policy)
            write_json_new(
                args.output, binding, schema_name="receiver-calibration-binding.schema.json"
            )
        except (ReceiverCalibrationError, OfflineAnalysisError, OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(binding, indent=2, sort_keys=True))
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
            calibration = (
                disabled_receiver_calibration()
                if args.receiver_calibration_binding is None
                else load_json_document(
                    args.receiver_calibration_binding,
                    "receiver-calibration-binding.schema.json",
                )
            )
            result = compose_acquired_replay(
                args.plan,
                args.expected_events,
                args.capture_metadata,
                args.output_directory,
                source_revision=args.source_revision,
                receiver_calibration=calibration,
            )
        except (
            CwReplayError,
            CwIqError,
            OfflineAnalysisError,
            ReceiverCalibrationError,
            OSError,
        ) as error:
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
    try:
        if args.command == "analyze-carrier":
            if args.receiver_calibration_binding is not None and any(
                value is not None
                for value in (
                    args.receiver_calibration_profile,
                    args.receiver_calibration_request,
                )
            ):
                raise ReceiverCalibrationError(
                    "use either a receiver calibration binding or profile/request inputs"
                )
            if (args.receiver_calibration_profile is None) != (
                args.receiver_calibration_request is None
            ):
                raise ReceiverCalibrationError(
                    "receiver calibration profile and request are required together"
                )
            if args.receiver_calibration_binding is not None:
                receiver_calibration = load_json_document(
                    args.receiver_calibration_binding,
                    "receiver-calibration-binding.schema.json",
                )
            elif args.receiver_calibration_profile is None:
                receiver_calibration = disabled_receiver_calibration(
                    args.receiver_calibration_policy
                )
            else:
                receiver_calibration = compose_receiver_calibration(
                    args.receiver_calibration_profile,
                    args.receiver_calibration_request,
                    policy=args.receiver_calibration_policy,
                )
            document = analyze_carrier_acquired(
                args.rf_off,
                args.rf_on,
                args.rf_off_metadata,
                args.rf_on_metadata,
                args.bench_profile,
                args.test_profile,
                args.evidence,
                cw_mode_plan_path=args.cw_mode_plan,
                cw_expected_path=args.cw_expected_events,
                fft_size=args.fft_size,
                dc_exclusion_hz=args.dc_exclusion_hz,
                relocation_bundle=args.relocation_bundle,
                plot_path=args.plot,
                receiver_calibration=receiver_calibration,
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
                selected_frequency_hz=args.selected_frequency_hz,
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
    except (OfflineAnalysisError, ReceiverCalibrationError, OSError, ValueError) as error:
        evidence = getattr(args, "evidence", None)
        if isinstance(evidence, Path) and not evidence.exists():
            with suppress(OSError, OfflineAnalysisError):
                write_offline_failure(evidence, args.command, error)
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(document, indent=2, sort_keys=True, default=str))
    return 0
