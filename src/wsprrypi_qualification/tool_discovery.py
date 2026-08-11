"""Cross-platform executable discovery without executing external tools."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path, PurePath, PureWindowsPath


def bundled_candidates(name: str, *, platform_name: str | None = None) -> tuple[PurePath, ...]:
    platform_name = sys.platform if platform_name is None else platform_name
    if name != "wsprd":
        return ()
    if platform_name == "darwin":
        return (
            Path("/Applications/wsjtx.app/Contents/MacOS/wsprd"),
            Path("/Applications/WSJT-X.app/Contents/MacOS/wsprd"),
            Path.home() / "Applications/wsjtx.app/Contents/MacOS/wsprd",
            Path.home() / "Applications/WSJT-X.app/Contents/MacOS/wsprd",
        )
    if platform_name == "win32":
        candidates: list[PurePath] = [PureWindowsPath(r"C:\WSJT\wsjtx\bin\wsprd.exe")]
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(variable)
            if root:
                candidates.extend(
                    [
                        PureWindowsPath(root) / "WSJT" / "wsjtx" / "bin" / "wsprd.exe",
                        PureWindowsPath(root) / "wsjtx" / "bin" / "wsprd.exe",
                    ]
                )
        return tuple(candidates)
    return ()


def discover_executable(name: str) -> Path | None:
    found = shutil.which(name)
    if found is not None:
        return Path(found).resolve()
    for candidate in bundled_candidates(name):
        path = Path(candidate)
        if path.is_file():
            return path.resolve()
    return None
