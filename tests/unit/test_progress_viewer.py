from __future__ import annotations

import io
import json
from pathlib import Path

from wsprrypi_qualification.progress_viewer import (
    MAX_COLUMNS,
    Renderer,
    Step,
    format_step,
    tracking_command,
    view,
)


class FakeTerminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def event(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "campaign_id": "campaign-1",
        "detail": "capture started",
        "evidence_type": "complete_test_progress",
        "item": None,
        "item_count": None,
        "mode": "TONE",
        "schema_version": 1,
        "sequence": 1,
        "stage": "rf_off_capture",
        "status": "started",
        "timestamp_utc": "2026-08-24T19:08:04Z",
    }
    value.update(overrides)
    return value


def test_format_step_is_bounded_and_colors_only_outside_visible_text() -> None:
    step = Step("WSPR", "x" * 200, "completed", "success")
    plain = format_step(step, color=False)
    colored = format_step(step, color=True)

    assert len(plain) == MAX_COLUMNS
    assert plain.endswith("  completed")
    assert "…" in plain
    assert colored.endswith("\x1b[0m")
    assert plain in colored


def test_format_step_bounds_untrusted_scope_and_state() -> None:
    step = Step("SCOPE" * 100, "label", "state" * 100, "failure")

    assert len(format_step(step, color=False)) == MAX_COLUMNS


def test_alias_updates_the_existing_logical_step() -> None:
    stream = io.StringIO()
    renderer = Renderer(stream, color=False)
    renderer.update(event())
    renderer.update(
        event(
            sequence=2,
            stage="rf_off_captured",
            status="completed",
            detail="RF-off exact-count capture retained",
        )
    )

    assert len(renderer._steps) == 1
    assert next(iter(renderer._steps.values())).state == "completed"


def test_failed_semantics_override_completed_transport_status() -> None:
    stream = io.StringIO()
    renderer = Renderer(stream, color=False)
    renderer.update(
        event(
            mode="QRSS",
            stage="mode",
            status="completed",
            detail="QRSS ended with unqualified_keyed",
        )
    )

    line = stream.getvalue()
    assert line.startswith("✗")
    assert "unqualified_keyed" in line


def test_interactive_update_redraws_one_stable_row_and_reset_clears_it() -> None:
    stream = FakeTerminal()
    renderer = Renderer(stream, color=False)
    renderer.update(event())
    renderer.update(
        event(
            sequence=2,
            stage="rf_off_captured",
            status="completed",
            detail="RF-off exact-count capture retained",
        )
    )
    renderer.reset()

    output = stream.getvalue()
    assert output.count("\x1b[1F") == 2
    assert "\x1b[2K✓ TONE" in output
    assert not renderer._steps


def test_aggregate_completion_closes_frame_and_command_rows() -> None:
    stream = io.StringIO()
    renderer = Renderer(stream, color=False)
    renderer.update(event(campaign_id=None, mode=None, stage="command", status="started"))
    renderer.update(event(mode="WSPR", stage="wspr_frame", item=1, item_count=3))
    renderer.update(event(mode="WSPR", stage="wspr_frames", status="completed"))
    renderer.update(
        event(
            mode=None,
            stage="campaign",
            status="terminal",
            detail="campaign ended with qualified",
        )
    )

    steps = list(renderer._steps.values())
    assert steps[0].state == "completed"
    assert steps[1].state == "completed"


def test_replay_handles_partial_final_record_and_ignores_other_json(tmp_path: Path) -> None:
    path = tmp_path / "progress log.jsonl"
    records = [
        {"evidence_type": "something_else"},
        event(),
        event(
            sequence=2,
            stage="campaign",
            mode=None,
            status="terminal",
            detail="campaign ended with qualified",
        ),
    ]
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    stream = io.StringIO()

    assert view(path, follow=False, stream=stream) == 0
    assert "RF-off capture" in stream.getvalue()
    assert "qualified" in stream.getvalue()
    assert stream.getvalue().count("RF-off capture") == 1


def test_malformed_field_types_do_not_crash_renderer() -> None:
    stream = io.StringIO()
    renderer = Renderer(stream, color=False)

    renderer.update(event(campaign_id={"bad": "type"}, item=[1], status=["started"]))

    assert len(stream.getvalue().rstrip("\n")) == MAX_COLUMNS


def test_tracking_command_quotes_paths() -> None:
    assert tracking_command(Path("a progress.jsonl")) == "wspq-progress 'a progress.jsonl'"
    assert (
        tracking_command(Path("a progress.jsonl"), windows=True)
        == 'wspq-progress "a progress.jsonl"'
    )
