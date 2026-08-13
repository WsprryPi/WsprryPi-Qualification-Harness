import json
import threading
import time
from pathlib import Path

import pytest

from tests.unit.test_real_session import plan_document
from wsprrypi_qualification.live_adapters import (
    LiveAdapterPaths,
    ProductionRealSessionAdapters,
)
from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.real_capabilities import ServiceState
from wsprrypi_qualification.real_session import RealSessionError


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


def test_capture_ready_requires_native_incomplete_output(tmp_path: Path) -> None:
    adapter = bare_adapter(tmp_path)
    expected = tmp_path / "rf_on-0.cf32.incomplete"
    expected.write_bytes(b"")
    worker = threading.Thread(target=lambda: None)
    worker.start()
    adapter._wait_capture_ready(plan_document(), "rf_on", worker, [])
    worker.join()


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
