"""Pure UTC WSPR-slot and exact sample-count calculations."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction


def require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def is_even_wspr_slot(value: datetime) -> bool:
    utc = require_aware_utc(value)
    return utc.minute % 2 == 0 and utc.second == 0 and utc.microsecond == 0


def next_even_wspr_slot(value: datetime) -> datetime:
    """Return the current slot when exact, otherwise the next even UTC boundary."""
    utc = require_aware_utc(value)
    if is_even_wspr_slot(utc):
        return utc
    base = utc.replace(second=0, microsecond=0)
    minutes = 2 - (base.minute % 2)
    return base + timedelta(minutes=minutes)


def exact_sample_count(sample_rate_hz: int, duration_s: int) -> int:
    if isinstance(sample_rate_hz, bool) or not isinstance(sample_rate_hz, int):
        raise TypeError("sample_rate_hz must be an integer")
    if isinstance(duration_s, bool) or not isinstance(duration_s, int):
        raise TypeError("duration_s must be an integer")
    if sample_rate_hz <= 0 or duration_s <= 0:
        raise ValueError("sample rate and duration must be positive")
    return sample_rate_hz * duration_s


def sample_index_at_utc(capture_start: datetime, instant: datetime, sample_rate_hz: int) -> int:
    """Map an exact UTC instant to an integral sample boundary without float drift."""
    start = require_aware_utc(capture_start)
    target = require_aware_utc(instant)
    if isinstance(sample_rate_hz, bool) or not isinstance(sample_rate_hz, int):
        raise TypeError("sample_rate_hz must be an integer")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    delta = target - start
    microseconds = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    samples = Fraction(microseconds * sample_rate_hz, 1_000_000)
    if samples.denominator != 1:
        raise ValueError("UTC instant does not fall on an integral sample boundary")
    if samples < 0:
        raise ValueError("UTC instant precedes capture start")
    return samples.numerator


def consecutive_wspr_slots(first_slot: datetime, count: int) -> tuple[datetime, ...]:
    first = require_aware_utc(first_slot)
    if not is_even_wspr_slot(first):
        raise ValueError("first_slot must be an even UTC two-minute boundary")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("count must be a positive integer")
    return tuple(first + timedelta(minutes=2 * index) for index in range(count))
