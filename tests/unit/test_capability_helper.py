import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import wsprrypi_qualification.capability_helper as helper_module
from wsprrypi_qualification.capability_helper import (
    CapabilityHelperServer,
    CommandGpioBackend,
    HelperProtocolError,
    JsonInspectionBackend,
    OwnedProcessRegistry,
    SystemctlServiceBackend,
    decode_request,
    encode_request,
    load_server_config,
)
from wsprrypi_qualification.real_capabilities import (
    CapabilityError,
    HelperGpioProvider,
    HelperServiceProvider,
    HelperSi5351Provider,
    JsonHelperClient,
    PersistentHelperTransport,
    SshOwnedProcessLauncher,
)

PLAN = "a" * 64


def _static_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "helper_identity": "helper-1",
                "allowed_services": ["wsprrypi"],
            }
        ),
        encoding="utf-8",
    )


def test_static_helper_config_binds_runtime_plan_without_circular_identity(
    tmp_path: Path,
) -> None:
    config = tmp_path / "immutable helper config.json"
    _static_config(config)
    original = config.read_bytes()
    loaded = load_server_config(config, PLAN)
    assert loaded.plan_sha256 == PLAN
    assert config.read_bytes() == original
    assert "plan_sha256" not in json.loads(original)

    configured = json.loads(original)
    configured["plan_sha256"] = "b" * 64
    config.write_text(json.dumps(configured), encoding="utf-8")
    with pytest.raises(HelperProtocolError, match="must not contain"):
        load_server_config(config, PLAN)


def test_runtime_helper_and_configuration_substitution_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "capability-helper"
    helper.write_bytes(b"reviewed helper")
    config = tmp_path / "helper.json"
    _static_config(config)
    monkeypatch.setattr(sys, "argv", [str(helper)])
    monkeypatch.setattr(helper_module, "serve", lambda server: 0)
    helper_hash = hashlib.sha256(helper.read_bytes()).hexdigest()
    config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
    arguments = [
        "--serve",
        "--config",
        str(config),
        "--plan-sha256",
        PLAN,
        "--helper-sha256",
        helper_hash,
        "--config-sha256",
        config_hash,
    ]
    assert helper_module.main(arguments) == 0
    with pytest.raises(HelperProtocolError, match="executable SHA-256"):
        helper_module.main([*arguments[:-3], "0" * 64, *arguments[-2:]])
    config.write_text("{}", encoding="utf-8")
    with pytest.raises(HelperProtocolError, match="configuration SHA-256"):
        helper_module.main(arguments)


def test_persistent_response_timeout_uses_one_absolute_budget(tmp_path: Path):
    executable = Path(sys.executable).resolve()
    transport = PersistentHelperTransport(
        (
            str(executable),
            "-c",
            "import sys,time; sys.stdin.readline(); time.sleep(5)",
        ),
        cleanup_timeout_s=1.0,
    )
    started = time.monotonic()
    try:
        with pytest.raises(CapabilityError, match="cleanup remains owned"):
            transport.exchange("request", 0.05)
        assert time.monotonic() - started < 0.25
    finally:
        transport._process.kill()
        transport._process.wait(timeout=1)


def test_persistent_startup_failure_retains_bounded_stderr() -> None:
    executable = Path(sys.executable).resolve()
    transport = PersistentHelperTransport(
        (str(executable), "-c", "import sys;sys.stderr.write('invalid config');sys.exit(7)")
    )
    transport._process.wait(timeout=2)
    with pytest.raises(CapabilityError, match="exit 7: invalid config"):
        transport.exchange("request", 1)


class Services:
    def __init__(self):
        self.running = True

    def inspect(self, name, manager):
        return self.running

    def set_running(self, name, manager, running):
        self.running = running
        return running


class Gpio:
    def inspect(self, pin):
        return {"pin": pin, "direction": "input", "owner": None}


class Si5351:
    def inspect(self, bus, address):
        return {"bus": bus, "address": address, "enabled_outputs": [], "owner": None}


class BoundedTone:
    def run(self, request_id, frequency_hz, duration_ms, outer_timeout_s):
        return {
            "schema_version": 1,
            "evidence_type": "bounded_tone_control",
            "request_id": request_id,
            "frequency_hz": frequency_hz,
            "duration_ms": duration_ms,
            "outer_timeout_s": outer_timeout_s,
            "loopback_host": "127.0.0.1",
            "port": 31416,
            "path": "/",
            "maximum_frame_bytes": 16384,
            "start_response": {"started": True},
            "terminal_response": {"stopped": True, "scheduler_restored": True},
            "observed_responses": [
                {"started": True},
                {"stopped": True, "scheduler_restored": True},
            ],
            "cleanup_attempted": False,
            "completed": True,
            "qualification_claim": False,
            "wsprrypi_revision": "1" * 40,
        }


def request(operation, payload, **changes):
    payload = dict(payload)
    if operation in {"process-start", "process-prepare"}:
        payload.setdefault("cleanup_timeout_s", payload.get("hard_timeout_s", 1))
    value = {
        "protocol_version": 1,
        "request_id": "request-1",
        "operation": operation,
        "plan_sha256": PLAN,
        "payload": payload,
    }
    value.update(changes)
    return value


def server():
    return CapabilityHelperServer(
        "helper-1",
        PLAN,
        frozenset({"wsprrypi"}),
        services=Services(),
        gpio=Gpio(),
        si5351=Si5351(),
        bounded_tone=BoundedTone(),
    )


class InProcessLauncher:
    def __init__(self, helper):
        self.helper = helper
        self.timeouts = []

    def exchange(self, encoded, timeout_s):
        self.timeouts.append(timeout_s)
        response = self.helper.dispatch(decode_request(encoded))
        return json.dumps(response)


@pytest.mark.parametrize(
    ("operation", "payload", "field"),
    (
        ("service-inspect", {"name": "wsprrypi", "manager": "systemd"}, "running"),
        (
            "service-set",
            {"name": "wsprrypi", "manager": "systemd", "running": False},
            "running",
        ),
        ("gpio-inspect", {"pin": 4}, "direction"),
        ("si5351-inspect", {"bus": 1, "address": "0x60"}, "enabled_outputs"),
    ),
)
def test_inspection_and_service_round_trips(operation, payload, field):
    response = server().dispatch(request(operation, payload))
    assert response["request_id"] == "request-1"
    assert response["operation"] == operation
    assert field in response["result"]


def test_bounded_tone_round_trip_preserves_helper_envelope_and_nonclaim() -> None:
    response = server().dispatch(
        request(
            "bounded-tone",
            {"frequency_hz": 14_097_100, "duration_ms": 2000, "outer_timeout_s": 3.0},
        )
    )
    assert response["request_id"] == response["result"]["request_id"]
    assert response["result"]["qualification_claim"] is False
    assert response["result"]["wsprrypi_revision"] == "1" * 40


def test_request_encoding_round_trip_and_strict_envelope():
    value = request("gpio-inspect", {"pin": 4})
    assert decode_request(encode_request(value)) == value
    with pytest.raises(HelperProtocolError, match="unexpected"):
        server().dispatch({**value, "extra": True})
    with pytest.raises(HelperProtocolError, match="unknown"):
        server().dispatch({**value, "operation": "unknown"})
    with pytest.raises(HelperProtocolError, match="digest"):
        server().dispatch({**value, "plan_sha256": "b" * 64})


def test_service_allowlist_is_enforced():
    with pytest.raises(HelperProtocolError, match="allowlist"):
        server().dispatch(request("service-inspect", {"name": "other", "manager": "systemd"}))


def test_owned_process_preserves_literal_argv_and_output(tmp_path: Path):
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    marker = "literal ; $(not-a-shell) argument with spaces"
    owned = server()
    started = owned.dispatch(
        request(
            "process-start",
            {
                "arguments": [str(executable), "-c", "import sys; print(sys.argv[1])", marker],
                "executable_sha256": digest,
                "privilege_wrapper_path": None,
                "privilege_wrapper_sha256": None,
                "pinned_arguments": {},
                "hard_timeout_s": 2,
                "environment": {},
            },
        )
    )
    handle = started["result"]["handle_id"]
    result = owned.dispatch(request("process-wait", {"handle_id": handle, "timeout_s": 2}))
    assert result["result"]["stdout"].strip() == marker
    assert result["result"]["cleanup_verified"] is True


def test_scheduled_process_arms_then_launches_at_local_deadline():
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    scheduled = (datetime.now(UTC) + timedelta(seconds=0.15)).isoformat().replace("+00:00", "Z")
    helper = server()
    started = helper.dispatch(
        request(
            "process-start",
            {
                "arguments": [str(executable), "-c", "print('scheduled')"],
                "executable_sha256": digest,
                "privilege_wrapper_path": None,
                "privilege_wrapper_sha256": None,
                "pinned_arguments": {},
                "hard_timeout_s": 2,
                "environment": {},
                "scheduled_start_utc": scheduled,
                "minimum_arm_margin_s": 0.05,
            },
        )
    )["result"]
    assert started["child_identity"] == "armed"
    assert started["scheduled_start_utc"] == scheduled
    result = helper.dispatch(
        request("process-wait", {"handle_id": started["handle_id"], "timeout_s": 2})
    )["result"]
    assert result["stdout"].strip() == "scheduled"
    assert result["actual_start_utc"] is not None
    assert abs(result["schedule_error_ms"]) < 100


def test_relative_scheduled_process_selects_start_after_helper_preparation():
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    helper = server()
    before = datetime.now(UTC)
    started = helper.dispatch(
        request(
            "process-start",
            {
                "arguments": [str(executable), "-c", "print('relative')"],
                "executable_sha256": digest,
                "privilege_wrapper_path": None,
                "privilege_wrapper_sha256": None,
                "pinned_arguments": {},
                "hard_timeout_s": 2,
                "environment": {},
                "schedule_after_arm_s": 0.15,
                "minimum_arm_margin_s": 0.1,
            },
        )
    )["result"]
    observed = datetime.fromisoformat(started["helper_observed_utc"].replace("Z", "+00:00"))
    scheduled = datetime.fromisoformat(started["scheduled_start_utc"].replace("Z", "+00:00"))
    assert observed >= before
    assert (scheduled - observed).total_seconds() == pytest.approx(0.15)
    assert started["arm_margin_s"] == pytest.approx(0.15)
    result = helper.dispatch(
        request("process-wait", {"handle_id": started["handle_id"], "timeout_s": 2})
    )["result"]
    assert result["stdout"].strip() == "relative"


def test_prepared_process_cannot_launch_until_separate_arm_event():
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    helper = server()
    prepared = helper.dispatch(
        request(
            "process-prepare",
            {
                "arguments": [str(executable), "-c", "print('two-phase')"],
                "executable_sha256": digest,
                "privilege_wrapper_path": None,
                "privilege_wrapper_sha256": None,
                "pinned_arguments": {},
                "hard_timeout_s": 2,
                "environment": {},
            },
        )
    )["result"]
    assert prepared["child_identity"] == "armed"
    assert prepared["scheduled_start_utc"] is None
    handle = prepared["handle_id"]
    armed = helper.dispatch(
        request(
            "process-arm",
            {
                "handle_id": handle,
                "schedule_after_arm_s": 0.15,
                "minimum_arm_margin_s": 0.1,
            },
        )
    )["result"]
    assert armed["handle_id"] == handle
    assert armed["scheduled_start_utc"] is not None
    result = helper.dispatch(request("process-wait", {"handle_id": handle, "timeout_s": 2}))[
        "result"
    ]
    assert result["stdout"].strip() == "two-phase"


@pytest.mark.parametrize(
    "scheduled,margin,match",
    [
        (lambda: datetime.now(UTC) - timedelta(seconds=1), 0.0, "insufficient"),
        (lambda: datetime.now(UTC) + timedelta(seconds=0.01), 1.0, "insufficient"),
    ],
)
def test_scheduled_process_rejects_late_or_underarmed_start(scheduled, margin, match):
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    with pytest.raises(HelperProtocolError, match=match):
        server().dispatch(
            request(
                "process-start",
                {
                    "arguments": [str(executable), "-c", "pass"],
                    "executable_sha256": digest,
                    "privilege_wrapper_path": None,
                    "privilege_wrapper_sha256": None,
                    "pinned_arguments": {},
                    "hard_timeout_s": 2,
                    "environment": {},
                    "scheduled_start_utc": scheduled().isoformat().replace("+00:00", "Z"),
                    "minimum_arm_margin_s": margin,
                },
            )
        )


def test_scheduled_process_stop_prevents_future_launch(tmp_path: Path):
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    marker = tmp_path / "must-not-exist"
    scheduled = (datetime.now(UTC) + timedelta(seconds=0.2)).isoformat().replace("+00:00", "Z")
    helper = server()
    started = helper.dispatch(
        request(
            "process-start",
            {
                "arguments": [str(executable), "-c", f"open({str(marker)!r}, 'w').close()"],
                "executable_sha256": digest,
                "privilege_wrapper_path": None,
                "privilege_wrapper_sha256": None,
                "pinned_arguments": {},
                "hard_timeout_s": 2,
                "environment": {},
                "scheduled_start_utc": scheduled,
                "minimum_arm_margin_s": 0.05,
            },
        )
    )["result"]
    stopped = helper.dispatch(request("process-stop", {"handle_id": started["handle_id"]}))[
        "result"
    ]
    assert stopped["cancelled"] is True
    time.sleep(0.25)
    assert not marker.exists()


def test_scheduled_process_hard_deadline_and_shutdown_prevent_future_launch(
    tmp_path: Path,
):
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    for action in ("deadline", "shutdown"):
        marker = tmp_path / action
        registry = OwnedProcessRegistry()
        delay = 0.1 if action == "deadline" else 0.25
        scheduled = (
            (datetime.now(UTC) + timedelta(seconds=delay)).isoformat().replace("+00:00", "Z")
        )
        script = (
            f"import time; time.sleep(1); open({str(marker)!r}, 'w').close()"
            if action == "deadline"
            else f"open({str(marker)!r}, 'w').close()"
        )
        started = registry.start(
            {
                "arguments": [str(executable), "-c", script],
                "executable_sha256": digest,
                "privilege_wrapper_path": None,
                "privilege_wrapper_sha256": None,
                "pinned_arguments": {},
                "hard_timeout_s": 0.2 if action == "deadline" else 2,
                "environment": {},
                "scheduled_start_utc": scheduled,
                "minimum_arm_margin_s": 0.05,
            }
        )
        if action == "deadline":
            result = registry.wait({"handle_id": started["handle_id"], "timeout_s": 1})
            assert result["timed_out"] is True
        else:
            registry.shutdown()
        time.sleep(0.3)
        assert not marker.exists()


def test_scheduled_process_rejects_malformed_utc():
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    with pytest.raises(HelperProtocolError, match=r"invalid|malformed"):
        server().dispatch(
            request(
                "process-start",
                {
                    "arguments": [str(executable), "-c", "pass"],
                    "executable_sha256": digest,
                    "privilege_wrapper_path": None,
                    "privilege_wrapper_sha256": None,
                    "pinned_arguments": {},
                    "hard_timeout_s": 2,
                    "environment": {},
                    "scheduled_start_utc": "tomorrowZ",
                    "minimum_arm_margin_s": 0.05,
                },
            )
        )


def test_process_hash_mismatch_and_unknown_handle_fail_closed():
    executable = Path(sys.executable).resolve()
    with pytest.raises(HelperProtocolError, match="SHA-256"):
        server().dispatch(
            request(
                "process-start",
                {
                    "arguments": [str(executable), "-c", "pass"],
                    "executable_sha256": "0" * 64,
                    "privilege_wrapper_path": None,
                    "privilege_wrapper_sha256": None,
                    "pinned_arguments": {},
                    "hard_timeout_s": 1,
                    "environment": {},
                },
            )
        )
    with pytest.raises(HelperProtocolError, match="unknown"):
        server().dispatch(request("process-stop", {"handle_id": "missing"}))


def test_process_start_rechecks_pinned_argument_identity(tmp_path: Path):
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    config = tmp_path / "tone.ini"
    config.write_text("Transmit = False\n", encoding="utf-8")
    with pytest.raises(HelperProtocolError, match="identity changed"):
        server().dispatch(
            request(
                "process-start",
                {
                    "arguments": [str(executable), str(config)],
                    "executable_sha256": digest,
                    "privilege_wrapper_path": None,
                    "privilege_wrapper_sha256": None,
                    "pinned_arguments": {str(config.resolve()): "0" * 64},
                    "hard_timeout_s": 1,
                    "environment": {},
                },
            )
        )


def test_pinned_mutable_process_input_cannot_launch_without_repository_guard(
    tmp_path: Path,
) -> None:
    executable = Path(sys.executable).resolve()
    config = tmp_path / "tone.ini"
    config.write_text("Transmit = False\n", encoding="utf-8")
    config_digest = hashlib.sha256(config.read_bytes()).hexdigest()
    payload = {
        "arguments": [str(executable), str(config)],
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "privilege_wrapper_path": None,
        "privilege_wrapper_sha256": None,
        "pinned_arguments": {str(config.resolve()): config_digest},
        "hard_timeout_s": 1,
        "environment": {},
    }
    with pytest.raises(HelperProtocolError, match="repository mutation guard"):
        server().dispatch(request("process-start", payload))
    pinned = tmp_path / "helper executable"
    pinned.write_text("helper", encoding="utf-8")
    client = JsonHelperClient(pinned.resolve(), InProcessLauncher(server()), 1, PLAN, "helper-1")
    with pytest.raises(CapabilityError, match="repository protection"):
        SshOwnedProcessLauncher(
            client,
            1,
            hashlib.sha256(executable.read_bytes()).hexdigest(),
            pinned_arguments={str(config.resolve()): config_digest},
        )


def _repository_guard_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, object]]:
    git_name = shutil.which("git")
    assert git_name is not None
    git = Path(git_name).resolve()
    root = tmp_path / "WsprryPi source"
    root.mkdir()
    subprocess.run((str(git), "-C", str(root), "init", "-q"), check=True)
    subprocess.run(
        (str(git), "-C", str(root), "config", "user.email", "fixture@example.invalid"),
        check=True,
    )
    subprocess.run((str(git), "-C", str(root), "config", "user.name", "Fixture"), check=True)
    (root / "config").mkdir()
    source = root / "config/wsprrypi.ini"
    source.write_text("original\n", encoding="utf-8")
    subprocess.run((str(git), "-C", str(root), "add", "config/wsprrypi.ini"), check=True)
    subprocess.run((str(git), "-C", str(root), "commit", "-qm", "fixture"), check=True)
    runtime = tmp_path / "runtime/wsprrypi.ini"
    runtime.parent.mkdir()
    runtime.write_bytes(source.read_bytes())
    runtime_digest = hashlib.sha256(runtime.read_bytes()).hexdigest()
    guard: dict[str, object] = {
        "protected_source_roots": [str(root.resolve())],
        "git_path": str(git),
        "git_sha256": hashlib.sha256(git.read_bytes()).hexdigest(),
        "working_directory": str(runtime.parent.resolve()),
        "mutable_inputs": [
            {
                "source_path": str(source.resolve()),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "runtime_path": str(runtime.resolve()),
                "runtime_sha256": runtime_digest,
            }
        ],
        "writable_paths": [str(runtime.resolve())],
        "inspection_timeout_s": 2,
    }
    return root, source, runtime, guard


def test_repository_guard_contains_mutating_child_and_retains_dirty_baseline(
    tmp_path: Path,
) -> None:
    root, source, runtime, guard = _repository_guard_fixture(tmp_path)
    operator_file = root / "operator-notes.txt"
    operator_file.write_text("pre-existing dirty work", encoding="utf-8")
    executable = Path(sys.executable).resolve()
    executable_digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    code = "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('normalized')"
    helper = server()
    started = helper.dispatch(
        request(
            "process-start",
            {
                "arguments": [str(executable), "-c", code, str(runtime.resolve())],
                "executable_sha256": executable_digest,
                "privilege_wrapper_path": None,
                "privilege_wrapper_sha256": None,
                "pinned_arguments": {
                    str(runtime.resolve()): hashlib.sha256(runtime.read_bytes()).hexdigest()
                },
                "hard_timeout_s": 2,
                "environment": {},
                "repository_guard": guard,
            },
        )
    )["result"]
    result = helper.dispatch(
        request("process-wait", {"handle_id": started["handle_id"], "timeout_s": 2})
    )["result"]
    assert runtime.read_text(encoding="utf-8") == "normalized"
    assert source.read_text(encoding="utf-8") == "original\n"
    assert operator_file.read_text(encoding="utf-8") == "pre-existing dirty work"
    assert result["cleanup_verified"] is True
    assert result["repository_integrity"][0]["outcome"] == "unchanged"


def test_repository_guard_reports_child_source_mutation_without_repair(tmp_path: Path) -> None:
    _, source, runtime, guard = _repository_guard_fixture(tmp_path)
    executable = Path(sys.executable).resolve()
    code = f"import pathlib;pathlib.Path({str(source)!r}).write_text('malicious child')"
    helper = server()
    started = helper.dispatch(
        request(
            "process-start",
            {
                "arguments": [str(executable), "-c", code, str(runtime.resolve())],
                "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "privilege_wrapper_path": None,
                "privilege_wrapper_sha256": None,
                "pinned_arguments": {
                    str(runtime.resolve()): hashlib.sha256(runtime.read_bytes()).hexdigest()
                },
                "hard_timeout_s": 2,
                "environment": {},
                "repository_guard": guard,
            },
        )
    )["result"]
    result = helper.dispatch(
        request("process-wait", {"handle_id": started["handle_id"], "timeout_s": 2})
    )["result"]
    assert result["cleanup_verified"] is False
    assert result["repository_integrity"][0]["outcome"] == "integrity_failure"
    assert result["repository_integrity"][0]["repair_attempted"] is False
    assert source.read_text(encoding="utf-8") == "malicious child"


def test_repository_guard_fails_cleanup_when_post_exit_discovery_is_unavailable(
    tmp_path: Path,
) -> None:
    root, _, runtime, guard = _repository_guard_fixture(tmp_path)
    executable = Path(sys.executable).resolve()
    code = (
        "import pathlib;root=pathlib.Path(" + repr(str(root)) + ");"
        "(root/'.git').rename(root/'.git-hidden')"
    )
    helper = server()
    started = helper.dispatch(
        request(
            "process-start",
            {
                "arguments": [str(executable), "-c", code, str(runtime.resolve())],
                "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "privilege_wrapper_path": None,
                "privilege_wrapper_sha256": None,
                "pinned_arguments": {
                    str(runtime.resolve()): hashlib.sha256(runtime.read_bytes()).hexdigest()
                },
                "hard_timeout_s": 2,
                "environment": {},
                "repository_guard": guard,
            },
        )
    )["result"]
    result = helper.dispatch(
        request("process-wait", {"handle_id": started["handle_id"], "timeout_s": 2})
    )["result"]
    assert result["cleanup_verified"] is False
    assert result["repository_integrity"][0]["outcome"] == "unavailable"
    assert (root / ".git-hidden").is_dir()


@pytest.mark.parametrize("mutates", (False, True))
def test_service_restoration_repository_integrity_is_independent_and_fail_closed(
    tmp_path: Path, mutates: bool
) -> None:
    _, source, _, guard = _repository_guard_fixture(tmp_path)

    class GuardedService:
        running = False

        def inspect(self, name: str, manager: str) -> bool:
            del name, manager
            return self.running

        def set_running(self, name: str, manager: str, running: bool) -> bool:
            del name, manager
            self.running = running
            if mutates:
                source.write_text("service mutation", encoding="utf-8")
            return running

    helper = CapabilityHelperServer(
        "helper-1", PLAN, frozenset({"wsprrypi"}), services=GuardedService()
    )
    pinned = tmp_path / "helper executable"
    pinned.write_text("helper", encoding="utf-8")
    provider = HelperServiceProvider(
        JsonHelperClient(pinned.resolve(), InProcessLauncher(helper), 2, PLAN, "helper-1"),
        "systemd",
        guard,
    )
    if mutates:
        with pytest.raises(CapabilityError, match="changed protected repository"):
            provider.set_running("wsprrypi", True)
        assert source.read_text(encoding="utf-8") == "service mutation"
    else:
        provider.set_running("wsprrypi", True)
        assert provider.inspect("wsprrypi").running is True


def test_nonfinite_and_forbidden_environment_are_rejected():
    with pytest.raises(HelperProtocolError, match="finite"):
        server().dispatch(request("process-wait", {"handle_id": "x", "timeout_s": float("nan")}))
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    with pytest.raises(HelperProtocolError, match="environment"):
        server().dispatch(
            request(
                "process-start",
                {
                    "arguments": [str(executable), "-c", "pass"],
                    "executable_sha256": digest,
                    "privilege_wrapper_path": None,
                    "privilege_wrapper_sha256": None,
                    "pinned_arguments": {},
                    "hard_timeout_s": 1,
                    "environment": {"SECRET": "no"},
                },
            )
        )


def test_production_clients_round_trip_against_server(tmp_path: Path):
    helper = server()
    pinned = tmp_path / "helper executable"
    pinned.write_bytes(b"fake pinned helper")
    transport = InProcessLauncher(helper)
    client = JsonHelperClient(pinned.resolve(), transport, 2, PLAN, "helper-1")
    assert HelperServiceProvider(client, "systemd").inspect("wsprrypi").running is True
    assert HelperGpioProvider(client).inspect(4).direction == "input"
    assert HelperSi5351Provider(client).inspect(1, "0x60").enabled_outputs == ()

    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    process = SshOwnedProcessLauncher(client, 2, digest).begin(
        (str(executable), "-c", "print('owned remote output')")
    )
    # The production launcher supplies the hash through its resolved process request.
    # Direct helper process requests cover hash authentication; this assertion ensures
    # the client/server ownership protocol itself remains wired.
    result = process.wait(2, None)
    assert result.stdout.strip() == "owned remote output"
    assert result.cleanup_verified is True
    prepared = SshOwnedProcessLauncher(client, 2, digest).prepare(
        (str(executable), "-c", "print('prepared then armed')")
    )
    assert prepared.arming_acknowledgement["scheduled_start_utc"] is None
    prepared.arm(0.15, 0.1)
    assert prepared.arming_acknowledgement["scheduled_start_utc"] is not None
    armed_result = prepared.wait(2, None)
    assert armed_result.stdout.strip() == "prepared then armed"
    assert armed_result.cleanup_verified is True


def test_process_start_response_contains_repository_inspection_envelope(tmp_path: Path):
    helper = server()
    pinned = tmp_path / "helper executable"
    pinned.write_bytes(b"fake pinned helper")
    transport = InProcessLauncher(helper)
    client = JsonHelperClient(pinned.resolve(), transport, 5, PLAN, "helper-1")
    executable = Path(sys.executable).resolve()
    with pytest.raises(HelperProtocolError, match="repository"):
        SshOwnedProcessLauncher(
            client,
            20,
            hashlib.sha256(executable.read_bytes()).hexdigest(),
            repository_guard={
                "protected_source_roots": [str(tmp_path)],
                "git_path": str(executable),
                "git_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "working_directory": str(tmp_path),
                "mutable_inputs": [],
                "writable_paths": [],
                "inspection_timeout_s": 20,
            },
            cleanup_timeout_s=10,
        ).begin((str(executable), "-c", "pass"))
    assert transport.timeouts[-1] == 25


@pytest.mark.parametrize("invalid", [False, True, 0, -1, float("inf"), float("nan")])
def test_helper_response_envelopes_reject_invalid_numeric_values(tmp_path: Path, invalid: float):
    pinned = tmp_path / "helper executable"
    pinned.write_bytes(b"fake pinned helper")
    transport = InProcessLauncher(server())
    client = JsonHelperClient(pinned.resolve(), transport, 5, PLAN, "helper-1")
    with pytest.raises(CapabilityError, match="response deadline"):
        client.request("service-inspect", {"name": "wsprrypi"}, response_timeout_s=invalid)
    with pytest.raises(CapabilityError, match="cleanup deadline"):
        SshOwnedProcessLauncher(client, 5, "0" * 64, cleanup_timeout_s=invalid)


def test_persistent_entrypoint_preserves_ownership_and_enforces_deadline(tmp_path: Path):
    config = tmp_path / "helper config.json"
    config.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "helper_identity": "installed-helper",
                "plan_sha256": PLAN,
                "allowed_services": [],
            }
        ),
        encoding="utf-8",
    )
    # Preserve the virtual-environment entry point. Resolving a POSIX symlink
    # can escape the venv, while sys.prefix/Scripts is not portable to every
    # Windows Python environment.
    module_python = Path(sys.executable).absolute()
    transport = PersistentHelperTransport(
        (
            str(module_python.absolute()),
            "-m",
            "wsprrypi_qualification.capability_helper",
            "--serve",
            "--config",
            str(config),
        )
    )
    client = JsonHelperClient(
        Path(sys.executable).resolve(), transport, 5, PLAN, "installed-helper"
    )
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    try:
        process = SshOwnedProcessLauncher(client, 5, digest).begin(
            (str(executable), "-c", "print('persistent')")
        )
        assert process.wait(2, None).stdout.strip() == "persistent"
        timed = SshOwnedProcessLauncher(client, 0.05, digest).begin(
            (str(executable), "-c", "import time; time.sleep(5)")
        )
        time.sleep(0.1)
        result = timed.wait(1, None)
        assert result.timed_out is True
        assert result.cleanup_verified is True
        stopped = SshOwnedProcessLauncher(client, 5, digest).begin(
            (str(executable), "-c", "import time; time.sleep(5)")
        )
        stop_result = stopped.stop()
        assert stop_result.cancelled is True
        assert stop_result.cleanup_verified is True
    finally:
        transport.close()


def test_production_provider_hashes_are_pinned_and_rechecked(tmp_path: Path):
    provider = tmp_path / "provider with spaces"
    provider.write_bytes(b"provider-v1")
    digest = hashlib.sha256(provider.read_bytes()).hexdigest()
    SystemctlServiceBackend(provider.resolve(), digest, frozenset({"svc"}), 1)
    backend = JsonInspectionBackend(provider.resolve(), digest, "gpio-inspect", 1)
    CommandGpioBackend(backend)
    provider.write_bytes(b"provider-v2")
    with pytest.raises(HelperProtocolError, match="identity changed"):
        backend.request({"pin": 4})
    with pytest.raises(HelperProtocolError, match="hash"):
        JsonInspectionBackend(provider.resolve(), digest, "gpio-inspect", 1)


def test_owned_process_uses_only_authenticated_noninteractive_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "wsprrypi"
    wrapper = tmp_path / "sudo"
    executable.write_bytes(b"wsprrypi")
    wrapper.write_bytes(b"sudo")
    executable_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    wrapper_hash = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    calls: list[list[str]] = []

    class Process:
        pid = 1234
        returncode = 0

        @staticmethod
        def poll():
            return 0

    def fake_popen(arguments, **kwargs):
        del kwargs
        calls.append(arguments)
        return Process()

    monkeypatch.setattr(helper_module.subprocess, "Popen", fake_popen)
    registry = OwnedProcessRegistry(wrapper.resolve(), wrapper_hash)
    result = registry.start(
        {
            "arguments": [str(executable.resolve()), "--mode", "QRSS"],
            "executable_sha256": executable_hash,
            "privilege_wrapper_path": str(wrapper.resolve()),
            "privilege_wrapper_sha256": wrapper_hash,
            "pinned_arguments": {},
            "hard_timeout_s": 1,
            "environment": {},
        }
    )
    assert result["handle_id"].startswith("owned-")
    assert calls == [
        [str(wrapper.resolve()), "-n", "--", str(executable.resolve()), "--mode", "QRSS"]
    ]
    registry.shutdown()

    with pytest.raises(HelperProtocolError, match="does not match resolved plan"):
        OwnedProcessRegistry(wrapper.resolve(), wrapper_hash).start(
            {
                "arguments": [str(executable.resolve())],
                "executable_sha256": executable_hash,
                "privilege_wrapper_path": str(wrapper.resolve()),
                "privilege_wrapper_sha256": "0" * 64,
                "pinned_arguments": {},
                "hard_timeout_s": 1,
                "environment": {},
            }
        )
    copied_wrapper = tmp_path / "copied-sudo"
    copied_wrapper.write_bytes(wrapper.read_bytes())
    with pytest.raises(HelperProtocolError, match="does not match resolved plan"):
        registry.start(
            {
                "arguments": [str(executable.resolve())],
                "executable_sha256": executable_hash,
                "privilege_wrapper_path": str(copied_wrapper.resolve()),
                "privilege_wrapper_sha256": wrapper_hash,
                "pinned_arguments": {},
                "hard_timeout_s": 1,
                "environment": {},
            }
        )
    wrapper.write_bytes(b"substituted")
    with pytest.raises(HelperProtocolError, match="identity changed"):
        registry.start(
            {
                "arguments": [str(executable.resolve())],
                "executable_sha256": executable_hash,
                "privilege_wrapper_path": str(wrapper.resolve()),
                "privilege_wrapper_sha256": wrapper_hash,
                "pinned_arguments": {},
                "hard_timeout_s": 1,
                "environment": {},
            }
        )


def test_service_backend_authenticates_privilege_wrapper_and_uses_noninteractive_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    systemctl = tmp_path / "systemctl"
    wrapper = tmp_path / "sudo"
    systemctl.write_bytes(b"systemctl")
    wrapper.write_bytes(b"sudo")
    calls: list[list[str]] = []

    def fake_run(arguments, **kwargs):
        del kwargs
        calls.append(arguments)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(helper_module.subprocess, "run", fake_run)
    backend = SystemctlServiceBackend(
        systemctl.resolve(),
        hashlib.sha256(systemctl.read_bytes()).hexdigest(),
        frozenset({"sdrplay.service"}),
        1,
        wrapper.resolve(),
        hashlib.sha256(wrapper.read_bytes()).hexdigest(),
    )
    assert backend.set_running("sdrplay.service", "systemd", True)
    prefix = [str(wrapper.resolve()), "-n", "--", str(systemctl.resolve())]
    assert calls == [
        [*prefix, "start", "--", "sdrplay.service"],
        [str(systemctl.resolve()), "is-active", "--quiet", "--", "sdrplay.service"],
    ]
    wrapper.write_bytes(b"substituted")
    with pytest.raises(HelperProtocolError, match="wrapper identity changed"):
        backend.inspect("sdrplay.service", "systemd")


def test_helper_config_rejects_incomplete_service_privilege_wrapper(
    tmp_path: Path,
) -> None:
    config = tmp_path / "helper.json"
    config.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "helper_identity": "helper",
                "allowed_services": ["service"],
                "systemctl_path": "/usr/bin/systemctl",
                "systemctl_sha256": "a" * 64,
                "service_privilege_wrapper_path": "/usr/bin/sudo",
                "plan_sha256": PLAN,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="violates schema"):
        load_server_config(config)


def test_helper_config_rejects_incomplete_process_privilege_wrapper(tmp_path: Path) -> None:
    config = tmp_path / "helper.json"
    config.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "helper_identity": "helper",
                "allowed_services": [],
                "process_privilege_wrapper_path": "/usr/bin/sudo",
                "plan_sha256": PLAN,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="violates schema"):
        load_server_config(config)
