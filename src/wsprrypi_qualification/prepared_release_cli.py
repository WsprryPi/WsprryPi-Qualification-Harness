"""Operator prepare-now / publish-later entrypoints; publication has no build path."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from wsprrypi_qualification import lifecycle as lc
from wsprrypi_qualification import prepared_release as release
from wsprrypi_qualification.prepared_release_adapters import CanonicalReleaseAdapter
from wsprrypi_qualification.progress import ProgressReporter
from wsprrypi_qualification.progress_viewer import tracking_command

ACTIONS = {"release-session-plan", "release-prepare", "release-status", "release-publish"}


def add_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    plan = commands.add_parser(
        "release-session-plan", help="resolve a non-publishing retained release preparation"
    )
    plan.add_argument("request", type=Path)
    plan.add_argument("--state-root", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser(
        "release-prepare", help="build/test and seal exact packets without publication"
    )
    prepare.add_argument("plan", type=Path)
    prepare.add_argument("--confirm-plan-sha256", required=True)
    status = commands.add_parser(
        "release-status", help="inspect retained readiness and invalidation offline"
    )
    status.add_argument("state_root", type=Path)
    publish = commands.add_parser(
        "release-publish", help="verify/publish retained bytes in a later invocation; never rebuild"
    )
    publish.add_argument("state_root", type=Path)
    publish.add_argument("--confirm-prepared-sha256", required=True)
    mode = publish.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--validate-only", action="store_true", help="read-only canonical/public-state verification"
    )
    mode.add_argument(
        "--enable-publication",
        action="store_true",
        help="requires separate current publication authority",
    )
    publish.add_argument(
        "--resume",
        action="store_true",
        help="inspect and continue a checkpointed partial publication",
    )


@contextmanager
def adapter_for(value: dict[str, Any], root: Path) -> Iterator[CanonicalReleaseAdapter]:
    path = root.parent / (root.name + "-progress-" + secrets.token_hex(8) + ".jsonl")
    lc._outside(path, [v["path"] for v in value["sources"].values()])
    print("Progress: " + str(path), file=sys.stderr, flush=True)
    print(tracking_command(path), file=sys.stderr, flush=True)
    with ProgressReporter(path, mirror=sys.stderr) as reporter:
        adapter = CanonicalReleaseAdapter(value["tools"])
        adapter.progress = reporter
        reporter.emit(
            "release-session", "started", "exact-byte software release operation; RF disabled"
        )
        try:
            yield adapter
        except BaseException:
            reporter.emit(
                "release-session",
                "failed",
                "operation interrupted or refused; inspect retained state",
            )
            raise
        reporter.emit(
            "release-session",
            "completed" if adapter.outcome in {"prepared", "validated", "complete"} else "failed",
            "inspect structured result and remaining gates",
        )


def dispatch(args: argparse.Namespace) -> int:
    action = args.lifecycle_action
    if action == "release-session-plan":
        result = release.plan(lc.load(args.request), args.state_root)
        lc.save(args.output, result, new=True)
    elif action == "release-prepare":
        plan = lc.load(args.plan)
        print("Retained state: " + plan["state_root"], flush=True, file=sys.stderr)
        print(
            "Inspect with: wsprrypi-qualification lifecycle release-status " + plan["state_root"],
            flush=True,
            file=sys.stderr,
        )
        release.check_plan(plan)
        with adapter_for(plan, Path(plan["state_root"])) as adapter:
            result = release.prepare(plan, args.confirm_plan_sha256, adapter)
            adapter.outcome = result["status"]
    elif action == "release-status":
        result = release.status(args.state_root)
    else:
        value = release.verify_retained(args.state_root)
        with adapter_for(value, args.state_root) as adapter:
            result = release.publish(
                args.state_root,
                args.confirm_prepared_sha256,
                adapter,
                enable_publication=args.enable_publication,
                validate_only=args.validate_only,
                resume=args.resume,
            )
            adapter.outcome = result["status"]
    print(json.dumps(result, indent=2, sort_keys=True))
    return (
        0
        if result.get("status", "planned") in {"planned", "prepared", "validated", "complete"}
        else 1
    )
