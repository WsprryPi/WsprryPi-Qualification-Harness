from __future__ import annotations

import json
import struct
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wsprrypi_qualification.manifests import build_manifest, write_manifest
from wsprrypi_qualification.offline import OfflineAnalysisError, artifact
from wsprrypi_qualification.receiver_integration import (
    ReceiverInjection,
    ReceiverIntegrationError,
    ReceiverIntegrationSession,
    ReceiverRuntimeAuthorization,
    ResolvedReceiverIntegrationPlan,
    SealedFakeReceiverAdapters,
    validate_capture_evidence,
    validate_receiver_bundle,
    validate_receiver_plan,
    validate_receiver_session,
)


def plan_document(run_id: str = "20260813T120000Z-receiver-fixture") -> dict[str, object]:
    return {
        "schema_version": 1,
        "plan_type": "resolved_receiver_integration",
        "execution_mode": "hardware_free_validation",
        "run_id": run_id,
        "test_id": "receiver-fixture",
        "controller": {"name": "controller", "identity": "mac", "source_revision": "a" * 40},
        "capture_host": {"name": "wspr5", "identity": "pi", "source_revision": "b" * 40},
        "coordination_host": {"name": "wspr4", "identity": "pi", "source_revision": "c" * 40},
        "coordination_required": True,
        "transport": "ssh",
        "capability_bindings": {
            name: letter * 64
            for name, letter in zip(
                ("host", "coordination", "helper", "soapy"), "def0", strict=True
            )
        },
        "remote_helper": {
            "host": "wspr5",
            "path": "/opt/wspq/helper",
            "sha256": "1" * 64,
            "identity": "fixture-helper",
            "protocol_version": 1,
        },
        "receiver": {
            "manufacturer": "SDRplay",
            "model": "RSP1B",
            "driver": "sdrplay",
            "serial": "2404058C60",
            "channel": 0,
            "module": "SoapySDRPlay3",
            "module_version": "0.5.2",
        },
        "capture": {
            "sample_format": "CF32",
            "sample_rate_hz": 250000,
            "bandwidth_hz": 200000,
            "center_frequency_hz": 1863100,
            "gain_db": 10,
            "agc": False,
            "bias_tee": False,
            "clipping_threshold": 0.999,
            "first_read_discarded": True,
            "sample_count": 32,
            "expected_byte_count": 256,
            "read_timeout_us": 2000000,
        },
        "rf_path": {
            "path_type": "radiated",
            "antenna_connected": True,
            "termination": None,
            "attenuation_db": None,
            "filter": None,
            "safe_input_basis": "operator supplied per-run fixture",
        },
        "raw_iq_retention": "retain",
        "deadlines": {
            "helper_s": 2,
            "capture_s": 5,
            "cleanup_s": 2,
            "coordination_s": 2,
            "overall_s": 15,
        },
        "stopping_procedure": "Stop only harness-owned fake operations.",
        "receiver_release_contract": "Verify the selected receiver is no longer owned.",
        "artifact_policy": "New immutable run directory; retain compact fake IQ.",
        "transmitter_operation_authorized": False,
    }


def run_session(tmp_path: Path, injection: ReceiverInjection = ReceiverInjection.NONE):
    plan = ResolvedReceiverIntegrationPlan(plan_document())
    adapters = SealedFakeReceiverAdapters(injection)
    auth = ReceiverRuntimeAuthorization(
        "operator", datetime(2026, 8, 13, 12, tzinfo=UTC), plan.sha256, "single_run"
    )
    result = ReceiverIntegrationSession(
        plan, adapters, now=datetime(2026, 8, 13, 12, tzinfo=UTC)
    ).run(auth, tmp_path)
    return plan, adapters, result


def test_success_is_inconclusive_and_durable(tmp_path: Path) -> None:
    plan, adapters, result = run_session(tmp_path / "parent with spaces")
    assert result["result"]["status"] == "inconclusive"
    assert result["result"]["qualification_claim"] is False
    assert result["result"]["transmitter_operated"] is False
    assert adapters.cleanup_attempted and adapters.release_checked
    root = Path(result["bundle"])
    assert (root / "rf-off.cf32").stat().st_size == 256
    assert (root / "SHA256SUMS").is_file()
    validate_receiver_session(json.loads((root / "session.json").read_text(encoding="utf-8")))
    validate_receiver_bundle(root)
    assert plan.validated()["execution_mode"] == "hardware_free_validation"


@pytest.mark.parametrize(
    ("injection", "status"),
    [
        (ReceiverInjection.CAPABILITY, "fixture_blocked"),
        (ReceiverInjection.CAPTURE_HOST, "fixture_blocked"),
        (ReceiverInjection.COORDINATION, "fixture_blocked"),
        (ReceiverInjection.HELPER, "fixture_blocked"),
        (ReceiverInjection.OWNERSHIP, "fixture_blocked"),
        (ReceiverInjection.RF_PATH, "fixture_blocked"),
        (ReceiverInjection.ACQUIRE, "fixture_blocked"),
        (ReceiverInjection.SHORT_READ, "fixture_blocked"),
        (ReceiverInjection.OVERFLOW, "fixture_blocked"),
        (ReceiverInjection.TIMEOUT, "fixture_blocked"),
        (ReceiverInjection.CANCEL, "aborted"),
        (ReceiverInjection.HELPER_EXIT, "fixture_blocked"),
        (ReceiverInjection.RECEIVER_DISCONNECT, "fixture_blocked"),
        (ReceiverInjection.CLIPPING, "fixture_blocked"),
        (ReceiverInjection.STOP, "cleanup_failed"),
        (ReceiverInjection.HELPER_SHUTDOWN, "cleanup_failed"),
        (ReceiverInjection.COORDINATION_CLOSE, "cleanup_failed"),
        (ReceiverInjection.RELEASE, "cleanup_failed"),
    ],
)
def test_failure_injection_is_classified_and_cleaned(
    tmp_path: Path, injection: ReceiverInjection, status: str
) -> None:
    _, adapters, result = run_session(tmp_path, injection)
    assert result["result"]["status"] == status
    if injection not in {
        ReceiverInjection.CAPABILITY,
        ReceiverInjection.CAPTURE_HOST,
        ReceiverInjection.COORDINATION,
        ReceiverInjection.HELPER,
        ReceiverInjection.OWNERSHIP,
        ReceiverInjection.RF_PATH,
    }:
        assert adapters.cleanup_attempted


def test_partial_cleanup_registration_still_cleans(tmp_path: Path) -> None:
    _, adapters, result = run_session(tmp_path, ReceiverInjection.CLEANUP_REGISTRATION)
    assert adapters.cleanup_attempted and adapters.release_checked
    assert result["result"]["status"] == "fixture_blocked"


def test_authorization_is_ephemeral_and_plan_bound(tmp_path: Path) -> None:
    plan = ResolvedReceiverIntegrationPlan(plan_document())
    with pytest.raises(ReceiverIntegrationError, match="authorization"):
        ReceiverIntegrationSession(plan, SealedFakeReceiverAdapters(), now=datetime.now(UTC)).run(
            None, tmp_path
        )
    wrong = ReceiverRuntimeAuthorization("operator", datetime.now(UTC), "f" * 64, "single_run")
    with pytest.raises(ReceiverIntegrationError, match="bind"):
        ReceiverIntegrationSession(plan, SealedFakeReceiverAdapters(), now=datetime.now(UTC)).run(
            wrong, tmp_path
        )
    profile = ReceiverRuntimeAuthorization(
        "operator", datetime.now(UTC), plan.sha256, "single_run", profile_derived=True
    )
    with pytest.raises(OfflineAnalysisError):
        profile.document()
    stale = ReceiverRuntimeAuthorization(
        "operator", datetime(2026, 8, 13, 11, tzinfo=UTC), plan.sha256, "single_run"
    )
    with pytest.raises(ReceiverIntegrationError, match="stale"):
        ReceiverIntegrationSession(
            plan, SealedFakeReceiverAdapters(), now=datetime(2026, 8, 13, 12, tzinfo=UTC)
        ).run(stale, tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_mode", "live"),
        ("transmitter_operation_authorized", True),
        ("run_id", "../escape"),
    ],
)
def test_plan_refuses_live_transmitter_and_traversal(field: str, value: object) -> None:
    document = plan_document()
    document[field] = value
    with pytest.raises(OfflineAnalysisError):
        validate_receiver_plan(document)


def test_plan_rejects_byte_and_deadline_contradictions() -> None:
    document = plan_document()
    document["capture"]["expected_byte_count"] = 255  # type: ignore[index]
    with pytest.raises(ReceiverIntegrationError, match="byte"):
        validate_receiver_plan(document)
    document = plan_document()
    document["deadlines"]["capture_s"] = 16  # type: ignore[index]
    with pytest.raises(ReceiverIntegrationError, match="overall"):
        validate_receiver_plan(document)


def test_capture_tampering_is_rejected(tmp_path: Path) -> None:
    plan, _, result = run_session(tmp_path)
    capture = deepcopy(result["session"]["capture"])
    capture["overflow_count"] = 1
    with pytest.raises(ReceiverIntegrationError, match="outcome"):
        validate_capture_evidence(capture, plan.document, plan.sha256)


def test_session_status_relabel_is_rejected(tmp_path: Path) -> None:
    _, _, result = run_session(tmp_path)
    document = deepcopy(result["session"])
    document["final_status"] = "cleanup_failed"
    with pytest.raises(ReceiverIntegrationError, match="cleanup"):
        validate_receiver_session(document)
    document = deepcopy(result["session"])
    document["events"] = [
        item for item in document["events"] if item["phase"] != "receiver_release"
    ]
    document["events"][-1]["sequence"] = len(document["events"])
    with pytest.raises(ReceiverIntegrationError, match="release event"):
        validate_receiver_session(document)


def test_bundle_rejects_reauthenticated_result_relabel(tmp_path: Path) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    result_path = root / "result.json"
    document = json.loads(result_path.read_text(encoding="utf-8"))
    document["status"] = "fixture_blocked"
    result_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    from wsprrypi_qualification.manifests import write_manifest

    (root / "SHA256SUMS").unlink()
    write_manifest(root)
    with pytest.raises(ReceiverIntegrationError, match="result"):
        validate_receiver_bundle(root)


def test_bundle_rejects_capture_path_escape(tmp_path: Path) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    session_path = root / "session.json"
    document = json.loads(session_path.read_text(encoding="utf-8"))
    document["capture"]["iq"]["path"] = str(root.parent / "outside.cf32")
    session_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    from wsprrypi_qualification.manifests import write_manifest

    (root / "SHA256SUMS").unlink()
    write_manifest(root)
    with pytest.raises(ReceiverIntegrationError, match="escape"):
        validate_receiver_bundle(root)


def _rewrite_manifest(root: Path) -> None:
    (root / "SHA256SUMS").unlink()
    write_manifest(root)


def _reauthenticate_bundle(root: Path) -> None:
    index_path = root / "artifact-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["artifacts"] = [
        {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
        for item in build_manifest(root)
        if item.path not in {"artifact-index.json", "SHA256SUMS"}
    ]
    index_path.write_text(json.dumps(index, sort_keys=True) + "\n", encoding="utf-8")
    _rewrite_manifest(root)


def test_bundle_rejects_omitted_required_lifecycle_stage(tmp_path: Path) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    session_path = root / "session.json"
    document = json.loads(session_path.read_text(encoding="utf-8"))
    del document["stages"]["helper"]
    document["events"] = [item for item in document["events"] if item["phase"] != "helper"]
    for sequence, event in enumerate(document["events"], 1):
        event["sequence"] = sequence
    session_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    _rewrite_manifest(root)
    with pytest.raises(ReceiverIntegrationError, match="omits required helper"):
        validate_receiver_bundle(root)


@pytest.mark.parametrize(
    ("target", "field"),
    [("receiver_stopped", "owned_resources_absent"), ("cleanup", "actions_complete")],
)
def test_bundle_rejects_cleanup_detail_contradictions(
    tmp_path: Path, target: str, field: str
) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    session_path = root / "session.json"
    document = json.loads(session_path.read_text(encoding="utf-8"))
    container = document["cleanup"] if target == "cleanup" else document["stages"][target]
    container["details"][field] = False
    session_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    _rewrite_manifest(root)
    with pytest.raises(ReceiverIntegrationError, match=r"cleanup|receiver-stop"):
        validate_receiver_bundle(root)


def test_bundle_rejects_capture_metadata_semantic_tampering(tmp_path: Path) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    metadata_path = root / "capture-metadata.json"
    document = json.loads(metadata_path.read_text(encoding="utf-8"))
    document["run_id"] = "other-run"
    metadata_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    _rewrite_manifest(root)
    with pytest.raises(ReceiverIntegrationError, match="metadata"):
        validate_receiver_bundle(root)


def test_bundle_recomputes_cf32_clipping_from_retained_bytes(tmp_path: Path) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    iq_path = root / "rf-off.cf32"
    data = bytearray(iq_path.read_bytes())
    data[:8] = struct.pack("<ff", 1.0, 0.0)
    iq_path.write_bytes(data)
    iq_record = artifact(iq_path)
    metadata_path = root / "capture-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["iq"] = iq_record
    metadata_path.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
    session_path = root / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["capture"]["iq"] = iq_record
    session["capture"]["metadata"] = artifact(metadata_path)
    session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
    _rewrite_manifest(root)
    with pytest.raises(ReceiverIntegrationError, match="clipping"):
        validate_receiver_bundle(root)


@pytest.mark.parametrize("elapsed", [-1.0, float("nan"), float("inf"), 999.0])
def test_bundle_rejects_invalid_capture_elapsed_time(tmp_path: Path, elapsed: float) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    session_path = root / "session.json"
    metadata_path = root / "capture-metadata.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    session["capture"]["elapsed_s"] = elapsed
    metadata["elapsed_s"] = elapsed
    metadata_path.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
    session["capture"]["metadata"] = artifact(metadata_path)
    session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
    _reauthenticate_bundle(root)
    with pytest.raises((ReceiverIntegrationError, OfflineAnalysisError)):
        validate_receiver_bundle(root)


@pytest.mark.parametrize("kind", ["reversed", "noncanonical", "inconsistent"])
def test_bundle_rejects_capture_timestamp_contradictions(tmp_path: Path, kind: str) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    session_path = root / "session.json"
    metadata_path = root / "capture-metadata.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if kind == "reversed":
        completed = datetime.fromisoformat(session["capture"]["started_utc"].replace("Z", "+00:00"))
        session["capture"]["completed_utc"] = (
            (completed - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        )
    elif kind == "noncanonical":
        session["capture"]["started_utc"] = session["capture"]["started_utc"].replace("Z", "+00:00")
    else:
        completed = datetime.fromisoformat(session["capture"]["started_utc"].replace("Z", "+00:00"))
        session["capture"]["completed_utc"] = (
            (completed + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        )
    metadata["started_utc"] = session["capture"]["started_utc"]
    metadata["completed_utc"] = session["capture"]["completed_utc"]
    metadata_path.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
    session["capture"]["metadata"] = artifact(metadata_path)
    session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
    _reauthenticate_bundle(root)
    with pytest.raises((ReceiverIntegrationError, OfflineAnalysisError)):
        validate_receiver_bundle(root)


@pytest.mark.parametrize(
    "stage",
    [
        "capabilities",
        "capture_host",
        "coordination",
        "helper",
        "ownership",
        "rf_path",
        "cleanup_registration",
        "receiver_acquired",
    ],
)
def test_bundle_rejects_hardware_access_claim_in_fake_stage(tmp_path: Path, stage: str) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    session_path = root / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["stages"][stage]["details"]["hardware_access"] = True
    session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
    _reauthenticate_bundle(root)
    with pytest.raises((ReceiverIntegrationError, OfflineAnalysisError)):
        validate_receiver_bundle(root)


@pytest.mark.parametrize(
    ("stage", "mutation"),
    [
        ("capture_host", lambda details: details["host"].update(name="other")),
        ("coordination", lambda details: details.update(required=False)),
        ("helper", lambda details: details["helper"].update(identity="evil")),
        ("helper", lambda details: details.update(external_process_started=True)),
        ("ownership", lambda details: details["receiver"].update(serial="other")),
        ("rf_path", lambda details: details["rf_path"].update(antenna_connected=False)),
        ("cleanup_registration", lambda details: details.update(before_receiver_acquisition=False)),
        ("receiver_acquired", lambda details: details.update(physical_sdr_opened=True)),
    ],
)
def test_bundle_rejects_plan_stage_detail_tampering(
    tmp_path: Path, stage: str, mutation: object
) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    session_path = root / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    mutation(session["stages"][stage]["details"])  # type: ignore[operator]
    session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
    _reauthenticate_bundle(root)
    with pytest.raises((ReceiverIntegrationError, OfflineAnalysisError)):
        validate_receiver_bundle(root)


def _replace_bundle_causes(root: Path, causes: list[str]) -> None:
    for name in ("session.json", "result.json"):
        path = root / name
        document = json.loads(path.read_text(encoding="utf-8"))
        document["failure_causes"] = causes
        path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    _reauthenticate_bundle(root)


@pytest.mark.parametrize("forged", ["rf_transmitted", "receiver exploded", "unsafe_rf_path"])
def test_bundle_rejects_reauthenticated_preflight_cause_forgery(
    tmp_path: Path, forged: str
) -> None:
    _, _, result = run_session(tmp_path, ReceiverInjection.CAPABILITY)
    root = Path(result["bundle"])
    session_path = root / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["stages"]["capabilities"]["details"]["cause"] = forged
    session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
    _replace_bundle_causes(root, [forged])
    with pytest.raises((ReceiverIntegrationError, OfflineAnalysisError)):
        validate_receiver_bundle(root)


def test_bundle_rejects_cause_on_passed_stage(tmp_path: Path) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    session_path = root / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["stages"]["capabilities"]["details"]["cause"] = "missing_capability"
    session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
    _reauthenticate_bundle(root)
    with pytest.raises(ReceiverIntegrationError, match="cause"):
        validate_receiver_bundle(root)


@pytest.mark.parametrize(
    ("injection", "expected", "forged"),
    [
        (ReceiverInjection.SHORT_READ, "short_read", "overflow"),
        (ReceiverInjection.OVERFLOW, "overflow", "capture_timeout"),
        (ReceiverInjection.TIMEOUT, "capture_timeout", "short_read"),
        (ReceiverInjection.CANCEL, "capture_cancelled", "receiver_disconnect"),
        (ReceiverInjection.HELPER_EXIT, "helper_nonzero", "clipping"),
        (ReceiverInjection.RECEIVER_DISCONNECT, "receiver_disconnect", "helper_nonzero"),
        (ReceiverInjection.CLIPPING, "clipping", "overflow"),
    ],
)
def test_bundle_rejects_missing_or_wrong_capture_cause(
    tmp_path: Path, injection: ReceiverInjection, expected: str, forged: str
) -> None:
    _, _, result = run_session(tmp_path, injection)
    root = Path(result["bundle"])
    assert result["session"]["failure_causes"] == [expected]
    _replace_bundle_causes(root, [forged])
    with pytest.raises(ReceiverIntegrationError, match="causes"):
        validate_receiver_bundle(root)


@pytest.mark.parametrize(
    ("injection", "expected"),
    [
        (ReceiverInjection.STOP, "receiver_stop_failed"),
        (ReceiverInjection.HELPER_SHUTDOWN, "helper_shutdown_failed"),
        (ReceiverInjection.COORDINATION_CLOSE, "coordination_close_failed"),
        (ReceiverInjection.RELEASE, "receiver_release_failed"),
    ],
)
def test_bundle_rejects_missing_cleanup_cause(
    tmp_path: Path, injection: ReceiverInjection, expected: str
) -> None:
    _, _, result = run_session(tmp_path, injection)
    root = Path(result["bundle"])
    assert result["session"]["failure_causes"] == [expected]
    _replace_bundle_causes(root, [])
    with pytest.raises(ReceiverIntegrationError, match="causes"):
        validate_receiver_bundle(root)


def test_success_rejects_added_failure_cause(tmp_path: Path) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    _replace_bundle_causes(root, ["internal_error"])
    with pytest.raises(ReceiverIntegrationError, match="causes"):
        validate_receiver_bundle(root)


def _replace_authorization_time(root: Path, recorded_utc: str) -> None:
    authorization_path = root / "runtime-authorization.json"
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    authorization["recorded_utc"] = recorded_utc
    authorization_path.write_text(
        json.dumps(authorization, sort_keys=True) + "\n", encoding="utf-8"
    )
    session_path = root / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["authorization"] = authorization
    session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
    _reauthenticate_bundle(root)


@pytest.mark.parametrize(
    "recorded_utc",
    [
        "1900-01-01T00:00:00Z",
        "2099-01-01T00:00:00Z",
        "2026-08-13T12:00:00.000001Z",
        "2026-08-13T11:59:44.999999Z",
        "2026-08-13T12:00:00+00:00",
    ],
)
def test_bundle_rejects_reauthenticated_authorization_chronology(
    tmp_path: Path, recorded_utc: str
) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    _replace_authorization_time(root, recorded_utc)
    with pytest.raises((ReceiverIntegrationError, OfflineAnalysisError)):
        validate_receiver_bundle(root)


def test_bundle_rejects_standalone_authorization_divergence(tmp_path: Path) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    authorization_path = root / "runtime-authorization.json"
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    authorization["recorded_utc"] = "2026-08-13T11:59:59Z"
    authorization_path.write_text(
        json.dumps(authorization, sort_keys=True) + "\n", encoding="utf-8"
    )
    _reauthenticate_bundle(root)
    with pytest.raises(ReceiverIntegrationError, match="authorization"):
        validate_receiver_bundle(root)


def test_bundle_rejects_reauthenticated_session_start_and_window(tmp_path: Path) -> None:
    _, _, result = run_session(tmp_path)
    root = Path(result["bundle"])
    session_path = root / "session.json"
    for field, value in (
        ("started_utc", "1900-01-01T00:00:00Z"),
        ("authorization_freshness_s", 999),
    ):
        session = json.loads(session_path.read_text(encoding="utf-8"))
        session["chronology"][field] = value
        session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
        _reauthenticate_bundle(root)
        with pytest.raises(ReceiverIntegrationError):
            validate_receiver_bundle(root)
        session["chronology"][field] = "2026-08-13T12:00:00Z" if field == "started_utc" else 15
        session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
        _reauthenticate_bundle(root)


@pytest.mark.parametrize("age_s", [0, 15])
def test_authorization_freshness_boundaries_are_inclusive(tmp_path: Path, age_s: int) -> None:
    plan = ResolvedReceiverIntegrationPlan(plan_document())
    started = datetime(2026, 8, 13, 12, tzinfo=UTC)
    authorization = ReceiverRuntimeAuthorization(
        "operator", started - timedelta(seconds=age_s), plan.sha256, "single_run"
    )
    result = ReceiverIntegrationSession(plan, SealedFakeReceiverAdapters(), now=started).run(
        authorization, tmp_path
    )
    validate_receiver_bundle(Path(result["bundle"]))


def test_destination_reuse_and_single_use_are_rejected(tmp_path: Path) -> None:
    plan = ResolvedReceiverIntegrationPlan(plan_document())
    started = datetime(2026, 8, 13, 12, tzinfo=UTC)
    auth = ReceiverRuntimeAuthorization("operator", started, plan.sha256, "single_run")
    session = ReceiverIntegrationSession(plan, SealedFakeReceiverAdapters(), now=started)
    session.run(auth, tmp_path)
    with pytest.raises(ReceiverIntegrationError, match="single-use"):
        session.run(auth, tmp_path)
    with pytest.raises(ReceiverIntegrationError, match="reuse"):
        ReceiverIntegrationSession(plan, SealedFakeReceiverAdapters(), now=started).run(
            auth, tmp_path
        )


def test_fake_adapter_is_sealed() -> None:
    class Evil(SealedFakeReceiverAdapters):
        pass

    with pytest.raises(TypeError, match="sealed"):
        Evil()
