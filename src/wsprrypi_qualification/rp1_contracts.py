"""Fail-closed RP1 GPCLK plan and lifecycle evidence contracts."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, cast


class Rp1ContractError(ValueError):
    """RP1 identity or lifecycle evidence is incomplete or contradictory."""


RP1_ENDPOINT = "/dev/rp1-gpclk"
RP1_MODULE = "rp1_gpclk_dkms"
RP1_ROUTES = {
    "gpio4": {
        "gpio": 4,
        "output": "GPIO4",
        "endpoint_node": "rp1-gpclk-dkms-gpio4",
        "compatibility_id": "v1.1.2-pi5-gpio4-6.18.34-development-candidate-r2",
    },
    "gpio20": {
        "gpio": 20,
        "output": "GPIO20",
        "endpoint_node": "rp1-gpclk-dkms-gpio20",
        "compatibility_id": "v1.1.2-pi5-gpio20-6.18.34-development-candidate-r2",
    },
}


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def route_contract(route: str) -> dict[str, object]:
    try:
        selected = RP1_ROUTES[route]
    except KeyError as error:
        raise Rp1ContractError("RP1 route must be exactly gpio4 or gpio20") from error
    return {
        "backend": "rp1_gpclk",
        "route": route,
        **selected,
        "endpoint": RP1_ENDPOINT,
        "module": RP1_MODULE,
        "abi_version": 2,
        "finite_tone_required": True,
        "development_enrollment": "Experimental",
        "live_output_required": True,
        "terminal_silence_required": True,
    }


def validate_role_bindings(document: dict[str, Any]) -> None:
    if document.get("topology") != "same_host_roles":
        raise Rp1ContractError("RP1 same-host topology must be same_host_roles")
    transmitter = cast(dict[str, Any], document.get("transmitter_role"))
    receiver = cast(dict[str, Any], document.get("receiver_role"))
    if not isinstance(transmitter, dict) or not isinstance(receiver, dict):
        raise Rp1ContractError("same-host transmitter and receiver roles are required")
    if transmitter.get("host") != receiver.get("host"):
        raise Rp1ContractError("same-host role bindings must name the same authenticated host")
    for label, role in (("transmitter", transmitter), ("receiver", receiver)):
        if (
            role.get("role") != label
            or not role.get("helper_sha256")
            or not role.get("config_sha256")
        ):
            raise Rp1ContractError(f"{label} role identity is incomplete")
    if transmitter["helper_sha256"] == receiver["helper_sha256"]:
        raise Rp1ContractError("same-host roles require distinct helper identities")
    if transmitter["config_sha256"] == receiver["config_sha256"]:
        raise Rp1ContractError("same-host roles require distinct helper configurations")


def validate_preflight(document: dict[str, Any], *, route: str) -> dict[str, Any]:
    expected = route_contract(route)
    required_text = (
        "host",
        "endpoint_type",
        "endpoint_owner",
        "endpoint_group",
        "endpoint_mode",
        "endpoint_device_identity",
        "module_version",
        "module_build_id",
        "uapi_sha256",
        "vermagic",
        "signer",
        "installed_module_sha256",
        "compatibility_reason",
        "development_manifest_sha256",
        "operation_state",
        "terminal_reason",
        "current_event",
    )
    if any(not isinstance(document.get(name), str) or not document[name] for name in required_text):
        raise Rp1ContractError("RP1 preflight has missing or unknown identity fields")
    equality = {
        "endpoint": RP1_ENDPOINT,
        "module": RP1_MODULE,
        "route": route,
        "endpoint_node": expected["endpoint_node"],
        "compatibility_id": expected["compatibility_id"],
        "compatibility_state": "Experimental",
        "development_enrollment": "Experimental",
        "abi_version": 2,
        "query_version": 2,
        "live_output": True,
        "live_eligible": True,
        "finite_tone": True,
        "endpoint_available": True,
        "endpoint_open": False,
        "owner_present": False,
        "lease_present": False,
        "cleanup_fault": False,
        "operation_state": "IDLE",
        "gpio_safe": True,
        "clock_quiescent": True,
        "dma_quiescent": True,
        "drain_complete": True,
        "unresolved_route_transaction": False,
    }
    for name, value in equality.items():
        if document.get(name) != value:
            raise Rp1ContractError(f"RP1 preflight field {name} is unsafe or mismatched")
    if document.get("endpoint_mode") != "0600":
        raise Rp1ContractError("RP1 endpoint permissions must be exactly 0600")
    routes = document.get("route_state")
    if (
        not isinstance(routes, dict)
        or set(routes)
        != {"requested", "saved", "configured", "active_overlay", "module_reported", "reconciled"}
        or any(value != route for value in routes.values())
    ):
        raise Rp1ContractError("RP1 route observations do not agree")
    generation = document.get("generation")
    if not isinstance(generation, int) or generation < 0:
        raise Rp1ContractError("RP1 generation is missing or malformed")
    for name in ("elapsed_ns", "remaining_ns"):
        value = document.get(name)
        if not isinstance(value, int) or value < 0:
            raise Rp1ContractError(f"RP1 {name} is missing or malformed")
    return document


def validate_operation_lifecycle(
    document: dict[str, Any], *, route: str, prior_generation: int
) -> dict[str, Any]:
    expected = route_contract(route)
    if document.get("endpoint") != RP1_ENDPOINT or document.get("route") != route:
        raise Rp1ContractError("RP1 lifecycle endpoint or wrong-route substitution detected")
    if document.get("compatibility_id") != expected["compatibility_id"]:
        raise Rp1ContractError("RP1 lifecycle compatibility identity is wrong-route")
    if not isinstance(document.get("plan_sha256"), str) or len(document["plan_sha256"]) != 64:
        raise Rp1ContractError("RP1 lifecycle plan digest is missing")
    lease = document.get("lease")
    generation = document.get("generation")
    if not isinstance(lease, int) or lease <= 0:
        raise Rp1ContractError("RP1 lifecycle requires a nonzero lease")
    if not isinstance(generation, int) or generation <= prior_generation:
        raise Rp1ContractError("RP1 generation is not strictly increasing")
    transitions = document.get("transitions")
    if not isinstance(transitions, list) or not transitions or transitions[0] != "RUNNING":
        raise Rp1ContractError("RP1 lifecycle is missing RUNNING")
    if transitions[-1] not in {"COMPLETE", "CANCELLED"}:
        raise Rp1ContractError("RP1 lifecycle lacks a stable terminal state")
    if "DRAINING" in transitions and transitions.index("DRAINING") < transitions.index("RUNNING"):
        raise Rp1ContractError("RP1 lifecycle transition order is contradictory")
    terminal = "COMPLETE" if transitions[-1] == "COMPLETE" else "CANCELLED"
    if document.get("terminal_reason") != terminal:
        raise Rp1ContractError("RP1 terminal reason contradicts terminal state")
    required_true = (
        "bounded_drain",
        "lease_released",
        "endpoint_closed",
        "cleanup_verified",
        "gpio_safe",
        "clock_quiescent",
        "dma_quiescent",
        "terminal_silence_verified",
    )
    if any(document.get(name) is not True for name in required_true):
        raise Rp1ContractError("RP1 cleanup, closure, quiescence, or silence is unverified")
    if document.get("cleanup_fault") is not False:
        raise Rp1ContractError("RP1 cleanup-fault latch is set or unknown")
    duration = document.get("duration_ns")
    if not isinstance(duration, int) or not 1_000_000 <= duration <= 120_000_000_000:
        raise Rp1ContractError("RP1 finite duration is outside ABI-v2 bounds")
    if document.get("tone_operation") == "CONTINUOUS":
        raise Rp1ContractError("continuous RP1 TONE cannot satisfy the campaign entry gate")
    return document


def effective_ppm(source: dict[str, Any], residual: float, *, host: str, route: str) -> float:
    expected = route_contract(route)
    if (
        source
        != {
            "source_type": "manual_host_ppm",
            "value_ppm": source.get("value_ppm"),
            "host": host,
            "backend": "rp1_gpclk",
            "route": route,
            "compatibility_id": expected["compatibility_id"],
            "provenance": source.get("provenance"),
            "application_path": "--gpio-manual-ppm",
        }
        or not isinstance(source.get("provenance"), str)
        or not source["provenance"]
    ):
        raise Rp1ContractError("RP1 transmitter PPM provenance is incomplete or mismatched")
    value = source.get("value_ppm")
    if not isinstance(value, (int, float)) or not all(math.isfinite(x) for x in (value, residual)):
        raise Rp1ContractError("RP1 transmitter PPM values must be finite")
    result = float(value) + residual
    if not -200 <= result <= 200:
        raise Rp1ContractError("effective RP1 transmitter PPM exceeds +/-200")
    return result
