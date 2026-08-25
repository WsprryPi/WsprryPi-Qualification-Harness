"""Maintained zero-IF avoidance policy for qualification receivers."""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_RECEIVER_TUNING_OFFSET_HZ = 25_000.0
DEFAULT_DC_EXCLUSION_HZ = 1_000.0
DEFAULT_TARGET_SEARCH_HALF_WIDTH_HZ = 500.0


class ReceiverTuningError(ValueError):
    """Receiver geometry cannot contain the complete target acquisition window."""


@dataclass(frozen=True)
class ReceiverTuningGeometry:
    requested_frequency_hz: float
    center_frequency_hz: float
    sample_rate_hz: float
    bandwidth_hz: float
    dc_exclusion_hz: float = DEFAULT_DC_EXCLUSION_HZ
    target_search_half_width_hz: float = DEFAULT_TARGET_SEARCH_HALF_WIDTH_HZ
    fft_bin_hz: float = 0.0

    @property
    def tuning_offset_hz(self) -> float:
        return self.requested_frequency_hz - self.center_frequency_hz

    @property
    def usable_half_span_hz(self) -> float:
        return min(self.sample_rate_hz, self.bandwidth_hz) / 2.0

    def validate(self) -> ReceiverTuningGeometry:
        values = (
            self.requested_frequency_hz,
            self.center_frequency_hz,
            self.sample_rate_hz,
            self.bandwidth_hz,
            self.dc_exclusion_hz,
            self.target_search_half_width_hz,
            self.fft_bin_hz,
        )
        if not all(math.isfinite(value) for value in values):
            raise ReceiverTuningError("receiver tuning geometry requires finite values")
        if min(values[:4]) <= 0 or any(value < 0 for value in values[4:]):
            raise ReceiverTuningError("receiver tuning geometry requires positive dimensions")
        guard = max(self.fft_bin_hz, 0.0)
        distance = abs(self.tuning_offset_hz)
        if distance - self.target_search_half_width_hz <= self.dc_exclusion_hz + guard:
            raise ReceiverTuningError("requested carrier window overlaps the receiver DC exclusion")
        if distance + self.target_search_half_width_hz + guard > self.usable_half_span_hz:
            raise ReceiverTuningError(
                "requested carrier window falls outside the usable receiver span"
            )
        return self

    def to_document(self) -> dict[str, float | str]:
        self.validate()
        return {
            "policy": "zero_if_offset_target_window_v1",
            "requested_frequency_hz": self.requested_frequency_hz,
            "center_frequency_hz": self.center_frequency_hz,
            "tuning_offset_hz": self.tuning_offset_hz,
            "dc_exclusion_hz": self.dc_exclusion_hz,
            "target_search_half_width_hz": self.target_search_half_width_hz,
            "usable_half_span_hz": self.usable_half_span_hz,
        }


def default_receiver_center_hz(requested_frequency_hz: float) -> float:
    """Place the requested carrier at positive baseband, away from zero IF."""
    center = requested_frequency_hz - DEFAULT_RECEIVER_TUNING_OFFSET_HZ
    if center <= 0:
        raise ReceiverTuningError("requested frequency is too low for the maintained offset")
    return center
