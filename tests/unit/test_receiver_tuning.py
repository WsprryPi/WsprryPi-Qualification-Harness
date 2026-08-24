import pytest

from wsprrypi_qualification.receiver_tuning import (
    ReceiverTuningError,
    ReceiverTuningGeometry,
    default_receiver_center_hz,
)


def test_default_receiver_tuning_is_positive_baseband_and_valid() -> None:
    requested = 14_097_100.0
    center = default_receiver_center_hz(requested)
    geometry = ReceiverTuningGeometry(requested, center, 250_000, 200_000).validate()
    assert center == 14_072_100.0
    assert geometry.tuning_offset_hz == 25_000.0
    assert geometry.usable_half_span_hz == 100_000.0


@pytest.mark.parametrize("center", [14_097_100.0, 14_096_000.0, 14_098_200.0])
def test_target_window_overlapping_dc_is_rejected(center: float) -> None:
    with pytest.raises(ReceiverTuningError, match="overlaps"):
        ReceiverTuningGeometry(14_097_100, center, 250_000, 200_000).validate()


@pytest.mark.parametrize("center", [13_996_000.0, 14_198_200.0])
def test_target_window_outside_usable_span_is_rejected(center: float) -> None:
    with pytest.raises(ReceiverTuningError, match="outside"):
        ReceiverTuningGeometry(14_097_100, center, 250_000, 200_000).validate()


def test_positive_and_negative_offsets_are_valid() -> None:
    for center in (14_072_100.0, 14_122_100.0):
        ReceiverTuningGeometry(14_097_100, center, 250_000, 200_000).validate()
