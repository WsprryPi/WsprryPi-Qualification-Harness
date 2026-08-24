import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import wsprrypi_qualification.live_adapters as live_adapters_module
import wsprrypi_qualification.live_keyed as live_keyed_module
from tests.unit.test_keyed_session_contracts import plan
from wsprrypi_qualification.cli import main
from wsprrypi_qualification.keyed_coordinator import KeyedCoordinatorError, publish_keyed_session
from wsprrypi_qualification.keyed_session_contracts import (
    compose_keyed_runtime_authorization,
    resolved_keyed_plan_sha256,
)
from wsprrypi_qualification.live_keyed import (
    ProductionKeyedAdapter,
    run_live_keyed_session,
)
from wsprrypi_qualification.offline import artifact


class SealedFakeLiveProviders:
    __slots__ = ("calls", "close_ok", "evidence_root", "failure", "repeat_evidence")

    def __init__(
        self,
        failure: str | None = None,
        *,
        close_ok: bool = True,
        evidence_root: Path | None = None,
        repeat_evidence: bool = False,
    ) -> None:
        if type(self) is not SealedFakeLiveProviders:
            raise TypeError("live keyed fake provider is sealed")
        self.failure, self.close_ok = failure, close_ok
        self.evidence_root = evidence_root
        self.repeat_evidence = repeat_evidence
        self.calls: list[tuple[str, int]] = []

    def _perform(self, name: str, number: int) -> bool:
        self.calls.append((name, number))
        return self.failure != name

    def preflight(self, resolved, number):
        del resolved
        return self._perform("preflight", number)

    def install_cleanup(self, resolved, number):
        del resolved
        return self._perform("cleanup_installed", number)

    def start_process(self, arguments, number):
        assert arguments[0] == "inputs/wsprrypi.json"
        return f"fake-process-{number}" if self._perform("process_started", number) else None

    def capture(self, resolved, number):
        del resolved
        if not self._perform("capture_completed", number):
            return None
        process_outcome = {
            "process_exit_failed": "failed",
            "process_wait_aborted": "aborted",
        }.get(self.failure, "passed")
        return (
            f"fake-capture-{number}",
            f"fake-acquisition-{number}",
            process_outcome,
        )

    def analyze(self, resolved, number):
        del resolved
        return (
            ("passed", f"fake-analysis-{number}")
            if self._perform("analysis_completed", number)
            else ("failed", f"failed-analysis-{number}")
        )

    def cleanup(self, resolved, number):
        del resolved
        return self._perform("cleanup_completed", number)

    def verify_quiescence(self, resolved, number):
        del resolved
        return self._perform("quiescence_verified", number)

    def evidence_paths(self, number):
        if self.evidence_root is None:
            return {}
        directory = self.evidence_root / str(number)
        directory.mkdir(parents=True)
        result = {}
        for role in ("process", "process_launch", "capture", "acquisition", "analysis"):
            path = directory / f"{role}.json"
            suffix = "same" if self.repeat_evidence else str(number)
            path.write_text(f"{role}-{suffix}\n", encoding="utf-8")
            result[role] = path
        return result

    def close(self):
        return self.close_ok


def _run(tmp_path: Path, providers: SealedFakeLiveProviders, mode: str = "QRSS"):
    resolved = plan(mode)
    authorization = compose_keyed_runtime_authorization(
        resolved, operator="operator", authorized_utc="2026-08-21T12:00:00Z"
    )
    return run_live_keyed_session(
        resolved,
        authorization,
        tmp_path,
        ProductionKeyedAdapter(providers),
    )


@pytest.mark.parametrize("mode", ("QRSS", "FSKCW", "DFCW"))
def test_production_adapter_success_uses_three_independent_transactions(
    tmp_path: Path, mode: str
) -> None:
    providers = SealedFakeLiveProviders()
    outcome = _run(tmp_path, providers, mode)
    assert outcome["result"]["final_status"] == "qualified"
    assert len(outcome["aggregate"]["transactions"]) == 3
    assert {number for _, number in providers.calls} == {1, 2, 3}


@pytest.mark.parametrize(
    "failure,expected",
    (
        ("preflight", "preflight_failed"),
        ("cleanup_installed", "aborted"),
        ("process_started", "aborted"),
        ("capture_completed", "fixture_blocked"),
        ("analysis_completed", "unqualified_keyed"),
        ("cleanup_completed", "cleanup_failed"),
        ("quiescence_verified", "cleanup_failed"),
    ),
)
def test_every_production_boundary_fails_closed_and_preserves_partial_output(
    tmp_path: Path, failure: str, expected: str
) -> None:
    providers = SealedFakeLiveProviders(failure)
    outcome = _run(tmp_path, providers)
    assert outcome["result"]["final_status"] == expected
    assert len(outcome["aggregate"]["transactions"]) == 1
    assert Path(outcome["bundle"]).is_dir()
    assert (Path(outcome["bundle"]) / "transaction-1.json").is_file()
    assert ("cleanup_completed", 1) in providers.calls
    assert ("quiescence_verified", 1) in providers.calls


@pytest.mark.parametrize(
    "failure,expected,lifecycle_outcome",
    (
        ("process_exit_failed", "unqualified_keyed", "failed"),
        ("process_wait_aborted", "aborted", "aborted"),
    ),
)
def test_transmitter_completion_is_not_misclassified_as_fixture_blockage(
    tmp_path: Path, failure: str, expected: str, lifecycle_outcome: str
) -> None:
    outcome = _run(tmp_path, SealedFakeLiveProviders(failure))
    transaction = outcome["aggregate"]["transactions"][0]
    assert transaction["lifecycle"][3]["outcome"] == "passed"
    assert transaction["lifecycle"][4]["outcome"] == lifecycle_outcome
    assert outcome["result"]["final_status"] == expected


def test_incomplete_provider_close_overrides_measurement_success(tmp_path: Path) -> None:
    outcome = _run(tmp_path, SealedFakeLiveProviders(close_ok=False))
    assert outcome["result"]["final_status"] == "cleanup_failed"
    assert len(outcome["aggregate"]["transactions"]) == 3


def test_production_evidence_bytes_are_retained_and_authenticated(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    outcome = _run(tmp_path / "output", SealedFakeLiveProviders(evidence_root=source_root))
    bundle = Path(outcome["bundle"])
    for transaction in outcome["aggregate"]["transactions"]:
        for record in transaction["artifacts"]:
            retained = bundle / record["path"]
            assert retained.read_text(encoding="utf-8") == (
                f"{record['role']}-{transaction['transaction_number']}\n"
            )


def test_repeated_diagnostic_bytes_are_retained_once_across_session(tmp_path: Path) -> None:
    outcome = _run(
        tmp_path / "output",
        SealedFakeLiveProviders(evidence_root=tmp_path / "sources", repeat_evidence=True),
    )
    artifacts = [
        artifact
        for transaction in outcome["aggregate"]["transactions"]
        for artifact in transaction["artifacts"]
    ]
    assert len(artifacts) == 5
    assert len({item["sha256"] for item in artifacts}) == len(artifacts)


@pytest.mark.parametrize("mutation", ("alter", "remove"))
def test_production_diagnostic_substitution_or_omission_is_rejected(
    tmp_path: Path, mutation: str
) -> None:
    resolved = plan()
    authorization = compose_keyed_runtime_authorization(
        resolved, operator="operator", authorized_utc="2026-08-21T12:00:00Z"
    )
    providers = SealedFakeLiveProviders(
        failure="capture_completed", evidence_root=tmp_path / "sources"
    )
    adapter = ProductionKeyedAdapter(providers)
    transaction = adapter.transaction(resolved, authorization, 1, None)
    source = next(iter(adapter.artifact_sources.values()))
    if mutation == "alter":
        source.write_text("substituted\n", encoding="utf-8")
    else:
        source.unlink()
    with pytest.raises(KeyedCoordinatorError, match=r"identity changed|unavailable"):
        publish_keyed_session(
            resolved,
            authorization,
            [transaction],
            tmp_path / "output",
            artifact_sources=adapter.artifact_sources,
        )


def test_cli_requires_every_gate_before_provider_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    resolved = plan()
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(resolved), encoding="utf-8")
    called = False

    def fail_builder(*args, **kwargs):
        nonlocal called
        del args, kwargs
        called = True
        raise AssertionError("provider builder must not run after digest rejection")

    monkeypatch.setattr(live_keyed_module, "build_production_keyed_adapter", fail_builder)
    result = main(
        [
            "run-cw-live-keyed",
            str(plan_path),
            str(tmp_path / "out"),
            "--work-directory",
            str(tmp_path / "work"),
            "--ssh",
            str(tmp_path / "ssh"),
            "--operator",
            "operator",
            "--confirm-plan-sha256",
            "0" * 64,
            "--enable-live-keyed",
            "--enable-rf",
        ]
    )
    assert result == 2
    assert not called
    assert "digest confirmation" in capsys.readouterr().err


def test_cli_success_path_uses_sealed_production_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    resolved = plan()
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(resolved), encoding="utf-8")
    monkeypatch.setattr(
        live_keyed_module,
        "build_production_keyed_adapter",
        lambda *args, **kwargs: ProductionKeyedAdapter(SealedFakeLiveProviders()),
    )
    result = main(
        [
            "run-cw-live-keyed",
            str(plan_path),
            str(tmp_path / "out"),
            "--work-directory",
            str(tmp_path / "work"),
            "--ssh",
            str(tmp_path / "ssh"),
            "--operator",
            "operator",
            "--confirm-plan-sha256",
            resolved_keyed_plan_sha256(resolved),
            "--enable-live-keyed",
            "--enable-rf",
        ]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["result"]["final_status"] == "qualified"


def test_cli_parser_requires_live_keyed_and_rf_flags() -> None:
    with pytest.raises(SystemExit):
        main(["run-cw-live-keyed"])


def test_production_provider_builder_uses_exact_pinned_ssh_and_helper_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = plan()
    bindings = resolved["capability_bindings"]
    local_paths = {}
    for name in (
        "ssh",
        "known_hosts",
        "receiver_helper",
        "receiver_helper_config",
        "capture_helper",
    ):
        path = (tmp_path / name).resolve()
        if name.endswith("config"):
            path.write_text(
                json.dumps(
                    {
                        "protocol_version": 1,
                        "helper_identity": "receiver-helper",
                        "allowed_services": ["wsprrypi.service", "sdrplay.service"],
                    }
                ),
                encoding="utf-8",
            )
        else:
            path.write_text(name, encoding="utf-8")
        local_paths[name] = path
        bindings[name] = artifact(path)
    bindings["transmitter_helper"]["path"] = "/opt/wspq/helper"
    bindings["transmitter_helper_config"]["path"] = "/etc/wspq/helper.json"
    bindings["transmitter_process_privilege_wrapper"]["path"] = "/usr/bin/sudo"
    digest = resolved_keyed_plan_sha256(resolved)
    assert "plan_sha256" not in json.loads(
        local_paths["receiver_helper_config"].read_text(encoding="utf-8")
    )
    assert resolved_keyed_plan_sha256(resolved) == digest
    commands = []

    class FakePersistentTransport:
        def __init__(self, command, cleanup_timeout_s):
            commands.append((command, cleanup_timeout_s))

        def exchange(self, encoded_request, timeout_s):
            raise AssertionError((encoded_request, timeout_s))

        def close(self):
            return None

    monkeypatch.setattr(live_adapters_module, "PersistentHelperTransport", FakePersistentTransport)
    providers = live_adapters_module.build_keyed_capability_providers(
        resolved,
        ssh_executable=local_paths["ssh"],
        work_directory=(tmp_path / "work").resolve(),
    )
    ssh_command = commands[0][0]
    assert ssh_command == (
        str(local_paths["ssh"]),
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={local_paths['known_hosts']}",
        "--",
        resolved["transmitter"]["host"],
        "/opt/wspq/helper --serve --config /etc/wspq/helper.json "
        f"--plan-sha256 {digest} "
        f"--helper-sha256 {bindings['transmitter_helper']['sha256']} "
        f"--config-sha256 {bindings['transmitter_helper_config']['sha256']}",
    )
    assert commands[1][0] == (
        str(local_paths["receiver_helper"]),
        "--serve",
        "--config",
        str(local_paths["receiver_helper_config"]),
        "--plan-sha256",
        digest,
        "--helper-sha256",
        bindings["receiver_helper"]["sha256"],
        "--config-sha256",
        bindings["receiver_helper_config"]["sha256"],
    )
    assert (
        providers.launcher.privilege_wrapper_path
        == bindings["transmitter_process_privilege_wrapper"]["path"]
    )
    assert (
        providers.launcher.privilege_wrapper_sha256
        == bindings["transmitter_process_privilege_wrapper"]["sha256"]
    )
    assert providers.close()


class _ServiceState:
    def __init__(self, running: bool) -> None:
        self.running = running


class _FakeServiceProvider:
    def __init__(self, running: bool, *, refuse_change: bool = False) -> None:
        self.running = running
        self.refuse_change = refuse_change
        self.requests: list[bool] = []

    def inspect(self, name: str) -> _ServiceState:
        assert name
        return _ServiceState(self.running)

    def set_running(self, name: str, running: bool) -> _ServiceState:
        assert name
        self.requests.append(running)
        if not self.refuse_change:
            self.running = running
        return _ServiceState(self.running)


def _service_provider_fixture(
    *, receiver_running: bool, receiver_refuses_change: bool = False
) -> tuple[
    live_adapters_module.KeyedCapabilityProviders, _FakeServiceProvider, _FakeServiceProvider
]:
    providers = object.__new__(live_adapters_module.KeyedCapabilityProviders)
    tx = _FakeServiceProvider(True)
    rx = _FakeServiceProvider(receiver_running, refuse_change=receiver_refuses_change)
    providers.tx_services = tx
    providers.rx_services = rx
    providers.initial_services = {
        1: [(tx, "wsprrypi.service", True), (rx, "sdrplay.service", receiver_running)]
    }
    providers.capture_tasks = {}
    providers.owned = {}
    providers.process_outcomes = {}
    return providers, tx, rx


def _service_plan() -> dict:
    resolved = plan()
    resolved["capability_bindings"]["services"] = [  # type: ignore[index]
        "tx:wsprrypi.service",
        "rx:sdrplay.service",
    ]
    resolved["capability_bindings"]["required_receiver_services"] = [  # type: ignore[index]
        "rx:sdrplay.service"
    ]
    return resolved


def test_required_inactive_receiver_service_starts_after_cleanup_installation_and_restores() -> (
    None
):
    providers, tx, rx = _service_provider_fixture(receiver_running=False)
    resolved = _service_plan()
    assert providers.install_cleanup(resolved, 1)
    assert tx.running is False
    assert rx.running is True
    assert providers.cleanup(resolved, 1)
    assert tx.running is True
    assert rx.running is False


def test_required_initially_active_receiver_service_is_preserved_and_restored() -> None:
    providers, _, rx = _service_provider_fixture(receiver_running=True)
    resolved = _service_plan()
    assert providers.install_cleanup(resolved, 1)
    assert rx.requests == []
    assert providers.cleanup(resolved, 1)
    assert rx.running is True
    assert rx.requests == []


def test_required_receiver_service_start_failure_fails_closed() -> None:
    providers, _, rx = _service_provider_fixture(
        receiver_running=False, receiver_refuses_change=True
    )
    assert not providers.install_cleanup(_service_plan(), 1)
    assert rx.running is False


def test_required_receiver_service_restoration_failure_fails_cleanup() -> None:
    providers, _, rx = _service_provider_fixture(receiver_running=False)
    resolved = _service_plan()
    assert providers.install_cleanup(resolved, 1)
    rx.refuse_change = True
    assert not providers.cleanup(resolved, 1)
    assert rx.running is True


def test_keyed_capture_is_ready_and_prequiet_completes_before_process_launch(
    tmp_path: Path,
) -> None:
    from tests.unit.test_cw_contracts import _chain

    reference = tmp_path / "reference"
    reference.mkdir()
    mode_plan_path = _chain(reference, "qrss")[0]
    mode_document = json.loads(mode_plan_path.read_text(encoding="utf-8"))
    mode_document["protocol"]["repetitions"] = 1
    mode_document["protocol"]["pre_quiet_seconds"] = 0.02
    mode_document["capture_contract"]["sample_count"] = 10
    mode_plan_path.write_text(json.dumps(mode_document), encoding="utf-8")
    helper = tmp_path / "capture-helper"
    helper.write_text("helper", encoding="utf-8")
    resolved = plan()
    resolved["reference"]["plan"] = artifact(mode_plan_path)  # type: ignore[index]
    resolved["capability_bindings"]["capture_helper"] = artifact(helper)  # type: ignore[index]
    release = threading.Event()
    calls: list[str] = []

    class FakeCapture:
        def execute(self, capture_plan, authorization, cancellation):
            del authorization
            incomplete = Path(str(capture_plan.output_path) + ".incomplete")
            incomplete.write_bytes(b"")
            calls.append("capture_ready")
            while not release.wait(0.001):
                if cancellation.is_set():
                    raise RuntimeError("cancelled")
            capture_plan.output_path.write_bytes(b"capture")
            capture_plan.metadata_path.write_text("{}", encoding="utf-8")
            incomplete.unlink()
            return {
                "output": artifact(capture_plan.output_path),
                "capture_metadata": artifact(capture_plan.metadata_path),
            }

    class FakeProcess:
        handle_id = "process-1"

        def wait(self, deadline, cancellation):
            del deadline, cancellation
            return SimpleNamespace(
                handle_id=self.handle_id,
                return_code=0,
                stdout="",
                stderr="",
                timed_out=False,
                cancelled=False,
                disconnected=False,
                cleanup_verified=True,
                stop_requested=False,
                running_before_stop=False,
            )

        def stop(self):
            return self.wait(0, None)

    class FakeLauncher:
        def begin_scheduled(self, arguments, *, scheduled_start_utc, minimum_arm_margin_s):
            assert arguments
            assert calls == ["capture_ready"]
            assert minimum_arm_margin_s > 0
            calls.append("process_armed")
            process = FakeProcess()
            process.arming_acknowledgement = {
                "handle_id": process.handle_id,
                "child_identity": "armed",
                "cleanup_verified": False,
                "scheduled_start_utc": scheduled_start_utc,
                "helper_observed_utc": scheduled_start_utc,
                "arm_margin_s": minimum_arm_margin_s,
            }
            return process

    providers = object.__new__(live_adapters_module.KeyedCapabilityProviders)
    providers.plan = resolved
    providers.launcher = FakeLauncher()
    providers.capture_capability = FakeCapture()
    providers.work_directory = tmp_path / "work"
    providers.work_directory.mkdir()
    providers.owned = {}
    providers.capture_metadata = {}
    providers.capture_outputs = {}
    providers.process_evidence = {}
    providers.process_outcomes = {}
    providers.analysis_evidence = {}
    providers.capture_diagnostics = {}
    providers.capture_tasks = {}
    started = time.monotonic()
    assert providers.start_process(("wsprrypi",), 1) == "process-1"
    assert time.monotonic() - started < 0.5
    assert calls == ["capture_ready", "process_armed"]
    release.set()
    assert providers.capture(resolved, 1) is not None


def test_scheduled_rebase_preserves_dynamic_fskcw_waveform_contract() -> None:
    original = {
        "protocol": {
            "pre_quiet_seconds": 2.0,
            "post_quiet_seconds": 2.0,
            "message": "CUSTOM",
            "dot_seconds": 1.25,
            "primary_frequency_hz": 14_097_123,
            "secondary_frequency_hz": 14_097_116,
        }
    }
    capture_start = datetime.now(UTC)
    derived, relative = live_adapters_module._derive_scheduled_mode_plan(
        original, capture_start, capture_start + timedelta(seconds=2.4)
    )
    assert relative == pytest.approx(2.4)
    assert derived["protocol"]["pre_quiet_seconds"] == pytest.approx(2.4)
    assert derived["protocol"]["post_quiet_seconds"] == pytest.approx(1.6)
    for field in (
        "message",
        "dot_seconds",
        "primary_frequency_hz",
        "secondary_frequency_hz",
    ):
        assert derived["protocol"][field] == original["protocol"][field]
    assert original["protocol"]["pre_quiet_seconds"] == 2.0


def test_scheduled_completion_cannot_substitute_accepted_start(tmp_path: Path) -> None:
    directory = tmp_path / "transaction-1"
    directory.mkdir()
    accepted = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    schedule_path = directory / "process-schedule.json"
    live_adapters_module.write_json_new(
        schedule_path,
        {
            "schema_version": 1,
            "evidence_type": "scheduled_process_start",
            "requested_start_utc": accepted,
            "minimum_arm_margin_s": 0.25,
            "helper_acknowledgement": {
                "handle_id": "owned-1",
                "child_identity": "armed",
                "cleanup_verified": False,
                "scheduled_start_utc": accepted,
                "helper_observed_utc": accepted,
                "arm_margin_s": 1.0,
            },
            "actual_start_utc": None,
            "schedule_error_ms": None,
            "capture_start_utc": None,
            "capture_relative_start_s": None,
        },
        schema_name="keyed-process-schedule.schema.json",
    )
    providers = object.__new__(live_adapters_module.KeyedCapabilityProviders)
    providers.work_directory = tmp_path
    providers.process_outcomes = {}
    providers.schedule_evidence = {1: schedule_path}
    result = SimpleNamespace(
        handle_id="owned-1",
        return_code=0,
        stdout="",
        stderr="",
        timed_out=False,
        cancelled=False,
        disconnected=False,
        cleanup_verified=True,
        stop_requested=False,
        running_before_stop=False,
        scheduled_start_utc=(datetime.now(UTC) + timedelta(seconds=10))
        .isoformat()
        .replace("+00:00", "Z"),
        actual_start_utc=accepted,
        schedule_error_ms=0.0,
        launch_error=None,
    )
    with pytest.raises(live_adapters_module.RealSessionError, match="changed the accepted start"):
        providers._retain_process_outcome(1, result)
    assert not (directory / "process-outcome.json").exists()


@pytest.mark.parametrize(
    "return_code,timed_out,cleanup_verified,expected",
    ((7, False, True, "failed"), (None, True, True, "aborted"), (0, False, True, "passed")),
)
def test_valid_capture_preserves_distinct_transmitter_completion_outcome(
    tmp_path: Path,
    return_code: int | None,
    timed_out: bool,
    cleanup_verified: bool,
    expected: str,
) -> None:
    directory = tmp_path / "transaction-1"
    directory.mkdir()
    output = directory / "capture.cf32"
    metadata = directory / "capture-metadata.json"
    output.write_bytes(b"capture")
    metadata.write_text("{}", encoding="utf-8")
    captured = [{"output": artifact(output), "capture_metadata": artifact(metadata)}]
    worker = threading.Thread(target=lambda: None)
    worker.start()
    worker.join()

    class FakeProcess:
        handle_id = "process-1"

        def wait(self, deadline, cancellation):
            del deadline, cancellation
            return SimpleNamespace(
                handle_id=self.handle_id,
                return_code=return_code,
                stdout="stdout",
                stderr="stderr",
                timed_out=timed_out,
                cancelled=False,
                disconnected=False,
                cleanup_verified=cleanup_verified,
                stop_requested=False,
                running_before_stop=False,
            )

    providers = object.__new__(live_adapters_module.KeyedCapabilityProviders)
    providers.work_directory = tmp_path
    providers.capture_tasks = {1: (worker, threading.Event(), captured, [])}
    providers.owned = {1: FakeProcess()}
    providers.capture_outputs = {1: output}
    providers.capture_metadata = {1: metadata}
    providers.process_outcomes = {}
    providers.validated_captures = set()
    result = providers.capture(plan(), 1)
    assert result is not None
    assert result[2] == expected
    assert output.exists()
    assert metadata.exists()


def test_valid_capture_with_process_wait_error_is_aborted_not_fixture_blocked(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "transaction-1"
    directory.mkdir()
    output = directory / "capture.cf32"
    metadata = directory / "capture-metadata.json"
    output.write_bytes(b"capture")
    metadata.write_text("{}", encoding="utf-8")
    worker = threading.Thread(target=lambda: None)
    worker.start()
    worker.join()

    class FailingWaitProcess:
        handle_id = "process-1"

        def wait(self, deadline, cancellation):
            del deadline, cancellation
            raise RuntimeError("helper transport failed after capture")

    providers = object.__new__(live_adapters_module.KeyedCapabilityProviders)
    providers.work_directory = tmp_path
    providers.capture_tasks = {
        1: (
            worker,
            threading.Event(),
            [{"output": artifact(output), "capture_metadata": artifact(metadata)}],
            [],
        )
    }
    providers.owned = {1: FailingWaitProcess()}
    providers.capture_outputs = {1: output}
    providers.capture_metadata = {1: metadata}
    providers.process_outcomes = {}
    providers.validated_captures = set()
    result = providers.capture(plan(), 1)
    assert result is not None
    assert result[2] == "aborted"
    assert 1 in providers.owned


@pytest.mark.parametrize("fail_after_ready", [False, True])
def test_keyed_capture_failure_prevents_process_launch(
    tmp_path: Path, fail_after_ready: bool
) -> None:
    from tests.unit.test_cw_contracts import _chain

    reference = tmp_path / "reference"
    reference.mkdir()
    mode_plan_path = _chain(reference, "qrss")[0]
    mode_document = json.loads(mode_plan_path.read_text(encoding="utf-8"))
    mode_document["protocol"]["repetitions"] = 1
    mode_document["protocol"]["pre_quiet_seconds"] = 0.05
    mode_document["capture_contract"]["sample_count"] = 10
    mode_plan_path.write_text(json.dumps(mode_document), encoding="utf-8")
    helper = tmp_path / "capture-helper"
    helper.write_text("helper", encoding="utf-8")
    resolved = plan()
    resolved["reference"]["plan"] = artifact(mode_plan_path)  # type: ignore[index]
    resolved["capability_bindings"]["capture_helper"] = artifact(helper)  # type: ignore[index]

    class FailingCapture:
        def execute(self, capture_plan, authorization, cancellation):
            del authorization, cancellation
            if fail_after_ready:
                Path(str(capture_plan.output_path) + ".incomplete").write_bytes(b"")
                capture_plan.output_path.write_bytes(b"untrusted partial IQ")
                capture_plan.metadata_path.write_text("{}", encoding="utf-8")
            raise RuntimeError("injected capture failure")

    armed_started: list[bool] = []
    armed_stopped: list[bool] = []

    class ForbiddenLauncher:
        def begin_scheduled(self, arguments, *, scheduled_start_utc, minimum_arm_margin_s):
            assert arguments
            armed_started.append(True)

            class ArmedProcess:
                handle_id = "armed-only"

                def __init__(self):
                    self.arming_acknowledgement = {
                        "handle_id": self.handle_id,
                        "child_identity": "armed",
                        "cleanup_verified": False,
                        "scheduled_start_utc": scheduled_start_utc,
                        "helper_observed_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "arm_margin_s": minimum_arm_margin_s,
                    }

                @staticmethod
                def stop():
                    armed_stopped.append(True)
                    return SimpleNamespace(cleanup_verified=True)

            return ArmedProcess()

    providers = object.__new__(live_adapters_module.KeyedCapabilityProviders)
    providers.plan = resolved
    providers.launcher = ForbiddenLauncher()
    providers.capture_capability = FailingCapture()
    providers.work_directory = tmp_path / "work"
    providers.work_directory.mkdir()
    providers.owned = {}
    providers.capture_metadata = {}
    providers.capture_outputs = {}
    providers.process_evidence = {}
    providers.process_outcomes = {}
    providers.analysis_evidence = {}
    providers.capture_diagnostics = {}
    providers.capture_tasks = {}
    providers.initial_services = {}
    assert providers.start_process(("wsprrypi",), 1) is None
    if armed_started:
        assert armed_stopped == [True]
    diagnostic = providers.evidence_paths(1)["capture_diagnostic"]
    failure = json.loads(diagnostic.read_text(encoding="utf-8"))
    assert failure["evidence_type"] == "capture_background_failure"
    assert failure["exception_type"] == "RuntimeError"
    assert failure["message"] == "injected capture failure"
    evidence = providers.evidence_paths(1)
    assert "capture" not in evidence
    assert "acquisition" not in evidence
    assert not providers.capture_outputs[1].exists()
    assert not Path(f"{providers.capture_outputs[1]}.incomplete").exists()
    assert not providers.cleanup(resolved, 1)
