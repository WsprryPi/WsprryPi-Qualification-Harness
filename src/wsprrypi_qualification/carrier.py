"""Full-span, RF-off-subtracted continuous-carrier analysis."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
import numpy.typing as npt

from wsprrypi_qualification.capture_metadata import CaptureMetadata, load_capture_metadata
from wsprrypi_qualification.carrier_plot import (
    canonical_analysis_sha256,
    inspect_carrier_plot,
    render_carrier_plot,
)
from wsprrypi_qualification.cf32 import inspect_cf32, open_cf32
from wsprrypi_qualification.offline import (
    FailureCause,
    OfflineAnalysisError,
    artifact,
    load_json_document,
    write_json_new,
)
from wsprrypi_qualification.offline_context import (
    load_profile_context,
    validate_acquired_capture,
)
from wsprrypi_qualification.receiver_calibration import (
    interpret_frequency,
)
from wsprrypi_qualification.receiver_calibration import (
    validate_binding as validate_receiver_calibration,
)
from wsprrypi_qualification.receiver_tuning import ReceiverTuningError, ReceiverTuningGeometry


@dataclass(frozen=True)
class CarrierParameters:
    sample_rate_hz: int
    center_frequency_hz: float
    requested_frequency_hz: float
    fft_size: int = 262_144
    dc_exclusion_hz: float = 1_000.0
    usable_half_span_hz: float | None = None
    resolved_threshold_db: float = 6.0
    best_channel_hz: float = 20.0
    offset_gate_hz: float = 100.0
    share_gate: float = 0.5
    relative_acquisition_offset_gate_hz: float = 500.0
    relative_acquisition_contrast_gate_db: float = 10.0
    temporal_on_intervals_s: list[list[float]] | None = None
    startup_acquisition_max_s: float = 0.0


@dataclass(frozen=True)
class AcquiredCarrierEvidence:
    document: dict[str, Any]
    rf_off_path: Path
    rf_on_path: Path


def _average_power(path: Path, fft_size: int) -> tuple[npt.NDArray[np.float64], int]:
    iq = open_cf32(path)
    blocks = len(iq) // fft_size
    if blocks < 1:
        raise OfflineAnalysisError("carrier input is shorter than one FFT block")
    window = np.hanning(fft_size).astype(np.float64)
    scale = float(np.sum(window * window))
    total = np.zeros(fft_size, dtype=np.float64)
    for index in range(blocks):
        block = np.asarray(iq[index * fft_size : (index + 1) * fft_size], dtype=np.complex128)
        spectrum = np.fft.fftshift(np.fft.fft(block * window))
        total += np.abs(spectrum) ** 2 / scale
    return total / blocks, blocks


def _aligned_tone_intervals(
    path: Path, plan: dict[str, Any], expected: dict[str, Any]
) -> list[list[float]]:
    """Use the cadence detector's bounded common latency, never individual edge fits."""
    from wsprrypi_qualification.cw_iq import _acquired_timing_alignment
    from wsprrypi_qualification.noise import detect

    capture = plan["capture_contract"]
    thresholds = plan["thresholds"]
    rate = float(capture["sample_rate_hz"])
    _, active, detector = detect(
        open_cf32(path),
        rate,
        float(capture["center_frequency_hz"]),
        float(plan["protocol"]["primary_frequency_hz"]),
        None,
        float(thresholds["frequency_acquisition_half_width_hz"]),
        float(plan["protocol"]["pre_quiet_seconds"]),
        float(thresholds["minimum_contrast_db"]),
    )
    alignment = _acquired_timing_alignment(plan, expected, active, rate)
    if detector["issues"] or alignment is None:
        raise OfflineAnalysisError("TONE temporal guard lacks supported bounded alignment")
    margin = float(thresholds["timing_tolerance_s"])
    if float(detector["edge_uncertainty_s"]) >= margin:
        raise OfflineAnalysisError("TONE temporal alignment uncertainty exceeds timing tolerance")
    shift = float(alignment["common_shift_s"])
    return [
        [float(e["start_s"]) + shift + margin, float(e["end_s"]) + shift - margin]
        for e in expected["events"]
        if e["rf_state"] != "off"
    ]


def _temporal_carrier_guard(
    path: Path, parameters: CarrierParameters, frequency: float
) -> dict[str, Any]:
    """Independent short-window coherence; broadband impulses cannot prove a tone.

    Use all samples including the FFT tail. Projection width is bounded to avoid
    requiring sub-bin frequency accuracy. This guard never changes FFT metrics.
    """
    iq = open_cf32(path)
    width = max(4, min(parameters.fft_size // 8, round(parameters.sample_rate_hz * 0.02)))
    contrasts: list[float] = []
    coherence: list[float] = []
    intervals = parameters.temporal_on_intervals_s
    bounds = (
        [(0, len(iq))]
        if intervals is None
        else [
            (math.ceil(a * parameters.sample_rate_hz), math.floor(b * parameters.sample_rate_hz))
            for a, b in intervals
        ]
    )
    if not bounds or any(a < 0 or b > len(iq) or b - a < width for a, b in bounds):
        raise OfflineAnalysisError("temporal carrier intervals lack complete supported windows")
    windows = [
        (start, min(start + width, end))
        for first, end in bounds
        for start in range(first, end, width)
    ]
    for start, stop in windows:
        block = np.asarray(iq[start:stop], dtype=np.complex128)
        if block.size < 4:
            continue
        oscillator = np.exp(
            -2j
            * np.pi
            * (frequency - parameters.center_frequency_hz)
            * np.arange(block.size)
            / parameters.sample_rate_hz
        )
        total = float(np.mean(np.abs(block) ** 2))
        coherent = float(abs(np.mean(block * oscillator)) ** 2)
        # A remote stronger feature must remain diagnostic: estimate local
        # background with symmetric guard channels rather than full-span power.
        guard = 3 * parameters.sample_rate_hz / block.size
        references = []
        for sign in (-1, 1):
            shifted = oscillator * np.exp(
                sign * 2j * np.pi * guard * np.arange(block.size) / parameters.sample_rate_hz
            )
            references.append(float(abs(np.mean(block * shifted)) ** 2))
        background = max(sum(references) / 2, np.finfo(float).tiny)
        contrasts.append(
            float(10 * (np.log10(max(coherent, np.finfo(float).tiny)) - np.log10(background)))
        )
        coherence.append(coherent / max(total, np.finfo(float).tiny))
    startup: dict[str, Any] = {}
    if parameters.startup_acquisition_max_s:
        # Acquire once, at the earliest confirmed carrier. Never search again
        # after a dropout, nor trim the tail to a convenient passing interval.
        confirmation = max(1, math.ceil(0.1 * parameters.sample_rate_hz / width))
        limit = math.floor(parameters.startup_acquisition_max_s * parameters.sample_rate_hz)
        acquired = next(
            (
                i
                for i in range(len(contrasts))
                if (i + confirmation) * width <= limit
                and len(contrasts[i : i + confirmation]) == confirmation
                and all(
                    c >= parameters.relative_acquisition_contrast_gate_db
                    for c in contrasts[i : i + confirmation]
                )
            ),
            None,
        )
        sufficient = (
            acquired is not None and len(iq) - acquired * width >= parameters.sample_rate_hz
        )
        startup = {
            "startup_policy": "bounded_prefix_acquisition_v1",
            "startup_acquired": sufficient,
            "startup_max_s": parameters.startup_acquisition_max_s,
            "startup_confirmation_s": confirmation * width / parameters.sample_rate_hz,
            "minimum_steady_s": 1.0,
            "startup_excluded_windows": acquired if sufficient else 0,
            "startup_excluded_below_contrast_windows": sum(
                c < parameters.relative_acquisition_contrast_gate_db for c in contrasts[:acquired]
            )
            if sufficient
            else 0,
        }
        if sufficient:
            contrasts = contrasts[acquired:]
            coherence = coherence[acquired:]
    return {
        **startup,
        "version": 2 if startup else 1,
        "window_samples": width,
        "window_count": len(contrasts),
        "minimum_local_contrast_db": min(contrasts) if contrasts else 0.0,
        "minimum_coherent_fraction": min(coherence) if coherence else 0.0,
        "below_contrast_window_count": sum(
            c < parameters.relative_acquisition_contrast_gate_db for c in contrasts
        ),
        "policy": "all_short_windows_local_contrast_10db",
        "includes_fft_tail": intervals is None,
    }


def analyze_carrier(
    rf_off_path: Path,
    rf_on_path: Path,
    parameters: CarrierParameters,
    evidence_path: Path,
    *,
    rf_off_metadata_path: Path | None = None,
    rf_on_metadata_path: Path | None = None,
    profile_evidence: dict[str, Any] | None = None,
    receiver_calibration: dict[str, Any] | None = None,
    plot_path: Path | None = None,
    temporal_cw_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        not math.isfinite(parameters.startup_acquisition_max_s)
        or not 0 <= parameters.startup_acquisition_max_s <= 1.1
        or (parameters.startup_acquisition_max_s and parameters.temporal_on_intervals_s is not None)
    ):
        raise OfflineAnalysisError(
            "startup acquisition requires 0..1.1 seconds and continuous input"
        )
    if parameters.sample_rate_hz <= 0 or parameters.fft_size < 16:
        raise OfflineAnalysisError("sample rate and FFT size must be positive")
    if not 0 <= parameters.share_gate <= 1:
        raise OfflineAnalysisError("share gate must be in [0, 1]")
    if (
        not math.isfinite(parameters.relative_acquisition_offset_gate_hz)
        or parameters.relative_acquisition_offset_gate_hz <= 0
        or parameters.relative_acquisition_contrast_gate_db != 10.0
    ):
        raise OfflineAnalysisError(
            "bounded relative acquisition requires a positive finite offset "
            "and the maintained 10 dB contrast gate"
        )
    half_span = parameters.usable_half_span_hz or parameters.sample_rate_hz / 2
    try:
        ReceiverTuningGeometry(
            requested_frequency_hz=parameters.requested_frequency_hz,
            center_frequency_hz=parameters.center_frequency_hz,
            sample_rate_hz=parameters.sample_rate_hz,
            bandwidth_hz=2 * half_span,
            dc_exclusion_hz=parameters.dc_exclusion_hz,
            target_search_half_width_hz=parameters.relative_acquisition_offset_gate_hz,
            fft_bin_hz=parameters.sample_rate_hz / parameters.fft_size,
        ).validate()
    except ReceiverTuningError as error:
        raise OfflineAnalysisError(f"invalid receiver tuning geometry: {error}") from error
    off_threshold = 0.999
    on_threshold = 0.999
    if (rf_off_metadata_path is None) != (rf_on_metadata_path is None):
        raise OfflineAnalysisError("both RF-off and RF-on capture metadata are required together")
    metadata_validation: dict[str, Any]
    if rf_off_metadata_path is not None and rf_on_metadata_path is not None:
        off_metadata = load_capture_metadata(rf_off_metadata_path)
        on_metadata = load_capture_metadata(rf_on_metadata_path)
        off_threshold = off_metadata.clipping_threshold
        on_threshold = on_metadata.clipping_threshold
        off_info = inspect_cf32(rf_off_path, clipping_threshold=off_threshold)
        on_info = inspect_cf32(rf_on_path, clipping_threshold=on_threshold)
        _validate_capture_metadata(off_metadata, off_info, parameters, "RF-off")
        _validate_capture_metadata(on_metadata, on_info, parameters, "RF-on")
        if off_metadata.resolved_device != on_metadata.resolved_device:
            raise OfflineAnalysisError("RF-off and RF-on receiver identities differ")
        if off_metadata.actual_settings != on_metadata.actual_settings:
            raise OfflineAnalysisError("RF-off and RF-on receiver settings differ")
        if off_metadata.wire_format != on_metadata.wire_format:
            raise OfflineAnalysisError("RF-off and RF-on wire formats differ")
        if off_metadata.clipping_threshold != on_metadata.clipping_threshold:
            raise OfflineAnalysisError("RF-off and RF-on clipping thresholds differ")
        if profile_evidence is not None:
            _require_distinct_capture_pair(
                rf_off_metadata_path,
                rf_on_metadata_path,
                off_metadata,
                on_metadata,
                off_info,
                on_info,
            )
        metadata_validation = {
            "outcome": "passed",
            "rf_off": _capture_reference(rf_off_metadata_path, off_metadata),
            "rf_on": _capture_reference(rf_on_metadata_path, on_metadata),
        }
    else:
        off_info = inspect_cf32(rf_off_path, clipping_threshold=off_threshold)
        on_info = inspect_cf32(rf_on_path, clipping_threshold=on_threshold)
        metadata_validation = {
            "outcome": "synthetic_fixture_without_capture_metadata",
            "limitation": "not acceptable as acquired qualification evidence",
        }
    if off_info.clipped_samples or on_info.clipped_samples:
        raise OfflineAnalysisError("clipped carrier evidence is fixture_blocked")
    off_power, off_blocks = _average_power(rf_off_path, parameters.fft_size)
    on_power, on_blocks = _average_power(rf_on_path, parameters.fft_size)
    frequencies = parameters.center_frequency_hz + np.fft.fftshift(
        np.fft.fftfreq(parameters.fft_size, 1 / parameters.sample_rate_hz)
    )
    usable = np.abs(frequencies - parameters.center_frequency_hz) <= half_span
    usable &= np.abs(frequencies - parameters.center_frequency_hz) > parameters.dc_exclusion_hz
    residual = on_power - off_power
    threshold = off_power * (10 ** (parameters.resolved_threshold_db / 10))
    resolved = usable & (residual > 0) & (on_power >= threshold)
    global_candidates = np.flatnonzero(resolved if np.any(resolved) else usable)
    global_strongest_index = int(global_candidates[np.argmax(residual[global_candidates])])
    target_window = (
        np.abs(frequencies - parameters.requested_frequency_hz)
        <= parameters.relative_acquisition_offset_gate_hz
    )
    target_resolved = resolved & target_window
    target_usable = usable & target_window
    if not np.any(target_usable):
        raise OfflineAnalysisError("requested carrier window has no usable FFT bins")
    if not np.any(target_resolved):
        gate = "inconclusive"
        strongest_index = int(np.argmax(np.where(target_usable, residual, -np.inf)))
        share = 0.0
    else:
        strongest_index = int(np.argmax(np.where(target_resolved, residual, -np.inf)))
        bin_hz = parameters.sample_rate_hz / parameters.fft_size
        channel_bins = max(1, round(parameters.best_channel_hz / bin_hz))
        kernel = np.ones(channel_bins, dtype=np.float64)
        target_power = np.where(target_resolved, residual, 0.0)
        channel_power = np.convolve(target_power, kernel, mode="same")
        share = float(np.max(channel_power) / np.sum(target_power))
        offset = abs(float(frequencies[strongest_index] - parameters.requested_frequency_hz))
        tiny = np.finfo(np.float64).tiny
        strongest_contrast = float(
            10
            * (
                np.log10(max(on_power[strongest_index], tiny))
                - np.log10(max(off_power[strongest_index], tiny))
            )
        )
        global_is_outside_target = not bool(target_window[global_strongest_index])
        target_peak_is_edge_leakage = global_is_outside_target and offset >= (
            parameters.relative_acquisition_offset_gate_hz - bin_hz
        )
        gate = (
            "passed"
            if offset <= parameters.relative_acquisition_offset_gate_hz
            and strongest_contrast >= parameters.relative_acquisition_contrast_gate_db
            and not target_peak_is_edge_leakage
            else "failed"
        )
    strongest_hz = float(frequencies[strongest_index])
    if (
        gate == "passed"
        and abs(strongest_hz - parameters.requested_frequency_hz) > parameters.offset_gate_hz
    ):
        gate = "failed"
    requested_index = int(np.argmin(np.abs(frequencies - parameters.requested_frequency_hz)))
    tiny = np.finfo(np.float64).tiny

    def contrast(index: int) -> float:
        return float(
            10 * (np.log10(max(on_power[index], tiny)) - np.log10(max(off_power[index], tiny)))
        )

    noise_guard = _temporal_carrier_guard(rf_on_path, parameters, strongest_hz)
    # Comparable separated in-window features cannot uniquely identify the tone.
    competitors = target_resolved & (
        np.abs(frequencies - strongest_hz)
        > max(parameters.best_channel_hz, 4 * parameters.sample_rate_hz / parameters.fft_size)
    )
    ambiguous = bool(
        np.any(competitors) and np.max(residual[competitors]) >= 0.5 * residual[strongest_index]
    )
    noise_guard["ambiguous_in_window_feature"] = ambiguous
    noise_guard["outcome"] = (
        "inconclusive"
        if ambiguous
        or noise_guard["below_contrast_window_count"]
        or not noise_guard.get("startup_acquired", True)
        else "passed"
    )
    if gate == "passed" and noise_guard["outcome"] != "passed":
        gate = "inconclusive"
    ordered = global_candidates[np.argsort(residual[global_candidates])[::-1]][:10]
    document: dict[str, Any] = {
        "schema_version": 3,
        "evidence_type": "carrier_analysis",
        "gate_outcome": gate,
        "inputs": {"rf_off": asdict(off_info), "rf_on": asdict(on_info)},
        "contract": {
            **{
                k: v
                for k, v in asdict(parameters).items()
                if k != "startup_acquisition_max_s" or v != 0
            },
            "evidence_scope": "acquired" if profile_evidence is not None else "synthetic",
            "window": "hann",
            "averaging": "non_overlapping_power_blocks",
            "power_domain": "linear",
            "subtraction": "rf_on_minus_rf_off",
            "negative_residual_policy": "excluded",
            "fft_bin_hz": parameters.sample_rate_hz / parameters.fft_size,
            "rf_off_blocks": off_blocks,
            "rf_on_blocks": on_blocks,
            "unequal_capture_policy": (
                "validate each exact count independently; average all complete FFT blocks "
                "per capture without truncation or repetition"
            ),
            "capture_metadata_validation": metadata_validation,
            "profiles": profile_evidence,
            "resolved_threshold_interpretation": (
                "per-frequency-bin RF-on power at least resolved_threshold_db above "
                "the corresponding RF-off power, with positive RF-on-minus-RF-off residual"
            ),
            "edge_channel_policy": "same-length convolution; zero outside usable span",
            "gate_policy": "target_window_relative_carrier_acquisition_v3",
            "temporal_cw_reference": temporal_cw_reference,
        },
        "metrics": {
            "noise_guard": noise_guard,
            "requested_frequency_hz": parameters.requested_frequency_hz,
            "strongest_transmitter_added_frequency_hz": strongest_hz,
            "strongest_offset_hz": strongest_hz - parameters.requested_frequency_hz,
            "requested_bin_contrast_db": contrast(requested_index),
            "strongest_feature_contrast_db": contrast(strongest_index),
            "best_20hz_resolved_power_share": share,
            "nominal_offset_gate_passed": (
                abs(strongest_hz - parameters.requested_frequency_hz) <= parameters.offset_gate_hz
            ),
            "nominal_share_gate_passed": share >= parameters.share_gate,
            "relative_acquisition_passed": gate == "passed",
            "resolved_bin_count": int(np.count_nonzero(target_resolved)),
            "global_resolved_bin_count": int(np.count_nonzero(resolved)),
            "global_strongest_frequency_hz": float(frequencies[global_strongest_index]),
            "global_strongest_contrast_db": contrast(global_strongest_index),
            "strongest_features": [
                {
                    "frequency_hz": float(frequencies[i]),
                    "residual_power": float(max(residual[i], 0)),
                    "contrast_db": contrast(int(i)),
                }
                for i in ordered
            ],
        },
        "limitations": [
            "relative captured-span measurement; not calibrated frequency, power, or "
            "spectral compliance",
            "bounded carrier acquisition tolerates receiver frequency error and thermal drift; "
            "nominal offset and best-20-Hz concentration remain diagnostic only",
            "global features are diagnostic and do not redefine the requested carrier",
        ],
    }
    if receiver_calibration is not None:
        binding = validate_receiver_calibration(receiver_calibration)
        document["receiver_calibration"] = binding
        document["metrics"]["receiver_frequency_interpretation"] = interpret_frequency(
            binding, strongest_hz
        )
        if binding["applied"]:
            document["limitations"][0] = (
                "receiver-frequency interpretation uses the bound frozen calibration; "
                "power and spectral compliance remain uncalibrated"
            )
    # pathlib values from asdict are made explicit for portable JSON.
    for value in document["inputs"].values():
        value["path"] = str(Path(value["path"]).resolve(strict=True))
    if plot_path is not None:
        if plot_path.resolve() == evidence_path.resolve():
            raise OfflineAnalysisError("carrier plot and analysis paths must differ")
        plot = render_carrier_plot(
            plot_path,
            frequencies[usable],
            residual[usable],
            parameters.requested_frequency_hz,
            strongest_hz,
            parameters.center_frequency_hz,
            parameters.dc_exclusion_hz,
            parameters.relative_acquisition_offset_gate_hz,
            canonical_analysis_sha256(document),
        )
        document["plot"] = plot
    try:
        write_json_new(evidence_path, document, schema_name="carrier-analysis.schema.json")
    except Exception:
        if plot_path is not None:
            plot_path.unlink(missing_ok=True)
        raise
    return document


def analyze_carrier_acquired(
    rf_off_path: Path,
    rf_on_path: Path,
    rf_off_metadata_path: Path,
    rf_on_metadata_path: Path,
    bench_profile_path: Path,
    test_profile_path: Path,
    evidence_path: Path,
    *,
    fft_size: int = 262_144,
    dc_exclusion_hz: float = 1_000.0,
    relocation_bundle: Path | None = None,
    plot_path: Path | None = None,
    receiver_calibration: dict[str, Any] | None = None,
    cw_mode_plan_path: Path | None = None,
    cw_expected_path: Path | None = None,
    startup_acquisition_max_s: float = 0.0,
) -> dict[str, Any]:
    context = load_profile_context(bench_profile_path, test_profile_path)
    off_metadata = validate_acquired_capture(
        rf_off_metadata_path, rf_off_path, context, relocation_bundle=relocation_bundle
    )
    on_metadata = validate_acquired_capture(
        rf_on_metadata_path, rf_on_path, context, relocation_bundle=relocation_bundle
    )
    if off_metadata.actual_settings != on_metadata.actual_settings:
        raise OfflineAnalysisError("RF-off and RF-on settings differ")
    intervals = None
    temporal_reference = None
    if (cw_mode_plan_path is None) != (cw_expected_path is None):
        raise OfflineAnalysisError("CW plan and expected events are required together")
    if cw_mode_plan_path is not None and cw_expected_path is not None:
        from wsprrypi_qualification.cw_iq import _load_inputs

        cw_plan, expected = _load_inputs(cw_mode_plan_path, cw_expected_path)
        capture = cw_plan["capture_contract"]
        if (
            cw_plan["mode"] != "tone"
            or capture["sample_count"] != on_metadata.retained_sample_count
            or capture["sample_rate_hz"] != context.bench.receiver.sample_rate_hz
            or capture["center_frequency_hz"] != context.test.receiver_center_hz
            or cw_plan["protocol"]["primary_frequency_hz"] != context.test.frequency_hz
        ):
            raise OfflineAnalysisError("temporal CW plan contradicts carrier capture or frequency")
        intervals = _aligned_tone_intervals(rf_on_path, cw_plan, expected)
        temporal_reference = {
            "alignment_policy": "bounded_common_latency_v1",
            "plan": artifact(cw_mode_plan_path.resolve()),
            "expected_events": artifact(cw_expected_path.resolve()),
        }
    parameters = CarrierParameters(
        startup_acquisition_max_s=startup_acquisition_max_s,
        temporal_on_intervals_s=intervals,
        sample_rate_hz=context.bench.receiver.sample_rate_hz,
        center_frequency_hz=context.test.receiver_center_hz,
        requested_frequency_hz=context.test.frequency_hz,
        fft_size=fft_size,
        dc_exclusion_hz=dc_exclusion_hz,
        usable_half_span_hz=context.bench.receiver.bandwidth_hz / 2,
        offset_gate_hz=context.test.gates.carrier_offset_max_hz,
        share_gate=context.test.gates.best_20hz_share_min,
        relative_acquisition_offset_gate_hz=(
            context.test.gates.frequency_acquisition_half_width_hz
        ),
    )
    return analyze_carrier(
        rf_off_path,
        rf_on_path,
        parameters,
        evidence_path,
        rf_off_metadata_path=rf_off_metadata_path,
        rf_on_metadata_path=rf_on_metadata_path,
        temporal_cw_reference=temporal_reference,
        profile_evidence=context.evidence(),
        receiver_calibration=receiver_calibration,
        plot_path=plot_path,
    )


def _capture_reference(path: Path, metadata: CaptureMetadata) -> dict[str, Any]:
    return {
        **artifact(path),
        "capture_id": metadata.capture_id,
        "retained_capture_start_utc": (
            metadata.retained_capture_start_utc.isoformat().replace("+00:00", "Z")
            if metadata.retained_capture_start_utc is not None
            else None
        ),
        "retained_sample_count": metadata.retained_sample_count,
    }


def _require_distinct_capture_pair(
    off_metadata_path: Path,
    on_metadata_path: Path,
    off_metadata: CaptureMetadata,
    on_metadata: CaptureMetadata,
    off_info: Any,
    on_info: Any,
) -> None:
    off_metadata_artifact = artifact(off_metadata_path)
    on_metadata_artifact = artifact(on_metadata_path)
    duplicates = (
        off_metadata.capture_id == on_metadata.capture_id
        or off_metadata_path.resolve() == on_metadata_path.resolve()
        or off_metadata_artifact["sha256"] == on_metadata_artifact["sha256"]
        or Path(off_info.path).resolve() == Path(on_info.path).resolve()
        or off_info.sha256 == on_info.sha256
    )
    if duplicates:
        raise OfflineAnalysisError(
            "RF-off and RF-on must be distinct capture, metadata, and IQ artifacts",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )


def load_acquired_carrier_evidence(path: Path) -> AcquiredCarrierEvidence:
    """Authenticate acquired carrier evidence and deterministically recompute its analysis."""
    try:
        document = load_json_document(path, "carrier-analysis.schema.json")
        plot_path = inspect_carrier_plot(document) if "plot" in document else None
        if document["schema_version"] != 3:
            raise OfflineAnalysisError(
                "historical carrier evidence requires its original analyzer; "
                "compose new evidence for version 3"
            )
        contract = document["contract"]
        if contract["evidence_scope"] != "acquired":
            raise OfflineAnalysisError(
                "carrier evidence is not acquired evidence",
                cause=FailureCause.INCOMPLETE_EVIDENCE,
            )
        reference = contract.get("temporal_cw_reference")
        if (
            reference is not None
            and reference.get("alignment_policy") != "bounded_common_latency_v1"
        ):
            raise OfflineAnalysisError(
                "historical unaligned TONE evidence requires its original analyzer; "
                "compose new evidence"
            )
        profiles = contract["profiles"]
        bench_path = Path(profiles["bench"]["path"])
        test_path = Path(profiles["test"]["path"])
        context = load_profile_context(bench_path, test_path)
        if profiles != context.evidence():
            raise OfflineAnalysisError(
                "carrier profile context changed",
                cause=FailureCause.CONTRADICTORY_EVIDENCE,
            )
        metadata_records = contract["capture_metadata_validation"]
        off_metadata_path = Path(metadata_records["rf_off"]["path"])
        on_metadata_path = Path(metadata_records["rf_on"]["path"])
        off_path = Path(document["inputs"]["rf_off"]["path"])
        on_path = Path(document["inputs"]["rf_on"]["path"])
        off_metadata = validate_acquired_capture(off_metadata_path, off_path, context)
        on_metadata = validate_acquired_capture(on_metadata_path, on_path, context)
        if metadata_records["rf_off"] != _capture_reference(off_metadata_path, off_metadata):
            raise OfflineAnalysisError(
                "RF-off capture reference changed", cause=FailureCause.CONTRADICTORY_EVIDENCE
            )
        if metadata_records["rf_on"] != _capture_reference(on_metadata_path, on_metadata):
            raise OfflineAnalysisError(
                "RF-on capture reference changed", cause=FailureCause.CONTRADICTORY_EVIDENCE
            )
        with TemporaryDirectory(prefix="wspq-carrier-verify-") as directory:
            recomputed_plot = (
                Path(directory) / f"carrier{plot_path.suffix.lower()}"
                if plot_path is not None
                else None
            )
            recomputed = analyze_carrier_acquired(
                off_path,
                on_path,
                off_metadata_path,
                on_metadata_path,
                bench_path,
                test_path,
                Path(directory) / "carrier.json",
                startup_acquisition_max_s=contract.get("startup_acquisition_max_s", 0.0),
                fft_size=contract["fft_size"],
                dc_exclusion_hz=contract["dc_exclusion_hz"],
                plot_path=recomputed_plot,
                cw_mode_plan_path=(
                    Path(contract["temporal_cw_reference"]["plan"]["path"])
                    if contract["temporal_cw_reference"]
                    else None
                ),
                cw_expected_path=(
                    Path(contract["temporal_cw_reference"]["expected_events"]["path"])
                    if contract["temporal_cw_reference"]
                    else None
                ),
                receiver_calibration=document.get("receiver_calibration"),
            )
        observed_without_plot = dict(document)
        recomputed_without_plot = dict(recomputed)
        observed_plot = observed_without_plot.pop("plot", None)
        recomputed_plot_metadata = recomputed_without_plot.pop("plot", None)
        if observed_plot is not None and recomputed_plot_metadata is not None:
            observed_plot = {**observed_plot, "artifact": {**observed_plot["artifact"]}}
            observed_plot["artifact"]["path"] = recomputed_plot_metadata["artifact"]["path"]
        if (
            observed_without_plot != recomputed_without_plot
            or observed_plot != recomputed_plot_metadata
        ):
            raise OfflineAnalysisError(
                "carrier metrics or contract contradict authenticated inputs",
                cause=FailureCause.CONTRADICTORY_EVIDENCE,
            )
        return AcquiredCarrierEvidence(document, off_path.resolve(), on_path.resolve())
    except OfflineAnalysisError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise OfflineAnalysisError(
            f"carrier evidence is incomplete or unreadable: {error}",
            cause=FailureCause.INCOMPLETE_EVIDENCE,
        ) from error


def _validate_capture_metadata(
    metadata: CaptureMetadata, info: Any, parameters: CarrierParameters, label: str
) -> None:
    if metadata.evidence_type != "capture_success" or metadata.process_exit_code != 0:
        raise OfflineAnalysisError(f"{label} capture metadata is not successful")
    if metadata.overflow_count or metadata.clipped_samples or metadata.timeout_count:
        raise OfflineAnalysisError(
            f"{label} capture metadata reports overflow, clipping, or timeout"
        )
    if metadata.output.sha256 != info.sha256 or metadata.output.size_bytes != info.size_bytes:
        raise OfflineAnalysisError(f"{label} capture artifact hash or size differs from metadata")
    settings = metadata.actual_settings
    if settings is None or settings["format"] != "CF32":
        raise OfflineAnalysisError(f"{label} capture lacks actual CF32 settings")
    if not np.isclose(settings["sample_rate_hz"], parameters.sample_rate_hz):
        raise OfflineAnalysisError(f"{label} sample rate differs from analysis contract")
    if not np.isclose(settings["center_frequency_hz"], parameters.center_frequency_hz):
        raise OfflineAnalysisError(f"{label} center frequency differs from analysis contract")
