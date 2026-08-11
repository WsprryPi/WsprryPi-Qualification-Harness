#!/usr/bin/env python3
"""Compare relative carrier stability in two uninterrupted complex-IQ captures."""

from pathlib import Path
import csv
import json
import math
import os
import numpy as np

FS = 250_000.0
DECIM = 2_500
BLOCK_RATE = FS / DECIM
EDGE_SECONDS = 0.5
WINDOW_SECONDS = 2.0
STEP_SECONDS = 0.25

CAPTURES = {
    "2 m": {
        "path": Path("/Users/lbussy/GitHub/WsprryPi-issue-379-evidence/2026-08-07-fixed-gain-sequence/verified-calibration-tone0.cf32"),
        "center_hz": 144_515_500.0,
        "nominal_hz": 144_490_497.802734375,
    },
    "30 m": {
        "path": Path("/Users/lbussy/GitHub/WsprryPi-issue-379-evidence/2026-08-07-30m-comparison/valid-tone0-30m.cf32"),
        "center_hz": 10_165_200.0,
        "nominal_hz": 10_140_197.802734375,
    },
}

if os.environ.get("STABILITY_CAPTURE_ROOT"):
    root = Path(os.environ["STABILITY_CAPTURE_ROOT"])
    CAPTURES["2 m"]["path"] = root / "2m-tone0-300s.cf32"
    CAPTURES["30 m"]["path"] = root / "30m-tone0-300s.cf32"


def robust_sigma(x):
    med = np.median(x)
    return 1.4826 * np.median(np.abs(x - med))


def load_blocks(cfg):
    iq = np.memmap(cfg["path"], dtype=np.complex64, mode="r")
    usable = (len(iq) // DECIM) * DECIM
    block_count = usable // DECIM
    block_starts = np.arange(block_count, dtype=np.float64) * DECIM
    offset = cfg["nominal_hz"] - cfg["center_hz"]
    n = np.arange(DECIM, dtype=np.float64)
    intra = np.exp(-2j * np.pi * offset * n / FS)
    z = np.empty(block_count, dtype=np.complex128)
    blocks_per_chunk = 1000
    for first in range(0, block_count, blocks_per_chunk):
        last = min(block_count, first + blocks_per_chunk)
        chunk = np.asarray(iq[first * DECIM:last * DECIM]).reshape(-1, DECIM)
        starts = np.exp(-2j * np.pi * offset * block_starts[first:last] / FS)
        z[first:last] = (chunk @ intra) / DECIM * starts
    t = (block_starts + (DECIM - 1) / 2) / FS
    return t, z, len(iq)


def analyze(label, cfg):
    t, z, sample_count = load_blocks(cfg)
    keep = (t >= EDGE_SECONDS) & (t <= t[-1] - EDGE_SECONDS)
    t, z = t[keep], z[keep]
    phase = np.unwrap(np.angle(z))

    win = int(WINDOW_SECONDS * BLOCK_RATE)
    step = int(STEP_SECONDS * BLOCK_RATE)
    rows = []
    for start in range(0, len(t) - win + 1, step):
        sl = slice(start, start + win)
        tx = t[sl]
        px = phase[sl]
        slope, intercept = np.polyfit(tx - tx.mean(), px, 1)
        fit = slope * (tx - tx.mean()) + intercept
        rows.append((tx.mean(), slope / (2 * np.pi),
                     math.sqrt(np.mean((px - fit) ** 2))))
    arr = np.asarray(rows)
    wt, freq, phase_rms = arr.T

    drift_hz_s, intercept = np.polyfit(wt - wt.mean(), freq, 1)
    trend = drift_hz_s * (wt - wt.mean()) + intercept
    detrended = freq - trend
    relative = freq - np.median(freq)

    # Adjacent residual phase increments expose true phase/frequency steps.
    q2, q1, q0 = np.polyfit(t - t.mean(), phase, 2)
    phase_fit = q2 * (t - t.mean()) ** 2 + q1 * (t - t.mean()) + q0
    phase_residual = phase - phase_fit
    jumps = np.diff(phase_residual)
    jump_sigma = robust_sigma(jumps)
    jump_threshold = max(math.pi / 2, 8 * jump_sigma)
    jump_count = int(np.count_nonzero(np.abs(jumps) > jump_threshold))

    amp_db = 20 * np.log10(np.maximum(np.abs(z), np.finfo(float).tiny))
    summary = {
        "band": label,
        "source": str(cfg["path"]),
        "sample_rate_hz": FS,
        "sample_count": sample_count,
        "capture_duration_s": sample_count / FS,
        "analyzed_duration_s": float(t[-1] - t[0]),
        "window_s": WINDOW_SECONDS,
        "step_s": STEP_SECONDS,
        "frequency_estimates": len(freq),
        "raw_frequency_median_hz": float(np.median(freq)),
        "relative_frequency_peak_to_peak_hz": float(np.ptp(relative)),
        "relative_frequency_p05_p95_hz": float(np.percentile(relative, 95) - np.percentile(relative, 5)),
        "linear_drift_hz_per_min": float(drift_hz_s * 60),
        "detrended_frequency_rms_hz": float(math.sqrt(np.mean(detrended ** 2))),
        "detrended_frequency_p05_p95_hz": float(np.percentile(detrended, 95) - np.percentile(detrended, 5)),
        "median_phase_fit_rms_rad": float(np.median(phase_rms)),
        "max_adjacent_phase_residual_jump_rad": float(np.max(np.abs(jumps))),
        "phase_jump_threshold_rad": float(jump_threshold),
        "phase_discontinuities": jump_count,
        "amplitude_median_dbfs": float(np.median(amp_db)),
        "amplitude_p05_p95_db": float(np.percentile(amp_db, 95) - np.percentile(amp_db, 5)),
        "amplitude_peak_to_peak_db": float(np.ptp(amp_db)),
    }
    series = [{"band": label, "time_s": float(a), "relative_frequency_hz": float(b),
               "linear_trend_hz": float(c - np.median(freq)),
               "phase_fit_rms_rad": float(d)}
              for a, b, c, d in zip(wt, relative, trend, phase_rms)]
    return summary, series


def main():
    out = Path(os.environ.get(
        "STABILITY_OUTPUT",
        str(Path(__file__).resolve().parent / "stability-results")))
    out.mkdir(exist_ok=True)
    summaries, series = [], []
    for label, cfg in CAPTURES.items():
        summary, rows = analyze(label, cfg)
        summaries.append(summary)
        series.extend(rows)
    (out / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n")
    with (out / "frequency-series.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=series[0].keys())
        writer.writeheader()
        writer.writerows(series)
    for label in CAPTURES:
        safe = label.replace(" ", "")
        with (out / f"{safe}-frequency-series.dat").open("w") as f:
            f.write("# time_s relative_frequency_hz linear_trend_hz\n")
            for row in series:
                if row["band"] == label:
                    f.write(f'{row["time_s"]:.6f} {row["relative_frequency_hz"]:.12f} {row["linear_trend_hz"]:.12f}\n')
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
