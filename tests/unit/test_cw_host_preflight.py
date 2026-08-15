from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from wsprrypi_qualification.cw_host_preflight import (
    CwHostPreflightError,
    Probe,
    probes_for,
    run_cw_actual_host_preflight,
    validate_cw_actual_host_preflight_bundle,
    validate_probe,
)


def plan() -> dict:
    return {
        "schema_version": 1,
        "evidence_type": "cw_actual_host_preflight_plan",
        "run_id": "20260815T120000Z-test",
        "controller_revision": "a" * 40,
        "gate_d_status": "incomplete",
        "timeout_seconds": 2,
        "known_blockers": ["gate-d-not-complete"],
        "rf_path": {
            "declared_current": False,
            "antenna_state": "unverified",
            "termination": "unverified",
            "attenuation": "unverified",
            "safe_input_basis": "not established",
        },
        "hosts": [
            {
                "host_id": "wspr5",
                "role": "combined",
                "ssh_destination": "pi@wspr5.local",
                "host_key_alias": "wspr5.local",
                "expected_hostname": "wspr5",
                "expected_model": "Raspberry Pi 5 Model B Rev 1.0",
                "expected_revision_hex": "00c04170",
                "repository_path": "/home/pi/WsprryPi",
                "expected_repository_revision": "b" * 40,
                "required_binaries": ["python3", "git"],
                "required_groups": ["gpio"],
                "inspect_services": ["wsprrypi.service"],
                "conflicting_process_names": ["wsprrypi"],
            }
        ],
    }


class FakeRunner:
    def run(self, arguments: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
        del timeout_s
        command = arguments[arguments.index("pi@wspr5.local") + 1 :]
        outputs = {
            ("hostname",): "wspr5\n",
            ("cat", "/proc/device-tree/model"): "Raspberry Pi 5 Model B Rev 1.0\x00",
            ("od", "-An", "-tx1", "/proc/device-tree/system/linux,revision"): " 00 c0 41 70\n",
            ("id",): "uid=1000(pi) gid=1000(pi) groups=1000(pi),997(gpio)\n",
            ("git", "--no-optional-locks", "-C", "/home/pi/WsprryPi", "rev-parse", "HEAD"): "b" * 40
            + "\n",
            (
                "git",
                "--no-optional-locks",
                "-C",
                "/home/pi/WsprryPi",
                "status",
                "--short",
                "--branch",
                "--untracked-files=no",
            ): "## devel...origin/devel\n",
            ("which", "python3"): "/usr/bin/python3\n",
            ("which", "git"): "/usr/bin/git\n",
            ("ps", "-eo", "pid,ppid,user,etimes,stat,comm"): "PID COMMAND\n",
        }
        return subprocess.CompletedProcess(
            arguments, 0, outputs.get(tuple(command), "observed\n"), ""
        )


class EmptyClockRunner(FakeRunner):
    def run(self, arguments: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
        result = super().run(arguments, timeout_s)
        if "timedatectl" in arguments:
            return subprocess.CompletedProcess(arguments, 0, "", "")
        return result


@pytest.mark.parametrize(
    "arguments",
    [
        ("sudo", "cat", "/proc/modules"),
        ("sh", "-c", "uname"),
        ("cat", "/dev/mem"),
        ("systemctl", "restart", "wsprrypi.service"),
        ("git", "-C", "/repo", "status"),
        ("cat", "/proc/modules;reboot"),
    ],
)
def test_safety_boundary_rejects_mutation_and_shell_escape(arguments: tuple[str, ...]) -> None:
    with pytest.raises(CwHostPreflightError):
        validate_probe(Probe("attack", arguments))


def test_all_production_probes_pass_safety_classifier() -> None:
    assert probes_for(plan()["hosts"][0])


def test_digest_and_enable_are_both_required(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan()) + "\n", encoding="utf-8")
    ssh = tmp_path / "ssh"
    ssh.write_text("fake\n", encoding="utf-8")
    with pytest.raises(CwHostPreflightError, match="digest"):
        run_cw_actual_host_preflight(
            plan_path,
            tmp_path,
            ssh_path=ssh.resolve(),
            confirmation_sha256="0" * 64,
            enabled=True,
            runner=FakeRunner(),
        )


def test_blocked_bundle_is_immutable_and_validated(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan(), sort_keys=True) + "\n", encoding="utf-8")
    ssh = tmp_path / "ssh"
    ssh.write_text("fake\n", encoding="utf-8")
    digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    result = run_cw_actual_host_preflight(
        plan_path,
        tmp_path,
        ssh_path=ssh.resolve(),
        confirmation_sha256=digest,
        enabled=True,
        runner=FakeRunner(),
    )
    assert result["overall_outcome"] == "blocked"
    assert result["qualification_claim"] is False
    assert result["rf_or_hardware_operation_performed"] is False
    bundle = Path(result["bundle"])
    assert validate_cw_actual_host_preflight_bundle(bundle)["overall_outcome"] == "blocked"
    records = json.loads((bundle / "command-records.json").read_text(encoding="utf-8"))["records"]
    assert all("sudo" not in record["arguments"] for record in records)


def test_manifest_or_command_tampering_is_rejected(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan(), sort_keys=True) + "\n", encoding="utf-8")
    ssh = tmp_path / "ssh"
    ssh.write_text("fake\n", encoding="utf-8")
    result = run_cw_actual_host_preflight(
        plan_path,
        tmp_path,
        ssh_path=ssh.resolve(),
        confirmation_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        enabled=True,
        runner=FakeRunner(),
    )
    bundle = Path(result["bundle"])
    document = json.loads((bundle / "command-records.json").read_text(encoding="utf-8"))
    document["records"][0]["arguments"].append("reboot")
    (bundle / "command-records.json").write_text(json.dumps(document) + "\n", encoding="utf-8")
    with pytest.raises(CwHostPreflightError):
        validate_cw_actual_host_preflight_bundle(bundle)


def test_empty_required_observation_blocks_preflight(tmp_path: Path) -> None:
    document = plan()
    document["run_id"] = "20260815T120001Z-test"
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    ssh = tmp_path / "ssh"
    ssh.write_text("fake\n", encoding="utf-8")
    result = run_cw_actual_host_preflight(
        plan_path,
        tmp_path,
        ssh_path=ssh.resolve(),
        confirmation_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        enabled=True,
        runner=EmptyClockRunner(),
    )
    clock = next(
        check for check in result["host_results"][0]["checks"] if check["check_id"] == "clock"
    )
    assert clock["outcome"] == "failed"
