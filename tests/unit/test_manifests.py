import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from wsprrypi_qualification.manifests import (
    build_manifest,
    normalize_manifest_path,
    render_manifest,
    validate_manifest_name,
    write_manifest,
)


def test_deterministic_order_hashes_and_spaces(tmp_path: Path) -> None:
    root = tmp_path / "run with spaces"
    (root / "nested").mkdir(parents=True)
    (root / "z.txt").write_bytes(b"z\n")
    (root / "nested" / "a file.txt").write_bytes(b"a\n")
    records = build_manifest(root)
    assert [record.path for record in records] == ["nested/a file.txt", "z.txt"]
    assert records[0].sha256 == hashlib.sha256(b"a\n").hexdigest()
    assert render_manifest(records) == render_manifest(build_manifest(root))


def test_write_manifest_excludes_itself_and_is_repeatable(tmp_path: Path) -> None:
    (tmp_path / "artifact.bin").write_bytes(b"payload")
    first = write_manifest(tmp_path).read_text(encoding="utf-8")
    second = write_manifest(tmp_path).read_text(encoding="utf-8")
    assert first == second
    assert "SHA256SUMS" not in first


def test_safe_custom_manifest_name(tmp_path: Path) -> None:
    (tmp_path / "artifact").write_bytes(b"payload")
    destination = write_manifest(tmp_path, "ARTIFACT-SHA256.txt")
    assert destination == tmp_path / "ARTIFACT-SHA256.txt"
    assert destination.is_file()


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../outside",
        "..\\outside",
        "/absolute",
        "C:\\outside",
        "\\\\server\\share",
        "subdirectory/manifest",
        "subdirectory\\manifest",
        "trailing.",
        "trailing ",
        "line\nbreak",
        "CON",
        "con.txt",
        "NUL.json",
        "COM1",
        "com9.log",
        "LPT1",
        "lpt9.txt",
        "bad<name",
        "bad>name",
        'bad"name',
        "bad|name",
        "bad?name",
        "bad*name",
    ],
)
def test_unsafe_manifest_names_are_rejected_without_writes(tmp_path: Path, name: str) -> None:
    root = tmp_path / "run"
    root.mkdir()
    before = set(tmp_path.rglob("*"))
    with pytest.raises(ValueError):
        write_manifest(root, name)
    assert set(tmp_path.rglob("*")) == before


def test_manifest_name_length_limit() -> None:
    with pytest.raises(ValueError, match="too long"):
        validate_manifest_name("x" * 256)


@pytest.mark.parametrize(
    "name",
    ["SHA256SUMS", "ARTIFACT-SHA256.txt", "manifest with spaces.txt", "console.txt"],
)
def test_portable_manifest_names_are_accepted(name: str) -> None:
    assert validate_manifest_name(name) == name


def test_empty_directory_manifest(tmp_path: Path) -> None:
    assert build_manifest(tmp_path) == []
    assert write_manifest(tmp_path).read_bytes() == b""


def test_content_change_changes_hash(tmp_path: Path) -> None:
    path = tmp_path / "data"
    path.write_bytes(b"one")
    before = build_manifest(tmp_path)[0].sha256
    path.write_bytes(b"two")
    assert build_manifest(tmp_path)[0].sha256 != before


def test_incomplete_artifacts_are_excluded(tmp_path: Path) -> None:
    (tmp_path / "capture.incomplete-attempt").write_bytes(b"partial")
    assert build_manifest(tmp_path) == []


def test_symlinks_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"value")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable on this host")
    with pytest.raises(ValueError, match="symlinks"):
        build_manifest(tmp_path)


def test_portable_path_normalization() -> None:
    assert normalize_manifest_path(PureWindowsPath("folder", "file.txt")) == "folder/file.txt"
    assert normalize_manifest_path(PurePosixPath("folder/file.txt")) == "folder/file.txt"
    with pytest.raises(ValueError):
        normalize_manifest_path(PureWindowsPath("C:/outside.txt"))
    with pytest.raises(ValueError):
        normalize_manifest_path(PurePosixPath("../outside.txt"))
