import hashlib
import inspect
import json
import threading
from pathlib import Path

import pytest

import wsprrypi_qualification.keyed_coordinator as coordinator_module
from wsprrypi_qualification.keyed_coordinator import (
    Boundary,
    KeyedCoordinatorError,
    SealedFakeKeyedAdapter,
    run_hardware_free_keyed_session,
)
from wsprrypi_qualification.keyed_session_contracts import (
    compose_keyed_runtime_authorization,
    validate_keyed_artifact_index,
)
from wsprrypi_qualification.manifests import build_manifest, render_manifest


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _artifact(value: str) -> dict[str, object]:
    return {"path": f"inputs/{value}.json", "size_bytes": 1, "sha256": _digest(value)}


def _application_plan(mode: str) -> dict[str, object]:
    from tests.unit.test_keyed_session_contracts import application_plan

    return application_plan(mode)


def _plan(mode: str = "QRSS", session_id: str = "fake-keyed-session") -> dict[str, object]:
    return {
        "schema_version": 1,
        "evidence_type": "resolved_keyed_session_plan",
        "session_id": session_id,
        "mode": mode,
        "transmitter": {
            "host": "sealed-fake",
            "backend": "gpio",
            "output": "GPIO4",
            "frequency_hz": 14_097_100,
            "drive": 0,
            "executable": _artifact("wsprrypi"),
        },
        "receiver": {
            "host": "sealed-fake",
            "driver": "fake",
            "device": "none",
            "identity_sha256": _digest("none"),
            "sample_rate_hz": 250_000,
            "bandwidth_hz": 200_000,
            "center_frequency_hz": 14_097_100,
            "gain_db": 20,
            "channel": 0,
            "read_timeout_us": 100_000,
            "clipping_threshold": 0.98,
        },
        "rf_path": {
            "antenna_connected": False,
            "attenuation_db": 20,
            "termination": "sealed fake",
            "filter_state": "sealed fake",
            "routing": "sealed fake",
            "safe_input_basis": "sealed hardware-free fixture",
        },
        "reference": {"plan": _artifact("plan"), "expected_events": _artifact("events")},
        "application_plan": _application_plan(mode),
        "target_revision": "1" * 40,
        "target_submodule_revision": "2" * 40,
        "analyzer_revision": "3" * 40,
        "message_repetitions_per_transaction": 1,
        "capability_bindings": {
            "ssh": _artifact("ssh"),
            "known_hosts": _artifact("known-hosts"),
            "transmitter_helper": _artifact("tx-helper"),
            "transmitter_helper_config": _artifact("tx-helper-config"),
            "transmitter_helper_identity": "tx-helper-v1",
            "transmitter_process_privilege_wrapper": _artifact("tx-process-sudo"),
            "receiver_helper": _artifact("rx-helper"),
            "receiver_helper_config": _artifact("rx-helper-config"),
            "receiver_helper_identity": "rx-helper-v1",
            "capture_helper": _artifact("capture-helper"),
            "services": ["tx:wsprrypi", "rx:SoapySDRServer"],
            "required_receiver_services": [],
            "quiescence": "gpio",
        },
        "deadlines": {"transaction_s": 10, "cleanup_s": 5, "overall_s": 35},
        "stopping_procedure": "cancel sealed fake",
        "transaction_count": 3,
    }


def _run(tmp_path: Path, adapter: SealedFakeKeyedAdapter, *, mode: str = "QRSS"):
    plan = _plan(mode)
    authorization = compose_keyed_runtime_authorization(
        plan, operator="test", authorized_utc="2026-08-21T12:00:00Z"
    )
    return run_hardware_free_keyed_session(plan, authorization, tmp_path, adapter)


@pytest.mark.parametrize("mode", ("QRSS", "FSKCW", "DFCW"))
def test_success_runs_three_independent_transactions_and_publishes_bundle(
    tmp_path: Path, mode: str
) -> None:
    adapter = SealedFakeKeyedAdapter()
    outcome = _run(tmp_path, adapter, mode=mode)
    aggregate = outcome["aggregate"]
    assert aggregate["final_status"] == "qualified"
    assert len(aggregate["transactions"]) == 3
    for identity in ("transaction_id", "process_id", "capture_id", "acquisition_id", "analysis_id"):
        assert len({item[identity] for item in aggregate["transactions"]}) == 3
    root = Path(outcome["bundle"])
    index = json.loads((root / "artifact-index.json").read_text())
    validate_keyed_artifact_index(_plan(mode), index)
    for transaction in aggregate["transactions"]:
        for artifact in transaction["artifacts"]:
            payload = (root / artifact["path"]).read_bytes()
            assert len(payload) == artifact["size_bytes"]
            assert hashlib.sha256(payload).hexdigest() == artifact["sha256"]
    assert (root / "SHA256SUMS").read_text() == render_manifest(build_manifest(root))
    assert len(adapter.calls) == 21


@pytest.mark.parametrize("boundary", tuple(Boundary))
def test_failure_at_every_boundary_stops_and_always_checks_cleanup_and_quiescence(
    tmp_path: Path, boundary: Boundary
) -> None:
    adapter = SealedFakeKeyedAdapter(failure_at=boundary)
    outcome = _run(tmp_path, adapter)
    transactions = outcome["aggregate"]["transactions"]
    assert len(transactions) == 1
    assert all(number == 1 for number, _ in adapter.calls)
    assert (1, Boundary.CLEANUP_COMPLETED) in adapter.calls
    assert (1, Boundary.QUIESCENCE_VERIFIED) in adapter.calls
    expected = (
        "cleanup_failed"
        if boundary
        in {
            Boundary.CLEANUP_COMPLETED,
            Boundary.QUIESCENCE_VERIFIED,
        }
        else "preflight_failed"
        if boundary is Boundary.PREFLIGHT
        else "aborted"
        if boundary
        in {
            Boundary.CLEANUP_INSTALLED,
            Boundary.PROCESS_STARTED,
        }
        else "fixture_blocked"
        if boundary is Boundary.CAPTURE_COMPLETED
        else "unqualified_keyed"
    )
    assert outcome["result"]["final_status"] == expected
    root = Path(outcome["bundle"])
    assert (root / "SHA256SUMS").read_text() == render_manifest(build_manifest(root))


@pytest.mark.parametrize("boundary", tuple(Boundary))
def test_cancellation_at_every_boundary_stops_and_cleanup_precedence_is_preserved(
    tmp_path: Path, boundary: Boundary
) -> None:
    adapter = SealedFakeKeyedAdapter(cancel_at=boundary)
    outcome = _run(tmp_path, adapter)
    assert len(outcome["aggregate"]["transactions"]) == 1
    assert outcome["result"]["final_status"] == (
        "cleanup_failed"
        if boundary in {Boundary.CLEANUP_COMPLETED, Boundary.QUIESCENCE_VERIFIED}
        else "aborted"
    )
    assert (
        1,
        Boundary.CLEANUP_COMPLETED,
    ) in adapter.calls or boundary is Boundary.CLEANUP_COMPLETED
    assert (
        1,
        Boundary.QUIESCENCE_VERIFIED,
    ) in adapter.calls or boundary is Boundary.QUIESCENCE_VERIFIED


def test_preexisting_cancellation_does_not_reach_primary_adapter_boundary(tmp_path: Path) -> None:
    cancellation = threading.Event()
    cancellation.set()
    plan = _plan()
    authorization = compose_keyed_runtime_authorization(
        plan, operator="test", authorized_utc="2026-08-21T12:00:00Z"
    )
    adapter = SealedFakeKeyedAdapter()
    outcome = run_hardware_free_keyed_session(
        plan, authorization, tmp_path, adapter, cancellation=cancellation
    )
    assert outcome["result"]["final_status"] == "aborted"
    assert adapter.calls == [
        (1, Boundary.CLEANUP_COMPLETED),
        (1, Boundary.QUIESCENCE_VERIFIED),
    ]


def test_adapter_is_sealed_single_injection_and_destination_is_immutable(tmp_path: Path) -> None:
    class Impostor(SealedFakeKeyedAdapter):
        pass

    with pytest.raises(TypeError, match="sealed"):
        Impostor()
    with pytest.raises(ValueError, match="at most one"):
        SealedFakeKeyedAdapter(failure_at=Boundary.PREFLIGHT, cancel_at=Boundary.PREFLIGHT)
    _run(tmp_path, SealedFakeKeyedAdapter())
    with pytest.raises(KeyedCoordinatorError, match="reused"):
        _run(tmp_path, SealedFakeKeyedAdapter())


def test_preexisting_incomplete_destination_is_never_reused(tmp_path: Path) -> None:
    incomplete = tmp_path / ".incomplete-fake-keyed-session"
    incomplete.mkdir()
    marker = incomplete / "untrusted.txt"
    marker.write_text("must remain untouched", encoding="utf-8")
    with pytest.raises(KeyedCoordinatorError, match="reused"):
        _run(tmp_path, SealedFakeKeyedAdapter())
    assert marker.read_text(encoding="utf-8") == "must remain untouched"


def test_session_directory_is_portably_safe(tmp_path: Path) -> None:
    plan = _plan(session_id="CON")
    authorization = compose_keyed_runtime_authorization(
        plan, operator="test", authorized_utc="2026-08-21T12:00:00Z"
    )
    with pytest.raises(KeyedCoordinatorError, match="unsafe keyed session ID"):
        run_hardware_free_keyed_session(plan, authorization, tmp_path, SealedFakeKeyedAdapter())


def test_module_has_no_hardware_or_execution_imports() -> None:
    source = inspect.getsource(coordinator_module)
    forbidden = (
        "subprocess",
        "socket",
        "paramiko",
        "SoapySDR",
        "live_adapters",
        "transports",
        "gpio",
        "i2c",
        "ssh",
    )
    assert all(token not in source for token in forbidden)
