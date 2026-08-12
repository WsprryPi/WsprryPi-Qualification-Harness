import json
import socket
import subprocess
import sys
import threading
import time
from dataclasses import fields
from importlib.resources import files
from pathlib import Path, PureWindowsPath

import pytest
from jsonschema import Draft202012Validator

from wsprrypi_qualification.adapters import (
    MockOperation,
    MockQuiescenceAdapter,
    MockReceiverAdapter,
    MockServiceAdapter,
    MockTransmitterAdapter,
    OperationOutcome,
    OperationResult,
)
from wsprrypi_qualification.supervisor import (
    OperationDeadlines,
    ResolvedPlan,
    Supervisor,
    validate_supervisor_document,
)
from wsprrypi_qualification.transports import (
    CommandPlan,
    DeterministicFakeSshExecutor,
    FakeSshResponse,
    LocalCommandTransport,
    LocalProcessOperation,
    SshCommandTransport,
    SshPlan,
    TransportError,
    sanitized_environment,
    validate_execution_record,
    validate_ssh_execution_record,
)


def test_local_transport_preserves_spaces_and_environment(tmp_path: Path) -> None:
    directory = tmp_path / "working directory with spaces"
    directory.mkdir()
    argument = "argument with spaces"
    record = LocalCommandTransport().execute(
        CommandPlan(
            Path(sys.executable),
            ("-c", "import os,sys; print(sys.argv[1]); print(os.getcwd())", argument),
            directory,
            5,
            {"WSPQ_TEST": "yes"},
        )
    )
    assert record.return_code == 0 and argument in record.stdout and str(directory) in record.stdout
    assert record.arguments[-1] == argument and "WSPQ_TEST" in record.environment_keys
    assert "HOME" not in sanitized_environment()
    assert str(PureWindowsPath(r"C:\Program Files\WSPR Harness\tool.exe"))
    schema = json.loads(
        files("wsprrypi_qualification.schemas")
        .joinpath("transport-execution.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(record.to_dict())


def test_local_transport_nonzero_timeout_and_cancellation() -> None:
    transport = LocalCommandTransport()
    failure = transport.execute(
        CommandPlan(
            Path(sys.executable),
            ("-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"),
            timeout_s=5,
        )
    )
    assert (
        failure.return_code == 7
        and failure.stdout.strip() == "out"
        and failure.stderr.strip() == "err"
    )
    timeout = transport.execute(
        CommandPlan(Path(sys.executable), ("-c", "import time; time.sleep(2)"), timeout_s=0.05)
    )
    assert timeout.timed_out and timeout.cleanup_verified
    cancelled = threading.Event()
    cancelled.set()
    result = transport.execute(
        CommandPlan(Path(sys.executable), ("-c", "import time; time.sleep(2)"), timeout_s=5),
        cancellation=cancelled,
    )
    assert result.cancelled and result.cleanup_verified


def test_local_process_handle_integrates_with_supervisor() -> None:
    operation = LocalProcessOperation(
        CommandPlan(Path(sys.executable), ("-c", "print('complete')"), timeout_s=5),
        "local-child",
        "monitor",
    )
    result = Supervisor(MockReceiverAdapter("r"), MockTransmitterAdapter("t")).run(
        execution=operation
    )
    assert result.final_status == "inconclusive"
    record = next(item for item in result.ownership if item.handle_id == operation.handle_id)
    assert record.transport == "local" and record.return_code == 0 and not record.final_alive


def test_local_process_timeout_finalizes_and_retains_output() -> None:
    operation = LocalProcessOperation(
        CommandPlan(
            Path(sys.executable),
            (
                "-c",
                "import sys,time; print('OUT', flush=True); "
                "print('ERR', file=sys.stderr, flush=True); time.sleep(10)",
            ),
            timeout_s=5,
        ),
        "local-child",
        "monitor",
    )
    result = Supervisor(MockReceiverAdapter("r"), MockTransmitterAdapter("t")).run(
        # Native Windows process startup can exceed tens of milliseconds.
        # Allow startup and flushed output, while remaining far below the
        # child's sleep so this still exercises forced timeout finalization.
        ResolvedPlan("timeout-output", OperationDeadlines(monitor_s=2.0)),
        execution=operation,
    )
    record = next(item for item in result.ownership if item.handle_id == operation.handle_id)
    assert result.final_status == "fixture_blocked"
    assert record.outcome == "timed_out" and not record.final_alive
    assert "OUT" in record.stdout and "ERR" in record.stderr


def test_ssh_refuses_real_connections() -> None:
    with pytest.raises(TypeError):
        SshPlan("example.invalid", ("true",), executable=Path("/usr/bin/ssh"))  # type: ignore[call-arg]
    assert SshCommandTransport.validate_remote_arguments(("path with spaces",)) == (
        "path with spaces",
    )
    with pytest.raises(TransportError):
        SshCommandTransport.validate_remote_arguments(("bad\nargument",))


def test_fake_ssh_round_trip_disconnect_timeout_and_cancellation() -> None:
    plan = SshPlan("fake-host", ("tool", "path with spaces", "µ"), 5)
    transport = SshCommandTransport(DeterministicFakeSshExecutor())
    record = transport.execute(plan)
    assert (
        SshCommandTransport.decode_remote_command(record.encoded_remote_command)
        == plan.remote_arguments
    )
    assert record.destination_host == "fake-host" and record.version_return_code == 0
    ssh_schema = json.loads(
        files("wsprrypi_qualification.schemas")
        .joinpath("ssh-execution.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator(ssh_schema).validate(record.to_dict())
    disconnect = SshCommandTransport(
        DeterministicFakeSshExecutor(FakeSshResponse(return_code=255, disconnected=True))
    ).execute(SshPlan("fake", ("x",)))
    assert disconnect.disconnected
    timeout = SshCommandTransport(
        DeterministicFakeSshExecutor(FakeSshResponse(return_code=None, timed_out=True))
    ).execute(SshPlan("fake", ("x",), 0.05))
    assert timeout.timed_out and timeout.cleanup_verified
    event = threading.Event()
    event.set()
    assert transport.execute(plan, cancellation=event).cancelled


def test_fake_ssh_has_no_process_or_network_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    launched = False

    def fail_launch(*args, **kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("launch attempted")

    monkeypatch.setattr(subprocess, "Popen", fail_launch)
    monkeypatch.setattr(socket, "socket", fail_launch)
    SshCommandTransport(DeterministicFakeSshExecutor()).execute(
        SshPlan("example.invalid", ("true",))
    )
    assert not launched


def test_transport_semantics_reject_contradictions() -> None:
    local = (
        LocalCommandTransport()
        .execute(CommandPlan(Path(sys.executable), ("-c", "pass"), timeout_s=5))
        .to_dict()
    )
    local["disconnected"] = True
    with pytest.raises(TransportError, match="local transport"):
        validate_execution_record(local)
    local = (
        LocalCommandTransport()
        .execute(CommandPlan(Path(sys.executable), ("-c", "pass"), timeout_s=5))
        .to_dict()
    )
    local["timed_out"] = True
    with pytest.raises(TransportError, match="timeout"):
        validate_execution_record(local)
    local["timed_out"] = False
    local["completed_utc"] = "2000-01-01T00:00:00Z"
    with pytest.raises(TransportError, match="timestamps"):
        validate_execution_record(local)
    local = (
        LocalCommandTransport()
        .execute(CommandPlan(Path(sys.executable), ("-c", "pass"), timeout_s=5))
        .to_dict()
    )
    local["cleanup_verified"] = False
    with pytest.raises(TransportError, match="verified cleanup"):
        validate_execution_record(local)
    plan = SshPlan("fake", ("x",), 5)
    ssh = SshCommandTransport(DeterministicFakeSshExecutor()).execute(plan).to_dict()
    ssh["disconnected"] = True
    with pytest.raises(TransportError, match="disconnect"):
        validate_ssh_execution_record(ssh)
    ssh = SshCommandTransport(DeterministicFakeSshExecutor()).execute(plan).to_dict()
    ssh["cleanup_verified"] = False
    with pytest.raises(TransportError, match="verified cleanup"):
        validate_ssh_execution_record(ssh)
    ssh = SshCommandTransport(DeterministicFakeSshExecutor()).execute(plan).to_dict()
    ssh["version_return_code"] = None
    with pytest.raises(TransportError, match="version evidence"):
        validate_ssh_execution_record(ssh)
    ssh = SshCommandTransport(DeterministicFakeSshExecutor()).execute(plan).to_dict()
    ssh.update(return_code=None, cleanup_verified=False)
    with pytest.raises(TransportError, match="terminal outcome"):
        validate_ssh_execution_record(ssh)


def test_descendant_inherited_output_cannot_extend_transport_deadline() -> None:
    code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(.2)']); "
        "time.sleep(2)"
    )
    started = time.monotonic()
    result = LocalCommandTransport().execute(
        CommandPlan(Path(sys.executable), ("-c", code), timeout_s=0.05)
    )
    assert result.timed_out and time.monotonic() - started < 0.8


@pytest.mark.parametrize("failure", ["acquire", "start", "stop", "release"])
def test_supervisor_failure_cleanup_and_precedence(failure: str) -> None:
    transmitter = MockTransmitterAdapter("transmitter", {failure})
    receiver = MockReceiverAdapter("receiver")
    result = Supervisor(receiver, transmitter).run()
    assert result.final_status == (
        "cleanup_failed" if failure in {"stop", "release"} else "fixture_blocked"
    )
    assert receiver.released
    assert [event.sequence for event in result.events] == list(range(1, len(result.events) + 1))


def test_cancel_disconnect_service_quiescence_and_idempotence() -> None:
    service = MockServiceAdapter(True)
    service.set_running(False)
    supervisor = Supervisor(
        MockReceiverAdapter("receiver"),
        MockTransmitterAdapter("transmitter"),
        service=service,
        quiescence=MockQuiescenceAdapter("si5351"),
    )
    cancellation = threading.Event()
    cancellation.set()
    result = supervisor.run(cancellation=cancellation)
    assert result.final_status == "aborted" and service.running
    with pytest.raises(Exception, match="single-use"):
        supervisor.run()
    disconnected = Supervisor(MockReceiverAdapter("r"), MockTransmitterAdapter("t")).run(
        execution=MockOperation(
            "ssh-1", "ssh", "monitor", OperationResult(OperationOutcome.DISCONNECTED, 255)
        )
    )
    assert disconnected.final_status == "fixture_blocked"


def test_supervisor_timeout_child_failure_and_quiescence_failure() -> None:
    timeout = Supervisor(MockReceiverAdapter("r"), MockTransmitterAdapter("t")).run(
        ResolvedPlan("timeout", OperationDeadlines(monitor_s=0.01)),
        execution=MockOperation("blocked", "child", "monitor", polls_before_result=1_000_000),
    )
    assert timeout.final_status == "fixture_blocked"
    child = Supervisor(MockReceiverAdapter("r"), MockTransmitterAdapter("t")).run(
        execution=MockOperation(
            "child", "child", "monitor", OperationResult(OperationOutcome.NONZERO_EXIT, 7)
        )
    )
    assert child.final_status == "fixture_blocked"
    backend = Supervisor(
        MockReceiverAdapter("r"),
        MockTransmitterAdapter("t"),
        quiescence=MockQuiescenceAdapter("gpio", False),
    ).run()
    assert backend.final_status == "cleanup_failed"


def test_partial_ownership_never_cleans_unowned_resources() -> None:
    receiver = MockReceiverAdapter("receiver", {"acquire"})
    transmitter = MockTransmitterAdapter("transmitter")
    result = Supervisor(receiver, transmitter).run()
    assert [(item.component, item.action) for item in result.cleanup_actions] == [
        ("helpers", "leak_verify")
    ]
    assert transmitter.calls == []
    receiver = MockReceiverAdapter("receiver")
    transmitter = MockTransmitterAdapter("transmitter", {"acquire"})
    result = Supervisor(receiver, transmitter).run()
    assert [(item.component, item.action) for item in result.cleanup_actions] == [
        ("receiver", "release"),
        ("helpers", "leak_verify"),
    ]
    assert "stop" not in receiver.calls and "release" not in transmitter.calls


def test_begin_exception_becomes_typed_launch_evidence() -> None:
    result = Supervisor(
        MockReceiverAdapter("receiver", raise_at={"acquire"}),
        MockTransmitterAdapter("transmitter"),
    ).run()
    record = next(item for item in result.ownership if item.outcome == "launch_failed")
    assert not record.owned_by_harness and record.operation == "acquire"
    assert result.final_status == "fixture_blocked"


def test_unchanged_service_is_not_restored() -> None:
    service = MockServiceAdapter(True)
    result = Supervisor(
        MockReceiverAdapter("receiver"), MockTransmitterAdapter("transmitter"), service=service
    ).run()
    assert not any(item.component == "service" for item in result.cleanup_actions)


@pytest.mark.parametrize(
    ("component", "action"),
    [
        ("receiver", "acquire"),
        ("transmitter", "acquire"),
        ("receiver", "start"),
        ("transmitter", "start"),
    ],
)
def test_each_setup_deadline_is_bounded(component: str, action: str) -> None:
    receiver = MockReceiverAdapter(
        "receiver", block_at={action} if component == "receiver" else set()
    )
    transmitter = MockTransmitterAdapter(
        "transmitter", block_at={action} if component == "transmitter" else set()
    )
    deadlines = OperationDeadlines(
        receiver_acquire_s=0.01,
        transmitter_acquire_s=0.01,
        receiver_start_s=0.01,
        transmitter_start_s=0.01,
    )
    result = Supervisor(receiver, transmitter).run(ResolvedPlan("bounded", deadlines))
    assert result.final_status == "fixture_blocked"
    assert any(item.outcome == "timed_out" for item in result.ownership)


@pytest.mark.parametrize(
    ("component", "action"),
    [
        ("receiver", "stop"),
        ("transmitter", "stop"),
        ("receiver", "release"),
        ("transmitter", "release"),
    ],
)
def test_each_cleanup_deadline_has_precedence(component: str, action: str) -> None:
    receiver = MockReceiverAdapter(
        "receiver", block_at={action} if component == "receiver" else set()
    )
    transmitter = MockTransmitterAdapter(
        "transmitter", block_at={action} if component == "transmitter" else set()
    )
    deadlines = OperationDeadlines(
        receiver_stop_s=0.01,
        transmitter_stop_s=0.01,
        receiver_release_s=0.01,
        transmitter_release_s=0.01,
    )
    result = Supervisor(receiver, transmitter).run(ResolvedPlan("cleanup-timeout", deadlines))
    assert result.final_status == "cleanup_failed"
    assert result.cleanup_actions[-1].action in {"leak_verify", "quiescence"}


def test_service_and_quiescence_deadlines_are_bounded() -> None:
    service = MockServiceAdapter(True, block_restore=True)
    service.set_running(False)
    service_result = Supervisor(
        MockReceiverAdapter("r"), MockTransmitterAdapter("t"), service=service
    ).run(ResolvedPlan("service", OperationDeadlines(service_restore_s=0.01)))
    assert service_result.final_status == "cleanup_failed"
    gpio_result = Supervisor(
        MockReceiverAdapter("r"),
        MockTransmitterAdapter("t"),
        quiescence=MockQuiescenceAdapter("gpio", blocked=True),
    ).run(ResolvedPlan("gpio", OperationDeadlines(quiescence_s=0.01)))
    assert gpio_result.final_status == "cleanup_failed"
    leak_result = Supervisor(
        MockReceiverAdapter("r"),
        MockTransmitterAdapter("t"),
        leak_operation=MockOperation(
            "blocked-leak", "helpers", "leak_verify", polls_before_result=1_000_000
        ),
    ).run(ResolvedPlan("leak", OperationDeadlines(leak_verify_s=0.01)))
    assert leak_result.final_status == "cleanup_failed"


def test_overall_deadline_bounds_execution_and_cleanup() -> None:
    result = Supervisor(MockReceiverAdapter("r"), MockTransmitterAdapter("t")).run(
        ResolvedPlan("overall", OperationDeadlines(monitor_s=1, overall_s=0.01)),
        execution=MockOperation(
            "blocked-overall", "execution", "monitor", polls_before_result=1_000_000
        ),
    )
    assert result.final_status == "cleanup_failed"
    assert any(item.outcome == "timed_out" for item in result.ownership)


class _BlockingUnreviewedOperation:
    handle_id = "unreviewed"
    component = "bad"
    action = "monitor"
    transport = "bad"

    def poll(self):
        time.sleep(2)

    def request_stop(self):
        time.sleep(2)

    def force_stop(self):
        time.sleep(2)

    def is_alive(self):
        time.sleep(2)
        return True

    def collect_result(self):
        return None


def test_unreviewed_blocking_operation_is_refused_before_poll() -> None:
    started = time.monotonic()
    result = Supervisor(MockReceiverAdapter("r"), MockTransmitterAdapter("t")).run(
        execution=_BlockingUnreviewedOperation()
    )
    assert result.final_status == "fixture_blocked"
    assert time.monotonic() - started < 0.5


def test_mock_subclass_and_method_shadowing_are_structurally_refused() -> None:
    class EvilMock(MockOperation):
        def poll(self):
            raise AssertionError("must not run")

    result = Supervisor(MockReceiverAdapter("r"), MockTransmitterAdapter("t")).run(
        execution=EvilMock("evil", "evil", "monitor")
    )
    assert result.final_status == "fixture_blocked"
    receiver = MockReceiverAdapter("r")
    with pytest.raises((AttributeError, TypeError)):
        receiver.begin = lambda action: None  # type: ignore[method-assign]


def test_reviewed_mocks_expose_no_callable_injection() -> None:
    names = {item.name for item in fields(MockOperation)} | {
        item.name for item in fields(MockReceiverAdapter)
    }
    assert not {"callback", "poll_callback", "on_success", "callable"} & names


@pytest.mark.parametrize(
    ("target", "action"),
    [
        ("receiver", "stop"),
        ("transmitter", "stop"),
        ("receiver", "release"),
        ("transmitter", "release"),
    ],
)
def test_cancellation_during_adapter_cleanup_is_recorded(target: str, action: str) -> None:
    event = threading.Event()
    receiver = MockReceiverAdapter(
        "receiver", cancellation_event=event, cancel_at={action} if target == "receiver" else set()
    )
    transmitter = MockTransmitterAdapter(
        "transmitter",
        cancellation_event=event,
        cancel_at={action} if target == "transmitter" else set(),
    )
    result = Supervisor(receiver, transmitter).run(cancellation=event)
    assert result.final_status == "aborted"
    assert result.cleanup_actions[-1].action == "leak_verify"


def test_cancellation_during_service_leak_and_quiescence_is_recorded() -> None:
    event = threading.Event()
    service = MockServiceAdapter(True, cancellation_event=event, cancel_on_restore=True)
    service.set_running(False)
    service_result = Supervisor(
        MockReceiverAdapter("r"), MockTransmitterAdapter("t"), service=service
    ).run(cancellation=event)
    assert service_result.final_status == "aborted"
    event = threading.Event()
    leak = MockOperation("leak", "helpers", "leak_verify", cancel_event=event, cancel_at_poll=1)
    leak_result = Supervisor(
        MockReceiverAdapter("r"), MockTransmitterAdapter("t"), leak_operation=leak
    ).run(cancellation=event)
    assert leak_result.final_status == "aborted"
    event = threading.Event()
    quiescence = MockQuiescenceAdapter("gpio", cancellation_event=event, cancel_on_inspection=True)
    result = Supervisor(
        MockReceiverAdapter("r"), MockTransmitterAdapter("t"), quiescence=quiescence
    ).run(cancellation=event)
    assert result.final_status == "aborted"


def test_leak_cleanup_action_uses_the_executed_operation_handle() -> None:
    leak = MockOperation("custom-leak-id", "helpers", "leak_verify")
    result = Supervisor(
        MockReceiverAdapter("r"), MockTransmitterAdapter("t"), leak_operation=leak
    ).run()
    action = next(item for item in result.cleanup_actions if item.action == "leak_verify")
    record = next(item for item in result.ownership if item.operation == "leak_verify")
    assert action.handle_id == record.handle_id == "custom-leak-id"
    validate_supervisor_document(result.to_document())


@pytest.mark.parametrize(
    ("component", "action"),
    [
        ("receiver", "acquire"),
        ("transmitter", "acquire"),
        ("receiver", "start"),
        ("transmitter", "start"),
    ],
)
def test_cancellation_is_observed_during_each_setup_phase(component: str, action: str) -> None:
    event = threading.Event()
    receiver = (
        MockReceiverAdapter(
            "receiver", block_at={action}, cancellation_event=event, cancel_at={action}
        )
        if component == "receiver"
        else MockReceiverAdapter("receiver")
    )
    transmitter = (
        MockTransmitterAdapter(
            "transmitter", block_at={action}, cancellation_event=event, cancel_at={action}
        )
        if component == "transmitter"
        else MockTransmitterAdapter("transmitter")
    )
    result = Supervisor(receiver, transmitter).run(cancellation=event)
    assert result.final_status == "aborted"


def test_supervisor_evidence_schema() -> None:
    document = Supervisor(MockReceiverAdapter("r"), MockTransmitterAdapter("t")).run().to_document()
    schema = json.loads(
        files("wsprrypi_qualification.schemas")
        .joinpath("slice4-supervisor.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(document)
    validate_supervisor_document(document)
    assert document["final_status"] == "inconclusive"
    document["leak_verification"]["verified"] = False
    with pytest.raises(ValueError, match="contradicts"):
        validate_supervisor_document(document)


def test_semantic_validator_rejects_quiescence_time_and_status_contradictions() -> None:
    document = (
        Supervisor(
            MockReceiverAdapter("r"),
            MockTransmitterAdapter("t"),
            quiescence=MockQuiescenceAdapter("gpio"),
        )
        .run()
        .to_document()
    )
    document["quiescence"]["verified"] = False
    with pytest.raises(ValueError, match="quiescence"):
        validate_supervisor_document(document)
    document = Supervisor(MockReceiverAdapter("r"), MockTransmitterAdapter("t")).run().to_document()
    document["events"][1]["timestamp_utc"] = "2000-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="timestamps"):
        validate_supervisor_document(document)
    document = Supervisor(MockReceiverAdapter("r"), MockTransmitterAdapter("t")).run().to_document()
    document["final_status"] = "fixture_blocked"
    with pytest.raises(ValueError, match="final status"):
        validate_supervisor_document(document)


def test_semantic_validator_rejects_reordered_cleanup() -> None:
    document = (
        Supervisor(MockReceiverAdapter("receiver"), MockTransmitterAdapter("transmitter"))
        .run()
        .to_document()
    )
    document["cleanup_actions"] = list(reversed(document["cleanup_actions"]))
    for sequence, action in enumerate(document["cleanup_actions"], 1):
        action["sequence"] = sequence
    with pytest.raises(ValueError, match="cleanup action"):
        validate_supervisor_document(document)


def test_semantic_validator_rejects_ownership_and_cleanup_time_contradictions() -> None:
    document = (
        Supervisor(MockReceiverAdapter("receiver"), MockTransmitterAdapter("transmitter"))
        .run()
        .to_document()
    )
    document["ownership"][0]["started_utc"] = "2030-01-01T00:00:00Z"
    document["ownership"][0]["stopped_utc"] = "2000-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="ownership timestamps"):
        validate_supervisor_document(document)
    document = (
        Supervisor(MockReceiverAdapter("receiver"), MockTransmitterAdapter("transmitter"))
        .run()
        .to_document()
    )
    document["cleanup_actions"][0]["timestamp_utc"] = "2000-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="cleanup action timestamps"):
        validate_supervisor_document(document)
    document = (
        Supervisor(MockReceiverAdapter("receiver"), MockTransmitterAdapter("transmitter"))
        .run()
        .to_document()
    )
    document["ownership"][1]["handle_id"] = document["ownership"][0]["handle_id"]
    with pytest.raises(ValueError, match="not unique"):
        validate_supervisor_document(document)


def test_shell_is_never_used(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        seen.update(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    LocalCommandTransport().execute(CommandPlan(Path(sys.executable), ("-c", "pass"), timeout_s=5))
    assert seen["shell"] is False
