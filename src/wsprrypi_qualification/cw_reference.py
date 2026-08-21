"""Pure reference encoders for tone and CW-family expected events."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from wsprrypi_qualification.offline import (
    FailureCause,
    OfflineAnalysisError,
    artifact,
    load_json_document,
    write_json_new,
)

GENERATOR_NAME = "wsprrypi-qualification-cw-reference"
GENERATOR_VERSION = "1"
DFCW_V1_DEFINITION = "wsprrypi-dfcw@v1"
DFCW_V1_GAPS = (Decimal("0.333333"), Decimal("1"), Decimal("3"))

MORSE: dict[str, str] = {
    "A": ".-",
    "B": "-...",
    "C": "-.-.",
    "D": "-..",
    "E": ".",
    "F": "..-.",
    "G": "--.",
    "H": "....",
    "I": "..",
    "J": ".---",
    "K": "-.-",
    "L": ".-..",
    "M": "--",
    "N": "-.",
    "O": "---",
    "P": ".--.",
    "Q": "--.-",
    "R": ".-.",
    "S": "...",
    "T": "-",
    "U": "..-",
    "V": "...-",
    "W": ".--",
    "X": "-..-",
    "Y": "-.--",
    "Z": "--..",
    "0": "-----",
    "1": ".----",
    "2": "..---",
    "3": "...--",
    "4": "....-",
    "5": ".....",
    "6": "-....",
    "7": "--...",
    "8": "---..",
    "9": "----.",
    "/": "-..-.",
    "?": "..--..",
    ".": ".-.-.-",
    ",": "--..--",
    "-": "-....-",
    "+": ".-.-.",
    "=": "-...-",
}


class ReferenceEncoderError(OfflineAnalysisError):
    """A normalized plan cannot produce a supported reference timeline."""


def _fail(message: str) -> None:
    raise ReferenceEncoderError(message, cause=FailureCause.INVALID_ARGUMENTS)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    return int(integral) if value == integral else float(value)


def _words(message: str) -> list[list[tuple[int, str, str]]]:
    words: list[list[tuple[int, str, str]]] = []
    current: list[tuple[int, str, str]] = []
    for position, raw in enumerate(message):
        if not raw.isascii():
            _fail(f"unsupported Morse character at message position {position}")
        if raw in " \t\n\r\v\f":
            if current:
                words.append(current)
                current = []
            continue
        character = raw.upper()
        code = MORSE.get(character)
        if code is None:
            _fail(f"unsupported Morse character at message position {position}")
        assert code is not None
        current.append((position, character, code))
    if current:
        words.append(current)
    if not words:
        _fail("message contains no encodable character")
    return words


def _validate_definition(plan: dict[str, Any]) -> None:
    mode = plan["mode"]
    definition = plan["protocol"]["definition"]
    expected = DFCW_V1_DEFINITION if mode == "dfcw" else f"wspq-{mode}@v1"
    if definition != expected:
        _fail(f"unsupported {mode} protocol definition: {definition}")
    primary = _decimal(plan["protocol"]["primary_frequency_hz"])
    secondary_value = plan["protocol"]["secondary_frequency_hz"]
    if mode == "fskcw":
        assert secondary_value is not None
        if primary <= _decimal(secondary_value):
            _fail("wspq-fskcw@v1 requires primary mark above secondary space")
    if mode == "dfcw":
        assert secondary_value is not None
        if primary == _decimal(secondary_value):
            _fail("wsprrypi-dfcw@v1 requires distinct dot and dash frequencies")
    if mode == "dfcw":
        protocol = plan["protocol"]
        gaps = tuple(
            _decimal(protocol[name])
            for name in (
                "intra_element_gap_units",
                "inter_character_gap_units",
                "inter_word_gap_units",
            )
        )
        if gaps != DFCW_V1_GAPS:
            _fail("wsprrypi-dfcw@v1 requires reviewed gap multipliers 0.333333, 1, and 3")


def generate_expected_events(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the exact expected events for one already schema-validated plan."""
    _validate_definition(plan)
    protocol = plan["protocol"]
    mode = plan["mode"]
    primary = protocol["primary_frequency_hz"]
    secondary = protocol["secondary_frequency_hz"]
    offset = Decimal("0")
    events: list[dict[str, Any]] = []

    def emit(
        *,
        repetition: int | None,
        position: int | None,
        symbol: str | None,
        role: str,
        duration: Decimal,
        state: str,
        frequency: int | float | None,
        continuous: bool,
    ) -> None:
        nonlocal offset
        if duration <= 0:
            _fail("all expected-event durations must be positive")
        end = offset + duration
        events.append(
            {
                "index": len(events),
                "repetition": repetition,
                "message_position": position,
                "symbol": symbol,
                "role": role,
                "start_s": _number(offset),
                "end_s": _number(end),
                "rf_state": state,
                "frequency_hz": frequency,
                "continuity_required": continuous,
            }
        )
        offset = end

    if mode == "tone":
        off = _decimal(protocol["tone_off_seconds"])
        on = _decimal(protocol["tone_on_seconds"])
        for _cycle in range(protocol["tone_cycles"]):
            emit(
                repetition=None,
                position=None,
                symbol=None,
                role="quiet",
                duration=off,
                state="off",
                frequency=None,
                continuous=False,
            )
            emit(
                repetition=None,
                position=None,
                symbol=None,
                role="carrier",
                duration=on,
                state="primary",
                frequency=primary,
                continuous=True,
            )
        emit(
            repetition=None,
            position=None,
            symbol=None,
            role="quiet",
            duration=off,
            state="off",
            frequency=None,
            continuous=False,
        )
    else:
        emit(
            repetition=None,
            position=None,
            symbol=None,
            role="quiet",
            duration=_decimal(protocol["pre_quiet_seconds"]),
            state="off",
            frequency=None,
            continuous=False,
        )
        words = _words(protocol["message"])
        dot = _decimal(protocol["dot_seconds"])
        gaps = {
            "intra_element_gap": dot * _decimal(protocol["intra_element_gap_units"]),
            "inter_character_gap": dot * _decimal(protocol["inter_character_gap_units"]),
            "inter_word_gap": dot * _decimal(protocol["inter_word_gap_units"]),
        }
        repetitions = protocol["repetitions"]
        for repetition in range(repetitions):
            for word_index, word in enumerate(words):
                for character_index, (position, character, code) in enumerate(word):
                    for element_index, element in enumerate(code):
                        if mode in {"cw", "qrss"}:
                            duration = dot if element == "." else dot * 3
                            state, frequency, continuous = "primary", primary, False
                        elif mode == "fskcw":
                            duration = dot if element == "." else dot * 3
                            state, frequency, continuous = "primary", primary, True
                        else:
                            duration = dot
                            state = "primary" if element == "." else "secondary"
                            frequency = primary if element == "." else secondary
                            continuous = False
                        emit(
                            repetition=repetition,
                            position=position,
                            symbol=element,
                            role="mark" if mode == "fskcw" else "dot" if element == "." else "dash",
                            duration=duration,
                            state=state,
                            frequency=frequency,
                            continuous=continuous,
                        )
                        if element_index + 1 < len(code):
                            _emit_gap(
                                emit,
                                mode,
                                repetition,
                                position,
                                character,
                                "intra_element_gap",
                                gaps["intra_element_gap"],
                                secondary,
                            )
                    if character_index + 1 < len(word):
                        _emit_gap(
                            emit,
                            mode,
                            repetition,
                            position,
                            character,
                            "inter_character_gap",
                            gaps["inter_character_gap"],
                            secondary,
                        )
                if word_index + 1 < len(words):
                    position = word[-1][0] + 1
                    _emit_gap(
                        emit,
                        mode,
                        repetition,
                        position,
                        None,
                        "inter_word_gap",
                        gaps["inter_word_gap"],
                        secondary,
                    )
            if repetition + 1 < repetitions:
                _emit_gap(
                    emit,
                    mode,
                    repetition,
                    None,
                    None,
                    "inter_word_gap",
                    gaps["inter_word_gap"],
                    secondary,
                )
        emit(
            repetition=None,
            position=None,
            symbol=None,
            role="quiet",
            duration=_decimal(protocol["post_quiet_seconds"]),
            state="off",
            frequency=None,
            continuous=False,
        )

    capture_duration = _decimal(plan["capture_contract"]["sample_count"]) / _decimal(
        plan["capture_contract"]["sample_rate_hz"]
    )
    if offset > capture_duration:
        _fail("generated expected timeline extends beyond the planned capture duration")
    return events


def _emit_gap(
    emit: Any,
    mode: str,
    repetition: int,
    position: int | None,
    symbol: str | None,
    role: str,
    duration: Decimal,
    secondary: int | float | None,
) -> None:
    shifted = mode == "fskcw"
    emit(
        repetition=repetition,
        position=position,
        symbol=symbol,
        role="space" if shifted else role,
        duration=duration,
        state="secondary" if shifted else "off",
        frequency=secondary if shifted else None,
        continuous=shifted,
    )


def write_expected_events(
    plan_path: Path, output_path: Path, *, source_revision: str
) -> dict[str, Any]:
    """Validate a plan and atomically create its bound expected-event document."""
    plan = load_json_document(plan_path, "cw-mode-plan.schema.json")
    document = {
        "schema_version": 1,
        "evidence_type": "cw_expected_events",
        "run_id": plan["run_id"],
        "mode": plan["mode"],
        "plan": artifact(plan_path),
        "generator": {
            "origin": "harness_generated",
            "name": GENERATOR_NAME,
            "version": GENERATOR_VERSION,
            "source_revision": source_revision,
        },
        "protocol_definition": plan["protocol"]["definition"],
        "events": generate_expected_events(plan),
    }
    write_json_new(output_path, document, schema_name="cw-expected-events.schema.json")
    return document
