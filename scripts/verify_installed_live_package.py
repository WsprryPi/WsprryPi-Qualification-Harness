"""Verify live-session modules and schemas from an installed wheel."""

from __future__ import annotations

import importlib
import json
from importlib.resources import files

from jsonschema import Draft202012Validator

MODULES = (
    "wsprrypi_qualification.deployment",
    "wsprrypi_qualification.live_adapters",
    "wsprrypi_qualification.real_session",
    "wsprrypi_qualification.receiver_calibration",
)
SCHEMAS = (
    "application-plan.schema.json",
    "real-session-stage-evidence.schema.json",
    "resolved-real-session-plan.schema.json",
    "receiver-calibration-binding.schema.json",
)


def main() -> int:
    for name in MODULES:
        importlib.import_module(name)
    root = files("wsprrypi_qualification").joinpath("schemas")
    for name in SCHEMAS:
        Draft202012Validator.check_schema(
            json.loads(root.joinpath(name).read_text(encoding="utf-8"))
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
