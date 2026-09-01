import json
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import wsprrypi_qualification.cli as cli_module
import wsprrypi_qualification.complete_test as complete_test_module
from tests.unit.test_keyed_session_contracts import plan as keyed_plan
from tests.unit.test_real_session import tone_plan_document
from wsprrypi_qualification.cli import main
from wsprrypi_qualification.complete_test import (
    DEFAULTS,
    MODE_ORDER,
    CompleteTestError,
    CompleteTestOverrides,
    _fixed_gpio_ppm_arguments,
    _validate_campaign_input,
    complete_test_sha256,
    compose_complete_test_plan,
    configuration_path,
    delegate_complete_test,
    normalize_modes,
    rehearse_complete_test,
    resolve_local_sdr,
    run_complete_test,
    validate_complete_test_bundle,
    validate_complete_test_plan,
)
from wsprrypi_qualification.cw_reference import validate_keyed_capture_margin
from wsprrypi_qualification.manifests import write_manifest
from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.progress import ProgressReporter
from wsprrypi_qualification.progress_viewer import tracking_command
from wsprrypi_qualification.real_session import required_tone_overall_deadline

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


def _configuration(
    tmp_path: Path,
    *,
    topology: str = "split_host_ssh",
) -> Path:
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
    assert tone["deadlines"]["overall_s"] == required_tone_overall_deadline(tone)
    assert tone["deadlines"]["overall_s"] != 60
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
        "target_search_half_width_hz": 1_000.0,
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
        reference = json.loads(Path(child["reference"]["plan"]["path"]).read_text())
        validate_keyed_capture_margin(reference)
        capture = reference["capture_contract"]
        assert child["deadlines"]["transaction_s"] > (
            capture["sample_count"] / capture["sample_rate_hz"]
        )
        assert child["deadlines"]["overall_s"] >= (
            3 * child["deadlines"]["transaction_s"] + child["deadlines"]["cleanup_s"]
        )
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
    ("composed_at", "expected_deadline"),
    (
        (datetime(2026, 8, 24, 21, 0, 20, tzinfo=UTC), 1092),
        (datetime(2026, 8, 24, 21, 1, 59, tzinfo=UTC), 993),
        (datetime(2026, 8, 24, 21, 2, 0, tzinfo=UTC), 1112),
    ),
)
def test_wspr_deadline_tracks_final_slot_boundary(
    tmp_path: Path, composed_at: datetime, expected_deadline: int
) -> None:
    plan = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=_configuration(tmp_path),
        live=False,
        now=composed_at,
    )

    wspr = next(entry["plan"] for entry in plan["mode_plans"] if entry["mode"] == "WSPR")
    assert wspr["deadlines"]["overall_s"] == expected_deadline


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
        "-i",
        "estimate-enabled.ini",
        "--no-system-clock-frequency-estimate",
        "--gpio-manual-ppm",
        "-1.25",
    ]


def test_default_path_needs_no_cli_configuration_argument(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WSPQ_CONFIG_DIR", str(tmp_path))
    assert configuration_path("wspr5", "wspr6") == (tmp_path / "complete-test/wspr5--wspr6.json")


def test_complete_test_announces_runnable_progress_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    progress_path = tmp_path / "Application Support/progress.jsonl"

    assert (
        main(
            [
                "complete-test",
                "wspr4.local",
                "wspr5.local",
                "--sdr",
                SDR,
                "--progress-log",
                str(progress_path),
            ]
        )
        == 2
    )

    stderr = capsys.readouterr().err
    assert f"Progress log: {progress_path.resolve()}" in stderr
    assert f"Track progress: {tracking_command(progress_path)}" in stderr


def test_exact_sdr_selector_resolves_unique_driver_and_serial(monkeypatch) -> None:
    monkeypatch.setattr(
        "wsprrypi_qualification.complete_test.discover_executable",
        lambda name: Path("/usr/bin/true"),
    )
    monkeypatch.setattr(
        "wsprrypi_qualification.complete_test._run_to_completion",
        lambda executable, arguments: SimpleNamespace(
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
    monkeypatch.setattr(
        "wsprrypi_qualification.complete_test._run_to_completion",
        lambda executable, arguments: next(responses),
    )
    outcome = delegate_complete_test(
        "wspr4.local", "wspr5.local", SDR, ["--enable-rf"], configuration=configuration
    )
    assert outcome["result"] == expected["result"]
    assert outcome["bundle"] == "/runs/campaign"
    assert outcome["result"]["delegation_receipt"] == receipt


def test_every_override_is_bound_and_changes_digest(tmp_path: Path) -> None:
    config = _configuration(tmp_path)
    composed_at = datetime(2026, 8, 24, 21, 0, 20, tzinfo=UTC)
    baseline = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=config,
        live=False,
        now=composed_at,
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
        carrier_offset_max_hz=1_000.0,
        carrier_best_20hz_share_min=0.1,
    )
    changed = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=config,
        overrides=override,
        live=False,
        now=composed_at,
    )
    assert changed["resolved_values"] == override.validated()
    assert complete_test_sha256(changed) != complete_test_sha256(baseline)
    keyed = {entry["mode"]: entry["plan"] for entry in changed["mode_plans"][2:]}
    for child in keyed.values():
        reference = json.loads(Path(child["reference"]["plan"]["path"]).read_text())
        assert reference["band"] == "30m"
    assert all(
        entry["plan"]["carrier"]["best_20hz_share_min"] == 0.1
        for entry in changed["mode_plans"][:2]
    )
    assert (
        keyed["FSKCW"]["application_plan"]["protocol_contract"]["secondary_frequency_hz"]
        == 10_140_094.0
    )
    assert (
        keyed["DFCW"]["application_plan"]["protocol_contract"]["secondary_frequency_hz"]
        == 10_140_093.0
    )
    # Re-hashing a mislabeled reference must not make it acceptable.
    reference_path = Path(keyed["QRSS"]["reference"]["plan"]["path"])
    reference = json.loads(reference_path.read_text())
    reference["band"] = "20m"
    reference_path.write_text(json.dumps(reference))
    keyed["QRSS"]["reference"]["plan"] = artifact(reference_path)
    with pytest.raises(CompleteTestError, match="reference band contradicts"):
        validate_complete_test_plan(changed)


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


def test_frequency_contract_propagates_offset_window_and_explicit_ppm(tmp_path: Path) -> None:
    plan = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=_configuration(tmp_path),
        overrides=CompleteTestOverrides(
            requested_transmit_frequency_offset_hz=1_100,
            frequency_acquisition_half_width_hz=1_000.0,
            gpio_manual_ppm=3.924,
        ),
        live=False,
    )
    derived = plan["derived_frequencies"]
    assert derived == {
        "nominal_frequency_hz": 14_097_100.0,
        "requested_transmit_frequency_offset_hz": 1_100.0,
        "effective_transmit_frequency_hz": 14_098_200.0,
        "wspr_dial_frequency_hz": 14_096_700.0,
        "wspr_audio_offset_hz": 1_500,
        "fskcw_secondary_frequency_hz": 14_098_195.0,
        "dfcw_secondary_frequency_hz": 14_098_195.0,
    }
    assert plan["transmitter_ppm_resolution"]["effective_correction_ppm"] == 3.924
    assert plan["receiver_tuning"]["target_search_half_width_hz"] == 1_000.0
    for entry in plan["mode_plans"]:
        child = entry["plan"]
        assert child["frequency_contract"] == {
            "nominal_frequency_hz": 14_097_100.0,
            "requested_transmit_frequency_offset_hz": 1_100.0,
            "effective_transmit_frequency_hz": 14_098_200.0,
            "application": "exactly_once_before_child_plan_composition",
        }
        if entry["mode"] in {"TONE", "WSPR"}:
            assert child["frequency_hz"] == 14_098_200.0
            assert child["calibration"]["ppm"] == 3.924
        else:
            assert child["frequency_acquisition_half_width_hz"] == 1_000.0
            assert (
                child["application_plan"]["protocol_contract"]["primary_frequency_hz"]
                == 14_098_200.0
            )
            reference = json.loads(Path(child["reference"]["plan"]["path"]).read_text())
            assert reference["thresholds"]["frequency_acquisition_half_width_hz"] == 1_000.0
            assert reference["frequency_contract"] == child["frequency_contract"]

    tampered = deepcopy(plan)
    tampered["mode_plans"][2]["plan"]["frequency_acquisition_half_width_hz"] = 500.0
    with pytest.raises(CompleteTestError):
        complete_test_sha256(tampered)


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


def test_campaign_inputs_survive_runtime_work_cleanup_and_remain_separate(
    tmp_path: Path,
) -> None:
    plan = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=_configuration(tmp_path),
        live=False,
        now=datetime(2026, 8, 24, 21, 0, 20, tzinfo=UTC),
    )
    input_store = Path(plan["input_store"]["directory"])
    work_directory = Path(plan["execution_paths"]["work_directory"])
    output_parent = Path(plan["execution_paths"]["output_parent"])

    assert input_store.parent == output_parent / "complete-test-inputs"
    assert not input_store.is_relative_to(work_directory)
    assert plan["input_store"] == {
        "directory": str(input_store),
        "ownership": "campaign",
        "retention": "retain_while_campaign_or_subordinate_result_exists",
        "cleanup": "manual_only",
    }

    shutil.rmtree(work_directory, ignore_errors=True)
    validate_complete_test_plan(plan)
    outcome = rehearse_complete_test(plan, output_parent)
    assert Path(outcome["bundle"]).is_dir()
    assert input_store.is_dir()
    validate_complete_test_bundle(Path(outcome["bundle"]))


def test_campaign_input_missing_changed_symlink_and_escape_fail_closed(tmp_path: Path) -> None:
    plan = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=_configuration(tmp_path),
        live=False,
        now=datetime(2026, 8, 24, 21, 0, 20, tzinfo=UTC),
    )
    input_store = Path(plan["input_store"]["directory"])
    qrss = plan["mode_plans"][2]["plan"]["reference"]["plan"]
    reference = Path(qrss["path"])
    original = reference.read_bytes()

    reference.unlink()
    with pytest.raises(CompleteTestError, match="unavailable or unsafe"):
        validate_complete_test_plan(plan)

    reference.write_bytes(original + b" ")
    with pytest.raises(CompleteTestError, match="changed"):
        validate_complete_test_plan(plan)

    reference.write_bytes(original)
    outside = tmp_path / "outside-reference.json"
    outside.write_bytes(original)
    escaped = {**qrss, **artifact(outside)}
    with pytest.raises(CompleteTestError, match="escapes the campaign input store"):
        _validate_campaign_input(escaped, input_store, label="QRSS reference plan")

    reference.unlink()
    reference.symlink_to(outside)
    with pytest.raises(CompleteTestError, match="unavailable or unsafe"):
        validate_complete_test_plan(plan)


def test_failed_composition_removes_unretained_campaign_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _configuration(tmp_path)

    def fail_profiles(*args, **kwargs):
        raise CompleteTestError("injected profile failure")

    monkeypatch.setattr(complete_test_module, "_materialize_real_profiles", fail_profiles)
    with pytest.raises(CompleteTestError, match="injected profile failure"):
        compose_complete_test_plan(
            "wspr4.local",
            "wspr5.local",
            SDR,
            configuration=config,
            live=False,
            now=datetime(2026, 8, 24, 21, 0, 20, tzinfo=UTC),
        )

    parent = tmp_path / "runs/complete-test-inputs"
    assert not parent.exists() or list(parent.iterdir()) == []


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
    with pytest.raises(CompleteTestError, match="between zero and one"):
        CompleteTestOverrides(carrier_best_20hz_share_min=1.1).validated()

    config = _configuration(tmp_path / "rp1")
    keyed_path = tmp_path / "rp1/templates/keyed.json"
    keyed = json.loads(keyed_path.read_text(encoding="utf-8"))
    keyed["application_plan"]["backend"] = "rp1_gpclk"
    _write(keyed_path, keyed)
    with pytest.raises(CompleteTestError, match="RP1 requires"):
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
        if mode == "QRSS":
            raise RuntimeError("test-only unpublished child safety stop")
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
    assert calls == ["TONE", "WSPR", "QRSS"]
    assert [entry["state"] for entry in outcome["result"]["modes"]] == [
        "attempted",
        "attempted",
        "attempted_unverified",
        "not_attempted",
        "not_attempted",
    ]
    assert outcome["result"]["final_status"] == "cleanup_failed"
    assert outcome["result"]["modes"][0]["final_status"] == "qualified"
    progress = [json.loads(line) for line in progress_path.read_text().splitlines()]
    assert [(item["mode"], item["status"]) for item in progress if item["stage"] == "mode"] == [
        ("TONE", "started"),
        ("TONE", "completed"),
        ("WSPR", "started"),
        ("WSPR", "completed"),
        ("QRSS", "started"),
        ("QRSS", "failed"),
        ("FSKCW", "skipped"),
        ("DFCW", "skipped"),
    ]
    assert progress[-1]["stage"] == "campaign" and progress[-1]["status"] == "terminal"
    (tmp_path / "runs/child-tone/extra.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(CompleteTestError, match="bundle evidence changed"):
        validate_complete_test_bundle(Path(outcome["bundle"]))


def test_selected_tone_can_qualify_only_its_exact_scope(tmp_path: Path) -> None:
    plan = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=_configuration(tmp_path),
        discovered_sdr=DISCOVERED_SDR,
        live=True,
        modes=("TONE",),
    )
    calls: list[str] = []

    def dispatch(wrapper, output_parent, **kwargs):
        mode = wrapper["mode"]
        calls.append(mode)
        bundle = output_parent / "child-tone"
        bundle.mkdir(parents=True)
        result = {
            "schema_version": 1,
            "run_id": "20260823T200000Z-tone",
            "status": "inconclusive",
            "started_utc": "2026-08-23T20:00:00Z",
            "completed_utc": "2026-08-23T20:00:01Z",
            "preflight_passed": True,
            "carrier_gate": "passed",
            "decode_gate": "not_run",
            "cleanup_outcome": "verified",
            "failure_causes": [],
            "artifacts": [],
        }
        _write(bundle / "result.json", result)
        session = {"run_id": result["run_id"], "final_status": "inconclusive"}
        _write(bundle / "session.json", session)
        write_manifest(bundle)
        return {"authoritative_bundle": str(bundle), "underlying_result": session}

    outcome = run_complete_test(
        plan,
        tmp_path / "runs",
        ssh_executable=tmp_path / "ssh-fixture",
        work_directory=tmp_path / "work",
        dispatcher=dispatch,
    )
    assert calls == ["TONE"]
    assert outcome["result"]["mode_order"] == ["TONE"]
    assert outcome["result"]["qualification_scope"]["modes"] == ["TONE"]
    assert outcome["result"]["final_status"] == "qualified"
    assert outcome["result"]["qualification_claim"] is True
    bundle = Path(outcome["bundle"])
    assert validate_complete_test_bundle(bundle) == outcome["result"]

    tampered = json.loads((bundle / "result.json").read_text(encoding="utf-8"))
    tampered["qualification_scope"]["modes"] = ["WSPR"]
    _write(bundle / "result.json", tampered)
    write_manifest(bundle)
    with pytest.raises(CompleteTestError, match="exact plan and order"):
        validate_complete_test_bundle(bundle)


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
    assert Path(plan["input_store"]["directory"]).is_dir()


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
    assert Path(plan["input_store"]["directory"]).is_dir()


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
    monkeypatch.setenv("WSPQ_PROGRESS_DIR", str(tmp_path / "progress"))
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


def test_complete_test_source_build_is_explicit_and_mutually_exclusive(tmp_path: Path) -> None:
    parser = cli_module._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "complete-test",
                "wspr4",
                "wspr5",
                "--sdr",
                SDR,
                "--wsprrypi-binary",
                "/opt/wsprrypi",
                "--wsprrypi-source",
                str(tmp_path),
            ]
        )


@pytest.mark.parametrize(
    ("runtime_arguments", "expected_binary", "expected_source"),
    (
        ((), "/usr/local/bin/wsprrypi", None),
        (("--wsprrypi-source", "."), None, Path(".")),
    ),
)
@pytest.mark.parametrize("gpio", [None, 4, 20])
def test_remote_complete_test_forwards_explicit_runtime_selection(
    tmp_path: Path,
    monkeypatch,
    capsys,
    runtime_arguments: tuple[str, ...],
    expected_binary: str | None,
    expected_source: Path | None,
    gpio: int | None,
) -> None:
    monkeypatch.setenv("WSPQ_PROGRESS_DIR", str(tmp_path / "progress"))
    observed = {}

    def delegated(*args, **kwargs):
        observed.update(kwargs)
        observed["forwarded"] = args[3]
        return {
            "bundle": "/retained/campaign",
            "result": {
                "final_status": "qualified",
                "transmitter_host": "wspr4",
                "receiver_host": "wspr5",
                "sdr_selector": SDR,
            },
        }

    monkeypatch.setattr(cli_module, "receiver_is_local", lambda host: False)
    monkeypatch.setattr(cli_module, "delegate_automatic_complete_test", delegated)
    arguments = [
        "complete-test",
        "wspr4",
        "wspr5",
        "--sdr",
        SDR,
        "--enable-rf",
        *runtime_arguments,
        *([] if gpio is None else ["--transmit-gpio", str(gpio)]),
    ]
    assert main(arguments) == 0
    assert observed["wsprrypi_binary"] == expected_binary
    assert observed["wsprrypi_source"] == expected_source
    assert observed["wsprrypi_configuration"] == "/usr/local/etc/wsprrypi.ini"
    assert observed["transmit_gpio"] == gpio
    if gpio is not None:
        forwarded = observed["forwarded"]
        assert forwarded[forwarded.index("--transmit-gpio") + 1] == str(gpio)
    capsys.readouterr()


def test_receiver_delegation_returns_full_machine_result(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("WSPQ_PROGRESS_DIR", str(tmp_path / "progress"))
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


def test_explicit_rf_path_reaches_deployment_and_invalid_input_blocks_it(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("WSPQ_PROGRESS_DIR", str(tmp_path / "progress"))
    monkeypatch.setattr(cli_module, "receiver_is_local", lambda host: False)
    observed = []

    def delegated(*args, **kwargs):
        observed.append(kwargs["rf_path"])
        assert kwargs["allow_unqualified_frequency"] is True
        return {
            "bundle": "/retained/campaign",
            "result": {
                "final_status": "inconclusive",
                "transmitter_host": "wspr5",
                "receiver_host": "wspr5",
                "sdr_selector": SDR,
            },
        }

    monkeypatch.setattr(cli_module, "delegate_automatic_complete_test", delegated)
    path = tmp_path / "path facts.json"
    facts = {
        "path_type": "conducted",
        "antenna_connected": False,
        "termination": "SDR input via combiner",
        "attenuation_db": 20,
        "filter": None,
        "safe_input_basis": "operator reported attenuated bench path",
        "authorization_scope": "single_run",
    }
    path.write_text(json.dumps(facts))
    arguments = [
        "complete-test",
        "wspr5",
        "wspr5",
        "--sdr",
        SDR,
        "--enable-rf",
        "--transmitter-backend",
        "rp1_gpclk",
        "--allow-unqualified-frequency",
        "--transmit-gpio",
        "20",
        "--rf-path",
        str(path),
    ]
    main(arguments)
    assert observed == [facts]
    facts["attenuation_db"] = -20
    path.write_text(json.dumps(facts))
    assert main(arguments) == 2
    assert len(observed) == 1
    capsys.readouterr()


@pytest.mark.parametrize("failed_mode", ["TONE", "WSPR"])
@pytest.mark.parametrize("measurement_status", ["inconclusive", "unqualified_carrier"])
def test_measurement_result_does_not_gate_later_modes(tmp_path, failed_mode, measurement_status):
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
        if mode == "QRSS":
            # Deliberate safety stop proves dispatch reached the next independent mode.
            raise RuntimeError("test-only unpublished child safety stop")
        bundle = output_parent / f"child-{mode.lower()}"
        bundle.mkdir(parents=True)
        failing = mode == failed_mode
        status = (
            measurement_status if failing else "inconclusive" if mode == "TONE" else "qualified"
        )
        document = {
            "schema_version": 1,
            "run_id": f"20260823T200000Z-{mode.lower()}",
            "status": status,
            "started_utc": "2026-08-23T20:00:00Z",
            "completed_utc": "2026-08-23T20:00:01Z",
            "preflight_passed": True,
            "carrier_gate": ("inconclusive" if status == "inconclusive" else "failed")
            if failing
            else "passed",
            "decode_gate": "not_run" if failing or mode == "TONE" else "passed",
            "cleanup_outcome": "verified",
            "failure_causes": ["transmitter_carrier"] if status == "unqualified_carrier" else [],
            "artifacts": [],
        }
        _write(bundle / "result.json", document)
        session = {"run_id": document["run_id"], "final_status": status}
        _write(bundle / "session.json", session)
        write_manifest(bundle)
        return {"authoritative_bundle": str(bundle), "underlying_result": session}

    outcome = run_complete_test(
        plan,
        tmp_path / "runs",
        ssh_executable=tmp_path / "ssh-fixture",
        work_directory=tmp_path / "work",
        dispatcher=dispatch,
    )
    assert calls == ["TONE", "WSPR", "QRSS"]
    entries = outcome["result"]["modes"]
    assert entries[0 if failed_mode == "TONE" else 1]["final_status"] == measurement_status
    assert entries[2]["state"] == "attempted_unverified"
    assert entries[3]["state"] == entries[4]["state"] == "not_attempted"
    assert outcome["result"]["final_status"] == "cleanup_failed"


@pytest.mark.parametrize(
    "options",
    [
        ["--transmitter-backend", "si5351", "--transmit-gpio", "20"],
        ["--transmitter-backend", "gpio", "--rp1-route", "gpio20"],
        ["--transmitter-backend", "rp1_gpclk"],
        ["--transmitter-backend", "rp1_gpclk", "--transmit-gpio", "4", "--rp1-route", "gpio20"],
    ],
)
def test_invalid_gpio_selector_combinations_block_before_deployment(tmp_path, monkeypatch, options):
    monkeypatch.setenv("WSPQ_PROGRESS_DIR", str(tmp_path / "progress"))

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid GPIO selection must not contact a host")

    monkeypatch.setattr(cli_module, "delegate_automatic_complete_test", forbidden)
    assert main(["complete-test", "wspr4", "wspr5", "--sdr", SDR, "--enable-rf", *options]) == 2


@pytest.mark.parametrize("gpio,expected", [(4, 0), (20, 2)])
def test_explicit_gpio_must_match_supplied_mode_plans(tmp_path, monkeypatch, gpio, expected):
    monkeypatch.setenv("WSPQ_PROGRESS_DIR", str(tmp_path / "progress"))
    monkeypatch.setattr(cli_module, "receiver_is_local", lambda host: True)
    config = _configuration(tmp_path)
    assert (
        main(
            [
                "complete-test",
                "wspr4.local",
                "wspr5.local",
                "--sdr",
                SDR,
                "--configuration",
                str(config),
                "--rehearse",
                "--transmit-gpio",
                str(gpio),
            ]
        )
        == expected
    )


def test_selected_modes_are_canonical_bounded_and_exclusive(tmp_path: Path) -> None:
    plan = compose_complete_test_plan(
        "wspr4.local",
        "wspr5.local",
        SDR,
        configuration=_configuration(tmp_path),
        live=False,
        modes=("DFCW", "TONE", "QRSS"),
    )
    assert plan["mode_order"] == ["TONE", "QRSS", "DFCW"]
    assert [entry["mode"] for entry in plan["mode_plans"]] == plan["mode_order"]
    assert plan["campaign_deadline_s"] == sum(
        entry["plan"]["deadlines"]["overall_s"] for entry in plan["mode_plans"]
    )
    input_store = Path(plan["input_store"]["directory"])
    assert not (input_store / "wspr").exists()
    assert not (input_store / "fskcw").exists()
    validate_complete_test_plan(plan)
    result = rehearse_complete_test(plan, Path(plan["execution_paths"]["output_parent"]))
    assert result["result"]["mode_order"] == plan["mode_order"]
    assert result["result"]["qualification_scope"]["modes"] == plan["mode_order"]
    assert len(result["result"]["modes"]) == 3


@pytest.mark.parametrize("modes", [(), ("TONE", "TONE"), ("BOGUS",)])
def test_selected_modes_reject_empty_duplicate_and_unknown(modes: tuple[str, ...]) -> None:
    with pytest.raises(CompleteTestError):
        normalize_modes(modes)


def test_remote_mode_selection_is_forwarded_in_operator_order(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("WSPQ_PROGRESS_DIR", str(tmp_path / "progress"))
    observed = {}

    def delegated(*args, **kwargs):
        observed["forwarded"] = args[3]
        return {
            "bundle": "/retained/campaign",
            "result": {
                "final_status": "qualified",
                "transmitter_host": "wspr4",
                "receiver_host": "wspr5",
                "sdr_selector": SDR,
            },
        }

    monkeypatch.setattr(cli_module, "receiver_is_local", lambda host: False)
    monkeypatch.setattr(cli_module, "delegate_automatic_complete_test", delegated)
    assert (
        main(
            [
                "complete-test",
                "wspr4",
                "wspr5",
                "--sdr",
                SDR,
                "--enable-rf",
                "--mode",
                "dfcw",
                "--mode",
                "tone",
            ]
        )
        == 0
    )
    forwarded = observed["forwarded"]
    assert [forwarded[i + 1] for i, value in enumerate(forwarded) if value == "--mode"] == [
        "DFCW",
        "TONE",
    ]
    capsys.readouterr()


def test_cli_rejects_duplicate_modes_before_delegation(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("WSPQ_PROGRESS_DIR", str(tmp_path / "progress"))
    monkeypatch.setattr(cli_module, "receiver_is_local", lambda host: False)
    monkeypatch.setattr(
        cli_module, "delegate_automatic_complete_test", lambda *a, **k: pytest.fail("delegated")
    )
    assert (
        main(
            [
                "complete-test",
                "wspr4",
                "wspr5",
                "--sdr",
                SDR,
                "--enable-rf",
                "--mode",
                "tone",
                "--mode",
                "TONE",
            ]
        )
        == 2
    )
    assert "must not be duplicated" in capsys.readouterr().err
