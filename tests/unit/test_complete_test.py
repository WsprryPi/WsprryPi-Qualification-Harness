import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

import wsprrypi_qualification.cli as cli_module
from tests.unit.test_keyed_session_contracts import plan as keyed_plan
from tests.unit.test_real_session import tone_plan_document
from wsprrypi_qualification.cli import main
from wsprrypi_qualification.complete_test import (
    DEFAULTS,
    MODE_ORDER,
    CompleteTestError,
    CompleteTestOverrides,
    _fixed_gpio_ppm_arguments,
    complete_test_sha256,
    compose_complete_test_plan,
    configuration_path,
    delegate_complete_test,
    rehearse_complete_test,
    resolve_local_sdr,
    run_complete_test,
    validate_complete_test_bundle,
)
from wsprrypi_qualification.manifests import write_manifest
from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.progress import ProgressReporter

SDR = "driver=sdrplay,serial=2404058C60"
DISCOVERED_SDR = {
    "driver": "sdrplay",
    "label": "SDRplay Dev0 RSP1B 2404058C60",
    "serial": "2404058C60",
}
REMOTE_EXEC_IDENTITY = {
    "launcher": {
        "path": "/usr/local/bin/wspq-remote-exec",
        "size_bytes": 10,
        "sha256": "a" * 64,
    },
    "module": {"path": "/opt/remote_exec.py", "size_bytes": 12, "sha256": "b" * 64},
}
QUALIFICATION_IDENTITY = {
    "launcher": {
        "path": "/usr/local/bin/wsprrypi-qualification",
        "size_bytes": 11,
        "sha256": "c" * 64,
    },
    "module": {"path": "/opt/cli.py", "size_bytes": 13, "sha256": "d" * 64},
}


def _write(path: Path, document: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _configuration(tmp_path: Path, *, topology: str = "split_host_ssh") -> Path:
    known_hosts = tmp_path / "known_hosts"
    ssh_executable = tmp_path / "ssh-fixture"
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    if not known_hosts.exists():
        known_hosts.write_text("receiver key", encoding="utf-8")
    if not ssh_executable.exists():
        ssh_executable.write_text("portable ssh fixture", encoding="utf-8")
    real = tone_plan_document(execution_mode="live")
    real["backend"] = "gpio"
    real["output"] = "GPIO4"
    real["backend_contract"] = {
        "backend": "gpio",
        "output": "GPIO4",
        "gpio_pin": 4,
        "drive_or_power_level": 2,
        "quiescence_provider_sha256": "3" * 64,
    }
    keyed = keyed_plan("QRSS")
    keyed["transmitter"]["host"] = "wspr4.local"
    keyed["receiver"]["host"] = "wspr5.local"
    real["receiver"]["host"] = "wspr5.local"
    real["receiver"]["serial"] = "2404058C60"
    real["rf_path"] = {
        "path_type": "conducted",
        "antenna_connected": False,
        "termination": "50 ohm direct SDR input through attenuator",
        "attenuation_db": 20,
        "filter": "none",
        "safe_input_basis": "bounded conducted fixture",
        "authorization_scope": "single_run",
    }
    _write(tmp_path / "templates/real.json", real)
    _write(tmp_path / "templates/keyed.json", keyed)
    return _write(
        tmp_path / "wspr4.local.json",
        {
            "schema_version": 1,
            "evidence_type": "complete_test_configuration",
            "campaign_id": "complete-five-mode",
            "transmitter_host": "wspr4.local",
            "receiver_host": "wspr5.local",
            "sdr_selector": SDR,
            "receiver_delegation": {
                "ssh": artifact(ssh_executable),
                "known_hosts": artifact(known_hosts),
                "remote_exec": REMOTE_EXEC_IDENTITY,
                "qualification": QUALIFICATION_IDENTITY,
            },
            "topology": topology,
            "production_templates": {
                "real_session": "templates/real.json",
                "keyed_session": "templates/keyed.json",
            },
            "ssh_executable": str(ssh_executable),
            "work_directory": str(tmp_path / "work"),
            "output_parent": str(tmp_path / "runs"),
            "campaign_deadline_s": 1200,
        },
    )


def test_exact_defaults_order_derivations_and_no_typed_digest(tmp_path: Path, capsys) -> None:
    config = _configuration(tmp_path)
    plan = compose_complete_test_plan(
        "wspr4.local", "wspr5.local", SDR, configuration=config, live=False
    )
    assert plan["defaults"] == DEFAULTS
    assert plan["resolved_values"] == DEFAULTS
    assert [entry["mode"] for entry in plan["mode_plans"]] == list(MODE_ORDER)
    wspr = plan["mode_plans"][1]["plan"]
    assert wspr["frequency_hz"] == 14_097_100
    assert wspr["receiver"]["center_frequency_hz"] == 14_072_100
    assert wspr["frame_count"] == 3
    assert plan["derived_frequencies"]["wspr_dial_frequency_hz"] == 14_095_600
    assert wspr["backend"] == "gpio"
    assert wspr["output"] == "GPIO4"
    assert wspr["backend_contract"]["gpio_pin"] == 4
    assert wspr["backend_contract"]["drive_or_power_level"] == 2
    tone = plan["mode_plans"][0]["plan"]
    assert "--no-web" not in tone["tone_server"]["arguments"]
    tone_arguments = tone["tone_server"]["arguments"]
    assert tone_arguments.count("--no-system-clock-frequency-estimate") == 1
    assert tone_arguments.count("--gpio-manual-ppm") == 1
    assert tone_arguments[tone_arguments.index("--gpio-manual-ppm") + 1] == "2.3536"
    assert Path(tone["cw_contract"]["plan"]["path"]).is_file()
    assert Path(tone["resolved_profiles"]["bench"]["path"]).is_file()
    keyed = {entry["mode"]: entry["plan"] for entry in plan["mode_plans"][2:]}
    assert plan["receiver_tuning"] == {
        "policy": "zero_if_offset_target_window_v1",
        "requested_frequency_hz": 14_097_100.0,
        "center_frequency_hz": 14_072_100.0,
        "tuning_offset_hz": 25_000.0,
        "dc_exclusion_hz": 1_000.0,
        "target_search_half_width_hz": 500.0,
        "usable_half_span_hz": 100_000.0,
    }
    assert all(
        entry["plan"]["receiver"]["center_frequency_hz"] == 14_072_100
        for entry in plan["mode_plans"]
    )
    assert keyed["QRSS"]["application_plan"]["protocol_contract"] == {
        "message": "ETE",
        "dot_seconds": 0.7,
        "primary_frequency_hz": 14_097_100.0,
        "secondary_frequency_hz": None,
    }
    assert (
        keyed["FSKCW"]["application_plan"]["protocol_contract"]["secondary_frequency_hz"]
        == 14_097_095.0
    )
    assert (
        keyed["DFCW"]["application_plan"]["protocol_contract"]["secondary_frequency_hz"]
        == 14_097_095.0
    )
    assert keyed["QRSS"]["transmitter"]["drive"] == 0
    assert Path(keyed["QRSS"]["reference"]["plan"]["path"]).is_file()
    assert keyed["QRSS"]["application_plan"]["backend_contract"]["gpio_pin"] == 4
    for child in keyed.values():
        arguments = child["application_plan"]["arguments"]
        assert arguments.count("--no-system-clock-frequency-estimate") == 1
        assert arguments.count("--gpio-manual-ppm") == 1
        assert (
            float(arguments[arguments.index("--gpio-manual-ppm") + 1])
            == child["application_plan"]["backend_contract"]["ppm"]
        )
    with pytest.raises(SystemExit) as help_exit:
        main(["complete-test", "--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "confirm-plan-sha256" not in help_text
    assert "--enable-rf" in help_text
    assert "--operator" not in help_text
    assert "TRANSMITTER_HOST RECEIVER_HOST" in help_text


@pytest.mark.parametrize(
    ("arguments", "match"),
    (
        (["wsprrypi", "-n"], "contradicts"),
        (["wsprrypi", "--use-system-clock-frequency-estimate"], "contradicts"),
        (["wsprrypi", "--use-system-clock-frequency-estimate=true"], "contradicts"),
        (["wsprrypi", "-p", "1.25"], "contradicts"),
        (["wsprrypi", "--ppm", "1.25"], "contradicts"),
        (["wsprrypi", "--ppm=1.25"], "contradicts"),
        (["wsprrypi", "--gpio-manual-ppm=1.25"], "contradicts"),
        (["wsprrypi", "--gpio-manual-ppm", "1", "--gpio-manual-ppm", "1"], "duplicates"),
        (["wsprrypi", "--gpio-manual-ppm"], "malformed"),
        (["wsprrypi", "--gpio-manual-ppm", "not-a-number"], "malformed"),
        (["wsprrypi", "--gpio-manual-ppm", "2"], "differs"),
    ),
)
def test_gpio_manual_ppm_containment_rejects_ambiguous_launches(
    arguments: list[str], match: str
) -> None:
    with pytest.raises(CompleteTestError, match=match):
        _fixed_gpio_ppm_arguments(arguments, 1.25)


def test_gpio_manual_ppm_containment_overrides_configuration_defaults() -> None:
    arguments = _fixed_gpio_ppm_arguments(["wsprrypi", "-i", "estimate-enabled.ini"], -1.25)
    assert arguments == [
        "wsprrypi",
        "--no-system-clock-frequency-estimate",
        "-i",
        "estimate-enabled.ini",
        "--gpio-manual-ppm",
        "-1.25",
    ]


def test_default_path_needs_no_cli_configuration_argument(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WSPQ_CONFIG_DIR", str(tmp_path))
    assert configuration_path("wspr5", "wspr6") == (tmp_path / "complete-test/wspr5--wspr6.json")


def test_exact_sdr_selector_resolves_unique_driver_and_serial(monkeypatch) -> None:
    monkeypatch.setattr(
        "wsprrypi_qualification.complete_test.discover_executable",
        lambda name: Path("/usr/bin/true"),
    )
    monkeypatch.setattr(
        "wsprrypi_qualification.complete_test.LocalCommandTransport.execute",
        lambda self, plan: SimpleNamespace(
            return_code=0,
            timed_out=False,
            stderr="",
            stdout=(
                "Found device 0\n"
                "  driver = sdrplay\n"
                "  label = SDRplay Dev0 RSP1B 2404058C60\n"
                "  serial = 2404058C60\n"
            ),
        ),
    )
    assert resolve_local_sdr(SDR) == {
        "driver": "sdrplay",
        "label": "SDRplay Dev0 RSP1B 2404058C60",
        "serial": "2404058C60",
    }
    with pytest.raises(CompleteTestError, match="stable serial"):
        resolve_local_sdr("driver=sdrplay")


def test_remote_receiver_returns_classified_nonpassing_result(tmp_path: Path, monkeypatch) -> None:
    configuration = _configuration(tmp_path)
    receipt = {
        "receiver_host": "wspr5.local",
        "ssh": artifact(tmp_path / "ssh-fixture"),
        "known_hosts": artifact(tmp_path / "known_hosts"),
        "remote_exec": REMOTE_EXEC_IDENTITY,
        "qualification": QUALIFICATION_IDENTITY,
    }
    expected = {
        "result": {
            "final_status": "unqualified_carrier",
            "delegation_receipt": receipt,
        }
    }
    responses = iter(
        (
            SimpleNamespace(
                return_code=0,
                timed_out=False,
                disconnected=False,
                stderr="",
                stdout=json.dumps(REMOTE_EXEC_IDENTITY),
            ),
            SimpleNamespace(
                return_code=0,
                timed_out=False,
                disconnected=False,
                stderr="",
                stdout=json.dumps(QUALIFICATION_IDENTITY),
            ),
            SimpleNamespace(
                return_code=4,
                timed_out=False,
                disconnected=False,
                stderr="",
                stdout=json.dumps({**expected, "bundle": "/runs/campaign"}),
            ),
            SimpleNamespace(
                return_code=0,
                timed_out=False,
                disconnected=False,
                stderr="",
                stdout=json.dumps(expected["result"]),
            ),
            SimpleNamespace(
                return_code=0,
                timed_out=False,
                disconnected=False,
                stderr="",
                stdout=json.dumps(REMOTE_EXEC_IDENTITY),
            ),
            SimpleNamespace(
                return_code=0,
                timed_out=False,
                disconnected=False,
                stderr="",
                stdout=json.dumps(QUALIFICATION_IDENTITY),
            ),
        )
    )
    monkeypatch.setattr(
        "wsprrypi_qualification.complete_test.LocalCommandTransport.execute",
        lambda self, plan: next(responses),
    )
    outcome = delegate_complete_test(
        "wspr4.local", "wspr5.local", SDR, ["--enable-rf"], configuration=configuration
    )
    assert outcome["result"] == expected["result"]
    assert outcome["bundle"] == "/runs/campaign"
    assert outcome["result"]["delegation_receipt"] == receipt


def test_every_override_is_bound_and_changes_digest(tmp_path: Path) -> None:
    config = _configuration(tmp_path)
    baseline = compose_complete_test_plan(
        "wspr4.local", "wspr5.local", SDR, configuration=config, live=False
    )
    override = CompleteTestOverrides(
        band="30m",
        frequency_hz=10_140_100,
        callsign="Q1QQQ",
        grid="AA00",
        power_dbm=3,
        message="TEST",
        qrss_dot_seconds=1.1,
        fskcw_dot_seconds=1.2,
        dfcw_dot_seconds=1.3,
        fskcw_separation_hz=6.0,
        dfcw_separation_hz=7.0,
    )
    changed = compose_complete_test_plan(
        "wspr4.local", "wspr5.local", SDR, configuration=config, overrides=override, live=False
    )
    assert changed["resolved_values"] == override.validated()
    assert complete_test_sha256(changed) != complete_test_sha256(baseline)
    keyed = {entry["mode"]: entry["plan"] for entry in changed["mode_plans"][2:]}
    assert (
        keyed["FSKCW"]["application_plan"]["protocol_contract"]["secondary_frequency_hz"]
        == 10_140_094.0
    )
    assert (
        keyed["DFCW"]["application_plan"]["protocol_contract"]["secondary_frequency_hz"]
        == 10_140_093.0
    )


def test_retained_defaults_values_and_sdr_binding_are_revalidated(tmp_path: Path) -> None:
    hardware_free = compose_complete_test_plan(
        "wspr4.local", "wspr5.local", SDR, configuration=_configuration(tmp_path), live=False
    )
    changed_defaults = deepcopy(hardware_free)
    changed_defaults["defaults"]["band"] = "30m"
    with pytest.raises(CompleteTestError, match="canonical defaults"):
        complete_test_sha256(changed_defaults)

    invalid_values = deepcopy(hardware_free)
    invalid_values["resolved_values"]["power_dbm"] = 1
    with pytest.raises(CompleteTestError, match="resolved values are invalid"):
        complete_test_sha256(invalid_values)

    live = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=_configuration(tmp_path / "live"),
        discovered_sdr=DISCOVERED_SDR,
        live=True,
    )
    changed_receiver = deepcopy(live)
    changed_receiver["mode_plans"][2]["plan"]["receiver"]["device"] = f"{SDR}-other"
    with pytest.raises(CompleteTestError, match="retained SDR identity"):
        complete_test_sha256(changed_receiver)


def test_hardware_free_rehearsal_publishes_five_mode_immutable_bundle(tmp_path: Path) -> None:
    plan = compose_complete_test_plan(
        "wspr4.local", "wspr5.local", SDR, configuration=_configuration(tmp_path), live=False
    )
    outcome = rehearse_complete_test(plan, tmp_path / "output with spaces")
    assert [entry["mode"] for entry in outcome["result"]["modes"]] == list(MODE_ORDER)
    assert all(entry["final_status"] == "inconclusive" for entry in outcome["result"]["modes"])
    assert outcome["result"]["qualification_claim"] is False
    with pytest.raises(CompleteTestError, match="not new"):
        rehearse_complete_test(plan, tmp_path / "output with spaces")
    bundle = Path(outcome["bundle"])
    tampered = json.loads((bundle / "result.json").read_text(encoding="utf-8"))
    tampered["final_status"] = "qualified"
    tampered["qualification_claim"] = True
    _write(bundle / "result.json", tampered)
    write_manifest(bundle)
    with pytest.raises(CompleteTestError, match="broader than evidence"):
        validate_complete_test_bundle(bundle)


def test_unsupported_topology_and_invalid_input_fail_before_dispatch(tmp_path: Path) -> None:
    with pytest.raises(CompleteTestError, match="unsupported_topology"):
        compose_complete_test_plan(
            "wspr4.local",
            "wspr5.local",
            SDR,
            configuration=_configuration(tmp_path, topology="same_host_local"),
            live=False,
        )
    with pytest.raises(CompleteTestError, match="positive"):
        CompleteTestOverrides(fskcw_separation_hz=0).validated()

    config = _configuration(tmp_path / "rp1")
    keyed_path = tmp_path / "rp1/templates/keyed.json"
    keyed = json.loads(keyed_path.read_text(encoding="utf-8"))
    keyed["application_plan"]["backend"] = "rp1_gpclk"
    _write(keyed_path, keyed)
    with pytest.raises(CompleteTestError, match="rp1_gpclk"):
        compose_complete_test_plan(
            "wspr4.local", "wspr5.local", SDR, configuration=config, live=False
        )


def test_live_order_early_stop_and_not_attempted(tmp_path: Path) -> None:
    plan = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=_configuration(tmp_path),
        discovered_sdr=DISCOVERED_SDR,
        live=True,
    )
    calls = []

    def dispatch(wrapper, output_parent, **kwargs):
        mode = wrapper["mode"]
        calls.append(mode)
        bundle = output_parent / f"child-{mode.lower()}"
        bundle.mkdir(parents=True)
        status = "fixture_blocked" if mode == "WSPR" else "inconclusive"
        document = {
            "schema_version": 1,
            "run_id": f"20260823T200000Z-{mode.lower()}",
            "status": status,
            "started_utc": "2026-08-23T20:00:00Z",
            "completed_utc": "2026-08-23T20:00:01Z",
            "preflight_passed": True,
            "carrier_gate": "blocked" if status == "fixture_blocked" else "passed",
            "decode_gate": "not_run" if mode == "TONE" or status == "fixture_blocked" else "passed",
            "cleanup_outcome": "verified",
            "failure_causes": ["receiver_unavailable"] if status == "fixture_blocked" else [],
            "artifacts": [],
        }
        _write(bundle / "result.json", document)
        session = {"run_id": document["run_id"], "final_status": status}
        _write(bundle / "session.json", session)
        write_manifest(bundle)
        return {"authoritative_bundle": str(bundle), "underlying_result": session}

    progress_path = tmp_path / "campaign progress.jsonl"
    with ProgressReporter(progress_path) as reporter:
        outcome = run_complete_test(
            plan,
            tmp_path / "runs",
            ssh_executable=tmp_path / "ssh-fixture",
            work_directory=tmp_path / "work",
            dispatcher=dispatch,
            progress=reporter.emit,
        )
    assert calls == ["TONE", "WSPR"]
    assert [entry["state"] for entry in outcome["result"]["modes"]] == [
        "attempted",
        "attempted",
        "not_attempted",
        "not_attempted",
        "not_attempted",
    ]
    assert outcome["result"]["final_status"] == "fixture_blocked"
    assert outcome["result"]["modes"][0]["final_status"] == "qualified"
    progress = [json.loads(line) for line in progress_path.read_text().splitlines()]
    assert [(item["mode"], item["status"]) for item in progress if item["stage"] == "mode"] == [
        ("TONE", "started"),
        ("TONE", "completed"),
        ("WSPR", "started"),
        ("WSPR", "completed"),
        ("QRSS", "skipped"),
        ("FSKCW", "skipped"),
        ("DFCW", "skipped"),
    ]
    assert progress[-1]["stage"] == "campaign" and progress[-1]["status"] == "terminal"
    (tmp_path / "runs/child-tone/extra.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(CompleteTestError, match="bundle evidence changed"):
        validate_complete_test_bundle(Path(outcome["bundle"]))


def test_tampering_reordering_and_path_escape_are_rejected(tmp_path: Path) -> None:
    plan = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=_configuration(tmp_path),
        discovered_sdr=DISCOVERED_SDR,
        live=True,
    )
    reordered = deepcopy(plan)
    reordered["mode_plans"][0], reordered["mode_plans"][1] = (
        reordered["mode_plans"][1],
        reordered["mode_plans"][0],
    )
    with pytest.raises(CompleteTestError, match="missing, duplicated, or reordered"):
        complete_test_sha256(reordered)

    def escape(wrapper, output_parent, **kwargs):
        return {
            "authoritative_bundle": str(tmp_path.parent / "escape"),
            "underlying_result": {"final_status": "qualified"},
        }

    with pytest.raises(CompleteTestError, match="escapes"):
        run_complete_test(
            plan,
            tmp_path / "runs",
            ssh_executable=tmp_path / "ssh-fixture",
            work_directory=tmp_path / "work",
            dispatcher=escape,
        )


def test_bound_configuration_and_template_tampering_are_rejected(tmp_path: Path) -> None:
    config = _configuration(tmp_path)
    plan = compose_complete_test_plan(
        "wspr4.local", "wspr5.local", SDR, configuration=config, live=False
    )
    config.write_text(config.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(CompleteTestError, match="configuration changed"):
        complete_test_sha256(plan)

    config = _configuration(tmp_path / "second")
    plan = compose_complete_test_plan(
        "wspr4.local", "wspr5.local", SDR, configuration=config, live=False
    )
    template = tmp_path / "second/templates/keyed.json"
    template.write_text(template.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(CompleteTestError, match="source template changed"):
        complete_test_sha256(plan)


def test_symbolic_link_configuration_is_rejected(tmp_path: Path) -> None:
    config = _configuration(tmp_path)
    linked = tmp_path / "linked.json"
    linked.symlink_to(config)
    with pytest.raises(CompleteTestError, match="unsafe"):
        compose_complete_test_plan(
            "wspr4.local", "wspr5.local", SDR, configuration=linked, live=False
        )


def test_coordinator_exception_publishes_partial_authenticated_campaign(tmp_path: Path) -> None:
    plan = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=_configuration(tmp_path),
        discovered_sdr=DISCOVERED_SDR,
        live=True,
    )

    def blocked(*args, **kwargs):
        raise RuntimeError("capability unavailable")

    outcome = run_complete_test(
        plan,
        tmp_path / "runs",
        ssh_executable=tmp_path / "ssh-fixture",
        work_directory=tmp_path / "work",
        dispatcher=blocked,
    )
    assert outcome["result"]["final_status"] == "cleanup_failed"
    assert outcome["result"]["modes"][0]["state"] == "attempted_unverified"
    assert outcome["result"]["campaign_cleanup"] == {
        "subordinate_cleanup_authoritative": False,
        "restoration_authoritative": False,
        "all_attempted_modes_authenticated": False,
    }
    assert all(entry["state"] == "not_attempted" for entry in outcome["result"]["modes"][1:])
    assert "capability unavailable" in outcome["result"]["modes"][0]["stopping_reason"]


def test_complete_validation_precedes_dispatch_and_rehearsal_has_no_contact(
    tmp_path: Path,
) -> None:
    config = _configuration(tmp_path)
    plan = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=config,
        discovered_sdr=DISCOVERED_SDR,
        live=True,
    )
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("dispatch")
        raise AssertionError("production dispatch must remain unreachable")

    config.write_text(config.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(CompleteTestError, match="configuration changed"):
        run_complete_test(
            plan,
            tmp_path / "runs",
            ssh_executable=tmp_path / "ssh-fixture",
            work_directory=tmp_path / "work",
            dispatcher=forbidden,
        )
    assert calls == []


def test_keyboard_interrupt_is_authenticated_as_campaign_abort(tmp_path: Path) -> None:
    plan = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=_configuration(tmp_path),
        discovered_sdr=DISCOVERED_SDR,
        live=True,
    )

    def cancelled(*args, **kwargs):
        raise KeyboardInterrupt

    outcome = run_complete_test(
        plan,
        tmp_path / "runs",
        ssh_executable=tmp_path / "ssh-fixture",
        work_directory=tmp_path / "work",
        dispatcher=cancelled,
    )
    assert outcome["result"]["final_status"] == "aborted"


@pytest.mark.parametrize(
    ("status", "exit_code"),
    (
        ("qualified", 0),
        ("fixture_blocked", 3),
        ("unqualified_decode", 4),
        ("aborted", 5),
        ("cleanup_failed", 6),
    ),
)
def test_live_cli_classified_exit_codes(
    tmp_path: Path, monkeypatch, capsys, status: str, exit_code: int
) -> None:
    config = _configuration(tmp_path)

    def outcome(*args, **kwargs):
        return {
            "bundle": str(tmp_path / "unused"),
            "result": {
                "final_status": status,
                "transmitter_host": "wspr4.local",
                "receiver_host": "wspr5.local",
                "sdr_selector": SDR,
            },
        }

    monkeypatch.setattr(cli_module, "run_complete_test", outcome)
    monkeypatch.setattr(cli_module, "receiver_is_local", lambda host: True)
    monkeypatch.setattr(cli_module, "resolve_local_sdr", lambda selector: DISCOVERED_SDR)
    assert (
        main(
            [
                "complete-test",
                "wspr4.local",
                "wspr5.local",
                "--sdr",
                SDR,
                "--enable-rf",
                "--configuration",
                str(config),
            ]
        )
        == exit_code
    )
    rendered = json.loads(capsys.readouterr().out)
    assert set(rendered) == {"status", "transmitter", "receiver", "sdr", "bundle"}


def test_receiver_delegation_returns_full_machine_result(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = _configuration(tmp_path)
    expected = {
        "bundle": str(tmp_path / "remote-run"),
        "result": {
            "final_status": "qualified",
            "transmitter_host": "wspr4.local",
            "receiver_host": "wspr5.local",
            "sdr_selector": SDR,
        },
    }
    monkeypatch.setattr(cli_module, "receiver_is_local", lambda host: True)
    monkeypatch.setattr(cli_module, "resolve_local_sdr", lambda selector: DISCOVERED_SDR)
    monkeypatch.setattr(cli_module, "run_complete_test", lambda *args, **kwargs: expected)
    assert (
        main(
            [
                "complete-test",
                "wspr4.local",
                "wspr5.local",
                "--sdr",
                SDR,
                "--enable-rf",
                "--receiver-local",
                "--delegated-output",
                "--configuration",
                str(config),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == expected
