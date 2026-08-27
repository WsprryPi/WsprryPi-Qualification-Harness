import json
from pathlib import Path

import pytest

from wsprrypi_qualification.automatic_configuration import write_automatic_configuration
from wsprrypi_qualification.complete_test import CompleteTestOverrides, compose_complete_test_plan
from wsprrypi_qualification.offline import artifact


def test_discovered_facts_create_all_five_production_plans(tmp_path: Path) -> None:
    names = (
        "ssh",
        "ssh_keygen",
        "known_hosts",
        "tx_helper",
        "tx_helper_config",
        "tx_keyed_helper_config",
        "tx_sudo",
        "tx_systemctl",
        "tx_gpio",
        "tx_si5351",
        "tx_wsprrypi",
        "tx_git",
        "rx_helper",
        "rx_helper_config",
        "rx_systemctl",
        "rx_gpio",
        "capture_helper",
        "wsprd",
        "tone_ini_source",
        "tone_ini",
    )
    artifacts = {}
    for name in names:
        path = (
            tmp_path / "source/config/wsprrypi.ini"
            if name == "tone_ini_source"
            else tmp_path / "runtime/wsprrypi.ini"
            if name == "tone_ini"
            else tmp_path / name
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("tone configuration" if name.startswith("tone_ini") else name)
        artifacts[name] = artifact(path)
    launcher = {
        "launcher": artifacts["rx_helper"],
        "module": artifacts["rx_helper_config"],
    }
    facts = {
        "transmitter_host": "wspr4",
        "receiver_host": "wspr5",
        "receiver_hostname": "wspr5",
        "sdr": {
            "driver": "sdrplay",
            "serial": "2404058C60",
            "label": "SDRplay Dev0 RSP1B 2404058C60",
        },
        "sdr_selector": "driver=sdrplay,serial=2404058C60",
        "artifacts": artifacts,
        "source": {"parent_revision": "1" * 40, "submodule_revision": "2" * 40},
        "transmitter_host_key_sha256": "SHA256:" + "A" * 43,
        "transmitter_source_path": str(tmp_path / "source"),
        "work_directory": str(tmp_path / "work"),
        "output_parent": str(tmp_path / "runs"),
        "rf_confirmation": {
            "path_type": "conducted",
            "antenna_connected": False,
            "termination": "50 ohm direct SDR input through attenuator",
            "attenuation_db": 20,
            "filter": "none",
            "safe_input_basis": (
                "explicit --enable-rf confirmation of the documented conducted 20 dB default path"
            ),
            "authorization_scope": "single_run",
        },
        "receiver_delegation": {
            "ssh": artifacts["ssh"],
            "known_hosts": artifacts["known_hosts"],
            "remote_exec": launcher,
            "qualification": launcher,
        },
    }
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(facts))
    configuration = write_automatic_configuration(facts_path, tmp_path / "configuration")
    plan = compose_complete_test_plan(
        "wspr4",
        "wspr5",
        facts["sdr_selector"],
        configuration=configuration,
        live=False,
    )
    assert [entry["mode"] for entry in plan["mode_plans"]] == [
        "TONE",
        "WSPR",
        "QRSS",
        "FSKCW",
        "DFCW",
    ]
    assert all(entry["plan"]["rf_path"]["attenuation_db"] == 20 for entry in plan["mode_plans"])
    assert all(
        entry["plan"]["receiver"]["clipping_threshold"] == 0.999 for entry in plan["mode_plans"][2:]
    )
    assert plan["resolved_values"]["carrier_offset_max_hz"] == 100.0
    assert plan["transmitter_ppm_resolution"]["effective_correction_ppm"] == 0.0

    adjusted = compose_complete_test_plan(
        "wspr4",
        "wspr5",
        facts["sdr_selector"],
        configuration=configuration,
        overrides=CompleteTestOverrides(carrier_offset_max_hz=250.0, transmitter_ppm_offset=-1.25),
        live=False,
    )
    assert adjusted["resolved_values"]["carrier_offset_max_hz"] == 250.0
    assert adjusted["transmitter_ppm_resolution"]["effective_correction_ppm"] == -1.25
    for entry in adjusted["mode_plans"]:
        child = entry["plan"]
        if entry["mode"] in {"TONE", "WSPR"}:
            assert child["carrier"]["offset_gate_hz"] == 250.0
            assert child["calibration"]["ppm"] == -1.25
        else:
            assert child["application_plan"]["backend_contract"]["ppm"] == -1.25

    measured = compose_complete_test_plan(
        "wspr4",
        "wspr5",
        facts["sdr_selector"],
        configuration=configuration,
        overrides=CompleteTestOverrides(
            gpio_manual_ppm=3.560,
            carrier_offset_max_hz=250.0,
        ),
        live=False,
    )
    resolution = measured["transmitter_ppm_resolution"]
    assert resolution["host_correction_ppm"] == 3.560
    assert resolution["harness_offset_ppm"] == 0
    assert resolution["effective_correction_ppm"] == 3.560
    assert resolution["contributors"][0]["source_type"] == "manual_host_ppm"
    assert "--gpio-manual-ppm" in resolution["contributors"][0]["source_location"]
    for entry in measured["mode_plans"]:
        child = entry["plan"]
        if entry["mode"] == "WSPR":
            assert child["calibration"]["ppm"] == 3.560
            continue
        arguments = (
            child["tone_server"]["arguments"]
            if entry["mode"] == "TONE"
            else child["application_plan"]["arguments"]
        )
        assert arguments.count("--gpio-manual-ppm") == 1
        assert float(arguments[arguments.index("--gpio-manual-ppm") + 1]) == 3.560
        assert arguments.count("--no-system-clock-frequency-estimate") == 1
        assert "--use-system-clock-frequency-estimate" not in arguments


def test_discovered_si5351_facts_create_si5351_plans(tmp_path: Path) -> None:
    names = (
        "ssh",
        "ssh_keygen",
        "known_hosts",
        "tx_helper",
        "tx_helper_config",
        "tx_keyed_helper_config",
        "tx_sudo",
        "tx_systemctl",
        "tx_gpio",
        "tx_si5351",
        "tx_wsprrypi",
        "tx_git",
        "rx_helper",
        "rx_helper_config",
        "rx_systemctl",
        "rx_gpio",
        "capture_helper",
        "wsprd",
        "tone_ini_source",
        "tone_ini",
    )
    artifacts = {}
    for name in names:
        path = (
            tmp_path / "source/config/wsprrypi.ini"
            if name == "tone_ini_source"
            else tmp_path / "runtime/wsprrypi.ini"
            if name == "tone_ini"
            else tmp_path / name
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("tone configuration" if name.startswith("tone_ini") else name)
        artifacts[name] = artifact(path)
    launcher = {"launcher": artifacts["rx_helper"], "module": artifacts["rx_helper_config"]}
    facts = {
        "transmitter_host": "wspr2",
        "receiver_host": "wspr5",
        "receiver_hostname": "wspr5",
        "transmitter_backend": "si5351",
        "sdr": {"driver": "sdrplay", "serial": "2404058C60", "label": "RSP1B"},
        "sdr_selector": "driver=sdrplay,serial=2404058C60",
        "artifacts": artifacts,
        "source": {"parent_revision": "1" * 40, "submodule_revision": "2" * 40},
        "transmitter_host_key_sha256": "SHA256:" + "A" * 43,
        "transmitter_source_path": str(tmp_path / "source"),
        "work_directory": str(tmp_path / "work"),
        "output_parent": str(tmp_path / "runs"),
        "rf_confirmation": {
            "path_type": "conducted",
            "antenna_connected": False,
            "termination": "50 ohm direct SDR input through attenuator",
            "attenuation_db": 20,
            "filter": "none",
            "safe_input_basis": (
                "explicit --enable-rf confirmation of the documented conducted 20 dB default path"
            ),
            "authorization_scope": "single_run",
        },
        "receiver_delegation": {
            "ssh": artifacts["ssh"],
            "known_hosts": artifacts["known_hosts"],
            "remote_exec": launcher,
            "qualification": launcher,
        },
    }
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(facts))
    configuration = write_automatic_configuration(facts_path, tmp_path / "configuration")
    plan = compose_complete_test_plan(
        "wspr2",
        "wspr5",
        facts["sdr_selector"],
        configuration=configuration,
        overrides=CompleteTestOverrides(transmitter_ppm_offset=-2.292),
        live=False,
    )
    for entry in plan["mode_plans"]:
        child = entry["plan"]
        if entry["mode"] in {"TONE", "WSPR"}:
            assert child["backend"] == "si5351"
            assert child["backend_contract"]["i2c_bus"] == 1
            assert child["backend_contract"]["i2c_address"] == "0x60"
            assert child["backend_contract"]["drive_or_power_level"] == 1
            test_profile = json.loads(Path(child["resolved_profiles"]["test"]["path"]).read_text())
            assert test_profile["transmitter"]["i2c_bus"] == 1
            assert test_profile["transmitter"]["i2c_address"] == "0x60"
            assert test_profile["transmitter"]["reference_frequency_hz"] == 27_000_000
            assert test_profile["transmitter"]["power_level"] == 1
        else:
            assert child["transmitter"]["backend"] == "si5351"
            arguments = child["application_plan"]["arguments"]
            assert arguments[arguments.index("--si5351-ppm") + 1] == "-2.292"
    tone_arguments = plan["mode_plans"][0]["plan"]["tone_server"]["arguments"]
    assert tone_arguments[tone_arguments.index("--si5351-ppm") + 1] == "-2.292"


@pytest.mark.parametrize("transmit_gpio", [4, 20])
def test_same_host_rp1_route_and_manual_ppm_are_bound(tmp_path: Path, transmit_gpio: int) -> None:
    names = (
        "ssh",
        "ssh_keygen",
        "known_hosts",
        "tx_helper",
        "tx_helper_config",
        "tx_keyed_helper_config",
        "tx_sudo",
        "tx_systemctl",
        "tx_gpio",
        "tx_si5351",
        "tx_rp1",
        "tx_wsprrypi",
        "tx_git",
        "rx_helper",
        "rx_helper_config",
        "rx_systemctl",
        "rx_gpio",
        "capture_helper",
        "wsprd",
        "tone_ini_source",
        "tone_ini",
    )
    artifacts = {}
    for name in names:
        path = tmp_path / ("source/config/wsprrypi.ini" if name == "tone_ini_source" else name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "tone configuration" if name.startswith("tone_ini") else name, encoding="utf-8"
        )
        artifacts[name] = artifact(path)
    launcher = {"launcher": artifacts["rx_helper"], "module": artifacts["rx_helper_config"]}
    facts = {
        "transmitter_host": "wspr5",
        "receiver_host": "wspr5",
        "receiver_hostname": "wspr5",
        "transmitter_backend": "rp1_gpclk",
        "transmit_gpio": transmit_gpio,
        "sdr": {"driver": "sdrplay", "serial": "2404058C60", "label": "RSP1B"},
        "sdr_selector": "driver=sdrplay,serial=2404058C60",
        "artifacts": artifacts,
        "source": {"parent_revision": "1" * 40, "submodule_revision": "2" * 40},
        "transmitter_host_key_sha256": "SHA256:" + "A" * 43,
        "transmitter_source_path": str(tmp_path / "source"),
        "work_directory": str(tmp_path / "work"),
        "output_parent": str(tmp_path / "runs"),
        "rf_confirmation": {
            "path_type": "conducted",
            "antenna_connected": False,
            "termination": "50 ohm direct SDR input through attenuator",
            "attenuation_db": 20,
            "filter": "none",
            "safe_input_basis": (
                "explicit --enable-rf confirmation of the documented conducted 20 dB default path"
            ),
            "authorization_scope": "single_run",
        },
        "receiver_delegation": {
            "ssh": artifacts["ssh"],
            "known_hosts": artifacts["known_hosts"],
            "remote_exec": launcher,
            "qualification": launcher,
        },
    }
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    configuration = write_automatic_configuration(facts_path, tmp_path / "configuration")
    plan = compose_complete_test_plan(
        "wspr5",
        "wspr5",
        facts["sdr_selector"],
        configuration=configuration,
        overrides=CompleteTestOverrides(gpio_manual_ppm=3.560),
        live=False,
    )
    assert plan["topology"] == "same_host_roles"
    assert plan["transport"] == "local_role_channels"
    assert plan["transmitter_ppm_resolution"]["host_correction_ppm"] == 3.560
    for entry in plan["mode_plans"]:
        child = entry["plan"]
        contract = (
            child["backend_contract"]
            if entry["mode"] in {"TONE", "WSPR"}
            else child["application_plan"]["backend_contract"]
        )
        assert contract["rp1_route"] == f"gpio{transmit_gpio}"
        assert contract["gpio_pin"] == transmit_gpio
        assert contract["compatibility_id"].endswith(
            f"gpio{transmit_gpio}-6.18.34-development-candidate-r3"
        )
        if entry["mode"] != "WSPR":
            arguments = (
                child["tone_server"]["arguments"]
                if entry["mode"] == "TONE"
                else child["application_plan"]["arguments"]
            )
            backend_index = arguments.index("--backend")
            assert arguments[backend_index + 1] == "rp1-gpclk"
            assert "gpio" not in arguments[backend_index : backend_index + 2]
            assert arguments.count("--transmit-gpio") == 1
            assert arguments[arguments.index("--transmit-gpio") + 1] == str(transmit_gpio)
            assert arguments.count("--gpio-manual-ppm") == 1
            assert float(arguments[arguments.index("--gpio-manual-ppm") + 1]) == 3.560
