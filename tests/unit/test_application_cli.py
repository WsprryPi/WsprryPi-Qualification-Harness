import json
from pathlib import Path

import pytest

from wsprrypi_qualification.cli import main

ROOT = Path(__file__).resolve().parents[2]


def test_validate_example_application_plan(capsys: pytest.CaptureFixture[str]) -> None:
    result = main(
        [
            "validate-application-plan",
            str(ROOT / "examples" / "application-plan-wsprrypi-wspr.json"),
        ]
    )
    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["valid"] is True


def test_application_plan_command_cannot_execute(tmp_path: Path) -> None:
    marker = tmp_path / "was executed"
    document = json.loads(
        (ROOT / "examples" / "application-plan-wsprrypi-wspr.json").read_text(encoding="utf-8")
    )
    document["identity"]["executable"] = str(marker)
    document["arguments"][0] = str(marker)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(document), encoding="utf-8")
    assert main(["validate-application-plan", str(plan)]) == 0
    assert not marker.exists()
