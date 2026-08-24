import json
from pathlib import Path

from wsprrypi_qualification.automatic_configuration import write_automatic_configuration
from wsprrypi_qualification.complete_test import compose_complete_test_plan
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
        "tx_wsprrypi",
        "tx_git",
        "rx_helper",
        "rx_helper_config",
        "rx_systemctl",
        "rx_gpio",
        "capture_helper",
        "wsprd",
        "tone_ini",
    )
    artifacts = {}
    for name in names:
        path = tmp_path / name
        path.write_text(name)
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
        "transmitter_source_path": "/tmp/source",
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
