"""Phase 6 read-only actual-host preflight for tone and CW-family qualification."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from wsprrypi_qualification.manifests import build_manifest, render_manifest, write_manifest
from wsprrypi_qualification.offline import load_json_document, validate_document, write_json_new


class CwHostPreflightError(RuntimeError):
    """The Phase 6 request or retained evidence is unsafe or contradictory."""


SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_@%+=:,./-]+$")
SAFE_DESTINATION = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+$")
SAFE_UNIT = re.compile(r"^[A-Za-z0-9_.@-]+\.service$")
SAFE_BINARY = re.compile(r"^[A-Za-z0-9_.+-]+$")
PROHIBITED_TOKENS = {"sudo", "su", "doas", "sh", "bash", "zsh", "dash", "env"}
PROHIBITED_COMMANDS = {
    "apt",
    "apt-get",
    "dnf",
    "yum",
    "pacman",
    "dkms",
    "modprobe",
    "insmod",
    "rmmod",
    "systemctl-start",
    "systemctl-stop",
    "systemctl-restart",
    "tee",
    "dd",
    "mount",
    "umount",
    "reboot",
    "shutdown",
    "gpio",
    "gpioset",
    "raspi-gpio",
    "pinctrl",
}


@dataclass(frozen=True)
class Probe:
    probe_id: str
    arguments: tuple[str, ...]
    required: bool = True


class Runner(Protocol):
    def run(self, arguments: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]: ...


class SubprocessRunner:
    """Structured local process runner; it never invokes a shell."""

    def run(self, arguments: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
            shell=False,
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _gate_d_satisfied(plan: dict[str, Any]) -> bool:
    """Return whether the candidate clears the RP1-specific Gate D input."""
    return plan["gate_d_status"] in {"complete", "not_applicable"}


def _validate_token(token: str) -> None:
    if not token or not SAFE_TOKEN.fullmatch(token) or token in PROHIBITED_TOKENS:
        raise CwHostPreflightError(f"unsafe remote command token: {token!r}")
    if any(character in token for character in ";|&`$<>(){}[]\\\n\r\t"):
        raise CwHostPreflightError(f"shell syntax is prohibited: {token!r}")


def validate_probe(probe: Probe) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", probe.probe_id):
        raise CwHostPreflightError("probe ID is unsafe")
    if not probe.arguments:
        raise CwHostPreflightError("empty remote command is prohibited")
    for token in probe.arguments:
        _validate_token(token)
    command = probe.arguments[0]
    if command in PROHIBITED_COMMANDS:
        raise CwHostPreflightError(f"mutating remote command is prohibited: {command}")
    if command == "systemctl" and any(
        item in {"start", "stop", "restart", "reload", "enable", "disable", "mask", "unmask"}
        for item in probe.arguments[1:]
    ):
        raise CwHostPreflightError("mutating systemctl action is prohibited")
    if command == "git" and "--no-optional-locks" not in probe.arguments:
        raise CwHostPreflightError("git inspection must disable optional locks")
    if any(token.startswith(("/sys/", "/proc/sys/", "/dev/")) for token in probe.arguments):
        raise CwHostPreflightError("unsafe host path is outside the read-only inspection surface")


def probes_for(host: dict[str, Any]) -> tuple[Probe, ...]:
    repo = host["repository_path"]
    if not re.fullmatch(r"/[A-Za-z0-9_.+/-]+", repo) or ".." in Path(repo).parts:
        raise CwHostPreflightError("repository path is unsafe")
    probes = [
        Probe("hostname", ("hostname",)),
        Probe("kernel", ("uname", "-srm")),
        Probe("os-release", ("cat", "/etc/os-release")),
        Probe("model", ("cat", "/proc/device-tree/model")),
        Probe("revision", ("od", "-An", "-tx1", "/proc/device-tree/system/linux,revision")),
        Probe("identity", ("id",)),
        Probe(
            "clock",
            (
                "timedatectl",
                "show",
                "--property=NTPSynchronized",
                "--property=TimeUSec",
                "--property=Timezone",
            ),
        ),
        Probe("processes", ("ps", "-eo", "pid,ppid,user,etimes,stat,comm")),
        Probe("modules", ("cat", "/proc/modules")),
        Probe("repo-revision", ("git", "--no-optional-locks", "-C", repo, "rev-parse", "HEAD")),
        Probe(
            "repo-status",
            (
                "git",
                "--no-optional-locks",
                "-C",
                repo,
                "status",
                "--short",
                "--branch",
                "--untracked-files=no",
            ),
        ),
    ]
    for binary in host["required_binaries"]:
        if not SAFE_BINARY.fullmatch(binary):
            raise CwHostPreflightError(f"unsafe binary name: {binary!r}")
        probes.append(Probe(f"binary-{binary.lower().replace('.', '-')}", ("which", binary)))
    for unit in host["inspect_services"]:
        if not SAFE_UNIT.fullmatch(unit):
            raise CwHostPreflightError(f"unsafe service unit: {unit!r}")
        probes.append(
            Probe(
                f"service-{unit.lower().replace('.', '-')}",
                (
                    "systemctl",
                    "show",
                    unit,
                    "--property=Id,LoadState,ActiveState,SubState,FragmentPath,MainPID",
                    "--no-pager",
                ),
                required=False,
            )
        )
    for probe in probes:
        validate_probe(probe)
    if len({probe.probe_id for probe in probes}) != len(probes):
        raise CwHostPreflightError("probe IDs are not unique")
    return tuple(probes)


def _ssh_arguments(ssh: Path, host: dict[str, Any], probe: Probe) -> list[str]:
    destination = host["ssh_destination"]
    alias = host["host_key_alias"]
    if not SAFE_DESTINATION.fullmatch(destination) or not re.fullmatch(r"[A-Za-z0-9_.-]+", alias):
        raise CwHostPreflightError("SSH destination or host-key alias is unsafe")
    arguments = [
        str(ssh),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"HostKeyAlias={alias}",
        "-T",
        destination,
        *probe.arguments,
    ]
    if any(not isinstance(item, str) or "\x00" in item for item in arguments):
        raise CwHostPreflightError("invalid SSH argument")
    return arguments


def _bounded(value: str) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= 65_536:
        return value, False
    return encoded[:65_536].decode("utf-8", errors="replace"), True


def _evaluate(host: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {record["probe_id"]: record for record in records}
    checks: list[dict[str, Any]] = []

    def add(check_id: str, expected: str, observed: str, outcome: str, diagnostic: str) -> None:
        checks.append(
            {
                "check_id": check_id,
                "expected": expected,
                "observed": observed,
                "outcome": outcome,
                "diagnostic": diagnostic,
            }
        )

    for probe in probes_for(host):
        record = by_id[probe.probe_id]
        if (
            record["outcome"] != "completed"
            or record["return_code"] != 0
            or (probe.required and not record["stdout"].strip("\x00\n "))
        ):
            add(
                probe.probe_id,
                "successful read-only observation",
                record["outcome"],
                "failed" if probe.required else "blocked",
                "probe did not return usable non-empty evidence",
            )
    failed_probe_ids = {item["check_id"] for item in checks}
    if "hostname" in failed_probe_ids:
        return checks
    hostname = by_id["hostname"]["stdout"].strip()
    add(
        "hostname",
        host["expected_hostname"],
        hostname,
        "passed" if hostname == host["expected_hostname"] else "failed",
        "OpenSSH strict known-host validation and hostname must both match",
    )
    model = by_id["model"]["stdout"].rstrip("\x00\n")
    add(
        "model",
        host["expected_model"],
        model,
        "passed" if model == host["expected_model"] else "failed",
        "candidate hardware model mismatch",
    )
    revision_hex = "".join(by_id["revision"]["stdout"].split())
    expected_revision = host["expected_revision_hex"].lower()
    add(
        "revision",
        expected_revision,
        revision_hex,
        "passed" if revision_hex == expected_revision else "failed",
        "candidate board revision mismatch",
    )
    revision = by_id["repo-revision"]["stdout"].strip()
    add(
        "repository-revision",
        host["expected_repository_revision"],
        revision,
        "passed" if revision == host["expected_repository_revision"] else "failed",
        "target repository revision is not the frozen candidate",
    )
    status = by_id["repo-status"]["stdout"].splitlines()
    dirty = [line for line in status[1:] if line.strip()]
    add(
        "repository-clean",
        "no tracked changes",
        "clean" if not dirty else "tracked changes present",
        "passed" if not dirty else "blocked",
        "existing maintainer work is a non-interference blocker",
    )
    identity = by_id["identity"]["stdout"]
    for group in host["required_groups"]:
        present = re.search(rf"(?:^|[ ,(]){re.escape(group)}(?:[ ,)]|$)", identity) is not None
        add(
            f"group-{group}",
            f"member of {group}",
            "present" if present else "absent",
            "passed" if present else "blocked",
            "required access group is unavailable",
        )
    for binary in host["required_binaries"]:
        record = by_id[f"binary-{binary.lower().replace('.', '-')}"]
        present = record["return_code"] == 0 and record["stdout"].strip().startswith("/")
        add(
            f"binary-{binary}",
            "absolute executable path",
            record["stdout"].strip() or "missing",
            "passed" if present else "blocked",
            "required executable resolved" if present else "required executable is unavailable",
        )
    for probe_id, label in (
        ("kernel", "kernel identity"),
        ("os-release", "operating-system identity"),
        ("clock", "clock synchronization state"),
        ("modules", "loaded-module snapshot"),
    ):
        if probe_id not in failed_probe_ids:
            add(
                probe_id,
                "read-only observation retained",
                by_id[probe_id]["stdout"].strip()[:4096],
                "passed",
                f"{label} was observed without mutation",
            )
    for unit in host["inspect_services"]:
        probe_id = f"service-{unit.lower().replace('.', '-')}"
        if probe_id not in failed_probe_ids:
            add(
                probe_id,
                "read-only service state retained",
                by_id[probe_id]["stdout"].strip()[:4096],
                "passed",
                "service state was inspected but not changed",
            )
    process_text = by_id["processes"]["stdout"].lower()
    for forbidden in host["conflicting_process_names"]:
        present = forbidden.lower() in process_text
        add(
            f"conflict-{forbidden}",
            "not running",
            "running" if present else "not observed",
            "blocked" if present else "passed",
            "active process could contaminate later live qualification",
        )
    return checks


def run_cw_actual_host_preflight(
    plan_path: Path,
    output_parent: Path,
    *,
    ssh_path: Path,
    confirmation_sha256: str,
    enabled: bool,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Execute the sealed read-only probe set and retain an immutable evidence bundle."""
    plan = load_json_document(plan_path, "cw-actual-host-preflight-plan.schema.json")
    plan_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    if not enabled or confirmation_sha256 != plan_sha256:
        raise CwHostPreflightError(
            "exact plan digest and explicit read-only enable flag are required"
        )
    if ssh_path.is_symlink() or not ssh_path.is_file() or not ssh_path.is_absolute():
        raise CwHostPreflightError("SSH executable must be an absolute regular non-symlink file")
    output_parent = output_parent.resolve()
    if not output_parent.is_dir():
        raise CwHostPreflightError("output parent must already exist")
    root = output_parent / f"{plan['run_id']}-cw-actual-host-preflight"
    if root.exists():
        raise CwHostPreflightError("refusing to reuse a preflight evidence directory")
    root.mkdir()
    active_runner = runner or SubprocessRunner()
    write_json_new(
        root / "resolved-plan.json", plan, schema_name="cw-actual-host-preflight-plan.schema.json"
    )
    contract = {
        "schema_version": 1,
        "evidence_type": "cw_actual_host_command_contract",
        "plan_sha256": plan_sha256,
        "read_only": True,
        "shell": False,
        "timeout_seconds": plan["timeout_seconds"],
        "commands": [
            {
                "host_id": host["host_id"],
                "probe_id": probe.probe_id,
                "arguments": _ssh_arguments(ssh_path, host, probe),
            }
            for host in plan["hosts"]
            for probe in probes_for(host)
        ],
    }
    contract["command_contract_sha256"] = _sha256_json(contract["commands"])
    write_json_new(root / "command-contract.json", contract)
    all_records: list[dict[str, Any]] = []
    host_results: list[dict[str, Any]] = []
    for host in plan["hosts"]:
        records: list[dict[str, Any]] = []
        for probe in probes_for(host):
            arguments = _ssh_arguments(ssh_path, host, probe)
            started_utc = _utc_now()
            started = time.monotonic()
            try:
                completed = active_runner.run(arguments, float(plan["timeout_seconds"]))
                stdout, stdout_truncated = _bounded(completed.stdout)
                stderr, stderr_truncated = _bounded(completed.stderr)
                record = {
                    "host_id": host["host_id"],
                    "probe_id": probe.probe_id,
                    "arguments": arguments,
                    "started_utc": started_utc,
                    "completed_utc": _utc_now(),
                    "duration_seconds": time.monotonic() - started,
                    "outcome": "completed",
                    "return_code": completed.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                }
            except subprocess.TimeoutExpired as error:
                record = {
                    "host_id": host["host_id"],
                    "probe_id": probe.probe_id,
                    "arguments": arguments,
                    "started_utc": started_utc,
                    "completed_utc": _utc_now(),
                    "duration_seconds": time.monotonic() - started,
                    "outcome": "timeout",
                    "return_code": None,
                    "stdout": str(error.stdout or ""),
                    "stderr": str(error.stderr or ""),
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                }
            records.append(record)
            all_records.append(record)
        checks = _evaluate(host, records)
        host_results.append({"host_id": host["host_id"], "checks": checks})
    write_json_new(root / "command-records.json", {"records": all_records})
    outcomes = [check["outcome"] for host in host_results for check in host["checks"]]
    blockers = list(plan["known_blockers"])
    if not _gate_d_satisfied(plan):
        blockers.append("rp1-gpclk-dkms-gate-d-incomplete")
    if any(outcome in {"failed", "blocked"} for outcome in outcomes):
        blockers.append("one-or-more-preflight-checks-not-passed")
    result = {
        "schema_version": 1,
        "evidence_type": "cw_actual_host_preflight_result",
        "run_id": plan["run_id"],
        "created_utc": _utc_now(),
        "read_only": True,
        "host_connections_performed": True,
        "rf_or_hardware_operation_performed": False,
        "qualification_claim": False,
        "host_results": host_results,
        "blockers": sorted(set(blockers)),
        "overall_outcome": "ready" if not blockers else "blocked",
        "next_phase_authorized": False,
    }
    validate_document(result, "cw-actual-host-preflight-result.schema.json")
    write_json_new(
        root / "result.json", result, schema_name="cw-actual-host-preflight-result.schema.json"
    )
    write_manifest(root)
    validate_cw_actual_host_preflight_bundle(root)
    return {"bundle": str(root), **result}


def validate_cw_actual_host_preflight_bundle(root: Path) -> dict[str, Any]:
    root = root.resolve()
    expected = {
        "resolved-plan.json",
        "command-contract.json",
        "command-records.json",
        "result.json",
        "SHA256SUMS",
    }
    actual = {path.name for path in root.iterdir() if path.is_file() and not path.is_symlink()}
    if actual != expected or any(path.is_symlink() for path in root.iterdir()):
        raise CwHostPreflightError("preflight artifact set is incomplete or unexpected")
    if (root / "SHA256SUMS").read_text(encoding="utf-8") != render_manifest(build_manifest(root)):
        raise CwHostPreflightError("preflight manifest is incomplete or invalid")
    plan = load_json_document(
        root / "resolved-plan.json", "cw-actual-host-preflight-plan.schema.json"
    )
    result = load_json_document(root / "result.json", "cw-actual-host-preflight-result.schema.json")
    contract = json.loads((root / "command-contract.json").read_text(encoding="utf-8"))
    records_doc = json.loads((root / "command-records.json").read_text(encoding="utf-8"))
    expected_commands = contract.get("commands")
    if not isinstance(expected_commands, list) or contract.get(
        "command_contract_sha256"
    ) != _sha256_json(expected_commands):
        raise CwHostPreflightError("command contract digest is invalid")
    records = records_doc.get("records")
    if not isinstance(records, list) or [item.get("arguments") for item in records] != [
        item["arguments"] for item in expected_commands
    ]:
        raise CwHostPreflightError("executed commands do not exactly match the sealed contract")
    for host in plan["hosts"]:
        for probe in probes_for(host):
            validate_probe(probe)
    recomputed_hosts = []
    for host in plan["hosts"]:
        host_records = [item for item in records if item.get("host_id") == host["host_id"]]
        expected_ids = [probe.probe_id for probe in probes_for(host)]
        if [item.get("probe_id") for item in host_records] != expected_ids:
            raise CwHostPreflightError("probe identities are missing, duplicated, or reordered")
        if any(
            item.get("outcome") not in {"completed", "timeout"}
            or not isinstance(item.get("duration_seconds"), (int, float))
            or item["duration_seconds"] < 0
            or item["duration_seconds"] > plan["timeout_seconds"] + 1
            for item in host_records
        ):
            raise CwHostPreflightError("command outcome or timing evidence is invalid")
        recomputed_hosts.append(
            {"host_id": host["host_id"], "checks": _evaluate(host, host_records)}
        )
    if result["host_results"] != recomputed_hosts:
        raise CwHostPreflightError("retained result does not match recomputed probe semantics")
    expected_blockers = set(plan["known_blockers"])
    if not _gate_d_satisfied(plan):
        expected_blockers.add("rp1-gpclk-dkms-gate-d-incomplete")
    if any(
        check["outcome"] in {"failed", "blocked"}
        for host in recomputed_hosts
        for check in host["checks"]
    ):
        expected_blockers.add("one-or-more-preflight-checks-not-passed")
    if result["blockers"] != sorted(expected_blockers):
        raise CwHostPreflightError("retained blockers do not match recomputed evidence")
    if result["overall_outcome"] == "ready" and (result["blockers"] or not _gate_d_satisfied(plan)):
        raise CwHostPreflightError("ready outcome contradicts its blockers or Gate D status")
    return result
