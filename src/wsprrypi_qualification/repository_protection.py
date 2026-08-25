"""Fail-closed containment for target source repositories and mutable inputs."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class RepositoryProtectionError(RuntimeError):
    """A repository boundary or mutable runtime input is unsafe."""


Classification = Literal[
    "immutable_source_input",
    "staged_mutable_runtime_input",
    "generated_runtime_output",
    "retained_evidence",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(path: Path, *, must_exist: bool = True) -> Path:
    if not path.is_absolute():
        raise RepositoryProtectionError("protected paths must be absolute")
    try:
        return path.resolve(strict=must_exist)
    except (OSError, RuntimeError) as error:
        raise RepositoryProtectionError("path identity cannot be canonicalized") from error


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class ProtectedSourceRoot:
    path: Path
    git_directory: Path
    superproject_root: Path | None = None

    def document(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "git_directory": str(self.git_directory),
            "superproject_root": (
                None if self.superproject_root is None else str(self.superproject_root)
            ),
        }


@dataclass(frozen=True)
class SourceArtifactBinding:
    source_root: Path
    relative_path: str
    canonical_path: Path
    size_bytes: int
    mode: int
    sha256: str

    def document(self) -> dict[str, object]:
        return {
            "classification": "immutable_source_input",
            "source_root": str(self.source_root),
            "relative_path": self.relative_path,
            "canonical_path": str(self.canonical_path),
            "size_bytes": self.size_bytes,
            "mode": self.mode,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class RuntimeArtifactBinding:
    path: Path
    staging_root: Path
    namespace: str
    size_bytes: int
    mode: int
    sha256: str
    source: SourceArtifactBinding

    def document(self) -> dict[str, object]:
        return {
            "classification": "staged_mutable_runtime_input",
            "path": str(self.path),
            "staging_root": str(self.staging_root),
            "namespace": self.namespace,
            "size_bytes": self.size_bytes,
            "mode": self.mode,
            "sha256": self.sha256,
            "source": self.source.document(),
        }


@dataclass(frozen=True)
class RepositorySnapshot:
    root: Path
    head: str
    branch: str | None
    upstream: str | None
    status_porcelain_v2: str
    cached_diff_sha256: str
    worktree_fingerprint_sha256: str

    def document(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "head": self.head,
            "branch": self.branch,
            "upstream": self.upstream,
            "status_porcelain_v2": self.status_porcelain_v2,
            "cached_diff_sha256": self.cached_diff_sha256,
            "worktree_fingerprint_sha256": self.worktree_fingerprint_sha256,
        }


@dataclass(frozen=True)
class RepositoryIntegrityResult:
    root: Path
    outcome: Literal["unchanged", "integrity_failure", "unavailable"]
    before: RepositorySnapshot
    after: RepositorySnapshot | None
    changed_fields: tuple[str, ...]
    repair_attempted: Literal[False] = False
    qualification_claim: Literal[False] = False

    def document(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "outcome": self.outcome,
            "before": self.before.document(),
            "after": None if self.after is None else self.after.document(),
            "changed_fields": list(self.changed_fields),
            "repair_attempted": False,
            "qualification_claim": False,
        }


def _git(git: Path, root: Path, *arguments: str, allow_missing: bool = False) -> str | None:
    result = subprocess.run(
        (str(git), "-C", str(root), *arguments),
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    if result.returncode != 0:
        if allow_missing:
            return None
        raise RepositoryProtectionError("Git repository identity is unavailable")
    return result.stdout.rstrip("\n")


def discover_protected_roots(
    candidates: Sequence[Path], *, git_executable: Path
) -> tuple[ProtectedSourceRoot, ...]:
    """Discover ordinary, linked, nested, and superproject repository roots."""
    git = _canonical(git_executable)
    if not git.is_file():
        raise RepositoryProtectionError("Git executable is unavailable")
    discovered: dict[Path, ProtectedSourceRoot] = {}
    pending = list(candidates)
    while pending:
        candidate = _canonical(pending.pop())
        probe = candidate if candidate.is_dir() else candidate.parent
        top_text = _git(git, probe, "rev-parse", "--show-toplevel", allow_missing=True)
        if not top_text:
            continue
        top = _canonical(Path(top_text))
        git_dir_text = _git(git, top, "rev-parse", "--absolute-git-dir")
        assert git_dir_text is not None
        git_directory = _canonical(Path(git_dir_text))
        super_text = _git(
            git, top, "rev-parse", "--show-superproject-working-tree", allow_missing=True
        )
        super_root = _canonical(Path(super_text)) if super_text else None
        discovered[top] = ProtectedSourceRoot(top, git_directory, super_root)
        for marker in top.rglob(".git"):
            if marker.parent != top and marker.parent not in discovered:
                pending.append(marker.parent)
        if super_root is not None and super_root not in discovered:
            pending.append(super_root)
    return tuple(discovered[path] for path in sorted(discovered, key=str))


def assert_outside_protected(
    path: Path, roots: Sequence[ProtectedSourceRoot], *, must_exist: bool = True
) -> Path:
    canonical = _canonical(path, must_exist=must_exist)
    if any(_inside(canonical, root.path) for root in roots):
        raise RepositoryProtectionError("runtime path resolves inside a protected repository")
    return canonical


def bind_source(path: Path, roots: Sequence[ProtectedSourceRoot]) -> SourceArtifactBinding:
    if path.is_symlink():
        raise RepositoryProtectionError("mutable source inputs must not be symlinks")
    canonical = _canonical(path)
    matching = [root.path for root in roots if _inside(canonical, root.path)]
    if not matching:
        raise RepositoryProtectionError("source input is outside every protected source root")
    source_root = max(matching, key=lambda item: len(item.parts))
    before = canonical.stat()
    if not stat.S_ISREG(before.st_mode):
        raise RepositoryProtectionError("source input must be a regular file")
    digest = _sha256(canonical)
    after = canonical.stat()
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise RepositoryProtectionError("source input changed while it was being bound")
    return SourceArtifactBinding(
        source_root,
        canonical.relative_to(source_root).as_posix(),
        canonical,
        before.st_size,
        stat.S_IMODE(before.st_mode),
        digest,
    )


def stage_mutable_input(
    source: SourceArtifactBinding,
    destination: Path,
    *,
    staging_root: Path,
    namespace: str,
    roots: Sequence[ProtectedSourceRoot],
    mode: int = 0o600,
) -> RuntimeArtifactBinding:
    """Copy one mutable input with exclusive creation outside protected roots."""
    if staging_root.is_symlink():
        raise RepositoryProtectionError("staging root must not be a symlink")
    stage = assert_outside_protected(staging_root, roots)
    if not stage.is_dir() or stage.is_symlink():
        raise RepositoryProtectionError("staging root must be an existing real directory")
    if not namespace or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        for character in namespace
    ):
        raise RepositoryProtectionError("staging namespace is invalid")
    target = destination
    if not target.is_absolute():
        raise RepositoryProtectionError("staged mutable input path must be absolute")
    parent = target.parent.resolve(strict=True)
    if not _inside(parent, stage):
        raise RepositoryProtectionError("staged mutable input escapes its staging root")
    if target.exists() or target.is_symlink():
        raise RepositoryProtectionError("staged mutable input must be newly created")
    source_before = source.canonical_path.stat()
    if _sha256(source.canonical_path) != source.sha256:
        raise RepositoryProtectionError("source input changed before staging")
    data = source.canonical_path.read_bytes()
    source_after = source.canonical_path.stat()
    if (
        source_before.st_dev,
        source_before.st_ino,
        source_before.st_size,
        source_before.st_mtime_ns,
    ) != (
        source_after.st_dev,
        source_after.st_ino,
        source_after.st_size,
        source_after.st_mtime_ns,
    ) or hashlib.sha256(data).hexdigest() != source.sha256:
        raise RepositoryProtectionError("source input changed while staging")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(target, mode)
    finally:
        os.close(descriptor)
    canonical_target = assert_outside_protected(target, roots)
    observed = canonical_target.stat()
    digest = _sha256(canonical_target)
    if observed.st_size != source.size_bytes or digest != source.sha256:
        raise RepositoryProtectionError("staged mutable input differs from its source binding")
    return RuntimeArtifactBinding(
        canonical_target,
        stage,
        namespace,
        observed.st_size,
        stat.S_IMODE(observed.st_mode),
        digest,
        source,
    )


def capture_repository_snapshot(
    root: ProtectedSourceRoot, *, git_executable: Path
) -> RepositorySnapshot:
    git = _canonical(git_executable)
    head = _git(git, root.path, "rev-parse", "HEAD")
    branch = _git(git, root.path, "symbolic-ref", "--quiet", "--short", "HEAD", allow_missing=True)
    upstream = _git(
        git,
        root.path,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        allow_missing=True,
    )
    status_text = _git(
        git,
        root.path,
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    cached = _git(git, root.path, "diff", "--cached", "--binary", "--no-ext-diff")
    assert head is not None and status_text is not None and cached is not None
    return RepositorySnapshot(
        root.path,
        head,
        branch or None,
        upstream or None,
        status_text,
        hashlib.sha256(cached.encode("utf-8")).hexdigest(),
        _worktree_fingerprint(root.path),
    )


def _worktree_fingerprint(root: Path) -> str:
    """Bind every non-Git path's bytes, symlink target, kind, and portable mode."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        metadata = path.lstat()
        kind = "link" if path.is_symlink() else "dir" if path.is_dir() else "file"
        header = f"{relative.as_posix()}\0{kind}\0{stat.S_IMODE(metadata.st_mode):o}\0".encode()
        digest.update(header)
        if path.is_symlink():
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        elif path.is_file():
            digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def compare_repository_snapshot(
    before: RepositorySnapshot, root: ProtectedSourceRoot, *, git_executable: Path
) -> RepositoryIntegrityResult:
    try:
        after = capture_repository_snapshot(root, git_executable=git_executable)
    except RepositoryProtectionError:
        return RepositoryIntegrityResult(root.path, "unavailable", before, None, ("snapshot",))
    fields = tuple(
        name
        for name in (
            "head",
            "branch",
            "upstream",
            "status_porcelain_v2",
            "cached_diff_sha256",
            "worktree_fingerprint_sha256",
        )
        if getattr(before, name) != getattr(after, name)
    )
    return RepositoryIntegrityResult(
        root.path, "unchanged" if not fields else "integrity_failure", before, after, fields
    )


def validate_process_boundary(
    *,
    arguments: Sequence[str],
    working_directory: Path,
    mutable_inputs: Sequence[RuntimeArtifactBinding],
    writable_paths: Sequence[Path],
    roots: Sequence[ProtectedSourceRoot],
) -> None:
    """Recheck staged inputs, argv, cwd, and every declared writable path."""
    if not arguments:
        raise RepositoryProtectionError("process argument vector is empty")
    assert_outside_protected(working_directory, roots)
    for writable in writable_paths:
        resolved = writable if writable.is_absolute() else working_directory / writable
        assert_outside_protected(resolved, roots, must_exist=False)
    for binding in mutable_inputs:
        if binding.path == binding.source.canonical_path:
            raise RepositoryProtectionError("source and staged mutable paths are identical")
        assert_outside_protected(binding.path, roots)
        if str(binding.path) not in arguments:
            raise RepositoryProtectionError("staged mutable input is absent from process argv")
        if _sha256(binding.path) != binding.sha256:
            raise RepositoryProtectionError("staged mutable input changed before launch")
        if _sha256(binding.source.canonical_path) != binding.source.sha256:
            raise RepositoryProtectionError("protected source input changed before launch")
        if str(binding.source.canonical_path) in arguments:
            raise RepositoryProtectionError("process argv references protected mutable source")
