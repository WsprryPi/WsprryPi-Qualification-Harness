from datetime import UTC, datetime, timedelta, timezone

import pytest

from wsprrypi_qualification.timing import (
    exact_sample_count,
    is_even_wspr_slot,
    next_even_wspr_slot,
)


def test_even_slot_and_inclusive_boundary() -> None:
    boundary = datetime(2026, 8, 11, 12, 2, tzinfo=UTC)
    assert is_even_wspr_slot(boundary)
    assert next_even_wspr_slot(boundary) == boundary


def test_next_slot_normalizes_offset_to_utc() -> None:
    central = timezone(timedelta(hours=-5))
    value = datetime(2026, 8, 11, 7, 2, 0, 1, tzinfo=central)
    assert next_even_wspr_slot(value) == datetime(2026, 8, 11, 12, 4, tzinfo=UTC)


def test_naive_slot_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_even_wspr_slot(datetime(2026, 8, 11, 12, 0))


def test_exact_historical_sample_count() -> None:
    assert exact_sample_count(250_000, 370) == 92_500_000


@pytest.mark.parametrize("rate,duration", [(250_000.5, 370), (250_000, 0.5), (True, 370)])
def test_fractional_or_boolean_sample_inputs_rejected(rate: object, duration: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        exact_sample_count(rate, duration)  # type: ignore[arg-type]
