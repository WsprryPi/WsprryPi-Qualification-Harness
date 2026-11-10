"""Generate synthetic watcher input from the maintained inhibited production fixture."""

import argparse
import importlib.util
import json
from pathlib import Path

from tests.support.pico_smoke import run_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--wsprd", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=False, exist_ok=False)
    tool = Path(__file__).resolve().parents[2] / "scripts/pico_negative_gate.py"
    spec = importlib.util.spec_from_file_location("negative_gate", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = run_case(args.source, args.native, args.wsprd, root / "inhibited", "keyed")
    if result["outcome"] != "passed":
        raise ValueError("Inhibited positive fixture failed")
    run = Path(result["output_directory"])
    plan = module.read(run / "requested-plan.json")
    audit = module.Audit(plan)
    frames = []
    for line in (run / "usb-io.jsonl").read_bytes().splitlines(keepends=True):
        before = len(audit.frames)
        audit.feed(line)
        for direction, frame, _, _ in audit.frames[before:]:
            frames.append(dict(direction=direction, frame=frame))
            if frame.get("event") == "JOB_STATE" and frame["body"]["state"] == "running":
                frame["body"]["output_active"] = (
                    True  # Explicit synthetic trigger, never firmware evidence.
                )
                value = dict(
                    execution_kind="offline_benign",
                    qualification_claim=False,
                    plan=plan,
                    frames=frames,
                )
                (root / "watch-fixture.json").write_text(json.dumps(value, indent=2) + "\n")
                return
    raise ValueError("Fixture did not execute a running job")


if __name__ == "__main__":
    main()
