"""Lifecycle CLI kept separate from RF command parsing."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from wsprrypi_qualification import lifecycle


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "lifecycle", help="RF-disabled component/system lifecycle plans and execution"
    )
    commands = parser.add_subparsers(dest="lifecycle_action", required=True)
    from wsprrypi_qualification.prepared_release_cli import add_parser as add_prepared

    add_prepared(commands)
    release = commands.add_parser(
        "release-plan",
        help="select independent/system release order, artifact promotion and canonical CI",
    )
    release.add_argument("request", type=Path)
    release.add_argument("--output", type=Path, required=True)
    plan = commands.add_parser(
        "plan", help="resolve local inputs and CI reuse without host/device access"
    )
    plan.add_argument(
        "--publication-selected",
        action="store_true",
        help="select a publishing release plan; execution needs explicit publication enablement",
    )
    plan.add_argument("request", type=Path)
    plan.add_argument("--output", type=Path, required=True)
    public_system = commands.add_parser(
        "system-public-plan", help="public Pi matrix and real USB controller handoff"
    )
    public_system.add_argument("pi_bench", type=Path)
    public_system.add_argument("pico_bench", type=Path)
    public_system.add_argument(
        "--installer-branch",
        default="devel",
        help="explicit streamed source branch; a feature branch is not default-public acceptance",
    )
    for argument in ("state-root", "pi-source", "rp1-source", "pico-source", "output"):
        public_system.add_argument("--" + argument, type=Path, required=True)
    system = commands.add_parser(
        "system-plan",
        help="coordinate components; require explicit handoff and compatibility",
    )
    system.add_argument("requests", nargs="+", type=Path)
    system.add_argument("--state-root", type=Path, required=True)
    system.add_argument("--output", type=Path, required=True)
    pico = commands.add_parser(
        "pico-plan", help="generate an identified Pico fresh/preserve request"
    )
    pico.add_argument("bench", type=Path)
    pico.add_argument("--state-root", type=Path, required=True)
    pico.add_argument("--pico-source", type=Path, required=True)
    pico.add_argument("--track", choices=("fresh", "preserve"), required=True)
    pico.add_argument("--output", type=Path, required=True)
    public = commands.add_parser(
        "pi-public-plan", help="generate maintained public Pi/RP1 eight-step request"
    )
    public.add_argument("bench", type=Path)
    public.add_argument(
        "--installer-branch",
        default="devel",
        help="explicit streamed source branch; a feature branch is not default-public acceptance",
    )
    public.add_argument("--state-root", type=Path, required=True)
    public.add_argument("--pi-source", type=Path, required=True)
    public.add_argument("--rp1-source", type=Path, required=True)
    public.add_argument("--output", type=Path, required=True)
    identity = commands.add_parser(
        "source", help="record exact local Git and dirty input identities"
    )
    identity.add_argument("component", choices=("pi", "pico", "rp1"))
    identity.add_argument("path", type=Path)
    for name in ("execute", "resume"):
        action = commands.add_parser(
            name, help="run bounded production commands; requires exact plan digest"
        )
        action.add_argument("plan", type=Path)
        action.add_argument("run_directory", type=Path)
        action.add_argument("--enable-publication", action="store_true")
        action.add_argument("--confirm-plan-sha256", required=True)
        if name == "resume":
            action.add_argument(
                "--inspect-step",
                required=True,
                help="read-only actual-state inspection before continuing",
            )
    for name in ("status", "report"):
        action = commands.add_parser(
            name, help="inspect retained checkpoints/results without target access"
        )
        action.add_argument("run_directory", type=Path)
    recover = commands.add_parser(
        "recover", help="run a separate owning-component recovery plan; retains original run"
    )
    recover.add_argument("plan", type=Path)
    recover.add_argument("run_directory", type=Path)
    recover.add_argument("--confirm-plan-sha256", required=True)


def dispatch(args: argparse.Namespace) -> int:
    try:
        action = args.lifecycle_action
        from wsprrypi_qualification.prepared_release_cli import (
            ACTIONS,
        )
        from wsprrypi_qualification.prepared_release_cli import (
            dispatch as prepared_dispatch,
        )

        if action in ACTIONS:
            return prepared_dispatch(args)
        if action == "release-plan":
            from wsprrypi_qualification.lifecycle_release import release_graph

            result = release_graph(lifecycle.load(args.request))
            lifecycle.save(args.output, result, new=True)
        elif action == "system-public-plan":
            from wsprrypi_qualification.lifecycle_recipes import system_public_request

            result = system_public_request(
                args.pi_bench,
                args.pico_bench,
                args.state_root,
                args.pi_source,
                args.rp1_source,
                args.pico_source,
                installer_branch=args.installer_branch,
            )
            lifecycle.save(args.output, result, new=True)
        elif action == "system-plan":
            from wsprrypi_qualification.lifecycle_recipes import system_request

            result = system_request([lifecycle.load(p) for p in args.requests], args.state_root)
            lifecycle.save(args.output, result, new=True)
        elif action == "pico-plan":
            from wsprrypi_qualification.lifecycle_recipes import pico_request

            result = pico_request(args.bench, args.state_root, args.pico_source, args.track)
            lifecycle.save(args.output, result, new=True)
        elif action == "pi-public-plan":
            from wsprrypi_qualification.lifecycle_recipes import pi_public_request

            result = pi_public_request(
                args.bench,
                args.state_root,
                args.pi_source,
                args.rp1_source,
                installer_branch=args.installer_branch,
            )
            lifecycle.save(args.output, result, new=True)
        elif action == "source":
            result = lifecycle.source_identity(args.component, args.path)
        elif action == "plan":
            result = lifecycle.compose(
                lifecycle.load(args.request), publication_selected=args.publication_selected
            )
            lifecycle.save(args.output, result, new=True)
        elif action == "status":
            result = lifecycle.load(args.run_directory / "checkpoint.json")
        elif action == "report":
            result = lifecycle.report(args.run_directory)
        else:
            plan = lifecycle.load(args.plan)
            if action == "recover" and not any(
                s["phase"] == "recover" for s in plan["request"]["steps"]
            ):
                raise lifecycle.LifecycleError("recovery needs an owning-component recovery phase")
            result = lifecycle.run(
                plan,
                args.run_directory,
                confirm=args.confirm_plan_sha256,
                resume=action == "resume",
                inspection=getattr(args, "inspect_step", None),
                enable_publication=getattr(args, "enable_publication", False),
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status", "pass") in {"pass", "expected_refusal"} else 1
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    add_parser(parser.add_subparsers(dest="command", required=True))
    return dispatch(parser.parse_args(argv))
