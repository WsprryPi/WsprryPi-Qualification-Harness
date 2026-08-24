import json
import sys
from pathlib import Path

import pytest

from wsprrypi_qualification.offline import validate_document
from wsprrypi_qualification.progress import (
    ProgressError,
    ProgressReporter,
    default_progress_path,
    run_streaming,
)
from wsprrypi_qualification.transports import CommandPlan


def test_progress_is_tail_ready_ordered_schema_valid_and_path_safe(tmp_path: Path) -> None:
    path = tmp_path / "path with spaces" / "progress.jsonl"
    with ProgressReporter(path) as reporter:
        first = reporter.emit("capture", "started", "RF-off capture", mode="TONE")
        assert path.read_text(encoding="utf-8").endswith("\n")
        second = reporter.emit("capture", "completed", "RF-off retained", mode="TONE")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [first, second]
    assert [row["sequence"] for row in rows] == [1, 2]
    for row in rows:
        validate_document(row, "complete-test-progress.schema.json")
    with pytest.raises(ProgressError):
        ProgressReporter(path)


def test_progress_protocol_streams_without_polluting_stdout(tmp_path: Path) -> None:
    child = tmp_path / "child.py"
    child.write_text(
        "import json,sys\n"
        "e={'schema_version':1,'evidence_type':'complete_test_progress','sequence':9,"
        "'timestamp_utc':'2026-01-01T00:00:00Z','campaign_id':'c','mode':'FSKCW',"
        "'stage':'keyed_observation','status':'completed','detail':'observation 1',"
        "'item':1,'item_count':3}\n"
        "print('WSPQ_PROGRESS '+json.dumps(e),file=sys.stderr,flush=True)\n"
        "print(json.dumps({'final':True}))\n",
        encoding="utf-8",
    )
    log = tmp_path / "progress.jsonl"
    with ProgressReporter(log) as reporter:
        result = run_streaming(
            CommandPlan(Path(sys.executable), (str(child),), timeout_s=5), reporter
        )
    assert json.loads(result.stdout) == {"final": True}
    assert result.stderr == ""
    assert json.loads(log.read_text(encoding="utf-8"))["item"] == 1


def test_default_progress_log_is_durable_and_independent_of_runtime_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    durable = tmp_path / "durable progress"
    runtime_stage = tmp_path / "temporary deployment"
    runtime_stage.mkdir()
    monkeypatch.setenv("WSPQ_PROGRESS_DIR", str(durable))

    first = default_progress_path()
    second = default_progress_path()
    assert first.parent == durable.resolve()
    assert first != second

    with ProgressReporter(first) as reporter:
        reporter.emit("command", "started", "accepted")
    runtime_stage.rmdir()
    assert first.is_file()
