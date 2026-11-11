"""GPS acquisition diagnostics and independently evidenced WTP clock authority.

No new freshness or uncertainty limit is imposed: WTP capabilities and the
actual ARM request govern the clock. Original source age is never renewed.
"""

from __future__ import annotations

from typing import Any

from wsprrypi_qualification.offline import validate_document
from wsprrypi_qualification.pico_wire import PicoWireError, validate_contract, validate_diagnostic

CLOCK_POLICY = {"version": 1, "authority": "wtp-clock", "acquisition": "diagnostic"}


def uses_holdover(plan: dict[str, Any]) -> bool:
    return plan.get("gps_clock_admission") == CLOCK_POLICY


def check_clock(
    clock: dict[str, Any], capabilities: dict[str, Any], maximum_uncertainty: int
) -> None:
    """Apply advertised WTP clock/holdover rules without changing their limits."""
    validate_contract(clock, "wtp", "clock")
    validate_contract(capabilities, "wtp", "capsResponse")
    age = int(clock["sync_age_ns"])
    if (
        clock["state"] == "unsynchronized"
        or int(clock["utc_now_ns"]) == 0
        or age > int(clock["monotonic_now_ns"])
        or (
            clock["state"] == "holdover"
            and (
                int(capabilities["maximum_holdover_age_ns"]) == 0
                or age > int(capabilities["maximum_holdover_age_ns"])
            )
        )
    ):
        raise PicoWireError("Pico clock has no usable synchronized/holdover authority")
    if int(clock["uncertainty_ns"]) > min(
        maximum_uncertainty, int(capabilities["maximum_arm_uncertainty_ns"])
    ):
        raise PicoWireError("Pico clock exceeds the actual ARM uncertainty limit")
    if clock["leap"] == "unknown":
        raise PicoWireError("Pico clock leap state is unknown")


def validate_clock_admission(
    evidence: dict[str, Any], snapshot: dict[str, Any], transaction: dict[str, Any]
) -> None:
    """Validate a retained clock decision; wire correlation is checked separately."""
    validate_document(evidence, "pico-clock-admission.schema.json")
    target = snapshot["target"]
    if evidence["target"] != target or evidence["job_id"] != transaction["job"]["job_id"]:
        raise PicoWireError("Clock admission belongs to another job/boot")
    if (
        transaction["hello"]["boot_id"] != target["boot_id"]
        or transaction["hello"]["device_id"] != target["device_id"]
    ):
        raise PicoWireError("Clock admission WTP/management identity differs")
    for source in (evidence["source_before"], evidence["source_after"]):
        validate_diagnostic(source)
        if (
            source["schema"] != "ntp-observations"
            or source["target"] != target
            or source["active_source"] != "gps"
            or source["source_disagreement"]
        ):
            raise PicoWireError("Clock admission lacks consistent GPS source authority")
    if int(evidence["source_after"]["clock_generation"]) < int(
        evidence["source_before"]["clock_generation"]
    ):
        raise PicoWireError("Clock model generation moved backward")
    if evidence["source_before"] != snapshot["ntp-observations"]:
        raise PicoWireError("Clock source bracket differs from prepared resources")
    if int(evidence["completed_monotonic_ns"]) < int(evidence["started_monotonic_ns"]):
        raise PicoWireError("Clock collection interval moved backward")
    caps = snapshot["capabilities"]["wtp"]
    if evidence["capabilities"] != caps:
        raise PicoWireError("Clock admission capabilities differ from prepared resources")
    limit = int(evidence["maximum_start_uncertainty_ns"])
    check_clock(evidence["clock"], caps, limit)
    if "arm" in transaction:
        check_clock(transaction["arm"]["clock"], caps, limit)
