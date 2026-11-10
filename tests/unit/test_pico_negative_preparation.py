"""Clock/input preparation is offline; no device or network constructors."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.test_pico_negative_gate import g


def observation():
    return dict(
        tracking_csv="50505300,PPS,1,1700000000,0.000001,0,0,2,0,1,0.000002,0.000003,16,Normal",
        before_ns=100_000_000_000,
        after_ns=100_001_000_000,
        wall_ns=1_700_000_001_000_000_000,
        tool=dict(path="/fixture/chronyc", sha256="a" * 64),
    )


def test_clock_bound_uses_documented_terms_and_projection():
    clock = g.clock_from_observation(observation())
    # 1us system + 3us dispersion + 1us half-delay + 180us rate + 1ms observation.
    assert clock["uncertainty_ns"] == 1_185_000
    assert clock["wall_offset_ns"] == 1_700_000_001_000_000_000 - 100_001_000_000


@pytest.mark.parametrize(
    "field,value",
    [
        (0, "00000000"),
        (0, "7F7F0101"),
        (2, "0"),
        (2, "16"),
        (4, "NaN"),
        (9, "-1"),
        (10, "-1"),
        (11, "-1"),
        (13, "Not synchronised"),
        (3, "1800000000"),
    ],
)
def test_clock_observation_refusals(field, value):
    raw = observation()
    fields = raw["tracking_csv"].split(",")
    fields[field] = value
    raw["tracking_csv"] = ",".join(fields)
    with pytest.raises(ValueError):
        g.clock_from_observation(raw)


def test_old_100ms_host_limit_is_not_an_admission_requirement():
    raw = observation()
    fields = raw["tracking_csv"].split(",")
    fields[11] = "0.2"
    raw["tracking_csv"] = ",".join(fields)
    assert g.clock_from_observation(raw)["uncertainty_ns"] > 100_000_000


def test_reference_age_does_not_override_reported_uncertainty():
    raw = observation()
    raw["tracking_csv"] = raw["tracking_csv"].replace("1700000000", "1600000000")
    assert g.clock_from_observation(raw)["uncertainty_ns"] == 1_185_000


def test_unsupported_live_platform_refuses_before_processes(monkeypatch):
    monkeypatch.setattr(g.sys, "platform", "win32")
    monkeypatch.setattr(g.subprocess, "Popen", lambda *a, **kw: pytest.fail("Process created"))
    with pytest.raises(ValueError, match="Linux pidfd"):
        g.platform_check()


def test_clock_summary_cannot_override_observation():
    raw = observation()
    pre = dict(
        scope="local_process_and_clock_only",
        execution_kind="physical_inputs",
        device_admission="pending_production_runner",
        observation=raw,
        clock=g.clock_from_observation(raw),
    )
    g.validate_clock_evidence(pre)
    pre["clock"]["uncertainty_ns"] = 0
    with pytest.raises(ValueError):
        g.validate_clock_evidence(pre)


def test_clock_collection_is_fixed_read_only_and_bounded(monkeypatch, tmp_path):
    executable = tmp_path / "chronyc"
    executable.write_text("fixture")
    raw = observation()
    readings = iter((raw["before_ns"], raw["after_ns"]))
    monkeypatch.setattr(g.time, "monotonic_ns", lambda: next(readings))
    monkeypatch.setattr(g.time, "time_ns", lambda: raw["wall_ns"])

    def run(args, **kw):
        if args == [str(executable), "-v"]:
            assert kw["timeout"] == 1 and kw["check"] is False
            return SimpleNamespace(stdout="fixture chronyc 1", returncode=0)
        assert args == [str(executable), "-n", "-c", "tracking"]
        assert kw["timeout"] == 2 and kw["check"] is True
        assert "SSH_AUTH_SOCK" not in kw["env"]
        return SimpleNamespace(stdout=raw["tracking_csv"])

    monkeypatch.setattr(g.subprocess, "run", run)
    observed, clock = g.collect_clock(executable)
    assert observed["tool"]["sha256"] == g.digest(executable)
    assert observed["tool"]["version"] == "fixture chronyc 1"
    assert clock == g.clock_from_observation(observed)


@pytest.mark.skipif(__import__("os").name != "posix", reason="Linux adapter file publication")
def test_prepare_inputs_and_refuse_reuse_without_claiming_admission(monkeypatch, tmp_path):
    helper = tmp_path / "helper with spaces"
    helper.write_text("inert fixture")
    plan = {"configuration": {"receiver": {"driver": "sdrplay", "capture_helper": str(helper)}}}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    root = tmp_path / "new inputs with spaces"
    raw = observation()
    monkeypatch.setattr(g, "platform_check", lambda: None)
    monkeypatch.setattr(g, "check_scope", lambda p: None)
    monkeypatch.setattr(g, "collect_clock", lambda p: (raw, g.clock_from_observation(raw)))
    real = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **kw: (
            "boot" if str(p) == "/proc/sys/kernel/random/boot_id" else real(p, *a, **kw)
        ),
    )
    with pytest.raises(ValueError):
        g.prepare(path, root, helper)
    assert not root.exists()
    result = g.prepare(path, root, helper, observe_host=True)
    assert result["rf_authorized"] is False and result["device_admission"] == "pending"
    pre = g.read(root / "host-preflight.json")
    g.validate_clock_evidence(pre)
    assert pre["scope"] == "local_process_and_clock_only"
    assert g.read(root / "resolved-gate.json")["capture_helper"]["sha256"] == g.digest(helper)
    with pytest.raises(FileExistsError):
        g.prepare(path, root, helper, observe_host=True)
    fixture = copy.deepcopy(plan)
    fixture["configuration"]["receiver"]["driver"] = "fixture"
    path.write_text(json.dumps(fixture))
    with pytest.raises(ValueError):
        g.prepare(path, tmp_path / "refused", helper, observe_host=True)
