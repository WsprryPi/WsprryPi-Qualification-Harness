"""Fail-closed production coordination for live QRSS, FSKCW, and DFCW sessions."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Protocol

from wsprrypi_qualification.keyed_coordinator import artifact_path_identity, publish_keyed_session
from wsprrypi_qualification.keyed_session_contracts import (
    LIFECYCLE_STAGES,
    authorization_sha256,
    derive_keyed_transaction_outcome,
    resolved_keyed_plan_sha256,
    validate_keyed_runtime_authorization,
    validate_resolved_keyed_plan,
)
from wsprrypi_qualification.manifests import validate_manifest_name
from wsprrypi_qualification.offline import artifact


class LiveKeyedError(RuntimeError):
    """A live keyed production invariant failed closed."""


class LiveKeyedProviders(Protocol):
    """Narrow seam implemented by authenticated production capabilities."""

    def preflight(self, plan: dict[str, Any], number: int) -> bool: ...
    def install_cleanup(self, plan: dict[str, Any], number: int) -> bool: ...
    def start_process(self, arguments: tuple[str, ...], number: int) -> str | None: ...
    def capture(self, plan: dict[str, Any], number: int) -> tuple[str, str, str] | None: ...
    def analyze(self, plan: dict[str, Any], number: int) -> tuple[str, str]: ...
    def cleanup(self, plan: dict[str, Any], number: int) -> bool: ...
    def verify_quiescence(self, plan: dict[str, Any], number: int) -> bool: ...
    def evidence_paths(self, number: int) -> dict[str, Path]: ...
    def close(self) -> bool: ...


class ProductionKeyedAdapter:
    """Sealed lifecycle adapter over authenticated capability providers."""

    __slots__ = ("artifact_sources", "providers")

    def __init__(self, providers: LiveKeyedProviders) -> None:
        if type(self) is not ProductionKeyedAdapter:
            raise TypeError("production keyed adapter is sealed")
        self.providers = providers
        self.artifact_sources: dict[str, Path] = {}

    def transaction(
        self,
        plan: dict[str, Any],
        authorization: dict[str, Any],
        number: int,
        cancellation: threading.Event | None,
    ) -> dict[str, Any]:
        outcomes = {stage: "not_run" for stage in LIFECYCLE_STAGES}
        measurement = "inconclusive"
        primary_failed = False
        cleanup_installed = False
        process_id = f"no-process-{number}"
        capture_id = f"no-capture-{number}"
        acquisition_id = f"no-acquisition-{number}"
        analysis_id = f"no-analysis-{number}"
        try:
            if _cancelled(cancellation):
                outcomes["preflight"] = "aborted"
                primary_failed = True
            elif self.providers.preflight(plan, number):
                outcomes["preflight"] = "passed"
            else:
                outcomes["preflight"] = "failed"
                measurement = "blocked"
                primary_failed = True
            if not primary_failed:
                if _cancelled(cancellation):
                    outcomes["cleanup_installed"] = "aborted"
                    primary_failed = True
                elif self.providers.install_cleanup(plan, number):
                    outcomes["cleanup_installed"] = "passed"
                    cleanup_installed = True
                else:
                    outcomes["cleanup_installed"] = "aborted"
                    primary_failed = True
            if not primary_failed:
                if _cancelled(cancellation):
                    outcomes["process_started"] = "aborted"
                    primary_failed = True
                elif started := self.providers.start_process(
                    tuple(plan["application_plan"]["arguments"]), number
                ):
                    outcomes["process_started"] = "passed"
                    process_id = started
                else:
                    outcomes["process_started"] = "aborted"
                    primary_failed = True
            if not primary_failed:
                if _cancelled(cancellation):
                    outcomes["capture_completed"] = "aborted"
                    primary_failed = True
                elif captured := self.providers.capture(plan, number):
                    outcomes["capture_completed"] = "passed"
                    capture_id, acquisition_id, process_outcome = captured
                    if process_outcome == "failed":
                        outcomes["analysis_completed"] = "failed"
                        measurement = "failed"
                        primary_failed = True
                    elif process_outcome == "aborted":
                        outcomes["analysis_completed"] = "aborted"
                        primary_failed = True
                    elif process_outcome != "passed":
                        raise LiveKeyedError("keyed process outcome is invalid")
                else:
                    outcomes["capture_completed"] = "failed"
                    measurement = "blocked"
                    primary_failed = True
            if not primary_failed:
                if _cancelled(cancellation):
                    outcomes["analysis_completed"] = "aborted"
                else:
                    measurement, analysis_id = self.providers.analyze(plan, number)
                    outcomes["analysis_completed"] = (
                        "passed" if measurement == "passed" else "failed"
                    )
        except Exception:
            first = next((stage for stage, value in outcomes.items() if value == "not_run"), None)
            if first is not None and first not in {"cleanup_completed", "quiescence_verified"}:
                if first in {"preflight", "capture_completed"}:
                    outcomes[first] = "failed"
                    measurement = "blocked"
                elif first == "analysis_completed":
                    outcomes[first] = "failed"
                    measurement = "failed"
                else:
                    outcomes[first] = "aborted"
        finally:
            try:
                cleanup_ok = self.providers.cleanup(plan, number)
            except Exception:
                cleanup_ok = False
            outcomes["cleanup_completed"] = "passed" if cleanup_ok else "failed"
            try:
                quiescent = self.providers.verify_quiescence(plan, number)
            except Exception:
                quiescent = False
            outcomes["quiescence_verified"] = "passed" if quiescent else "failed"
        retained_artifacts = []
        try:
            evidence_paths = self.providers.evidence_paths(number)
        except Exception:
            evidence_paths = {}
        core_roles = ("process", "capture", "analysis")
        ordered_roles = (*core_roles, *sorted(set(evidence_paths) - set(core_roles)))
        for role in ordered_roles:
            source = evidence_paths.get(role)
            if source is None or not source.is_file() or source.is_symlink():
                continue
            identity = artifact_path_identity(source)
            suffix = source.suffix if source.suffix else ".bin"
            relative = f"transactions/{number}/{role}{suffix}"
            retained_artifacts.append({"role": role, "path": relative, **identity})
            self.artifact_sources[relative] = source
        transaction = {
            "schema_version": 1,
            "evidence_type": "keyed_transaction",
            "session_id": plan["session_id"],
            "mode": plan["mode"],
            "plan_sha256": resolved_keyed_plan_sha256(plan),
            "authorization_sha256": authorization_sha256(plan, authorization),
            "transaction_number": number,
            "transaction_id": f"{plan['session_id']}-transaction-{number}",
            "process_id": process_id,
            "capture_id": capture_id,
            "acquisition_id": acquisition_id,
            "analysis_id": analysis_id,
            "lifecycle": [
                {"stage": stage, "outcome": outcomes[stage]} for stage in LIFECYCLE_STAGES
            ],
            "measurement_outcome": measurement,
            "cleanup_outcome": "verified" if cleanup_ok else "failed",
            "quiescence_outcome": "verified" if quiescent else "failed",
            "final_outcome": "inconclusive",
            "artifacts": retained_artifacts,
            "qualification_claim": False,
        }
        transaction["final_outcome"] = derive_keyed_transaction_outcome(transaction)
        if not cleanup_installed and transaction["final_outcome"] == "inconclusive":
            transaction["final_outcome"] = "aborted"
        return transaction


def run_live_keyed_session(
    plan: dict[str, Any],
    authorization: dict[str, Any],
    output_parent: Path,
    adapter: ProductionKeyedAdapter,
    *,
    cancellation: threading.Event | None = None,
) -> dict[str, Any]:
    """Execute at most three live transactions through the sealed production adapter."""
    if type(adapter) is not ProductionKeyedAdapter:
        raise TypeError("live keyed execution requires the sealed production adapter")
    resolved = validate_resolved_keyed_plan(plan)
    auth = validate_keyed_runtime_authorization(resolved, authorization)
    try:
        validate_manifest_name(str(resolved["session_id"]))
    except ValueError as error:
        raise LiveKeyedError(f"unsafe live keyed session ID: {error}") from error
    transactions: list[dict[str, Any]] = []
    try:
        for number in (1, 2, 3):
            transaction = adapter.transaction(resolved, auth, number, cancellation)
            transactions.append(transaction)
            if transaction["final_outcome"] != "passed":
                break
    finally:
        try:
            closed = adapter.providers.close()
        except Exception:
            closed = False
    if not closed:
        if transactions:
            transactions[-1]["cleanup_outcome"] = "failed"
            transactions[-1]["lifecycle"][5]["outcome"] = "failed"
            transactions[-1]["final_outcome"] = "cleanup_failed"
        else:
            raise LiveKeyedError("production capability sessions could not be closed")
    return publish_keyed_session(
        resolved,
        auth,
        transactions,
        output_parent,
        artifact_sources=adapter.artifact_sources,
    )


def build_production_keyed_adapter(
    plan: dict[str, Any], *, ssh_executable: Path, work_directory: Path
) -> ProductionKeyedAdapter:
    """Construct the existing-capability provider composition for a live keyed plan."""
    resolved = validate_resolved_keyed_plan(plan)
    ssh_binding = resolved["capability_bindings"]["ssh"]
    if (
        not ssh_executable.is_absolute()
        or not ssh_executable.is_file()
        or artifact(ssh_executable) != ssh_binding
    ):
        raise LiveKeyedError("pinned SSH executable identity changed")
    if work_directory.exists() or not work_directory.is_absolute():
        raise LiveKeyedError("live keyed work directory must be a new absolute path")
    # Provider construction lives beside the existing SSH/helper, capture,
    # service, and quiescence adapters to keep platform behavior isolated.
    from wsprrypi_qualification.live_adapters import build_keyed_capability_providers

    try:
        providers = build_keyed_capability_providers(
            resolved, ssh_executable=ssh_executable, work_directory=work_directory
        )
    except Exception as error:
        raise LiveKeyedError(f"keyed production capability construction failed: {error}") from error
    return ProductionKeyedAdapter(providers)


def _cancelled(cancellation: threading.Event | None) -> bool:
    return cancellation is not None and cancellation.is_set()
