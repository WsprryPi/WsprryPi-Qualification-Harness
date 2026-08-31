"""Versioned, hardware-free carrier presence and independent transient evidence.

No expected edge is used to detect an edge. The fixed FIR is three centered
odd boxcars (a compact B-spline); its complete support bounds edge smearing.
All powers are relative, not calibrated. No theoretical CFAR claim is made.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np

SPECIFICATION: dict[str, Any] = {
    "version": 1,
    "filter": "three_centered_odd_boxcars",
    "boxcar_seconds": 0.002,
    "persistence_seconds": 0.010,
    "off_threshold_ratio": 0.5,
    "reference_guard_seconds": 0.020,
    "minimum_reference_seconds": 0.050,
    "acquisition_block_seconds": 0.5,
    "reference_channel_fraction": 0.25,
    "reference_peak_ratio_maximum": 100.0,
    "reference_quarter_ratio_maximum": 10.0,
    "minimum_resolved_samples": 4,
    "ambiguity_power_ratio": 0.5,
    "raw_impulse_noise_ratio": 100.0,
    "transient_coherence_minimum": 0.5,
    "false_alarm_claim": "empirical_only_no_distributional_guarantee",
}


def specification() -> dict[str, Any]:
    encoded = json.dumps(SPECIFICATION, sort_keys=True, separators=(",", ":")).encode()
    return {**SPECIFICATION, "sha256": hashlib.sha256(encoded).hexdigest()}


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return list(
        zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist(), strict=True)
    )


def average(values: np.ndarray, width: int) -> np.ndarray:
    """Centered zero-padded odd boxcar, O(n), identical at chunk boundaries."""
    if width == 1:
        return values.copy()
    half = width // 2
    padded = np.pad(values, (half, half))
    cumulative = np.r_[
        0, np.cumsum(padded, dtype=np.complex128 if np.iscomplexobj(values) else np.float64)
    ]
    result: np.ndarray = (cumulative[width:] - cumulative[:-width]) / width
    return result


def persistent_states(
    power: np.ndarray, high: float, low: float, count: int
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Confirm sustained crossings, retaining onset rather than confirmation time."""
    on_runs = runs(power >= high)
    off_runs = runs(power < low)
    candidates = sorted(
        [(a, b, True) for a, b in on_runs if b - a >= count]
        + [(a, b, False) for a, b in off_runs if b - a >= count]
    )
    active = np.zeros(power.size, dtype=bool)
    edges: list[dict[str, Any]] = []
    state = False
    onset = 0
    stable_end = 0
    for start, end, next_state in candidates:
        if next_state == state:
            stable_end = end
            continue
        # Backdate to the first crossing after the last sustained old state,
        # not the last quiet subrun. Noise in the hysteresis band must not move
        # an already observed onset while confirmation is pending.
        crossing = (
            power[stable_end : start + 1] >= high
            if next_state
            else power[stable_end : start + 1] < low
        )
        indexes = np.flatnonzero(crossing)
        first = stable_end + int(indexes[0]) if indexes.size else start
        if state:
            active[onset:first] = True
        state = next_state
        onset = first
        edges.append(
            {
                "sample": first,
                "transition_start_sample": stable_end,
                "confirmed_sample": start + count - 1,
                "active": state,
            }
        )
        stable_end = end
    if state:
        active[onset:] = True
    return active, edges


def filter_width(rate: float, separation: float) -> int:
    width = max(
        1,
        round(
            min(
                rate * SPECIFICATION["boxcar_seconds"],
                rate / (4 * separation) if separation > 0 else rate,
            )
        ),
    )
    return int(width + 1 - width % 2)


def detect(
    samples: np.ndarray,
    rate: float,
    center: float,
    primary: float,
    secondary: float | None,
    acquisition: float,
    pre_quiet: float,
    minimum_contrast: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Acquire in-window spectral evidence, then measure a carrier-channel envelope."""
    separation = abs(primary - secondary) if secondary is not None else 0.0
    width = filter_width(rate, separation)
    support = 3 * (width - 1) // 2
    guard = max(support, math.ceil(rate * SPECIFICATION["reference_guard_seconds"]))
    ref_end = min(samples.size, math.floor(pre_quiet * rate)) - guard
    ref_start = guard
    issues: list[str] = []
    valid_reference = ref_end - ref_start >= max(
        4, math.ceil(rate * SPECIFICATION["minimum_reference_seconds"])
    )
    # No zero-floor fallback can authorize a pass without a usable reference.
    reference = samples[ref_start:ref_end] if valid_reference else samples[:0]
    block = min(samples.size, max(16, round(rate * SPECIFICATION["acquisition_block_seconds"])))
    frequencies = center + np.fft.fftfreq(block, 1 / rate)
    requested = primary
    selected = np.abs(frequencies - requested) <= acquisition
    if secondary is not None:
        selected |= np.abs(frequencies - secondary) <= acquisition
    selected &= np.abs(frequencies - center) < rate / 2
    spectral = np.zeros(block)
    window = np.hanning(block)
    # Sum power, but require temporal confirmation below: one impulse cannot
    # qualify solely by winning this acquisition search.
    for start in range(0, samples.size - block + 1, block):
        spectral += np.abs(np.fft.fft(samples[start : start + block] * window)) ** 2
    if not np.any(selected):
        issues.append("unresolved_carrier_acquisition")
        acquired = requested
    else:
        peak = int(np.argmax(np.where(selected, spectral, -np.inf)))
        acquired = float(frequencies[peak])
        separation = abs(primary - secondary) if secondary is not None else 0.0
        exclusion = max(20.0, separation + 4 * rate / block)
        competitors = selected & (np.abs(frequencies - acquired) > exclusion)
        if (
            np.any(competitors)
            and float(np.max(spectral[competitors]))
            >= spectral[peak] * SPECIFICATION["ambiguity_power_ratio"]
        ):
            issues.append("ambiguous_carrier_acquisition")
    # For shifted modes, one common channel includes both states. It never
    # changes the independent phase-derived spacing measurements.
    oscillator = np.exp(-2j * np.pi * (acquired - center) * np.arange(samples.size) / rate)
    baseband = samples * oscillator
    for _ in range(3):
        baseband = average(baseband, width)
    channel_power = np.abs(baseband) ** 2
    raw_noise = (
        float(np.median(np.abs(reference.astype(np.complex128)) ** 2) / math.log(2))
        if valid_reference
        else 0.0
    )
    noise = (
        float(np.median(channel_power[ref_start:ref_end]) / math.log(2)) if valid_reference else 0.0
    )
    if not valid_reference or noise <= 0 or raw_noise <= 0:
        issues.append("unusable_noise_reference")
    noise = max(noise, 1e-30)
    raw_noise = max(raw_noise, 1e-30)
    reference_peak_ratio = 0.0
    reference_peak_frequency = None
    if valid_reference:
        # A coherent RF-off feature cannot be normalized away as background.
        n = min(reference.size, block)
        spectrum = np.abs(np.fft.fft(reference[:n] * np.hanning(n))) ** 2
        reference_frequencies = center + np.fft.fftfreq(n, 1 / rate)
        reference_band = np.abs(reference_frequencies - acquired) <= min(
            acquisition, rate / width * SPECIFICATION["reference_channel_fraction"]
        )
        spectrum = spectrum[reference_band]
        if spectrum.size:
            reference_peak_ratio = float(np.max(spectrum)) / max(float(np.median(spectrum)), 1e-30)
            reference_peak_frequency = float(
                reference_frequencies[reference_band][np.argmax(spectrum)]
            )
        if reference_peak_ratio > SPECIFICATION["reference_peak_ratio_maximum"]:
            issues.append("contaminated_noise_reference")
        quarters = np.array_split(channel_power[ref_start:ref_end], 4)
        medians = [float(np.median(q)) for q in quarters]
        if min(medians) <= 0 or max(medians) > SPECIFICATION[
            "reference_quarter_ratio_maximum"
        ] * min(medians):
            issues.append("nonstationary_noise_reference")
    high = noise * 10 ** (minimum_contrast / 10)
    persistence = max(4, math.ceil(rate * SPECIFICATION["persistence_seconds"]))
    active, edges = persistent_states(
        channel_power, high, high * SPECIFICATION["off_threshold_ratio"], persistence
    )
    pending_samples = max(
        (e["confirmed_sample"] - e["transition_start_sample"] - persistence + 1 for e in edges),
        default=0,
    )
    result = {
        "specification": specification(),
        "acquired_frequency_hz": acquired,
        "boxcar_samples": width,
        "filter_support_seconds": support / rate,
        "sample_granularity_s": 1 / rate,
        "edge_uncertainty_s": (support + 1 + pending_samples) / rate,
        "confirmation_seconds": persistence / rate,
        "delay_correction_s": 0.0,
        "delay_policy": "centered_filter_full_support_uncertainty",
        "reference_search_half_width_hz": min(
            acquisition, rate / width * SPECIFICATION["reference_channel_fraction"]
        ),
        "reference_peak_ratio": reference_peak_ratio,
        "reference_peak_frequency_hz": reference_peak_frequency,
        "reference_start_s": ref_start / rate,
        "reference_end_s": max(ref_start, ref_end) / rate,
        "reference_provenance": "plan_preamble_candidate_with_contamination_checks",
        "channel_noise_power": noise,
        "raw_noise_power": raw_noise,
        "on_threshold_power": high,
        "off_threshold_power": high * SPECIFICATION["off_threshold_ratio"],
        "edges": [
            {
                "onset_s": e["sample"] / rate,
                "transition_start_s": e["transition_start_sample"] / rate,
                "confirmation_s": e["confirmed_sample"] / rate,
                "active": e["active"],
            }
            for e in edges
        ],
        "issues": sorted(set(issues)),
    }
    return baseband / oscillator, active, result


def quiet_evidence(
    samples: np.ndarray,
    rate: float,
    center: float,
    frequency: float,
    start: int,
    end: int,
    noise: float,
    minimum_contrast: float,
    channel_half_width_hz: float | None = None,
) -> dict[str, Any]:
    """Assess raw quiet-window transients independently of edge persistence.

    Resolved coherent bursts fail silence; strong unresolved impulses and broadband
    bursts are ambiguous. Tiny subresolution near-noise excursions are diagnostic.
    """
    values = samples[start:end]
    power = np.abs(values.astype(np.complex128)) ** 2
    smoothed = average(power, 3)
    mask = smoothed >= noise * 10 ** (minimum_contrast / 10)
    bursts: list[dict[str, Any]] = []
    issues: set[str] = set()
    for a, b in runs(mask):
        segment = values[a:b]
        n = b - a
        products = segment[1:].astype(np.complex128) * np.conjugate(segment[:-1])
        measured_frequency = center + float(np.angle(np.sum(products))) * rate / (2 * np.pi)
        phase = np.exp(-2j * np.pi * (measured_frequency - center) * np.arange(n) / rate)
        energy = float(np.sum(power[a:b]))
        coherence = float(abs(np.sum(segment * phase)) ** 2 / max(n * energy, np.finfo(float).tiny))
        peak = float(np.max(power[a:b]))
        resolved = n >= 4
        credible = resolved or peak >= noise * SPECIFICATION["raw_impulse_noise_ratio"]
        if not credible:
            continue
        half_width = (
            channel_half_width_hz
            if channel_half_width_hz is not None
            else max(20.0, rate / filter_width(rate, 0.0) / 4)
        )
        coherent = (
            resolved
            and coherence >= SPECIFICATION["transient_coherence_minimum"]
            and abs(measured_frequency - frequency) <= half_width
        )
        issues.add("false_silence" if coherent else "ambiguous_quiet_contamination")
        bursts.append(
            {
                "start_s": (start + a) / rate,
                "end_s": (start + b) / rate,
                "duration_s": n / rate,
                "peak_power": peak,
                "integrated_power_seconds": energy / rate,
                "carrier_coherence": coherence,
                "measured_frequency_hz": measured_frequency if resolved else None,
                "classification": "coherent_in_band" if coherent else "unresolved_interference",
            }
        )
    return {
        "start_s": start / rate,
        "end_s": end / rate,
        "occupancy": sum(b["duration_s"] for b in bursts) / max((end - start) / rate, 1 / rate),
        "bursts": bursts,
        "issues": sorted(issues),
    }


def raw_quiet_bounds(
    samples: np.ndarray,
    rate: float,
    start: int,
    end: int,
    guard: int,
    confirmation_s: float,
    noise: float,
    contrast: float,
    first: bool,
    last: bool,
) -> tuple[int, int]:
    """Retain short bursts next to a confirmed edge instead of blanking a guard.

    Find a four-sample raw quiet run locally around the confirmed boundary. This
    only sets transient-inspection bounds; it never changes the timing measurement.
    """
    radius = guard + math.ceil(confirmation_s * rate)
    threshold = noise * 10 ** (contrast / 10)
    qstart, qend = (0 if first else start), (samples.size if last else end)
    if not first:
        lower, upper = max(0, start - radius), min(end, start + guard + 4)
        quiet = runs(np.abs(samples[lower:upper].astype(np.complex128)) ** 2 < threshold)
        candidates = [lower + a for a, b in quiet if b - a >= 4]
        if candidates:
            qstart = min(candidates)
    if not last:
        lower, upper = max(qstart, end - guard - 4), min(samples.size, end + radius)
        quiet = runs(np.abs(samples[lower:upper].astype(np.complex128)) ** 2 < threshold)
        candidates = [lower + b for a, b in quiet if b - a >= 4]
        if candidates:
            qend = max(candidates)
    return min(qstart, qend), qend


def validate_live_detector_plan(plan: dict[str, Any]) -> None:
    """Reject known unsupported detector geometry before any live acquisition."""
    rate = float(plan["capture_contract"]["sample_rate_hz"])
    protocol = plan["protocol"]
    secondary = protocol["secondary_frequency_hz"]
    separation = (
        abs(float(protocol["primary_frequency_hz"]) - float(secondary))
        if secondary is not None
        else 0.0
    )
    support = 3 * (filter_width(rate, separation) - 1) // 2
    classification = max(
        4, round(min(0.1, float(plan["thresholds"]["maximum_transition_s"]) / 2) * rate)
    )
    state_support = (classification // 2 + 1) / rate if secondary is not None else 0.0
    minimum = max(
        4 / rate,
        (support + 1) / rate + state_support,
    )
    if any(
        float(plan["thresholds"][key]) < minimum
        for key in ("timing_tolerance_s", "maximum_transition_s", "maximum_alignment_shift_s")
    ):
        raise ValueError("plan timing gates are tighter than detector support")
    guard = max(support, math.ceil(rate * SPECIFICATION["reference_guard_seconds"]))
    available = math.floor(float(protocol["pre_quiet_seconds"]) * rate) - 2 * guard
    if available < max(4, math.ceil(rate * SPECIFICATION["minimum_reference_seconds"])):
        raise ValueError("plan cannot supply a guarded detector noise reference")
    if (
        plan["mode"] == "tone"
        and float(protocol["tone_on_seconds"])
        <= 2 * float(plan["thresholds"]["timing_tolerance_s"]) + 0.02
    ):
        raise ValueError("TONE has no supported temporal ON interior")
