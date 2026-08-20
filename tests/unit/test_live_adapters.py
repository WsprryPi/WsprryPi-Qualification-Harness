import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.unit.test_cw_contracts import _chain
from tests.unit.test_real_session import plan_document, tone_plan_document
from wsprrypi_qualification.cw_iq import analyze_synthetic_iq, generate_synthetic_iq
from wsprrypi_qualification.live_adapters import (
    LiveAdapterPaths,
    ProductionRealSessionAdapters,
    _coherent_capture_launch_epoch,
    _derive_rebound_expected_events,
    _intentional_carrier_stop_verified,
    _owned_process_released,
    _retained_capture_has_margin,
    _stage_bound_artifact,
)
from wsprrypi_qualification.manifests import build_manifest, render_manifest, write_manifest
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


def test_stage_bound_artifact_retains_external_path_with_spaces(tmp_path: Path) -> None:
    source_root = tmp_path / "external contract source"
    source_root.mkdir()
    source = source_root / "tone plan.json"
    source.write_text('{"sealed": true}\n', encoding="utf-8")
    binding = artifact(source)
    work = tmp_path / "fresh work directory"
    work.mkdir()
    destination = work / "tone-plan.json"

    retained = _stage_bound_artifact(binding, destination)

    assert retained == artifact(destination)
    assert destination.read_bytes() == source.read_bytes()
    source.unlink()
    assert destination.read_text(encoding="utf-8") == '{"sealed": true}\n'


def test_stage_bound_artifact_rejects_changed_source_and_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "tone-plan.json"
    source.write_text("sealed", encoding="utf-8")
    binding = artifact(source)
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(RealSessionError, match="identity changed"):
        _stage_bound_artifact(binding, tmp_path / "retained.json")

    binding = artifact(source)
    destination = tmp_path / "existing.json"
    destination.write_text("preserve", encoding="utf-8")
    with pytest.raises(RealSessionError, match="refusing to overwrite"):
        _stage_bound_artifact(binding, destination)
    assert destination.read_text(encoding="utf-8") == "preserve"


def test_live_tone_analysis_stages_external_contract_before_relative_references(
    tmp_path: Path, monkeypatch
) -> None:
    work = tmp_path / "fresh work directory"
    work.mkdir()
    external = tmp_path / "sealed inputs outside work"
    external.mkdir()
    plan_source = external / "tone plan.json"
    expected_source = external / "tone events.json"
    plan_source.write_text('{"kind": "plan"}\n', encoding="utf-8")
    expected_source.write_text(
        json.dumps({"kind": "events", "plan": artifact(plan_source)}), encoding="utf-8"
    )
    off = work / "rf-off.cf32"
    on = work / "rf-on.cf32"
    off.write_bytes(b"off-data")
    on.write_bytes(b"on-data")
    off_metadata = work / "rf-off-metadata.json"
    on_metadata = work / "rf-on-metadata.json"
    off_metadata.write_text("{}", encoding="utf-8")
    on_metadata.write_text("{}", encoding="utf-8")

    plan = tone_plan_document()
    plan["cw_contract"]["plan"] = artifact(plan_source)
    plan["cw_contract"]["expected_events"] = artifact(expected_source)
    adapter = bare_adapter(work)
    adapter._capture_artifacts = {
        "rf_off": (off, off_metadata),
        "rf_on": (on, on_metadata),
    }
    observed: dict[str, object] = {}

    monkeypatch.setattr(adapter, "_run_offline", lambda *args, **kwargs: None)

    def load_document(path: Path, schema: str) -> dict[str, object]:
        if schema == "carrier-analysis.schema.json":
            return {
                "gate_outcome": "passed",
                "metrics": {
                    "strongest_transmitter_added_frequency_hz": 14_097_200.0,
                    "strongest_offset_hz": 100.0,
                    "best_20hz_resolved_power_share": 0.9,
                    "strongest_feature_contrast_db": 20.0,
                },
                "contract": {
                    "gate_policy": "bounded_relative_carrier_acquisition",
                    "relative_acquisition_offset_gate_hz": 500.0,
                    "relative_acquisition_contrast_gate_db": 10.0,
                },
            }
        if schema == "cw-expected-events.schema.json":
            return json.loads(path.read_text(encoding="utf-8"))
        assert path == on_metadata
        assert schema == "capture-metadata.schema.json"
        return {
            "retained_sample_count": plan["carrier"]["rf_on_sample_count"],
            "overflow_count": 0,
            "first_read": {"discarded": True},
            "timestamps": {"retained_capture_start_utc": "2026-08-17T13:27:36Z"},
        }

    def write_document(path: Path, document: dict, **kwargs) -> None:
        path.write_text(json.dumps(document), encoding="utf-8")

    def analyze(plan_path: Path, expected_path: Path, metadata_path: Path, *args, **kwargs):
        observed["plan_path"] = plan_path
        observed["expected_path"] = expected_path
        observed["metadata"] = json.loads(metadata_path.read_text(encoding="utf-8"))
        observed["artifact_root"] = kwargs["_artifact_root"]
        observed["artifacts_at_analysis"] = tuple(adapter._artifacts)
        return {}, {"carrier_gate": "passed", "mode_gate": "not_applicable"}

    monkeypatch.setattr("wsprrypi_qualification.live_adapters.load_json_document", load_document)
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.write_json_new", write_document)
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.analyze_synthetic_iq", analyze)

    result = adapter.analyze_carrier(plan, {}, {})

    retained_plan = work / "tone-plan.json"
    sealed_expected = work / "tone-expected-events.sealed.source"
    retained_expected = work / "tone-expected-events.json"
    assert observed["plan_path"] == retained_plan
    assert observed["expected_path"] == retained_expected
    assert observed["artifact_root"] == work
    metadata = observed["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["plan"] == artifact(retained_plan)
    assert metadata["expected_events"] == artifact(retained_expected)
    assert plan_source not in adapter._artifacts
    assert expected_source not in adapter._artifacts
    assert retained_plan in adapter._artifacts
    assert sealed_expected in adapter._artifacts
    assert retained_expected in adapter._artifacts
    assert observed["artifacts_at_analysis"] == (
        work / "carrier-analysis.json",
        retained_plan,
        sealed_expected,
        retained_expected,
        work / "tone-acquired-capture.json",
    )
    assert sealed_expected.read_bytes() == expected_source.read_bytes()
    assert json.loads(retained_expected.read_text(encoding="utf-8"))["plan"] == artifact(
        retained_plan
    )
    assert result["details"]["gate_outcome"] == "passed"
    assert result["details"]["mode_gate"] == "not_applicable"


def test_live_tone_analysis_propagates_detailed_carrier_failure(
    tmp_path: Path, monkeypatch
) -> None:
    work = tmp_path / "analysis work"
    work.mkdir()
    external = tmp_path / "sealed inputs"
    external.mkdir()
    plan_source = external / "tone-plan.json"
    expected_source = external / "tone-events.json"
    plan_source.write_text('{"kind":"plan"}\n', encoding="utf-8")
    expected_source.write_text(
        json.dumps({"kind": "events", "plan": artifact(plan_source)}), encoding="utf-8"
    )
    adapter = bare_adapter(work)
    off = work / "off.cf32"
    on = work / "on.cf32"
    off.write_bytes(b"off")
    on.write_bytes(b"on")
    off_metadata = work / "off.json"
    on_metadata = work / "on.json"
    off_metadata.write_text("{}", encoding="utf-8")
    on_metadata.write_text("{}", encoding="utf-8")
    adapter._capture_artifacts = {"rf_off": (off, off_metadata), "rf_on": (on, on_metadata)}
    plan = tone_plan_document()
    plan["cw_contract"]["plan"] = artifact(plan_source)
    plan["cw_contract"]["expected_events"] = artifact(expected_source)

    monkeypatch.setattr(adapter, "_run_offline", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "wsprrypi_qualification.live_adapters.load_json_document",
        lambda path, schema: (
            {
                "gate_outcome": "passed",
                "metrics": {
                    "strongest_transmitter_added_frequency_hz": 14_097_286.0,
                    "strongest_offset_hz": 186.0,
                    "best_20hz_resolved_power_share": 0.99,
                    "strongest_feature_contrast_db": 110.0,
                },
                "contract": {
                    "gate_policy": "bounded_relative_carrier_acquisition",
                    "relative_acquisition_offset_gate_hz": 500.0,
                    "relative_acquisition_contrast_gate_db": 10.0,
                },
            }
            if schema == "carrier-analysis.schema.json"
            else {
                "retained_sample_count": plan["carrier"]["rf_on_sample_count"],
                "overflow_count": 0,
                "first_read": {"discarded": True},
                "timestamps": {"retained_capture_start_utc": "2026-08-20T11:11:02Z"},
            }
        ),
    )
    monkeypatch.setattr(
        "wsprrypi_qualification.live_adapters.write_json_new",
        lambda path, document, **kwargs: path.write_text(json.dumps(document), encoding="utf-8"),
    )
    monkeypatch.setattr(
        "wsprrypi_qualification.live_adapters.analyze_synthetic_iq",
        lambda *args, **kwargs: (
            {},
            {
                "carrier_gate": "failed",
                "mode_gate": "not_applicable",
                "failure_causes": ["timing_error"],
            },
        ),
    )

    result = adapter.analyze_carrier(plan, {}, {})
    assert result["details"]["offset_hz"] == 186.0
    assert result["details"]["gate_outcome"] == "failed"
    assert result["details"]["mode_gate"] == "not_applicable"


def test_rebound_retained_contracts_complete_real_iq_analysis(tmp_path: Path) -> None:
    external = tmp_path / "sealed contracts outside work"
    external.mkdir()
    plan_source, expected_source, *_ = _chain(external, "tone")
    source_capture = external / "source.cf32"
    source_metadata = external / "source-metadata.json"
    generate_synthetic_iq(
        plan_source,
        expected_source,
        source_capture,
        source_metadata,
        seed=17,
    )

    work = tmp_path / "fresh analysis work"
    work.mkdir()
    retained_plan = work / "tone-plan.json"
    sealed_expected = work / "tone-expected-events.sealed.source"
    retained_expected = work / "tone-expected-events.json"
    retained_plan_ref = _stage_bound_artifact(artifact(plan_source), retained_plan)
    sealed_ref = _stage_bound_artifact(artifact(expected_source), sealed_expected)
    retained_expected_ref = _derive_rebound_expected_events(
        sealed_expected,
        retained_expected,
        retained_plan_ref,
    )
    metadata = json.loads(source_metadata.read_text(encoding="utf-8"))
    metadata["plan"] = retained_plan_ref
    metadata["expected_events"] = retained_expected_ref
    retained_capture = work / "retained.cf32"
    shutil.copyfile(source_capture, retained_capture)
    metadata["capture"] = artifact(retained_capture)
    retained_metadata = work / "retained-metadata.json"
    retained_metadata.write_text(json.dumps(metadata), encoding="utf-8")

    observations, gate = analyze_synthetic_iq(
        retained_plan,
        retained_expected,
        retained_metadata,
        work / "observations.json",
        work / "gate.json",
        source_revision="d" * 40,
        _artifact_root=work,
    )

    assert sealed_ref == artifact(sealed_expected)
    assert sealed_expected.read_bytes() == expected_source.read_bytes()
    assert retained_expected.read_bytes() != sealed_expected.read_bytes()
    assert observations["analysis_outcome"] == "passed"
    assert gate["carrier_gate"] == "passed"
    assert gate["mode_gate"] == "not_applicable"

    adapter = bare_adapter(work)
    adapter._artifacts = [
        retained_plan,
        sealed_expected,
        retained_expected,
        retained_capture,
        retained_metadata,
        work / "observations.json",
        work / "gate.json",
    ]
    bundle = tmp_path / "relocatable bundle"
    bundle.mkdir()
    adapter.publish_artifacts(bundle)
    shutil.rmtree(external)
    adapter.validate_published_artifacts(bundle)
    write_manifest(bundle)
    assert (bundle / "SHA256SUMS").read_text(encoding="utf-8") == render_manifest(
        build_manifest(bundle)
    )


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


def test_helper_suboperation_refuses_individual_deadline_overrun(monkeypatch) -> None:
    observations = iter((10.0, 10.002))
    monkeypatch.setattr(
        "wsprrypi_qualification.live_adapters.time.monotonic",
        lambda: next(observations, 10.002),
    )
    with pytest.raises(
        RealSessionError,
        match="helper verification transmitter_service_inspect exceeded its hard deadline",
    ):
        ProductionRealSessionAdapters._bounded_helper_operation(
            "transmitter_service_inspect", 0.001, lambda: None
        )


def test_helper_suboperation_attributes_operation_failure() -> None:
    def fail() -> object:
        raise RuntimeError("injected")

    with pytest.raises(
        RealSessionError,
        match="helper verification receiver_service_inspect failed: RuntimeError: injected",
    ):
        ProductionRealSessionAdapters._bounded_helper_operation(
            "receiver_service_inspect", 5.0, fail
        )


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


def test_inactive_required_receiver_service_starts_only_after_cleanup_registration(
    tmp_path: Path,
) -> None:
    adapter = bare_adapter(tmp_path)
    adapter.tx_services = FakeServiceProvider(False)
    adapter.rx_services = FakeServiceProvider(False)
    adapter._initial_services = {}
    adapter._changed_services = []
    adapter._owned = []
    adapter._cleanup_installed = False
    plan = plan_document()
    plan["services"]["receiver_required"] = ["SoapySDRServer"]

    adapter.inspect_services_and_ownership(plan)
    assert adapter.rx_services.running is False
    adapter.install_cleanup(plan)

    assert adapter._cleanup_installed is True
    assert adapter.rx_services.running is True
    assert adapter._changed_services == [("receiver", "SoapySDRServer")]


def test_initially_active_required_receiver_service_is_preserved(tmp_path: Path) -> None:
    adapter = bare_adapter(tmp_path)
    adapter.tx_services = FakeServiceProvider(False)
    adapter.rx_services = FakeServiceProvider(True)
    adapter._initial_services = {}
    adapter._changed_services = []
    adapter._owned = []
    adapter._cleanup_installed = False
    plan = plan_document()
    plan["services"]["receiver_required"] = ["SoapySDRServer"]

    adapter.inspect_services_and_ownership(plan)
    adapter.install_cleanup(plan)

    assert adapter.rx_services.running is True
    assert adapter._changed_services == []


def test_required_receiver_start_failure_retains_restoration_intent(tmp_path: Path) -> None:
    class StartFailure(FakeServiceProvider):
        def set_running(self, name: str, running: bool) -> None:
            assert running is True

    adapter = bare_adapter(tmp_path)
    adapter.tx_services = FakeServiceProvider(False)
    adapter.rx_services = StartFailure(False)
    adapter._initial_services = {("receiver", "SoapySDRServer"): False}
    adapter._changed_services = []
    adapter._owned = []
    adapter._cleanup_installed = False
    plan = plan_document()
    plan["services"]["receiver_required"] = ["SoapySDRServer"]

    with pytest.raises(RealSessionError, match="could not be started"):
        adapter.install_cleanup(plan)

    assert adapter._changed_services == [("receiver", "SoapySDRServer")]


def test_cleanup_restores_required_receiver_service_after_downstream_failure(
    tmp_path: Path, monkeypatch
) -> None:
    class Client:
        timeout_s = 1.0
        transport = object()

    adapter = bare_adapter(tmp_path)
    adapter.tx_services = FakeServiceProvider(False)
    adapter.rx_services = FakeServiceProvider(False)
    adapter.tx_client = Client()
    adapter.rx_client = Client()
    adapter._initial_services = {}
    adapter._changed_services = []
    adapter._owned = []
    adapter._cleanup_installed = False
    adapter._final_quiescence = None
    plan = plan_document()
    plan["services"]["receiver_required"] = ["SoapySDRServer"]
    adapter.inspect_services_and_ownership(plan)
    adapter.install_cleanup(plan)
    assert adapter.rx_services.running is True
    monkeypatch.setattr(adapter, "_quiescence", lambda plan, authorization: True)
    monkeypatch.setattr(adapter, "close", lambda deadline_s=None: True)

    cleanup = adapter.cleanup(plan)

    assert cleanup["outcome"] == "verified"
    assert adapter.rx_services.running is False


def test_overall_deadline_is_cumulative_and_reserves_cleanup(tmp_path: Path, monkeypatch) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._session_deadline = 100.0
    adapter._cleanup_reserve_s = 10.0
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.monotonic", lambda: 90.1)
    with pytest.raises(RealSessionError, match="overall deadline"):
        adapter._remaining(5.0, reserve_cleanup=True)


def _bounded_tone_envelope(plan: dict, cycle: int) -> dict:
    return {
        "protocol_version": 1,
        "request_id": f"cycle-{cycle}",
        "operation": "bounded-tone",
        "plan_sha256": plan["remote_helper"]["plan_sha256"],
        "helper_identity": plan["remote_helper"]["identity"],
        "outcome": "completed",
        "result": {
            "schema_version": 1,
            "evidence_type": "bounded_tone_control",
            "request_id": f"cycle-{cycle}",
            "frequency_hz": int(plan["frequency_hz"]),
            "duration_ms": 2000,
            "outer_timeout_s": 3.0,
            "loopback_host": "::1",
            "port": 31416,
            "path": "/",
            "maximum_frame_bytes": 16384,
            "start_response": {},
            "terminal_response": {},
            "cleanup_attempted": False,
            "completed": True,
            "qualification_claim": False,
            "wsprrypi_revision": plan["remote_helper"]["wsprrypi_revision"],
        },
    }


def test_tone_pattern_uses_absolute_deadlines_and_stops_every_cycle(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = bare_adapter(tmp_path)
    clock = {"now": 100.0}
    sleeps = []
    requests = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    class Client:
        def request_evidence(self, operation, payload):
            assert operation == "bounded-tone"
            requests.append(payload)
            clock["now"] += payload["duration_ms"] / 1000
            return _bounded_tone_envelope(plan, len(requests))

    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.sleep", sleep)
    adapter.tx_client = Client()

    class Worker:
        running = True

        def is_alive(self):
            return self.running

        def join(self, timeout=None):
            self.running = False

    worker = Worker()
    captured = [{"capture": "complete"}]
    adapter._capture_tasks = [(worker, threading.Event())]
    plan = tone_plan_document()
    result = adapter._run_tone_pattern_cycles(
        plan, worker, adapter._capture_tasks[0][1], captured, []
    )
    assert result == captured[0]
    assert len(requests) == 3
    assert sum(sleeps) == 8.0
    assert len([path for path in adapter._artifacts if "bounded-tone" in path.name]) == 3


def test_tone_pattern_owns_one_revision_bound_loopback_server(tmp_path: Path, monkeypatch) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._cleanup_installed = True
    adapter._owned = []
    adapter.tx_client = object()
    plan = tone_plan_document()
    observed = {}

    class Process:
        def stop(self):
            return LaunchResult(
                return_code=-15,
                stdout="",
                stderr="",
                timed_out=False,
                cancelled=True,
                disconnected=False,
                cleanup_verified=True,
                handle_id="tone-server",
                stop_requested=True,
                running_before_stop=True,
            )

    class Launcher:
        def __init__(self, client, hard_timeout_s, executable_sha256, pinned_arguments=None):
            observed.update(
                client=client,
                hard_timeout_s=hard_timeout_s,
                executable_sha256=executable_sha256,
                pinned_arguments=pinned_arguments,
            )

        def begin(self, arguments):
            observed["arguments"] = arguments
            return Process()

    monkeypatch.setattr("wsprrypi_qualification.live_adapters.SshOwnedProcessLauncher", Launcher)
    monkeypatch.setattr(
        adapter,
        "_run_tone_pattern_cycles",
        lambda plan, worker, cancellation, captured, errors, **kwargs: captured[0],
    )
    monkeypatch.setattr(adapter, "_retain_transmitter_result", lambda *args, **kwargs: None)
    result = adapter._run_tone_pattern(
        plan, object(), threading.Event(), [{"capture": "complete"}], []
    )
    assert result == {"capture": "complete"}
    assert observed["arguments"] == tuple(plan["tone_server"]["arguments"])
    assert observed["pinned_arguments"] == {
        plan["tone_server"]["configuration"]["path"]: plan["tone_server"]["configuration"]["sha256"]
    }
    assert adapter._owned == []


def test_tone_pattern_scheduler_overshoot_does_not_consume_extra_rf_budget(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = bare_adapter(tmp_path)
    clock = {"now": 100.0}
    requests = []

    def sleep(seconds):
        clock["now"] += seconds + 0.01

    plan = tone_plan_document()

    class Client:
        def request_evidence(self, operation, payload):
            requests.append(payload)
            clock["now"] += payload["duration_ms"] / 1000
            return _bounded_tone_envelope(plan, len(requests))

    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.sleep", sleep)
    adapter.tx_client = Client()

    class Worker:
        running = True

        def is_alive(self):
            return self.running

        def join(self, timeout=None):
            self.running = False

    worker = Worker()
    captured = [{"capture": "complete"}]
    cancellation = threading.Event()
    adapter._capture_tasks = [(worker, cancellation)]

    assert adapter._run_tone_pattern_cycles(plan, worker, cancellation, captured, []) == captured[0]
    assert len(requests) == 3


def test_tone_pattern_rejects_over_budget_cycle_before_launch(tmp_path: Path, monkeypatch) -> None:
    adapter = bare_adapter(tmp_path)
    clock = {"now": 100.0}
    requests = []

    def sleep(seconds):
        clock["now"] += seconds

    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.sleep", sleep)

    class Worker:
        def is_alive(self):
            return True

    plan = tone_plan_document()

    class Client:
        def request_evidence(self, operation, payload):
            requests.append(payload)
            clock["now"] += payload["duration_ms"] / 1000
            return _bounded_tone_envelope(plan, len(requests))

    adapter.tx_client = Client()
    plan["tone_schedule"]["maximum_rf_on_seconds"] = 3
    worker = Worker()
    cancellation = threading.Event()
    adapter._capture_tasks = [(worker, cancellation)]

    with pytest.raises(RealSessionError, match="exceeds its cumulative RF-on bound"):
        adapter._run_tone_pattern_cycles(plan, worker, cancellation, [], [])
    assert len(requests) == 1


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
        def __init__(self, client, hard_timeout_s, executable_sha256, pinned_arguments=None):
            observed.update(
                client=client,
                hard_timeout_s=hard_timeout_s,
                executable_sha256=executable_sha256,
                pinned_arguments=pinned_arguments,
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


def test_published_artifact_index_resolves_relative_source_dependency_offline(
    tmp_path: Path,
) -> None:
    adapter = bare_adapter(tmp_path)
    source = tmp_path / "source with spaces"
    source.mkdir()
    contract = source / "tone-plan.json"
    contract.write_text('{"sealed": true}\n', encoding="utf-8")
    evidence = source / "observations.json"
    reference = artifact(contract)
    reference["path"] = contract.name
    evidence.write_text(json.dumps({"plan": reference}), encoding="utf-8")
    adapter._artifacts = [contract, evidence]
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    adapter.publish_artifacts(bundle)
    shutil.rmtree(source)

    adapter.validate_published_artifacts(bundle)
