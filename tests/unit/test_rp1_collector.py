from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wsprrypi_qualification.capability_helper import (
    PROTOCOL_VERSION,
    CapabilityHelperServer,
    CommandRp1Backend,
    HelperProtocolError,
    decode_request,
)
from wsprrypi_qualification.real_capabilities import JsonHelperClient, RuntimeAuthorization
from wsprrypi_qualification.rp1_collector import (
    Rp1CollectorError,
    SameHostRole,
    SameHostRp1Collector,
    collection_plan_sha256,
    validate_rp1_collection,
)
from wsprrypi_qualification.rp1_contracts import route_contract


def preflight(route: str = "gpio4") -> dict[str, object]:
    contract = route_contract(route)
    return {
        "host": "wspr5",
        "endpoint": contract["endpoint"],
        "endpoint_type": "character",
        "endpoint_owner": "root",
        "endpoint_group": "root",
        "endpoint_mode": "0600",
        "endpoint_device_identity": "240:0",
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


def roles() -> tuple[SameHostRole, SameHostRole]:
    return (
        SameHostRole("transmitter", "wspr5", "tx", "tx-helper", "1" * 64, "2" * 64),
        SameHostRole("receiver", "wspr5", "rx", "rx-helper", "3" * 64, "4" * 64),
    )


@dataclass
class FakeRoleClient:
    plan_sha256: str
    helper_identity: str
    result: dict[str, object]
    executable_sha256: str
    configuration_sha256: str
    expected_route: str = "gpio4"
    calls: int = 0

    def request_evidence(
        self,
        operation: str,
        payload: dict[str, object],
        *,
        response_timeout_s: float | None = None,
    ) -> dict[str, object]:
        assert operation == "rp1-inspect"
        assert payload == {
            "route": self.expected_route,
            "read_only": True,
            "acquire_endpoint": False,
        }
        assert response_timeout_s == 2
        self.calls += 1
        return {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "request-1",
            "operation": operation,
            "plan_sha256": self.plan_sha256,
            "helper_identity": self.helper_identity,
            "outcome": "completed",
            "result": self.result,
        }


def collector(
    route: str = "gpio4",
) -> tuple[SameHostRp1Collector, FakeRoleClient, FakeRoleClient, str]:
    transmitter, receiver = roles()
    digest = collection_plan_sha256(route, transmitter, receiver)
    tx_client = FakeRoleClient(
        digest,
        transmitter.helper_identity,
        preflight(route),
        transmitter.helper_sha256,
        transmitter.config_sha256,
        route,
    )
    rx_client = FakeRoleClient(
        digest,
        receiver.helper_identity,
        preflight(route),
        receiver.helper_sha256,
        receiver.config_sha256,
        route,
    )
    return (
        SameHostRp1Collector(transmitter, tx_client, receiver, rx_client),
        tx_client,
        rx_client,
        digest,
    )


def authorization(digest: str) -> RuntimeAuthorization:
    return RuntimeAuthorization(digest, "operator", datetime.now(UTC), True, False)


def test_passive_collection_uses_only_transmitter_channel_and_binds_both_roles() -> None:
    subject, tx_client, rx_client, digest = collector()
    document = subject.collect("gpio4", authorization(digest), response_timeout_s=2)
    assert document["plan_sha256"] == digest
    assert document["read_only"] is True
    assert document["endpoint_acquired"] is False
    assert document["qualification_claim"] is False
    assert tx_client.calls == 1
    assert rx_client.calls == 0
    validate_rp1_collection(document)


def test_gpio20_collection_remains_independently_route_bound() -> None:
    subject, tx_client, rx_client, digest = collector("gpio20")
    document = subject.collect("gpio20", authorization(digest), response_timeout_s=2)
    assert document["route"] == "gpio20"
    assert document["preflight"]["route"] == "gpio20"
    assert tx_client.calls == 1
    assert rx_client.calls == 0


def test_collector_rejects_authorization_role_and_plan_substitution() -> None:
    subject, _tx_client, _rx_client, digest = collector()
    with pytest.raises(Rp1CollectorError, match="authorization"):
        subject.collect("gpio4", None)
    with pytest.raises(Rp1CollectorError, match="authorization"):
        subject.collect(
            "gpio4", RuntimeAuthorization(digest, "operator", datetime.now(UTC), True, True)
        )
    transmitter, receiver = roles()
    duplicate = SameHostRole(
        "receiver",
        "wspr5",
        transmitter.channel_id,
        "rx-helper",
        "3" * 64,
        "4" * 64,
    )
    with pytest.raises(Rp1CollectorError, match="independent"):
        collection_plan_sha256("gpio4", transmitter, duplicate)
    wrong_plan = FakeRoleClient(
        "f" * 64,
        transmitter.helper_identity,
        preflight(),
        transmitter.helper_sha256,
        transmitter.config_sha256,
    )
    receiver_client = FakeRoleClient(
        digest,
        receiver.helper_identity,
        preflight(),
        receiver.helper_sha256,
        receiver.config_sha256,
    )
    subject = SameHostRp1Collector(transmitter, wrong_plan, receiver, receiver_client)
    with pytest.raises(Rp1CollectorError, match="collection plan"):
        subject.collect("gpio4", authorization(digest))
    wrong_plan.configuration_sha256 = "0" * 64
    with pytest.raises(Rp1CollectorError, match="configuration"):
        SameHostRp1Collector(transmitter, wrong_plan, receiver, receiver_client)


def test_collection_rejects_wrong_host_route_and_retained_response_tampering() -> None:
    subject, tx_client, _rx_client, digest = collector()
    tx_client.result["host"] = "other"
    with pytest.raises(Rp1CollectorError, match="host"):
        subject.collect("gpio4", authorization(digest), response_timeout_s=2)
    tx_client.result = preflight("gpio20")
    with pytest.raises((Rp1CollectorError, ValueError), match=r"wrong-route|mismatched"):
        subject.collect("gpio4", authorization(digest), response_timeout_s=2)
    tx_client.result = preflight()
    document = subject.collect("gpio4", authorization(digest), response_timeout_s=2)
    tampered = deepcopy(document)
    tampered["helper_response"]["request_id"] = "changed"
    with pytest.raises(Rp1CollectorError, match="contradictory"):
        validate_rp1_collection(tampered)
    tampered = deepcopy(document)
    tampered["receiver_role"]["channel_id"] = "changed"
    with pytest.raises(Rp1CollectorError, match="contradictory"):
        validate_rp1_collection(tampered)


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("inf"), float("nan")])
def test_collection_rejects_invalid_deadline_before_provider_call(timeout: float) -> None:
    subject, tx_client, _rx_client, digest = collector()
    with pytest.raises(Rp1CollectorError, match="deadline"):
        subject.collect("gpio4", authorization(digest), response_timeout_s=timeout)
    assert tx_client.calls == 0


def test_helper_protocol_exposes_only_fixed_passive_rp1_inspection() -> None:
    class Backend:
        def inspect(self, route: str) -> dict[str, object]:
            assert route == "gpio4"
            return preflight(route)

    digest = "d" * 64
    server = CapabilityHelperServer("tx-helper", digest, rp1=Backend())
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "request-1",
        "operation": "rp1-inspect",
        "plan_sha256": digest,
        "payload": {"route": "gpio4", "read_only": True, "acquire_endpoint": False},
    }
    response = server.dispatch(request)
    assert response["result"] == preflight()
    acquire = deepcopy(request)
    acquire["payload"]["acquire_endpoint"] = True
    with pytest.raises((HelperProtocolError, ValueError)):
        server.dispatch(acquire)


def test_command_rp1_backend_adds_only_passive_fixed_fields() -> None:
    class Backend:
        def __init__(self) -> None:
            self.payload: dict[str, object] | None = None

        def request(self, payload: dict[str, object]) -> dict[str, object]:
            self.payload = payload
            return preflight("gpio20")

    backend = Backend()
    result = CommandRp1Backend(backend).inspect("gpio20")  # type: ignore[arg-type]
    assert result == preflight("gpio20")
    assert backend.payload == {
        "route": "gpio20",
        "read_only": True,
        "acquire_endpoint": False,
    }
    with pytest.raises(HelperProtocolError, match="allowlisted"):
        CommandRp1Backend(backend).inspect("gpio17")  # type: ignore[arg-type]


def test_real_json_client_composes_two_in_process_role_channels(tmp_path: Path) -> None:
    class Backend:
        def inspect(self, route: str) -> dict[str, object]:
            return preflight(route)

    class Exchange:
        def __init__(self, server: CapabilityHelperServer) -> None:
            self.server = server
            self.calls = 0

        def exchange(self, encoded_request: str, timeout_s: float) -> str:
            assert timeout_s == 2
            self.calls += 1
            return json.dumps(self.server.dispatch(decode_request(encoded_request)))

    tx_path = tmp_path / "tx helper"
    rx_path = tmp_path / "rx helper"
    tx_path.write_text("tx", encoding="utf-8")
    rx_path.write_text("rx", encoding="utf-8")
    tx_hash = hashlib.sha256(tx_path.read_bytes()).hexdigest()
    rx_hash = hashlib.sha256(rx_path.read_bytes()).hexdigest()
    transmitter = SameHostRole("transmitter", "wspr5", "tx", "tx-helper", tx_hash, "2" * 64)
    receiver = SameHostRole("receiver", "wspr5", "rx", "rx-helper", rx_hash, "4" * 64)
    digest = collection_plan_sha256("gpio4", transmitter, receiver)
    tx_exchange = Exchange(CapabilityHelperServer("tx-helper", digest, rp1=Backend()))
    rx_exchange = Exchange(CapabilityHelperServer("rx-helper", digest, rp1=Backend()))
    tx_client = JsonHelperClient(
        tx_path.resolve(),
        tx_exchange,
        2,
        digest,
        "tx-helper",
        configuration_sha256=transmitter.config_sha256,
    )
    rx_client = JsonHelperClient(
        rx_path.resolve(),
        rx_exchange,
        2,
        digest,
        "rx-helper",
        configuration_sha256=receiver.config_sha256,
    )
    document = SameHostRp1Collector(transmitter, tx_client, receiver, rx_client).collect(
        "gpio4", authorization(digest), response_timeout_s=2
    )
    assert document["preflight"] == preflight()
    assert tx_exchange.calls == 1
    assert rx_exchange.calls == 0
