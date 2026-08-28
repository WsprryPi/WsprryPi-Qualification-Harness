"""Deterministic CF32 channel translation and timestamped WSPR WAV creation."""

from __future__ import annotations

import os
import tempfile
import wave
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from wsprrypi_qualification.cf32 import inspect_cf32, open_cf32
from wsprrypi_qualification.offline import (
    OfflineAnalysisError,
    artifact,
    require_new_file,
    write_json_new,
)
from wsprrypi_qualification.offline_context import load_profile_context, validate_acquired_capture
from wsprrypi_qualification.timing import require_aware_utc, sample_index_at_utc

CONJUGATE_POLICY = (
    "expected decode is nearest positive target_audio_hz; other real-audio images are companions"
)


@dataclass(frozen=True)
class AudioParameters:
    sample_rate_hz: int
    center_frequency_hz: float
    selected_frequency_hz: float
    output_rate_hz: int = 12_000
    target_audio_hz: float = 1_500.0
    frame_duration_s: int = 120
    filter_taps: int = 64
    required_margin_s: int = 5


MARGIN_POLICY = "required_before_slot_complete_frame_required_after_start"


def slot_wav_name(slot: datetime) -> str:
    utc = require_aware_utc(slot)
    return utc.strftime("%Y%m%dT%H%M%SZ.wav")


def _resample_mixed(
    iq: np.ndarray[Any, Any], start: int, count: int, parameters: AudioParameters
) -> np.ndarray[Any, Any]:
    output_count = count * parameters.output_rate_hz // parameters.sample_rate_hz
    if output_count * parameters.sample_rate_hz != count * parameters.output_rate_hz:
        raise OfflineAnalysisError(
            "selected input length does not map to an integral output length"
        )
    half = parameters.filter_taps // 2
    output = np.empty(output_count, dtype=np.float64)
    mix_hz = (
        parameters.selected_frequency_hz - parameters.center_frequency_hz
    ) - parameters.target_audio_hz
    for first in range(0, output_count, 16_384):
        last = min(output_count, first + 16_384)
        positions = (
            start
            + np.arange(first, last, dtype=np.float64)
            * parameters.sample_rate_hz
            / parameters.output_rate_hz
        )
        centers = np.floor(positions).astype(np.int64)
        offsets = np.arange(-half + 1, half + 1, dtype=np.int64)
        indices = centers[:, None] + offsets[None, :]
        valid = (indices >= start) & (indices < start + count)
        clipped_indices = np.clip(indices, start, start + count - 1)
        delta = clipped_indices - positions[:, None]
        cutoff = min(0.45 * parameters.output_rate_hz / parameters.sample_rate_hz, 0.45)
        weights = 2 * cutoff * np.sinc(2 * cutoff * delta)
        weights *= np.hanning(parameters.filter_taps)[None, :]
        weights *= valid
        sums = np.sum(weights, axis=1)
        if np.any(np.abs(sums) < np.finfo(float).tiny):
            raise OfflineAnalysisError("resampler has an empty support interval")
        phase = np.exp(-2j * np.pi * mix_hz * clipped_indices / parameters.sample_rate_hz)
        values = np.asarray(iq[clipped_indices]) * phase
        output[first:last] = np.real(np.sum(values * weights, axis=1) / sums)
    return output


def render_slot_pcm(
    iq_path: Path, start: int, count: int, parameters: AudioParameters
) -> tuple[bytes, float]:
    """Return the deterministic maintained PCM payload and normalization scale."""
    iq = open_cf32(iq_path)
    audio = _resample_mixed(iq, start, count, parameters)
    peak = float(np.max(np.abs(audio)))
    scale = 0.95 / peak if peak > 0 else 1.0
    pcm = np.rint(np.clip(audio * scale, -1, 1) * 32767).astype("<i2")
    return pcm.tobytes(), scale


def create_slot_wav(
    iq_path: Path,
    capture_start_utc: datetime,
    slot_utc: datetime,
    output_path: Path,
    evidence_path: Path,
    parameters: AudioParameters,
    *,
    clipping_threshold: float = 0.999,
    profile_evidence: dict[str, Any] | None = None,
    capture_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if parameters.output_rate_hz <= 0 or parameters.filter_taps < 16 or parameters.filter_taps % 2:
        raise OfflineAnalysisError(
            "output rate must be positive and filter taps must be even and >= 16"
        )
    info = inspect_cf32(iq_path, clipping_threshold=clipping_threshold)
    if info.clipped_samples:
        raise OfflineAnalysisError("clipped IQ cannot produce decoder evidence")
    slot = require_aware_utc(slot_utc)
    if slot.minute % 2 or slot.second or slot.microsecond:
        raise OfflineAnalysisError("WSPR slot must be an even UTC two-minute boundary")
    start = sample_index_at_utc(capture_start_utc, slot, parameters.sample_rate_hz)
    count = parameters.sample_rate_hz * parameters.frame_duration_s
    margin = parameters.sample_rate_hz * parameters.required_margin_s
    if start < margin or start + count > info.sample_count:
        raise OfflineAnalysisError(
            "capture lacks the required pre-slot margin or complete selected WSPR slot"
        )
    require_new_file(output_path)
    require_new_file(evidence_path)
    pcm_bytes, scale = render_slot_pcm(iq_path, start, count, parameters)
    output_count = len(pcm_bytes) // 2
    temporary: Path | None = None
    promoted = False
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.name}.incomplete-", dir=output_path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
        with wave.open(str(temporary), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(parameters.output_rate_hz)
            wav.writeframes(pcm_bytes)
        # Windows requires a writable descriptor for FlushFileBuffers, which
        # backs os.fsync(). No bytes are modified through this handle.
        with temporary.open("rb+") as handle:
            os.fsync(handle.fileno())
        with wave.open(str(temporary), "rb") as wav:
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or wav.getframerate() != parameters.output_rate_hz
                or wav.getnframes() != output_count
            ):
                raise OfflineAnalysisError("temporary WAV validation failed")
        output_record = artifact(temporary)
        output_record["path"] = str(output_path.resolve())
        temporary.replace(output_path)
        promoted = True
        document = _audio_document(
            info,
            output_record,
            slot,
            start,
            count,
            output_count,
            scale,
            parameters,
            profile_evidence,
            capture_evidence,
        )
        try:
            write_json_new(
                evidence_path,
                document,
                schema_name=(
                    "audio-conversion.schema.json"
                    if profile_evidence is not None and capture_evidence is not None
                    else None
                ),
            )
        except Exception:
            output_path.unlink(missing_ok=True)
            promoted = False
            raise
        return document
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        if not promoted and output_path.exists() and not evidence_path.exists():
            output_path.unlink()


def _audio_document(
    info: Any,
    output_record: dict[str, Any],
    slot: datetime,
    start: int,
    count: int,
    output_count: int,
    scale: float,
    parameters: AudioParameters,
    profile_evidence: dict[str, Any] | None,
    capture_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": 1,
        "evidence_type": "audio_conversion",
        "slot_utc": slot.isoformat().replace("+00:00", "Z"),
        "input": {**asdict(info), "path": str(info.path.resolve(strict=True))},
        "output": output_record,
        "contract": {
            **asdict(parameters),
            "margin_policy": MARGIN_POLICY,
            "mix_hz": (parameters.selected_frequency_hz - parameters.center_frequency_hz)
            - parameters.target_audio_hz,
            "mix_formula": "real(iq[n] * exp(-j*2*pi*mix_hz*n/fs))",
            "resampler": "windowed_sinc_hann",
            "wav_format": "mono_pcm_s16le",
            "input_start_sample": start,
            "input_sample_count": count,
            "output_sample_count": output_count,
            "normalization_scale": scale,
        },
        "conjugate_policy": CONJUGATE_POLICY,
        "profiles": profile_evidence,
        "capture": capture_evidence,
        "publication": {"outcome": "complete", "cleanup": "verified"},
    }
    return document


def create_slot_wav_acquired(
    iq_path: Path,
    capture_metadata_path: Path,
    bench_profile_path: Path,
    test_profile_path: Path,
    slot_utc: datetime,
    output_directory: Path,
    evidence_path: Path,
    *,
    selected_frequency_hz: float | None = None,
) -> dict[str, Any]:
    context = load_profile_context(bench_profile_path, test_profile_path)
    metadata = validate_acquired_capture(capture_metadata_path, iq_path, context)
    if metadata.retained_capture_start_utc is None:
        raise OfflineAnalysisError("capture lacks authoritative retained-capture UTC start")
    receiver = context.bench.receiver
    half_bandwidth = receiver.bandwidth_hz / 2
    selected = (
        context.test.frequency_hz if selected_frequency_hz is None else float(selected_frequency_hz)
    )
    if (
        abs(selected - context.test.frequency_hz)
        > context.test.gates.frequency_acquisition_half_width_hz
    ):
        raise OfflineAnalysisError("acquired RF frequency exceeds the bounded acquisition window")
    if abs(selected - context.test.receiver_center_hz) > half_bandwidth:
        raise OfflineAnalysisError("selected RF frequency is outside recorded receiver coverage")
    canonical = output_directory / slot_wav_name(slot_utc)
    parameters = AudioParameters(
        receiver.sample_rate_hz,
        context.test.receiver_center_hz,
        selected,
    )
    return create_slot_wav(
        iq_path,
        metadata.retained_capture_start_utc,
        slot_utc,
        canonical,
        evidence_path,
        parameters,
        clipping_threshold=metadata.clipping_threshold,
        profile_evidence=context.evidence(),
        capture_evidence={
            **artifact(capture_metadata_path),
            "capture_id": metadata.capture_id,
            "retained_capture_start_utc": metadata.retained_capture_start_utc.isoformat().replace(
                "+00:00", "Z"
            ),
            "retained_sample_count": metadata.retained_sample_count,
        },
    )
