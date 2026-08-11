"""Deterministic SHA-256 manifests for retained evidence artifacts."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path, PurePath, PureWindowsPath

from wsprrypi_qualification.models import ArtifactRecord

DEFAULT_MANIFEST_NAME = "SHA256SUMS"
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
WINDOWS_INVALID_CHARACTERS = frozenset('<>:"/\\|?*')


def validate_manifest_name(manifest_name: str) -> str:
    if not manifest_name or manifest_name in {".", ".."}:
        raise ValueError("manifest name must be one non-empty filename")
    if any(character in WINDOWS_INVALID_CHARACTERS for character in manifest_name):
        raise ValueError("manifest name contains a character invalid on Windows")
    if manifest_name.endswith((".", " ")):
        raise ValueError("manifest name must not end with a dot or space")
    if any(ord(character) < 32 or ord(character) == 127 for character in manifest_name):
        raise ValueError("manifest name must not contain control characters")
    if len(manifest_name) > 255:
        raise ValueError("manifest name is too long")
    if manifest_name.split(".", maxsplit=1)[0].upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError("manifest name is reserved on Windows")
    return manifest_name


def normalize_manifest_path(path: PurePath) -> str:
    if isinstance(path, PureWindowsPath):
        parts = path.parts
        if path.drive or path.root:
            raise ValueError("manifest paths must be relative")
    else:
        parts = path.parts
        if path.is_absolute():
            raise ValueError("manifest paths must be relative")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("manifest path is empty or unsafe")
    return "/".join(parts)


def _sha256_stable(path: Path) -> tuple[int, str]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"artifact changed while hashing: {path}")
    return after.st_size, digest.hexdigest()


def build_manifest(root: Path, manifest_name: str = DEFAULT_MANIFEST_NAME) -> list[ArtifactRecord]:
    root = root.resolve()
    manifest_name = validate_manifest_name(manifest_name)
    if not root.is_dir():
        raise ValueError("artifact root must be an existing directory")
    records: list[ArtifactRecord] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        normalized = normalize_manifest_path(relative)
        if normalized == manifest_name or ".incomplete-" in path.name:
            continue
        if path.is_symlink():
            raise ValueError(f"symlinks are not permitted in evidence bundles: {normalized}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"non-regular artifact is not permitted: {normalized}")
        size, digest = _sha256_stable(path)
        records.append(ArtifactRecord(normalized, size, digest))
    return sorted(records, key=lambda record: record.path)


def render_manifest(records: list[ArtifactRecord]) -> str:
    return "".join(f"{record.sha256}  {record.path}\n" for record in records)


def write_manifest(root: Path, manifest_name: str = DEFAULT_MANIFEST_NAME) -> Path:
    root = root.resolve()
    manifest_name = validate_manifest_name(manifest_name)
    destination = root / manifest_name
    if destination.parent != root:
        raise ValueError("manifest destination must remain directly inside the artifact root")
    content = render_manifest(build_manifest(root, manifest_name))
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{manifest_name}.incomplete-",
            dir=root,
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination
