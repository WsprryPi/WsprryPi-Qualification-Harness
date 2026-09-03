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
