import base64
import subprocess
from pathlib import Path

import pytest

from wsprrypi_qualification import automatic_deployment


class _Stage:
    root = "/tmp/wspq-stage"
    owner_token = "c" * 64

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], float | None]] = []

    def run_python(
        self, program: str, *arguments: str, timeout_s: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((program, arguments, timeout_s))
        return subprocess.CompletedProcess([], 0, "", "")


class _TransmitterStage(_Stage):
    def run_python(
        self, program: str, *arguments: str, timeout_s: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        result = super().run_python(program, *arguments, timeout_s=timeout_s)
        if "wsprrypi.bundle" in program:
            deployment = arguments[0]
            return subprocess.CompletedProcess([], 0, f"{deployment}/wsprrypi\n", "")
        return result


def test_transmitter_binary_is_exclusively_installed_per_campaign() -> None:
    stage = _TransmitterStage()

    paths = automatic_deployment._prepare_transmitter(stage)

    deployment = "/home/pi/wsprrypi-qualification-runs/complete-test-deployment-stage"
    assert paths["binary"] == f"{deployment}/wsprrypi"
    assert paths["deployment_root"] == deployment
    assert stage.calls[0][1] == (deployment, stage.owner_token)
    compile(stage.calls[0][0], "<transmitter-build-program>", "exec")
    assert "os.O_EXCL" in stage.calls[0][0]
    assert "os.fsync" in stage.calls[0][0]
    assert "cached.write_bytes" not in stage.calls[0][0]


def test_receiver_preparation_binds_cache_and_transmitter_trust() -> None:
    stage = _Stage()
    cache_key = "a" * 64
    runtime_key = "b" * 64

    paths = automatic_deployment._prepare_receiver(
        stage,
        "wspr4",
        "192.0.2.4",
        "wspr4 ssh-ed25519 AAAA\n",
        cache_key,
        runtime_key,
    )

    deployment_root = "/home/pi/wsprrypi-qualification-runs/complete-test-deployment-stage"
    assert paths["capture"] == f"{deployment_root}/wspq-capture-soapy"
    assert stage.calls[0][1] == (deployment_root, stage.owner_token)
    assert "os.mkdir(root,0o700)" in stage.calls[0][0]
    assert "os.O_EXCL" in stage.calls[0][0]
    assert stage.calls[1][1] == (paths["capture"],)
    compile(stage.calls[1][0], "<native-build-program>", "exec")
    assert "os.O_EXCL" in stage.calls[1][0]
    assert "os.fsync" in stage.calls[1][0]
    assert "cached.write_bytes" not in stage.calls[1][0]
    assert stage.calls[2][1] == (
        f"{deployment_root}/runtime.zip",
        runtime_key,
    )
    assert "os.O_EXCL" in stage.calls[2][0]
    written = {
        arguments[0]: base64.urlsafe_b64decode(arguments[1]).decode()
        for _, arguments, _ in stage.calls[3:]
    }
    wrapper = written[paths["ssh"]]
    assert all(path.startswith(f"{deployment_root}/") for path in written)
    assert "StrictHostKeyChecking=yes" in wrapper
    assert f"UserKnownHostsFile={deployment_root}/tx-known-hosts" in wrapper
    assert "HostKeyAlias=wspr4" in wrapper
    assert "HostName=192.0.2.4" in wrapper


def test_transmitter_address_discovery_uses_strict_selected_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ssh = tmp_path / "ssh"
    known_hosts = tmp_path / "known_hosts"
    observed: list[str] = []

    def run(arguments: list[str], *, timeout_s: float = 120.0):
        del timeout_s
        observed.extend(arguments)
        return subprocess.CompletedProcess(arguments, 0, "192.0.2.4\n", "")

    monkeypatch.setattr(automatic_deployment, "_run", run)

    assert (
        automatic_deployment._host_address("wspr4", ssh=ssh, known_hosts=known_hosts) == "192.0.2.4"
    )
    assert observed == [
        str(ssh),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "--",
        "wspr4",
        "hostname -I",
    ]
