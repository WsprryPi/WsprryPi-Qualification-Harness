import hashlib
import inspect
import json
from copy import deepcopy

import pytest

import wsprrypi_qualification.keyed_session_contracts as contracts_module
from wsprrypi_qualification.keyed_session_contracts import (
    KeyedSessionContractError,
    authorization_sha256,
    canonical_sha256,
    compose_keyed_aggregate_session,
    compose_keyed_result,
    compose_keyed_runtime_authorization,
    resolved_keyed_plan_sha256,
    validate_keyed_aggregate_session,
    validate_keyed_artifact_index,
    validate_keyed_result,
    validate_keyed_runtime_authorization,
    validate_keyed_transaction,
    validate_resolved_keyed_plan,
)
from wsprrypi_qualification.offline import OfflineAnalysisError, validate_document


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def artifact(label: str) -> dict[str, object]:
    return {"path": f"inputs/{label}.json", "size_bytes": len(label) + 1, "sha256": digest(label)}


def plan(mode: str = "QRSS") -> dict[str, object]:
    return {
        "schema_version": 1,
        "evidence_type": "resolved_keyed_session_plan",
        "session_id": "keyed-session-001",
        "mode": mode,
        "transmitter": {
            "host": "wspr4",
            "backend": "legacy_gpio",
            "output": "GPIO4",
            "frequency_hz": 14_097_100,
            "drive": 0,
            "executable": artifact("wsprrypi"),
        },
        "receiver": {"host": "wspr5", "device": "RSP1B-2404058C60", "sample_rate_hz": 250_000},
        "rf_path": {
            "antenna_connected": False,
            "attenuation_db": 20,
            "safe_input_basis": "bounded conducted fixture",
        },
        "reference": {"plan": artifact("mode-plan"), "expected_events": artifact("events")},
        "deadlines": {"transaction_s": 10, "cleanup_s": 5, "overall_s": 35},
        "stopping_procedure": "stop and notify operator",
        "transaction_count": 3,
    }


def authorization(resolved: dict[str, object]) -> dict[str, object]:
    return compose_keyed_runtime_authorization(
        resolved, operator="operator", authorized_utc="2026-08-21T12:00:00Z"
    )


def transaction(
    resolved: dict[str, object], auth: dict[str, object], number: int
) -> dict[str, object]:
    lifecycle = [
        {"stage": stage, "outcome": "passed"}
        for stage in (
            "preflight",
            "cleanup_installed",
            "process_started",
            "capture_completed",
            "analysis_completed",
            "cleanup_completed",
            "quiescence_verified",
        )
    ]
    return {
        "schema_version": 1,
        "evidence_type": "keyed_transaction",
        "session_id": resolved["session_id"],
        "mode": resolved["mode"],
        "plan_sha256": resolved_keyed_plan_sha256(resolved),
        "authorization_sha256": authorization_sha256(resolved, auth),
        "transaction_number": number,
        "transaction_id": f"transaction-{number}",
        "process_id": f"process-{number}",
        "capture_id": f"capture-{number}",
        "acquisition_id": f"acquisition-{number}",
        "analysis_id": f"analysis-{number}",
        "lifecycle": lifecycle,
        "measurement_outcome": "passed",
        "cleanup_outcome": "verified",
        "quiescence_outcome": "verified",
        "final_outcome": "passed",
        "artifacts": [
            {
                "role": role,
                "path": f"transactions/{number}/{role}.json",
                "size_bytes": number * 10 + index,
                "sha256": digest(f"{number}-{role}"),
            }
            for index, role in enumerate(("process", "capture", "analysis"), start=1)
        ],
        "qualification_claim": False,
    }


@pytest.mark.parametrize("mode", ("QRSS", "FSKCW", "DFCW"))
def test_each_keyed_mode_composes_offline_qualified_contracts(mode: str) -> None:
    resolved = validate_resolved_keyed_plan(plan(mode))
    auth = authorization(resolved)
    transactions = [transaction(resolved, auth, number) for number in (1, 2, 3)]
    aggregate = compose_keyed_aggregate_session(resolved, auth, transactions)
    result = compose_keyed_result(resolved, auth, aggregate)
    assert aggregate["final_status"] == "qualified"
    assert aggregate["qualification_claim"] is True
    assert result["final_status"] == "qualified"
    assert result["qualification_claim"] is True
    assert result["cleanup_verified"] is True
    assert result["quiescence_verified"] is True


@pytest.mark.parametrize("mode", ("WSPR", "TONE", "CW", "qrss", ""))
def test_non_keyed_or_noncanonical_modes_are_rejected(mode: str) -> None:
    with pytest.raises(OfflineAnalysisError, match="violates schema"):
        validate_resolved_keyed_plan(plan(mode))


def test_plan_digest_is_canonical_and_authorization_is_exact() -> None:
    resolved = plan()
    reordered = json.loads(json.dumps(resolved, sort_keys=True))
    assert resolved_keyed_plan_sha256(resolved) == resolved_keyed_plan_sha256(reordered)
    auth = authorization(resolved)
    changed = deepcopy(resolved)
    changed["transmitter"]["drive"] = 1  # type: ignore[index]
    with pytest.raises(KeyedSessionContractError, match="exact resolved keyed plan"):
        validate_keyed_runtime_authorization(changed, auth)
    stale = deepcopy(auth)
    stale["authorized_utc"] = "2026-08-21T12:00:00+00:00"
    with pytest.raises(KeyedSessionContractError, match="canonical UTC Z"):
        validate_keyed_runtime_authorization(resolved, stale)


def test_plan_deadline_and_reference_independence_are_semantic() -> None:
    short = plan()
    short["deadlines"]["overall_s"] = 34  # type: ignore[index]
    with pytest.raises(KeyedSessionContractError, match="three bounded transactions"):
        validate_resolved_keyed_plan(short)
    reused = plan()
    reused["reference"]["expected_events"]["sha256"] = reused["reference"]["plan"][  # type: ignore[index]
        "sha256"
    ]
    with pytest.raises(KeyedSessionContractError, match="must be independent"):
        validate_resolved_keyed_plan(reused)


def test_partial_failure_aggregate_is_allowed_but_three_are_required_to_qualify() -> None:
    resolved = plan()
    auth = authorization(resolved)
    transactions = [transaction(resolved, auth, number) for number in (1, 2, 3)]
    partial = compose_keyed_aggregate_session(resolved, auth, transactions[:2])
    assert partial["final_status"] == "inconclusive"
    assert partial["qualification_claim"] is False
    swapped = compose_keyed_aggregate_session(resolved, auth, transactions)
    swapped["transactions"][0], swapped["transactions"][1] = (  # type: ignore[index]
        swapped["transactions"][1],  # type: ignore[index]
        swapped["transactions"][0],  # type: ignore[index]
    )
    with pytest.raises(KeyedSessionContractError, match="contiguous"):
        validate_keyed_aggregate_session(resolved, auth, swapped)


@pytest.mark.parametrize(
    "identity", ("transaction_id", "process_id", "capture_id", "acquisition_id", "analysis_id")
)
def test_independent_transaction_identities_cannot_be_reused(identity: str) -> None:
    resolved = plan()
    auth = authorization(resolved)
    transactions = [transaction(resolved, auth, number) for number in (1, 2, 3)]
    transactions[2][identity] = transactions[0][identity]
    with pytest.raises(KeyedSessionContractError, match=f"reuses {identity}"):
        compose_keyed_aggregate_session(resolved, auth, transactions)


@pytest.mark.parametrize("field", ("path", "sha256"))
def test_artifacts_cannot_be_reused_across_transactions(field: str) -> None:
    resolved = plan()
    auth = authorization(resolved)
    transactions = [transaction(resolved, auth, number) for number in (1, 2, 3)]
    transactions[2]["artifacts"][0][field] = transactions[0]["artifacts"][0][field]  # type: ignore[index]
    with pytest.raises(KeyedSessionContractError, match=f"artifact {field}"):
        compose_keyed_aggregate_session(resolved, auth, transactions)


def test_artifact_roles_cannot_be_reused_within_a_transaction() -> None:
    resolved = plan()
    auth = authorization(resolved)
    item = transaction(resolved, auth, 1)
    item["artifacts"][1]["role"] = item["artifacts"][0]["role"]  # type: ignore[index]
    with pytest.raises(KeyedSessionContractError, match="artifact role"):
        validate_keyed_transaction(resolved, auth, item)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("cleanup", "cleanup_failed"),
        ("quiescence", "cleanup_failed"),
        ("aborted", "aborted"),
        ("preflight", "preflight_failed"),
        ("blocked", "fixture_blocked"),
        ("failed", "unqualified_keyed"),
        ("inconclusive", "inconclusive"),
    ),
)
def test_result_precedence_overrides_measurement_success(mutation: str, expected: str) -> None:
    resolved = plan()
    auth = authorization(resolved)
    transactions = [transaction(resolved, auth, number) for number in (1, 2, 3)]
    changed = transactions[1]
    if mutation == "cleanup":
        changed["cleanup_outcome"] = "failed"
        changed["lifecycle"][5]["outcome"] = "failed"  # type: ignore[index]
        changed["final_outcome"] = "cleanup_failed"
    elif mutation == "quiescence":
        changed["quiescence_outcome"] = "failed"
        changed["lifecycle"][6]["outcome"] = "failed"  # type: ignore[index]
        changed["final_outcome"] = "cleanup_failed"
    elif mutation == "aborted":
        changed["lifecycle"][3]["outcome"] = "aborted"  # type: ignore[index]
        changed["final_outcome"] = "aborted"
    elif mutation == "preflight":
        changed["lifecycle"][0]["outcome"] = "failed"  # type: ignore[index]
        changed["final_outcome"] = "preflight_failed"
    else:
        changed["measurement_outcome"] = mutation
        changed["final_outcome"] = "blocked" if mutation == "blocked" else mutation
    aggregate = compose_keyed_aggregate_session(resolved, auth, transactions)
    result = compose_keyed_result(resolved, auth, aggregate)
    assert aggregate["final_status"] == expected
    assert result["final_status"] == expected
    assert result["qualification_claim"] is False
    if mutation in {"cleanup", "quiescence"}:
        assert result["cleanup_verified"] is (mutation != "cleanup")
        assert result["quiescence_verified"] is (mutation != "quiescence")


def test_transaction_lifecycle_order_and_derived_outcome_are_enforced() -> None:
    resolved = plan()
    auth = authorization(resolved)
    item = transaction(resolved, auth, 1)
    item["lifecycle"][2], item["lifecycle"][3] = item["lifecycle"][3], item["lifecycle"][2]  # type: ignore[index]
    with pytest.raises(OfflineAnalysisError, match="violates schema"):
        validate_keyed_transaction(resolved, auth, item)
    item = transaction(resolved, auth, 1)
    item["final_outcome"] = "failed"
    with pytest.raises(KeyedSessionContractError, match="result precedence"):
        validate_keyed_transaction(resolved, auth, item)


def test_result_must_equal_the_authenticated_aggregate_derivation() -> None:
    resolved = plan()
    auth = authorization(resolved)
    aggregate = compose_keyed_aggregate_session(
        resolved, auth, [transaction(resolved, auth, number) for number in (1, 2, 3)]
    )
    result = compose_keyed_result(resolved, auth, aggregate)
    result["aggregate_sha256"] = "f" * 64
    with pytest.raises(KeyedSessionContractError, match="contradicts"):
        validate_keyed_result(resolved, auth, aggregate, result)


def index(resolved: dict[str, object]) -> dict[str, object]:
    roles = [
        "resolved_plan",
        "runtime_authorization",
        "transaction_1",
        "transaction_2",
        "transaction_3",
        "aggregate_session",
        "result",
    ]
    return {
        "schema_version": 1,
        "evidence_type": "keyed_artifact_index",
        "session_id": resolved["session_id"],
        "plan_sha256": resolved_keyed_plan_sha256(resolved),
        "artifacts": [
            {
                "role": role,
                "path": f"contracts/{role}.json",
                "size_bytes": number + 1,
                "sha256": digest(role),
            }
            for number, role in enumerate(roles)
        ],
    }


def test_artifact_index_requires_unique_complete_safe_contract_set() -> None:
    resolved = plan()
    valid = index(resolved)
    assert validate_keyed_artifact_index(resolved, valid) == valid
    missing = deepcopy(valid)
    missing["artifacts"].pop()  # type: ignore[union-attr]
    with pytest.raises(OfflineAnalysisError, match="violates schema"):
        validate_keyed_artifact_index(resolved, missing)
    for mutation, message in (("path", "artifact path"), ("hash", "artifact sha256")):
        changed = deepcopy(valid)
        if mutation == "path":
            changed["artifacts"][1]["path"] = changed["artifacts"][0]["path"]  # type: ignore[index]
        else:
            changed["artifacts"][1]["sha256"] = changed["artifacts"][0]["sha256"]  # type: ignore[index]
        with pytest.raises(KeyedSessionContractError, match=message):
            validate_keyed_artifact_index(resolved, changed)
    unsafe = deepcopy(valid)
    unsafe["artifacts"][0]["path"] = r"C:\outside\plan.json"  # type: ignore[index]
    with pytest.raises(KeyedSessionContractError, match="safe and relative"):
        validate_keyed_artifact_index(resolved, unsafe)


def test_schema_rejects_extra_fields_and_nonfinite_digest_input() -> None:
    resolved = plan()
    changed = deepcopy(resolved)
    changed["future"] = True
    with pytest.raises(OfflineAnalysisError, match="violates schema"):
        validate_resolved_keyed_plan(changed)
    with pytest.raises(KeyedSessionContractError, match="finite JSON"):
        canonical_sha256({"value": float("nan")})
    validate_document(authorization(resolved), "keyed-runtime-authorization.schema.json")


def test_contract_module_has_no_hardware_capable_import_or_entrypoint() -> None:
    source = inspect.getsource(contracts_module)
    for forbidden in (
        "subprocess",
        "socket",
        "paramiko",
        "SoapySDR",
        "remote_exec",
        "live_adapters",
        "transports",
        "gpio",
    ):
        assert forbidden not in source
