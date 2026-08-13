import copy
import json
from pathlib import Path

import pytest

from wsprrypi_qualification.actual_host_preflight import (
    HOSTS,
    OPENSSH_HISTORICAL_LIMIT,
    REMOTE_COMMANDS,
    ActualHostEvidenceError,
    canonical_sha256,
    expected_arguments,
    file_sha256,
    validate_actual_host_correction_bundle,
    validate_controller_openssh,
    validate_correction_log,
    validate_host_identity_correction,
    validate_openssh_correction_chronology,
    validate_runtime_authorization,
)
from wsprrypi_qualification.manifests import build_manifest, write_manifest


def authorization() -> dict:
    contract = {
        "hosts": list(HOSTS),
        "remote_commands": [list(item) for item in REMOTE_COMMANDS],
        "ssh_executable": "/usr/bin/ssh",
        "ssh_options": ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-T"],
        "supervisor_timeout_s": 10,
        "command_count": 32,
        "command_contract_sha256": canonical_sha256(expected_arguments()),
    }
    return {
        "schema_version": 1,
        "evidence_type": "actual_host_runtime_authorization_correction",
        "retrospective_correction": True,
        "authorization_timing": "retrospective_record_of_prior_authorization",
        "operator_words": "I authorize those",
        "scope": {
            "boundary": "connection_and_non_interference",
            "authorized": ["bounded_read_only_ssh_commands"],
            "not_authorized": [
                "persistent_helper",
                "service_mutation",
                "gpio",
                "i2c",
                "physical_sdr",
                "wsprrypi_launch",
                "installation",
                "rf",
            ],
        },
        "requested_plan_sha256": "a" * 64,
        "collector_sha256": "b" * 64,
        "command_records_sha256": "c" * 64,
        "command_contract": contract,
    }


def records() -> list[dict]:
    return [{"host": arguments[6], "arguments": arguments} for arguments in expected_arguments()]


def original_files(tmp_path: Path) -> Path:
    root = tmp_path / "original"
    root.mkdir()
    (root / "requested-plan.json").write_text("requested", encoding="utf-8")
    (root / "boundary1_collect.py").write_text("collector", encoding="utf-8")
    (root / "boundary1-command-records.json").write_text("records", encoding="utf-8")
    return root


def bound_authorization(tmp_path: Path) -> tuple[dict, Path]:
    from wsprrypi_qualification.actual_host_preflight import file_sha256

    root = original_files(tmp_path)
    document = authorization()
    document["requested_plan_sha256"] = file_sha256(root / "requested-plan.json")
    document["collector_sha256"] = file_sha256(root / "boundary1_collect.py")
    document["command_records_sha256"] = file_sha256(root / "boundary1-command-records.json")
    return document, root


def test_exact_runtime_authorization_contract_passes(tmp_path: Path) -> None:
    document, root = bound_authorization(tmp_path)
    validate_runtime_authorization(document, records(), root, document["command_contract"])


@pytest.mark.parametrize("mutation", ["host", "order", "extra", "timeout", "option", "digest"])
def test_runtime_authorization_tampering_fails(tmp_path: Path, mutation: str) -> None:
    bound, root = bound_authorization(tmp_path)
    document, command_records = copy.deepcopy(bound), copy.deepcopy(records())
    if mutation == "host":
        document["command_contract"]["hosts"].reverse()
    elif mutation == "order":
        command_records[0], command_records[1] = command_records[1], command_records[0]
    elif mutation == "extra":
        command_records.append(copy.deepcopy(command_records[-1]))
    elif mutation == "timeout":
        document["command_contract"]["supervisor_timeout_s"] = 11
    elif mutation == "option":
        command_records[0]["arguments"][2] = "BatchMode=no"
    else:
        document["command_contract"]["command_contract_sha256"] = "f" * 64
    with pytest.raises((ActualHostEvidenceError, ValueError)):
        validate_runtime_authorization(
            document, command_records, root, document["command_contract"]
        )


def test_retrospective_authorization_cannot_be_mislabeled(tmp_path: Path) -> None:
    document, root = bound_authorization(tmp_path)
    document["retrospective_correction"] = False
    with pytest.raises((ActualHostEvidenceError, ValueError)):
        validate_runtime_authorization(document, records(), root, document["command_contract"])


def test_hostname_observation_cannot_claim_verified_identity() -> None:
    document = {
        "schema_version": 1,
        "evidence_type": "actual_host_identity_correction",
        "retrospective_correction": True,
        "hosts": [
            {
                "ssh_destination": "pi@wspr4.local",
                "hostname_observed": "wspr4",
                "server_key_fingerprint_at_run": None,
                "exact_host_identity_verified": True,
                "identity_outcome": "unresolved",
                "known_hosts_context": [],
            },
            {
                "ssh_destination": "pi@wspr5.local",
                "hostname_observed": "wspr5",
                "server_key_fingerprint_at_run": None,
                "exact_host_identity_verified": False,
                "identity_outcome": "unresolved",
                "known_hosts_context": [],
            },
        ],
    }
    with pytest.raises((ActualHostEvidenceError, ValueError)):
        validate_host_identity_correction(document)


def test_schema_files_are_valid_json() -> None:
    root = Path(__file__).parents[2]
    for name in (
        "actual-host-runtime-authorization.schema.json",
        "actual-host-identity-correction.schema.json",
        "actual-host-original-bundle-reference.schema.json",
    ):
        assert json.loads((root / "schemas" / name).read_text(encoding="utf-8"))


def artifact_records(root: Path) -> list[dict]:
    return [
        {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
        for item in build_manifest(root)
    ]


def write_json(path: Path, document: object) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def correction_graph(tmp_path: Path) -> tuple[Path, Path, Path]:
    correction_effect = "Evidence meaning corrected; preflight remains incomplete and blocked."
    next_step = (
        "Begin a new Boundary 1 preflight after ongoing work ends, retaining authorization "
        "and negotiated server-key identity contemporaneously."
    )
    original = tmp_path / "20260813T003213Z-read-only-actual-host-preflight"
    original.mkdir()
    write_json(
        original / "requested-plan.json",
        {
            "started_utc": "2026-08-13T00:32:13Z",
            "test_id": "read-only-actual-host-preflight",
            "controller_revision": "a" * 40,
        },
    )
    (original / "boundary1_collect.py").write_text("collector\n", encoding="utf-8")
    write_json(original / "boundary1-command-records.json", records())
    write_json(original / "boundary1-summary.json", {"blocked": True})
    write_json(original / "result.json", {"status": "fixture_blocked"})
    (original / "session-log.jsonl").write_text("{}\n", encoding="utf-8")
    write_manifest(original)

    prior = tmp_path / "20260813T003812Z-read-only-actual-host-preflight-correction"
    prior.mkdir()
    for index in range(8):
        (prior / f"prior-{index}.json").write_text("{}\n", encoding="utf-8")
    write_manifest(prior)

    correction = tmp_path / "20260813T004822Z-read-only-actual-host-preflight-correction-2"
    correction.mkdir()
    original_reference = {
        "schema_version": 1,
        "evidence_type": "actual_host_original_bundle_reference",
        "original_run_id": original.name,
        "original_path": str(original.resolve()),
        "original_controller_revision": "a" * 40,
        "original_status": "fixture_blocked",
        "original_unchanged": True,
        "manifest": {
            "path": "SHA256SUMS",
            "size_bytes": (original / "SHA256SUMS").stat().st_size,
            "sha256": file_sha256(original / "SHA256SUMS"),
        },
        "artifacts": artifact_records(original),
        "audit_findings": [
            "runtime_authorization_not_bound_to_exact_command_contract",
            "hostname_observation_overstated_as_verified_identity",
        ],
    }
    write_json(correction / "original-bundle-reference.json", original_reference)
    write_json(
        correction / "prior-correction-reference.json",
        {
            "schema_version": 1,
            "evidence_type": "actual_host_prior_correction_reference",
            "run_id": prior.name,
            "path": str(prior.resolve()),
            "unchanged": True,
            "manifest": {
                "path": "SHA256SUMS",
                "size_bytes": (prior / "SHA256SUMS").stat().st_size,
                "sha256": file_sha256(prior / "SHA256SUMS"),
            },
            "artifacts": artifact_records(prior),
        },
    )
    contract = authorization()["command_contract"]
    write_json(correction / "command-contract.json", contract)
    auth = authorization()
    auth["requested_plan_sha256"] = file_sha256(original / "requested-plan.json")
    auth["collector_sha256"] = file_sha256(original / "boundary1_collect.py")
    auth["command_records_sha256"] = file_sha256(original / "boundary1-command-records.json")
    write_json(correction / "runtime-authorization.json", auth)
    identity = {
        "schema_version": 1,
        "evidence_type": "actual_host_identity_correction",
        "retrospective_correction": True,
        "hosts": [
            {
                "ssh_destination": destination,
                "hostname_observed": name,
                "server_key_fingerprint_at_run": None,
                "exact_host_identity_verified": False,
                "identity_outcome": "unresolved",
                "known_hosts_context": [],
            }
            for destination, name in (("pi@wspr4.local", "wspr4"), ("pi@wspr5.local", "wspr5"))
        ],
    }
    write_json(correction / "host-identity-correction.json", identity)
    write_json(
        correction / "controller-openssh.json",
        {
            "path": "/usr/bin/ssh",
            "return_code": 0,
            "historical_identity_limit": OPENSSH_HISTORICAL_LIMIT,
        },
    )
    write_json(
        correction / "corrected-result.json",
        {
            "schema_version": 2,
            "status": "fixture_blocked",
            "cleanup_outcome": "verified",
            "qualification_claim": False,
            "failure_causes": [
                "ongoing_work_detected",
                "ownership_conflict",
                "helper_not_installed",
                "exact_host_identity_unresolved",
            ],
            "later_boundaries": {
                name: "not_run"
                for name in (
                    "persistent_helper",
                    "systemd_provider",
                    "gpio",
                    "si5351",
                    "physical_sdr",
                    "rf",
                )
            },
            "correction_effect": correction_effect,
            "next_step": next_step,
        },
    )
    validator = Path(__file__).parents[2] / "src/wsprrypi_qualification/actual_host_preflight.py"
    write_json(
        correction / "correction-request.json",
        {
            "schema_version": 2,
            "correction_run_id": correction.name,
            "created_utc": "2026-08-13T00:48:22Z",
            "retrospective": True,
            "host_connections_authorized": False,
            "purpose": "Composite evidence repair only; no preceding evidence is rewritten.",
            "supersedes": [
                "unbound_external_digests",
                "incomplete_original_bundle_verification",
                "missing_composite_validator",
                "inexact_host_mapping",
            ],
            "validator_sha256": file_sha256(validator),
        },
    )
    (correction / "correction-log.jsonl").write_text(
        json.dumps({"outcome": "fixture_blocked"}) + "\n", encoding="utf-8"
    )
    write_manifest(correction)
    return correction, original, prior


def test_composite_correction_graph_passes(tmp_path: Path) -> None:
    correction, original, prior = correction_graph(tmp_path)
    validate_actual_host_correction_bundle(correction, original, prior)


@pytest.mark.parametrize(
    "field", ["requested_plan_sha256", "collector_sha256", "command_records_sha256"]
)
def test_composite_rejects_changed_external_binding(tmp_path: Path, field: str) -> None:
    correction, original, prior = correction_graph(tmp_path)
    path = correction / "runtime-authorization.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document[field] = "0" * 64
    write_json(path, document)
    write_manifest(correction)
    with pytest.raises((ActualHostEvidenceError, ValueError)):
        validate_actual_host_correction_bundle(correction, original, prior)


@pytest.mark.parametrize("mutation", ["result", "missing", "unexpected", "prior", "original_path"])
def test_composite_rejects_reauthenticated_graph_tampering(tmp_path: Path, mutation: str) -> None:
    correction, original, prior = correction_graph(tmp_path)
    if mutation == "result":
        path = correction / "corrected-result.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["status"] = "inconclusive"
        write_json(path, document)
    elif mutation == "missing":
        (correction / "controller-openssh.json").unlink()
    elif mutation == "unexpected":
        (correction / "extra.json").write_text("{}\n", encoding="utf-8")
    elif mutation == "prior":
        path = correction / "prior-correction-reference.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["path"] = str(tmp_path / "substituted")
        write_json(path, document)
    else:
        path = correction / "original-bundle-reference.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["original_path"] = str(tmp_path / "substituted")
        write_json(path, document)
    write_manifest(correction)
    with pytest.raises((ActualHostEvidenceError, ValueError)):
        validate_actual_host_correction_bundle(correction, original, prior)


def valid_openssh() -> dict:
    executable = Path("/usr/bin/ssh")
    if not executable.is_file():
        pytest.skip("retained controller OpenSSH correction is macOS-specific evidence")
    return {
        "schema_version": 1,
        "evidence_type": "actual_host_controller_openssh_identity",
        "evidence_timing": "current_local_retrospective_context",
        "path": str(executable),
        "size_bytes": executable.stat().st_size,
        "sha256": file_sha256(executable),
        "version_arguments": ["/usr/bin/ssh", "-V"],
        "started_utc": "2026-08-13T02:43:27Z",
        "completed_utc": "2026-08-13T02:43:27.100000Z",
        "return_code": 0,
        "stdout": "",
        "stderr": "OpenSSH_test\n",
        "historical_identity_limit": OPENSSH_HISTORICAL_LIMIT,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("size_bytes", 1),
        ("sha256", "0" * 64),
        ("version_arguments", ["evil"]),
        ("return_code", 1),
        ("stdout", "forged"),
        ("stderr", "unrelated"),
        ("started_utc", "2026-08-13T03:00:00Z"),
        ("completed_utc", "2026-08-13T02:00:00Z"),
        ("evidence_timing", "contemporaneous"),
    ],
)
def test_controller_openssh_tampering_is_rejected(field: str, value: object) -> None:
    document = valid_openssh()
    document[field] = value
    with pytest.raises((ActualHostEvidenceError, ValueError)):
        validate_controller_openssh(document)


def valid_request_and_result() -> tuple[dict, dict]:
    request = {
        "correction_run_id": "20260813T024327Z-read-only-actual-host-preflight-correction-3",
        "created_utc": "2026-08-13T02:43:27Z",
        "retrospective": True,
        "host_connections_authorized": False,
    }
    return request, {"status": "fixture_blocked"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_type", "rf_transmitted"),
        ("retrospective", False),
        ("host_connection_performed", True),
        ("prior_evidence_rewritten", True),
        ("outcome", "cleanup_failed"),
        ("detail", "RF transmitted"),
        ("timestamp_utc", "2099-01-01T00:00:00Z"),
    ],
)
def test_correction_log_tampering_is_rejected(field: str, value: object) -> None:
    request, result = valid_request_and_result()
    document = {
        "schema_version": 1,
        "event_type": "composite_correction_created",
        "timestamp_utc": request["created_utc"],
        "correction_run_id": request["correction_run_id"],
        "retrospective": True,
        "host_connection_performed": False,
        "prior_evidence_rewritten": False,
        "outcome": "fixture_blocked",
        "detail": (
            "Retrospective evidence repair only; no host connection and no prior evidence rewrite."
        ),
    }
    document[field] = value
    with pytest.raises((ActualHostEvidenceError, ValueError)):
        validate_correction_log(document, request, result)


@pytest.mark.parametrize(
    ("started", "completed", "passes"),
    [
        ("1900-01-01T00:00:00Z", "1900-01-01T00:00:01Z", False),
        ("2099-01-01T00:00:00Z", "2099-01-01T00:00:01Z", False),
        ("2026-08-13T02:43:26Z", "2026-08-13T02:43:27Z", False),
        ("2026-08-13T02:53:27Z", "2026-08-13T02:53:27Z", True),
        ("2026-08-13T02:53:27.000001Z", "2026-08-13T02:53:27.000001Z", False),
        ("2026-08-13T02:43:27Z", "2026-08-13T02:43:57Z", True),
        ("2026-08-13T02:43:27Z", "2026-08-13T02:43:57.000001Z", False),
    ],
)
def test_openssh_correction_chronology_bounds(started: str, completed: str, passes: bool) -> None:
    request, _ = valid_request_and_result()
    log = {"timestamp_utc": request["created_utc"]}
    openssh = {"started_utc": started, "completed_utc": completed}
    if passes:
        validate_openssh_correction_chronology(openssh, request, log)
    else:
        with pytest.raises(ActualHostEvidenceError):
            validate_openssh_correction_chronology(openssh, request, log)
