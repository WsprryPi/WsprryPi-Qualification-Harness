"""Purpose-specific diagnostic evidence; never normalize or rewrite peer documents.

Governance consumes a deliberately bounded projection. Qualification continues to
require the complete current contract and atomic calibration proof. Unknown
read-only telemetry is retained but cannot supply a validated capability.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from wsprrypi_qualification.pico_wire import PicoWireError, _validator

PURPOSES = {"qualification", "governance"}
RF_CORE = {
    "next_ppb",
    "next_generation",
    "next_source",
    "reason",
    "prepared_ppb",
    "prepared_source",
    "prepared_generation",
}
CLOCK_CORE = {
    "contract",
    "schema",
    "target",
    "active_source",
    "source_disagreement",
    "nominal_sample_hz",
    "clock_generation",
    "utc_rate_ppb",
    "sync_age_ns",
}


@lru_cache(maxsize=3)
def diagnostic_validator(resource: str) -> Any:
    if resource == "rf-calibration":
        validator = _validator("rf-calibration", None)
        schema = copy.deepcopy(validator.schema)
        selection = schema["properties"]["selection"]["anyOf"][1]
        selection["required"] = sorted(RF_CORE)
        selection["properties"] = {
            k: v
            for k, v in selection["properties"].items()
            if k in RF_CORE or k == "prepared_input"
        }
        selection["additionalProperties"] = True
    elif resource in {"ntp-observations", "message-schedule"}:
        validator = _validator("observation", None)
        schema = copy.deepcopy(
            validator.schema["oneOf"][1 if resource == "ntp-observations" else 2]
        )
        if resource == "ntp-observations":
            schema["required"] = sorted(CLOCK_CORE)
            schema["properties"] = {
                k: v for k, v in schema["properties"].items() if k in CLOCK_CORE
            }
    else:
        raise PicoWireError("Unsupported governance diagnostic resource")
    schema["additionalProperties"] = True
    schema.pop("$schema", None)  # Preserve the exact-integer and uint64 validators.
    return validator.evolve(schema=schema)


def validate_diagnostic_context(value: dict[str, Any], purpose: str, stage: str) -> None:
    if purpose not in PURPOSES or stage not in {"read", "admission", "prepared", "final"}:
        raise PicoWireError("Unsupported diagnostic validation purpose or lifecycle stage")
    resource = value.get("schema")
    if not isinstance(resource, str):
        raise PicoWireError("Diagnostic resource identifier must be a string")
    if purpose == "qualification":
        validator = _validator("observation", None)
    else:
        validator = diagnostic_validator(resource)
    error = next(validator.iter_errors(value), None)
    if error is not None:
        # Schema path contains trusted schema keys, never peer values or extension names.
        while error.context:
            # Prefer the applicable object branch over a nullable alternative.
            error = next(
                (child for child in error.context if child.validator != "type"), error.context[-1]
            )
        path = "/".join(str(part) for part in error.absolute_schema_path)
        if error.validator == "required" and isinstance(error.instance, dict):
            path += "/" + ",".join(
                key for key in error.validator_value if key not in error.instance
            )
        raise PicoWireError(
            f"Invalid Pico diagnostic: purpose={purpose} stage={stage} rule=/{path}"
        )


def assessment(snapshot: dict[str, Any], stage: str) -> dict[str, Any]:
    """Explicit claim boundary, reconstructed from unchanged retained resources."""
    for name in ("rf-calibration", "ntp-observations", "message-schedule"):
        validate_diagnostic_context(snapshot[name], "governance", stage)
    selection = snapshot["rf-calibration"]["selection"]
    atomic = isinstance(selection, dict) and "prepared_input" in selection
    return dict(
        policy="pico-governance-v1",
        purpose="governance",
        stage=stage,
        target=snapshot["target"],
        qualification_claim=False,
        atomic_input_capability="present" if atomic else "unavailable",
        calibration_qualification="not_assessed",
        telemetry_policy="Only declared governance fields are assessed; raw extensions retained",
    )


def check_transition(previous: dict[str, Any], current: dict[str, Any]) -> None:
    """A capability cannot silently disappear or change during one owned run."""
    if previous["target"] != current["target"]:
        raise PicoWireError("Governance diagnostic target changed across lifecycle stages")
    before = assessment(previous, "read")["atomic_input_capability"]
    after = assessment(current, "read")["atomic_input_capability"]
    if before != after:
        raise PicoWireError("Governance diagnostic capability changed across lifecycle stages")
