from datetime import UTC, datetime, timedelta

import pytest

from wsprrypi_qualification.transmitter_ppm import TransmitterPpmError, resolve_transmitter_ppm

NOW = datetime(2026, 8, 25, tzinfo=UTC)


def source(kind: str, value: float, **extra: object) -> dict[str, object]:
    return {
        "source_type": kind,
        "source_location": "C:\\Program Data\\WsprryPi\\settings.ini",
        "value_ppm": value,
        "host": "tx host",
        "backend": "gpio",
        "acquired_utc": None,
        **extra,
    }


def test_manual_plus_harness_delta_and_sign() -> None:
    result = resolve_transmitter_ppm(
        [source("manual_host_ppm", 2.5)],
        -0.75,
        transmitter_host="tx host",
        backend="gpio",
        resolved_at=NOW,
    )
    assert result["effective_correction_ppm"] == 1.75
    assert result["application"] == "exactly_once_as_backend_ppm_argument"


def test_fresh_tracked_supersedes_manual_then_composes_offset() -> None:
    tracked = source(
        "tracked_host_ppm",
        -3.0,
        acquired_utc=(NOW - timedelta(seconds=10)).isoformat(),
        maximum_age_s=60,
    )
    result = resolve_transmitter_ppm(
        [source("manual_host_ppm", 9.0), tracked],
        0.5,
        transmitter_host="tx host",
        backend="gpio",
        resolved_at=NOW,
    )
    assert result["effective_correction_ppm"] == -2.5
    assert [item["decision"] for item in result["contributors"][:2]] == ["superseded", "applied"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -201, 201])
def test_rejects_invalid_values(value: float) -> None:
    with pytest.raises(TransmitterPpmError):
        resolve_transmitter_ppm(
            [source("manual_host_ppm", value)],
            0,
            transmitter_host="tx host",
            backend="gpio",
            resolved_at=NOW,
        )


def test_rejects_stale_ambiguous_and_wrong_identity() -> None:
    stale = source(
        "tracked_host_ppm",
        1.0,
        acquired_utc=(NOW - timedelta(seconds=61)).isoformat(),
        maximum_age_s=60,
    )
    with pytest.raises(TransmitterPpmError, match="stale"):
        resolve_transmitter_ppm(
            [stale], 0, transmitter_host="tx host", backend="gpio", resolved_at=NOW
        )
    with pytest.raises(TransmitterPpmError, match="ambiguous"):
        resolve_transmitter_ppm(
            [source("manual_host_ppm", 1), source("manual_host_ppm", 1)],
            0,
            transmitter_host="tx host",
            backend="gpio",
            resolved_at=NOW,
        )
    with pytest.raises(TransmitterPpmError, match="identity"):
        resolve_transmitter_ppm(
            [source("manual_host_ppm", 1)],
            0,
            transmitter_host="other",
            backend="gpio",
            resolved_at=NOW,
        )


@pytest.mark.parametrize("carrier", [0.0, 250.0])
def test_complete_overrides_accept_valid_carrier_tolerances(carrier: float) -> None:
    from wsprrypi_qualification.complete_test import CompleteTestOverrides

    assert (
        CompleteTestOverrides(carrier_offset_max_hz=carrier).validated()["carrier_offset_max_hz"]
        == carrier
    )


@pytest.mark.parametrize("carrier", [-1.0, float("nan"), float("inf")])
def test_complete_overrides_reject_invalid_carrier_tolerances(carrier: float) -> None:
    from wsprrypi_qualification.complete_test import CompleteTestError, CompleteTestOverrides

    with pytest.raises(CompleteTestError):
        CompleteTestOverrides(carrier_offset_max_hz=carrier).validated()


@pytest.mark.parametrize("ppm", [float("nan"), float("inf"), -201.0, 201.0])
def test_gpio_manual_ppm_rejects_nonfinite_or_out_of_range(ppm: float) -> None:
    from wsprrypi_qualification.complete_test import CompleteTestError, CompleteTestOverrides

    with pytest.raises(CompleteTestError, match="gpio-manual-ppm"):
        CompleteTestOverrides(gpio_manual_ppm=ppm).validated()
