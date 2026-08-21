import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

import wsprrypi_qualification.capability_helper as helper_module
from wsprrypi_qualification.capability_helper import (
    CapabilityHelperServer,
    CommandGpioBackend,
    HelperProtocolError,
    JsonInspectionBackend,
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

    def exchange(self, encoded, timeout_s):
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


def test_process_hash_mismatch_and_unknown_handle_fail_closed():
    executable = Path(sys.executable).resolve()
    with pytest.raises(HelperProtocolError, match="SHA-256"):
        server().dispatch(
            request(
                "process-start",
                {
                    "arguments": [str(executable), "-c", "pass"],
                    "executable_sha256": "0" * 64,
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
                    "pinned_arguments": {str(config.resolve()): "0" * 64},
                    "hard_timeout_s": 1,
                    "environment": {},
                },
            )
        )


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
    client = JsonHelperClient(pinned.resolve(), InProcessLauncher(helper), 2, PLAN, "helper-1")
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
