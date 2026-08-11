"""Portable inspection of the maintained little-endian CF32 wire format."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from wsprrypi_qualification.offline import OfflineAnalysisError, artifact

CF32_DTYPE = np.dtype("<c8")


@dataclass(frozen=True)
class Cf32Inspection:
    path: Path
    sample_count: int
    size_bytes: int
    sha256: str
    peak_magnitude: float
    peak_component: float
    clipping_threshold: float
    clipped_samples: int


def open_cf32(path: Path) -> npt.NDArray[np.complex64]:
    if not path.is_file():
        raise OfflineAnalysisError(f"CF32 input is not a regular file: {path}")
    size = path.stat().st_size
    if size == 0 or size % 8:
        raise OfflineAnalysisError(f"CF32 byte length must be a positive multiple of 8: {size}")
    return np.memmap(path, dtype=CF32_DTYPE, mode="r")


def inspect_cf32(path: Path, *, clipping_threshold: float = 0.999) -> Cf32Inspection:
    if not 0 < clipping_threshold <= 1:
        raise OfflineAnalysisError("clipping threshold must be in (0, 1]")
    iq = open_cf32(path)
    peak = 0.0
    peak_component = 0.0
    clipped = 0
    for first in range(0, len(iq), 1_000_000):
        chunk = np.asarray(iq[first : first + 1_000_000])
        if not np.all(np.isfinite(chunk.real)) or not np.all(np.isfinite(chunk.imag)):
            raise OfflineAnalysisError("CF32 input contains non-finite components")
        magnitude = np.abs(chunk)
        peak = max(peak, float(np.max(magnitude)))
        components = np.maximum(np.abs(chunk.real), np.abs(chunk.imag))
        peak_component = max(peak_component, float(np.max(components)))
        clipped += int(np.count_nonzero(components >= clipping_threshold))
    record = artifact(path)
    return Cf32Inspection(
        path,
        len(iq),
        record["size_bytes"],
        record["sha256"],
        peak,
        peak_component,
        clipping_threshold,
        clipped,
    )
