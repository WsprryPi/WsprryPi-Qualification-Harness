from __future__ import annotations

import json
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest


def test_enrollments_are_recorded_without_becoming_an_eligibility_whitelist(
    tmp_path: Path,
) -> None:
    script = (
        Path(__file__).resolve().parents[2] / "deployment" / "raspberry-pi-os" / "wspq-rp1-inspect"
    )
    namespace = runpy.run_path(str(script))
    observe = cast(Callable[[Path], list[dict[str, object]]], namespace["observed_enrollments"])
    assert observe(tmp_path) == []
    first = tmp_path / "old.json"
    second = tmp_path / "new.json"
    first.write_text(json.dumps({"sourceCommit": "a" * 40, "route": "gpio20"}))
    second.write_text(json.dumps({"sourceCommit": "b" * 40, "route": "gpio20"}))
    observations = observe(tmp_path)
    assert [item["source_commit"] for item in observations] == ["b" * 40, "a" * 40]
    assert all(item["status"] == "parsed" for item in observations)


def test_passive_inspector_uses_current_immutable_output_inhibit_gate() -> None:
    script = (
        Path(__file__).resolve().parents[2] / "deployment" / "raspberry-pi-os" / "wspq-rp1-inspect"
    )
    source = script.read_text(encoding="utf-8")
    assert "parameters/output_inhibit" in source
    assert "parameters/live_output" not in source
    assert 'values["live_eligible"]' not in source


@pytest.mark.parametrize(
    ("output_inhibit", "snapshot_inhibited", "operational_ready", "expected"),
    [
        ("0", "false", "true", True),
        ("N", "false", "true", True),
        ("1", "true", "false", False),
        ("Y", "true", "false", False),
        ("0", "true", "true", False),
        ("0", "false", "false", False),
        ("unexpected", "false", "true", False),
    ],
)
def test_transmission_deployment_gate_fails_closed(
    output_inhibit: str,
    snapshot_inhibited: str,
    operational_ready: str,
    expected: bool,
) -> None:
    script = (
        Path(__file__).resolve().parents[2] / "deployment" / "raspberry-pi-os" / "wspq-rp1-inspect"
    )
    namespace = runpy.run_path(str(script))
    ready = cast(Callable[[str, dict[str, str]], bool], namespace["transmission_deployment_ready"])
    assert (
        ready(
            output_inhibit,
            {
                "output_inhibited": snapshot_inhibited,
                "operational_ready": operational_ready,
            },
        )
        is expected
    )


def test_device_tree_observation_follows_proc_symlink(tmp_path: Path) -> None:
    script = (
        Path(__file__).resolve().parents[2] / "deployment" / "raspberry-pi-os" / "wspq-rp1-inspect"
    )
    namespace = runpy.run_path(str(script))
    observe = cast(Callable[[str, Path], dict[str, object]], namespace["observed_device_tree"])
    actual = tmp_path / "sys" / "firmware" / "devicetree" / "base"
    node = actual / "soc" / "rp1-gpclk-dkms-gpio4"
    node.mkdir(parents=True)
    (node / "compatible").write_bytes(b"wsprrypi,rp1-gpclk-dkms-v1\0")
    (node / "clock-names").write_bytes(b"gpclk\0parent\0")
    (node / "clocks").write_bytes(bytes.fromhex("0000000100000021"))
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "device-tree").symlink_to(actual)

    observed = observe("rp1-gpclk-dkms-gpio4", proc / "device-tree")

    assert observed["status"] == "observed"
    assert observed["path"] == str(node)
    assert observed["clock_names"] == ["gpclk", "parent"]
