"""One bounded QRSS governance observation; never a qualification campaign."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

from wsprrypi_qualification.capture_metadata import load_capture_metadata
from wsprrypi_qualification.offline import artifact, validate_document, write_json_new
from wsprrypi_qualification.pico_audit import _frames, validate_wire_audit
from wsprrypi_qualification.pico_campaign import check_admission, plan_digest, validate_pico_plan
from wsprrypi_qualification.pico_context import assessment, check_transition
from wsprrypi_qualification.pico_live import (
    PicoLiveAdapter,
    _authenticate_plan,
    _error_outcome,
    _private_destination,
)
from wsprrypi_qualification.pico_results import _validate_frame, settings_match
from wsprrypi_qualification.pico_serial import PicoCleanupError, require_supported_platform
from wsprrypi_qualification.pico_wire import PicoWireError, loads
from wsprrypi_qualification.progress import ProgressReporter
from wsprrypi_qualification.progress_viewer import tracking_command
from wsprrypi_qualification.real_capabilities import CaptureCapabilityPlan, capability_plan_sha256
from wsprrypi_qualification.transports import sanitized_environment
from wsprrypi_qualification.wtp_control import WtpPeer, validate_transaction

OPERATION_SECONDS = 120
CLEANUP_SECONDS = 45
ANALYSIS_SECONDS = 60
VALIDATION_SECONDS = 30
TOTAL_SECONDS = 300
MAX_CAPTURE_BYTES = 128 * 1024 * 1024


def error_detail(error: BaseException) -> str:
    """Preserve subprocess diagnostics in the private failure record."""
    detail = str(error)
    if isinstance(error, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
        for name in ("stdout", "stderr"):
            value = getattr(error, name, None)
            if value:
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
                detail += "\n" + name + ":\n" + value
    return detail


class DeadlineEvent(threading.Event):
    """Cancellation with an absolute deadline that cannot be cleared or renewed."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        super().__init__()
        self.clock = clock
        self.deadline = clock() + seconds

    def is_set(self) -> bool:
        return super().is_set() or self.clock() >= self.deadline

    def wait(self, timeout: float | None = None) -> bool:
        remaining = max(0.0, self.deadline - self.clock())
        super().wait(remaining if timeout is None else min(timeout, remaining))
        return self.is_set()


class OneJobPeer:
    """Consume each mutating attempt before sending, including ambiguous writes."""

    def __init__(self, peer: WtpPeer):
        self.peer = peer
        self.used: set[str] = set()
        self.owner_id: str | None = None

    def request(self, operation: str, body: dict[str, Any], timeout: float = 8) -> dict[str, Any]:
        if operation in {"LOAD", "ARM"}:
            if operation in self.used:
                raise PicoWireError("One-job attempt already consumed: " + operation)
            self.used.add(operation)
        reply = self.peer.request(operation, body, timeout)
        if operation == "CLAIM":
            self.owner_id = body["owner_id"]
        return reply

    def next_message(self, timeout: float) -> dict[str, Any]:
        return self.peer.next_message(timeout)


class SmokeAdapter(PicoLiveAdapter):
    """Production acquisition with deferred analysis and a shared cleanup deadline."""

    validation_purpose = "governance"

    cleanup_cancel: DeadlineEvent | None = None

    def begin_cleanup(self) -> None:
        super().begin_cleanup()
        if self.cleanup_cancel is None:
            self.cleanup_cancel = DeadlineEvent(CLEANUP_SECONDS)
        self.usb.begin_operations(self.cleanup_cancel)

    def analyze(
        self,
        mode: str,
        directory: Path,
        snapshot: dict[str, Any],
        capture: CaptureCapabilityPlan,
        off: CaptureCapabilityPlan,
        slot: Any,
        pre_quiet: float,
        acquired_frequency: float | None = None,
    ) -> dict[str, Any]:
        if mode != "qrss" or slot is not None:
            raise PicoWireError("Smoke runner supports one QRSS observation only")
        write_json_new(
            directory / "analysis-input.json",
            dict(
                snapshot=snapshot,
                capture=capture.document(),
                off=off.document(),
                pre_quiet=pre_quiet,
                acquired_frequency=acquired_frequency,
            ),
        )
        return dict(outcome="inconclusive", directory=str(directory), deferred=True)


def check_scope(plan: dict[str, Any]) -> None:
    validate_pico_plan(plan)
    rows = plan["modes"]
    if len(rows) != 1 or rows[0]["mode"] != "qrss" or len(rows[0]["jobs"]) != 1:
        raise PicoWireError("Smoke plan requires exactly one QRSS job")
    if rows[0]["carrier_job"] is not None:
        raise PicoWireError("Smoke plan cannot contain a carrier prerequisite")
    duration = int(rows[0]["jobs"][0]["total_duration_ns"]) / 1e9
    if duration > 10:
        raise PicoWireError("Smoke QRSS job must be at most ten seconds")
    if (duration + 32) * plan["configuration"]["receiver"][
        "sample_rate_hz"
    ] * 8 > MAX_CAPTURE_BYTES:
        raise PicoWireError("Smoke capture exceeds its 128 MiB evidence budget")


def acquire(adapter: PicoLiveAdapter, plan: dict[str, Any], output: Path) -> dict[str, Any]:
    """Common lifecycle used by production and connected, hardware-inhibited tests."""
    initial = None
    result: dict[str, Any] = dict(
        schema_version=1,
        evidence_type="pico_one_job_governance",
        qualification_claim=False,
        outcome="inconclusive",
        cleanup_verified=False,
        settings_unchanged=False,
        frame=None,
        errors=[],
    )
    try:
        check_scope(plan)
        write_json_new(output / "usb-topology.json", adapter.usb.identity)
        initial = adapter.snapshot(initial=True)
        write_json_new(output / "initial.json", initial)
        if plan["configuration"]["pico"]["configuration_revision"] is None:
            plan["configuration"]["pico"]["configuration_revision"] = initial["revision"]
        write_json_new(output / "plan.json", plan)
        check_admission(plan, initial, purpose="governance")
        adapter.reporter.emit("preflight", "completed", "Management admission passed")
        adapter.start_wtp()
        # WTP is opened only after read-only management admission.
        adapter._wtp = OneJobPeer(adapter.wtp)  # type: ignore[assignment]
        row = plan["modes"][0]
        adapter.reporter.emit("capture", "started", "One QRSS job; no automatic retry")
        result["frame"] = adapter.observe("qrss", row["jobs"][0], output / "observation")
    except BaseException as error:
        result["outcome"] = _error_outcome(error)
        result["errors"].append(
            dict(phase="operation", kind=type(error).__name__, detail=error_detail(error))
        )
    finally:
        try:
            adapter.reporter.emit("cleanup", "started", "Verifying release and unchanged settings")
            adapter.begin_cleanup()
            if adapter.jobs_attempted:
                write_json_new(output / "final-status.json", adapter.inactive())
            if initial is not None:
                final = adapter.snapshot()
                write_json_new(output / "final.json", final)
                result["settings_unchanged"] = settings_match(initial, final)
                if not result["settings_unchanged"]:
                    raise PicoWireError("Final settings/identity do not match initial snapshot")
            elif adapter.jobs_attempted:
                raise PicoWireError("Initial settings snapshot unavailable")
            result["cleanup_verified"] = True
        except BaseException as error:
            result["errors"].append(
                dict(phase="cleanup", kind=type(error).__name__, detail=error_detail(error))
            )
        finally:
            try:
                adapter.close()
            except BaseException as error:
                result["cleanup_verified"] = False
                result["errors"].append(
                    dict(phase="close", kind=type(error).__name__, detail=error_detail(error))
                )
        if not result["cleanup_verified"]:
            result["outcome"] = "cleanup_failed"
    return result


def analyze_retained(output: Path) -> dict[str, Any]:
    """Offline worker: no adapter constructor, USB, receiver, or WTP access."""
    plan = loads((output / "plan.json").read_bytes())
    check_scope(plan)
    value = loads((output / "observation/analysis-input.json").read_bytes())
    adapter = object.__new__(PicoLiveAdapter)
    adapter.plan = plan
    adapter.tools = loads((output / "controller.json").read_bytes())["tools"]
    captures = []
    for key in ("capture", "off"):
        item = value[key]
        for name in ("helper", "metadata_path", "output_path"):
            item[name] = Path(item[name])
        captures.append(CaptureCapabilityPlan(**item))
    result = adapter.analyze(
        "qrss",
        output / "observation",
        value["snapshot"],
        captures[0],
        captures[1],
        None,
        value["pre_quiet"],
        value["acquired_frequency"],
    )
    write_json_new(output / "frame.json", result)
    return result


def validate_retained(output: Path) -> dict[str, Any]:
    result_path = output / "smoke-result.json"
    if not result_path.exists():
        result_path = output / "candidate-result.json"
    result = loads(result_path.read_bytes())
    validate_document(result, "pico-smoke-result.schema.json")
    if (
        result["evidence_type"] != "pico_one_job_governance"
        or result["qualification_claim"] is not False
    ):
        raise PicoWireError("Smoke evidence cannot make a qualification claim")
    expected = result["artifacts"]
    actual = [
        artifact(p)
        for p in sorted(output.rglob("*"))
        if p.is_file() and p not in {output / "smoke-result.json", output / "candidate-result.json"}
    ]
    if expected != actual or any(p.is_symlink() for p in output.rglob("*")):
        raise PicoWireError("Smoke evidence inventory differs")
    if result["outcome"] != "passed":
        validate_failure(output, result)
        return result
    plan = loads((output / "plan.json").read_bytes())
    check_scope(plan)
    requested = loads((output / "requested-plan.json").read_bytes())
    attempt = loads((output / "attempt.json").read_bytes())
    if attempt["consumed"] is not True or attempt["plan_sha256"] != plan_digest(requested):
        raise PicoWireError("Attempt does not bind the authorized plan")
    resolved = copy.deepcopy(requested)
    if resolved["configuration"]["pico"]["configuration_revision"] is None:
        resolved["configuration"]["pico"]["configuration_revision"] = plan["configuration"]["pico"][
            "configuration_revision"
        ]
    if resolved != plan:
        raise PicoWireError("Resolved plan differs beyond initial configuration revision")
    initial = loads((output / "initial.json").read_bytes())
    final = loads((output / "final.json").read_bytes())
    check_admission(plan, initial, purpose="governance")
    check_admission(plan, final, purpose="governance")
    if not result["cleanup_verified"] or not settings_match(initial, final):
        raise PicoWireError("Cleanup/settings evidence differs")
    status = loads((output / "final-status.json").read_bytes())
    if (
        status["output_active"]
        or status["owner_id"] is not None
        or status["boot_id"] != initial["target"]["boot_id"]
        or status["state"] not in {"empty", "complete", "aborted", "missed"}
    ):
        raise PicoWireError("Final output/ownership is not released")
    transaction = loads((output / "observation/transaction.json").read_bytes())
    validated = validate_transaction(transaction)
    if not validated["cleanup_verified"] or not validated["completed"]:
        raise PicoWireError("Transaction did not complete and release")
    frame = loads((output / "frame.json").read_bytes())
    if frame["outcome"] not in {"passed", "failed"} or result["frame"] != frame:
        raise PicoWireError("Measurement must be conclusive and bound to the result")
    jobs: set[str] = set()
    _validate_frame(
        output / "observation",
        frame,
        plan,
        plan["modes"][0]["jobs"][0],
        jobs,
        set(),
        purpose="governance",
    )
    validate_wire_audit(output / "usb-io.jsonl", jobs, initial["target"])
    validate_management_snapshots(output)
    snapshots = (
        [initial]
        + [
            loads((output / "observation" / name).read_bytes())
            for name in ("before.json", "prepared.json")
        ]
        + [final]
    )
    for previous, current in pairwise(snapshots):
        check_transition(previous, current)
    if loads((output / "validation-context.json").read_bytes()) != retained_context(output):
        raise PicoWireError("Governance validation context differs from retained evidence")
    return result


def validate_failure(output: Path, result: dict[str, Any]) -> None:
    """Check partial evidence without turning an expected refusal into a pass."""
    if not result["errors"]:
        raise PicoWireError("Unsuccessful run has no retained cause")
    if not result["cleanup_verified"] and result["outcome"] != "cleanup_failed":
        raise PicoWireError("Cleanup failure lost precedence")
    transaction = output / "observation/transaction.json"
    if transaction.exists():
        checked = validate_transaction(loads(transaction.read_bytes()))
        if result["cleanup_verified"] and not checked["cleanup_verified"]:
            raise PicoWireError("Transmitter cleanup claim contradicts transaction")
    if result["settings_unchanged"]:
        initial = loads((output / "initial.json").read_bytes())
        final = loads((output / "final.json").read_bytes())
        if not settings_match(initial, final):
            raise PicoWireError("Partial result settings claim differs")
    for path in (output / "observation").glob("*-plan.json"):
        if not path.name.startswith(("rf-on-", "rf-off-")):
            continue
        capture = loads(path.read_bytes())
        diagnostic = Path(capture["metadata_path"] + ".execution.json")
        if not diagnostic.exists():
            continue
        if diagnostic.resolve().parent != (output / "observation").resolve():
            raise PicoWireError("Receiver diagnostic escapes observation")
        value = loads(diagnostic.read_bytes())
        if (
            value["capability_plan_sha256"] != capability_plan_sha256(capture)
            or value["iq_retained"] is not False
            or Path(capture["output_path"]).exists()
        ):
            raise PicoWireError("Rejected capture binding differs")
        native = value["native_failure_metadata"] or value["rejected_capture_metadata"]
        verified = value["execution"]["cleanup_verified"] is True and native is not None
        if native is not None:
            native_path = Path(native["path"])
            if (
                native_path.resolve().parent != (output / "observation").resolve()
                or artifact(native_path) != native
            ):
                raise PicoWireError("Native failure receipt differs")
            verified = verified and load_capture_metadata(native_path).cleanup_outcome == "verified"
        if not verified and result["cleanup_verified"]:
            raise PicoWireError("Receiver cleanup claim lacks native proof")


def validate_management_snapshots(output: Path) -> None:
    """Bind all four admission/settings snapshots to retained GET response bytes."""
    records = [loads(line) for line in (output / "usb-io.jsonl").read_bytes().splitlines()]
    streams = {}
    for phase, direction in (("write_requested", "out"), ("read", "in")):
        data = b"".join(
            bytes.fromhex(row["data_hex"])
            for row in records
            if row["carrier"] == "management" and row["phase"] == phase
        )
        streams[direction] = _frames(data, "management", direction)
    groups: list[list[tuple[str, dict[str, Any]]]] = []
    for request, response in zip(streams["out"], streams["in"], strict=True):
        if request["operation"] != "request":
            continue
        resource = request["path"].removeprefix("/api/control/v1/")
        if resource == "status":
            groups.append([])
        if groups:
            groups[-1].append((resource, response))
    paths = ["initial.json", "observation/before.json", "observation/prepared.json", "final.json"]
    if len(groups) != len(paths):
        raise PicoWireError("Management snapshot coverage differs")
    for group, name in zip(groups, paths, strict=True):
        snapshot = loads((output / name).read_bytes())
        for resource, value in snapshot.items():
            if resource in {"revision", "target"}:
                continue
            matches = [
                response
                for key, response in group
                if key == resource
                and loads(response["body"]) == value
                and response["target"] == snapshot["target"]
            ]
            if not matches or (
                resource == "config" and matches[-1]["etag"] != snapshot["revision"]
            ):
                raise PicoWireError(
                    "Snapshot differs from management response: " + name + ":" + resource
                )


def retained_context(output: Path) -> dict[str, Any]:
    paths = {
        "admission": "initial.json",
        "prepared": "observation/prepared.json",
        "final": "final.json",
    }
    return {
        stage: assessment(loads((output / name).read_bytes()), stage)
        for stage, name in paths.items()
        if (output / name).is_file()
    }


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("One-job total time budget exhausted")
    return value


def worker(output: Path, action: str, timeout: float) -> None:
    subprocess.run(
        [sys.executable, "-I", "-m", "wsprrypi_qualification.pico_smoke", action, str(output)],
        cwd=output,
        check=True,
        timeout=timeout,
        capture_output=True,
        env=sanitized_environment({"PYTHONNOUSERSITE": "1", "MPLBACKEND": "Agg"}),
    )


def publish(output: Path, result: dict[str, Any], timeout: float = VALIDATION_SECONDS) -> None:
    """Only publish passing evidence after independent retained validation."""
    candidate = output / "candidate-result.json"
    write_json_new(candidate, result, schema_name="pico-smoke-result.schema.json")
    try:
        if timeout <= 0:
            raise TimeoutError("No time remains for retained validation")
        worker(output, "validate", timeout)
    except BaseException as error:
        result["outcome"] = (
            "cleanup_failed"
            if not result["cleanup_verified"]
            else "aborted"
            if isinstance(error, KeyboardInterrupt)
            else "blocked"
        )
        result["errors"].append(
            dict(phase="validation", kind=type(error).__name__, detail=error_detail(error))
        )
    finally:
        candidate.unlink()
    write_json_new(
        output / "smoke-result.json", result, schema_name="pico-smoke-result.schema.json"
    )


def run(
    plan: dict[str, Any],
    output_parent: Path,
    operator: str,
    expected_digest: str,
    *,
    enable_rf: bool = False,
) -> dict[str, Any]:
    if not enable_rf or not operator.strip():
        raise PicoWireError("Explicit RF authorization and operator are required")
    check_scope(plan)
    if plan_digest(plan) != expected_digest:
        raise PicoWireError("Authorized plan digest differs")
    require_supported_platform()
    _authenticate_plan(plan)
    plan = copy.deepcopy(plan)
    output = _private_destination(output_parent)
    started = time.monotonic()
    deadline = started + TOTAL_SECONDS
    write_json_new(
        output / "attempt.json",
        dict(
            operator=operator,
            plan_sha256=expected_digest,
            consumed=True,
            qualification_claim=False,
            operation_seconds=OPERATION_SECONDS,
            cleanup_seconds=CLEANUP_SECONDS,
            analysis_seconds=ANALYSIS_SECONDS,
            validation_seconds=VALIDATION_SECONDS,
            total_seconds=TOTAL_SECONDS,
        ),
    )
    write_json_new(output / "requested-plan.json", plan)
    reporter = ProgressReporter(output / "progress.jsonl", mirror=sys.stderr)
    print(str(output), flush=True)
    print(tracking_command(output / "progress.jsonl"), flush=True)
    result: dict[str, Any]
    adapter: SmokeAdapter | None = None
    acquired = False
    try:
        reporter.emit("preflight", "started", "Attempt consumed; opening selected Pico")
        adapter = SmokeAdapter(plan, output, reporter, DeadlineEvent(OPERATION_SECONDS))
        result = acquire(adapter, plan, output)
        acquired = True
        write_json_new(output / "validation-context.json", retained_context(output))
        if result["frame"] is not None and result["cleanup_verified"]:
            reporter.emit("analysis", "started", "Devices closed; analyzing retained IQ")
            worker(output, "analyze", min(ANALYSIS_SECONDS, remaining(deadline)))
            result["frame"] = loads((output / "frame.json").read_bytes())
            if result["frame"]["outcome"] in {"passed", "failed"}:
                result["outcome"] = "passed"
    except BaseException as error:
        result = locals().get(
            "result",
            dict(
                schema_version=1,
                evidence_type="pico_one_job_governance",
                qualification_claim=False,
                cleanup_verified=False,
                settings_unchanged=False,
                frame=None,
                errors=[],
            ),
        )
        if adapter is None and not isinstance(error, PicoCleanupError):
            result["cleanup_verified"] = True  # Constructor closes its own partial resources.
        result["outcome"] = (
            _error_outcome(error) if result["cleanup_verified"] else "cleanup_failed"
        )
        result["errors"].append(
            dict(phase="runner", kind=type(error).__name__, detail=error_detail(error))
        )
    finally:
        if adapter is not None and not acquired:
            try:
                adapter.close()
            except BaseException as error:
                result["cleanup_verified"] = False
                result["outcome"] = "cleanup_failed"
                result["errors"].append(
                    dict(phase="close", kind=type(error).__name__, detail=error_detail(error))
                )
        reporter.close()
    result["artifacts"] = [artifact(p) for p in sorted(output.rglob("*")) if p.is_file()]
    publish(output, result, min(VALIDATION_SECONDS, max(0.0, deadline - time.monotonic())))
    return dict(result, output_directory=str(output))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("analyze", "validate"):
        sub.add_parser(name).add_argument("output", type=Path)
    live = sub.add_parser("run")
    live.add_argument("--plan", type=Path, required=True)
    live.add_argument("--output-parent", type=Path, required=True)
    live.add_argument("--operator", required=True)
    live.add_argument("--expected-plan-sha256", required=True)
    live.add_argument("--enable-rf", action="store_true")
    args = parser.parse_args()
    if args.action == "run":
        value = run(
            loads(args.plan.read_bytes()),
            args.output_parent,
            args.operator,
            args.expected_plan_sha256,
            enable_rf=args.enable_rf,
        )
    else:
        value = (analyze_retained if args.action == "analyze" else validate_retained)(args.output)
    print(json.dumps(value, indent=2))
    if args.action == "run" and value["outcome"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
