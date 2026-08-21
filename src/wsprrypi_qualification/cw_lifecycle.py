"""Mock-only bounded lifecycle evidence for tone and CW-family modes."""

from __future__ import annotations

import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from wsprrypi_qualification.adapters import (
    MockOperation,
    MockQuiescenceAdapter,
    MockReceiverAdapter,
    MockServiceAdapter,
    MockTransmitterAdapter,
    OperationOutcome,
    OperationResult,
)
from wsprrypi_qualification.cw_contracts import CwContractError, _bind, _validate_events
from wsprrypi_qualification.offline import artifact, load_json_document, write_json_new
from wsprrypi_qualification.supervisor import (
    OperationDeadlines,
    ResolvedPlan,
    Supervisor,
    validate_supervisor_document,
)


class CwLifecycleError(RuntimeError):
    """A mock lifecycle request or document is invalid."""


INJECTIONS = {
    "none",
    "receiver_acquire_fail",
    "receiver_acquire_timeout",
    "receiver_acquire_cancel",
    "transmitter_acquire_fail",
    "transmitter_acquire_timeout",
    "transmitter_acquire_cancel",
    "receiver_start_fail",
    "receiver_start_timeout",
    "receiver_start_cancel",
    "transmitter_start_fail",
    "transmitter_start_timeout",
    "transmitter_start_cancel",
    "monitor_fail",
    "monitor_timeout",
    "monitor_cancel",
    "transmitter_stop_fail",
    "transmitter_stop_timeout",
    "transmitter_stop_cancel",
    "receiver_stop_fail",
    "receiver_stop_timeout",
    "receiver_stop_cancel",
    "transmitter_release_fail",
    "transmitter_release_timeout",
    "transmitter_release_cancel",
    "receiver_release_fail",
    "receiver_release_timeout",
    "receiver_release_cancel",
    "service_restore_fail",
    "service_restore_timeout",
    "service_restore_cancel",
    "leak_verify_fail",
    "leak_verify_timeout",
    "leak_verify_cancel",
    "quiescence_fail",
    "quiescence_timeout",
    "quiescence_cancel",
}


def _reference(path: Path) -> dict[str, Any]:
    result = artifact(path)
    result["path"] = path.name
    return result


def _inputs(paths: tuple[Path, Path, Path, Path]) -> tuple[dict[str, Any], ...]:
    schemas = (
        "cw-mode-plan.schema.json",
        "cw-expected-events.schema.json",
        "cw-generated-observations.schema.json",
        "cw-mode-gate.schema.json",
    )
    documents = tuple(
        load_json_document(path, schema) for path, schema in zip(paths, schemas, strict=True)
    )
    plan, expected, observations, gate = documents
    try:
        _bind(expected["plan"], paths[1], paths[0], "expected-event plan")
        _bind(observations["plan"], paths[2], paths[0], "observation plan")
        _bind(observations["expected_events"], paths[2], paths[1], "observation events")
        _bind(gate["plan"], paths[3], paths[0], "gate plan")
        _bind(gate["expected_events"], paths[3], paths[1], "gate events")
        _bind(gate["observations"], paths[3], paths[2], "gate observations")
        _validate_events(plan, expected)
    except CwContractError as error:
        raise CwLifecycleError(str(error)) from error
    if any(item["run_id"] != plan["run_id"] for item in documents[1:]):
        raise CwLifecycleError("lifecycle inputs identify different runs")
    if any(item["mode"] != plan["mode"] for item in documents[1:]):
        raise CwLifecycleError("lifecycle inputs identify different modes")
    return documents


def _configure(injection: str) -> tuple[Any, ...]:
    if injection not in INJECTIONS:
        raise CwLifecycleError(f"unsupported mock lifecycle injection: {injection}")
    cancellation = threading.Event()
    receiver = MockReceiverAdapter("receiver", cancellation_event=cancellation)
    transmitter = MockTransmitterAdapter("transmitter", cancellation_event=cancellation)
    service = MockServiceAdapter(True, cancellation_event=cancellation)
    service.set_running(False)
    quiescence = MockQuiescenceAdapter("mock", cancellation_event=cancellation)
    leak = MockOperation("leak-verification", "helpers", "leak_verify", cancel_event=cancellation)
    monitor = MockOperation("mode-capture", "capture", "monitor", cancel_event=cancellation)
    deadlines = OperationDeadlines()
    if injection == "none":
        return receiver, transmitter, service, quiescence, leak, monitor, cancellation, deadlines
    target, behavior = injection.rsplit("_", 1)
    if target in {"receiver_acquire", "receiver_start", "receiver_stop", "receiver_release"}:
        action = target.removeprefix("receiver_")
        adapter: Any = receiver
        getattr(
            adapter, {"fail": "fail_at", "timeout": "block_at", "cancel": "cancel_at"}[behavior]
        ).add(action)
    elif target in {
        "transmitter_acquire",
        "transmitter_start",
        "transmitter_stop",
        "transmitter_release",
    }:
        action = target.removeprefix("transmitter_")
        adapter = transmitter
        getattr(
            adapter, {"fail": "fail_at", "timeout": "block_at", "cancel": "cancel_at"}[behavior]
        ).add(action)
    elif target == "monitor":
        if behavior == "fail":
            monitor.result = OperationResult(OperationOutcome.NONZERO_EXIT, 7, "injected monitor")
        elif behavior == "timeout":
            monitor.polls_before_result = 1_000_000
        else:
            monitor.cancel_at_poll = 1
    elif target == "service_restore":
        setattr(
            service,
            {"fail": "fail_restore", "timeout": "block_restore", "cancel": "cancel_on_restore"}[
                behavior
            ],
            True,
        )
    elif target == "leak_verify":
        if behavior == "fail":
            leak.result = OperationResult(
                OperationOutcome.CLEANUP_FAILED, None, "injected leak check"
            )
        elif behavior == "timeout":
            leak.polls_before_result = 1_000_000
        else:
            leak.cancel_at_poll = 1
    elif target == "quiescence":
        setattr(
            quiescence,
            {"fail": "verified", "timeout": "blocked", "cancel": "cancel_on_inspection"}[behavior],
            behavior != "fail",
        )
    deadline_values = asdict(OperationDeadlines())
    if behavior == "timeout":
        field = "monitor_s" if target == "monitor" else f"{target}_s"
        deadline_values[field] = 0.01
        deadlines = OperationDeadlines(**deadline_values)
    return receiver, transmitter, service, quiescence, leak, monitor, cancellation, deadlines


def run_mock_lifecycle(
    plan_path: Path,
    expected_path: Path,
    observations_path: Path,
    gate_path: Path,
    output_path: Path,
    *,
    injection: str = "none",
) -> dict[str, Any]:
    """Run and retain one sealed mock lifecycle rehearsal."""
    paths = (plan_path, expected_path, observations_path, gate_path)
    if output_path.exists():
        raise CwLifecycleError("refusing to overwrite mock lifecycle evidence")
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise CwLifecycleError("lifecycle inputs must be regular non-symlink files")
    if any(path.resolve().parent != output_path.resolve().parent for path in paths):
        raise CwLifecycleError("lifecycle inputs and output must share one evidence directory")
    if len({path.resolve() for path in (*paths, output_path)}) != 5:
        raise CwLifecycleError("lifecycle evidence paths must be distinct")
    plan, _, _, gate = _inputs(paths)
    receiver, transmitter, service, quiescence, leak, monitor, cancel, deadlines = _configure(
        injection
    )
    result = Supervisor(
        receiver, transmitter, service=service, quiescence=quiescence, leak_operation=leak
    ).run(
        ResolvedPlan(f"{plan['run_id']}-{plan['mode']}-mock", deadlines),
        cancellation=cancel,
        execution=monitor,
    )
    lifecycle_gate = (
        "passed"
        if result.cleanup_outcome == "verified" and result.outcome == "completed"
        else "failed"
    )
    final_status = result.final_status
    document = {
        "schema_version": 1,
        "evidence_type": "cw_mock_lifecycle",
        "run_id": plan["run_id"],
        "mode": plan["mode"],
        "mock_only": True,
        "injection": injection,
        "inputs": {
            role: _reference(path)
            for role, path in zip(
                ("plan", "expected_events", "observations", "mode_gate"), paths, strict=True
            )
        },
        "measurement": {"carrier_gate": gate["carrier_gate"], "mode_gate": gate["mode_gate"]},
        "supervisor": result.to_document(),
        "lifecycle_gate": lifecycle_gate,
        "failure_causes": sorted(set(result.failure_causes)),
        "final_status": final_status,
        "qualification_claim": False,
    }
    write_json_new(output_path, document, schema_name="cw-mock-lifecycle.schema.json")
    try:
        return validate_mock_lifecycle(output_path)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def _supervisor_signature(document: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic safety semantics, excluding wall-clock evidence."""
    return {
        "outcome": document["outcome"],
        "cleanup_outcome": document["cleanup_outcome"],
        "final_status": document["final_status"],
        "failure_causes": document["failure_causes"],
        "events": [
            {
                **{key: item[key] for key in ("phase", "component", "action", "outcome", "detail")},
                "detail": "" if item["action"] == "cleanup_installed" else item["detail"],
            }
            for item in document["events"]
        ],
        "ownership": [
            {
                key: item[key]
                for key in (
                    "handle_id",
                    "component",
                    "operation",
                    "transport",
                    "owned_by_harness",
                    "final_alive",
                    "return_code",
                    "outcome",
                    "stdout",
                    "stderr",
                )
            }
            for item in document["ownership"]
        ],
        "cleanup_actions": [
            {key: item[key] for key in ("component", "handle_id", "action", "outcome", "detail")}
            for item in document["cleanup_actions"]
        ],
        "service_delta": document["service_delta"],
        "leak_verification": document["leak_verification"],
        "quiescence": document["quiescence"],
    }


def validate_mock_lifecycle(path: Path) -> dict[str, Any]:
    document = load_json_document(path, "cw-mock-lifecycle.schema.json")
    if path.is_symlink():
        raise CwLifecycleError("mock lifecycle evidence cannot be a symlink")
    roles = ("plan", "expected_events", "observations", "mode_gate")
    resolved: list[Path] = []
    for role in roles:
        reference = document["inputs"][role]
        if Path(reference["path"]).name != reference["path"]:
            raise CwLifecycleError("lifecycle references must be canonical relative filenames")
        target = path.parent / reference["path"]
        if target.is_symlink() or not target.is_file():
            raise CwLifecycleError("lifecycle reference is not a regular file")
        actual = artifact(target)
        if (
            actual["size_bytes"] != reference["size_bytes"]
            or actual["sha256"] != reference["sha256"]
        ):
            raise CwLifecycleError("lifecycle input authentication failed")
        resolved.append(target)
    plan, _, _, gate = _inputs(tuple(resolved))  # type: ignore[arg-type]
    if document["run_id"] != plan["run_id"] or document["mode"] != plan["mode"]:
        raise CwLifecycleError("lifecycle identity contradicts the resolved plan")
    if document["injection"] not in INJECTIONS or document["mock_only"] is not True:
        raise CwLifecycleError("lifecycle is not a supported sealed mock rehearsal")
    validate_supervisor_document(document["supervisor"])
    supervisor = document["supervisor"]
    receiver, transmitter, service, quiescence, leak, monitor, cancel, deadlines = _configure(
        document["injection"]
    )
    replayed = Supervisor(
        receiver, transmitter, service=service, quiescence=quiescence, leak_operation=leak
    ).run(
        ResolvedPlan(f"{plan['run_id']}-{plan['mode']}-mock", deadlines),
        cancellation=cancel,
        execution=monitor,
    )
    if _supervisor_signature(supervisor) != _supervisor_signature(replayed.to_document()):
        raise CwLifecycleError("supervisor evidence contradicts the declared mock injection")
    expected_measurement = {"carrier_gate": gate["carrier_gate"], "mode_gate": gate["mode_gate"]}
    if document["measurement"] != expected_measurement:
        raise CwLifecycleError("lifecycle measurement contradicts the authenticated mode gate")
    expected_lifecycle = (
        "passed"
        if supervisor["cleanup_outcome"] == "verified" and supervisor["outcome"] == "completed"
        else "failed"
    )
    if document["lifecycle_gate"] != expected_lifecycle:
        raise CwLifecycleError("lifecycle gate contradicts supervisor evidence")
    if document["final_status"] != supervisor["final_status"]:
        raise CwLifecycleError("final status contradicts supervisor cleanup precedence")
    if document["failure_causes"] != sorted(set(supervisor["failure_causes"])):
        raise CwLifecycleError("failure causes contradict supervisor evidence")
    if document["qualification_claim"] is not False or document["final_status"] == "qualified":
        raise CwLifecycleError("mock lifecycle evidence cannot qualify hardware")
    return document
