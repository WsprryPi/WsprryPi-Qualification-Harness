from __future__ import annotations

import hashlib
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "deployment" / "raspberry-pi-os" / "wspq-gpio-inspect"
ANCHOR = ROOT / "evidence-anchors" / "bounded-carrier-original-anchors.json"
WHEEL_PROVIDER = "wsprrypi_qualification/deployment_assets/wspq-gpio-inspect"
WHEEL_ANCHOR = "wsprrypi_qualification/evidence_anchors/bounded-carrier-original-anchors.json"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_sdist_and_wheel_retain_exact_reviewed_assets(tmp_path: Path) -> None:
    output = tmp_path / "artifacts with spaces"
    output.mkdir()
    subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    wheels = list(output.glob("*.whl"))
    sdists = list(output.glob("*.tar.gz"))
    assert len(wheels) == len(sdists) == 1
    provider_bytes = PROVIDER.read_bytes()
    anchor_bytes = ANCHOR.read_bytes()
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        assert names.count(WHEEL_PROVIDER) == 1
        assert names.count(WHEEL_ANCHOR) == 1
        packaged_provider = archive.read(WHEEL_PROVIDER)
        assert packaged_provider == provider_bytes
        assert digest(packaged_provider) == digest(provider_bytes)
        assert archive.read(WHEEL_ANCHOR) == anchor_bytes
        assert names.count("wsprrypi_qualification/schemas/carrier-run-correction.schema.json") == 1
        assert (
            names.count("wsprrypi_qualification/schemas/carrier-runtime-authorization.schema.json")
            == 1
        )
        # Portable invocation is explicit through Python; Windows need not preserve a Unix mode.
        assert packaged_provider.startswith(b"#!/usr/bin/env python3\n")
    with tarfile.open(sdists[0], "r:gz") as archive:
        provider_members = [name for name in archive.getnames() if name.endswith(WHEEL_PROVIDER)]
        source_members = [
            name
            for name in archive.getnames()
            if name.endswith("deployment/raspberry-pi-os/wspq-gpio-inspect")
        ]
        assert len(source_members) == 1
        extracted = archive.extractfile(source_members[0])
        assert extracted is not None and extracted.read() == provider_bytes
        assert len(provider_members) <= 1
