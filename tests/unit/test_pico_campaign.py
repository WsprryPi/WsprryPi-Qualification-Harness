"""Pico target matrix, strict admission, no-effect rehearsal, and failure precedence."""

import copy
import hashlib
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from wsprrypi_qualification.cw_contracts import CwContractError, _validate_events
from wsprrypi_qualification.cw_reference import generate_expected_events
from wsprrypi_qualification.offline import validate_document
from wsprrypi_qualification.pico_campaign import (
    check_admission,
    compose_pico_campaign,
    cw_analysis_plan,
    validate_configuration,
)
from wsprrypi_qualification.pico_live import PicoLiveAdapter, run_pico_campaign
from wsprrypi_qualification.pico_wire import PicoWireError
from wsprrypi_qualification.progress import ProgressReporter
from wsprrypi_qualification.receiver_calibration import disabled_binding

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/pico"
SERIAL = "0123456789ABCDEF"
DEVICE = hashlib.sha256(b"WsprryPico/1" + bytes.fromhex(SERIAL)).hexdigest()[:32]


def configuration(engine="pio-dma-gp2", gps=False, automatic=False):
    return dict(
        schema_version=1,
        target_kind="pico",
        configuration_id=f"{engine}-gps{gps}-auto{automatic}",
        pico_source=os.environ.get("WSPQ_PICO_SOURCE", "candidate source"),
        pico=dict(
            control_carrier="usb",
            board="pico2_w",
            serial=SERIAL,
            device_id=DEVICE,
            engine=engine,
            management_port="management-role-4",
            wtp_port="wtp-role-2",
            gps_enabled=gps,
            utc_source="gps" if gps else "sntp",
            automatic_correction=automatic,
            manual_correction_ppb=0,
            configuration_revision="revision-1",
        ),
        receiver=dict(
            host="localhost",
            capture_helper="capture tool",
            driver="fixture",
            serial="receiver1",
            channel=0,
            sample_rate_hz=12000,
            bandwidth_hz=10000,
            center_frequency_hz=14094000,
            gain_db=20,
            wsprd="wsprd tool",
        ),
        protocol=dict(
            frequency_hz=14097100,
            tone_seconds=5,
            callsign="K1ABC",
            locator="FN42",
            power_dbm=37,
            message="TEST",
            dot_seconds=1,
            shift_hz=5,
        ),
        rf_path=dict(
            attenuation_db=60,
            filter_state="fixed conducted fixture",
            termination="SDR terminated conducted path",
            antenna_state="disconnected",
            safe_input_basis="fixture only; no physical authorization",
        ),
        receiver_calibration=None,
    )


def make_plan(config=None):
    config = config or configuration()
    file = dict(path="C:\\Program Files\\Pico\\compiler.exe", size_bytes=1, sha256="a" * 64)
    job = dict(
        job_id="a" * 32,
        profile="rf-events/1",
        mode="tone",
        total_duration_ns="19000001000",
        allow_frequency_adjustment=True,
        events=[
            dict(
                offset_ns="0",
                duration_ns="5000000000",
                rf_on=True,
                frequency_nhz="14097100000000000",
            ),
            dict(offset_ns="5000000000", duration_ns="2000000000", rf_on=False),
            dict(
                offset_ns="7000000000",
                duration_ns="5000000000",
                rf_on=True,
                frequency_nhz="14097100000000000",
            ),
            dict(offset_ns="12000000000", duration_ns="2000000000", rf_on=False),
            dict(
                offset_ns="14000000000",
                duration_ns="5000000000",
                rf_on=True,
                frequency_nhz="14097100000000000",
            ),
            dict(offset_ns="19000000000", duration_ns="1000", rf_on=False),
        ],
    )
    return dict(
        schema_version=1,
        target_kind="pico",
        configuration=config,
        compiler=file,
        installer=file,
        encoder_sources=[file],
        analyzer_source=dict(revision="sha256:" + "d" * 64, files=[file]),
        source_revision="b" * 40,
        receiver_calibration=disabled_binding(),
        modes=[
            dict(
                mode="tone",
                independent_observations=3,
                carrier_job=None,
                specification=dict(
                    mode="tone",
                    job_id="a" * 32,
                    frequency_nhz="14097100000000000",
                    duration_ns="5000000000",
                    off_ns="2000000000",
                    cycles=3,
                    allow_frequency_adjustment=True,
                ),
                jobs=[job],
            )
        ],
    )


def make_snapshot(engine="pio-dma-gp2", gps=False, automatic=False):
    value = json.loads((FIXTURES / "inhibited-host-snapshot.json").read_text())
    for resource in (
        "hardware",
        "gps",
        "si5351",
        "ntp-observations",
        "rf-calibration",
        "message-schedule",
    ):
        value[resource]["target"]["device_id"] = DEVICE
    value["target"]["device_id"] = DEVICE
    value["revision"] = "revision-1"
    value["hardware"]["gpio_drive"]["target"]["device_id"] = DEVICE
    value["hardware"]["execution_engine"] = engine
    value["hardware"]["gpio_drive"]["execution_engine"] = engine
    value["capabilities"]["wtp"]["engine"] = engine
    status = value["status"]["standalone"]
    status.update(
        engine=engine,
        configured=True,
        clock_state="synchronized",
        sync_age_ns="1000000000",
        uncertainty_ns="1000000",
        gpio_drive_ma=4,
        boot_gpio_drive_ma=4,
    )
    value["ntp-observations"].update(
        active_source="gps" if gps else "sntp",
        nominal_sample_hz=138000000,
        sync_age_ns="1000000000",
    )
    pins = dict(
        engine="i2c" if engine == "si5351" else "direct",
        rf_gp=None if engine == "si5351" else 2,
        i2c_pair=1 if engine == "si5351" else None,
        button_gp=14,
        amplifier_gp=None,
        lpf_gps=[],
        indicator="onboard_led",
        indicator_gp=None,
        amplifier_active_high=True,
    )
    value["hardware"].update(
        saved=pins, active=pins, saved_generation="1", active_revision="c" * 64
    )
    value["hardware"]["gpio_drive"].update(
        saved=4, boot_active=4, pad_readback=4, rf_gp=pins["rf_gp"]
    )
    saved = dict(
        enabled=False,
        station=dict(callsign="K1ABC", locator="FN42", power_dbm=37),
        wifi=dict(ssid="fixture", password=None, ntp_ipv4="127.0.0.1"),
        schedules=[],
        expires_utc_s=0,
        pins=pins,
        gpio_drive_ma=4,
    )
    if engine == "si5351":
        si = json.loads((FIXTURES / "si5351.json").read_text())
        si["target"] = dict(value["target"])
        si["execution_engine"] = engine
        si["saved"]["automatic_calibration"] = automatic
        si["boot_active"]["automatic_calibration"] = automatic
        si["saved"]["manual_correction_ppb"] = 0
        si["boot_active"]["manual_correction_ppb"] = 0
        si["runtime"].update(
            transmit="register_disabled", communication="register_control_verified", inhibited=False
        )
        value["si5351"] = si
        saved["si5351"] = copy.deepcopy(si["saved"])
    if gps:
        g = json.loads((FIXTURES / "gps.json").read_text())
        g["target"] = dict(value["target"])
        for key in ("saved", "boot_active"):
            g[key]["automatic_rf_calibration"] = automatic if engine != "si5351" else False
        estimate = dict(
            oscillator_error_ppb=25,
            uncertainty_ppb=1,
            observed_raw_ns="10000000000",
            age_ns="1000000000",
            span_ns="60000000000",
            generation="7",
            fresh=True,
        )
        g["runtime"].update(
            serial_present=True,
            pps_present=True,
            time_valid=True,
            age_ns="1000000000",
            timer_calibration=copy.deepcopy(estimate),
            synthesizer_calibration=copy.deepcopy(estimate),
            error="",
        )
        value["gps"] = g
        saved["gps"] = copy.deepcopy(g["saved"])
    value["config"]["config"] = saved
    selection = dict(
        candidate=dict(
            qualified=False,
            utc_rate_ppb=0,
            uncertainty_ppb=0,
            generation="0",
            sampled_raw_ns="0",
            span_ns="0",
            samples=0,
            reason="ntp_rf_rejected",
        ),
        candidate_correction_ppb=None,
        candidate_uncertainty_ppb=0,
        saved_ntp_available=False,
        saved_ntp=None,
        candidate_age_ns="0",
        topology_supported=False,
        independent_rf_verified=False,
        ntp_rf_supported=False,
        next_ppb=25 if automatic else 0,
        next_generation="7" if automatic else "0",
        next_source="gps" if automatic else "manual",
        reason="gps_calibrated" if automatic else "manual_mode",
        prepared_ppb=None,
        prepared_source=None,
        prepared_generation=None,
        prepared_input=None,
    )
    value["rf-calibration"].update(
        saved=dict(manual_ppm="0.000", mode="manual"), selection=selection, execution_engine=engine
    )
    return value


@pytest.mark.parametrize("engine", ["pio-dma-gp2", "si5351"])
@pytest.mark.parametrize("gps,automatic", [(False, False), (True, False), (True, True)])
def test_all_four_rows_and_correction_subcases_admit(engine, gps, automatic):
    config = configuration(engine, gps, automatic)
    validate_configuration(config)
    check_admission(make_plan(config), make_snapshot(engine, gps, automatic))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v["pico"].update(engine="gpio"),
        lambda v: v["pico"].update(gps_enabled=False, automatic_correction=True),
        lambda v: v["pico"].update(device_id="f" * 32),
        lambda v: v["pico"].update(wtp_port=v["pico"]["management_port"]),
        lambda v: v["pico"].update(utc_source="gps"),
        lambda v: v["pico"].update(automatic_correction=1),
        lambda v: v.update(extra=True),
        lambda v: v["protocol"].update(frequency_hz=70000000),
        lambda v: v["receiver"].update(center_frequency_hz=14097100),
        lambda v: v["receiver"].update(bandwidth_hz=50000),
        lambda v: v["rf_path"].update(antenna_state="connected"),
    ],
)
def test_configuration_refusals(mutate):
    value = configuration()
    mutate(value)
    with pytest.raises(ValueError):
        validate_configuration(value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v["status"]["job"].update(owner_id="c" * 32),
        lambda v: v["status"]["job"].update(output_active=True),
        lambda v: v["status"]["job"].update(boot_id="e" * 32),
        lambda v: v["config"]["config"].update(enabled=True),
        lambda v: v.update(revision="changed"),
        lambda v: v["hardware"].update(pending_restart=True),
        lambda v: v["hardware"].update(execution_engine="inhibited-no-rf"),
        lambda v: v["gps"]["runtime"].update(serial_present=False),
        lambda v: v["gps"]["runtime"].update(pps_present=False),
        lambda v: v["gps"]["runtime"].update(time_valid=False),
        lambda v: v["gps"]["runtime"].update(age_ns="18446744073709551615"),
        lambda v: v["gps"]["runtime"].update(age_ns="18446744073709551616"),
        lambda v: v["gps"]["runtime"].update(age_ns=1),
        lambda v: v["gps"]["runtime"].update(extra=True),
        lambda v: v["gps"]["boot_active"].update(pairing_qualified=False),
        lambda v: v["gps"]["runtime"]["timer_calibration"].update(fresh=False),
        lambda v: v["gps"]["runtime"]["timer_calibration"].update(generation="9"),
        lambda v: v["ntp-observations"].update(source_disagreement=True),
        lambda v: v["ntp-observations"].update(active_source="sntp"),
        lambda v: v["rf-calibration"]["selection"].update(next_source="manual"),
        lambda v: v["rf-calibration"]["selection"].update(reason="gps_calibration_required"),
        lambda v: v["gps"].update(contract="Wsprry-Control/2"),
        lambda v: v["gps"]["target"].update(boot_id="e" * 32),
    ],
)
def test_admission_failures_before_ownership(mutate):
    value = make_snapshot(gps=True, automatic=True)
    mutate(value)
    with pytest.raises(ValueError):
        check_admission(make_plan(configuration(gps=True, automatic=True)), value)


def test_si5351_missing_feedback_and_register_state_refuse():
    plan = make_plan(configuration("si5351", True, True))
    for change in (
        lambda v: v["si5351"]["boot_active"].update(loopback_output=None, loopback_input_gp=None),
        lambda v: v["si5351"]["runtime"].update(transmit="unknown"),
        lambda v: v["gps"]["runtime"].update(synthesizer_calibration=None),
    ):
        value = make_snapshot("si5351", True, True)
        change(value)
        with pytest.raises(ValueError):
            check_admission(plan, value)


@pytest.mark.parametrize("mode", ["tone", "qrss", "fskcw", "dfcw"])
def test_independent_reference_uses_distinct_pico_source_contract(mode):
    plan = make_plan()
    value = cw_analysis_plan(plan, mode, "20261108T120000Z-pico-test", 5)
    assert value["source"]["target_kind"] == "pico"
    assert "submodule_revision" not in value["source"]
    assert value["backend"] == "pico_gpio"
    validate_document(value, "cw-mode-plan.schema.json")
    invalid = copy.deepcopy(value)
    invalid["backend"] = "gpio"
    with pytest.raises(ValueError):
        validate_document(invalid, "cw-mode-plan.schema.json")


def test_rehearsal_opens_no_devices_and_claims_no_observations(tmp_path, monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("Rehearsal attempted physical capability")

    monkeypatch.setattr("wsprrypi_qualification.pico_live.PicoLiveAdapter", forbidden)
    with ProgressReporter(tmp_path / "progress.jsonl") as reporter:
        result = run_pico_campaign(
            make_plan(),
            tmp_path / "output with spaces",
            reporter,
            enable_rf=False,
            operator="fixture",
            rehearse=True,
        )
    assert result["outcome"] == "rehearsed" and result["qualification_claim"] is False
    assert result["modes"][0]["observations"] == []
    assert result["modes"][0]["outcome"] == "unattempted"


def test_missing_live_authority_and_unsupported_platform_have_no_effects(tmp_path, monkeypatch):
    with ProgressReporter(tmp_path / "progress.jsonl") as reporter:
        with pytest.raises(PicoWireError):
            run_pico_campaign(
                make_plan(), tmp_path / "no output", reporter, enable_rf=False, operator="fixture"
            )
        monkeypatch.setattr("wsprrypi_qualification.pico_serial.platform.system", lambda: "Windows")
        with pytest.raises(PicoWireError):
            run_pico_campaign(
                make_plan(), tmp_path / "no output", reporter, enable_rf=True, operator="fixture"
            )
    assert not (tmp_path / "no output").exists()


def test_production_compiler_composes_six_explicit_configurations():
    compiler = os.environ.get("WSPQ_PICO_COMPILER")
    if compiler is None:
        pytest.skip("Explicit production encoder integration")
    for engine in ("pio-dma-gp2", "si5351"):
        for gps, auto in ((False, False), (True, False), (True, True)):
            plan = compose_pico_campaign(configuration(engine, gps, auto), Path(compiler))
            assert [row["mode"] for row in plan["modes"]] == [
                "tone",
                "wspr",
                "qrss",
                "fskcw",
                "dfcw",
            ]
            assert all(row["independent_observations"] == 3 for row in plan["modes"])
            assert all(row["jobs"] for row in plan["modes"])


def test_hardware_resource_requires_current_control_identifier():
    snapshot = make_snapshot("pio-dma-gp2", False, False)
    snapshot["hardware"].pop("contract")
    with pytest.raises(PicoWireError, match="current contract"):
        check_admission(make_plan(), snapshot)


def test_analyzer_provenance_is_harness_content_not_pico_git():
    from wsprrypi_qualification.pico_campaign import analyzer_identity

    identity = analyzer_identity()
    assert identity["revision"].startswith("sha256:")
    assert any(Path(record["path"]).name == "cw_iq.py" for record in identity["files"])
    assert all("wsprrypi_qualification" in record["path"] for record in identity["files"])


def test_independent_reference_refuses_compiler_event_tampering():
    from wsprrypi_qualification.pico_campaign import validate_pico_plan

    plan = make_plan()
    validate_pico_plan(plan)
    plan["modes"][0]["jobs"][0]["events"][0]["frequency_nhz"] = "14097101000000000"
    with pytest.raises(PicoWireError, match="independent mode reference"):
        validate_pico_plan(plan)


def test_occupied_status_refuses_before_other_resources():
    from wsprrypi_qualification.pico_live import _snapshot

    class Occupied:
        def __init__(self):
            self.calls = []

        def get(self, resource, definition):
            self.calls.append(resource)
            return dict(job=dict(owner_id="c" * 32, state="running", output_active=True))

    client = Occupied()
    with pytest.raises(PicoWireError, match="occupied"):
        _snapshot(client, require_idle=True)
    assert client.calls == ["status"]


@pytest.mark.parametrize("mode", ["tone", "qrss", "fskcw", "dfcw"])
def test_pico_mode_reference_and_maintained_analyzer_known_truth(tmp_path, mode):
    from wsprrypi_qualification.cw_iq import analyze_synthetic_iq, generate_synthetic_iq
    from wsprrypi_qualification.cw_reference import write_expected_events

    plan = make_plan()
    cw = cw_analysis_plan(plan, mode, "20261108T000000Z-pico-known-truth", 2)
    (tmp_path / "mode-plan.json").write_text(json.dumps(cw))
    revision = plan["analyzer_source"]["revision"]
    write_expected_events(
        tmp_path / "mode-plan.json", tmp_path / "expected.json", source_revision=revision
    )
    generate_synthetic_iq(
        tmp_path / "mode-plan.json",
        tmp_path / "expected.json",
        tmp_path / "iq.cf32",
        tmp_path / "iq.json",
        seed=42,
    )
    measured, gate = analyze_synthetic_iq(
        tmp_path / "mode-plan.json",
        tmp_path / "expected.json",
        tmp_path / "iq.json",
        tmp_path / "observations.json",
        tmp_path / "gate.json",
        source_revision=revision,
    )
    assert measured["analysis_outcome"] == "passed"
    assert measured["analyzer"]["source_revision"] == revision
    assert gate["carrier_gate"] == "passed"
    assert gate["mode_gate"] == ("not_applicable" if mode == "tone" else "passed")


def test_cli_production_compiler_rehearsal_and_validation(tmp_path, monkeypatch, capsys):
    from wsprrypi_qualification.cli import main

    compiler = os.environ.get("WSPQ_PICO_COMPILER")
    if compiler is None:
        pytest.skip("Explicit production encoder integration")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Rehearsal attempted physical access")

    monkeypatch.setattr("wsprrypi_qualification.pico_live.PicoLiveAdapter", forbidden)
    config = tmp_path / "configuration with spaces.json"
    config.write_text(json.dumps(configuration()))
    output = tmp_path / "private runs"
    assert (
        main(
            [
                "complete-test",
                "pico",
                "localhost",
                "--sdr",
                "driver=fixture,serial=receiver1",
                "--pico-configuration",
                str(config),
                "--pico-compiler",
                compiler,
                "--pico-output-parent",
                str(output),
                "--progress-log",
                str(tmp_path / "progress.jsonl"),
                "--rehearse",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert not result["qualification_claim"]
    assert main(["validate-complete-test", result["output_directory"]]) == 0


@pytest.mark.parametrize("mutation", ["tool", "wav", "audio", "data", "arguments"])
def test_decoder_artifact_and_tool_tampering_refused(tmp_path, mutation):
    from wsprrypi_qualification.offline import artifact
    from wsprrypi_qualification.pico_results import _decoder_bindings

    directory = tmp_path / "wspr-1-1"
    directory.mkdir()
    data = directory / "wsprd-data"
    data.mkdir()
    wav = directory / "slot.wav"
    wav.write_bytes(b"test artifact only")
    audio_path = directory / "audio.json"
    audio_path.write_text("{}")
    tool = dict(path="selected wsprd", sha256="a" * 64)
    (tmp_path / "controller.json").write_text(json.dumps(dict(tools=dict(wsprd=tool))))
    document = dict(
        wav=artifact(wav),
        audio_evidence=artifact(audio_path),
        tool=dict(tool, version_query=dict(arguments=[tool["path"], "--version"])),
        decoder_data_directory=str(data),
        decoder_data_artifacts=[],
        arguments=[tool["path"], "-a", str(data), str(wav)],
    )
    audio = dict(output=artifact(wav))
    _decoder_bindings(directory, document, audio)
    if mutation == "tool":
        document["tool"]["sha256"] = "b" * 64
    elif mutation == "wav":
        document["wav"]["sha256"] = "b" * 64
    elif mutation == "audio":
        document["audio_evidence"]["sha256"] = "b" * 64
    elif mutation == "data":
        (data / "unindexed-output").write_text("must be indexed")
    else:
        document["arguments"].append("--unrecorded-option")
    with pytest.raises(PicoWireError):
        _decoder_bindings(directory, document, audio)


def test_production_fractional_dot_timing_survives_independent_reference_quantization():
    compiler = os.environ.get("WSPQ_PICO_COMPILER")
    if compiler is None:
        pytest.skip("Explicit production encoder integration")
    config = configuration()
    config["protocol"]["dot_seconds"] = 0.7
    config["protocol"]["tone_seconds"] = 3.5
    plan = compose_pico_campaign(config, Path(compiler))
    assert len(plan["modes"]) == 5


def test_stale_compiler_inputs_are_refused_before_live_access(tmp_path):
    import shutil

    compiler, source = os.environ.get("WSPQ_PICO_COMPILER"), os.environ.get("WSPQ_PICO_SOURCE")
    if compiler is None or source is None:
        pytest.skip("Explicit production encoder integration")
    source = Path(source)
    candidate = tmp_path / "candidate source"
    for name in ("src/encoding", "src/wtp"):
        shutil.copytree(source / name, candidate / name)
    for name in (
        "engineering/protocol/control-1.schema.json",
        "engineering/protocol/wtp-1.schema.json",
        "engineering/protocol/network-diagnostics.schema.json",
        "engineering/protocol/rf-calibration-diagnostics.schema.json",
        "scripts/usb_installer.py",
    ):
        destination = candidate / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    with (candidate / "src/encoding/wspr.cpp").open("a") as handle:
        handle.write("\n// test-only changed compiler input\n")
    config = configuration()
    config["pico_source"] = str(candidate)
    with pytest.raises(PicoWireError, match="different encoder inputs"):
        compose_pico_campaign(config, Path(compiler))


@pytest.mark.parametrize(
    "cause,expected",
    [(KeyboardInterrupt, "aborted"), (InterruptedError, "aborted"), (RuntimeError, "blocked")],
)
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_wrapped_cancellation_and_cleanup_precedence(
    tmp_path, monkeypatch, cause, expected, cleanup_fails
):
    from types import SimpleNamespace

    from wsprrypi_qualification.wtp_control import WtpControlError

    class Adapter:
        def __init__(self, *_args):
            self.usb = SimpleNamespace(identity={})
            self.jobs_attempted = False

        def snapshot(self, **_kwargs):
            return make_snapshot("pio-dma-gp2", False, False)

        def start_wtp(self):
            pass

        def observe(self, *_args, **_kwargs):
            self.jobs_attempted = True
            raise WtpControlError(
                "wrapped transaction failure", {"cleanup_verified": True}
            ) from cause()

        def begin_cleanup(self):
            pass

        def inactive(self):
            return dict(output_active=False)

        def close(self):
            if cleanup_fails:
                raise PicoWireError("injected cleanup failure")

    monkeypatch.setattr("wsprrypi_qualification.pico_live.PicoLiveAdapter", Adapter)
    monkeypatch.setattr("wsprrypi_qualification.pico_live._authenticate_plan", lambda _plan: None)
    monkeypatch.setattr("wsprrypi_qualification.pico_live.require_supported_platform", lambda: None)
    with ProgressReporter(tmp_path / "progress.jsonl") as reporter:
        result = run_pico_campaign(
            make_plan(), tmp_path / "runs", reporter, enable_rf=True, operator="fixture"
        )
    assert result["outcome"] == ("cleanup_failed" if cleanup_fails else expected)
    assert result["transmitter_transaction_attempted"]
    assert not result["qualification_claim"]
    assert result["cleanup_verified"] is not cleanup_fails


def test_missed_transmitter_job_is_failed_not_a_fixture_block():
    from wsprrypi_qualification.pico_live import _error_outcome
    from wsprrypi_qualification.wtp_control import WtpControlError

    assert (
        _error_outcome(
            WtpControlError("missed", dict(cleanup_verified=True, status=dict(state="missed")))
        )
        == "failed"
    )
    assert (
        _error_outcome(
            WtpControlError("failed", dict(cleanup_verified=False, status=dict(state="failed")))
        )
        == "cleanup_failed"
    )


@pytest.mark.parametrize("engine", ["pio-dma-gp2", "si5351"])
@pytest.mark.parametrize("lead_in", [2, 7.287371873855591])
def test_pico_tone_contract_accepts_real_capture_lead_in(engine, lead_in):
    plan = cw_analysis_plan(
        make_plan(configuration(engine)), "tone", "20260815T120000Z-capture-lead-in", lead_in
    )
    expected = {
        "protocol_definition": plan["protocol"]["definition"],
        "events": generate_expected_events(plan),
    }
    _validate_events(plan, expected)


@pytest.mark.parametrize("engine", ["pio-dma-gp2", "si5351"])
@pytest.mark.parametrize("boundary", [0, 1, 2, 5])
def test_pico_tone_contract_still_rejects_one_ulp_boundary_drift(engine, boundary):
    plan = cw_analysis_plan(
        make_plan(configuration(engine)),
        "tone",
        "20260815T120000Z-capture-boundary",
        7.287371873855591,
    )
    expected = {
        "protocol_definition": plan["protocol"]["definition"],
        "events": generate_expected_events(plan),
    }
    end = math.nextafter(expected["events"][boundary]["end_s"], math.inf)
    expected["events"][boundary]["end_s"] = end
    expected["events"][boundary + 1]["start_s"] = end
    with pytest.raises(CwContractError):
        _validate_events(plan, expected)


@pytest.mark.parametrize("engine", ["pio-dma-gp2", "si5351"])
def test_pico_produced_capture_metadata_is_accepted_by_acquired_replay(
    tmp_path, monkeypatch, engine
):
    # Signal measurement is separate; exercise the real metadata producer and
    # replay consumer together without opening a device or declaring live RF.
    from wsprrypi_qualification.cw_replay import _load_acquired_inputs

    adapter = object.__new__(PicoLiveAdapter)
    adapter.plan = make_plan(configuration(engine))
    capture_path = tmp_path / "fixture.cf32"
    capture_path.write_bytes(bytes(480000 * 8))
    capture = SimpleNamespace(
        output_path=capture_path,
        metadata_path=tmp_path / "unused-native-metadata.json",
        sample_rate_hz=12000,
        sample_count=480000,
        center_frequency_hz=14094000,
    )

    def fixture_measurement(*args, **kwargs):
        args[4].write_text('{"fixture_measurement_only":true}')
        return {"analysis_outcome": "passed", "observations": []}, {}

    monkeypatch.setattr(
        "wsprrypi_qualification.pico_live.analyze_synthetic_iq",
        fixture_measurement,
    )
    monkeypatch.setattr(
        "wsprrypi_qualification.pico_live.analyze_carrier",
        lambda *args, **kwargs: {"gate_outcome": "passed"},
    )
    snapshot = {
        "si5351": {"boot_active": {"drive_ma": 8}},
        "status": {"standalone": {"gpio_drive_ma": 12}},
        "ntp-observations": {"nominal_sample_hz": 138000000},
    }
    adapter.analyze("tone", tmp_path, snapshot, capture, capture, None, 7.287371873855591)
    _load_acquired_inputs(
        tmp_path / "mode-plan.json", tmp_path / "expected.json", tmp_path / "acquired.json"
    )


def frozen_input_case(engine="pio-dma-gp2"):
    plan = make_plan(configuration(engine, True, True))
    prepared = make_snapshot(engine, True, True)
    key = "timer_calibration" if engine == "pio-dma-gp2" else "synthesizer_calibration"
    bracket = [copy.deepcopy(prepared["gps"]) for _ in range(2)]
    for document, generation, error, observed, span in (
        (bracket[0], "228", 182, "377000000000", "54000000000"),
        (bracket[1], "233", 218, "382000000000", "59000000000"),
        (prepared["gps"], "234", 195, "383000000000", "60000000000"),
    ):
        document["runtime"][key].update(
            generation=generation,
            oscillator_error_ppb=error,
            observed_raw_ns=observed,
            span_ns=span,
            uncertainty_ppb=7014,
        )
    prepared["rf-calibration"]["selection"].update(
        prepared_source="gps",
        prepared_generation="230",
        prepared_ppb=200,
        prepared_input=dict(
            job_id="a" * 32,
            manual_correction_ppb=0,
            oscillator_error_ppb=200,
            uncertainty_ppb=7000,
            observed_raw_ns="379000000000",
            span_ns="56000000000",
            generation="230",
            selected_raw_ns="380000000000",
        ),
    )
    return plan, prepared, bracket, key


def verify_frozen(plan, prepared, bracket, observations=None):
    from wsprrypi_qualification.pico_campaign import check_prepared_correction

    check_prepared_correction(
        plan,
        prepared,
        [prepared["gps"]] if observations is None else observations,
        job_id="a" * 32,
        load_bracket=bracket,
    )


@pytest.mark.parametrize("engine", ["pio-dma-gp2", "si5351"])
def test_frozen_input_does_not_require_polling_intervening_generation(engine):
    plan, prepared, bracket, _ = frozen_input_case(engine)
    verify_frozen(plan, prepared, bracket)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "job",
        "generation",
        "manual",
        "ppb",
        "target",
        "shape",
        "stale",
        "future",
        "span",
        "uncertainty",
        "bound_growth",
        "overflow",
        "boolean",
        "before_generation",
        "after_generation",
        "missing_bracket",
        "conflict",
    ],
)
def test_frozen_correction_rejects_unbound_stale_or_conflicting_inputs(mutation):
    plan, prepared, bracket, key = frozen_input_case()
    selection = prepared["rf-calibration"]["selection"]
    frozen = selection["prepared_input"]
    observations = [prepared["gps"]]
    if mutation == "missing":
        del selection["prepared_input"]
    elif mutation == "extra":
        frozen["unknown"] = 1
    elif mutation == "job":
        frozen["job_id"] = "f" * 32
    elif mutation == "generation":
        frozen["generation"] = "229"
    elif mutation == "manual":
        frozen["manual_correction_ppb"] = 1
    elif mutation == "ppb":
        selection["prepared_ppb"] += 1
    elif mutation == "target":
        bracket[0]["target"]["boot_id"] = "f" * 32
    elif mutation == "shape":
        bracket[0]["unknown"] = True
    elif mutation == "stale":
        frozen["selected_raw_ns"] = "4000000000000"
    elif mutation == "future":
        frozen["observed_raw_ns"] = "381000000000"
    elif mutation == "span":
        frozen["span_ns"] = "9000000000"
    elif mutation == "uncertainty":
        frozen["uncertainty_ppb"] = 0
    elif mutation == "bound_growth":
        frozen["uncertainty_ppb"] = 100001
    elif mutation == "overflow":
        frozen["selected_raw_ns"] = str(2**64)
    elif mutation == "boolean":
        frozen["manual_correction_ppb"] = False
    elif mutation == "before_generation":
        bracket[0]["runtime"][key]["generation"] = "231"
    elif mutation == "after_generation":
        bracket[1]["runtime"][key]["generation"] = "229"
    elif mutation == "missing_bracket":
        bracket.pop()
    else:
        changed = copy.deepcopy(bracket[0])
        changed["runtime"][key].update(
            {
                name: frozen[name]
                for name in ("generation", "oscillator_error_ppb", "observed_raw_ns", "span_ns")
            }
        )
        changed["runtime"][key]["oscillator_error_ppb"] += 1
        observations.append(changed)
    with pytest.raises((PicoWireError, ValueError)):
        verify_frozen(plan, prepared, bracket, observations)


@pytest.mark.parametrize("mutation", [None, "error", "bound"])
def test_matching_retained_generation_corroborates_atomic_input(mutation):
    plan, prepared, bracket, key = frozen_input_case()
    frozen = prepared["rf-calibration"]["selection"]["prepared_input"]
    estimate = bracket[0]["runtime"][key]
    estimate.update(
        {
            name: frozen[name]
            for name in ("generation", "oscillator_error_ppb", "observed_raw_ns", "span_ns")
        }
    )
    estimate["uncertainty_ppb"] = 7014
    if mutation == "error":
        estimate["oscillator_error_ppb"] += 1
    elif mutation == "bound":
        estimate["uncertainty_ppb"] += 1
    if mutation is None:
        verify_frozen(plan, prepared, bracket)
    else:
        with pytest.raises(PicoWireError, match="conflicts"):
            verify_frozen(plan, prepared, bracket)


@pytest.mark.parametrize("never_matches", [False, True])
def test_snapshot_reads_bounded_matching_gps_pair_without_weakening_admission(never_matches):
    from wsprrypi_qualification.pico_live import _snapshot

    value = make_snapshot("pio-dma-gp2", True, True)
    generation = value["gps"]["runtime"]["timer_calibration"]["generation"]

    class Client:
        def __init__(self):
            self.hello = {"target": value["target"]}
            self.etags = {"config": value["revision"]}
            self.gps_reads = 0

        def get(self, resource, definition):
            result = copy.deepcopy(value[resource])
            if resource == "gps":
                self.gps_reads += 1
                if self.gps_reads == 1 or never_matches:
                    result["runtime"]["timer_calibration"]["generation"] = "987654"
            return result

    client = Client()
    observed = _snapshot(client)
    if never_matches:
        assert client.gps_reads == 4
        with pytest.raises(PicoWireError, match="generation-mismatched"):
            check_admission(make_plan(configuration("pio-dma-gp2", True, True)), observed)
    else:
        assert client.gps_reads == 2
        assert observed["gps"]["runtime"]["timer_calibration"]["generation"] == generation
        check_admission(make_plan(configuration("pio-dma-gp2", True, True)), observed)


@pytest.mark.parametrize(
    "manual,learned,expected", [(50000, 10000, 60001), (-50000, 10000, -40001)]
)
def test_frozen_correction_preserves_signed_residual_rounding(manual, learned, expected):
    plan, prepared, bracket, _ = frozen_input_case()
    plan["configuration"]["pico"]["manual_correction_ppb"] = manual
    selection = prepared["rf-calibration"]["selection"]
    selection["prepared_input"].update(manual_correction_ppb=manual, oscillator_error_ppb=learned)
    selection["prepared_ppb"] = expected
    verify_frozen(plan, prepared, bracket)


@pytest.mark.parametrize("defect", ["pairing", "stale", "missing_pps", "missing_time"])
@pytest.mark.parametrize("recovers", [False, True])
def test_snapshot_waits_boundedly_for_qualified_gps_without_relaxing_admission(
    monkeypatch, defect, recovers
):
    from wsprrypi_qualification.pico_live import _snapshot

    value = make_snapshot("pio-dma-gp2", True, False)
    clock = [0.0]
    monkeypatch.setattr("wsprrypi_qualification.pico_live.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "wsprrypi_qualification.pico_live.time.sleep",
        lambda delay: clock.__setitem__(0, clock[0] + delay),
    )

    class Client:
        def __init__(self):
            self.hello = {"target": value["target"]}
            self.etags = {"config": value["revision"]}
            self.gps_reads = 0

        def get(self, resource, definition):
            result = copy.deepcopy(value[resource])
            if resource == "gps":
                self.gps_reads += 1
                if not recovers or clock[0] < 0.75:
                    runtime = result["runtime"]
                    if defect == "pairing":
                        runtime["error"] = "unpaired_utc"
                    elif defect == "stale":
                        runtime["age_ns"] = "3000000001"
                    elif defect == "missing_pps":
                        runtime["pps_present"] = False
                    else:
                        runtime["time_valid"] = False
            return result

    client = Client()
    observed = _snapshot(client)
    assert client.gps_reads > 1
    assert clock[0] <= 2.001
    plan = make_plan(configuration("pio-dma-gp2", True, False))
    if recovers:
        assert 0.75 <= clock[0] < 2
        check_admission(plan, observed)
    else:
        assert clock[0] == pytest.approx(2)
        with pytest.raises(PicoWireError, match="GPS"):
            check_admission(plan, observed)


@pytest.mark.parametrize("gps_enabled", [False, True])
@pytest.mark.parametrize("fraction_ns", [0, 1, 993_724_160, 999_999_999])
def test_non_wspr_uses_full_requested_utc_second(gps_enabled, fraction_ns):
    from wsprrypi_qualification.pico_live import _non_wspr_start_ns

    now = 1_794_197_941_000_000_000 + fraction_ns
    start = _non_wspr_start_ns(now, gps_enabled)
    lead = 7_000_000_000 if gps_enabled else 5_000_000_000
    assert lead <= start - now < lead + 1_000_000_000
    # A start in the final few milliseconds gave the product too little
    # of its defined UTC-second window for its valid indicator handshake.
    exclusive_deadline = start + (1_000_000_000 - start % 1_000_000_000)
    assert exclusive_deadline - start == 1_000_000_000
    assert start + 7_926_243 < exclusive_deadline


def test_production_usb_frozen_correction_input():
    import subprocess

    from wsprrypi_qualification.pico_campaign import check_prepared_correction
    from wsprrypi_qualification.pico_wire import validate_diagnostic

    build = os.environ.get("WSPQ_PICO_TEST_BUILD")
    if build is None:
        pytest.skip("Production Pico fixture requires explicit selected source build")
    response = subprocess.run(
        [str(Path(build) / "frequency_policy_tests"), "--frozen-input-fixture"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    diagnostic = json.loads(response.stdout)
    validate_diagnostic(diagnostic)
    plan, prepared, bracket, _ = frozen_input_case()
    prepared["rf-calibration"] = diagnostic
    prepared["target"] = diagnostic["target"]
    for document in [prepared["gps"], *bracket]:
        document["target"] = diagnostic["target"]
    check_prepared_correction(
        plan,
        prepared,
        [prepared["gps"]],
        job_id=diagnostic["selection"]["prepared_input"]["job_id"],
        load_bracket=bracket,
    )
