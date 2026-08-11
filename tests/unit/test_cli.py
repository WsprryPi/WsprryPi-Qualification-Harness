import json
from pathlib import Path

import pytest

from wsprrypi_qualification.cli import main

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


def test_enable_rf_fails_closed(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--enable-rf"]) == 2
    assert "unavailable in Slice 1" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["run", "capture", "transmit", "tone"])
def test_future_live_commands_fail_closed(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([command]) == 2
    assert "unavailable in Slice 1" in capsys.readouterr().err
