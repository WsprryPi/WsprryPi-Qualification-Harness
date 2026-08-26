from copy import deepcopy

import pytest

from wsprrypi_qualification.offline import OfflineAnalysisError, validate_document
from wsprrypi_qualification.rp1_contracts import (
    Rp1ContractError,
    effective_ppm,
    route_contract,
    validate_preflight,
    validate_role_bindings,
)
from wsprrypi_qualification.rp1_contracts import (
    validate_operation_lifecycle as _validate_operation_lifecycle,
)


def validate_operation_lifecycle(
    document: dict[str, object], *, route: str, prior_generation: int, expected_plan_sha256: str
) -> dict[str, object]:
    return _validate_operation_lifecycle(
        document,
        route=route,
        prior_generation=prior_generation,
        expected_plan_sha256=expected_plan_sha256,
        expected_endpoint_device_identity="major:minor",
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
        "abi_version": 3,
        "query_version": 3,
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
        "process": {
            "pid": 123,
            "executable_sha256": "e" * 64,
            "started": True,
            "exited": True,
            "exit_code": 0,
        },
        "endpoint": contract["endpoint"],
        "endpoint_device_identity": "major:minor",
        "route": route,
        "compatibility_id": contract["compatibility_id"],
        "lease": 19,
        "generation": 8,
        "transitions": ["RUNNING", "DRAINING", "COMPLETE"],
        "terminal_reason": "COMPLETE",
        "current_event": "NONE",
        "elapsed_ns": 1_000_000_000,
        "remaining_ns": 0,
        "cancellation_outcome": "not_requested",
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
        "capture_outcome": "completed",
        "measurement_outcome": "passed",
        "final_status": "passed",
    }


def test_rp1_preflight_and_finite_lifecycle_accept_complete_evidence() -> None:
    validate_preflight(preflight(), route="gpio4")
    validate_operation_lifecycle(
        lifecycle(), route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
    )


def test_real_session_quiescence_stage_retains_typed_rp1_preflight() -> None:
    evidence = {
        "schema_version": 1,
        "evidence_type": "rf_idle",
        "plan_sha256": "d" * 64,
        "outcome": "verified",
        "elapsed_s": 0.1,
        "deadline_s": 5,
        "details": {
            "backend": "rp1_gpclk",
            "output": "GPIO4",
            "verified": True,
            "rp1_preflight": preflight(),
        },
    }
    validate_document(evidence, "real-session-stage-evidence.schema.json")

    del evidence["details"]["rp1_preflight"]
    with pytest.raises(OfflineAnalysisError):
        validate_document(evidence, "real-session-stage-evidence.schema.json")


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
        validate_operation_lifecycle(
            lifecycle("gpio4"),
            route="gpio20",
            prior_generation=7,
            expected_plan_sha256="d" * 64,
        )
    stale = lifecycle()
    stale["generation"] = 7
    with pytest.raises(Rp1ContractError, match="strictly increasing"):
        validate_operation_lifecycle(
            stale, route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
        )


def test_cleanup_failure_must_override_measurement_success() -> None:
    for field in (
        "lease_released",
        "endpoint_closed",
        "cleanup_verified",
        "terminal_silence_verified",
    ):
        evidence = lifecycle()
        evidence[field] = False
        with pytest.raises(Rp1ContractError, match="override"):
            validate_operation_lifecycle(
                evidence,
                route="gpio4",
                prior_generation=7,
                expected_plan_sha256="d" * 64,
            )
        evidence["final_status"] = "cleanup_failed"
        validate_operation_lifecycle(
            evidence, route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
        )
    contradictory = lifecycle()
    contradictory["capture_outcome"] = "fixture_blocked"
    contradictory["measurement_outcome"] = "passed"
    contradictory["lease_released"] = False
    contradictory["final_status"] = "cleanup_failed"
    with pytest.raises(Rp1ContractError, match="capture and measurement"):
        validate_operation_lifecycle(
            contradictory,
            route="gpio4",
            prior_generation=7,
            expected_plan_sha256="d" * 64,
        )


def test_continuous_tone_and_unknown_fields_are_rejected_by_schema() -> None:
    continuous = lifecycle()
    continuous["tone_operation"] = "CONTINUOUS"
    with pytest.raises(Rp1ContractError, match="schema"):
        validate_operation_lifecycle(
            continuous,
            route="gpio4",
            prior_generation=7,
            expected_plan_sha256="d" * 64,
        )
    unknown = lifecycle()
    unknown["untrusted"] = True
    with pytest.raises(Rp1ContractError, match="Additional properties"):
        validate_operation_lifecycle(
            unknown, route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
        )


def test_plan_process_terminal_and_cancellation_bindings_fail_closed() -> None:
    with pytest.raises(Rp1ContractError, match="digest"):
        validate_operation_lifecycle(
            lifecycle(), route="gpio4", prior_generation=7, expected_plan_sha256="f" * 64
        )
    process = lifecycle()
    process["process"]["started"] = False
    with pytest.raises(Rp1ContractError, match="process"):
        validate_operation_lifecycle(
            process, route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
        )
    cancelled = lifecycle()
    cancelled["transitions"] = ["RUNNING", "DRAINING", "CANCELLED"]
    cancelled["terminal_reason"] = "CANCELLED"
    cancelled["cancellation_outcome"] = "completed"
    cancelled["final_status"] = "inconclusive"
    validate_operation_lifecycle(
        cancelled, route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
    )
    cancelled["cancellation_outcome"] = "not_requested"
    with pytest.raises(Rp1ContractError, match="cancellation"):
        validate_operation_lifecycle(
            cancelled, route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
        )
    contradictory = lifecycle()
    contradictory["transitions"] = ["RUNNING", "COMPLETE", "CANCELLED"]
    contradictory["terminal_reason"] = "CANCELLED"
    contradictory["cancellation_outcome"] = "completed"
    contradictory["final_status"] = "inconclusive"
    with pytest.raises(Rp1ContractError, match="transition order"):
        validate_operation_lifecycle(
            contradictory,
            route="gpio4",
            prior_generation=7,
            expected_plan_sha256="d" * 64,
        )


def test_receiver_blockage_after_authenticated_launch_is_not_transmitter_failure() -> None:
    blocked = lifecycle()
    blocked["capture_outcome"] = "fixture_blocked"
    blocked["measurement_outcome"] = "inconclusive"
    blocked["final_status"] = "fixture_blocked"
    validate_operation_lifecycle(
        blocked, route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
    )
    blocked["final_status"] = "transmitter_failed"
    with pytest.raises(Rp1ContractError, match="receiver blockage"):
        validate_operation_lifecycle(
            blocked, route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
        )
    failed_process = lifecycle()
    failed_process["process"]["exit_code"] = 1
    failed_process["capture_outcome"] = "fixture_blocked"
    failed_process["measurement_outcome"] = "inconclusive"
    failed_process["final_status"] = "fixture_blocked"
    with pytest.raises(Rp1ContractError, match="process failure"):
        validate_operation_lifecycle(
            failed_process,
            route="gpio4",
            prior_generation=7,
            expected_plan_sha256="d" * 64,
        )
    failed_process["final_status"] = "transmitter_failed"
    validate_operation_lifecycle(
        failed_process, route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
    )


def test_endpoint_identity_and_measurement_failure_classification_are_bound() -> None:
    substituted = lifecycle()
    substituted["endpoint_device_identity"] = "different-device"
    with pytest.raises(Rp1ContractError, match="device identity"):
        validate_operation_lifecycle(
            substituted, route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
        )
    failed = lifecycle()
    failed["measurement_outcome"] = "failed"
    failed["final_status"] = "measurement_failed"
    validate_operation_lifecycle(
        failed, route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
    )
    failed["final_status"] = "transmitter_failed"
    with pytest.raises(Rp1ContractError, match="final status"):
        validate_operation_lifecycle(
            failed, route="gpio4", prior_generation=7, expected_plan_sha256="d" * 64
        )


def test_preflight_rejects_unknown_fields_and_wrong_route_state() -> None:
    unknown = preflight()
    unknown["repair_command"] = "never"
    with pytest.raises(Rp1ContractError, match="Additional properties"):
        validate_preflight(unknown, route="gpio4")
    wrong_route = preflight()
    wrong_route["route_state"]["active_overlay"] = "gpio20"
    with pytest.raises(Rp1ContractError, match="do not agree"):
        validate_preflight(wrong_route, route="gpio4")


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
