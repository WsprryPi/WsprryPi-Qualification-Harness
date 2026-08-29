import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wsprrypi_qualification.cli import main
from wsprrypi_qualification.rp1_campaign import (
    Rp1CampaignError,
    compose_rp1_rehearsal,
    validate_rp1_rehearsal,
)
from wsprrypi_qualification.rp1_contracts import route_contract


def configuration(tmp_path: Path, route: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    identity = {
        **route_contract(route),
        "power_level": 0,
        "rp1_drive_ma": 2,
        "dkms_source_revision": "3" * 40,
        "uapi_sha256": "4" * 64,
        "module_build_id": "development-build",
    }
    document = {
        "schema_version": 1,
        "evidence_type": "rp1_complete_test_rehearsal_config",
        "host": "wspr5",
        "output_parent": str(tmp_path / "runs"),
        "wsprrypi": {
            "executable": "/opt/wsprrypi/wsprrypi",
            "source_revision": "1" * 40,
            "component_revision": "2" * 40,
        },
        "rp1_identity": identity,
        "transmitter_role": {
            "role": "transmitter",
            "host": "wspr5",
            "helper_sha256": "5" * 64,
            "config_sha256": "6" * 64,
        },
        "receiver_role": {
            "role": "receiver",
            "host": "wspr5",
            "helper_sha256": "7" * 64,
            "config_sha256": "8" * 64,
        },
        "receiver": {
            "host": "wspr5",
            "driver": "sdrplay",
            "serial": "2404058C60",
            "sample_rate_hz": 250000,
            "bandwidth_hz": 200000,
            "center_frequency_hz": 14072100,
            "gain_db": 20,
            "agc": False,
            "bias_tee": False,
        },
        "rf_path": {
            "path_type": "conducted",
            "antenna_connected": False,
            "termination": "50 ohm direct SDR input through 20 dB attenuation",
            "attenuation_db": 20,
            "safe_input_basis": "hardware-free future-path binding",
        },
        "transmitter_ppm_source": {
            "source_type": "manual_host_ppm",
            "value_ppm": 3.560,
            "host": "wspr5",
            "backend": "rp1_gpclk",
            "route": route,
            "compatibility_id": identity["compatibility_id"],
            "provenance": "operator-supplied Step 6 campaign value",
            "application_path": "--gpio-manual-ppm",
        },
        "frequency_hz": 14097100,
        "callsign": "Q0QQQ",
        "grid": "JJ00",
        "power_dbm": 0,
        "message": "ETE",
        "dot_seconds": 0.7,
        "separation_hz": 5.0,
    }
    path = tmp_path / f"{route}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_both_routes_compose_ten_independent_hardware_free_plans(tmp_path: Path) -> None:
    campaigns = [
        compose_rp1_rehearsal(
            configuration(tmp_path / route, route),
            route,
            residual_ppm=0.0,
            carrier_offset_max_hz=250.0,
            now=datetime(2026, 8, 26, 18, 0, tzinfo=UTC),
        )
        for route in ("gpio4", "gpio20")
    ]
    plans = [entry for campaign in campaigns for entry in campaign["plans"]]
    assert len(plans) == 10
    assert len({entry["route_mode_id"] for entry in plans}) == 10
    assert len({entry["plan_sha256"] for entry in plans}) == 10
    assert all(entry["carrier_offset_max_hz"] == 250 for entry in plans)
    assert all(entry["transmitter_ppm_resolution"]["effective_ppm"] == 3.56 for entry in plans)
    for entry in plans:
        arguments = entry["application_plan"]["arguments"]
        assert arguments[1:3] == ["--backend", "rp1-gpclk"]
        assert arguments[1:3] != ["--backend", "gpio"]
        assert arguments.count("--gpio-manual-ppm") == 1
        assert arguments.count("--no-system-clock-frequency-estimate") == 1
        assert arguments.count("--transmit-gpio") == 1
        assert arguments.count("--rp1-gpio-drive-ma") == 1


def test_route_substitution_tone_and_digest_tampering_fail(tmp_path: Path) -> None:
    campaign = compose_rp1_rehearsal(configuration(tmp_path, "gpio4"), "gpio4")
    wrong = deepcopy(campaign)
    wrong["plans"][0]["application_plan"]["backend_contract"]["rp1_route"] = "gpio20"
    with pytest.raises((Rp1CampaignError, ValueError)):
        validate_rp1_rehearsal(wrong)
    continuous = deepcopy(campaign)
    continuous["plans"][0]["rp1_lifecycle_contract"]["tone_operation"] = "CONTINUOUS"
    with pytest.raises(Rp1CampaignError, match="finite"):
        validate_rp1_rehearsal(continuous)
    changed = deepcopy(campaign)
    changed["plans"][1]["carrier_offset_max_hz"] = 251
    with pytest.raises(Rp1CampaignError, match="binding"):
        validate_rp1_rehearsal(changed)
    changed_digest = deepcopy(campaign)
    changed_digest["plans"][1]["plan_sha256"] = "0" * 64
    with pytest.raises(Rp1CampaignError, match="digest"):
        validate_rp1_rehearsal(changed_digest)


def test_cli_rehearsal_writes_bundle_and_live_rp1_dispatches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = configuration(tmp_path, "gpio4")
    assert (
        main(
            [
                "complete-test",
                "wspr5",
                "wspr5",
                "--sdr",
                "driver=sdrplay,serial=2404058C60",
                "--transmitter-backend",
                "rp1_gpclk",
                "--transmit-gpio",
                "4",
                "--configuration",
                str(config),
                "--carrier-offset-max-hz",
                "250",
                "--rehearse",
                "--progress-log",
                str(tmp_path / "rehearsal-progress.jsonl"),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert Path(result["bundle"], "rehearsal.json").is_file()
    assert result["qualification_claim"] is False
    observed: dict[str, object] = {}

    def delegated(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return {
            "bundle": "/tmp/rp1-live-result",
            "result": {
                "final_status": "preflight_failed",
                "transmitter_host": "wspr5",
                "receiver_host": "wspr5",
                "sdr_selector": "driver=sdrplay,serial=2404058C60",
            },
        }

    monkeypatch.setattr("wsprrypi_qualification.cli.delegate_automatic_complete_test", delegated)
    assert (
        main(
            [
                "complete-test",
                "wspr5",
                "wspr5",
                "--sdr",
                "driver=sdrplay,serial=2404058C60",
                "--transmitter-backend",
                "rp1_gpclk",
                "--rp1-route",
                "gpio4",
                "--enable-rf",
                "--progress-log",
                str(tmp_path / "live-progress.jsonl"),
            ]
        )
        == 3
    )
    assert observed["kwargs"]["transmit_gpio"] == 4
    assert observed["kwargs"]["transmitter_backend"] == "rp1_gpclk"


def test_cli_requires_route_exactly_for_rp1(tmp_path: Path, capsys) -> None:
    assert (
        main(
            [
                "complete-test",
                "wspr5",
                "wspr5",
                "--sdr",
                "driver=sdrplay,serial=x",
                "--transmitter-backend",
                "rp1_gpclk",
                "--rehearse",
                "--progress-log",
                str(tmp_path / "missing-route-progress.jsonl"),
            ]
        )
        == 2
    )
    assert "--transmit-gpio" in capsys.readouterr().err
    assert (
        main(
            [
                "complete-test",
                "wspr4",
                "wspr5",
                "--sdr",
                "driver=sdrplay,serial=x",
                "--rp1-route",
                "gpio4",
                "--rehearse",
                "--progress-log",
                str(tmp_path / "wrong-backend-progress.jsonl"),
            ]
        )
        == 2
    )
    assert "--transmit-gpio" in capsys.readouterr().err


def test_cli_rejects_conflicting_route_spellings(tmp_path: Path, capsys) -> None:
    assert (
        main(
            [
                "complete-test",
                "wspr5",
                "wspr5",
                "--sdr",
                "driver=sdrplay,serial=x",
                "--transmitter-backend",
                "rp1_gpclk",
                "--transmit-gpio",
                "4",
                "--rp1-route",
                "gpio20",
                "--rehearse",
                "--progress-log",
                str(tmp_path / "conflicting-route-progress.jsonl"),
            ]
        )
        == 2
    )
    assert "disagree" in capsys.readouterr().err
