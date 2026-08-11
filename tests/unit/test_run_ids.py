from datetime import UTC, datetime, timedelta, timezone

import pytest

from wsprrypi_qualification.run_ids import generate_run_id


def test_run_id_is_deterministic_and_path_safe() -> None:
    value = datetime(2026, 8, 11, 12, 34, 56, tzinfo=UTC)
    assert generate_run_id(value, "si5351-160m") == "20260811T123456Z-si5351-160m"


def test_equivalent_instants_normalize_identically() -> None:
    utc = datetime(2026, 8, 11, 12, 34, 56, tzinfo=UTC)
    local = utc.astimezone(timezone(timedelta(hours=-5)))
    assert generate_run_id(utc, "test-id") == generate_run_id(local, "test-id")


@pytest.mark.parametrize(
    "test_id",
    [
        "../escape",
        "AUpper",
        "x",
        "bad id",
        "bad/name",
        "bad\\name",
        "bad:name",
        "test.",
        "trailing ",
        "bad..path",
        "con",
        "nul.txt",
        "x" * 64,
    ],
)
def test_unsafe_test_ids_rejected(test_id: str) -> None:
    with pytest.raises(ValueError):
        generate_run_id(datetime.now(UTC), test_id)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        generate_run_id(datetime(2026, 8, 11), "test-id")
