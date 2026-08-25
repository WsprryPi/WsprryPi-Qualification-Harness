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


class _InstalledTransmitterStage(_Stage):
    def run_python(
        self, program: str, *arguments: str, timeout_s: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        super().run_python(program, *arguments, timeout_s=timeout_s)
        return subprocess.CompletedProcess([], 0, f"{arguments[0]}/wsprrypi\n", "")


def test_transmitter_binary_is_exclusively_installed_per_campaign() -> None:
    stage = _TransmitterStage()

    paths = automatic_deployment._prepare_transmitter(stage)

    deployment = "/home/pi/wsprrypi-qualification-runs/complete-test-deployment-stage"
    assert paths["binary"] == f"{deployment}/wsprrypi"
    assert paths["deployment_root"] == deployment
    assert paths["tone_configuration"] == f"{deployment}/wsprrypi.ini"
    assert paths["tone_configuration_source"] == (f"{stage.root}/source/config/wsprrypi.ini")
    assert stage.calls[0][1] == (deployment, stage.owner_token)
    compile(stage.calls[0][0], "<transmitter-build-program>", "exec")
    assert "os.O_EXCL" in stage.calls[0][0]
    assert "os.fsync" in stage.calls[0][0]
    assert "cached.write_bytes" not in stage.calls[0][0]
    assert "build_source=root/'build-source'" in stage.calls[0][0]
    assert "str(build_source/'src')" in stage.calls[0][0]
    assert "str(src/'src')" not in stage.calls[0][0]


def test_installed_configuration_is_staged_byte_for_byte_without_normalization() -> None:
    stage = _InstalledTransmitterStage()
    paths = automatic_deployment._prepare_transmitter(
        stage, installed_binary="/usr/local/bin/wsprrypi"
    )
    program, arguments, _ = stage.calls[0]
    assert arguments[-2:] == ("/usr/local/bin/wsprrypi", "/usr/local/etc/wsprrypi.ini")
    assert "config_source.read_bytes()" in program
    assert "splitlines" not in program
    assert "filtered" not in program
    assert paths["tone_configuration"].startswith(paths["deployment_root"])


def test_custom_installed_binary_and_configuration_are_bound_explicitly() -> None:
    stage = _InstalledTransmitterStage()
    automatic_deployment._prepare_transmitter(
        stage,
        installed_binary="/opt/wsprrypi/bin/wsprrypi",
        installed_configuration="/opt/wsprrypi/etc/runtime.ini",
    )
    _, arguments, timeout = stage.calls[0]
    assert arguments[-2:] == (
        "/opt/wsprrypi/bin/wsprrypi",
        "/opt/wsprrypi/etc/runtime.ini",
    )
    assert timeout == 120


def test_source_checkout_selection_is_explicit_and_exact(tmp_path: Path) -> None:
    source = tmp_path / "selected WsprryPi"
    (source / ".git").mkdir(parents=True)
    (source / "src").mkdir()
    assert automatic_deployment._source_repository(source) == source.resolve()


def test_invalid_explicit_source_checkout_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(automatic_deployment.AutomaticDeploymentError, match="selected"):
        automatic_deployment._source_repository(tmp_path / "missing")


def test_runtime_selection_rejects_ambiguity_and_absence_before_deployment(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / ".git").mkdir(parents=True)
    (source / "src").mkdir()
    with pytest.raises(automatic_deployment.AutomaticDeploymentError, match="mutually"):
        automatic_deployment._validate_runtime_selection(
            "/usr/local/bin/wsprrypi", "/usr/local/etc/wsprrypi.ini", source
        )
    with pytest.raises(automatic_deployment.AutomaticDeploymentError, match="required"):
        automatic_deployment._validate_runtime_selection(None, "/usr/local/etc/wsprrypi.ini", None)


def test_installed_runtime_paths_must_be_absolute() -> None:
    stage = _InstalledTransmitterStage()
    with pytest.raises(automatic_deployment.AutomaticDeploymentError, match="absolute"):
        automatic_deployment._prepare_transmitter(stage, installed_binary="relative/wsprrypi")


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
