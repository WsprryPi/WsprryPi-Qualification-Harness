"""Durable correction contracts for read-only actual-host preflight evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from wsprrypi_qualification.manifests import build_manifest, render_manifest
from wsprrypi_qualification.offline import validate_document


class ActualHostEvidenceError(ValueError):
    """Actual-host preflight evidence is missing or contradictory."""


HOSTS = ("pi@wspr4.local", "pi@wspr5.local")
REMOTE_COMMANDS = (
    ("hostname",),
    ("uname", "-a"),
    ("cat", "/etc/os-release"),
    ("date", "-u"),
    ("date",),
    ("uptime",),
    ("who",),
    ("ps", "-eo", "pid,ppid,user,etimes,stat,comm,args"),
    (
        "systemctl",
        "show",
        "wsprrypi.service",
        "wspq-capability-helper.service",
        "--property=Id,LoadState,ActiveState,SubState,FragmentPath,MainPID,ExecMainStartTimestampMonotonic",
        "--no-pager",
    ),
    ("df", "-Pk", "/", "/home"),
    ("git", "-C", "/home/pi/WsprryPi", "status", "--short", "--branch"),
    ("git", "-C", "/home/pi/WsprryPi", "rev-parse", "HEAD"),
    ("git", "-C", "/home/pi/WsprryPi", "submodule", "status"),
    ("ls", "-la", "/var/lib/wsprrypi-qualification"),
    ("ls", "-la", "/opt/wsprrypi-qualification"),
    ("ls", "-la", "/etc/wsprrypi-qualification"),
)


def expected_arguments(ssh_path: str = "/usr/bin/ssh") -> list[list[str]]:
    return [
        [
            ssh_path,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "-T",
            host,
            *command,
        ]
        for host in HOSTS
        for command in REMOTE_COMMANDS
    ]


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_runtime_authorization(
    document: dict[str, Any],
    command_records: list[dict[str, Any]],
    original_root: Path,
    separate_contract: dict[str, Any],
) -> None:
    validate_document(document, "actual-host-runtime-authorization.schema.json")
    if (
        not document["retrospective_correction"]
        or document["authorization_timing"] != "retrospective_record_of_prior_authorization"
    ):
        raise ActualHostEvidenceError("runtime authorization timing is mislabeled")
    contract = document["command_contract"]
    if contract != separate_contract:
        raise ActualHostEvidenceError("embedded and retained command contracts disagree")
    bindings = {
        "requested_plan_sha256": original_root / "requested-plan.json",
        "collector_sha256": original_root / "boundary1_collect.py",
        "command_records_sha256": original_root / "boundary1-command-records.json",
    }
    if any(document[field] != file_sha256(path) for field, path in bindings.items()):
        raise ActualHostEvidenceError("runtime authorization artifact binding is invalid")
    expected = expected_arguments(contract["ssh_executable"])
    if contract["hosts"] != list(HOSTS) or contract["remote_commands"] != [
        list(item) for item in REMOTE_COMMANDS
    ]:
        raise ActualHostEvidenceError("runtime authorization command contract changed")
    if contract["command_count"] != 32 or contract["supervisor_timeout_s"] != 10:
        raise ActualHostEvidenceError("runtime authorization timeout or count changed")
    if contract["command_contract_sha256"] != canonical_sha256(expected):
        raise ActualHostEvidenceError("runtime authorization command digest is invalid")
    actual = [record["arguments"] for record in command_records]
    if actual != expected or len(command_records) != contract["command_count"]:
        raise ActualHostEvidenceError("command records do not exactly match authorization")
    if any(
        record["host"] != arguments[6]
        for record, arguments in zip(command_records, expected, strict=True)
    ):
        raise ActualHostEvidenceError("command record host contradicts its argument vector")


def validate_host_identity_correction(document: dict[str, Any]) -> None:
    validate_document(document, "actual-host-identity-correction.schema.json")
    expected = (("pi@wspr4.local", "wspr4"), ("pi@wspr5.local", "wspr5"))
    if [(host["ssh_destination"], host["hostname_observed"]) for host in document["hosts"]] != list(
        expected
    ):
        raise ActualHostEvidenceError("host identity mapping is missing, duplicated, or reordered")
    for host in document["hosts"]:
        if host["server_key_fingerprint_at_run"] is None and host["exact_host_identity_verified"]:
            raise ActualHostEvidenceError(
                "host identity cannot be verified without run-time key evidence"
            )
        if host["hostname_observed"] not in {"wspr4", "wspr5"}:
            raise ActualHostEvidenceError("unexpected observed hostname")


def _manifest_artifacts(root: Path) -> list[dict[str, Any]]:
    manifest = root / "SHA256SUMS"
    if not manifest.is_file() or manifest.is_symlink():
        raise ActualHostEvidenceError("bundle manifest is missing or unsafe")
    expected_text = render_manifest(build_manifest(root))
    if manifest.read_text(encoding="utf-8") != expected_text:
        raise ActualHostEvidenceError("bundle manifest is incomplete or nondeterministic")
    return [
        {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
        for item in build_manifest(root)
    ]


def verify_original_bundle(reference: dict[str, Any], original: Path) -> None:
    validate_document(reference, "actual-host-original-bundle-reference.schema.json")
    original = original.resolve()
    if Path(reference["original_path"]).resolve() != original:
        raise ActualHostEvidenceError("original bundle path was substituted")
    manifest = original / "SHA256SUMS"
    if (
        manifest.stat().st_size != reference["manifest"]["size_bytes"]
        or file_sha256(manifest) != reference["manifest"]["sha256"]
    ):
        raise ActualHostEvidenceError("original manifest identity changed")
    artifacts = _manifest_artifacts(original)
    if reference["artifacts"] != artifacts:
        raise ActualHostEvidenceError("original artifact reference is incomplete or substituted")
    requested = json.loads((original / "requested-plan.json").read_text(encoding="utf-8"))
    result = json.loads((original / "result.json").read_text(encoding="utf-8"))
    if (
        reference["original_run_id"] != original.name
        or not original.name.endswith(f"-{requested['test_id']}")
        or requested["controller_revision"] != reference["original_controller_revision"]
        or result["status"] != reference["original_status"]
    ):
        raise ActualHostEvidenceError("original bundle identity or classification changed")


CORRECTION_FILES_V2 = {
    "correction-request.json",
    "original-bundle-reference.json",
    "prior-correction-reference.json",
    "runtime-authorization.json",
    "command-contract.json",
    "host-identity-correction.json",
    "controller-openssh.json",
    "corrected-result.json",
    "correction-log.jsonl",
    "SHA256SUMS",
}
CORRECTION_FILES_V3 = (CORRECTION_FILES_V2 - {"correction-log.jsonl"}) | {"correction-log.json"}
OPENSSH_HISTORICAL_LIMIT = (
    "Current local identity only; original records retain this path but not its run-time hash."
)


def _verify_prior_reference(reference: dict[str, Any], prior: Path) -> None:
    validate_document(reference, "actual-host-prior-correction-reference.schema.json")
    prior = prior.resolve()
    if Path(reference["path"]).resolve() != prior:
        raise ActualHostEvidenceError("prior correction path was substituted")
    if reference["run_id"] != prior.name:
        raise ActualHostEvidenceError("prior correction run identity was substituted")
    manifest = prior / "SHA256SUMS"
    if (
        reference["manifest"]["size_bytes"] != manifest.stat().st_size
        or reference["manifest"]["sha256"] != file_sha256(manifest)
        or reference["artifacts"] != _manifest_artifacts(prior)
    ):
        raise ActualHostEvidenceError("prior correction identity changed")


def validate_actual_host_correction_bundle(
    correction_root: Path,
    original_root: Path,
    prior_correction_root: Path | None = None,
) -> None:
    correction_root = correction_root.resolve()
    original_root = original_root.resolve()
    if prior_correction_root is None:
        raise ActualHostEvidenceError("superseding correction requires its prior correction")
    prior_correction_root = prior_correction_root.resolve()
    request = json.loads((correction_root / "correction-request.json").read_text(encoding="utf-8"))
    validate_document(request, "actual-host-correction-request.schema.json")
    expected_files = CORRECTION_FILES_V3 if request["schema_version"] >= 3 else CORRECTION_FILES_V2
    actual_files = {
        path.relative_to(correction_root).as_posix()
        for path in correction_root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_files != expected_files or any(
        path.is_symlink() for path in correction_root.rglob("*")
    ):
        raise ActualHostEvidenceError("correction artifact set is incomplete or unexpected")
    _manifest_artifacts(correction_root)
    if request["correction_run_id"] != correction_root.name or request[
        "validator_sha256"
    ] != file_sha256(Path(__file__)):
        raise ActualHostEvidenceError("correction request identity is invalid")
    if request["schema_version"] == 3 and request["supersedes"] != [
        "unvalidated_controller_openssh_evidence",
        "unbound_correction_chronology",
    ]:
        raise ActualHostEvidenceError("third correction supersedes unexpected claims")
    if request["schema_version"] == 4 and request["supersedes"] != [
        "unbound_controller_openssh_chronology"
    ]:
        raise ActualHostEvidenceError("fourth correction supersedes unexpected claims")
    original_reference = json.loads(
        (correction_root / "original-bundle-reference.json").read_text(encoding="utf-8")
    )
    verify_original_bundle(original_reference, original_root)
    prior_reference = json.loads(
        (correction_root / "prior-correction-reference.json").read_text(encoding="utf-8")
    )
    _verify_prior_reference(prior_reference, prior_correction_root)
    contract = json.loads((correction_root / "command-contract.json").read_text(encoding="utf-8"))
    authorization = json.loads(
        (correction_root / "runtime-authorization.json").read_text(encoding="utf-8")
    )
    records = json.loads(
        (original_root / "boundary1-command-records.json").read_text(encoding="utf-8")
    )
    validate_runtime_authorization(authorization, records, original_root, contract)
    identity = json.loads(
        (correction_root / "host-identity-correction.json").read_text(encoding="utf-8")
    )
    validate_host_identity_correction(identity)
    openssh = json.loads((correction_root / "controller-openssh.json").read_text(encoding="utf-8"))
    if request["schema_version"] >= 3:
        validate_controller_openssh(openssh)
    elif (
        openssh["path"] != "/usr/bin/ssh"
        or openssh["historical_identity_limit"] != OPENSSH_HISTORICAL_LIMIT
        or openssh["return_code"] != 0
    ):
        raise ActualHostEvidenceError("controller OpenSSH evidence overstates its meaning")
    result = json.loads((correction_root / "corrected-result.json").read_text(encoding="utf-8"))
    validate_document(result, "actual-host-corrected-result.schema.json")
    if request["schema_version"] >= 3:
        log = json.loads((correction_root / "correction-log.json").read_text(encoding="utf-8"))
        validate_correction_log(log, request, result)
        if request["schema_version"] == 4:
            validate_openssh_correction_chronology(openssh, request, log)
    else:
        lines = (correction_root / "correction-log.jsonl").read_text(encoding="utf-8").splitlines()
        if len(lines) != 1 or json.loads(lines[0])["outcome"] != "fixture_blocked":
            raise ActualHostEvidenceError("correction chronology is incomplete or contradictory")


def _utc_timestamp(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ActualHostEvidenceError("timestamp is not canonical UTC Z form")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ActualHostEvidenceError("timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ActualHostEvidenceError("timestamp is not UTC")
    return parsed


def validate_controller_openssh(document: dict[str, Any]) -> None:
    validate_document(document, "actual-host-controller-openssh.schema.json")
    started = _utc_timestamp(document["started_utc"])
    completed = _utc_timestamp(document["completed_utc"])
    if started > completed:
        raise ActualHostEvidenceError("controller OpenSSH timestamps are reversed")
    if document["historical_identity_limit"] != OPENSSH_HISTORICAL_LIMIT or not document[
        "stderr"
    ].startswith("OpenSSH_"):
        raise ActualHostEvidenceError("controller OpenSSH evidence overstates its meaning")
    path = Path(document["path"])
    if path.exists() and (
        not path.is_file()
        or path.is_symlink()
        or path.stat().st_size != document["size_bytes"]
        or file_sha256(path) != document["sha256"]
    ):
        raise ActualHostEvidenceError("current controller OpenSSH identity changed")


def validate_correction_log(
    document: dict[str, Any], request: dict[str, Any], result: dict[str, Any]
) -> None:
    validate_document(document, "actual-host-correction-log.schema.json")
    timestamp = _utc_timestamp(document["timestamp_utc"])
    created = _utc_timestamp(request["created_utc"])
    if timestamp != created or document["correction_run_id"] != request["correction_run_id"]:
        raise ActualHostEvidenceError("correction chronology contradicts its request")
    if (
        document["outcome"] != result["status"]
        or not request["retrospective"]
        or request["host_connections_authorized"]
    ):
        raise ActualHostEvidenceError("correction log contradicts its result or authorization")


def validate_openssh_correction_chronology(
    openssh: dict[str, Any], request: dict[str, Any], log: dict[str, Any]
) -> None:
    created = _utc_timestamp(request["created_utc"])
    log_time = _utc_timestamp(log["timestamp_utc"])
    started = _utc_timestamp(openssh["started_utc"])
    completed = _utc_timestamp(openssh["completed_utc"])
    if log_time != created:
        raise ActualHostEvidenceError("correction log does not equal request creation time")
    if not created <= started <= completed <= created + timedelta(minutes=10):
        raise ActualHostEvidenceError("controller OpenSSH evidence is outside correction window")
    if completed - started > timedelta(seconds=30):
        raise ActualHostEvidenceError("controller OpenSSH invocation exceeded correction bound")
