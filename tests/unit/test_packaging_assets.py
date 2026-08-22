from __future__ import annotations

import hashlib
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "deployment" / "raspberry-pi-os" / "wspq-gpio-inspect"
WHEEL_PROVIDER = "wsprrypi_qualification/deployment_assets/wspq-gpio-inspect"


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
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        assert names.count(WHEEL_PROVIDER) == 1
        packaged_provider = archive.read(WHEEL_PROVIDER)
        assert packaged_provider == provider_bytes
        assert digest(packaged_provider) == digest(provider_bytes)
        # Portable invocation is explicit through Python; Windows need not preserve a Unix mode.
        assert packaged_provider.splitlines()[0] == b"#!/usr/bin/env python3"
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
