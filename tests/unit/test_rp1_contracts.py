from copy import deepcopy

import pytest

from wsprrypi_qualification.rp1_contracts import (
    Rp1ContractError,
    effective_ppm,
    route_contract,
    validate_operation_lifecycle,
    validate_preflight,
    validate_role_bindings,
)


def preflight(route: str = "gpio4") -> dict[str, object]:
    contract = route_contract(route)
    return {
        "host": "wspr5",
        "endpoint": contract["endpoint"],
        "endpoint_type": "character",
        "endpoint_owner": "root",
        "endpoint_group": "root",
        "endpoint_mode": "0600",
        "endpoint_device_identity": "major:minor",
        "endpoint_available": True,
        "endpoint_open": False,
        "owner_present": False,
        "lease_present": False,
        "module": contract["module"],
        "module_version": "1.1.2",
        "module_build_id": "build",
        "uapi_sha256": "a" * 64,
        "vermagic": "6.18",
        "signer": "development",
        "installed_module_sha256": "b" * 64,
        "abi_version": 2,
        "query_version": 2,
        "finite_tone": True,
        "live_output": True,
        "route": route,
        "endpoint_node": contract["endpoint_node"],
        "compatibility_id": contract["compatibility_id"],
        "compatibility_state": "Experimental",
        "compatibility_reason": "matched",
        "development_enrollment": "Experimental",
        "development_manifest_sha256": "c" * 64,
        "live_eligible": True,
        "cleanup_fault": False,
        "generation": 7,
        "operation_state": "IDLE",
        "terminal_reason": "NONE",
        "current_event": "NONE",
        "elapsed_ns": 0,
        "remaining_ns": 0,
        "drain_complete": True,
        "gpio_safe": True,
        "clock_quiescent": True,
        "dma_quiescent": True,
        "unresolved_route_transaction": False,
        "route_state": {
            name: route
            for name in (
                "requested",
                "saved",
                "configured",
                "active_overlay",
                "module_reported",
                "reconciled",
            )
        },
    }


def lifecycle(route: str = "gpio4") -> dict[str, object]:
    contract = route_contract(route)
    return {
        "plan_sha256": "d" * 64,
        "endpoint": contract["endpoint"],
        "route": route,
        "compatibility_id": contract["compatibility_id"],
        "lease": 19,
        "generation": 8,
        "transitions": ["RUNNING", "DRAINING", "COMPLETE"],
        "terminal_reason": "COMPLETE",
        "bounded_drain": True,
        "lease_released": True,
        "endpoint_closed": True,
        "cleanup_verified": True,
        "cleanup_fault": False,
        "gpio_safe": True,
        "clock_quiescent": True,
        "dma_quiescent": True,
        "terminal_silence_verified": True,
        "duration_ns": 1_000_000_000,
        "tone_operation": "FINITE",
    }


def test_rp1_preflight_and_finite_lifecycle_accept_complete_evidence() -> None:
    validate_preflight(preflight(), route="gpio4")
    validate_operation_lifecycle(lifecycle(), route="gpio4", prior_generation=7)


@pytest.mark.parametrize(
    "field",
    [
        "endpoint_available",
        "live_output",
        "live_eligible",
        "finite_tone",
        "drain_complete",
        "gpio_safe",
        "clock_quiescent",
        "dma_quiescent",
    ],
)
def test_rp1_preflight_fails_closed(field: str) -> None:
    evidence = preflight()
    evidence[field] = False
    with pytest.raises(Rp1ContractError):
        validate_preflight(evidence, route="gpio4")


def test_rp1_route_and_generation_evidence_cannot_transfer() -> None:
    with pytest.raises(Rp1ContractError, match="wrong-route"):
        validate_operation_lifecycle(lifecycle("gpio4"), route="gpio20", prior_generation=7)
    stale = lifecycle()
    stale["generation"] = 7
    with pytest.raises(Rp1ContractError, match="strictly increasing"):
        validate_operation_lifecycle(stale, route="gpio4", prior_generation=7)


def test_cleanup_and_terminal_silence_are_mandatory_and_continuous_tone_is_rejected() -> None:
    for field in (
        "lease_released",
        "endpoint_closed",
        "cleanup_verified",
        "terminal_silence_verified",
    ):
        evidence = lifecycle()
        evidence[field] = False
        with pytest.raises(Rp1ContractError):
            validate_operation_lifecycle(evidence, route="gpio4", prior_generation=7)
    continuous = lifecycle()
    continuous["tone_operation"] = "CONTINUOUS"
    with pytest.raises(Rp1ContractError, match="continuous"):
        validate_operation_lifecycle(continuous, route="gpio4", prior_generation=7)


def test_same_host_roles_are_distinct_and_ppm_is_route_bound() -> None:
    roles = {
        "topology": "same_host_roles",
        "transmitter_role": {
            "role": "transmitter",
            "host": "wspr5",
            "helper_sha256": "a",
            "config_sha256": "b",
        },
        "receiver_role": {
            "role": "receiver",
            "host": "wspr5",
            "helper_sha256": "c",
            "config_sha256": "d",
        },
    }
    validate_role_bindings(roles)
    duplicate = deepcopy(roles)
    duplicate["receiver_role"]["helper_sha256"] = "a"
    with pytest.raises(Rp1ContractError, match="distinct"):
        validate_role_bindings(duplicate)
    source = {
        "source_type": "manual_host_ppm",
        "value_ppm": 3.560,
        "host": "wspr5",
        "backend": "rp1_gpclk",
        "route": "gpio4",
        "compatibility_id": route_contract("gpio4")["compatibility_id"],
        "provenance": "operator supplied Step 6 value",
        "application_path": "--gpio-manual-ppm",
    }
    assert effective_ppm(source, 0.0, host="wspr5", route="gpio4") == 3.56
    with pytest.raises(Rp1ContractError):
        effective_ppm(source, 0.0, host="wspr5", route="gpio20")
