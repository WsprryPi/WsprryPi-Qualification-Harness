"""Offline carrier-specific quiet assessment; raw transients remain diagnostics.

Hann projections compare acquired carrier states with simultaneous nearby
channels. Fixed engineering thresholds have no statistical false-alarm claim.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np

from wsprrypi_qualification.noise import assess_quiet_significance, runs


def policy(
    rate: float,
    center: float,
    acquired: float,
    primary: float,
    secondary: float | None,
    duration: float,
    contrast: float,
) -> dict[str, Any]:
    values = [rate, center, acquired, primary, duration, contrast]
    if secondary is not None:
        values.append(secondary)
    if not all(math.isfinite(v) for v in values) or rate <= 0 or duration <= 0:
        raise ValueError("invalid quiet carrier parameters")
    requested = [primary] if secondary is None else [primary, secondary]
    anchor = min(requested, key=lambda f: abs(f - acquired))
    targets = sorted(set(acquired + f - anchor for f in requested))
    # Longer windows resolve outer references at low rates or near the span edge.
    margin = min(rate / 2 - abs(f - center) for f in targets)
    size = max(16, round(rate * 0.004), math.ceil(5 * rate / (0.9 * margin)) if margin > 0 else 16)
    size = min(size, max(64, round(rate * 0.1)))
    hop = max(1, size // 4)
    outer = [
        min(targets) - 3 * rate / size,
        min(targets) - 5 * rate / size,
        max(targets) + 3 * rate / size,
        max(targets) + 5 * rate / size,
    ]
    references = [list(outer) for _ in targets]
    geometry = all(
        abs(f - center) < rate / 2 for f in targets + [v for row in references for v in row]
    )
    result = {
        "name": "carrier_specific_quiet",
        "version": 1,
        "window": "hann",
        "window_samples": size,
        "hop_samples": hop,
        "target_frequencies_hz": targets,
        "reference_frequencies_hz": references,
        "geometry_usable": geometry,
        "minimum_contrast_db": contrast,
        "reference_statistic": "maximum",
        "reference_line_concentration": 0.5,
        "reference_line_noise_ratio": 100.0,
        "material_samples": max(size, math.ceil(min(0.01, duration * 0.01) * rate)),
        "rolling_samples": max(4, round(duration * rate)),
        "occupancy_limit": 0.01,
        "raw_event_effect": "diagnostic_only",
    }
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    return {**result, "sha256": hashlib.sha256(encoded).hexdigest()}


def window_starts(start: int, end: int, size: int, hop: int) -> list[int]:
    if end - start < size:
        return []
    result = list(range(start, end - size + 1, hop))
    if result[-1] != end - size:
        result.append(end - size)
    return result


def assess(
    evidence: dict[str, Any],
    rate: float,
    center: float,
    acquired: float,
    primary: float,
    secondary: float | None,
    duration: float,
    contrast: float,
    noise: float,
    *,
    timing_basis: str,
) -> dict[str, Any]:
    """Recompute decisions from retained powers, with exact policy/coverage checks."""
    result = assess_quiet_significance(evidence, rate, duration, timing_basis=timing_basis)
    # Historical raw measurements/occupancy remain reproducible, but cannot gate.
    result["issues"] = []
    for burst in result["bursts"]:
        burst["qualification_effect"] = "diagnostic_only"
    p = policy(rate, center, acquired, primary, secondary, duration, contrast)
    record = evidence["carrier_assessment"]
    if record["policy"] != p:
        raise ValueError("quiet carrier policy contradicts plan or acquisition")
    start, end = round(evidence["start_s"] * rate), round(evidence["end_s"] * rate)
    size, hop = p["window_samples"], p["hop_samples"]
    starts = window_starts(start, end, size, hop) if p["geometry_usable"] else []
    windows = record["windows"]
    if [w["start_sample"] for w in windows] != starts:
        raise ValueError("quiet carrier windows have missing or duplicate coverage")
    weight = np.hanning(size)
    bandwidth = float(np.sum(weight**2) / np.sum(weight) ** 2)
    if not math.isfinite(noise) or noise <= 0:
        raise ValueError("quiet carrier noise reference is unusable")
    floor = noise * bandwidth
    ratio = 10 ** (contrast / 10)
    occupied = np.zeros(end - start, dtype=np.int8)
    derived = []
    obscured = np.zeros(end - start, dtype=np.int8)
    for i, window in enumerate(windows):
        targets, references = window["target_powers"], window["reference_powers"]
        mean = window["mean_power"]
        if len(targets) != len(p["target_frequencies_hz"]) or len(references) != len(targets):
            raise ValueError("quiet carrier channel count mismatch")
        if any(len(row) != 4 for row in references):
            raise ValueError("quiet carrier reference channel count mismatch")
        powers = [mean, *targets, *[v for row in references for v in row]]
        if any(not math.isfinite(v) or v < 0 for v in powers):
            raise ValueError("quiet carrier powers are invalid")
        present = any(
            power >= ratio * max(floor, max(ref))
            for power, ref in zip(targets, references, strict=True)
        )
        interference = any(
            max(ref) >= max(p["reference_line_concentration"], 8 * bandwidth) * max(mean, 1e-30)
            and max(ref) >= p["reference_line_noise_ratio"] * floor
            for ref in references
        )
        derived.append(
            {**window, "carrier_present": present, "reference_interference": interference}
        )
        # Assign the nearest-window region, rather than counting overlapping
        # window lengths multiple times. First/last windows cover the endpoints.
        a = start if i == 0 else (starts[i - 1] + starts[i] + size) // 2
        b = end if i + 1 == len(starts) else (starts[i] + starts[i + 1] + size) // 2
        if present:
            occupied[a - start : b - start] = 1
        elif interference:
            obscured[a - start : b - start] = 1
    width = min(p["rolling_samples"], end - start)
    cumulative = np.r_[0, np.cumsum(occupied, dtype=np.int64)]
    counts = cumulative[width:] - cumulative[:-width]
    peak = int(np.argmax(counts))
    intervals = [{"start_sample": start + a, "end_sample": start + b} for a, b in runs(occupied)]
    material = any(i["end_sample"] - i["start_sample"] >= p["material_samples"] for i in intervals)
    accumulated = counts[peak] >= max(size, math.ceil(width * p["occupancy_limit"]))
    obscured_cumulative = np.r_[0, np.cumsum(obscured, dtype=np.int64)]
    obscured_counts = obscured_cumulative[width:] - obscured_cumulative[:-width]
    interference_material = any(b - a >= p["material_samples"] for a, b in runs(obscured))
    interference_material |= bool(
        np.max(obscured_counts) >= max(size, math.ceil(width * p["occupancy_limit"]))
    )
    issues = (
        ["unusable_quiet_carrier_geometry"]
        if not starts
        else ["false_silence"]
        if material or accumulated
        else ["ambiguous_quiet_carrier_interference"]
        if interference_material
        else []
    )
    result["issues"] = issues
    result["carrier_assessment"] = {
        "policy": p,
        "windows": derived,
        "carrier_intervals": intervals,
        "maximum_rolling_occupancy": float(counts[peak]) / width,
        "peak_window_start_sample": start + peak,
        "peak_window_end_sample": start + peak + width,
        "issues": issues,
    }
    return result


def measure(
    evidence: dict[str, Any],
    samples: np.ndarray,
    rate: float,
    center: float,
    acquired: float,
    primary: float,
    secondary: float | None,
    duration: float,
    contrast: float,
    noise: float,
    *,
    timing_basis: str,
) -> dict[str, Any]:
    """Measure all quiet windows, independent of the raw transient detector."""
    p = policy(rate, center, acquired, primary, secondary, duration, contrast)
    size = p["window_samples"]
    start, end = round(evidence["start_s"] * rate), round(evidence["end_s"] * rate)
    starts = window_starts(start, end, size, p["hop_samples"]) if p["geometry_usable"] else []
    weight = np.hanning(size)
    frequencies = p["target_frequencies_hz"] + [
        v for row in p["reference_frequencies_hz"] for v in row
    ]
    projections = np.exp(
        -2j * np.pi * (np.asarray(frequencies)[:, None] - center) * np.arange(size) / rate
    )
    projections *= weight / np.sum(weight)
    windows = []
    channels = len(p["target_frequencies_hz"])
    for a in starts:
        segment = samples[a : a + size].astype(np.complex128)
        powers = np.abs(projections @ segment) ** 2
        windows.append(
            {
                "start_sample": a,
                "target_powers": powers[:channels].tolist(),
                "reference_powers": powers[channels:].reshape(channels, 4).tolist(),
                "mean_power": float(np.mean(np.abs(segment) ** 2)),
            }
        )
    return assess(
        {**evidence, "carrier_assessment": {"policy": p, "windows": windows}},
        rate,
        center,
        acquired,
        primary,
        secondary,
        duration,
        contrast,
        noise,
        timing_basis=timing_basis,
    )
