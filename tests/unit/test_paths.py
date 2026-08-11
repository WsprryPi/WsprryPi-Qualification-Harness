from pathlib import PurePosixPath, PureWindowsPath

from wsprrypi_qualification.manifests import normalize_manifest_path


def test_posix_windows_and_space_paths() -> None:
    assert normalize_manifest_path(PurePosixPath("run data/result.json")) == "run data/result.json"
    assert (
        normalize_manifest_path(PureWindowsPath("run data", "slot 1.wav")) == "run data/slot 1.wav"
    )
