"""Plan-bound passive RP1 evidence collection over distinct same-host roles."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Protocol, cast

from wsprrypi_qualification.offline import validate_document
from wsprrypi_qualification.real_capabilities import (
    RuntimeAuthorization,
    capability_plan_sha256,
)
from wsprrypi_qualification.rp1_contracts import (
    canonical_sha256,
    validate_preflight,
)


class Rp1CollectorError(RuntimeError):
    """Passive RP1 evidence or same-host role identity is unsafe."""


class RoleHelperClient(Protocol):
    plan_sha256: str
    helper_identity: str
    executable_sha256: str
    configuration_sha256: str | None

    def request_evidence(
        self,
        operation: str,
        payload: dict[str, object],
        *,
        response_timeout_s: float | None = None,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class SameHostRole:
    role: str
    host: str
    channel_id: str
    helper_identity: str
    helper_sha256: str
    config_sha256: str
    trust_path: str = "controller_to_host_pinned"
    agent_forwarding: bool = False
    self_ssh: bool = False

    def document(self) -> dict[str, object]:
        return asdict(self)


def collection_plan(
    route: str, transmitter: SameHostRole, receiver: SameHostRole
) -> dict[str, object]:
    document = {
        "operation": "rp1-inspect",
        "route": route,
        "host": transmitter.host,
        "topology": "same_host_roles",
        "transmitter_role": transmitter.document(),
        "receiver_role": receiver.document(),
        "read_only": True,
        "endpoint_acquired": False,
    }
    _validate_roles(document)
    return document


def collection_plan_sha256(route: str, transmitter: SameHostRole, receiver: SameHostRole) -> str:
    return capability_plan_sha256(collection_plan(route, transmitter, receiver))


class SameHostRp1Collector:
    """Collect through TX identity while retaining an independent RX role binding."""

    def __init__(
        self,
        transmitter: SameHostRole,
        transmitter_client: RoleHelperClient,
        receiver: SameHostRole,
        receiver_client: RoleHelperClient,
    ) -> None:
        plan = collection_plan("gpio4", transmitter, receiver)
        del plan  # route-independent role validation only
        if transmitter_client is receiver_client:
            raise Rp1CollectorError("same-host logical roles require distinct client channels")
        if (
            transmitter_client.helper_identity != transmitter.helper_identity
            or receiver_client.helper_identity != receiver.helper_identity
            or transmitter_client.executable_sha256 != transmitter.helper_sha256
            or receiver_client.executable_sha256 != receiver.helper_sha256
            or transmitter_client.configuration_sha256 != transmitter.config_sha256
            or receiver_client.configuration_sha256 != receiver.config_sha256
        ):
            raise Rp1CollectorError(
                "same-host helper or configuration differs from its role binding"
            )
        self.transmitter = transmitter
        self.transmitter_client = transmitter_client
        self.receiver = receiver
        self.receiver_client = receiver_client

    def collect(
        self,
        route: str,
        authorization: RuntimeAuthorization | None,
        *,
        response_timeout_s: float = 5.0,
    ) -> dict[str, object]:
        plan = collection_plan(route, self.transmitter, self.receiver)
        plan_sha256 = capability_plan_sha256(plan)
        if (
            isinstance(response_timeout_s, bool)
            or not math.isfinite(response_timeout_s)
            or response_timeout_s <= 0
        ):
            raise Rp1CollectorError("RP1 passive response deadline must be positive")
        if (
            authorization is None
            or not authorization.external_access_authorized
            or authorization.rf_authorized
            or authorization.plan_sha256 != plan_sha256
        ):
            raise Rp1CollectorError(
                "exact passive external-access authorization is required; "
                "RF must remain unauthorized"
            )
        if (
            self.transmitter_client.plan_sha256 != plan_sha256
            or self.receiver_client.plan_sha256 != plan_sha256
        ):
            raise Rp1CollectorError(
                "same-host helper channels are not bound to the collection plan"
            )
        response = self.transmitter_client.request_evidence(
            "rp1-inspect",
            {"route": route, "read_only": True, "acquire_endpoint": False},
            response_timeout_s=response_timeout_s,
        )
        validate_document(response, "helper-response.schema.json")
        if (
            response.get("operation") != "rp1-inspect"
            or response.get("plan_sha256") != plan_sha256
            or response.get("helper_identity") != self.transmitter.helper_identity
        ):
            raise Rp1CollectorError("RP1 helper response is not bound to the transmitter role")
        preflight = cast(dict[str, object], response["result"])
        validate_preflight(preflight, route=route)
        if preflight["host"] != self.transmitter.host:
            raise Rp1CollectorError("RP1 evidence host differs from same-host role binding")
        document = {
            "schema_version": 1,
            "evidence_type": "rp1_passive_collection",
            "plan_sha256": plan_sha256,
            "route": route,
            "host": self.transmitter.host,
            "topology": "same_host_roles",
            "transmitter_role": self.transmitter.document(),
            "receiver_role": self.receiver.document(),
            "helper_operation": "rp1-inspect",
            "helper_identity": self.transmitter.helper_identity,
            "helper_response_sha256": canonical_sha256(response),
            "helper_response": response,
            "preflight": preflight,
            "read_only": True,
            "endpoint_acquired": False,
            "qualification_claim": False,
        }
        validate_rp1_collection(document)
        return document


def validate_rp1_collection(document: dict[str, object]) -> dict[str, object]:
    validate_document(document, "rp1-passive-collection.schema.json")
    _validate_roles(document)
    route = cast(str, document["route"])
    preflight = cast(dict[str, object], document["preflight"])
    validate_preflight(preflight, route=route)
    transmitter = cast(dict[str, object], document["transmitter_role"])
    receiver = cast(dict[str, object], document["receiver_role"])
    response = cast(dict[str, object], document["helper_response"])
    expected_plan = {
        "operation": "rp1-inspect",
        "route": route,
        "host": document["host"],
        "topology": "same_host_roles",
        "transmitter_role": transmitter,
        "receiver_role": receiver,
        "read_only": True,
        "endpoint_acquired": False,
    }
    if (
        document["plan_sha256"] != capability_plan_sha256(expected_plan)
        or document["host"] != transmitter["host"]
        or preflight["host"] != document["host"]
        or document["helper_identity"] != transmitter["helper_identity"]
        or document["helper_response_sha256"] != canonical_sha256(response)
        or response.get("operation") != "rp1-inspect"
        or response.get("plan_sha256") != document["plan_sha256"]
        or response.get("helper_identity") != document["helper_identity"]
        or response.get("result") != preflight
    ):
        raise Rp1CollectorError("RP1 collection identity is contradictory")
    return document


def _validate_roles(document: dict[str, object]) -> None:
    route = document.get("route")
    if route not in {"gpio4", "gpio20"}:
        raise Rp1CollectorError("RP1 collection route must be exactly gpio4 or gpio20")
    transmitter = document.get("transmitter_role")
    receiver = document.get("receiver_role")
    if not isinstance(transmitter, dict) or not isinstance(receiver, dict):
        raise Rp1CollectorError("same-host role bindings are required")
    for expected, role in (("transmitter", transmitter), ("receiver", receiver)):
        if (
            role.get("role") != expected
            or role.get("host") != document.get("host")
            or role.get("trust_path") != "controller_to_host_pinned"
            or role.get("agent_forwarding") is not False
            or role.get("self_ssh") is not False
            or any(
                re.fullmatch(r"[0-9a-f]{64}", cast(str, role.get(name))) is None
                for name in ("helper_sha256", "config_sha256")
            )
        ):
            raise Rp1CollectorError(f"{expected} same-host role binding is incomplete")
    distinct = ("channel_id", "helper_identity", "helper_sha256", "config_sha256")
    if any(transmitter.get(name) == receiver.get(name) for name in distinct):
        raise Rp1CollectorError("same-host logical role channels are not independent")
