"""Compact terminal viewer for complete-test JSONL progress logs."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

MAX_COLUMNS = 79
POLL_SECONDS = 0.1

_STAGE_ALIASES = {
    "rf_off_captured": "rf_off_capture",
    "carrier_transmitted_and_captured": "carrier_capture",
    "wspr_frames": "coherent_capture",
}

_STAGE_LABELS = {
    "command": "Command",
    "delegation": "Receiver delegation",
    "campaign": "Five-mode campaign",
    "mode": "Mode",
    "requested": "Session request",
    "validated": "Plan validation",
    "runtime_confirmed": "Runtime authorization",
    "capabilities_discovered": "Capability discovery",
    "helper_verified": "Helper identity",
    "services_and_ownership_verified": "Services and ownership",
    "rf_idle_verified": "RF idle",
    "cleanup_installed": "Cleanup installation",
    "rf_off_capture": "RF-off capture",
    "carrier_capture": "Carrier capture",
    "carrier_gate": "Carrier gate",
    "coherent_capture": "Coherent capture",
    "wspr_frame": "WSPR frame",
    "wav_and_decode": "WAV and decode",
    "keyed_observation": "Observation",
    "cleanup": "Cleanup",
    "quiescence": "Final quiescence",
}

_COLORS = {
    "queued": "\x1b[2m",
    "running": "\x1b[36m",
    "success": "\x1b[32m",
    "failure": "\x1b[31m",
    "neutral": "\x1b[34m",
}
_RESET = "\x1b[0m"


@dataclass(frozen=True)
class Step:
    scope: str
    label: str
    state: str
    kind: str


def tracking_command(path: Path, *, windows: bool | None = None) -> str:
    """Return the command a producer can print when its progress log opens."""
    if windows is None:
        windows = os.name == "nt"
    if windows:
        return subprocess.list2cmdline(["wspq-progress", str(path)])
    return f"wspq-progress {shlex.quote(str(path))}"


def _semantic_state(status: str, detail: str) -> tuple[str, str]:
    lowered = detail.casefold()
    if "ended with " in lowered:
        state = detail[lowered.index("ended with ") + len("ended with ") :]
    elif status == "terminal" and " with " in lowered:
        state = detail[lowered.rindex(" with ") + len(" with ") :]
    elif status == "completed" and " passed" in lowered:
        state = "passed"
    else:
        state = status.replace("_", " ")

    failure_words = ("failed", "unqualified", "blocked", "inconclusive", "aborted")
    if any(word in state.casefold() for word in failure_words):
        return state, "failure"
    if status in {"passed", "completed", "verified", "terminal"}:
        return state, "success"
    if status in {"started", "running"}:
        return state, "running"
    if status == "queued":
        return state, "queued"
    return state, "neutral"


def _step(event: dict[str, object]) -> tuple[tuple[object, ...], Step]:
    stage = str(event.get("stage", "event"))
    canonical_stage = _STAGE_ALIASES.get(stage, stage)
    mode_value = event.get("mode")
    mode = mode_value if isinstance(mode_value, str) else ""
    item = event.get("item")
    item_count = event.get("item_count")
    scope = mode or ("RUN" if canonical_stage in {"command", "delegation", "campaign"} else "")
    label = _STAGE_LABELS.get(canonical_stage, canonical_stage.replace("_", " ").title())
    if canonical_stage == "mode" and mode:
        label = f"{mode} mode"
    elif item is not None:
        label = f"{label} {item}"
        if item_count is not None:
            label += f"/{item_count}"
    status = str(event.get("status", "recorded"))
    detail = str(event.get("detail", ""))
    state, kind = _semantic_state(status, detail)
    campaign_id = event.get("campaign_id")
    key = (
        "" if campaign_id is None else str(campaign_id),
        canonical_stage,
        mode,
        "" if item is None else str(item),
    )
    return key, Step(scope=scope, label=label, state=state, kind=kind)


def _glyph(kind: str) -> str:
    return {
        "queued": "○",
        "running": "▶",
        "success": "✓",
        "failure": "✗",
        "neutral": "•",
    }[kind]


def format_step(step: Step, *, color: bool) -> str:
    """Format one progress row with a maximum visible width of 79 columns."""
    scope = step.scope[:6]
    state = step.state
    if len(state) > 30:
        state = state[:29] + "…"
    prefix = f"{_glyph(step.kind)} {scope:<6} "
    suffix = f"  {state}"
    available = MAX_COLUMNS - len(prefix) - len(suffix)
    if available < 1:
        suffix = suffix[: MAX_COLUMNS - len(prefix) - 1]
        available = 1
    label = step.label
    if len(label) > available:
        label = label[: max(0, available - 1)] + "…"
    plain = f"{prefix}{label:<{available}}{suffix}"
    if not color:
        return plain
    return f"{_COLORS[step.kind]}{plain}{_RESET}"


class Renderer:
    """Maintain stable step order and redraw updates in-place when possible."""

    def __init__(
        self, stream: TextIO, *, color: bool | None = None, emit_updates: bool = True
    ) -> None:
        self._stream = stream
        self._interactive = bool(getattr(stream, "isatty", lambda: False)())
        self._color = self._interactive if color is None else color
        self._emit_updates = emit_updates
        self._steps: OrderedDict[tuple[object, ...], Step] = OrderedDict()
        self._rendered_lines = 0

    def update(self, event: dict[str, object]) -> None:
        key, step = _step(event)
        self._steps[key] = step
        if event.get("stage") == "wspr_frames":
            terminal_state = "completed" if step.kind == "success" else step.state
            for existing_key, existing_step in self._steps.items():
                if existing_key[1] == "wspr_frame" and existing_key[2] == key[2]:
                    self._steps[existing_key] = Step(
                        existing_step.scope, existing_step.label, terminal_state, step.kind
                    )
        if (event.get("stage") == "campaign" and event.get("status") == "terminal") or (
            event.get("stage") == "delegation"
            and event.get("status") in {"completed", "failed", "terminal"}
        ):
            for existing_key, existing_step in self._steps.items():
                if existing_key[1] == "command":
                    self._steps[existing_key] = Step(
                        existing_step.scope, existing_step.label, "completed", "success"
                    )
        if self._interactive and self._emit_updates:
            self._redraw()
        elif self._emit_updates:
            print(format_step(step, color=self._color), file=self._stream, flush=True)

    def snapshot(self) -> None:
        """Print the latest state of every retained step once."""
        for step in self._steps.values():
            print(format_step(step, color=self._color), file=self._stream)
        self._stream.flush()

    def reset(self) -> None:
        """Clear retained state when the followed path starts a different log."""
        if self._interactive and self._rendered_lines:
            self._stream.write(f"\x1b[{self._rendered_lines}F")
            for _ in range(self._rendered_lines):
                self._stream.write("\x1b[2K\n")
            self._stream.flush()
        self._steps.clear()
        self._rendered_lines = 0

    def _redraw(self) -> None:
        if self._rendered_lines:
            self._stream.write(f"\x1b[{self._rendered_lines}F")
        lines = [format_step(step, color=self._color) for step in self._steps.values()]
        total = max(self._rendered_lines, len(lines))
        for index in range(total):
            line = lines[index] if index < len(lines) else ""
            self._stream.write(f"\x1b[2K{line}\n")
        self._stream.flush()
        self._rendered_lines = len(lines)


def _is_terminal(event: dict[str, object], *, delegation_seen: bool) -> bool:
    stage = event.get("stage")
    status = event.get("status")
    if stage == "delegation" and status in {"completed", "failed", "terminal"}:
        return True
    return not delegation_seen and stage == "campaign" and status == "terminal"


def view(path: Path, *, follow: bool, stream: TextIO = sys.stdout) -> int:
    """Render a progress log, optionally following until the run terminates."""
    renderer = Renderer(stream, emit_updates=follow)
    delegation_seen = False
    offset = 0
    identity: tuple[int, int] | None = None
    pending = ""

    while True:
        try:
            stat = path.stat()
        except FileNotFoundError:
            if not follow:
                print(f"progress log not found: {path}", file=sys.stderr)
                return 2
            time.sleep(POLL_SECONDS)
            continue

        current_identity = (stat.st_dev, stat.st_ino)
        if identity is not None and (current_identity != identity or stat.st_size < offset):
            offset = 0
            pending = ""
            delegation_seen = False
            renderer.reset()
        identity = current_identity

        with path.open(encoding="utf-8") as handle:
            handle.seek(offset)
            chunk = handle.read()
            offset = handle.tell()
        pending += chunk
        lines = pending.splitlines(keepends=True)
        pending = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            pending = lines.pop()

        for raw_line in lines:
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                print("ignored malformed progress record", file=sys.stderr)
                continue
            if (
                not isinstance(event, dict)
                or event.get("evidence_type") != "complete_test_progress"
            ):
                continue
            if event.get("stage") == "delegation" and event.get("status") == "started":
                delegation_seen = True
            renderer.update(event)
            if _is_terminal(event, delegation_seen=delegation_seen):
                if not follow:
                    renderer.snapshot()
                return 0

        if not follow:
            if pending:
                try:
                    event = json.loads(pending)
                except json.JSONDecodeError:
                    print("ignored malformed progress record", file=sys.stderr)
                else:
                    if (
                        isinstance(event, dict)
                        and event.get("evidence_type") == "complete_test_progress"
                    ):
                        renderer.update(event)
            renderer.snapshot()
            return 0
        time.sleep(POLL_SECONDS)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wspq-progress", description="tail a complete-test JSONL progress log"
    )
    parser.add_argument("log_file", type=Path)
    parser.add_argument(
        "--replay", action="store_true", help="render existing records and exit at end-of-file"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    path = args.log_file.expanduser()
    print(f"Tracking: {path}")
    print(f"Command:  {tracking_command(path)}")
    try:
        return view(path, follow=not args.replay)
    except KeyboardInterrupt:
        return 130
    except (OSError, UnicodeError) as error:
        print(f"cannot read progress log: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
