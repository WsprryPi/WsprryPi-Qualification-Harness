from __future__ import annotations

import hashlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from wsprrypi_qualification.repository_protection import (
    RepositoryProtectionError,
    bind_source,
    capture_repository_snapshot,
    compare_repository_snapshot,
    discover_protected_roots,
    stage_mutable_input,
    validate_process_boundary,
)


def _git() -> Path:
    result = subprocess.run(
        ("git", "--exec-path"), check=True, capture_output=True, text=True, encoding="utf-8"
    )
    executable = Path(result.args[0])
    if not executable.is_absolute():
        from shutil import which

        found = which("git")
        assert found is not None
        executable = Path(found)
    return executable.resolve()


def _run(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        (str(_git()), "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _repository(root: Path) -> Path:
    root.mkdir(parents=True)
    _run(root, "init", "-q")
    _run(root, "config", "user.email", "fixture@example.invalid")
    _run(root, "config", "user.name", "Fixture")
    (root / "config").mkdir()
    (root / "config/wsprrypi.ini").write_bytes(b"[GPIO]\nvalue = old\n")
    _run(root, "add", "config/wsprrypi.ini")
    _run(root, "commit", "-qm", "fixture")
    return root


def test_discovers_checkout_linked_worktree_nested_repository_and_spaces(tmp_path: Path) -> None:
    primary = _repository(tmp_path / "source repository")
    linked = tmp_path / "linked worktree"
    _run(primary, "worktree", "add", "-q", "-b", "linked-fixture", str(linked))
    nested = _repository(primary / "nested repository")
    roots = discover_protected_roots(
        [primary / "config/wsprrypi.ini", linked, nested], git_executable=_git()
    )
    paths = {root.path for root in roots}
    assert primary.resolve() in paths
    assert linked.resolve() in paths
    assert nested.resolve() in paths
    linked_root = next(root for root in roots if root.path == linked.resolve())
    assert linked_root.git_directory.is_dir()


def test_path_outside_repository_is_not_invented(tmp_path: Path) -> None:
    outside = tmp_path / "ordinary directory"
    outside.mkdir()
    assert discover_protected_roots([outside], git_executable=_git()) == ()


def test_symlink_into_repository_is_protected_and_mutable_source_symlink_rejected(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path / "source")
    link = tmp_path / "outside-link.ini"
    try:
        link.symlink_to(root / "config/wsprrypi.ini")
    except OSError:
        pytest.skip("symlinks unavailable")
    roots = discover_protected_roots([link], git_executable=_git())
    assert roots[0].path == root.resolve()
    with pytest.raises(RepositoryProtectionError, match="symlink"):
        bind_source(link, roots)


def test_symlink_out_of_repository_cannot_be_bound_as_repository_source(tmp_path: Path) -> None:
    root = _repository(tmp_path / "source")
    outside = tmp_path / "outside.ini"
    outside.write_text("outside", encoding="utf-8")
    link = root / "config/outside.ini"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    roots = discover_protected_roots([root], git_executable=_git())
    with pytest.raises(RepositoryProtectionError):
        bind_source(link, roots)


def test_staging_is_exclusive_external_and_preserves_source(tmp_path: Path) -> None:
    root = _repository(tmp_path / "source")
    roots = discover_protected_roots([root], git_executable=_git())
    source_path = root / "config/wsprrypi.ini"
    source_mode = stat.S_IMODE(source_path.stat().st_mode)
    source = bind_source(source_path, roots)
    staging = tmp_path / "runtime staging"
    staging.mkdir(mode=0o700)
    target = staging / "wsprrypi.ini"
    runtime = stage_mutable_input(
        source,
        target,
        staging_root=staging,
        namespace="run-1",
        roots=roots,
    )
    assert runtime.path.read_bytes() == source_path.read_bytes()
    assert runtime.sha256 == source.sha256
    assert stat.S_IMODE(runtime.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(source_path.stat().st_mode) == source_mode
    with pytest.raises(RepositoryProtectionError, match="newly created"):
        stage_mutable_input(
            source,
            target,
            staging_root=staging,
            namespace="run-1",
            roots=roots,
        )
    with pytest.raises(RepositoryProtectionError, match="protected"):
        stage_mutable_input(
            source,
            root / "runtime.ini",
            staging_root=root,
            namespace="run-1",
            roots=roots,
        )


def test_wspr4_incident_regression_rewrites_only_staged_ini(tmp_path: Path) -> None:
    root = _repository(tmp_path / "WsprryPi")
    dirty = root / "operator-notes.txt"
    dirty.write_text("pre-existing operator work", encoding="utf-8")
    roots = discover_protected_roots([root], git_executable=_git())
    before = capture_repository_snapshot(roots[0], git_executable=_git())
    source_path = root / "config/wsprrypi.ini"
    source_bytes = source_path.read_bytes()
    source_metadata = source_path.stat()
    staging = tmp_path / "complete-test-runtime"
    staging.mkdir()
    runtime = stage_mutable_input(
        bind_source(source_path, roots),
        staging / "wsprrypi.ini",
        staging_root=staging,
        namespace="bounded-tone",
        roots=roots,
    )
    child = tmp_path / "fake wsprrypi.py"
    child.write_text(
        "import pathlib,sys\np=pathlib.Path(sys.argv[sys.argv.index('-i')+1])\n"
        "p.write_text('[GPIO]\\nvalue = normalized\\n')\n",
        encoding="utf-8",
    )
    arguments = (str(Path(sys.executable).resolve()), str(child), "-i", str(runtime.path))
    validate_process_boundary(
        arguments=arguments,
        working_directory=staging,
        mutable_inputs=[runtime],
        writable_paths=[runtime.path],
        roots=roots,
    )
    subprocess.run(arguments, check=True, cwd=staging)
    assert runtime.path.read_bytes() != source_bytes
    assert source_path.read_bytes() == source_bytes
    assert source_path.stat().st_ino == source_metadata.st_ino
    integrity = compare_repository_snapshot(before, roots[0], git_executable=_git())
    assert integrity.outcome == "unchanged"
    assert dirty.read_text(encoding="utf-8") == "pre-existing operator work"


def test_legacy_repository_ini_and_repository_cwd_fail_before_launch(tmp_path: Path) -> None:
    root = _repository(tmp_path / "WsprryPi")
    roots = discover_protected_roots([root], git_executable=_git())
    source = bind_source(root / "config/wsprrypi.ini", roots)
    staging = tmp_path / "runtime"
    staging.mkdir()
    runtime = stage_mutable_input(
        source,
        staging / "wsprrypi.ini",
        staging_root=staging,
        namespace="tone",
        roots=roots,
    )
    with pytest.raises(RepositoryProtectionError, match="protected mutable source"):
        validate_process_boundary(
            arguments=("/opt/wsprrypi", "-i", str(source.canonical_path), str(runtime.path)),
            working_directory=staging,
            mutable_inputs=[runtime],
            writable_paths=[],
            roots=roots,
        )
    with pytest.raises(RepositoryProtectionError, match="protected repository"):
        validate_process_boundary(
            arguments=("/opt/wsprrypi", "-i", str(runtime.path)),
            working_directory=root,
            mutable_inputs=[runtime],
            writable_paths=[],
            roots=roots,
        )


def test_integrity_failure_is_reported_without_repair(tmp_path: Path) -> None:
    root = _repository(tmp_path / "source")
    roots = discover_protected_roots([root], git_executable=_git())
    before = capture_repository_snapshot(roots[0], git_executable=_git())
    changed = root / "config/wsprrypi.ini"
    changed.write_text("child mutation", encoding="utf-8")
    result = compare_repository_snapshot(before, roots[0], git_executable=_git())
    assert result.outcome == "integrity_failure"
    assert "status_porcelain_v2" in result.changed_fields
    assert result.repair_attempted is False
    assert changed.read_text(encoding="utf-8") == "child mutation"


def test_staged_binding_changes_when_bytes_change(tmp_path: Path) -> None:
    root = _repository(tmp_path / "source")
    roots = discover_protected_roots([root], git_executable=_git())
    staging = tmp_path / "runtime"
    staging.mkdir()
    runtime = stage_mutable_input(
        bind_source(root / "config/wsprrypi.ini", roots),
        staging / "wsprrypi.ini",
        staging_root=staging,
        namespace="run",
        roots=roots,
    )
    runtime.path.write_text("changed", encoding="utf-8")
    assert hashlib.sha256(runtime.path.read_bytes()).hexdigest() != runtime.sha256
    with pytest.raises(RepositoryProtectionError, match="changed before launch"):
        validate_process_boundary(
            arguments=("/opt/wsprrypi", "-i", str(runtime.path)),
            working_directory=staging,
            mutable_inputs=[runtime],
            writable_paths=[],
            roots=roots,
        )


def test_relative_outputs_resolve_only_under_external_runtime_directory(tmp_path: Path) -> None:
    root = _repository(tmp_path / "source")
    roots = discover_protected_roots([root], git_executable=_git())
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    validate_process_boundary(
        arguments=("/opt/fake",),
        working_directory=runtime,
        mutable_inputs=[],
        writable_paths=[Path("logs/process.log")],
        roots=roots,
    )
    with pytest.raises(RepositoryProtectionError, match="protected repository"):
        validate_process_boundary(
            arguments=("/opt/fake",),
            working_directory=root,
            mutable_inputs=[],
            writable_paths=[Path("process.log")],
            roots=roots,
        )


def test_windows_style_mutable_path_is_rejected_as_relative_on_posix(tmp_path: Path) -> None:
    root = _repository(tmp_path / "source")
    roots = discover_protected_roots([root], git_executable=_git())
    source = bind_source(root / "config/wsprrypi.ini", roots)
    staging = tmp_path / "runtime"
    staging.mkdir()
    with pytest.raises(RepositoryProtectionError, match="must be absolute"):
        stage_mutable_input(
            source,
            Path(r"C:\\runtime\\wsprrypi.ini"),
            staging_root=staging,
            namespace="run",
            roots=roots,
        )


def test_symlinked_staging_root_is_rejected(tmp_path: Path) -> None:
    root = _repository(tmp_path / "source")
    roots = discover_protected_roots([root], git_executable=_git())
    real_stage = tmp_path / "real-stage"
    real_stage.mkdir()
    linked_stage = tmp_path / "linked-stage"
    try:
        linked_stage.symlink_to(real_stage, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(RepositoryProtectionError, match="must not be a symlink"):
        stage_mutable_input(
            bind_source(root / "config/wsprrypi.ini", roots),
            linked_stage / "wsprrypi.ini",
            staging_root=linked_stage,
            namespace="run",
            roots=roots,
        )


def test_metadata_only_repository_change_is_an_integrity_failure(tmp_path: Path) -> None:
    root = _repository(tmp_path / "source")
    roots = discover_protected_roots([root], git_executable=_git())
    before = capture_repository_snapshot(roots[0], git_executable=_git())
    source = root / "config/wsprrypi.ini"
    source.chmod(stat.S_IMODE(source.stat().st_mode) ^ stat.S_IXUSR)
    result = compare_repository_snapshot(before, roots[0], git_executable=_git())
    assert result.outcome == "integrity_failure"
    assert "worktree_fingerprint_sha256" in result.changed_fields
