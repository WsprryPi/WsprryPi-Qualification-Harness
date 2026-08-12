import json
from pathlib import Path

import pytest

from wsprrypi_qualification.cli import main
from wsprrypi_qualification.offline import (
    FailureCause,
    OfflineAnalysisError,
    write_offline_failure,
)

ROOT = Path(__file__).resolve().parents[2]


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip()


def test_capabilities_are_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["capabilities"]) == 0
    assert json.loads(capsys.readouterr().out)["read_only"] is True


def test_validate_profile(capsys: pytest.CaptureFixture[str]) -> None:
    path = ROOT / "examples" / "bench-wspr5-rsp1b.json"
    assert main(["validate-profile", "bench", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_invalid_profile_returns_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{}", encoding="utf-8")
    assert main(["validate-profile", "bench", str(path)]) == 2
    assert str(path) in capsys.readouterr().err


def test_validate_capture_metadata(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.unit.test_capture_metadata import capture_document

    path = tmp_path / "capture metadata.json"
    path.write_text(json.dumps(capture_document()), encoding="utf-8")
    assert main(["validate-capture-metadata", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_enable_rf_fails_closed(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--enable-rf"]) == 2
    assert "unavailable in Slice 4" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["run", "capture", "transmit", "tone"])
def test_future_live_commands_fail_closed(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([command]) == 2
    assert "unavailable in Slice 4" in capsys.readouterr().err


def test_offline_rejection_writes_failure_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "failure.json"
    assert (
        main(
            [
                "decode-wspr",
                str(tmp_path / "missing.wav"),
                str(tmp_path / "missing-audio.json"),
                str(evidence),
            ]
        )
        == 2
    )
    assert json.loads(evidence.read_text(encoding="utf-8"))["evidence_type"] == "offline_failure"
    assert capsys.readouterr().err


def test_failure_classification_is_independent_of_message_text(tmp_path: Path) -> None:
    first = write_offline_failure(
        tmp_path / "first.json",
        "decode-wspr",
        OfflineAnalysisError(
            "dependency words are deliberately absent",
            cause=FailureCause.DEPENDENCY_UNAVAILABLE,
            gate_outcome="blocked",
        ),
    )
    second = write_offline_failure(
        tmp_path / "second.json",
        "decode-wspr",
        OfflineAnalysisError(
            "completely different wording",
            cause=FailureCause.DEPENDENCY_UNAVAILABLE,
            gate_outcome="blocked",
        ),
    )
    assert first["failure_cause"] == second["failure_cause"] == "dependency_unavailable"
