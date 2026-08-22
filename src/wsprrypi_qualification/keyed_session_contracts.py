"""Pure schemas and semantic validation for three-transaction keyed sessions.

This module performs no process, transport, device, service, or RF operation.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, NoReturn

from wsprrypi_qualification.application_shims import validate_application_plan
from wsprrypi_qualification.offline import FailureCause, OfflineAnalysisError, validate_document

KEYED_MODES = frozenset({"QRSS", "FSKCW", "DFCW"})
LIFECYCLE_STAGES = (
    "preflight",
    "cleanup_installed",
    "process_started",
    "capture_completed",
    "analysis_completed",
    "cleanup_completed",
    "quiescence_verified",
)
REQUIRED_INDEX_ROLES = frozenset(
    {
        "resolved_plan",
        "runtime_authorization",
        "transaction_1",
        "transaction_2",
        "transaction_3",
        "aggregate_session",
        "result",
    }
)


class KeyedSessionContractError(OfflineAnalysisError):
    """A keyed-session contract is malformed, unbound, reused, or contradictory."""


def canonical_sha256(value: Any) -> str:
    """Return SHA-256 of the maintained canonical finite JSON representation."""
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        _fail(f"document is not canonical finite JSON: {error}")
    return hashlib.sha256(payload).hexdigest()


def validate_resolved_keyed_plan(document: dict[str, Any]) -> dict[str, Any]:
    validate_document(document, "resolved-keyed-session-plan.schema.json")
    if document["mode"] not in KEYED_MODES:
        _fail("resolved keyed mode is unsupported")
    application = document["application_plan"]
    validate_application_plan(application)
    transmitter = document["transmitter"]
    if (
        application["protocol"].upper() != document["mode"]
        or application["identity"]["executable"] != transmitter["executable"]["path"]
        or application["identity"]["source_revision"] != document["target_revision"]
        or application["identity"]["submodule_revision"] != document["target_submodule_revision"]
        or application["backend"] != transmitter["backend"]
        or application["backend_contract"] is None
        or application["backend_contract"]["output"] != transmitter["output"]
        or application["backend_contract"]["drive_or_power_level"] != transmitter["drive"]
        or application["protocol_contract"]["primary_frequency_hz"] != transmitter["frequency_hz"]
    ):
        _fail("resolved WsprryPi application plan contradicts keyed plan bindings")
    bindings = document["capability_bindings"]
    if bindings["quiescence"] != transmitter["backend"]:
        _fail("quiescence capability does not match the keyed backend")
    if any(
        not service.startswith(("tx:", "rx:")) or service.endswith(":")
        for service in bindings["services"]
    ):
        _fail("keyed service bindings must identify the tx or rx host")
    required_receiver_services = set(bindings["required_receiver_services"])
    if not required_receiver_services.issubset(bindings["services"]):
        _fail("required keyed receiver services must be included in the service allowlist")
    receiver = document["receiver"]
    if (
        hashlib.sha256(receiver["device"].encode("utf-8")).hexdigest()
        != receiver["identity_sha256"]
    ):
        _fail("receiver identity hash does not bind the resolved device")
    bound_artifacts = [
        transmitter["executable"],
        document["reference"]["plan"],
        document["reference"]["expected_events"],
        bindings["ssh"],
        bindings["known_hosts"],
        bindings["transmitter_helper"],
        bindings["transmitter_helper_config"],
        bindings["transmitter_process_privilege_wrapper"],
        bindings["receiver_helper"],
        bindings["receiver_helper_config"],
        bindings["capture_helper"],
    ]
    for field in ("path", "sha256"):
        values = [item[field] for item in bound_artifacts]
        if len(values) != len(set(values)):
            _fail(f"resolved keyed plan reuses artifact {field}")
    deadlines = document["deadlines"]
    minimum_overall = 3 * deadlines["transaction_s"] + deadlines["cleanup_s"]
    if deadlines["overall_s"] < minimum_overall:
        _fail("overall deadline cannot contain three bounded transactions and cleanup")
    references = document["reference"]
    if references["plan"]["sha256"] == references["expected_events"]["sha256"]:
        _fail("keyed plan and expected-event artifacts must be independent")
    return deepcopy(document)


def resolved_keyed_plan_sha256(document: dict[str, Any]) -> str:
    return canonical_sha256(validate_resolved_keyed_plan(document))


def compose_keyed_runtime_authorization(
    plan: dict[str, Any], *, operator: str, authorized_utc: str
) -> dict[str, Any]:
    validated = validate_resolved_keyed_plan(plan)
    authorization = {
        "schema_version": 1,
        "evidence_type": "keyed_runtime_authorization",
        "session_id": validated["session_id"],
        "mode": validated["mode"],
        "operator": operator,
        "authorized_utc": authorized_utc,
        "resolved_plan_sha256": canonical_sha256(validated),
        "transaction_count": 3,
        "external_access_authorized": True,
        "rf_authorized": True,
    }
    return validate_keyed_runtime_authorization(validated, authorization)


def validate_keyed_runtime_authorization(
    plan: dict[str, Any], authorization: dict[str, Any]
) -> dict[str, Any]:
    validated = validate_resolved_keyed_plan(plan)
    validate_document(authorization, "keyed-runtime-authorization.schema.json")
    if (
        authorization["session_id"] != validated["session_id"]
        or authorization["mode"] != validated["mode"]
        or authorization["resolved_plan_sha256"] != canonical_sha256(validated)
    ):
        _fail("runtime authorization does not bind the exact resolved keyed plan")
    try:
        timestamp = datetime.fromisoformat(authorization["authorized_utc"].replace("Z", "+00:00"))
    except ValueError as error:
        _fail(f"runtime authorization timestamp is invalid: {error}")
    if timestamp.tzinfo is None or not authorization["authorized_utc"].endswith("Z"):
        _fail("runtime authorization timestamp must use canonical UTC Z form")
    return deepcopy(authorization)


def authorization_sha256(plan: dict[str, Any], authorization: dict[str, Any]) -> str:
    return canonical_sha256(validate_keyed_runtime_authorization(plan, authorization))


def validate_keyed_transaction(
    plan: dict[str, Any], authorization: dict[str, Any], transaction: dict[str, Any]
) -> dict[str, Any]:
    validated_plan = validate_resolved_keyed_plan(plan)
    validated_authorization = validate_keyed_runtime_authorization(validated_plan, authorization)
    validate_document(transaction, "keyed-transaction.schema.json")
    if (
        transaction["session_id"] != validated_plan["session_id"]
        or transaction["mode"] != validated_plan["mode"]
        or transaction["plan_sha256"] != canonical_sha256(validated_plan)
        or transaction["authorization_sha256"] != canonical_sha256(validated_authorization)
    ):
        _fail("keyed transaction does not bind its plan and runtime authorization")
    stages = transaction["lifecycle"]
    if tuple(stage["stage"] for stage in stages) != LIFECYCLE_STAGES:
        _fail("keyed transaction lifecycle order is invalid")
    if transaction["cleanup_outcome"] != (
        "verified" if stages[5]["outcome"] == "passed" else "failed"
    ):
        _fail("cleanup outcome contradicts the cleanup lifecycle stage")
    if transaction["quiescence_outcome"] != (
        "verified" if stages[6]["outcome"] == "passed" else "failed"
    ):
        _fail("quiescence outcome contradicts the quiescence lifecycle stage")
    if stages[3]["outcome"] == "failed" and transaction["measurement_outcome"] != "blocked":
        _fail("capture failure must be classified as receiver or fixture blockage")
    if (
        stages[3]["outcome"] == "passed"
        and stages[4]["outcome"] == "failed"
        and transaction["measurement_outcome"] != "failed"
    ):
        _fail("completed capture with failed analysis must be a keyed measurement failure")
    expected = derive_keyed_transaction_outcome(transaction)
    if transaction["final_outcome"] != expected:
        _fail("keyed transaction final outcome violates result precedence")
    artifact_keys = [(item["path"], item["sha256"]) for item in transaction["artifacts"]]
    if len(artifact_keys) != len(set(artifact_keys)):
        _fail("keyed transaction reuses an artifact identity")
    artifact_roles = [item["role"] for item in transaction["artifacts"]]
    if len(artifact_roles) != len(set(artifact_roles)):
        _fail("keyed transaction reuses an artifact role")
    for item in transaction["artifacts"]:
        _validate_safe_relative_path(item["path"], "keyed transaction artifact")
    return deepcopy(transaction)


def derive_keyed_transaction_outcome(transaction: dict[str, Any]) -> str:
    stages = transaction["lifecycle"]
    if transaction["cleanup_outcome"] == "failed" or transaction["quiescence_outcome"] == "failed":
        return "cleanup_failed"
    if any(stage["outcome"] == "aborted" for stage in stages):
        return "aborted"
    if stages[0]["outcome"] != "passed":
        return "preflight_failed"
    measurement = transaction["measurement_outcome"]
    if measurement == "blocked":
        return "blocked"
    if measurement == "failed":
        return "failed"
    if measurement == "inconclusive":
        return "inconclusive"
    if all(stage["outcome"] == "passed" for stage in stages):
        return "passed"
    return "inconclusive"


def compose_keyed_aggregate_session(
    plan: dict[str, Any], authorization: dict[str, Any], transactions: list[dict[str, Any]]
) -> dict[str, Any]:
    validated_plan = validate_resolved_keyed_plan(plan)
    validated_authorization = validate_keyed_runtime_authorization(validated_plan, authorization)
    validated_transactions = [
        validate_keyed_transaction(validated_plan, validated_authorization, transaction)
        for transaction in transactions
    ]
    status = derive_keyed_session_status(validated_transactions)
    aggregate = {
        "schema_version": 1,
        "evidence_type": "keyed_aggregate_session",
        "session_id": validated_plan["session_id"],
        "mode": validated_plan["mode"],
        "plan_sha256": canonical_sha256(validated_plan),
        "authorization_sha256": canonical_sha256(validated_authorization),
        "transactions": validated_transactions,
        "final_status": status,
        "qualification_claim": status == "qualified",
    }
    return validate_keyed_aggregate_session(validated_plan, validated_authorization, aggregate)


def validate_keyed_aggregate_session(
    plan: dict[str, Any], authorization: dict[str, Any], aggregate: dict[str, Any]
) -> dict[str, Any]:
    validated_plan = validate_resolved_keyed_plan(plan)
    validated_authorization = validate_keyed_runtime_authorization(validated_plan, authorization)
    validate_document(aggregate, "keyed-aggregate-session.schema.json")
    if (
        aggregate["session_id"] != validated_plan["session_id"]
        or aggregate["mode"] != validated_plan["mode"]
        or aggregate["plan_sha256"] != canonical_sha256(validated_plan)
        or aggregate["authorization_sha256"] != canonical_sha256(validated_authorization)
    ):
        _fail("aggregate session does not bind its plan and authorization")
    transactions = [
        validate_keyed_transaction(validated_plan, validated_authorization, transaction)
        for transaction in aggregate["transactions"]
    ]
    expected_numbers = list(range(1, len(transactions) + 1))
    if [item["transaction_number"] for item in transactions] != expected_numbers:
        _fail("aggregate session requires contiguous transactions beginning with 1")
    for field in ("transaction_id", "process_id", "capture_id", "acquisition_id", "analysis_id"):
        values = [item[field] for item in transactions]
        if len(set(values)) != len(transactions):
            _fail(f"aggregate session reuses {field}")
    artifacts = [item for transaction in transactions for item in transaction["artifacts"]]
    for field in ("path", "sha256"):
        values = [item[field] for item in artifacts]
        if len(values) != len(set(values)):
            _fail(f"aggregate session reuses artifact {field}")
    status = derive_keyed_session_status(transactions)
    if aggregate["final_status"] != status or aggregate["qualification_claim"] != (
        status == "qualified"
    ):
        _fail("aggregate status or qualification claim violates precedence")
    return deepcopy(aggregate)


def derive_keyed_session_status(transactions: list[dict[str, Any]]) -> str:
    outcomes = [transaction["final_outcome"] for transaction in transactions]
    if "cleanup_failed" in outcomes:
        return "cleanup_failed"
    if "aborted" in outcomes:
        return "aborted"
    if "preflight_failed" in outcomes:
        return "preflight_failed"
    if "blocked" in outcomes:
        return "fixture_blocked"
    if "failed" in outcomes:
        return "unqualified_keyed"
    if "inconclusive" in outcomes:
        return "inconclusive"
    return "qualified" if outcomes == ["passed", "passed", "passed"] else "inconclusive"


def compose_keyed_result(
    plan: dict[str, Any], authorization: dict[str, Any], aggregate: dict[str, Any]
) -> dict[str, Any]:
    validated = validate_keyed_aggregate_session(plan, authorization, aggregate)
    result = {
        "schema_version": 1,
        "evidence_type": "keyed_result",
        "session_id": validated["session_id"],
        "mode": validated["mode"],
        "plan_sha256": validated["plan_sha256"],
        "authorization_sha256": validated["authorization_sha256"],
        "aggregate_sha256": canonical_sha256(validated),
        "transaction_outcomes": [
            {
                "transaction_number": item["transaction_number"],
                "transaction_id": item["transaction_id"],
                "final_outcome": item["final_outcome"],
            }
            for item in validated["transactions"]
        ],
        "cleanup_verified": all(
            item["cleanup_outcome"] == "verified" for item in validated["transactions"]
        ),
        "quiescence_verified": all(
            item["quiescence_outcome"] == "verified" for item in validated["transactions"]
        ),
        "final_status": validated["final_status"],
        "qualification_claim": validated["qualification_claim"],
    }
    return validate_keyed_result(plan, authorization, validated, result)


def validate_keyed_result(
    plan: dict[str, Any],
    authorization: dict[str, Any],
    aggregate: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    validated = validate_keyed_aggregate_session(plan, authorization, aggregate)
    validate_document(result, "keyed-result.schema.json")
    expected = compose_keyed_result_unchecked(validated)
    if result != expected:
        _fail("keyed result contradicts the authenticated aggregate session")
    return deepcopy(result)


def compose_keyed_result_unchecked(aggregate: dict[str, Any]) -> dict[str, Any]:
    transactions = aggregate["transactions"]
    return {
        "schema_version": 1,
        "evidence_type": "keyed_result",
        "session_id": aggregate["session_id"],
        "mode": aggregate["mode"],
        "plan_sha256": aggregate["plan_sha256"],
        "authorization_sha256": aggregate["authorization_sha256"],
        "aggregate_sha256": canonical_sha256(aggregate),
        "transaction_outcomes": [
            {
                "transaction_number": item["transaction_number"],
                "transaction_id": item["transaction_id"],
                "final_outcome": item["final_outcome"],
            }
            for item in transactions
        ],
        "cleanup_verified": all(item["cleanup_outcome"] == "verified" for item in transactions),
        "quiescence_verified": all(
            item["quiescence_outcome"] == "verified" for item in transactions
        ),
        "final_status": aggregate["final_status"],
        "qualification_claim": aggregate["qualification_claim"],
    }


def validate_keyed_artifact_index(plan: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    validated_plan = validate_resolved_keyed_plan(plan)
    validate_document(index, "keyed-artifact-index.schema.json")
    if index["session_id"] != validated_plan["session_id"] or index[
        "plan_sha256"
    ] != canonical_sha256(validated_plan):
        _fail("keyed artifact index does not bind the resolved plan")
    roles = [item["role"] for item in index["artifacts"]]
    if any(roles.count(role) != 1 for role in REQUIRED_INDEX_ROLES):
        _fail("keyed artifact index requires each contract artifact exactly once")
    for field in ("path", "sha256"):
        values = [item[field] for item in index["artifacts"]]
        if len(values) != len(set(values)):
            _fail(f"keyed artifact index reuses artifact {field}")
    for item in index["artifacts"]:
        _validate_safe_relative_path(item["path"], "keyed artifact index")
    return deepcopy(index)


def _validate_safe_relative_path(value: str, context: str) -> None:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or ".." in posix.parts
        or ".." in windows.parts
        or not posix.parts
    ):
        _fail(f"{context} paths must be safe and relative")


def _fail(message: str) -> NoReturn:
    raise KeyedSessionContractError(message, cause=FailureCause.CONTRADICTORY_EVIDENCE)
