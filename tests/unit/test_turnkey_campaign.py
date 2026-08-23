import json
from copy import deepcopy
from pathlib import Path

import pytest

import wsprrypi_qualification.live_adapters as live_adapters
import wsprrypi_qualification.live_keyed as live_keyed
import wsprrypi_qualification.real_session as real_session
from tests.unit.test_keyed_session_contracts import plan as keyed_plan
from tests.unit.test_real_session import plan_document, tone_plan_document
from wsprrypi_qualification.cli import main
from wsprrypi_qualification.turnkey_campaign import (
    TurnkeyCampaignError,
    canonical_sha256,
    compose_resolved_campaign_plan,
    resolved_campaign_sha256,
    run_hardware_free_campaign,
    run_live_campaign,
    validate_resolved_campaign_plan,
)


def _write(path: Path, document: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _child(mode: str, policy: str) -> dict:
    execution_mode = "live" if policy == "live" else "hardware_free_validation"
    if mode == "TONE":
        return tone_plan_document(execution_mode=execution_mode)
    if mode == "WSPR":
        return plan_document(execution_mode=execution_mode)
    return keyed_plan(mode)


def _resolved(tmp_path: Path, mode: str, policy: str = "hardware_free") -> dict:
    request = {
        "schema_version": 1,
        "evidence_type": "turnkey_campaign_request",
        "campaign_id": f"campaign-{mode.lower()}",
        "mode": mode,
        "execution_policy": policy,
    }
    request_path = _write(tmp_path / "inputs with spaces" / "request.json", request)
    child_path = _write(tmp_path / "inputs with spaces" / "mode-plan.json", _child(mode, policy))
    return compose_resolved_campaign_plan(request_path, child_path)


@pytest.mark.parametrize(
    ("mode", "route"),
    (
        ("TONE", "real_session"),
        ("WSPR", "real_session"),
        ("QRSS", "live_keyed"),
        ("FSKCW", "live_keyed"),
        ("DFCW", "live_keyed"),
    ),
)
def test_every_mode_routes_without_constructing_production_adapters(
    tmp_path: Path, mode: str, route: str
) -> None:
    resolved = _resolved(tmp_path, mode)
    assert resolved["production_route"] == route
    assert resolved["production_adapters_constructed"] is False
    outcome = run_hardware_free_campaign(resolved, tmp_path / "runs")
    assert outcome["result"]["final_status"] == "inconclusive"
    assert outcome["result"]["qualification_claim"] is False


def test_mode_mismatch_and_changed_bound_input_fail_closed(tmp_path: Path) -> None:
    request = {
        "schema_version": 1,
        "evidence_type": "turnkey_campaign_request",
        "campaign_id": "mismatch",
        "mode": "QRSS",
        "execution_policy": "hardware_free",
    }
    request_path = _write(tmp_path / "request.json", request)
    child_path = _write(tmp_path / "mode.json", keyed_plan("FSKCW"))
    with pytest.raises(TurnkeyCampaignError, match="differs"):
        compose_resolved_campaign_plan(request_path, child_path)

    resolved = _resolved(tmp_path / "changed", "QRSS")
    request_source = Path(resolved["request"]["artifact"]["path"])
    request["campaign_id"] = "replacement"
    _write(request_source, request)
    with pytest.raises(TurnkeyCampaignError, match="changed"):
        validate_resolved_campaign_plan(resolved)


def test_digest_is_canonical_and_destination_is_immutable(tmp_path: Path) -> None:
    assert canonical_sha256(
        {"windows_path": "C:\\Program Files\\Harness", "n": 1}
    ) == canonical_sha256({"n": 1, "windows_path": "C:\\Program Files\\Harness"})
    resolved = _resolved(tmp_path, "QRSS")
    assert resolved_campaign_sha256(resolved) == resolved_campaign_sha256(deepcopy(resolved))
    run_hardware_free_campaign(resolved, tmp_path / "runs")
    with pytest.raises(TurnkeyCampaignError, match="not new"):
        run_hardware_free_campaign(resolved, tmp_path / "runs")


@pytest.mark.parametrize("mode", ("TONE", "QRSS"))
@pytest.mark.parametrize(("operator", "digest"), (("operator", "wrong"), (" ", "exact")))
def test_live_builder_is_delayed_until_exact_confirmation(
    tmp_path: Path, monkeypatch, mode: str, operator: str, digest: str
) -> None:
    resolved = _resolved(tmp_path, mode, "live")

    def forbidden(*args, **kwargs):
        raise AssertionError("production adapter constructed before confirmation")

    monkeypatch.setattr(live_keyed, "build_production_keyed_adapter", forbidden)
    monkeypatch.setattr(live_adapters, "build_production_adapters", forbidden)
    with pytest.raises(TurnkeyCampaignError, match="exact campaign digest"):
        run_live_campaign(
            resolved,
            tmp_path / "runs",
            operator=operator,
            confirmed_plan_sha256=(
                resolved_campaign_sha256(resolved) if digest == "exact" else "0" * 64
            ),
            ssh_executable=Path("/usr/bin/ssh"),
            work_directory=tmp_path,
        )


def test_live_dispatch_returns_underlying_authoritative_bundle(tmp_path: Path, monkeypatch) -> None:
    resolved = _resolved(tmp_path, "QRSS", "live")
    authoritative = tmp_path / "underlying-bundle"
    calls = []

    def build(*args, **kwargs):
        calls.append("build")
        return object()

    def run(plan, authorization, output_parent, adapter):
        calls.append("run")
        return {"bundle": str(authoritative), "final_status": "qualified"}

    monkeypatch.setattr(live_keyed, "build_production_keyed_adapter", build)
    monkeypatch.setattr(live_keyed, "run_live_keyed_session", run)
    result = run_live_campaign(
        resolved,
        tmp_path / "runs",
        operator="operator",
        confirmed_plan_sha256=resolved_campaign_sha256(resolved),
        ssh_executable=Path("/usr/bin/ssh"),
        work_directory=tmp_path,
    )
    assert calls == ["build", "run"]
    assert result["authoritative_bundle"] == str(authoritative)
    assert result["underlying_result"]["final_status"] == "qualified"


def test_real_session_dispatch_uses_child_digest_and_authoritative_bundle(
    tmp_path: Path, monkeypatch
) -> None:
    resolved = _resolved(tmp_path, "TONE", "live")
    calls = []

    def build(*args, **kwargs):
        calls.append("build")
        return object()

    class FakeSession:
        def __init__(self, plan, adapters, *, now):
            self.plan = plan

        def run(self, external, rf, output_parent):
            calls.append((external.resolved_plan_sha256, rf.resolved_plan_sha256, self.plan.sha256))
            return {"run_id": "authoritative-real-run", "final_status": "qualified"}

    monkeypatch.setattr(live_adapters, "build_production_adapters", build)
    monkeypatch.setattr(real_session, "RealQualificationSession", FakeSession)
    result = run_live_campaign(
        resolved,
        tmp_path / "runs",
        operator="operator",
        confirmed_plan_sha256=resolved_campaign_sha256(resolved),
        ssh_executable=Path("/usr/bin/ssh"),
        work_directory=tmp_path,
    )
    assert calls[0] == "build"
    assert calls[1][0] == calls[1][1] == calls[1][2]
    assert result["authoritative_bundle"].endswith("authoritative-real-run")


def test_cli_exposes_one_discoverable_turnkey_surface(tmp_path: Path, capsys) -> None:
    resolved = _resolved(tmp_path, "QRSS")
    path = _write(tmp_path / "resolved.json", resolved)
    assert main(["turnkey-campaign", "validate", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["valid"] is True
    assert output["external_calls"] == 0


@pytest.mark.parametrize("missing_flag", ("--enable-turnkey-live", "--enable-rf"))
def test_cli_requires_both_live_enable_flags(tmp_path: Path, missing_flag: str) -> None:
    arguments = [
        "turnkey-campaign",
        "execute",
        "plan.json",
        "runs",
        "--operator",
        "operator",
        "--work-directory",
        str(tmp_path),
        "--ssh",
        "/usr/bin/ssh",
        "--confirm-plan-sha256",
        "0" * 64,
        "--enable-turnkey-live",
        "--enable-rf",
    ]
    arguments.remove(missing_flag)
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
