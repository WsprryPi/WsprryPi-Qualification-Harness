"""Verify live-session modules and schemas from an installed wheel."""

from __future__ import annotations

import importlib
import json
from importlib.resources import files

from jsonschema import Draft202012Validator

MODULES = (
    "wsprrypi_qualification.pico_campaign",
    "wsprrypi_qualification.pico_clock",
    "wsprrypi_qualification.pico_live",
    "wsprrypi_qualification.pico_results",
    "wsprrypi_qualification.pico_audit",
    "wsprrypi_qualification.deployment",
    "wsprrypi_qualification.live_adapters",
    "wsprrypi_qualification.real_session",
    "wsprrypi_qualification.receiver_calibration",
    "wsprrypi_qualification.turnkey_campaign",
    "wsprrypi_qualification.lifecycle",
    "wsprrypi_qualification.lifecycle_pi",
    "wsprrypi_qualification.lifecycle_recipes",
    "wsprrypi_qualification.lifecycle_release",
    "wsprrypi_qualification.prepared_release",
    "wsprrypi_qualification.prepared_release_adapters",
    "wsprrypi_qualification.prepared_release_cli",
)
SCHEMAS = (
    "pico-campaign-configuration.schema.json",
    "pico-campaign-plan.schema.json",
    "pico-campaign-result.schema.json",
    "pico-clock-admission.schema.json",
    "pico-usb-io.schema.json",
    "pico-control.schema.json",
    "pico-wtp.schema.json",
    "application-plan.schema.json",
    "real-session-stage-evidence.schema.json",
    "resolved-real-session-plan.schema.json",
    "receiver-calibration-binding.schema.json",
    "turnkey-campaign-request.schema.json",
    "resolved-turnkey-campaign-plan.schema.json",
    "turnkey-campaign-result.schema.json",
    "lifecycle-request.schema.json",
    "lifecycle-plan.schema.json",
    "lifecycle-result.schema.json",
    "lifecycle-checkpoint.schema.json",
    "lifecycle-release-request.schema.json",
    "lifecycle-pi-bench.schema.json",
    "lifecycle-pico-bench.schema.json",
    "prepared-release-request.schema.json",
    "prepared-release-plan.schema.json",
    "prepared-release-prepared.schema.json",
    "prepared-release-state.schema.json",
)
ASSETS = (
    "deployment_assets/helper-config.example.json",
    "deployment_assets/wspq-capability-helper.service.in",
    "deployment_assets/wspq-gpio-inspect",
    "lifecycle_assets/pi_snapshot.py.txt",
    "lifecycle_assets/pi_absence.py.txt",
    "lifecycle_assets/pico_cycle.py.txt",
    "lifecycle_assets/pi_usb_handoff.py.txt",
)


def main() -> int:
    for name in MODULES:
        importlib.import_module(name)
    root = files("wsprrypi_qualification").joinpath("schemas")
    for name in SCHEMAS:
        Draft202012Validator.check_schema(
            json.loads(root.joinpath(name).read_text(encoding="utf-8"))
        )
    package_root = files("wsprrypi_qualification")
    for name in ASSETS:
        if not package_root.joinpath(name).is_file():
            raise FileNotFoundError(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
