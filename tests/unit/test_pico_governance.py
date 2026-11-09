"""Negative controls for the execution-driven software receiver; no physical I/O."""

from types import SimpleNamespace

import numpy as np

from tests.support.pico_governance import noise, render_capture


def test_no_engine_execution_cannot_synthesize_the_requested_signal():
    plan = SimpleNamespace(sample_count=12000, sample_rate_hz=12000, center_frequency_hz=10000)
    assert np.array_equal(render_capture(plan, 0, []), noise(12000))


def test_interrupted_engine_execution_does_not_render_the_remaining_job():
    plan = SimpleNamespace(sample_count=12000, sample_rate_hz=12000, center_frequency_hz=10000)
    execution = dict(
        start_ns="0",
        until_ns="100000000",
        events=[
            dict(
                offset_ns="0", duration_ns="1000000000", rf_on=True, frequency_nhz="11000000000000"
            )
        ],
    )
    samples = render_capture(plan, 0, [execution])
    assert np.mean(np.abs(samples[:1200]) ** 2) > 0.03
    assert np.array_equal(samples[1200:], noise(12000)[1200:])
