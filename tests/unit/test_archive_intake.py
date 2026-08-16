import hashlib
import json
from pathlib import Path

import pytest

from tests.unit.test_cw_contracts import _artifact, _chain, _write
from wsprrypi_qualification.archive_intake import (
    ArchiveIntakeError,
    inventory_archive,
    validate_multi_capture_session,
)
from wsprrypi_qualification.cli import main


def _manifest(root: Path, paths: list[Path]) -> Path:
    manifest = root / "ARCHIVE-SHA256SUMS"
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{path.relative_to(root).as_posix()}"
        for path in paths
    ]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def test_archive_inventory_authenticates_and_classifies_without_qualifying(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    (root / "evidence" / "run" / "decode").mkdir(parents=True)
    (root / "repositories" / "project").mkdir(parents=True)
    paths = [
        root / "evidence" / "run" / "capture.cf32.incomplete",
        root / "evidence" / "run" / "capture.json",
        root / "evidence" / "run" / "decode" / "slot.wav",
        root / "repositories" / "project" / "HEAD",
    ]
    for index, path in enumerate(paths):
        path.write_bytes(f"artifact-{index}".encode())
    manifest = _manifest(root, paths)
    output = tmp_path / "inventory.json"
    result = inventory_archive(root, manifest, output, archive_id="fixture-archive")
    assert result["qualification_claim"] is False
    assert result["summary"]["entry_count"] == 4
    assert [item["path"] for item in result["entries"]] == sorted(
        item["path"] for item in result["entries"]
    )
    assert {item["classification"] for item in result["entries"]} == {
        "incomplete_artifact",
        "historical_ad_hoc_evidence",
        "generated_derivative",
        "repository_snapshot",
    }
    assert result["manifest"]["path"] == "ARCHIVE-SHA256SUMS"


@pytest.mark.parametrize(
    "line",
    [
        f"{'a' * 64}  ../escape",
        f"{'a' * 64}  /absolute",
        f"{'a' * 64}  folder\\windows",
        "malformed",
    ],
)
def test_archive_inventory_rejects_unsafe_manifest_paths(tmp_path: Path, line: str) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    manifest = root / "SHA256SUMS"
    manifest.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(ArchiveIntakeError):
        inventory_archive(root, manifest, tmp_path / "out.json", archive_id="fixture")


def test_archive_inventory_rejects_duplicate_and_tampered_entries(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    source = root / "artifact.bin"
    source.write_bytes(b"original")
    manifest = _manifest(root, [source])
    duplicate = manifest.read_text(encoding="utf-8") * 2
    manifest.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ArchiveIntakeError, match="duplicate"):
        inventory_archive(root, manifest, tmp_path / "duplicate.json", archive_id="fixture")
    manifest = _manifest(root, [source])
    source.write_bytes(b"tampered")
    with pytest.raises(ArchiveIntakeError, match="SHA-256"):
        inventory_archive(root, manifest, tmp_path / "tampered.json", archive_id="fixture")


def test_archive_inventory_rejects_empty_manifest_and_symlink_entry(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    manifest = root / "SHA256SUMS"
    manifest.write_text("", encoding="utf-8")
    with pytest.raises(ArchiveIntakeError, match="at least one"):
        inventory_archive(root, manifest, tmp_path / "empty.json", archive_id="fixture")

    target = root / "target.bin"
    target.write_bytes(b"target")
    link = root / "link.bin"
    try:
        link.symlink_to(target.name)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    manifest.write_text(
        f"{hashlib.sha256(target.read_bytes()).hexdigest()}  ./link.bin\n", encoding="utf-8"
    )
    with pytest.raises(ArchiveIntakeError, match="non-symlink"):
        inventory_archive(root, manifest, tmp_path / "symlink.json", archive_id="fixture")


def _multi_capture_document(tmp_path: Path) -> tuple[Path, list[Path]]:
    plan, _, _, _, _ = _chain(tmp_path, "fskcw")
    plan_reference = _artifact(plan)
    repetitions = []
    captures = []
    for number in range(1, 4):
        capture = tmp_path / f"capture-{number}.cf32"
        capture.write_bytes(bytes([number]) * 16)
        captures.append(capture)
        metadata = tmp_path / f"metadata-{number}.json"
        observations = tmp_path / f"observations-{number}.json"
        _write(
            metadata,
            {
                "mode": "fskcw",
                "plan": plan_reference,
                "capture": _artifact(capture),
                "acquisition_id": f"acquisition-{number}",
            },
        )
        _write(
            observations,
            {
                "mode": "fskcw",
                "plan": plan_reference,
                "capture": _artifact(capture),
                "measurement": "fixture",
            },
        )
        repetitions.append(
            {
                "repetition": number,
                "acquisition_id": f"acquisition-{number}",
                "capture": _artifact(capture),
                "metadata": _artifact(metadata),
                "observations": _artifact(observations),
            }
        )
    session = tmp_path / "multi-capture.json"
    _write(
        session,
        {
            "schema_version": 1,
            "evidence_type": "cw_multi_capture_session",
            "session_id": "fixture-multi-capture",
            "mode": "fskcw",
            "plan": plan_reference,
            "repetitions": repetitions,
            "lifecycle_evidence": None,
            "failure_causes": ["multi_capture_lifecycle_not_composed"],
            "final_status": "inconclusive",
            "qualification_claim": False,
        },
    )
    return session, captures


def test_multi_capture_session_authenticates_distinct_repetitions(tmp_path: Path) -> None:
    session, _ = _multi_capture_document(tmp_path)
    result = validate_multi_capture_session(session)
    assert result == {
        "session_id": "fixture-multi-capture",
        "mode": "fskcw",
        "repetition_count": 3,
        "final_status": "inconclusive",
        "qualification_claim": False,
        "valid": True,
    }
    assert main(["validate-cw-multi-capture", str(session)]) == 0


def test_multi_capture_rejects_reorder_reuse_and_tampering(tmp_path: Path) -> None:
    session, captures = _multi_capture_document(tmp_path)
    document = json.loads(session.read_text(encoding="utf-8"))
    document["repetitions"][0]["repetition"] = 2
    _write(session, document)
    with pytest.raises(ArchiveIntakeError, match="ordered"):
        validate_multi_capture_session(session)

    session, _ = _multi_capture_document(tmp_path)
    document = json.loads(session.read_text(encoding="utf-8"))
    document["repetitions"][1]["capture"] = document["repetitions"][0]["capture"]
    _write(session, document)
    with pytest.raises(ArchiveIntakeError, match="cannot satisfy"):
        validate_multi_capture_session(session)

    session, captures = _multi_capture_document(tmp_path)
    captures[2].write_bytes(b"tampered")
    with pytest.raises(ArchiveIntakeError, match="identity mismatch"):
        validate_multi_capture_session(session)


def test_multi_capture_rejects_cross_role_and_semantic_binding_reuse(tmp_path: Path) -> None:
    session, _ = _multi_capture_document(tmp_path)
    document = json.loads(session.read_text(encoding="utf-8"))
    document["repetitions"][0]["observations"] = document["repetitions"][0]["metadata"]
    _write(session, document)
    with pytest.raises(ArchiveIntakeError, match="multiple multi-capture roles"):
        validate_multi_capture_session(session)

    session, _ = _multi_capture_document(tmp_path)
    document = json.loads(session.read_text(encoding="utf-8"))
    metadata_path = tmp_path / document["repetitions"][0]["metadata"]["path"]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["acquisition_id"] = "different-acquisition"
    _write(metadata_path, metadata)
    document["repetitions"][0]["metadata"] = _artifact(metadata_path)
    _write(session, document)
    with pytest.raises(ArchiveIntakeError, match="acquisition identity"):
        validate_multi_capture_session(session)

    session, _ = _multi_capture_document(tmp_path)
    document = json.loads(session.read_text(encoding="utf-8"))
    observations_path = tmp_path / document["repetitions"][0]["observations"]["path"]
    observations = json.loads(observations_path.read_text(encoding="utf-8"))
    observations["capture"]["sha256"] = "0" * 64
    _write(observations_path, observations)
    document["repetitions"][0]["observations"] = _artifact(observations_path)
    _write(session, document)
    with pytest.raises(ArchiveIntakeError, match="observations do not bind"):
        validate_multi_capture_session(session)


def test_inventory_cli_is_transactional_on_failure(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    manifest = root / "SHA256SUMS"
    manifest.write_text("invalid\n", encoding="utf-8")
    output = tmp_path / "inventory.json"
    assert (
        main(["inventory-archive", str(root), str(manifest), str(output), "--archive-id", "x"]) == 2
    )
    assert not output.exists()
