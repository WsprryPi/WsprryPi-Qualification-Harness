from pathlib import Path


def test_installed_live_package_smoke_covers_required_modules_and_schemas() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / "scripts" / "verify_installed_live_package.py").read_text(encoding="utf-8")
    for required in (
        "wsprrypi_qualification.live_adapters",
        "wsprrypi_qualification.real_session",
        "application-plan.schema.json",
        "real-session-stage-evidence.schema.json",
        "resolved-real-session-plan.schema.json",
        "receiver-calibration-binding.schema.json",
        "wsprrypi_qualification.turnkey_campaign",
        "turnkey-campaign-request.schema.json",
        "resolved-turnkey-campaign-plan.schema.json",
        "turnkey-campaign-result.schema.json",
    ):
        assert required in text
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python scripts/ci_install_wheel_smoke.py" in workflow
