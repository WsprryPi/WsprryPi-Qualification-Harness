from pathlib import Path
from types import SimpleNamespace

import pytest

from wsprrypi_qualification.remote_staging import (
    RemoteStage,
    RemoteStagingError,
    StagedFile,
    build_runtime_archive,
    discover_remote_python,
)


def _stage(tmp_path: Path, source: Path, **kwargs) -> RemoteStage:
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("host key")
    return RemoteStage(
        "wspr5",
        (StagedFile(source, "runtime.whl"),),
        known_hosts=known_hosts,
        remote_python="/usr/bin/python3",
        remote_python_sha256="a" * 64,
        token="0123456789abcdef01234567",
        owner_token="b" * 64,
        **kwargs,
    )


def test_stage_copies_explicit_files_and_always_cleans(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "runtime.whl"
    source.write_bytes(b"wheel")
    calls: list[list[str]] = []

    def run(arguments, **kwargs):
        del kwargs
        calls.append(arguments)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", run)
    with _stage(tmp_path, source) as stage:
        assert stage.path("runtime.whl") == "/tmp/wspq-0123456789abcdef01234567/runtime.whl"
        operation = stage.run_python("print(sys.argv[1])", "argument")
        assert operation.returncode == 0
    assert "/tmp/wspq-0123456789abcdef01234567" in calls[0][-1]
    assert ".owner" in calls[0][-1]
    assert calls[1][-1] == "wspr5:/tmp/wspq-0123456789abcdef01234567/runtime.whl"
    assert "hashlib.sha256(p.read_bytes())" in calls[2][-1]
    assert "shutil.rmtree(p)" in calls[-1][-1]


def test_partial_copy_failure_still_attempts_cleanup(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "runtime.whl"
    source.write_bytes(b"wheel")
    return_codes = iter((0, 1, 0))
    calls: list[list[str]] = []

    def run(arguments, **kwargs):
        del kwargs
        calls.append(arguments)
        return SimpleNamespace(returncode=next(return_codes), stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", run)
    with (
        pytest.raises(RemoteStagingError, match="copy failed"),
        _stage(tmp_path, source),
    ):
        pass
    assert "shutil.rmtree(p)" in calls[-1][-1]


@pytest.mark.parametrize("host", ("bad host", "-option", "host/name"))
def test_rejects_unsafe_host(tmp_path: Path, host: str) -> None:
    source = tmp_path / "runtime.whl"
    source.write_bytes(b"wheel")
    with pytest.raises(RemoteStagingError, match="host"):
        known_hosts = tmp_path / "known_hosts"
        known_hosts.write_text("host key")
        RemoteStage(
            host,
            (StagedFile(source, "runtime.whl"),),
            known_hosts=known_hosts,
            remote_python="/usr/bin/python3",
            remote_python_sha256="a" * 64,
        )


def test_cleanup_failure_is_not_suppressed(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "runtime.whl"
    source.write_bytes(b"wheel")
    return_codes = iter((0, 0, 0, 1))

    def run(arguments, **kwargs):
        del arguments, kwargs
        return SimpleNamespace(returncode=next(return_codes), stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", run)
    with (
        pytest.raises(RemoteStagingError, match="cleanup"),
        _stage(tmp_path, source),
    ):
        pass


def test_ambiguous_creation_failure_attempts_idempotent_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "runtime.whl"
    source.write_bytes(b"wheel")
    calls: list[list[str]] = []

    def run(arguments, **kwargs):
        del kwargs
        calls.append(arguments)
        if len(calls) == 1:
            raise __import__("subprocess").TimeoutExpired(arguments, 1)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(RemoteStagingError, match="transport"), _stage(tmp_path, source):
        pass
    assert "os.path.lexists(p)" in calls[-1][-1]


def test_source_identity_change_before_copy_fails_and_cleans(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "runtime.whl"
    source.write_bytes(b"wheel")
    calls: list[list[str]] = []

    def run(arguments, **kwargs):
        del kwargs
        calls.append(arguments)
        source.write_bytes(b"changed")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(RemoteStagingError, match="source identity"), _stage(tmp_path, source):
        pass
    source.write_bytes(b"wheel")
    assert "shutil.rmtree" in calls[-1][-1]


def test_add_file_is_verified_and_becomes_addressable(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "runtime.whl"
    source.write_bytes(b"wheel")
    later = tmp_path / "config.json"
    later.write_text("{}")

    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    with _stage(tmp_path, source) as stage:
        assert stage.add_file(StagedFile(later, "config.json")).endswith("/config.json")
        assert stage.path("config.json").endswith("/config.json")
        with pytest.raises(RemoteStagingError, match="additional"):
            stage.add_file(StagedFile(later, "config.json"))


def test_remote_python_discovery_is_strict(tmp_path: Path, monkeypatch) -> None:
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("host key")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="/usr/bin/python3\n" + "a" * 64 + "\n",
            stderr="",
        ),
    )
    assert discover_remote_python("wspr5", known_hosts=known_hosts) == (
        "/usr/bin/python3",
        "a" * 64,
    )


def test_runtime_archive_is_reproducible_and_zip_importable(tmp_path: Path) -> None:
    source = tmp_path / "src"
    package = source / "wsprrypi_qualification"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 7\n")
    (package / "schemas").mkdir()
    (package / "schemas/example.json").write_text("{}\n")
    first = build_runtime_archive(source, tmp_path / "first.zip")
    second = build_runtime_archive(source, tmp_path / "second.zip")
    assert first.read_bytes() == second.read_bytes()
    import zipfile

    with zipfile.ZipFile(first) as archive:
        assert archive.read("wsprrypi_qualification/__init__.py") == b"VALUE = 7\n"
