"""Install a wheel in a new venv and run the installed-package smoke test."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    wheels = sorted(Path("dist").glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel, found {len(wheels)}")
    root = Path(tempfile.mkdtemp(prefix="wspq-wheel-smoke-"))
    venv = root / "venv"
    subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.check_call([str(python), "-m", "pip", "install", str(wheels[0].resolve())])
    smoke = root / "verify_installed_live_package.py"
    shutil.copyfile("scripts/verify_installed_live_package.py", smoke)
    subprocess.check_call([str(python), str(smoke)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
