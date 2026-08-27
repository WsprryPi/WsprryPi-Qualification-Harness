from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from wsprrypi_qualification.cli import main
from wsprrypi_qualification.cw_defaults import hardware_free_keyed_protocol
from wsprrypi_qualification.cw_reference import (
    KEYED_CAPTURE_GUARD_SECONDS,
    ReferenceEncoderError,
    generate_expected_events,
    required_keyed_capture_sample_count,
    validate_keyed_capture_margin,
)


def _plan(mode: str, *, message: str | None = None, repetitions: int | None = None) -> dict:
    tone = mode == "tone"
    resolved_repetitions = 3 if mode == "cw" and repetitions is None else repetitions or 1
    protocol = (
        {
            "definition": "wspq-tone@v1",
            "message": None,
            "dot_seconds": None,
            "repetitions": None,
            "primary_frequency_hz": 14_000_100,
            "secondary_frequency_hz": None,
            "pre_quiet_seconds": None,
            "post_quiet_seconds": None,
            "intra_element_gap_units": None,
            "inter_character_gap_units": None,
            "inter_word_gap_units": None,
            "tone_cycles": 3,
            "tone_on_seconds": 2,
            "tone_off_seconds": 1,
        }
        if tone
        else {
            "definition": "wspq-cw@v1",
            "message": "A B" if message is None else message,
            "dot_seconds": 2,
            "repetitions": resolved_repetitions,
            "primary_frequency_hz": 14_000_100,
            "secondary_frequency_hz": None,
            "pre_quiet_seconds": 2,
            "post_quiet_seconds": 3,
            "intra_element_gap_units": 1,
            "inter_character_gap_units": 3,
            "inter_word_gap_units": 7,
            "tone_cycles": None,
            "tone_on_seconds": None,
            "tone_off_seconds": None,
        }
        if mode == "cw"
        else hardware_free_keyed_protocol(
            mode,
            primary_frequency_hz=14_000_100,
            pre_quiet_seconds=2,
            post_quiet_seconds=3,
            **({} if message is None else {"message": message}),
        )
    )
    if not tone:
        protocol["repetitions"] = resolved_repetitions
    return {
        "schema_version": 1,
        "evidence_type": "resolved_cw_mode_plan",
        "run_id": f"20260815T180000Z-{mode}",
        "mode": mode,
        "backend": "gpio",
        "hardware_profile": "fixture",
        "band": "20m",
        "source": {"parent_revision": "a" * 40, "submodule_revision": "b" * 40},
        "transmitter": {
            "host": "fixture",
            "output": "GPIO4",
            "model": "fixture",
            "drive_value": 2,
            "drive_unit": "mA",
            "clock_reference": "fixture",
        },
        "receiver": {"host": "fixture", "driver": "mock", "device_identity": "fixture"},
        "rf_path": {
            "attenuation_db": 60,
            "filter_state": "fixture",
            "termination": "conducted",
            "antenna_state": "disconnected",
            "safe_input_basis": "synthetic",
        },
        "protocol": protocol,
        "capture_contract": {
            "format": "CF32LE",
            "sample_rate_hz": 1000,
            "center_frequency_hz": 14_000_100,
            "sample_count": 1_000_000,
            "overflow_max": 0,
            "fixed_gain": True,
            "agc_enabled": False,
            "bias_tee_enabled": False,
            "first_read_discarded": True,
        },
        "thresholds": {
            "frequency_acquisition_half_width_hz": 500.0,
            "frequency_tolerance_hz": 1,
            "spacing_tolerance_hz": 1,
            "minimum_contrast_db": 10,
            "timing_tolerance_s": 0.1,
            "maximum_transition_s": 0.2,
            "maximum_alignment_shift_s": 0.5,
            "maximum_clipping_fraction": 0.01,
        },
        "resolved_utc": "2026-08-15T18:00:00Z",
    }


@pytest.mark.parametrize("mode", ["qrss", "fskcw", "dfcw"])
@pytest.mark.parametrize("dot_seconds", [0.7, 0.333333])
def test_keyed_capture_margin_rounds_up_from_final_timeline(mode: str, dot_seconds: float) -> None:
    plan = _plan(mode, message="ETE")
    plan["protocol"]["dot_seconds"] = dot_seconds
    events = generate_expected_events(plan)
    required = required_keyed_capture_sample_count(plan)
    plan["capture_contract"]["sample_count"] = required

    assert validate_keyed_capture_margin(plan) == events
    duration = Decimal(required) / Decimal(plan["capture_contract"]["sample_rate_hz"])
    endpoint = Decimal(str(events[-1]["end_s"]))
    assert duration - endpoint >= KEYED_CAPTURE_GUARD_SECONDS

    plan["capture_contract"]["sample_count"] = required - 1
    with pytest.raises(ReferenceEncoderError, match="guard margin"):
        validate_keyed_capture_margin(plan)


def test_tone_golden_alternates_three_cycles_from_and_to_quiet() -> None:
    events = generate_expected_events(_plan("tone"))
    assert [(event["role"], event["start_s"], event["end_s"]) for event in events] == [
        ("quiet", 0, 1),
        ("carrier", 1, 3),
        ("quiet", 3, 4),
        ("carrier", 4, 6),
        ("quiet", 6, 7),
        ("carrier", 7, 9),
        ("quiet", 9, 10),
    ]


@pytest.mark.parametrize("mode", ["cw", "qrss"])
def test_on_off_morse_golden_has_traceable_repetitions(mode: str) -> None:
    events = generate_expected_events(_plan(mode, message=" e\tT ", repetitions=3))
    assert events[0]["role"] == events[-1]["role"] == "quiet"
    keyed = [event for event in events if event["role"] in {"dot", "dash"}]
    assert [
        (event["repetition"], event["message_position"], event["symbol"]) for event in keyed
    ] == [
        (repetition, position, symbol)
        for repetition in range(3)
        for position, symbol in ((1, "."), (3, "-"))
    ]
    assert all(event["rf_state"] == "off" for event in events if "gap" in event["role"])
    first_repetition_gaps = [
        event for event in events if event["repetition"] == 0 and "gap" in event["role"]
    ]
    assert first_repetition_gaps[0]["role"] == "inter_word_gap"
    assert first_repetition_gaps[0]["message_position"] == 2


def test_fskcw_uses_mark_for_elements_and_continuous_space_for_gaps() -> None:
    events = generate_expected_events(_plan("fskcw", message="EE"))
    internal = events[1:-1]
    assert [event["role"] for event in internal[:3]] == ["mark", "space", "mark"]
    assert all(event["continuity_required"] for event in internal)
    assert all(event["rf_state"] in {"primary", "secondary"} for event in internal)


def test_dfcw_v1_uses_equal_symbol_durations_two_tones_and_compressed_off_gap() -> None:
    plan = _plan("dfcw", message="A")
    plan["protocol"]["dot_seconds"] = 2
    events = generate_expected_events(plan)
    first_dot, gap, dash = events[1:4]
    assert (
        first_dot["symbol"],
        first_dot["rf_state"],
        first_dot["end_s"] - first_dot["start_s"],
    ) == (".", "primary", 2)
    assert (dash["symbol"], dash["rf_state"], dash["end_s"] - dash["start_s"]) == (
        "-",
        "secondary",
        2,
    )
    assert gap["rf_state"] == "off"
    assert gap["end_s"] - gap["start_s"] == pytest.approx(0.666666)


@pytest.mark.parametrize("message", ["", " \t ", "E@T", "EÉT", "E\u00a0T"])
def test_empty_or_unsupported_message_fails_closed(message: str) -> None:
    with pytest.raises(ReferenceEncoderError, match=r"no encodable|unsupported"):
        generate_expected_events(_plan("cw", message=message))


def test_unknown_definition_and_dfcw_parameter_drift_fail_closed() -> None:
    plan = _plan("cw")
    plan["protocol"]["definition"] = "wspq-cw@v2"
    with pytest.raises(ReferenceEncoderError, match="unsupported"):
        generate_expected_events(plan)
    plan = _plan("dfcw")
    plan["protocol"]["intra_element_gap_units"] = 1
    with pytest.raises(ReferenceEncoderError, match="reviewed gap"):
        generate_expected_events(plan)


def test_shifted_frequency_semantics_fail_closed() -> None:
    plan = _plan("fskcw")
    plan["protocol"]["primary_frequency_hz"] = plan["protocol"]["secondary_frequency_hz"]
    with pytest.raises(ReferenceEncoderError, match="mark above"):
        generate_expected_events(plan)
    plan = _plan("dfcw")
    plan["protocol"]["primary_frequency_hz"] = plan["protocol"]["secondary_frequency_hz"]
    with pytest.raises(ReferenceEncoderError, match="distinct"):
        generate_expected_events(plan)


def test_complete_reviewed_character_repertoire_is_encodable() -> None:
    message = "ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789 /?.,-+="
    plan = _plan("cw", message=message)
    plan["capture_contract"]["sample_count"] = 10_000_000
    events = generate_expected_events(plan)
    positions = {event["message_position"] for event in events if event["symbol"] in {".", "-"}}
    assert positions == {index for index, character in enumerate(message) if character != " "}


def test_capture_bound_and_cli_binding_and_no_overwrite(tmp_path: Path, capsys) -> None:
    plan = _plan("cw", message="E")
    plan["capture_contract"]["sample_count"] = 1
    with pytest.raises(ReferenceEncoderError, match="capture duration"):
        generate_expected_events(plan)
    plan["capture_contract"]["sample_count"] = 1_000_000
    plan_path = tmp_path / "plan.json"
    output = tmp_path / "expected.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    args = [
        "generate-cw-expected-events",
        str(plan_path),
        str(output),
        "--source-revision",
        "c" * 40,
    ]
    assert main(args) == 0
    capsys.readouterr()
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["plan"]["path"] == str(plan_path.resolve())
    assert document["generator"]["source_revision"] == "c" * 40
    assert main(args) == 2
    assert "refusing to overwrite" in capsys.readouterr().err
