import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.unit.test_cw_contracts import _chain
from tests.unit.test_real_session import plan_document, tone_plan_document
from wsprrypi_qualification.cw_iq import analyze_synthetic_iq, generate_synthetic_iq
from wsprrypi_qualification.cw_reference import (
    generate_expected_events,
    required_keyed_capture_sample_count,
    validate_keyed_capture_margin,
)
from wsprrypi_qualification.live_adapters import (
    LiveAdapterPaths,
    ProductionRealSessionAdapters,
    _coherent_capture_launch_epoch,
    _derive_rebound_expected_events,
    _derive_scheduled_mode_plan,
    _intentional_carrier_stop_verified,
    _owned_process_released,
    _reject_git_worktree_runtime,
    _retained_capture_covers_wspr_frames,
    _stage_bound_artifact,
    _wspr_frame_transitions,
)
from wsprrypi_qualification.manifests import build_manifest, render_manifest, write_manifest
from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.real_capabilities import (
    LaunchResult,
    RuntimeAuthorization,
    ServiceState,
    capability_plan_sha256,
)
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
    adapter._publication_budget_s = float("inf")
    adapter._capture_tasks = []
    adapter._capture_artifacts = {}
    return adapter


def test_progress_hook_does_not_reset_live_session_state(tmp_path: Path) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._session_deadline = 123.0
    adapter._cleanup_reserve_s = 7.0

    adapter.set_progress(lambda *args: None)

    assert adapter._session_deadline == 123.0
    assert adapter._cleanup_reserve_s == 7.0


def test_begin_wspr_session_rejects_insufficient_resolved_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._session_deadline = None
    plan = plan_document()
    now = datetime(2026, 8, 12, 19, 58, 20, tzinfo=UTC)
    monkeypatch.setattr(
        "wsprrypi_qualification.live_adapters.datetime",
        type("FixedDatetime", (), {"now": staticmethod(lambda tz: now)}),
    )

    with pytest.raises(RealSessionError, match="cannot contain its resolved slot wait"):
        adapter.begin_session(plan)


@pytest.mark.parametrize("mode", ["qrss", "fskcw", "dfcw"])
def test_scheduled_keyed_rebase_preserves_capture_guard(tmp_path: Path, mode: str) -> None:
    plan_path, *_ = _chain(tmp_path, mode)
    mode_plan = json.loads(plan_path.read_text())
    mode_plan["protocol"]["message"] = "ETE"
    mode_plan["protocol"]["dot_seconds"] = 0.7
    mode_plan["protocol"]["pre_quiet_seconds"] = 2.0
    mode_plan["protocol"]["post_quiet_seconds"] = 2.0
    events = generate_expected_events(mode_plan)
    mode_plan["capture_contract"]["sample_count"] = required_keyed_capture_sample_count(mode_plan)
    capture_start = datetime(2026, 8, 24, 19, 0, tzinfo=UTC)

    scheduled, relative_start = _derive_scheduled_mode_plan(
        mode_plan,
        capture_start,
        capture_start + timedelta(seconds=2.027201),
    )

    assert relative_start == 2.027201
    validate_keyed_capture_margin(scheduled)
    assert generate_expected_events(scheduled)[-1]["end_s"] == events[-1]["end_s"]


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

    def carrier_command(arguments, deadline):
        assert arguments[arguments.index("--cw-mode-plan") + 1] == str(work / "tone-plan.json")
        assert arguments[arguments.index("--cw-expected-events") + 1] == str(
            work / "tone-expected-events.json"
        )
        assert (work / "tone-plan.json").is_file()
        assert (work / "tone-expected-events.json").is_file()

    monkeypatch.setattr(adapter, "_run_offline", carrier_command)

    def load_document(path: Path, schema: str) -> dict[str, object]:
        if schema == "carrier-analysis.schema.json":
            return {
                "gate_outcome": "passed",
                "metrics": {
                    "noise_guard": {"below_contrast_window_count": 0, "outcome": "passed"},
                    "strongest_transmitter_added_frequency_hz": 14_097_200.0,
                    "strongest_offset_hz": 100.0,
                    "best_20hz_resolved_power_share": 0.9,
                    "strongest_feature_contrast_db": 20.0,
                },
                "contract": {
                    "gate_policy": "target_window_relative_carrier_acquisition_v3",
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
        work / "receiver-calibration-binding.json",
        retained_plan,
        sealed_expected,
        retained_expected,
        work / "carrier-analysis.json",
        work / "tone-acquired-capture.json",
    )
    assert sealed_expected.read_bytes() == expected_source.read_bytes()
    assert json.loads(retained_expected.read_text(encoding="utf-8"))["plan"] == artifact(
        retained_plan
    )
    assert result["details"]["gate_outcome"] == "passed"
    assert result["details"]["mode_gate"] == "not_applicable"


def test_live_tone_cadence_failure_blocks_progress_without_rewriting_frequency(
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
                    "noise_guard": {"below_contrast_window_count": 0, "outcome": "passed"},
                    "strongest_transmitter_added_frequency_hz": 14_097_286.0,
                    "strongest_offset_hz": 186.0,
                    "best_20hz_resolved_power_share": 0.99,
                    "strongest_feature_contrast_db": 110.0,
                },
                "contract": {
                    "gate_policy": "target_window_relative_carrier_acquisition_v3",
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
    assert result["details"]["cadence_gate"] == "failed"
    assert result["details"]["mode_gate"] == "not_applicable"
    assert work / "tone-observations.json" in adapter._artifacts
    assert work / "tone-mode-gate.json" in adapter._artifacts


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

    carrier = work / "carrier-analysis.json"
    carrier.write_text(
        json.dumps(
            {
                "contract": {
                    "temporal_cw_reference": {
                        "plan": artifact(retained_plan),
                        "expected_events": artifact(retained_expected),
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    adapter = bare_adapter(work)
    adapter._artifacts = [
        carrier,
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


def test_helper_revision_mismatch_reports_expected_and_observed(tmp_path: Path) -> None:
    adapter = bare_adapter(tmp_path)
    plan = plan_document()
    plan["source"]["parent_revision"] = "1" * 40
    plan["source"]["submodule_revision"] = "2" * 40

    class ServiceProvider:
        def inspect(self, service: str) -> object:
            return object()

    class Process:
        def __init__(self, revision: str) -> None:
            self.revision = revision

        def wait(self, deadline_s: float, cancellation: object) -> LaunchResult:
            return LaunchResult(0, self.revision + "\n", "", cleanup_verified=True)

    revisions = iter(("3" * 40, "4" * 40))

    class Launcher:
        def begin(self, arguments: tuple[str, ...]) -> Process:
            return Process(next(revisions))

    adapter.tx_services = ServiceProvider()
    adapter.rx_services = ServiceProvider()
    adapter.source_launcher = Launcher()

    with pytest.raises(
        RealSessionError,
        match=(
            "expected parent "
            + "1" * 40
            + " and component tree "
            + "2" * 40
            + "; observed parent "
            + "3" * 40
            + " and component tree "
            + "4" * 40
        ),
    ):
        adapter.verify_helper(plan)


def test_same_host_receiver_channel_inspects_shared_physical_service(tmp_path: Path) -> None:
    adapter = bare_adapter(tmp_path)
    plan = plan_document()
    plan["topology"] = "same_host_roles"
    plan["transport"] = "local_role_channels"
    plan["receiver"]["host"] = plan["host"]
    plan["services"]["receiver"] = []
    inspected: list[tuple[str, str]] = []

    class ServiceProvider:
        def __init__(self, role: str) -> None:
            self.role = role

        def inspect(self, service: str) -> object:
            inspected.append((self.role, service))
            return object()

    class Process:
        def __init__(self, revision: str) -> None:
            self.revision = revision

        def wait(self, deadline_s: float, cancellation: object) -> LaunchResult:
            return LaunchResult(0, self.revision + "\n", "", cleanup_verified=True)

    revisions = iter((plan["source"]["parent_revision"], plan["source"]["submodule_revision"]))

    class Launcher:
        def begin(self, arguments: tuple[str, ...]) -> Process:
            return Process(next(revisions))

    adapter.tx_services = ServiceProvider("transmitter")
    adapter.rx_services = ServiceProvider("receiver")
    adapter.source_launcher = Launcher()

    evidence = adapter.verify_helper(plan)

    assert evidence["outcome"] == "passed"
    assert inspected == [
        ("transmitter", plan["services"]["transmitter"][0]),
        ("receiver", plan["services"]["transmitter"][0]),
    ]
    assert plan["services"]["receiver"] == []


def test_split_host_receiver_channel_requires_own_service(tmp_path: Path) -> None:
    adapter = bare_adapter(tmp_path)
    plan = plan_document()
    plan["services"]["receiver"] = []

    class ServiceProvider:
        def inspect(self, service: str) -> object:
            return object()

    adapter.tx_services = ServiceProvider()
    adapter.rx_services = ServiceProvider()

    with pytest.raises(
        RealSessionError, match="each live host requires an inspectable service binding"
    ):
        adapter.verify_helper(plan)


def test_helper_suboperations_share_the_aggregate_verification_envelope(tmp_path: Path) -> None:
    adapter = bare_adapter(tmp_path)
    plan = plan_document()
    plan["deadlines"]["helper_s"] = 0.05

    class SlowServiceProvider:
        def inspect(self, service: str) -> object:
            time.sleep(0.06)
            return object()

    class Process:
        def __init__(self, revision: str) -> None:
            self.revision = revision

        def wait(self, deadline_s: float, cancellation: object) -> LaunchResult:
            assert deadline_s > 0
            return LaunchResult(0, self.revision + "\n", "", cleanup_verified=True)

    revisions = iter((plan["source"]["parent_revision"], plan["source"]["submodule_revision"]))

    class Launcher:
        def begin(self, arguments: tuple[str, ...]) -> Process:
            return Process(next(revisions))

    adapter.tx_services = SlowServiceProvider()
    adapter.rx_services = SlowServiceProvider()
    adapter.source_launcher = Launcher()

    evidence = adapter.verify_helper(plan)

    assert evidence["outcome"] == "passed"
    assert evidence["elapsed_s"] > plan["deadlines"]["helper_s"]
    assert evidence["elapsed_s"] < evidence["deadline_s"]


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
        "repository_integrity": [],
        "stop_requested": False,
        "running_before_stop": None,
        "outcome": "failed",
    }


def test_runtime_directory_inside_git_worktree_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "target checkout"
    repository.mkdir()
    subprocess.run(("git", "-C", str(repository), "init", "-q"), check=True)
    with pytest.raises(RealSessionError, match="inside a Git worktree"):
        _reject_git_worktree_runtime(repository / "runs/session")


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


def test_coherent_capture_guard_covers_complete_bounded_receiver_setup() -> None:
    slot = datetime(2026, 8, 13, 20, 44, tzinfo=UTC)
    launch = _coherent_capture_launch_epoch(slot, 5, 5)
    assert launch == slot.timestamp() - 10
    # The live failure took 591 ms from helper start to retained capture.  With
    # the complete readiness bound as the guard, the same startup is safely early.
    retained = datetime.fromtimestamp(launch + 0.591, UTC)
    assert _retained_capture_covers_wspr_frames(
        {
            "timestamps": {"retained_capture_start_utc": retained.isoformat()},
            "retained_sample_count": 92_500_000,
        },
        [slot, slot + timedelta(seconds=120), slot + timedelta(seconds=240)],
        sample_rate_hz=250_000,
        required_margin_s=5,
    )


def test_wspr_frames_transition_independently_at_exact_utc_boundaries() -> None:
    slots = [1_000.0, 1_120.0, 1_240.0]
    announced: set[int] = set()
    completed: set[int] = set()

    assert _wspr_frame_transitions(slots, 999.999, announced, completed) == []
    assert _wspr_frame_transitions(slots, 1_000.0, announced, completed) == [(1, "started")]
    assert _wspr_frame_transitions(slots, 1_110.591, announced, completed) == []
    assert _wspr_frame_transitions(slots, 1_110.592, announced, completed) == [(1, "completed")]
    assert _wspr_frame_transitions(slots, 1_120.0, announced, completed) == [(2, "started")]
    assert announced == {1, 2}
    assert completed == {1}


def test_wspr_wav_and_decode_emit_started_and_completed_per_frame(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = bare_adapter(tmp_path)
    iq = tmp_path / "coherent.cf32"
    metadata = tmp_path / "coherent.json"
    iq.write_bytes(b"12345678")
    metadata.write_text("{}", encoding="utf-8")
    adapter._capture_artifacts["coherent"] = (iq, metadata)
    adapter._acquired_carrier_frequency_hz = None
    updates: list[tuple[str, str, int | None, int | None]] = []
    adapter.set_progress(
        lambda stage, status, detail, item, item_count: updates.append(
            (stage, status, item, item_count)
        )
    )

    def run_offline(arguments: tuple[str, ...], timeout_s: float) -> None:
        del timeout_s
        command = arguments[0]
        if command == "make-slot-wav":
            Path(arguments[4]).write_text("{}", encoding="utf-8")
            (tmp_path / f"slot-{len(list(tmp_path.glob('slot-*.wav')))}.wav").write_bytes(b"RIFF")
        elif command == "decode-wspr":
            Path(arguments[3]).write_text("{}", encoding="utf-8")
        else:
            Path(arguments[1]).write_text("{}", encoding="utf-8")

    def load_document(path: Path, schema: str) -> dict:
        if schema == "audio-conversion.schema.json":
            number = int(path.stem.split("-")[-1])
            wav = tmp_path / f"slot-{number}.wav"
            wav.write_bytes(b"RIFF")
            return {"output": {"path": str(wav)}}
        if schema == "decoder-evidence.schema.json":
            return {
                "slot_utc": "2026-08-25T00:00:00Z",
                "gate_outcome": "passed",
                "decoder_data_artifacts": [],
            }
        return {"gate_outcome": "passed"}

    monkeypatch.setattr(adapter, "_run_offline", run_offline)
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.load_json_document", load_document)
    monkeypatch.setattr(adapter, "_stage", lambda *args, **kwargs: {"outcome": "completed"})
    plan = plan_document()
    adapter.create_wavs_and_decode(plan, {})

    assert updates == [
        (stage, status, item, 3)
        for item in range(1, 4)
        for stage, status in (
            ("wspr_wav", "started"),
            ("wspr_wav", "completed"),
            ("wspr_decode", "started"),
            ("wspr_decode", "completed"),
        )
    ]


@pytest.mark.parametrize(
    ("start_offset_s", "sample_count", "expected"),
    [
        (-5.0, 92_500_000, True),
        (-5.001, 92_500_000, True),
        (-4.999, 92_500_000, False),
        (-4.909, 92_500_000, False),
        (-5.0, 91_249_999, False),
        (-5.091, 92_500_000, True),
    ],
)
def test_retained_capture_proves_all_decoder_windows(
    start_offset_s: float, sample_count: int, expected: bool
) -> None:
    slot = datetime(2026, 8, 13, 20, 44, tzinfo=UTC)
    retained = slot + timedelta(seconds=start_offset_s)
    assert (
        _retained_capture_covers_wspr_frames(
            {
                "timestamps": {"retained_capture_start_utc": retained.isoformat()},
                "retained_sample_count": sample_count,
            },
            [slot, slot + timedelta(seconds=120), slot + timedelta(seconds=240)],
            sample_rate_hz=250_000,
            required_margin_s=5,
        )
        is expected
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


def test_cleanup_uses_passive_route_contract_for_rp1(tmp_path: Path, monkeypatch) -> None:
    class Client:
        timeout_s = 1.0
        transport = object()

    adapter = bare_adapter(tmp_path)
    adapter.tx_client = Client()
    adapter.rx_client = Client()
    adapter._initial_services = {}
    adapter._changed_services = []
    adapter._owned = []
    adapter._capture_tasks = []
    adapter._session_deadline = None
    observed_authorizations: list[RuntimeAuthorization] = []
    plan = plan_document()
    plan["backend"] = "rp1_gpclk"
    plan["output"] = "GPIO4"
    plan["backend_contract"] = {"rp1_route": "gpio4"}

    def quiescence(plan: dict[str, object], authorization: RuntimeAuthorization) -> bool:
        observed_authorizations.append(authorization)
        return True

    monkeypatch.setattr(adapter, "_quiescence", quiescence)
    monkeypatch.setattr(adapter, "close", lambda deadline_s=None: True)

    cleanup = adapter.cleanup(plan)

    assert cleanup["outcome"] == "verified"
    assert len(observed_authorizations) == 1
    assert observed_authorizations[0].plan_sha256 == capability_plan_sha256(
        {"route": "gpio4", "read_only": True, "acquire_endpoint": False}
    )


def test_overall_deadline_is_cumulative_and_reserves_cleanup(tmp_path: Path, monkeypatch) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._session_deadline = 100.0
    adapter._cleanup_reserve_s = 10.0
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.monotonic", lambda: 90.1)
    with pytest.raises(RealSessionError, match="overall deadline"):
        adapter._remaining(5.0, reserve_cleanup=True)


def test_live_publication_refuses_to_hide_its_deadline_overrun(tmp_path: Path, monkeypatch) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._publication_deadline = 100.0
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.monotonic", lambda: 100.1)

    with pytest.raises(RealSessionError, match="publication exceeded its hard deadline"):
        adapter._verify_publication_deadline()


def _bounded_tone_envelope(plan: dict, cycle: int, outer_timeout_s: float) -> dict:
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
            "outer_timeout_s": outer_timeout_s,
            "loopback_host": "::1",
            "port": 31416,
            "path": "/",
            "maximum_frame_bytes": 16384,
            "start_response": {},
            "terminal_response": {},
            "observed_responses": [{}, {}],
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
        def request_evidence(self, operation, payload, *, response_timeout_s=None):
            assert operation == "bounded-tone"
            requests.append((payload, response_timeout_s))
            clock["now"] += payload["duration_ms"] / 1000
            return _bounded_tone_envelope(plan, len(requests), payload["outer_timeout_s"])

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
    assert all(
        payload["outer_timeout_s"] == plan["deadlines"]["helper_s"] for payload, _ in requests
    )
    assert all(timeout == plan["deadlines"]["transmitter_s"] for _, timeout in requests)
    assert sum(sleeps) == 8.0
    assert len([path for path in adapter._artifacts if "bounded-tone" in path.name]) == 3


def test_rp1_tone_pattern_binds_development_confirmation_from_plan(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = bare_adapter(tmp_path)
    requests = []

    class Client:
        def request_evidence(self, operation, payload, *, response_timeout_s=None):
            requests.append(payload)
            return _bounded_tone_envelope(plan, len(requests), payload["outer_timeout_s"])

    adapter.tx_client = Client()
    plan = tone_plan_document()
    plan["backend"] = "rp1_gpclk"
    plan["output"] = "GPIO4"
    plan["backend_contract"] = {"rp1_route": "gpio4"}
    plan["rf_path"] = {
        "path_type": "conducted",
        "antenna_connected": False,
        "attenuation_db": 20,
    }
    plan["tone_schedule"]["cycles"] = 1

    class Worker:
        running = True

        def is_alive(self):
            return self.running

        def join(self, timeout=None):
            self.running = False

    worker = Worker()
    monkeypatch.setattr(adapter, "_sleep_until", lambda *args, **kwargs: None)
    adapter._run_tone_pattern_cycles(plan, worker, threading.Event(), [{"capture": "complete"}], [])
    assert requests[0]["rp1_development"] == {
        "enabled": True,
        "route": "GPIO4",
        "physical_connection": True,
        "attenuation_and_load": True,
        "bounded_operation": True,
        "non_radiating_topology": True,
        "experimental_acknowledged": True,
    }


def test_tone_pattern_owns_one_revision_bound_loopback_server(tmp_path: Path, monkeypatch) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._cleanup_installed = True
    adapter._owned = []
    adapter.tx_client = object()
    plan = tone_plan_document()
    observed = {}
    ordering = []

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
        def __init__(
            self,
            client,
            hard_timeout_s,
            executable_sha256,
            pinned_arguments=None,
            repository_guard=None,
            cleanup_timeout_s=None,
        ):
            observed.update(
                client=client,
                hard_timeout_s=hard_timeout_s,
                executable_sha256=executable_sha256,
                pinned_arguments=pinned_arguments,
                repository_guard=repository_guard,
                cleanup_timeout_s=cleanup_timeout_s,
            )

        def begin(self, arguments):
            observed["arguments"] = arguments
            ordering.append("server_started")
            return Process()

    monkeypatch.setattr("wsprrypi_qualification.live_adapters.SshOwnedProcessLauncher", Launcher)
    monkeypatch.setattr(
        adapter,
        "_run_tone_pattern_cycles",
        lambda plan, worker, cancellation, captured, errors, **kwargs: (
            ordering.append("cycles_started"),
            captured[0],
        )[1],
    )
    monkeypatch.setattr(
        adapter,
        "_capture_into",
        lambda captured, errors, plan, kind, count, cancellation: (
            ordering.append("capture_started"),
            captured.append({"capture": "complete"}),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_wait_capture_ready",
        lambda plan, kind, worker, errors: worker.join(),
    )
    monkeypatch.setattr(adapter, "_retain_transmitter_result", lambda *args, **kwargs: None)
    result = adapter._run_tone_pattern(plan)
    assert result == {"capture": "complete"}
    assert observed["arguments"] == tuple(plan["tone_server"]["arguments"])
    assert observed["repository_guard"] == {
        "protected_source_roots": plan["tone_server"]["protected_source_roots"],
        "git_path": plan["source"]["git_path"],
        "git_sha256": plan["source"]["git_sha256"],
        "working_directory": plan["tone_server"]["working_directory"],
        "mutable_inputs": [
            {
                "source_path": plan["tone_server"]["configuration_source"]["path"],
                "source_sha256": plan["tone_server"]["configuration_source"]["sha256"],
                "runtime_path": plan["tone_server"]["configuration"]["path"],
                "runtime_sha256": plan["tone_server"]["configuration"]["sha256"],
            }
        ],
        "writable_paths": [plan["tone_server"]["configuration"]["path"]],
        "inspection_timeout_s": 20,
    }
    assert observed["pinned_arguments"] == {
        plan["tone_server"]["configuration"]["path"]: plan["tone_server"]["configuration"]["sha256"]
    }
    assert observed["cleanup_timeout_s"] == plan["deadlines"]["cleanup_s"]
    assert ordering == ["server_started", "capture_started", "cycles_started"]
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
        def request_evidence(self, operation, payload, *, response_timeout_s=None):
            requests.append(payload)
            clock["now"] += payload["duration_ms"] / 1000
            return _bounded_tone_envelope(plan, len(requests), payload["outer_timeout_s"])

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
        def request_evidence(self, operation, payload, *, response_timeout_s=None):
            requests.append(payload)
            clock["now"] += payload["duration_ms"] / 1000
            return _bounded_tone_envelope(plan, len(requests), payload["outer_timeout_s"])

    adapter.tx_client = Client()
    plan["tone_schedule"]["maximum_rf_on_seconds"] = 3
    worker = Worker()
    cancellation = threading.Event()
    adapter._capture_tasks = [(worker, cancellation)]

    with pytest.raises(RealSessionError, match="exceeds its cumulative RF-on bound"):
        adapter._run_tone_pattern_cycles(plan, worker, cancellation, [], [])
    assert len(requests) == 1


def test_tone_pattern_retains_helper_rejection_before_failing(tmp_path: Path, monkeypatch) -> None:
    adapter = bare_adapter(tmp_path)
    clock = {"now": 100.0}
    monkeypatch.setattr("wsprrypi_qualification.live_adapters.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        "wsprrypi_qualification.live_adapters.time.sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )

    class Client:
        def request_evidence(self, operation, payload, *, response_timeout_s=None):
            raise RuntimeError('rejected frame: {"command":"log"}')

    class Worker:
        def is_alive(self):
            return True

    adapter.tx_client = Client()
    plan = tone_plan_document()
    cancellation = threading.Event()
    worker = Worker()
    adapter._capture_tasks = [(worker, cancellation)]

    with pytest.raises(RealSessionError, match="bounded Tone cycle 1 failed"):
        adapter._run_tone_pattern_cycles(plan, worker, cancellation, [], [])

    failure = json.loads((tmp_path / "carrier-cycle-1-bounded-tone-failure.json").read_text())
    assert failure["cycle"] == 1
    assert failure["error_type"] == "RuntimeError"
    assert '"command":"log"' in failure["error"]
    assert failure["qualification_claim"] is False
    assert tmp_path / "carrier-cycle-1-bounded-tone-failure.json" in adapter._artifacts


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
        def __init__(
            self,
            client,
            hard_timeout_s,
            executable_sha256,
            pinned_arguments=None,
            repository_guard=None,
            cleanup_timeout_s=None,
        ):
            observed.update(
                client=client,
                hard_timeout_s=hard_timeout_s,
                executable_sha256=executable_sha256,
                pinned_arguments=pinned_arguments,
                repository_guard=repository_guard,
                cleanup_timeout_s=cleanup_timeout_s,
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
    assert observed["cleanup_timeout_s"] == plan["deadlines"]["cleanup_s"]


@pytest.mark.parametrize(
    ("tone", "operation"),
    [(True, "carrier"), (False, "frames")],
)
def test_rp1_direct_wspr_launch_binds_distinct_operation_confirmation(
    tmp_path: Path, monkeypatch, tone: bool, operation: str
) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._cleanup_installed = True
    adapter._initial_services = {}
    adapter._changed_services = []
    adapter._owned = []
    observed = {}

    class Launcher:
        def begin(self, arguments):
            observed["arguments"] = arguments
            return object()

    class Application:
        arguments = ("/tx", "--test-tone", "14097100") if tone else ("/tx", "-n", "3")

        def to_document(self):
            return {}

    adapter.tx_launcher = Launcher()
    monkeypatch.setattr(adapter, "_application", lambda plan, tone: Application())
    monkeypatch.setattr(
        "wsprrypi_qualification.live_adapters.write_json_new", lambda *args, **kwargs: None
    )
    plan = tone_plan_document()
    plan["backend"] = "rp1_gpclk"
    plan["backend_contract"] = {"rp1_route": "gpio4"}
    plan["rf_path"] = {
        "path_type": "conducted",
        "antenna_connected": False,
        "attenuation_db": 20,
    }

    process = adapter._begin_transmitter(plan, tone)

    assert process in adapter._owned
    arguments = observed["arguments"]
    option = arguments.index("--rp1-development-confirmation-json")
    confirmation = json.loads(arguments[option + 1])
    assert confirmation == {
        "enabled": True,
        "route": "GPIO4",
        "physical_connection_confirmed": True,
        "attenuation_and_load_confirmed": True,
        "bounded_operation_confirmed": True,
        "non_radiating_topology_confirmed": True,
        "experimental_status_acknowledged": True,
        "operation_id": (f"wspq-{resolved_real_plan_sha256(plan)[:16]}-{operation}"),
    }


def test_rp1_direct_wspr_launch_rejects_unsafe_path_before_process(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._cleanup_installed = True
    adapter._initial_services = {}
    adapter._changed_services = []
    adapter._owned = []

    class Launcher:
        def begin(self, arguments):
            raise AssertionError("unsafe plan reached process launch")

    class Application:
        arguments = ("/tx", "--test-tone", "14097100")

        def to_document(self):
            return {}

    adapter.tx_launcher = Launcher()
    monkeypatch.setattr(adapter, "_application", lambda plan, tone: Application())
    monkeypatch.setattr(
        "wsprrypi_qualification.live_adapters.write_json_new", lambda *args, **kwargs: None
    )
    plan = tone_plan_document()
    plan["backend"] = "rp1_gpclk"
    plan["backend_contract"] = {"rp1_route": "gpio4"}
    plan["rf_path"] = {
        "path_type": "conducted",
        "antenna_connected": True,
        "attenuation_db": 20,
    }

    with pytest.raises(RealSessionError, match="exact conducted"):
        adapter._begin_transmitter(plan, True)


def test_rp1_direct_wspr_launch_rejects_duplicate_confirmation(tmp_path: Path, monkeypatch) -> None:
    adapter = bare_adapter(tmp_path)
    adapter._cleanup_installed = True
    adapter._initial_services = {}
    adapter._changed_services = []

    class Launcher:
        def begin(self, arguments):
            raise AssertionError("duplicate confirmation reached process launch")

    class Application:
        arguments = ("/tx", "--rp1-development-confirmation-json", "{}")

        def to_document(self):
            return {}

    adapter.tx_launcher = Launcher()
    monkeypatch.setattr(adapter, "_application", lambda plan, tone: Application())
    monkeypatch.setattr(
        "wsprrypi_qualification.live_adapters.write_json_new", lambda *args, **kwargs: None
    )
    plan = tone_plan_document()
    plan["backend"] = "rp1_gpclk"

    with pytest.raises(RealSessionError, match="duplicate RP1"):
        adapter._begin_transmitter(plan, True)


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


def test_wspr_live_analysis_supplies_onset_plus_confirmation_budget(tmp_path: Path, monkeypatch):
    adapter = bare_adapter(tmp_path)
    adapter._capture_artifacts = {
        "rf_off": (tmp_path / "off.cf32", tmp_path / "off.json"),
        "rf_on": (tmp_path / "on.cf32", tmp_path / "on.json"),
    }
    commands = []

    class CapturedCommand(Exception):
        pass

    def intercept(arguments, deadline):
        commands.append(arguments)
        raise CapturedCommand

    monkeypatch.setattr(adapter, "_run_offline", intercept)
    with pytest.raises(CapturedCommand):
        adapter.analyze_carrier(plan_document(), {}, {})
    args = commands[0]
    assert args[0] == "analyze-carrier"
    assert args[args.index("--startup-acquisition-max-s") + 1] == "1.1"
    assert "--cw-mode-plan" not in args
