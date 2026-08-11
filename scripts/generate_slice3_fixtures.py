"""Generate small deterministic offline-only Slice 3 CF32 fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def _new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite fixture: {path}")
    path.write_bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=False, exist_ok=False)
    rate, count, offset_hz = 4096, 4096 * 3, 500
    samples = np.arange(count, dtype=np.float64)
    fixtures = {
        "rf-off.cf32": np.zeros(count, dtype="<c8"),
        "rf-on.cf32": np.asarray(
            0.5 * np.exp(2j * np.pi * offset_hz * samples / rate), dtype="<c8"
        ),
    }
    records = []
    for name, values in fixtures.items():
        payload = values.tobytes()
        _new(args.output / name, payload)
        records.append(
            {
                "path": name,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    metadata = {
        "schema_version": 1,
        "generator": "scripts/generate_slice3_fixtures.py",
        "purpose": "hardware-free carrier-analysis positive fixture",
        "sample_format": "CF32 little-endian real/imaginary",
        "sample_rate_hz": rate,
        "sample_count": count,
        "center_frequency_hz": 10_000,
        "transmitter_added_offset_hz": offset_hz,
        "artifacts": records,
    }
    _new(
        args.output / "fixture.json",
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
