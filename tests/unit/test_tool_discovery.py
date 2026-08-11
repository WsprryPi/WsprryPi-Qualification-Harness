from pathlib import PureWindowsPath

from wsprrypi_qualification import tool_discovery
from wsprrypi_qualification.tool_discovery import bundled_candidates, discover_executable


def test_macos_wsjtx_app_bundle_candidate() -> None:
    candidates = bundled_candidates("wsprd", platform_name="darwin")
    assert str(candidates[0]) == "/Applications/wsjtx.app/Contents/MacOS/wsprd"


def test_windows_candidates_use_native_path_forms(monkeypatch) -> None:
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    candidates = bundled_candidates("wsprd", platform_name="win32")
    assert PureWindowsPath(r"C:\Program Files\WSJT\wsjtx\bin\wsprd.exe") in candidates
    assert PureWindowsPath(r"C:\WSJT\wsjtx\bin\wsprd.exe") in candidates


def test_path_discovery_precedes_bundle_candidates(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "directory with spaces" / "wsprd"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setattr(tool_discovery.shutil, "which", lambda _name: str(executable))
    assert discover_executable("wsprd") == executable.resolve()


def test_unavailable_discovery(monkeypatch) -> None:
    monkeypatch.setattr(tool_discovery.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tool_discovery, "bundled_candidates", lambda _name: ())
    assert discover_executable("wsprd") is None


def test_live_host_discovers_executable_absolute_path() -> None:
    found = discover_executable("wsprd")
    if found is not None:
        assert found.is_absolute() and found.is_file()
