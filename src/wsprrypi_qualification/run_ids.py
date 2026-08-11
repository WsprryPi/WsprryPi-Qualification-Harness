"""Deterministic, cross-platform-safe run identifiers."""

import re
from datetime import datetime

from wsprrypi_qualification.timing import require_aware_utc

IDENTIFIER_PATTERN_TEXT = r"^[a-z0-9](?:[a-z0-9._-]{0,61}[a-z0-9_-])$"
IDENTIFIER_PATTERN = re.compile(IDENTIFIER_PATTERN_TEXT)
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def validate_identifier(identifier: str, field_name: str = "identifier") -> str:
    if not IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError(
            f"{field_name} must contain 2-63 portable lowercase letters, digits, dots, "
            "underscores, or hyphens and must not end with a dot"
        )
    if ".." in identifier:
        raise ValueError(f"{field_name} must not contain path traversal components")
    base_name = identifier.split(".", 1)[0]
    if base_name in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{field_name} must not use a reserved Windows device name")
    return identifier


def validate_test_id(test_id: str) -> str:
    return validate_identifier(test_id, "test_id")


def generate_run_id(started: datetime, test_id: str) -> str:
    utc = require_aware_utc(started)
    safe_test_id = validate_test_id(test_id)
    return f"{utc:%Y%m%dT%H%M%SZ}-{safe_test_id}"
