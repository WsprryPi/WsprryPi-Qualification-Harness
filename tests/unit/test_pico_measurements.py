"""Actual Pico measurement writers and their acquired-evidence contracts; no hardware."""

import copy
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tests.unit.test_acquired_offline import metadata
from tests.unit.test_pico_campaign import configuration, make_plan
from wsprrypi_qualification.cw_contracts import (
    CwContractError,
    _validate_events,
    _validate_gate,
    _validate_observations,
)
from wsprrypi_qualification.cw_iq import generate_synthetic_iq
from wsprrypi_qualification.cw_reference import generate_expected_events, write_expected_events
from wsprrypi_qualification.offline import (
    OfflineAnalysisError,
    artifact,
    validate_document,
    write_json_new,
)
from wsprrypi_qualification.pico_campaign import (
    cw_analysis_plan,
    measurement_binding,
    validate_measurement_binding,
)
from wsprrypi_qualification.pico_live import PicoLiveAdapter
from wsprrypi_qualification.pico_results import (
    _validate_analysis_identity,
    _validate_carrier_binding,
)
from wsprrypi_qualification.pico_wire import PicoWireError


def adapter_for(engine):
    config = configuration(engine)
    config["receiver"].update(
        driver="sdrplay", serial="2404058C60", gain_db=10, wsprd=sys.executable
    )
    adapter = object.__new__(PicoLiveAdapter)
    adapter.plan = make_plan(config)
    adapter.tools = {"wsprd": artifact(Path(sys.executable))}
    return adapter


def capture_for(directory, name, path, count):
    native = metadata(directory, name + "-native.json", path, rate=12000, center=14094000)
    return SimpleNamespace(
        output_path=path,
        metadata_path=native,
        sample_rate_hz=12000,
        center_frequency_hz=14094000,
        sample_count=count,
    )


@pytest.mark.parametrize("engine", ["pio-dma-gp2", "si5351"])
def test_actual_pico_tone_analyzers_emit_bound_acquired_evidence(tmp_path, engine):
    adapter = adapter_for(engine)
    observation = tmp_path / "observation"
    observation.mkdir()
    seed = tmp_path / "independent signal fixture"
    seed.mkdir()
    lead = 7.287371873855591
    cw = cw_analysis_plan(adapter.plan, "tone", "20260815T120000Z-pico-fixture", lead)
    cw["capture_contract"]["sample_count"] = 480000
    write_json_new(seed / "plan.json", cw, schema_name="cw-mode-plan.schema.json")
    write_expected_events(seed / "plan.json", seed / "events.json", source_revision="a" * 40)
    on_path = observation / "on.cf32"
    generate_synthetic_iq(
        seed / "plan.json", seed / "events.json", on_path, seed / "synthetic.json", seed=71
    )
    off_path = observation / "off.cf32"
    np.zeros(24000, dtype="<c8").tofile(off_path)
    on = capture_for(observation, "on", on_path, 480000)
    off = capture_for(observation, "off", off_path, 24000)
    for name, capture in (("rf-on", on), ("rf-off", off)):
        (observation / (name + "-plan.json")).write_text(
            json.dumps(
                {
                    "output_path": str(capture.output_path),
                    "metadata_path": str(capture.metadata_path),
                }
            )
        )
    snapshot = {
        "si5351": {"boot_active": {"drive_ma": 8}},
        "status": {"standalone": {"gpio_drive_ma": 12}},
        "ntp-observations": {"nominal_sample_hz": 138000000},
    }
    result = adapter.analyze("tone", observation, snapshot, on, off, None, lead)
    assert result["outcome"] == "passed"
    check_actual_analysis_identity(observation, adapter.plan)
    carrier = _validate_carrier_binding(observation, adapter.plan)
    assert carrier["contract"]["profiles"] == measurement_binding(adapter.plan)
    original = (observation / "carrier.json").read_text()
    carrier["inputs"]["rf_on"]["sha256"] = carrier["inputs"]["rf_off"]["sha256"]
    (observation / "carrier.json").write_text(json.dumps(carrier))
    with pytest.raises(PicoWireError, match="independent Pico capture"):
        _validate_carrier_binding(observation, adapter.plan)
    (observation / "carrier.json").write_text(original)
    changed_plan = copy.deepcopy(adapter.plan)
    changed_plan["configuration"]["protocol"]["frequency_hz"] += 1
    with pytest.raises(PicoWireError, match="frozen Pico plan"):
        _validate_carrier_binding(observation, changed_plan)


@pytest.mark.parametrize("engine", ["pio-dma-gp2", "si5351"])
def test_actual_pico_wspr_audio_and_decoder_writers_retain_native_binding(tmp_path, engine):
    # Python deliberately acts as a non-decoder: real process execution and
    # artifact publication must produce a valid blocked result, never a decode pass.
    adapter = adapter_for(engine)
    path = tmp_path / "silent fixture.cf32"
    np.zeros(12000 * 140, dtype="<c8").tofile(path)
    capture = capture_for(tmp_path, "wspr", path, 12000 * 140)
    result = adapter.analyze(
        "wspr", tmp_path, {}, capture, capture, datetime(2026, 8, 11, 12, 2, tzinfo=UTC), 5
    )
    assert result["outcome"] == "blocked"
    for filename, schema in (
        ("audio.json", "audio-conversion"),
        ("decode.json", "decoder-evidence"),
    ):
        document = json.loads((tmp_path / filename).read_text())
        validate_document(document, schema + ".schema.json")
        validate_measurement_binding(document["profiles"], adapter.plan)


@pytest.mark.parametrize(
    "mutation", ["extra", "missing", "wrong_target", "bad_digest", "wrong_plan"]
)
def test_pico_measurement_binding_rejects_unknown_or_mismatched_context(mutation):
    plan = make_plan()
    binding = measurement_binding(plan)
    if mutation == "extra":
        binding["bench"] = {}
    elif mutation == "missing":
        del binding["configuration_id"]
    elif mutation == "wrong_target":
        binding["target_kind"] = "pi"
    elif mutation == "bad_digest":
        binding["plan_sha256"] = "0" * 63
    else:
        binding["plan_sha256"] = "0" * 64
    with pytest.raises((OfflineAnalysisError, PicoWireError)):
        validate_measurement_binding(binding, plan)


def check_actual_analysis_identity(directory, plan):
    expected = json.loads((directory / "expected.json").read_text())
    measured = json.loads((directory / "observations.json").read_text())
    assert "synthetic" not in measured["analyzer"]
    assert measured["capture"]["synthetic"] is False
    _validate_analysis_identity(plan, expected, measured)
    cw = json.loads((directory / "mode-plan.json").read_text())
    gate = json.loads((directory / "gate.json").read_text())
    _validate_events(cw, expected)
    _validate_observations(cw, expected, measured, directory / "observations.json")
    _validate_gate(cw["mode"], measured, gate)
    key = (
        "shifted_frequency_model"
        if cw["mode"] in {"fskcw", "dfcw"}
        else "unshifted_frequency_model"
    )
    changed = copy.deepcopy(measured)
    changed["measurement_summary"][key]["acquisition_offset_gate_hz"] -= 1
    with pytest.raises(CwContractError, match="model"):
        _validate_observations(cw, expected, changed, directory / "observations.json")
    for mutation in (
        "synthetic_capture",
        "synthetic_analyzer",
        "extra_false",
        "generator",
        "analyzer",
        "missing",
    ):
        changed_expected, changed_measured = copy.deepcopy(expected), copy.deepcopy(measured)
        if mutation == "synthetic_capture":
            changed_measured["capture"]["synthetic"] = True
        elif mutation in ("synthetic_analyzer", "extra_false"):
            changed_measured["analyzer"]["synthetic"] = mutation == "synthetic_analyzer"
        elif mutation == "generator":
            changed_expected["generator"]["source_revision"] = "b" * 40
        elif mutation == "analyzer":
            changed_measured["analyzer"]["source_revision"] = "b" * 40
        else:
            del changed_measured["capture"]["synthetic"]
        with pytest.raises((PicoWireError, OfflineAnalysisError)):
            _validate_analysis_identity(plan, changed_expected, changed_measured)


@pytest.mark.parametrize("engine", ["pio-dma-gp2", "si5351"])
@pytest.mark.parametrize("mode", ["qrss", "fskcw", "dfcw"])
def test_actual_keyed_writer_output_passes_closed_pico_identity_validation(tmp_path, engine, mode):
    # Independent synthetic signal fixture exercises the acquired-output writer
    # and validator together. It does not claim physical acquisition or RF.
    adapter = adapter_for(engine)
    observation, seed = tmp_path / "observation", tmp_path / "independent fixture"
    observation.mkdir()
    seed.mkdir()
    lead = 7.287371873855591
    cw = cw_analysis_plan(adapter.plan, mode, "20260815T120000Z-writer-fixture", lead)
    count = math.ceil((max(e["end_s"] for e in generate_expected_events(cw)) + 2) * 12000)
    cw["capture_contract"]["sample_count"] = count
    write_json_new(seed / "plan.json", cw, schema_name="cw-mode-plan.schema.json")
    write_expected_events(seed / "plan.json", seed / "events.json", source_revision="a" * 40)
    on_path, off_path = observation / "on.cf32", observation / "off.cf32"
    generate_synthetic_iq(
        seed / "plan.json", seed / "events.json", on_path, seed / "synthetic.json", seed=71
    )
    np.zeros(24000, dtype="<c8").tofile(off_path)
    on, off = (
        capture_for(observation, "on", on_path, count),
        capture_for(observation, "off", off_path, 24000),
    )
    snapshot = {
        "si5351": {"boot_active": {"drive_ma": 8}},
        "status": {"standalone": {"gpio_drive_ma": 12}},
        "ntp-observations": {"nominal_sample_hz": 138000000},
    }
    result = adapter.analyze(mode, observation, snapshot, on, off, None, lead)
    assert result["outcome"] == "passed"
    check_actual_analysis_identity(observation, adapter.plan)
