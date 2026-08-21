"""Portable, authenticated carrier-spectrum rendering with Matplotlib Agg."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
import numpy.typing as npt

from wsprrypi_qualification.offline import (
    FailureCause,
    OfflineAnalysisError,
    artifact,
    require_new_file,
)

PLOT_WIDTH_PX = 1200
PLOT_HEIGHT_PX = 600
PLOT_DPI = 100
PLOT_FLOOR_DB = -80.0
PLOT_RENDERER = "matplotlib-agg"
PLOT_NORMALIZATION = "relative_db_to_strongest_positive_residual"
MEDIA_TYPES = {".png": "image/png", ".svg": "image/svg+xml"}
_MATPLOTLIB: tuple[Any, Any, Any] | None = None
_MATPLOTLIB_CACHE: TemporaryDirectory[str] | None = None


def canonical_analysis_sha256(document: dict[str, Any]) -> str:
    """Hash the canonical carrier analysis before its optional plot is attached."""
    payload = dict(document)
    payload.pop("plot", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def render_carrier_plot(
    path: Path,
    frequencies_hz: npt.NDArray[np.float64],
    residual_power: npt.NDArray[np.float64],
    requested_frequency_hz: float,
    strongest_frequency_hz: float,
    source_analysis_sha256: str,
) -> dict[str, Any]:
    """Render an immutable relative residual-spectrum PNG or SVG and describe it."""
    suffix = path.suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise OfflineAnalysisError("carrier plot output must end in .png or .svg")
    require_new_file(path)
    if frequencies_hz.size < 2 or frequencies_hz.size != residual_power.size:
        raise OfflineAnalysisError("carrier plot inputs are incomplete")

    positive = np.maximum(residual_power, np.finfo(np.float64).tiny)
    relative_db = 10.0 * np.log10(positive / np.max(positive))
    relative_db = np.clip(relative_db, PLOT_FLOOR_DB, 0.0)
    x_values, y_values = _display_envelope(frequencies_hz, relative_db)

    matplotlib, plt, _ = _load_matplotlib()
    figure_dpi = 72 if suffix == ".svg" else PLOT_DPI
    figure, axes = plt.subplots(
        figsize=(PLOT_WIDTH_PX / figure_dpi, PLOT_HEIGHT_PX / figure_dpi),
        dpi=PLOT_DPI,
    )
    try:
        axes.plot(x_values, y_values, color="#1769aa", linewidth=1.0)
        axes.axvline(
            requested_frequency_hz,
            color="#d32f2f",
            linestyle="--",
            linewidth=1.0,
            label="Requested frequency",
        )
        axes.axvline(
            strongest_frequency_hz,
            color="#388e3c",
            linestyle=":",
            linewidth=1.0,
            label="Strongest transmitter-added feature",
        )
        axes.set(
            title="RF-on minus RF-off residual spectrum",
            xlabel="Frequency (Hz)",
            ylabel="Residual power (dB relative)",
            xlim=(float(frequencies_hz[0]), float(frequencies_hz[-1])),
            ylim=(PLOT_FLOOR_DB, 1.0),
        )
        axes.grid(True, alpha=0.25)
        axes.legend(loc="lower right")
        figure.tight_layout()
        _save_figure_new(matplotlib, figure, path, suffix, source_analysis_sha256)
    finally:
        plt.close(figure)

    return {
        "artifact": artifact(path),
        "media_type": MEDIA_TYPES[suffix],
        "width_px": PLOT_WIDTH_PX,
        "height_px": PLOT_HEIGHT_PX,
        "renderer": {"name": PLOT_RENDERER, "version": matplotlib.__version__},
        "normalization": {
            "kind": PLOT_NORMALIZATION,
            "floor_db": PLOT_FLOOR_DB,
            "reference": "strongest positive RF-on-minus-RF-off residual",
            "calibrated": False,
        },
        "source_analysis_sha256": source_analysis_sha256,
    }


def inspect_carrier_plot(document: dict[str, Any]) -> Path:
    """Authenticate plot bytes, dimensions, media type, and source-analysis binding."""
    plot = document.get("plot")
    if not isinstance(plot, dict):
        raise OfflineAnalysisError(
            "carrier analysis does not declare a plot", cause=FailureCause.INCOMPLETE_EVIDENCE
        )
    record = plot["artifact"]
    path = Path(record["path"])
    try:
        actual = artifact(path)
    except OSError as error:
        raise OfflineAnalysisError(
            f"carrier plot is missing or unreadable: {error}",
            cause=FailureCause.INCOMPLETE_EVIDENCE,
        ) from error
    if actual != record:
        raise OfflineAnalysisError(
            "carrier plot artifact identity changed", cause=FailureCause.CONTRADICTORY_EVIDENCE
        )
    suffix = path.suffix.lower()
    if MEDIA_TYPES.get(suffix) != plot["media_type"]:
        raise OfflineAnalysisError(
            "carrier plot media type contradicts its filename",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
    dimensions = _rendered_dimensions(path, suffix)
    if dimensions != (plot["width_px"], plot["height_px"]):
        raise OfflineAnalysisError(
            "carrier plot dimensions contradict its metadata",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
    if plot["source_analysis_sha256"] != canonical_analysis_sha256(document):
        raise OfflineAnalysisError(
            "carrier plot source analysis binding is contradictory",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
    return path.resolve()


def _display_envelope(
    frequencies_hz: npt.NDArray[np.float64], relative_db: npt.NDArray[np.float64]
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    maximum_points = 4_000
    if frequencies_hz.size <= maximum_points:
        return frequencies_hz, relative_db
    edges = np.linspace(0, frequencies_hz.size, maximum_points + 1, dtype=np.int64)
    x_values = np.array(
        [float(np.mean(frequencies_hz[left:right])) for left, right in pairwise(edges)]
    )
    y_values = np.array([float(np.max(relative_db[left:right])) for left, right in pairwise(edges)])
    return x_values, y_values


def _save_figure_new(
    matplotlib: Any,
    figure: Any,
    path: Path,
    suffix: str,
    source_analysis_sha256: str,
) -> None:
    temporary: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{path.stem}.incomplete-", suffix=suffix, dir=path.parent
        )
        os.close(descriptor)
        temporary = Path(raw_path)
        metadata: dict[str, str | None] = {"Creator": f"{PLOT_RENDERER} {matplotlib.__version__}"}
        if suffix == ".svg":
            metadata["Date"] = None
        with matplotlib.rc_context({"svg.hashsalt": source_analysis_sha256}):
            figure.savefig(
                temporary,
                format=suffix.removeprefix("."),
                dpi=PLOT_DPI,
                metadata=metadata,
                facecolor="white",
            )
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _rendered_dimensions(path: Path, suffix: str) -> tuple[int, int]:
    try:
        if suffix == ".png":
            _, _, imread = _load_matplotlib()
            image = imread(path)
            if image.ndim not in {2, 3}:
                raise ValueError("unexpected PNG dimensionality")
            return int(image.shape[1]), int(image.shape[0])
        root = ET.parse(path).getroot()
        view_box = root.attrib.get("viewBox", "").split()
        if len(view_box) != 4:
            raise ValueError("SVG lacks a four-value viewBox")
        return round(float(view_box[2])), round(float(view_box[3]))
    except (OSError, ValueError, ET.ParseError) as error:
        raise OfflineAnalysisError(
            f"carrier plot is not a readable rendered {suffix[1:].upper()}: {error}",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        ) from error


def _load_matplotlib() -> tuple[Any, Any, Any]:
    """Load Agg lazily without writing configuration or caches into the user profile."""
    global _MATPLOTLIB, _MATPLOTLIB_CACHE
    if _MATPLOTLIB is not None:
        return _MATPLOTLIB
    _MATPLOTLIB_CACHE = TemporaryDirectory(prefix="wspq-matplotlib-")
    os.environ.setdefault("MPLCONFIGDIR", _MATPLOTLIB_CACHE.name)
    os.environ.setdefault("XDG_CACHE_HOME", _MATPLOTLIB_CACHE.name)
    import matplotlib

    matplotlib.use("Agg", force=True)
    from matplotlib import pyplot as plt
    from matplotlib.image import imread

    _MATPLOTLIB = matplotlib, plt, imread
    return _MATPLOTLIB
