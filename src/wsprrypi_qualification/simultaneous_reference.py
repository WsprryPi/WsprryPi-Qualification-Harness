"""Offline, authenticated two-channel frequency-reference diagnostics.

The additive receiver-error model is local to one capture and requires an
explicit transfer-error budget. It never changes transmitter or qualification
state and does not consume or stack a frozen receiver calibration profile.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from wsprrypi_qualification.capture_metadata import load_capture_metadata
from wsprrypi_qualification.offline import artifact, load_json_document, write_json_new

VERSION = 1


def _channel(
    values: np.ndarray, rate: float, center: float, target: float, width: float
) -> dict[str, Any]:
    count = len(values)
    window = np.hanning(count)
    spectrum = np.abs(np.fft.fft(values.astype(np.complex128) * window)) ** 2
    spectrum /= count * float(np.sum(window**2))
    frequencies = center + np.fft.fftfreq(count, 1 / rate)
    selected = np.abs(frequencies - target) <= width
    indexes = np.flatnonzero(selected)
    peak = int(indexes[np.argmax(spectrum[indexes])])
    bin_hz = rate / count
    peak_frequency = float(frequencies[peak])
    noise_band = (np.abs(frequencies - target) >= 2 * width) & (
        np.abs(frequencies - target) <= 4 * width
    )
    noise = max(float(np.median(spectrum[noise_band])), 1e-30)
    power = float(np.sum(spectrum[selected]))
    contrast = 10 * math.log10(max(power, 1e-30) / (noise * len(indexes)))
    competitors = selected & (np.abs(frequencies - peak_frequency) > 4 * bin_hz)
    ambiguous = bool(np.any(spectrum[competitors] >= spectrum[peak] * 0.5))
    # Interpolation is diagnostic; the uncertainty budget retains a full bin.
    if 0 < peak < count - 1:
        logs = np.log(np.maximum(spectrum[peak - 1 : peak + 2], 1e-300))
        denominator = logs[0] - 2 * logs[1] + logs[2]
        if denominator != 0:
            peak_frequency += float(0.5 * (logs[0] - logs[2]) / denominator) * bin_hz
    return {
        "indicated_frequency_hz": peak_frequency,
        "power_dbfs": 10 * math.log10(max(power, 1e-30)),
        "contrast_db": contrast,
        "ambiguous": ambiguous,
        "edge_peak": abs(peak_frequency - target) >= width - 2 * bin_hz,
    }


def analyze(metadata_path: Path, capture_path: Path, request_path: Path) -> dict[str, Any]:
    """Analyze every complete window, including a separately assessed capture tail."""
    input_bindings = {
        "capture": artifact(capture_path.resolve()),
        "metadata": artifact(metadata_path.resolve()),
        "request": artifact(request_path.resolve()),
    }
    request = load_json_document(request_path, "simultaneous-reference-request.schema.json")
    metadata = load_capture_metadata(metadata_path)
    capture = artifact(capture_path.resolve())
    if any(capture[key] != getattr(metadata.output, key) for key in ("sha256", "size_bytes")):
        raise ValueError("capture does not match authenticated metadata")
    if (
        metadata.primary_outcome != "success"
        or metadata.cleanup_outcome != "verified"
        or metadata.overflow_count
        or metadata.timeout_count
        or metadata.clipped_samples
    ):
        raise ValueError("reference analysis requires successful, loss-free, unclipped capture")
    settings = metadata.actual_settings
    if settings is None or settings["agc"] or settings["bias_tee"]:
        raise ValueError("reference analysis requires fixed gain and bias tee off")
    rate = float(settings["sample_rate_hz"])
    center = float(settings["center_frequency_hz"])
    width = float(request["channel_half_width_hz"])
    signal = float(request["signal_frequency_hz"])
    reference = float(request["reference_frequency_hz"])
    span = min(rate, float(settings["bandwidth_hz"])) / 2
    count = round(float(request["window_seconds"]) * rate)
    if count < 32 or count > 4_194_304 or width < 8 * rate / count:
        raise ValueError("channel/window geometry is unresolved or exceeds analysis bounds")
    if abs(signal - reference) <= 8 * width:
        raise ValueError("signal and reference channels or noise guards overlap")
    if any(
        abs(f - center) + 4 * width >= span or abs(f - center) <= 4 * width
        for f in (signal, reference)
    ):
        raise ValueError("channels and noise guards must avoid DC and receiver edges")
    samples = np.memmap(capture_path, dtype="<c8", mode="r")
    if len(samples) != metadata.retained_sample_count or len(samples) < count:
        raise ValueError("capture count is inconsistent or shorter than one window")
    if not np.all(np.isfinite(samples)) or np.any(
        np.maximum(abs(samples.real), abs(samples.imag)) >= metadata.clipping_threshold
    ):
        raise ValueError("IQ is non-finite or clipped")
    windows: list[dict[str, Any]] = []
    starts = list(range(0, len(samples) - count + 1, count))
    if starts[-1] + count != len(samples):
        starts.append(len(samples) - count)  # overlap the tail, never discard it
    reference_frequencies = []
    for start in starts:
        values = samples[start : start + count]
        measured_signal = _channel(values, rate, center, signal, width)
        measured_reference = _channel(values, rate, center, reference, width)

        def usable(channel: dict[str, Any]) -> bool:
            return bool(
                channel["contrast_db"] >= request["minimum_contrast_db"]
                and not channel["ambiguous"]
                and not channel["edge_peak"]
            )

        # A long FFT must not average away a short reference dropout. Inspect
        # coherent amplitude in 20 ms windows, including the final partial tail.
        probe_size = min(count, max(16, round(rate * 0.02)))
        mixed = values * np.exp(
            -2j
            * np.pi
            * (measured_reference["indicated_frequency_hz"] - center)
            * np.arange(count)
            / rate
        )
        probe_starts = list(range(0, count - probe_size + 1, probe_size))
        if probe_starts[-1] + probe_size != count:
            probe_starts.append(count - probe_size)
        reference_powers = [
            float(abs(np.mean(mixed[a : a + probe_size])) ** 2) for a in probe_starts
        ]
        ratio = min(reference_powers) / max(float(np.median(reference_powers)), 1e-30)
        measured_reference["minimum_20ms_power_ratio"] = ratio
        reference_ok = usable(measured_reference) and ratio >= 0.5
        signal_ok = usable(measured_signal)
        if reference_ok:
            reference_frequencies.append(measured_reference["indicated_frequency_hz"])
        windows.append(
            {
                "start_s": start / rate,
                "end_s": (start + count) / rate,
                "signal": measured_signal,
                "reference": measured_reference,
                "signal_usable": signal_ok,
                "reference_usable": reference_ok,
                "corrected_signal_frequency_hz": None,
                "frequency_error_budget_hz": None,
                "signal_minus_reference_db": measured_signal["power_dbfs"]
                - measured_reference["power_dbfs"]
                if signal_ok and reference_ok
                else None,
            }
        )
    excursion = (
        max(reference_frequencies) - min(reference_frequencies) if reference_frequencies else None
    )
    reference_ok = (
        all(w["reference_usable"] for w in windows)
        and excursion is not None
        and excursion <= request["maximum_reference_excursion_hz"]
    )
    issues = []
    if not all(w["reference_usable"] for w in windows):
        issues.append("missing_ambiguous_variable_or_edge_reference")
    if excursion is not None and excursion > request["maximum_reference_excursion_hz"]:
        issues.append("unstable_reference")
    if not any(w["signal_usable"] for w in windows):
        issues.append("no_resolved_signal")
    if reference_ok:
        for w in windows:
            if w["signal_usable"]:
                w["corrected_signal_frequency_hz"] = w["signal"]["indicated_frequency_hz"] - (
                    w["reference"]["indicated_frequency_hz"] - reference
                )
                w["frequency_error_budget_hz"] = (
                    request["reference_uncertainty_hz"]
                    + request["transfer_uncertainty_hz"]
                    + 2 * rate / count
                )
    if any(artifact(Path(binding["path"])) != binding for binding in input_bindings.values()):
        raise ValueError("reference input changed during analysis")
    return {
        "schema_version": 1,
        "evidence_type": "simultaneous_reference_analysis",
        "algorithm_version": VERSION,
        "qualification_claim": False,
        "inputs": input_bindings,
        "request": request,
        "receiver_settings": settings,
        "model": "local_additive_receiver_error",
        "uncertainty_policy": "sum_of_declared_reference_and_transfer_bounds_plus_two_fft_bins",
        "power_reference": "unit_complex_CF32_power_not_calibrated_dBm",
        "reference_usable": reference_ok,
        "reference_excursion_hz": excursion,
        "outcome": "usable_diagnostic" if not issues else "inconclusive",
        "issues": issues,
        "windows": windows,
    }


def compose(metadata: Path, capture: Path, request: Path, output: Path) -> dict[str, Any]:
    document = analyze(metadata, capture, request)
    write_json_new(output, document, schema_name="simultaneous-reference-analysis.schema.json")
    return document


def validate(path: Path) -> dict[str, Any]:
    document = load_json_document(path, "simultaneous-reference-analysis.schema.json")
    inputs = document["inputs"]
    for binding in inputs.values():
        if artifact(Path(binding["path"])) != binding:
            raise ValueError("reference analysis input artifact changed")
    recomputed = analyze(
        Path(inputs["metadata"]["path"]),
        Path(inputs["capture"]["path"]),
        Path(inputs["request"]["path"]),
    )
    if document != recomputed:
        raise ValueError("reference analysis differs from authenticated recomputation")
    return document
