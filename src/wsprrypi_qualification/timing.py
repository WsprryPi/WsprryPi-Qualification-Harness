"""Pure UTC WSPR-slot and exact sample-count calculations."""

from datetime import UTC, datetime, timedelta


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
