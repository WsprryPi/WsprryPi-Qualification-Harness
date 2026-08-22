"""Hardware-free keyed coordinator exercised only through a sealed fake adapter."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from wsprrypi_qualification.keyed_session_contracts import (
    LIFECYCLE_STAGES,
    authorization_sha256,
    compose_keyed_aggregate_session,
    compose_keyed_result,
    derive_keyed_transaction_outcome,
    resolved_keyed_plan_sha256,
    validate_keyed_artifact_index,
    validate_keyed_runtime_authorization,
    validate_resolved_keyed_plan,
)
from wsprrypi_qualification.manifests import validate_manifest_name, write_manifest
from wsprrypi_qualification.offline import write_json_new


class KeyedCoordinatorError(RuntimeError):
    """The hardware-free keyed coordinator could not complete safely."""


class Boundary(StrEnum):
    PREFLIGHT = "preflight"
    CLEANUP_INSTALLED = "cleanup_installed"
    PROCESS_STARTED = "process_started"
    CAPTURE_COMPLETED = "capture_completed"
    ANALYSIS_COMPLETED = "analysis_completed"
    CLEANUP_COMPLETED = "cleanup_completed"
    QUIESCENCE_VERIFIED = "quiescence_verified"


class KeyedCoordinatorAdapter(Protocol):
    """Hardware-free adapter seam; this coordinator accepts only the sealed fake."""

    def perform(self, boundary: Boundary, transaction_number: int) -> bool: ...


class SealedFakeKeyedAdapter:
    """Deterministic fixture with no process, network, service, device, or RF access."""

    __slots__ = ("calls", "cancel_at", "failure_at")

    def __init__(
        self, *, failure_at: Boundary | None = None, cancel_at: Boundary | None = None
    ) -> None:
        if type(self) is not SealedFakeKeyedAdapter:
            raise TypeError("keyed fake adapter is sealed")
        if failure_at is not None and cancel_at is not None:
            raise ValueError("select at most one deterministic injection")
        self.failure_at = failure_at
        self.cancel_at = cancel_at
        self.calls: list[tuple[int, Boundary]] = []

    def perform(self, boundary: Boundary, transaction_number: int) -> bool:
        self.calls.append((transaction_number, boundary))
        return boundary is not self.failure_at


def run_hardware_free_keyed_session(
    plan: dict[str, Any],
    authorization: dict[str, Any],
    output_parent: Path,
    adapters: KeyedCoordinatorAdapter,
    *,
    cancellation: threading.Event | None = None,
) -> dict[str, Any]:
    """Run up to three fake transactions, stopping after the first unsuccessful one."""
    if type(adapters) is not SealedFakeKeyedAdapter:
        raise TypeError("only the sealed hardware-free keyed adapter is accepted")
    resolved = validate_resolved_keyed_plan(plan)
    auth = validate_keyed_runtime_authorization(resolved, authorization)
    try:
        session_directory = validate_manifest_name(str(resolved["session_id"]))
    except ValueError as error:
        raise KeyedCoordinatorError(f"unsafe keyed session ID: {error}") from error
    parent = output_parent.resolve()
    final = parent / session_directory
    temporary = parent / f".incomplete-{session_directory}"
    if final.parent != parent or temporary.parent != parent or final.exists() or temporary.exists():
        raise KeyedCoordinatorError("unsafe or reused keyed-session destination")
    parent.mkdir(parents=True, exist_ok=True)
    transactions: list[dict[str, Any]] = []
    for number in (1, 2, 3):
        transaction = _run_transaction(resolved, auth, adapters, number, cancellation)
        transactions.append(transaction)
        if transaction["final_outcome"] != "passed":
            break
    return publish_keyed_session(resolved, auth, transactions, parent)


def publish_keyed_session(
    plan: dict[str, Any],
    authorization: dict[str, Any],
    transactions: list[dict[str, Any]],
    output_parent: Path,
    *,
    artifact_sources: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Publish already validated keyed transactions to one new immutable directory."""
    resolved = validate_resolved_keyed_plan(plan)
    auth = validate_keyed_runtime_authorization(resolved, authorization)
    session_directory = validate_manifest_name(str(resolved["session_id"]))
    parent = output_parent.resolve()
    final = parent / session_directory
    temporary = parent / f".incomplete-{session_directory}"
    if final.exists() or temporary.exists():
        raise KeyedCoordinatorError("unsafe or reused keyed-session destination")
    temporary.mkdir(parents=True)
    try:
        aggregate = compose_keyed_aggregate_session(resolved, auth, transactions)
        result = compose_keyed_result(resolved, auth, aggregate)
        write_json_new(
            temporary / "resolved-plan.json",
            resolved,
            schema_name="resolved-keyed-session-plan.schema.json",
        )
        write_json_new(
            temporary / "runtime-authorization.json",
            auth,
            schema_name="keyed-runtime-authorization.schema.json",
        )
        for transaction in transactions:
            number = transaction["transaction_number"]
            for artifact in transaction["artifacts"]:
                target = temporary / str(artifact["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                source = (
                    None
                    if artifact_sources is None
                    else artifact_sources.get(str(artifact["path"]))
                )
                if source is None and artifact_sources is None:
                    target.write_bytes(_artifact_payload(int(number), str(artifact["role"])))
                elif source is None:
                    raise KeyedCoordinatorError("production keyed artifact source is missing")
                else:
                    if source.is_symlink() or not source.is_file():
                        raise KeyedCoordinatorError("production keyed artifact is unavailable")
                    identity = artifact_path_identity(source)
                    if any(identity[key] != artifact[key] for key in ("size_bytes", "sha256")):
                        raise KeyedCoordinatorError("production keyed artifact identity changed")
                    shutil.copyfile(source, target)
                    copied_identity = artifact_path_identity(target)
                    if any(
                        copied_identity[key] != artifact[key] for key in ("size_bytes", "sha256")
                    ):
                        raise KeyedCoordinatorError("copied keyed artifact identity changed")
            write_json_new(
                temporary / f"transaction-{number}.json",
                transaction,
                schema_name="keyed-transaction.schema.json",
            )
        write_json_new(
            temporary / "aggregate-session.json",
            aggregate,
            schema_name="keyed-aggregate-session.schema.json",
        )
        write_json_new(temporary / "result.json", result, schema_name="keyed-result.schema.json")
        if len(transactions) == 3:
            index = _artifact_index(resolved, temporary)
            validate_keyed_artifact_index(resolved, index)
            write_json_new(
                temporary / "artifact-index.json",
                index,
                schema_name="keyed-artifact-index.schema.json",
            )
        write_manifest(temporary)
        temporary.replace(final)
        return {"bundle": str(final), "aggregate": aggregate, "result": result}
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _run_transaction(
    plan: dict[str, Any],
    authorization: dict[str, Any],
    adapter: SealedFakeKeyedAdapter,
    number: int,
    cancellation: threading.Event | None,
) -> dict[str, Any]:
    outcomes = {stage: "not_run" for stage in LIFECYCLE_STAGES}
    failed: Boundary | None = None
    aborted = False
    primary = tuple(Boundary(stage) for stage in LIFECYCLE_STAGES[:5])
    for boundary in primary:
        if _cancelled(cancellation, adapter, boundary):
            outcomes[boundary.value] = "aborted"
            aborted = True
            break
        passed = adapter.perform(boundary, number)
        outcomes[boundary.value] = "passed" if passed else "failed"
        if not passed:
            if boundary in {Boundary.CLEANUP_INSTALLED, Boundary.PROCESS_STARTED}:
                outcomes[boundary.value] = "aborted"
            failed = boundary
            break
    # Cleanup and quiescence are always attempted for every transaction, even if
    # registration itself failed; the evidence must never imply silent cleanup.
    for boundary in (Boundary.CLEANUP_COMPLETED, Boundary.QUIESCENCE_VERIFIED):
        # Caller cancellation stops primary work, never cleanup. Explicit fake
        # injection remains available to exercise cleanup cancellation evidence.
        if _cancelled(None, adapter, boundary):
            outcomes[boundary.value] = "aborted"
            aborted = True
            continue
        passed = adapter.perform(boundary, number)
        outcomes[boundary.value] = "passed" if passed else "failed"
        if not passed:
            failed = boundary
    measurement = (
        "passed"
        if outcomes[Boundary.ANALYSIS_COMPLETED.value] == "passed"
        else "blocked"
        if failed in {Boundary.PREFLIGHT, Boundary.CAPTURE_COMPLETED}
        else "failed"
        if failed is Boundary.ANALYSIS_COMPLETED
        else "inconclusive"
    )
    transaction = {
        "schema_version": 1,
        "evidence_type": "keyed_transaction",
        "session_id": plan["session_id"],
        "mode": plan["mode"],
        "plan_sha256": resolved_keyed_plan_sha256(plan),
        "authorization_sha256": authorization_sha256(plan, authorization),
        "transaction_number": number,
        "transaction_id": f"{plan['session_id']}-transaction-{number}",
        "process_id": f"fake-process-{number}",
        "capture_id": f"fake-capture-{number}",
        "acquisition_id": f"fake-acquisition-{number}",
        "analysis_id": f"fake-analysis-{number}",
        "lifecycle": [{"stage": stage, "outcome": outcomes[stage]} for stage in LIFECYCLE_STAGES],
        "measurement_outcome": measurement,
        "cleanup_outcome": "verified"
        if outcomes[Boundary.CLEANUP_COMPLETED.value] == "passed"
        else "failed",
        "quiescence_outcome": "verified"
        if outcomes[Boundary.QUIESCENCE_VERIFIED.value] == "passed"
        else "failed",
        "final_outcome": "inconclusive",
        "artifacts": [_artifact(number, role) for role in ("process", "capture", "analysis")],
        "qualification_claim": False,
    }
    if (
        aborted
        and transaction["cleanup_outcome"] == transaction["quiescence_outcome"] == "verified"
    ):
        transaction["final_outcome"] = "aborted"
    else:
        transaction["final_outcome"] = derive_keyed_transaction_outcome(transaction)
    return transaction


def _cancelled(
    cancellation: threading.Event | None,
    adapter: SealedFakeKeyedAdapter,
    boundary: Boundary,
) -> bool:
    return bool(cancellation and cancellation.is_set()) or adapter.cancel_at is boundary


def _artifact(number: int, role: str) -> dict[str, Any]:
    payload = _artifact_payload(number, role)
    return {
        "role": role,
        "path": f"modeled/transaction-{number}-{role}.json",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _artifact_payload(number: int, role: str) -> bytes:
    return json.dumps(
        {"fixture": "sealed-hardware-free", "transaction": number, "role": role},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def artifact_path_identity(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {"size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _artifact_index(plan: dict[str, Any], root: Path) -> dict[str, Any]:
    bindings = (
        ("resolved_plan", "resolved-plan.json"),
        ("runtime_authorization", "runtime-authorization.json"),
        ("transaction_1", "transaction-1.json"),
        ("transaction_2", "transaction-2.json"),
        ("transaction_3", "transaction-3.json"),
        ("aggregate_session", "aggregate-session.json"),
        ("result", "result.json"),
    )
    artifacts = []
    for role, relative in bindings:
        payload = (root / relative).read_bytes()
        artifacts.append(
            {
                "role": role,
                "path": relative,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return {
        "schema_version": 1,
        "evidence_type": "keyed_artifact_index",
        "session_id": plan["session_id"],
        "plan_sha256": resolved_keyed_plan_sha256(plan),
        "artifacts": artifacts,
    }
