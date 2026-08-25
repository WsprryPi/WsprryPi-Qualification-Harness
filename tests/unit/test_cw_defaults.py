from __future__ import annotations

from typing import Any

import pytest

from wsprrypi_qualification.cw_defaults import (
    CANONICAL_KEYED_TEST_DOT_SECONDS,
    CANONICAL_KEYED_TEST_MESSAGE,
    CANONICAL_KEYED_TEST_SEPARATION_HZ,
    KEYED_MESSAGES_PER_TRANSACTION,
    KEYED_QUALIFICATION_TRANSACTION_COUNT,
    hardware_free_keyed_protocol,
)
from wsprrypi_qualification.cw_reference import ReferenceEncoderError, generate_expected_events


def _plan(mode: str, **overrides: Any) -> dict[str, Any]:
    protocol = hardware_free_keyed_protocol(
        mode,
        primary_frequency_hz=14_097_105.0,
        pre_quiet_seconds=1.0,
        post_quiet_seconds=1.0,
        **overrides,
    )
    return {
        "mode": mode,
        "protocol": protocol,
        "capture_contract": {"sample_count": 10_000, "sample_rate_hz": 1_000},
    }


def _message_events(mode: str) -> list[dict[str, Any]]:
    return generate_expected_events(_plan(mode))[1:-1]


@pytest.mark.parametrize("mode", ["qrss", "fskcw", "dfcw"])
def test_generic_hardware_free_scenario_selects_canonical_defaults(mode: str) -> None:
    protocol = _plan(mode)["protocol"]
    assert isinstance(protocol, dict)
    assert protocol["message"] == CANONICAL_KEYED_TEST_MESSAGE == "ETE"
    assert protocol["dot_seconds"] == CANONICAL_KEYED_TEST_DOT_SECONDS == 0.7
    assert protocol["repetitions"] == KEYED_MESSAGES_PER_TRANSACTION == 1
    assert KEYED_QUALIFICATION_TRANSACTION_COUNT == 3
    secondary = protocol["secondary_frequency_hz"]
    if mode == "qrss":
        assert secondary is None
    else:
        assert float(protocol["primary_frequency_hz"]) - float(secondary) == (
            CANONICAL_KEYED_TEST_SEPARATION_HZ
        )


def test_explicit_hardware_free_overrides_remain_available() -> None:
    protocol = hardware_free_keyed_protocol(
        "fskcw",
        primary_frequency_hz=10_140_100.0,
        pre_quiet_seconds=2.0,
        post_quiet_seconds=3.0,
        message="A",
        dot_seconds=1.2,
        frequency_separation_hz=7.5,
    )
    assert protocol["message"] == "A"
    assert protocol["dot_seconds"] == 1.2
    assert protocol["secondary_frequency_hz"] == 10_140_092.5
    assert protocol["pre_quiet_seconds"] == 2.0
    assert protocol["post_quiet_seconds"] == 3.0


def test_qrss_ete_timing_is_dot_gap_dash_gap_dot() -> None:
    events = _message_events("qrss")
    assert [event["role"] for event in events] == [
        "dot",
        "inter_character_gap",
        "dash",
        "inter_character_gap",
        "dot",
    ]
    assert [event["end_s"] - event["start_s"] for event in events] == pytest.approx(
        [0.7, 2.1, 2.1, 2.1, 0.7]
    )
    assert [event["rf_state"] for event in events] == [
        "primary",
        "off",
        "primary",
        "off",
        "primary",
    ]


def test_fskcw_ete_is_continuous_for_7_7_seconds_with_five_hertz_shift() -> None:
    events = _message_events("fskcw")
    assert [event["role"] for event in events] == ["mark", "space", "mark", "space", "mark"]
    assert all(event["continuity_required"] is True for event in events)
    assert all(event["rf_state"] in {"primary", "secondary"} for event in events)
    assert events[-1]["end_s"] - events[0]["start_s"] == pytest.approx(7.7)
    assert abs(events[0]["frequency_hz"] - events[1]["frequency_hz"]) == 5.0


def test_dfcw_ete_has_three_active_frequency_observations() -> None:
    events = _message_events("dfcw")
    assert [event["role"] for event in events] == [
        "dot",
        "inter_character_gap",
        "dash",
        "inter_character_gap",
        "dot",
    ]
    assert [event["end_s"] - event["start_s"] for event in events] == pytest.approx([0.7] * 5)
    assert [event["rf_state"] for event in events] == [
        "primary",
        "off",
        "secondary",
        "off",
        "primary",
    ]
    assert abs(events[0]["frequency_hz"] - events[2]["frequency_hz"]) == 5.0


def test_hardware_free_defaults_cannot_supply_a_resolved_or_live_plan() -> None:
    with pytest.raises(TypeError):
        hardware_free_keyed_protocol(  # type: ignore[call-arg]
            "qrss", pre_quiet_seconds=1.0, post_quiet_seconds=1.0
        )
    protocol = hardware_free_keyed_protocol(
        "qrss",
        primary_frequency_hz=14_097_105.0,
        pre_quiet_seconds=1.0,
        post_quiet_seconds=1.0,
    )
    assert not {
        "backend",
        "hardware_profile",
        "transmitter",
        "receiver",
        "rf_path",
        "authorization",
        "deadlines",
        "cleanup",
        "quiescence",
    }.intersection(protocol)


def test_special_case_validation_vectors_are_not_canonicalized() -> None:
    protocol = _plan("dfcw")["protocol"]
    assert isinstance(protocol, dict)
    protocol["intra_element_gap_units"] = 1.0
    with pytest.raises(ReferenceEncoderError, match="reviewed gap"):
        generate_expected_events(_plan("dfcw") | {"protocol": protocol})
