"""Run or validate the bounded, nonqualifying Pico software governance gate."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--native", type=Path)
    parser.add_argument("--wsprd", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--scenario", choices=["all", "keyed", "wspr", "receiver", "cancel"], default="all"
    )
    parser.add_argument(
        "--connected-capture",
        action="store_true",
        help="Step 2: real capture worker/process/writer; three cases by default",
    )
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not args.worker:
        # No inherited plugins, PYTHONPATH, RF opt-ins, SSH agent or device configuration.
        env = {
            key: os.environ[key]
            for key in ("SYSTEMROOT", "WINDIR", "PATH", "TMPDIR", "TEMP", "TMP")
            if key in os.environ
        }
        env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONNOUSERSITE="1", MPLBACKEND="Agg")
        command = [sys.executable, "-I", str(Path(__file__).resolve()), *sys.argv[1:], "--worker"]
        result = subprocess.run(command, env=env, cwd=args.output.parent, timeout=180, check=False)
        raise SystemExit(result.returncode)
    sys.path.insert(0, str(root))
    from tests.support.pico_governance import fresh_validation, run_case, run_gate, validate_gate

    if args.validate:
        value = validate_gate(args.output.resolve())
    else:
        if not all((args.source, args.native, args.wsprd)):
            parser.error("--source, --native, and --wsprd are required for execution")
        source, output = args.source.resolve(strict=True), args.output.resolve()
        if output.is_relative_to(root) or output.is_relative_to(source):
            parser.error("gate output must be outside harness and target source trees")
        arguments = (
            source,
            args.native.resolve(strict=True),
            args.wsprd.resolve(strict=True),
            output,
        )
        if args.connected_capture and args.scenario == "wspr":
            parser.error("Step 2 does not include WSPR")
        if args.connected_capture:
            from tests.support.pico_connected_capture import run_gate
        value = (
            run_gate(*arguments)
            if args.scenario == "all"
            else run_case(*arguments, args.scenario, connected=args.connected_capture)
        )
        if args.scenario != "all":
            checked = fresh_validation(output)
            if checked.returncode:
                print(checked.stderr, file=sys.stderr)
                raise SystemExit(checked.returncode)
    print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
