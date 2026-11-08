"""Hardware-free coverage of host-specific production composition checks."""

import hashlib
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.unit.test_real_session import plan_document, tone_plan_document
from wsprrypi_qualification import live_adapters
from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.real_session import RealSessionError, helper_verification_deadline


@pytest.fixture(params=["TONE", "WSPR"])
def composition(tmp_path, monkeypatch, request):
    plan = (
        tone_plan_document(execution_mode="live", gpio=True)
        if request.param == "TONE"
        else plan_document(execution_mode="live")
    )

    def pinned(name):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        return artifact(path)

    ssh = pinned("ssh")
    plan["capability_bindings"]["transmitter_ssh"] = ssh["sha256"]
    for name in ("known_hosts", "ssh_keygen"):
        record = pinned(name)
        plan["transport_identity"][f"{name}_path"] = record["path"]
        plan["transport_identity"][f"{name}_sha256"] = record["sha256"]
    for role in ("remote_helper", "receiver_helper"):
        plan[role].update(pinned(role))
        config = pinned(role + "-config")
        plan[role]["config_path"] = config["path"]
        plan[role]["config_sha256"] = config["sha256"]
    for name in ("capture_helper", "wsprd"):
        plan[name].update(pinned(name))
    for name, profile in plan["resolved_profiles"].items():
        profile.update(pinned(name))
    # Same pathname, different bytes on receiver and transmitter (e.g. ARM64/ARMv6 sudo).
    wrapper = pinned("sudo")
    plan["remote_helper"]["privilege_wrapper_path"] = wrapper["path"]
    remote_sha256 = hashlib.sha256(b"transmitter sudo").hexdigest()
    plan["remote_helper"]["privilege_wrapper_sha256"] = remote_sha256
    probe = Mock(return_value=subprocess.CompletedProcess((), 0, remote_sha256 + "\n", ""))

    def run(arguments, **kwargs):
        if arguments[0] == plan["transport_identity"]["ssh_keygen_path"]:
            output = (
                "host ssh-ed25519 key\n"
                if arguments[1] == "-F"
                else "256 " + plan["transport_identity"]["transmitter_host_key_sha256"] + " host\n"
            )
            return subprocess.CompletedProcess(arguments, 0, output, "")
        if arguments[0] == ssh["path"]:
            return probe(arguments, **kwargs)
        if "rev-parse" in arguments:
            return subprocess.CompletedProcess(arguments, 128, "", "not a git repository")
        pytest.fail(f"unexpected process: {arguments}")

    monkeypatch.setattr(live_adapters.subprocess, "run", run)
    transport = Mock()
    monkeypatch.setattr(live_adapters, "PersistentHelperTransport", transport)
    return plan, Path(ssh["path"]), tmp_path / "runtime", probe, transport


def build(composition):
    plan, ssh, runtime, _, _ = composition
    return live_adapters.build_production_adapters(plan, ssh_executable=ssh, work_directory=runtime)


@pytest.mark.parametrize("local_wrapper_exists", [True, False])
def test_split_host_uses_transmitter_wrapper_hash(composition, local_wrapper_exists):
    plan, ssh, runtime, probe, transport = composition
    wrapper = Path(plan["remote_helper"]["privilege_wrapper_path"])
    if local_wrapper_exists:
        assert artifact(wrapper)["sha256"] != plan["remote_helper"]["privilege_wrapper_sha256"]
    else:
        wrapper.unlink()

    adapter = build(composition)

    assert adapter.paths.work_directory == runtime.resolve()
    probe.assert_called_once()
    arguments = probe.call_args.args[0]
    assert arguments[0] == str(ssh)
    assert arguments[-2] == plan["host"]
    assert "BatchMode=yes" in arguments
    assert "StrictHostKeyChecking=yes" in arguments
    assert f"UserKnownHostsFile={plan['transport_identity']['known_hosts_path']}" in arguments
    probe_command = shlex.split(arguments[-1])
    assert probe_command[:2] == ["/usr/bin/python3", "-c"]
    assert probe_command[-1] == str(wrapper)
    assert probe.call_args.kwargs["timeout"] == helper_verification_deadline(plan)
    assert probe.call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert probe.call_args.kwargs["shell"] is False
    assert transport.call_count == 2
    assert transport.call_args_list[0].args[0][:-1] == arguments[:-1]
    assert shlex.split(transport.call_args_list[0].args[0][-1])[:2] == [str(wrapper), "-n"]


@pytest.mark.parametrize("failure", ["mismatch", "missing", "extra_output", "timeout", "oserror"])
def test_split_host_rejects_unverified_wrapper_before_helpers(composition, failure):
    plan, _, runtime, probe, transport = composition
    # A locally matching wrapper must not hide failure on the actual transmitter.
    plan["remote_helper"]["privilege_wrapper_sha256"] = artifact(
        Path(plan["remote_helper"]["privilege_wrapper_path"])
    )["sha256"]
    expected = plan["remote_helper"]["privilege_wrapper_sha256"]
    if failure == "timeout":
        probe.side_effect = subprocess.TimeoutExpired("ssh", 1)
    elif failure == "oserror":
        probe.side_effect = OSError("cannot launch ssh")
    else:
        output = "0" * 64 + "\n" if failure == "mismatch" else expected + "\n"
        if failure == "extra_output":
            output += "unexpected second line\n"
        probe.return_value = subprocess.CompletedProcess(
            (), 1 if failure == "missing" else 0, output, ""
        )

    with pytest.raises(RealSessionError, match="privilege wrapper"):
        build(composition)

    probe.assert_called_once()
    transport.assert_not_called()
    assert not runtime.exists()


@pytest.mark.parametrize("matches", [True, False])
def test_same_host_verifies_wrapper_locally_without_ssh(composition, matches):
    plan, _, runtime, probe, transport = composition
    plan["topology"] = "same_host_roles"
    plan["transport"] = "local_role_channels"
    wrapper = Path(plan["remote_helper"]["privilege_wrapper_path"])
    if matches:
        plan["remote_helper"]["privilege_wrapper_sha256"] = artifact(wrapper)["sha256"]
        build(composition)
        assert transport.call_args_list[0].args[0][:2] == (str(wrapper), "-n")
    else:
        with pytest.raises(RealSessionError, match="privilege wrapper identity changed"):
            build(composition)
        transport.assert_not_called()
        assert not runtime.exists()
    probe.assert_not_called()


@pytest.mark.parametrize("path", [None, "sudo", "C:\\Windows\\sudo.exe", "/bin/sudo\x00"])
def test_invalid_wrapper_path_blocks_probe_and_helpers(composition, path):
    plan, _, runtime, probe, transport = composition
    plan["remote_helper"]["privilege_wrapper_path"] = path
    with pytest.raises(RealSessionError, match="privilege wrapper path"):
        build(composition)
    probe.assert_not_called()
    transport.assert_not_called()
    assert not runtime.exists()


def test_probe_hashes_bytes_without_executing_wrapper_and_quotes_path(composition, monkeypatch):
    plan, _, runtime, probe, transport = composition
    # Exercise the generated Python probe against an inert file. Its path must
    # remain one argument in both the read-only probe and the helper command.
    wrapper = runtime.parent / "sudo with ' quote $()"
    wrapper.write_bytes(b"not an executable\n")
    plan["remote_helper"]["privilege_wrapper_path"] = str(wrapper)
    expected = artifact(wrapper)["sha256"]
    plan["remote_helper"]["privilege_wrapper_sha256"] = expected
    probe.return_value = subprocess.CompletedProcess((), 0, expected + "\n", "")
    build(composition)
    command = shlex.split(probe.call_args.args[0][-1])
    assert command[-1] == str(wrapper)
    assert shlex.split(transport.call_args_list[0].args[0][-1])[0] == str(wrapper)
    monkeypatch.undo()
    result = subprocess.run(
        (sys.executable, *command[1:]),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        shell=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected + "\n"
    wrapper.unlink()
    missing = subprocess.run(
        (sys.executable, *command[1:]),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        shell=False,
    )
    assert missing.returncode != 0
    assert missing.stdout == ""
