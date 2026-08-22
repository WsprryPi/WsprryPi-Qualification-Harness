from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wsprrypi_qualification.application_shims import (
    ApplicationIdentity,
    WsprProtocol,
    WsprryPiBackendConfig,
    WsprryPiShim,
)
from wsprrypi_qualification.manifests import write_manifest
from wsprrypi_qualification.offline import OfflineAnalysisError
from wsprrypi_qualification.transmitter_lifecycle import (
    ResolvedTransmitterLifecyclePlan,
    SealedFakeTransmitterAdapters,
    TransmitterInjection,
    TransmitterLifecycleError,
    TransmitterLifecycleSession,
    TransmitterRuntimeAuthorization,
    validate_transmitter_bundle,
    validate_transmitter_plan,
)

NOW = datetime(2026, 8, 13, 16, tzinfo=UTC)


def plan_document(tmp_path: Path) -> dict[str, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    executable = tmp_path / "fake WsprryPi with spaces"
    executable.write_bytes(b"not executable; never launched")
    application = WsprryPiShim(
        ApplicationIdentity("wsprrypi", str(executable), "a" * 40, "b" * 40),
        backend="gpio",
        backend_config=WsprryPiBackendConfig("GPIO4", 0, drive_or_power_level=1, gpio_pin=4),
    ).resolve_plan("lifecycle", WsprProtocol("Q0QQQ", "JJ00", 0, 14_097_100, 1, 1500))
    return {
        "schema_version": 1,
        "plan_type": "resolved_transmitter_lifecycle",
        "execution_mode": "hardware_free_validation",
        "run_id": "20260813T160000Z-transmitter-fixture",
        "host": {"name": "wspr4", "identity": "fixture", "source_revision": "c" * 40},
        "remote_helper": {
            "path": "/opt/wspq/helper",
            "sha256": "d" * 64,
            "identity": "fixture-helper",
        },
        "backend": "gpio",
        "services": [
            {"name": "wsprrypi.service", "initial_running": True, "change_for_exercise": True},
            {"name": "observer.service", "initial_running": False, "change_for_exercise": False},
        ],
        "capability_bindings": {"ssh": "e" * 64, "process": "f" * 64, "gpio": "0" * 64},
        "application_plan": application.to_document(),
        "deadlines": {"helper_s": 2, "transmitter_s": 3, "cleanup_s": 2, "overall_s": 10},
        "stopping_procedure": "Stop only the recorded owned handle and verify GPIO input.",
        "rf_authorized": False,
        "qualification_authorized": False,
    }


def run_session(tmp_path: Path, injection: TransmitterInjection = TransmitterInjection.NONE):
    plan = ResolvedTransmitterLifecyclePlan(plan_document(tmp_path))
    authorization = TransmitterRuntimeAuthorization("operator", NOW, plan.sha256)
    return TransmitterLifecycleSession(plan, SealedFakeTransmitterAdapters(injection), now=NOW).run(
        authorization, tmp_path / "evidence parent with spaces"
    )


def test_success_is_inconclusive_and_cleanup_precedes_launch(tmp_path: Path) -> None:
    result = run_session(tmp_path)
    assert result["result"]["status"] == "inconclusive"
    assert result["result"]["qualification_claim"] is False
    assert result["result"]["rf_emitted"] is False
    phases = [item["phase"] for item in result["session"]["events"]]
    assert phases.index("cleanup_registration") < phases.index("process_attempt")
    validate_transmitter_bundle(Path(result["bundle"]))


@pytest.mark.parametrize(
    ("injection", "status"),
    [
        (TransmitterInjection.CAPABILITY, "fixture_blocked"),
        (TransmitterInjection.HOST, "fixture_blocked"),
        (TransmitterInjection.HELPER, "fixture_blocked"),
        (TransmitterInjection.OWNERSHIP, "fixture_blocked"),
        (TransmitterInjection.RF_IDLE, "fixture_blocked"),
        (TransmitterInjection.CLEANUP_REGISTRATION, "fixture_blocked"),
        (TransmitterInjection.LAUNCH, "aborted"),
        (TransmitterInjection.NONZERO, "aborted"),
        (TransmitterInjection.TIMEOUT, "aborted"),
        (TransmitterInjection.CANCEL, "aborted"),
        (TransmitterInjection.DISCONNECT, "aborted"),
        (TransmitterInjection.PROCESS_LEAK, "cleanup_failed"),
        (TransmitterInjection.SERVICE_RESTORE, "cleanup_failed"),
        (TransmitterInjection.QUIESCENCE, "cleanup_failed"),
    ],
)
def test_failure_injection_and_cleanup_precedence(
    tmp_path: Path, injection: TransmitterInjection, status: str
) -> None:
    assert run_session(tmp_path, injection)["result"]["status"] == status


def test_refuses_live_rf_and_profile_authorization(tmp_path: Path) -> None:
    document = plan_document(tmp_path)
    document["execution_mode"] = "live"
    with pytest.raises(OfflineAnalysisError):
        validate_transmitter_plan(document)
    document = plan_document(tmp_path)
    document["rf_authorized"] = True
    with pytest.raises(OfflineAnalysisError):
        validate_transmitter_plan(document)
    document = plan_document(tmp_path)
    document["application_plan"]["execution_authorized"] = True  # type: ignore[index]
    with pytest.raises(OfflineAnalysisError):
        validate_transmitter_plan(document)


@pytest.mark.parametrize("field", ["host", "remote_helper", "capability_bindings"])
def test_plan_requires_closed_host_helper_and_capability_identity(
    tmp_path: Path, field: str
) -> None:
    document = plan_document(tmp_path)
    document[field] = {}
    with pytest.raises(OfflineAnalysisError):
        validate_transmitter_plan(document)


def test_plan_rejects_extra_identity_and_duplicate_service_name(tmp_path: Path) -> None:
    document = plan_document(tmp_path)
    document["host"]["extra"] = True  # type: ignore[index]
    with pytest.raises(OfflineAnalysisError):
        validate_transmitter_plan(document)
    document = plan_document(tmp_path)
    document["services"].append(  # type: ignore[union-attr]
        {"name": "wsprrypi.service", "initial_running": False, "change_for_exercise": False}
    )
    with pytest.raises(TransmitterLifecycleError, match="unique"):
        validate_transmitter_plan(document)


def test_requires_ephemeral_plan_bound_authorization(tmp_path: Path) -> None:
    plan = ResolvedTransmitterLifecyclePlan(plan_document(tmp_path))
    with pytest.raises(TransmitterLifecycleError, match="authorization"):
        TransmitterLifecycleSession(plan, SealedFakeTransmitterAdapters(), now=NOW).run(
            None, tmp_path / "none"
        )
    wrong = TransmitterRuntimeAuthorization("operator", NOW, "f" * 64)
    with pytest.raises(TransmitterLifecycleError, match="bind"):
        TransmitterLifecycleSession(plan, SealedFakeTransmitterAdapters(), now=NOW).run(
            wrong, tmp_path / "wrong"
        )


def test_tampering_cleanup_order_claims_and_status_rejects(tmp_path: Path) -> None:
    result = run_session(tmp_path)
    root = Path(result["bundle"])
    session_path = root / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["events"][7], session["events"][8] = session["events"][8], session["events"][7]
    for sequence, event in enumerate(session["events"], 1):
        event["sequence"] = sequence
    session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
    (root / "SHA256SUMS").unlink()
    write_manifest(root)
    with pytest.raises(TransmitterLifecycleError, match="sequence"):
        validate_transmitter_bundle(root)


def test_reauthenticated_stage_omission_and_cleanup_falsehood_reject(tmp_path: Path) -> None:
    for mutation in ("stage", "cleanup"):
        result = run_session(tmp_path / mutation)
        root = Path(result["bundle"])
        session_path = root / "session.json"
        document = json.loads(session_path.read_text(encoding="utf-8"))
        if mutation == "stage":
            del document["stages"]["helper"]
            document["events"] = [item for item in document["events"] if item["phase"] != "helper"]
            for sequence, event in enumerate(document["events"], 1):
                event["sequence"] = sequence
        else:
            document["cleanup"]["process_absent"] = False
            document["cleanup"]["cleanup_verified"] = True
        session_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
        (root / "SHA256SUMS").unlink()
        write_manifest(root)
        with pytest.raises(TransmitterLifecycleError):
            validate_transmitter_bundle(root)


def test_reauthenticated_authorization_chronology_rejects(tmp_path: Path) -> None:
    result = run_session(tmp_path)
    root = Path(result["bundle"])
    auth_path = root / "runtime-authorization.json"
    session_path = root / "session.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    session = json.loads(session_path.read_text(encoding="utf-8"))
    auth["recorded_utc"] = "1900-01-01T00:00:00Z"
    session["authorization"] = auth
    auth_path.write_text(json.dumps(auth, sort_keys=True) + "\n", encoding="utf-8")
    session_path.write_text(json.dumps(session, sort_keys=True) + "\n", encoding="utf-8")
    (root / "SHA256SUMS").unlink()
    write_manifest(root)
    with pytest.raises(TransmitterLifecycleError, match="stale"):
        validate_transmitter_bundle(root)


@pytest.mark.parametrize(
    "mutation",
    ["return_code", "timeout", "gpio", "service", "extra"],
)
def test_reauthenticated_process_and_cleanup_contradictions_reject(
    tmp_path: Path, mutation: str
) -> None:
    result = run_session(tmp_path)
    root = Path(result["bundle"])
    session_path = root / "session.json"
    document = json.loads(session_path.read_text(encoding="utf-8"))
    if mutation == "return_code":
        document["process"]["return_code"] = 99
    elif mutation == "timeout":
        document["process"]["timed_out"] = True
    elif mutation == "gpio":
        document["cleanup"]["quiescence"]["gpio_direction"] = "output"
    elif mutation == "extra":
        document["process"]["actual_rf_emitted"] = True
    else:
        service = document["cleanup"]["services"][0]
        service["initial_running"] = False
        service["restored_running"] = True
    session_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    (root / "SHA256SUMS").unlink()
    write_manifest(root)
    with pytest.raises(TransmitterLifecycleError):
        validate_transmitter_bundle(root)


def test_launch_failure_has_no_owned_handle(tmp_path: Path) -> None:
    result = run_session(tmp_path, TransmitterInjection.LAUNCH)
    process = result["session"]["process"]
    assert process["handle_id"] is None
    assert process["ownership_recorded_before_wait"] is False


def test_service_policy_preserves_mixed_initial_states(tmp_path: Path) -> None:
    result = run_session(tmp_path)
    services = result["session"]["cleanup"]["services"]
    assert services == [
        {
            "name": "wsprrypi.service",
            "initial_running": True,
            "changed_by_harness": True,
            "restored_running": True,
            "restoration_verified": True,
        },
        {
            "name": "observer.service",
            "initial_running": False,
            "changed_by_harness": False,
            "restored_running": False,
            "restoration_verified": True,
        },
    ]


def test_reauthenticated_qualification_and_rf_claims_reject(tmp_path: Path) -> None:
    result = run_session(tmp_path)
    root = Path(result["bundle"])
    result_path = root / "result.json"
    document = json.loads(result_path.read_text(encoding="utf-8"))
    document["qualification_claim"] = True
    document["rf_emitted"] = True
    result_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    (root / "SHA256SUMS").unlink()
    write_manifest(root)
    with pytest.raises(OfflineAnalysisError):
        validate_transmitter_bundle(root)


def test_single_use_and_destination_reuse(tmp_path: Path) -> None:
    plan = ResolvedTransmitterLifecyclePlan(plan_document(tmp_path))
    auth = TransmitterRuntimeAuthorization("operator", NOW, plan.sha256)
    session = TransmitterLifecycleSession(plan, SealedFakeTransmitterAdapters(), now=NOW)
    parent = tmp_path / "parent"
    session.run(auth, parent)
    with pytest.raises(TransmitterLifecycleError, match="single-use"):
        session.run(auth, parent)
    with pytest.raises(TransmitterLifecycleError, match="reused"):
        TransmitterLifecycleSession(plan, SealedFakeTransmitterAdapters(), now=NOW).run(
            auth, parent
        )
