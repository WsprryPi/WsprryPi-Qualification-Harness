"""Full-span, RF-off-subtracted continuous-carrier analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
import numpy.typing as npt

from wsprrypi_qualification.capture_metadata import CaptureMetadata, load_capture_metadata
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


def analyze_carrier(
    rf_off_path: Path,
    rf_on_path: Path,
    parameters: CarrierParameters,
    evidence_path: Path,
    *,
    rf_off_metadata_path: Path | None = None,
    rf_on_metadata_path: Path | None = None,
    profile_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if parameters.sample_rate_hz <= 0 or parameters.fft_size < 16:
        raise OfflineAnalysisError("sample rate and FFT size must be positive")
    if not 0 <= parameters.share_gate <= 1:
        raise OfflineAnalysisError("share gate must be in [0, 1]")
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
        if (
            off_metadata.retained_sample_count != on_metadata.retained_sample_count
            or off_metadata.requested_sample_count != on_metadata.requested_sample_count
        ):
            raise OfflineAnalysisError("RF-off and RF-on exact sample counts differ")
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
    half_span = parameters.usable_half_span_hz or parameters.sample_rate_hz / 2
    usable = np.abs(frequencies - parameters.center_frequency_hz) <= half_span
    usable &= np.abs(frequencies - parameters.center_frequency_hz) > parameters.dc_exclusion_hz
    residual = on_power - off_power
    threshold = off_power * (10 ** (parameters.resolved_threshold_db / 10))
    resolved = usable & (residual > 0) & (on_power >= threshold)
    if not np.any(resolved):
        gate = "inconclusive"
        strongest_index = int(np.argmax(np.where(usable, residual, -np.inf)))
        share = 0.0
    else:
        strongest_index = int(np.argmax(np.where(resolved, residual, -np.inf)))
        bin_hz = parameters.sample_rate_hz / parameters.fft_size
        channel_bins = max(1, round(parameters.best_channel_hz / bin_hz))
        kernel = np.ones(channel_bins, dtype=np.float64)
        channel_power = np.convolve(np.where(resolved, residual, 0.0), kernel, mode="same")
        share = float(np.max(channel_power) / np.sum(np.where(resolved, residual, 0.0)))
        offset = abs(float(frequencies[strongest_index] - parameters.requested_frequency_hz))
        gate = (
            "passed"
            if offset <= parameters.offset_gate_hz and share >= parameters.share_gate
            else "failed"
        )
    strongest_hz = float(frequencies[strongest_index])
    requested_index = int(np.argmin(np.abs(frequencies - parameters.requested_frequency_hz)))
    tiny = np.finfo(np.float64).tiny

    def contrast(index: int) -> float:
        return float(
            10 * (np.log10(max(on_power[index], tiny)) - np.log10(max(off_power[index], tiny)))
        )

    candidates = np.flatnonzero(resolved if np.any(resolved) else usable)
    ordered = candidates[np.argsort(residual[candidates])[::-1]][:10]
    document: dict[str, Any] = {
        "schema_version": 1,
        "evidence_type": "carrier_analysis",
        "gate_outcome": gate,
        "inputs": {"rf_off": asdict(off_info), "rf_on": asdict(on_info)},
        "contract": {
            **asdict(parameters),
            "evidence_scope": "acquired" if profile_evidence is not None else "synthetic",
            "window": "hann",
            "averaging": "non_overlapping_power_blocks",
            "power_domain": "linear",
            "subtraction": "rf_on_minus_rf_off",
            "negative_residual_policy": "excluded",
            "fft_bin_hz": parameters.sample_rate_hz / parameters.fft_size,
            "rf_off_blocks": off_blocks,
            "rf_on_blocks": on_blocks,
            "capture_metadata_validation": metadata_validation,
            "profiles": profile_evidence,
            "resolved_threshold_interpretation": (
                "per-frequency-bin RF-on power at least resolved_threshold_db above "
                "the corresponding RF-off power, with positive RF-on-minus-RF-off residual"
            ),
            "edge_channel_policy": "same-length convolution; zero outside usable span",
        },
        "metrics": {
            "requested_frequency_hz": parameters.requested_frequency_hz,
            "strongest_transmitter_added_frequency_hz": strongest_hz,
            "strongest_offset_hz": strongest_hz - parameters.requested_frequency_hz,
            "requested_bin_contrast_db": contrast(requested_index),
            "strongest_feature_contrast_db": contrast(strongest_index),
            "best_20hz_resolved_power_share": share,
            "resolved_bin_count": int(np.count_nonzero(resolved)),
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
            "relative captured-span measurement; not calibrated power or spectral compliance"
        ],
    }
    # pathlib values from asdict are made explicit for portable JSON.
    for value in document["inputs"].values():
        value["path"] = str(Path(value["path"]).resolve(strict=True))
    write_json_new(evidence_path, document, schema_name="carrier-analysis.schema.json")
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
) -> dict[str, Any]:
    context = load_profile_context(bench_profile_path, test_profile_path)
    off_metadata = validate_acquired_capture(rf_off_metadata_path, rf_off_path, context)
    on_metadata = validate_acquired_capture(rf_on_metadata_path, rf_on_path, context)
    if off_metadata.actual_settings != on_metadata.actual_settings:
        raise OfflineAnalysisError("RF-off and RF-on settings differ")
    parameters = CarrierParameters(
        sample_rate_hz=context.bench.receiver.sample_rate_hz,
        center_frequency_hz=context.test.receiver_center_hz,
        requested_frequency_hz=context.test.frequency_hz,
        fft_size=fft_size,
        dc_exclusion_hz=dc_exclusion_hz,
        usable_half_span_hz=context.bench.receiver.bandwidth_hz / 2,
        offset_gate_hz=context.test.gates.carrier_offset_max_hz,
        share_gate=context.test.gates.best_20hz_share_min,
    )
    return analyze_carrier(
        rf_off_path,
        rf_on_path,
        parameters,
        evidence_path,
        rf_off_metadata_path=rf_off_metadata_path,
        rf_on_metadata_path=rf_on_metadata_path,
        profile_evidence=context.evidence(),
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
        contract = document["contract"]
        if contract["evidence_scope"] != "acquired":
            raise OfflineAnalysisError(
                "carrier evidence is not acquired evidence",
                cause=FailureCause.INCOMPLETE_EVIDENCE,
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
            recomputed = analyze_carrier_acquired(
                off_path,
                on_path,
                off_metadata_path,
                on_metadata_path,
                bench_path,
                test_path,
                Path(directory) / "carrier.json",
                fft_size=contract["fft_size"],
                dc_exclusion_hz=contract["dc_exclusion_hz"],
            )
        if document != recomputed:
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
