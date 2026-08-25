"""Canonical hardware-free defaults for generic keyed CW-family tests."""

from __future__ import annotations

import math

CANONICAL_KEYED_TEST_MESSAGE = "ETE"
CANONICAL_KEYED_TEST_DOT_SECONDS = 0.7
CANONICAL_KEYED_TEST_SEPARATION_HZ = 5.0
KEYED_MESSAGES_PER_TRANSACTION = 1
KEYED_QUALIFICATION_TRANSACTION_COUNT = 3

_KEYED_MODES = frozenset({"qrss", "fskcw", "dfcw"})


def hardware_free_keyed_protocol(
    mode: str,
    *,
    primary_frequency_hz: float,
    pre_quiet_seconds: float,
    post_quiet_seconds: float,
    message: str = CANONICAL_KEYED_TEST_MESSAGE,
    dot_seconds: float = CANONICAL_KEYED_TEST_DOT_SECONDS,
    frequency_separation_hz: float | None = None,
) -> dict[str, object]:
    """Build one explicit protocol fragment for a hardware-free test transaction.

    This helper deliberately does not construct a resolved or live plan. Hardware,
    RF-path, authorization, deadline, cleanup, quiescence, and primary-frequency
    facts remain mandatory inputs at their existing plan boundaries.
    """
    if mode not in _KEYED_MODES:
        raise ValueError("canonical keyed test defaults support QRSS, FSKCW, and DFCW")
    if not message.strip():
        raise ValueError("keyed test message must not be empty")
    numeric_values = (primary_frequency_hz, pre_quiet_seconds, post_quiet_seconds, dot_seconds)
    if any(not math.isfinite(value) or value <= 0 for value in numeric_values):
        raise ValueError("keyed test frequencies and durations must be finite and positive")
    if mode == "qrss":
        if frequency_separation_hz is not None:
            raise ValueError("frequency separation applies only to FSKCW and DFCW")
        secondary_frequency_hz = None
    else:
        separation = (
            CANONICAL_KEYED_TEST_SEPARATION_HZ
            if frequency_separation_hz is None
            else frequency_separation_hz
        )
        if not math.isfinite(separation) or separation <= 0:
            raise ValueError("keyed test frequency separation must be finite and positive")
        secondary_frequency_hz = primary_frequency_hz - separation
        if secondary_frequency_hz <= 0:
            raise ValueError("keyed test secondary frequency must remain positive")
    dfcw = mode == "dfcw"
    return {
        "definition": "wsprrypi-dfcw@v1" if dfcw else f"wspq-{mode}@v1",
        "message": message,
        "dot_seconds": dot_seconds,
        "repetitions": KEYED_MESSAGES_PER_TRANSACTION,
        "primary_frequency_hz": primary_frequency_hz,
        "secondary_frequency_hz": secondary_frequency_hz,
        "pre_quiet_seconds": pre_quiet_seconds,
        "post_quiet_seconds": post_quiet_seconds,
        "intra_element_gap_units": 0.333333 if dfcw else 1.0,
        "inter_character_gap_units": 1.0 if dfcw else 3.0,
        "inter_word_gap_units": 3.0 if dfcw else 7.0,
        "tone_cycles": None,
        "tone_on_seconds": None,
        "tone_off_seconds": None,
    }
