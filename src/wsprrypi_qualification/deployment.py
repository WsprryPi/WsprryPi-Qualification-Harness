"""Hardware-free deployment configuration and pinned command providers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import subprocess
import time
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from wsprrypi_qualification.offline import validate_document


class DeploymentError(RuntimeError):
    """Deployment configuration or provider evidence is unsafe."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_deployment_config(
    path: Path, *, expected_identity: str | None = None, expected_plan_sha256: str | None = None
) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError("deployment configuration cannot be loaded") from exc
    validate_document(document, "helper-deployment-config.schema.json")
    if document["protocol_version"] != 1:
        raise DeploymentError("unsupported helper protocol version")
    if expected_identity is not None and document["helper_identity"] != expected_identity:
        raise DeploymentError("helper identity mismatch")
    if expected_plan_sha256 is not None and document["plan_sha256"] != expected_plan_sha256:
        raise DeploymentError("resolved plan digest mismatch")
    services = document["allowed_services"]
    if not services or len(services) > 16 or any("*" in item for item in services):
        raise DeploymentError("service allowlist must be narrow, explicit, and nonempty")
    for name in ("python", "helper", "systemctl", "gpio", "si5351"):
        configured = document["executables"][name]
        executable = Path(configured["path"])
        if not executable.is_absolute() or not executable.is_file():
            raise DeploymentError(f"{name} executable must be an existing absolute file")
        if sha256_file(executable) != configured["sha256"]:
            raise DeploymentError(f"{name} executable SHA-256 mismatch")
        if os.name != "nt" and executable.stat().st_mode & stat.S_IWOTH:
            raise DeploymentError(f"{name} executable must not be world-writable")
    for field in ("venv_path", "config_path", "state_directory"):
        if not Path(document[field]).is_absolute():
            raise DeploymentError(f"{field} must be absolute")
    return cast(dict[str, Any], document)


def runtime_helper_config(document: dict[str, Any]) -> dict[str, object]:
    """Translate deployment facts to the capability helper's runtime schema."""
    executables = document["executables"]
    return {
        "protocol_version": document["protocol_version"],
        "helper_identity": document["helper_identity"],
        "plan_sha256": document["plan_sha256"],
        "allowed_services": document["allowed_services"],
        "systemctl_path": executables["systemctl"]["path"],
        "systemctl_sha256": executables["systemctl"]["sha256"],
        "service_timeout_s": 5.0,
        "gpio_helper_path": executables["gpio"]["path"],
        "gpio_helper_sha256": executables["gpio"]["sha256"],
        "si5351_helper_path": executables["si5351"]["path"],
        "si5351_helper_sha256": executables["si5351"]["sha256"],
        "inspection_timeout_s": 5.0,
    }


@dataclass(frozen=True)
class PinnedCommand:
    provider_type: str
    executable: Path
    sha256: str
    timeout_s: float
    plan_sha256: str
    host: str
    prefix_arguments: tuple[str, ...] = ()
    pinned_arguments: tuple[tuple[Path, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.executable.is_absolute()
            or not self.executable.is_file()
            or not math.isfinite(self.timeout_s)
            or self.timeout_s <= 0
            or len(self.plan_sha256) != 64
        ):
            raise DeploymentError("provider command is not safely configured")
        if sha256_file(self.executable) != self.sha256:
            raise DeploymentError("provider executable SHA-256 mismatch")
        for path, digest in self.pinned_arguments:
            if not path.is_absolute() or not path.is_file() or sha256_file(path) != digest:
                raise DeploymentError("provider code argument SHA-256 mismatch")
        pinned_paths = tuple(str(path) for path, _digest in self.pinned_arguments)
        if len(pinned_paths) != len(set(pinned_paths)):
            raise DeploymentError("provider prefix pins must be unique")
        if any(
            argument.startswith("-") or not Path(argument).is_absolute()
            for argument in self.prefix_arguments
        ):
            raise DeploymentError("provider prefix arguments must be absolute pinned paths")
        path_prefixes = self.prefix_arguments
        if path_prefixes != pinned_paths:
            raise DeploymentError("every executable prefix path must have one exact pin")

    def run(self, arguments: tuple[str, ...], contract: dict[str, object]) -> dict[str, Any]:
        if sha256_file(self.executable) != self.sha256:
            raise DeploymentError("provider executable identity changed")
        for path, digest in self.pinned_arguments:
            if sha256_file(path) != digest:
                raise DeploymentError("provider code argument identity changed")
        argv = [str(self.executable), *self.prefix_arguments, *arguments]
        started = datetime.now(UTC)
        before = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_s,
            )
            timed_out = False
            return_code = completed.returncode
            stdout, stderr = completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out, return_code = True, None
            stdout = _text(exc.stdout)
            stderr = _text(exc.stderr)
        elapsed = time.monotonic() - before
        completed_utc = datetime.now(UTC)
        parsed: dict[str, object] | None = None
        cause = "timeout" if timed_out else "nonzero_exit" if return_code else None
        if cause is None:
            try:
                loaded = json.loads(stdout)
                if not isinstance(loaded, dict):
                    raise ValueError
                parsed = cast(dict[str, object], loaded)
            except (json.JSONDecodeError, ValueError):
                cause = "malformed_response"
        document: dict[str, Any] = {
            "schema_version": 1,
            "evidence_type": "deployment_provider_execution",
            "provider_type": self.provider_type,
            "host": self.host,
            "plan_sha256": self.plan_sha256,
            "executable": {"path": str(self.executable), "sha256": self.sha256},
            "executed_artifacts": [
                {"path": str(path), "sha256": digest} for path, digest in self.pinned_arguments
            ],
            "arguments": argv,
            "prefix_argument_count": len(self.prefix_arguments),
            "started_utc": _utc(started),
            "completed_utc": _utc(completed_utc),
            "elapsed_s": elapsed,
            "deadline_s": self.timeout_s,
            "return_code": return_code,
            "timed_out": timed_out,
            "stdout": stdout,
            "stderr": stderr,
            "contract": contract,
            "parsed_result": parsed,
            "outcome": "completed" if cause is None else "blocked",
            "cause": cause,
            "mutation_performed": None
            if self.provider_type == "systemd" and arguments[-3:-2] == ("systemd-set",)
            else False,
            "cleanup_verified": not timed_out,
        }
        validate_provider_evidence(document)
        return document


def validate_provider_evidence(document: dict[str, Any]) -> None:
    validate_document(document, "deployment-provider-evidence.schema.json")
    if document["timed_out"] != (document["cause"] == "timeout"):
        raise DeploymentError("timeout outcome is contradictory")
    if document["timed_out"] and document["cleanup_verified"]:
        raise DeploymentError("timed-out provider cleanup cannot be inferred")
    artifact_paths = [item["path"] for item in document["executed_artifacts"]]
    prefix_count = document["prefix_argument_count"]
    arguments = document["arguments"]
    if arguments[0] != document["executable"]["path"]:
        raise DeploymentError("provider executable contradicts the argument vector")
    if len(artifact_paths) != prefix_count or arguments[1 : 1 + prefix_count] != artifact_paths:
        raise DeploymentError("executed artifact evidence differs from the argument vector")
    operation_index = 1 + prefix_count
    operations = {
        "gpio": {"gpio-inspect"},
        "si5351": {"si5351-inspect"},
        "systemd": {"systemd-inspect", "systemd-set"},
    }
    if (
        operation_index >= len(arguments)
        or arguments[operation_index] not in operations[document["provider_type"]]
    ):
        raise DeploymentError("provider operation position contradicts executed artifacts")
    operation = arguments[operation_index]
    contract = document["contract"]
    if document["provider_type"] in {"gpio", "si5351"}:
        if len(arguments) != operation_index + 2:
            raise DeploymentError("inspection provider argument shape is invalid")
        try:
            payload = json.loads(arguments[operation_index + 1])
        except json.JSONDecodeError as exc:
            raise DeploymentError("inspection provider payload is not JSON") from exc
        if payload != contract or arguments[operation_index + 1] != json.dumps(
            contract, sort_keys=True
        ):
            raise DeploymentError("inspection provider payload contradicts its contract")
    elif operation == "systemd-inspect":
        if (
            len(arguments) != operation_index + 2
            or set(contract) != {"service", "allowed_services"}
            or arguments[operation_index + 1] != contract["service"]
            or contract["service"] not in contract["allowed_services"]
        ):
            raise DeploymentError("systemd inspection arguments contradict its contract")
    else:
        desired = contract.get("requested_state", contract.get("restore_to"))
        change_keys = {"service", "initial_state", "requested_state", "changed_by_harness"}
        restore_keys = {
            "service",
            "expected_current_state",
            "restore_to",
            "changed_by_harness",
        }
        if (
            len(arguments) != operation_index + 3
            or frozenset(contract) not in {frozenset(change_keys), frozenset(restore_keys)}
            or arguments[operation_index + 1] != contract.get("service")
            or arguments[operation_index + 2] != desired
            or desired not in {"active", "inactive"}
            or contract.get("changed_by_harness") is not True
        ):
            raise DeploymentError("systemd mutation arguments contradict its contract")
    if document["mutation_performed"] and document["provider_type"] in {"gpio", "si5351"}:
        raise DeploymentError("GPIO and Si5351 providers are read-only")
    if document["outcome"] == "completed" and (
        document["return_code"] != 0
        or document["parsed_result"] is None
        or document["cause"] is not None
    ):
        raise DeploymentError("successful provider evidence is incomplete")
    if document["outcome"] == "blocked" and document["cause"] is None:
        raise DeploymentError("blocked provider evidence lacks a typed cause")
    parsed = document["parsed_result"]
    if parsed is None:
        return
    if document["provider_type"] == "gpio":
        expected = {"chip", "line", "direction", "function", "owner", "value"}
        if (
            set(parsed) != expected
            or parsed["chip"] != contract["chip"]
            or parsed["line"] != contract["line"]
        ):
            raise DeploymentError("GPIO response contradicts the resolved line")
    if document["provider_type"] == "si5351":
        expected = {"bus", "address", "device", "enabled_outputs", "owner"}
        if (
            set(parsed) != expected
            or parsed["bus"] != contract["bus"]
            or parsed["address"] != contract["address"]
        ):
            raise DeploymentError("Si5351 response contradicts the resolved interface")
    if document["provider_type"] == "systemd":
        expected = {"service", "active_state", "unit_file_state", "load_state"}
        if (
            set(parsed) != expected
            or parsed["service"] != contract["service"]
            or parsed["active_state"] not in {"active", "inactive", "failed"}
            or parsed["unit_file_state"] not in {"enabled", "disabled", "masked", "missing"}
            or parsed["load_state"] not in {"loaded", "not-found", "masked", "error"}
            or (operation == "systemd-set" and parsed["active_state"] != desired)
        ):
            raise DeploymentError("systemd response contradicts the resolved service")


def inspect_gpio(command: PinnedCommand, contract: dict[str, object]) -> dict[str, Any]:
    document = command.run(("gpio-inspect", json.dumps(contract, sort_keys=True)), contract)
    parsed = document["parsed_result"]
    if parsed is not None:
        if parsed["owner"] is not None:
            _block(document, "ownership_conflict")
        elif (
            parsed["direction"] != contract["direction"]
            or parsed["function"] != contract["function"]
        ):
            _block(document, "active_output")
    validate_provider_evidence(document)
    return document


def inspect_si5351(command: PinnedCommand, contract: dict[str, object]) -> dict[str, Any]:
    document = command.run(("si5351-inspect", json.dumps(contract, sort_keys=True)), contract)
    parsed = document["parsed_result"]
    if parsed is not None:
        if parsed["device"] != contract["device"]:
            _block(document, "wrong_device")
        elif parsed["owner"] is not None:
            _block(document, "ownership_conflict")
        elif parsed["enabled_outputs"]:
            _block(document, "active_output")
    validate_provider_evidence(document)
    return document


def inspect_systemd(
    command: PinnedCommand, service: str, allowed_services: frozenset[str]
) -> dict[str, Any]:
    if service not in allowed_services:
        raise DeploymentError("service is outside the exact allowlist")
    contract: dict[str, object] = {"service": service, "allowed_services": sorted(allowed_services)}
    document = command.run(("systemd-inspect", service), contract)
    parsed = document["parsed_result"]
    if parsed is not None:
        expected = {"service", "active_state", "unit_file_state", "load_state"}
        if set(parsed) != expected or parsed["service"] != service:
            raise DeploymentError("systemd response contradicts the requested service")
        if parsed["active_state"] not in {"active", "inactive", "failed"} or parsed[
            "unit_file_state"
        ] not in {"enabled", "disabled", "masked", "missing"}:
            raise DeploymentError("systemd response contains an unsupported state")
    return document


class SystemdRestoration:
    """Restore only one exact service state changed through this instance."""

    def __init__(
        self, command: PinnedCommand, service: str, allowed_services: frozenset[str]
    ) -> None:
        self.command, self.service, self.allowed = command, service, allowed_services
        self.initial: str | None = None
        self.changed_to: str | None = None
        self.requested_state: str | None = None
        self.change_attempted = False
        self.restoration_complete = False
        self._steps: list[dict[str, Any]] = []

    @property
    def evidence(self) -> tuple[dict[str, Any], ...]:
        """Return snapshots; callers cannot mutate authoritative history."""
        return tuple(deepcopy(step["evidence"]) for step in self._steps)

    def _record(self, phase: str, document: dict[str, Any]) -> None:
        self._steps.append({"phase": phase, "evidence": deepcopy(document)})

    def inspect(self) -> dict[str, Any]:
        return inspect_systemd(self.command, self.service, self.allowed)

    def set_active(self, active: bool) -> dict[str, Any]:
        if self.initial is not None or self.changed_to is not None:
            raise DeploymentError("service transaction already changed state")
        before = self.inspect()
        self._record("initial_inspection", before)
        parsed = before["parsed_result"]
        if parsed is None:
            raise DeploymentError("service state is unavailable")
        self.initial = cast(str, parsed["active_state"])
        if self.initial not in {"active", "inactive"}:
            raise DeploymentError("service initial state is not safely restorable")
        desired = "active" if active else "inactive"
        if self.initial == desired:
            raise DeploymentError("refusing to record an unchanged service as harness-modified")
        change_contract: dict[str, object] = {
            "service": self.service,
            "initial_state": self.initial,
            "requested_state": desired,
            "changed_by_harness": True,
        }
        # Once the mutation is invoked, its effect is uncertain until a later
        # inspection proves otherwise.  A timeout or lost response is not
        # evidence that systemd left the service unchanged.
        self.change_attempted = True
        self.changed_to = desired
        self.requested_state = desired
        document = self.command.run(("systemd-set", self.service, desired), change_contract)
        if document["outcome"] == "completed":
            document["mutation_performed"] = True
            validate_provider_evidence(document)
        self._record("change_command", document)
        if document["outcome"] != "completed":
            with suppress(DeploymentError):
                self._record("post_change_inspection", self.inspect())
            return document
        after_document = self.inspect()
        self._record("post_change_inspection", after_document)
        after = after_document["parsed_result"]
        if after is None or after["active_state"] != desired:
            raise DeploymentError("service change was not verified")
        return document

    def restore(self) -> dict[str, Any]:
        if self.restoration_complete:
            raise DeploymentError("service transaction restoration is already complete")
        if self.initial is None or (self.changed_to is None and not self.change_attempted):
            raise DeploymentError("service was not changed by this harness transaction")
        current_document = self.inspect()
        self._record("pre_restore_inspection", current_document)
        current = current_document["parsed_result"]
        if current is None:
            raise DeploymentError("service state unavailable before restoration")
        if current["active_state"] == self.initial:
            current_document["cleanup_verified"] = True
            self.changed_to = None
            self.restoration_complete = True
            return current_document
        if self.changed_to is not None and current["active_state"] != self.changed_to:
            raise DeploymentError("service state drift prevents restoration")
        restore_contract: dict[str, object] = {
            "service": self.service,
            "expected_current_state": self.changed_to,
            "restore_to": self.initial,
            "changed_by_harness": True,
        }
        document = self.command.run(("systemd-set", self.service, self.initial), restore_contract)
        if document["outcome"] == "completed":
            document["mutation_performed"] = True
            validate_provider_evidence(document)
        self._record("restore_command", document)
        if document["outcome"] != "completed":
            document["cleanup_verified"] = False
            validate_provider_evidence(document)
            return document
        after_document = self.inspect()
        self._record("post_restore_inspection", after_document)
        after = after_document["parsed_result"]
        if after is None or after["active_state"] != self.initial:
            raise DeploymentError("service restoration was not verified")
        self.changed_to = None
        self.restoration_complete = True
        return document

    def transaction_document(self) -> dict[str, Any]:
        if self.initial is None:
            raise DeploymentError("service transaction has no initial state")
        inspections = [step for step in self._steps if step["phase"] == "post_change_inspection"]
        observed = (
            inspections[-1]["evidence"]["parsed_result"]["active_state"]
            if inspections and inspections[-1]["evidence"]["parsed_result"] is not None
            else None
        )
        if not self.change_attempted:
            effect = "not_attempted"
        elif observed == self.initial:
            effect = "confirmed_unchanged"
        elif observed == self.requested_state:
            effect = "confirmed_changed"
        else:
            effect = "uncertain"
        restore_failures = [
            step["evidence"]["cause"]
            for step in self._steps
            if step["phase"] == "restore_command" and step["evidence"]["outcome"] == "blocked"
        ]
        cleanup_outcome = (
            "verified"
            if self.restoration_complete
            else "failed"
            if restore_failures
            else "required"
        )
        document = {
            "schema_version": 1,
            "evidence_type": "systemd_restoration_transaction",
            "host": self.command.host,
            "plan_sha256": self.command.plan_sha256,
            "service": self.service,
            "allowed_services": sorted(self.allowed),
            "initial_state": self.initial,
            "requested_state": self.requested_state,
            "mutation_attempted": self.change_attempted,
            "mutation_effect": effect,
            "restoration_required": self.change_attempted and not self.restoration_complete,
            "cleanup_outcome": cleanup_outcome,
            "cleanup_failure_causes": restore_failures,
            "completed": self.restoration_complete,
            "steps": deepcopy(self._steps),
        }
        validate_systemd_transaction(document)
        return deepcopy(document)


def validate_systemd_transaction(document: dict[str, Any]) -> None:
    validate_document(document, "systemd-transaction-evidence.schema.json")
    if document["service"] not in document["allowed_services"]:
        raise DeploymentError("systemd transaction service is outside its allowlist")
    steps = document["steps"]
    phases = [step["phase"] for step in steps]
    if phases[:2] != ["initial_inspection", "change_command"]:
        raise DeploymentError("systemd transaction steps are missing, duplicated, or reordered")
    index = 2
    if index < len(phases) and phases[index] == "post_change_inspection":
        index += 1
    while index < len(phases):
        if phases[index] != "pre_restore_inspection":
            raise DeploymentError("systemd transaction steps are missing, duplicated, or reordered")
        index += 1
        if index < len(phases) and phases[index] == "restore_command":
            index += 1
            if index < len(phases) and phases[index] == "post_restore_inspection":
                index += 1
    previous: datetime | None = None
    for step in steps:
        evidence = step["evidence"]
        validate_provider_evidence(evidence)
        if (
            evidence["provider_type"] != "systemd"
            or evidence["host"] != document["host"]
            or evidence["plan_sha256"] != document["plan_sha256"]
        ):
            raise DeploymentError("systemd transaction step identity mismatch")
        if evidence["contract"]["service"] != document["service"]:
            raise DeploymentError("systemd transaction service mismatch")
        if (
            "allowed_services" in evidence["contract"]
            and evidence["contract"]["allowed_services"] != document["allowed_services"]
        ):
            raise DeploymentError("systemd transaction allowlist mismatch")
        started = _parse_utc(evidence["started_utc"])
        completed = _parse_utc(evidence["completed_utc"])
        if started > completed or (previous is not None and started < previous):
            raise DeploymentError("systemd transaction timestamps are out of order")
        previous = completed
    initial = steps[0]["evidence"]["parsed_result"]
    if initial is None or initial["active_state"] != document["initial_state"]:
        raise DeploymentError("systemd transaction initial state mismatch")
    change = steps[1]["evidence"]
    if change["mutation_performed"] is False:
        raise DeploymentError("systemd mutation command falsely claims no mutation")
    post = next((s["evidence"] for s in steps if s["phase"] == "post_change_inspection"), None)
    observed = post["parsed_result"]["active_state"] if post and post["parsed_result"] else None
    expected_effect = (
        "confirmed_unchanged"
        if observed == document["initial_state"]
        else "confirmed_changed"
        if observed == document["requested_state"]
        else "uncertain"
    )
    if not document["mutation_attempted"] or document["mutation_effect"] != expected_effect:
        raise DeploymentError("systemd mutation effect contradicts retained steps")
    verified = (
        phases[-1] in {"pre_restore_inspection", "post_restore_inspection"}
        and steps[-1]["evidence"]["parsed_result"] is not None
        and steps[-1]["evidence"]["parsed_result"]["active_state"] == document["initial_state"]
    )
    restore_failures = [
        step["evidence"]["cause"]
        for step in steps
        if step["phase"] == "restore_command" and step["evidence"]["outcome"] == "blocked"
    ]
    expected_cleanup = "verified" if verified else "failed" if restore_failures else "required"
    if (
        document["completed"] != verified
        or document["cleanup_outcome"] != expected_cleanup
        or document["cleanup_failure_causes"] != restore_failures
        or document["restoration_required"] != (not verified)
    ):
        raise DeploymentError("systemd cleanup outcome contradicts retained steps")


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeploymentError("systemd transaction timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise DeploymentError("systemd transaction timestamp lacks an offset")
    return parsed.astimezone(UTC)


def _text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _block(document: dict[str, Any], cause: str) -> None:
    document["outcome"] = "blocked"
    document["cause"] = cause
