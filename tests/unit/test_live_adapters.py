import json
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.unit.test_real_session import plan_document, tone_plan_document
from wsprrypi_qualification.live_adapters import (
    LiveAdapterPaths,
    ProductionRealSessionAdapters,
    _coherent_capture_launch_epoch,
    _intentional_carrier_stop_verified,
    _owned_process_released,
    _retained_capture_has_margin,
)
from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.real_capabilities import LaunchResult, ServiceState
from wsprrypi_qualification.real_session import RealSessionError, resolved_real_plan_sha256


def test_offline_runner_invokes_package_entrypoint(monkeypatch) -> None:
    observed = {}

    def run(arguments, **kwargs):
        observed["arguments"] = arguments
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    ProductionRealSessionAdapters._run_offline(("version",), 1)
    assert observed["arguments"][:3] == (
        sys.executable,
        "-m",
        "wsprrypi_qualification",
    )


def bare_adapter(tmp_path: Path) -> ProductionRealSessionAdapters:
    adapter = object.__new__(ProductionRealSessionAdapters)
    adapter.paths = LiveAdapterPaths(
        tmp_path,
        tmp_path / "bench.json",
        tmp_path / "test.json",
        tmp_path / "receiver.json",
        tmp_path / "capture",
        tmp_path / "wsprd",
    )
    adapter._artifacts = []
    adapter._session_deadline = float("inf")
    adapter._cleanup_reserve_s = 0.0
    adapter._capture_tasks = []
    adapter._capture_artifacts = {}
    return adapter


def test_capture_task_is_cancelled_and_joined_before_cleanup_can_verify(tmp_path: Path) -> None:
    adapter = bare_adapter(tmp_path)
    cancellation = threading.Event()

    def wait_for_cancel() -> None:
        cancellation.wait(1)

    worker = threading.Thread(target=wait_for_cancel)
    adapter._capture_tasks = [(worker, cancellation)]
    worker.start()
    assert adapter._cancel_capture_tasks(time.monotonic() + 0.5) == []
    assert not worker.is_alive()
    assert adapter._capture_tasks == []


def test_stage_refuses_to_hide_deadline_overrun(tmp_path: Path, monkeypatch) -> None:
    adapter = bare_adapter(tmp_path)
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.monotonic", lambda: 11.0)
    with pytest.raises(RealSessionError, match="hard deadline"):
        adapter._stage(plan_document(), "helper", "passed", {}, 5.0, 0.0)


def test_transmitter_execution_retains_complete_owned_process_result(tmp_path: Path) -> None:
    adapter = bare_adapter(tmp_path)
    plan = plan_document()
    result = LaunchResult(
        7,
        "complete stdout",
        "complete stderr",
        timed_out=True,
        cleanup_verified=False,
        handle_id="owned-7",
    )
    adapter._retain_transmitter_result(plan, tone=True, result=result)
    document = json.loads((tmp_path / "carrier-transmitter-execution.json").read_text())
    assert document == {
        "schema_version": 1,
        "evidence_type": "transmitter_execution",
        "run_id": plan["run_id"],
        "plan_sha256": resolved_real_plan_sha256(plan),
        "mode": "tone",
        "handle_id": "owned-7",
        "return_code": 7,
        "stdout": "complete stdout",
        "stderr": "complete stderr",
        "timed_out": True,
        "cancelled": False,
        "disconnected": False,
        "cleanup_verified": False,
        "stop_requested": False,
        "running_before_stop": None,
        "outcome": "failed",
    }


def test_capture_ready_requires_native_incomplete_output(tmp_path: Path) -> None:
    adapter = bare_adapter(tmp_path)
    expected = tmp_path / "rf_on-0.cf32.incomplete"
    expected.write_bytes(b"")
    worker = threading.Thread(target=lambda: None)
    worker.start()
    adapter._wait_capture_ready(plan_document(), "rf_on", worker, [])
    worker.join()


def test_intentional_carrier_stop_contract_is_fail_closed() -> None:
    good = LaunchResult(
        1,
        cancelled=True,
        cleanup_verified=True,
        stop_requested=True,
        running_before_stop=True,
    )
    assert _intentional_carrier_stop_verified(good)
    for changed in (
        {"stop_requested": False},
        {"running_before_stop": False},
        {"cancelled": False},
        {"timed_out": True},
        {"disconnected": True},
        {"cleanup_verified": False},
    ):
        values = good.__dict__ | changed
        assert not _intentional_carrier_stop_verified(LaunchResult(**values))


def test_verified_early_exit_releases_owned_process_without_claiming_intentional_stop() -> None:
    early_exit = LaunchResult(
        1,
        cancelled=True,
        cleanup_verified=True,
        stop_requested=True,
        running_before_stop=False,
    )
    assert _owned_process_released(early_exit)
    assert not _intentional_carrier_stop_verified(early_exit)
    assert not _owned_process_released(
        LaunchResult(
            1,
            cancelled=True,
            cleanup_verified=False,
            stop_requested=True,
            running_before_stop=False,
        )
    )


def test_coherent_capture_guard_and_retained_margin_are_distinct() -> None:
    slot = datetime(2026, 8, 13, 20, 44, tzinfo=UTC)
    assert _coherent_capture_launch_epoch(slot, 5) == slot.timestamp() - 7
    assert _retained_capture_has_margin(
        {"timestamps": {"retained_capture_start_utc": "2026-08-13T20:43:55Z"}},
        slot,
        5,
    )
    assert not _retained_capture_has_margin(
        {"timestamps": {"retained_capture_start_utc": "2026-08-13T20:43:55.001Z"}},
        slot,
        5,
    )


class FakeServiceProvider:
    def __init__(self, running: bool) -> None:
        self.running = running

    def inspect(self, name: str) -> ServiceState:
        return ServiceState(name, "fake", self.running)

    def set_running(self, name: str, running: bool) -> None:
        self.running = running


def test_active_receiver_owner_is_stopped_only_after_cleanup_registration(tmp_path: Path) -> None:
    adapter = bare_adapter(tmp_path)
    adapter.tx_services = FakeServiceProvider(False)
    adapter.rx_services = FakeServiceProvider(True)
    adapter._initial_services = {}
    adapter._changed_services = []
    adapter._owned = []
    adapter._cleanup_installed = False
    adapter.inspect_services_and_ownership(plan_document())
    assert adapter.rx_services.running is True
    assert adapter._changed_services == []
    adapter.install_cleanup(plan_document())
    assert adapter.rx_services.running is False
    assert adapter._changed_services == [("receiver", "SoapySDRServer")]


def test_overall_deadline_is_cumulative_and_reserves_cleanup(tmp_path: Path, monkeypatch) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._session_deadline = 100.0
    adapter._cleanup_reserve_s = 10.0
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.monotonic", lambda: 90.1)
    with pytest.raises(RealSessionError, match="overall deadline"):
        adapter._remaining(5.0, reserve_cleanup=True)


def test_tone_pattern_uses_absolute_deadlines_and_stops_every_cycle(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._owned = []
    clock = {"now": 100.0}
    sleeps = []
    stopped = []

    class Process:
        def stop(self):
            stopped.append(clock["now"])
            return LaunchResult(
                1,
                cancelled=True,
                cleanup_verified=True,
                handle_id=f"cycle-{len(stopped)}",
                stop_requested=True,
                running_before_stop=True,
            )

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    def begin(plan, tone, *, cycle=None):
        assert tone and cycle in {1, 2, 3}
        process = Process()
        adapter._owned.append(process)
        return process

    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.sleep", sleep)
    monkeypatch.setattr(adapter, "_begin_transmitter", begin)
    monkeypatch.setattr(adapter, "_retain_transmitter_result", lambda *args, **kwargs: None)

    class Worker:
        running = True

        def is_alive(self):
            return self.running

        def join(self, timeout=None):
            self.running = False

    worker = Worker()
    captured = [{"capture": "complete"}]
    adapter._capture_tasks = [(worker, threading.Event())]
    result = adapter._run_tone_pattern(
        tone_plan_document(), worker, adapter._capture_tasks[0][1], captured, []
    )
    assert result == captured[0]
    assert stopped == [104.0, 108.0, 112.0]
    assert sum(sleeps) == 14.0
    assert adapter._owned == []


def test_transmitter_service_preparation_precedes_tone_epoch(tmp_path: Path, monkeypatch) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._cleanup_installed = True
    adapter._initial_services = {("transmitter", "wsprrypi.service"): True}
    adapter._changed_services = []
    adapter.tx_services = FakeServiceProvider(True)
    clock = {"now": 100.0}

    def set_running(name: str, running: bool) -> None:
        assert name == "wsprrypi.service"
        clock["now"] += 1.75
        adapter.tx_services.running = running

    monkeypatch.setattr(adapter.tx_services, "set_running", set_running)
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.monotonic", lambda: clock["now"])
    plan = tone_plan_document()
    plan["services"]["transmitter"] = ["wsprrypi.service"]

    adapter._prepare_transmitter_services(plan)
    epoch = time.monotonic()

    assert epoch == 101.75
    assert adapter._changed_services == [("transmitter", "wsprrypi.service")]
    assert adapter.tx_services.running is False


def test_scheduled_transmitter_launch_refuses_unprepared_service(tmp_path: Path) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._cleanup_installed = True
    adapter._initial_services = {("transmitter", "wsprrypi.service"): True}
    adapter._changed_services = []
    plan = tone_plan_document()
    plan["services"]["transmitter"] = ["wsprrypi.service"]

    with pytest.raises(RealSessionError, match="not prepared before RF cadence"):
        adapter._begin_transmitter(plan, True, cycle=1)


def test_tone_pattern_refuses_late_transition_before_enabling_rf(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = bare_adapter(tmp_path)
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.monotonic", lambda: 103.0)
    with pytest.raises(RealSessionError, match="absolute cadence"):
        adapter._sleep_until(102.0)


def test_each_live_tone_cycle_uses_its_resolved_remote_watchdog(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._cleanup_installed = True
    adapter._initial_services = {}
    adapter._changed_services = []
    adapter._owned = []
    adapter.tx_services = FakeServiceProvider(False)
    adapter.tx_client = object()
    adapter.tx_launcher = object()
    observed = {}

    class Launcher:
        def __init__(self, client, hard_timeout_s, executable_sha256):
            observed.update(
                client=client,
                hard_timeout_s=hard_timeout_s,
                executable_sha256=executable_sha256,
            )

        def begin(self, arguments):
            observed["arguments"] = arguments
            return object()

    class Application:
        arguments = ("/tx",)

        def to_document(self):
            return {}

    monkeypatch.setattr("wsprrypi_qualification.live_adapters.SshOwnedProcessLauncher", Launcher)
    monkeypatch.setattr(adapter, "_application", lambda plan, tone: Application())
    monkeypatch.setattr(
        "wsprrypi_qualification.live_adapters.write_json_new", lambda *args, **kwargs: None
    )
    plan = tone_plan_document()
    process = adapter._begin_transmitter(plan, True, cycle=1)
    assert process in adapter._owned
    assert observed["hard_timeout_s"] == 2
    assert observed["executable_sha256"] == plan["wsprrypi"]["sha256"]


def test_partial_receiver_service_change_retains_restoration_intent(tmp_path: Path) -> None:
    class InspectFailure(FakeServiceProvider):
        calls = 0

        def inspect(self, name: str) -> ServiceState:
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("injected inspection failure")
            return super().inspect(name)

    adapter = bare_adapter(tmp_path)
    adapter._cleanup_installed = False
    adapter.tx_services = FakeServiceProvider(False)
    adapter.rx_services = InspectFailure(True)
    adapter.rx_services.calls = 1
    adapter._initial_services = {("receiver", "SoapySDRServer"): True}
    adapter._changed_services = []
    with pytest.raises(RuntimeError, match="inspection failure"):
        adapter.install_cleanup(plan_document())
    assert adapter._changed_services == [("receiver", "SoapySDRServer")]


def test_published_artifact_index_rejects_reauthenticated_dependency_tamper(
    tmp_path: Path,
) -> None:
    adapter = bare_adapter(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    iq = source / "capture.cf32"
    iq.write_bytes(b"12345678")
    evidence = source / "evidence.json"
    evidence.write_text(json.dumps({"input": artifact(iq)}), encoding="utf-8")
    adapter._artifacts = [iq, evidence]
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    adapter.publish_artifacts(bundle)
    adapter.validate_published_artifacts(bundle)
    retained_iq = next((bundle / "retained-artifacts").glob("*-capture.cf32"))
    retained_iq.write_bytes(b"87654321")
    with pytest.raises(RealSessionError, match="identity changed"):
        adapter.validate_published_artifacts(bundle)
