"""Strict production WTPF and UMP1 clients, independent of serial/OS adapters.

The packaged contracts are byte-for-byte copies of the selected Pico source.
No request is replayed after an uncertain response. A management ACK completes
delivery, independently of transmitter ownership acquired through WTP.
"""

from __future__ import annotations

import json
import math
import struct
import time
import uuid
from collections import deque
from collections.abc import Iterator
from functools import lru_cache
from importlib.resources import files
from typing import Any, Protocol, cast

from jsonschema import Draft202012Validator, ValidationError, validators
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


class PicoWireError(ValueError):
    """A peer cannot establish the current closed wire contract."""


class ByteStream(Protocol):
    """Deadline-bound byte IO; implementations must never renew the deadline."""

    def read(self, maximum: int, deadline: float) -> bytes: ...
    def write(self, data: bytes, deadline: float) -> None: ...
    def close(self) -> None: ...


def loads(data: bytes | str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise PicoWireError("Duplicate JSON member")
            result[key] = value
        return result

    def nonfinite(value: str) -> None:
        raise PicoWireError("Nonfinite JSON number: " + value)

    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
        raise PicoWireError("Invalid Pico JSON encoding or nesting") from error
    if not isinstance(value, dict):
        raise PicoWireError("Expected JSON object")

    def finite(node: Any) -> bool:
        if isinstance(node, float):
            return math.isfinite(node)
        if isinstance(node, dict):
            return all(finite(child) for child in node.values())
        if isinstance(node, list):
            return all(finite(child) for child in node)
        return True

    if not finite(value):
        raise PicoWireError("Nonfinite JSON number")
    return value


def encode(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _maximum(_validator: Any, maximum: str, value: Any, _schema: Any) -> Iterator[ValidationError]:
    if (
        isinstance(value, str)
        and value.isascii()
        and value.isdecimal()
        and int(value) > int(maximum)
    ):
        yield ValidationError("Decimal exceeds its wire bound")


def _utf8(_validator: Any, maximum: int, value: Any, _schema: Any) -> Iterator[ValidationError]:
    if isinstance(value, str) and len(value.encode("utf-8")) > maximum:
        yield ValidationError("String exceeds its UTF-8 wire bound")


@lru_cache(maxsize=32)
def _validator(contract: str, definition: str | None) -> Any:
    schema = loads(
        files("wsprrypi_qualification.schemas")
        .joinpath(f"pico-{contract}.schema.json")
        .read_bytes()
    )
    if definition is not None:
        schema = {"$defs": schema["$defs"], "$ref": "#/$defs/" + definition}
    checker = Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: type(value) is int
    )
    cls = cast(Any, validators.extend)(
        Draft202012Validator,
        {"x-maximum": _maximum, "x-maxUtf8Bytes": _utf8},
        type_checker=checker,
    )
    registry = Registry()
    for name in ("control", "wtp", "observation", "network-diagnostics"):
        filename = f"pico-{name}.schema.json"
        resource_schema = loads(
            files("wsprrypi_qualification.schemas").joinpath(filename).read_bytes()
        )
        # Keep the selected strict validator when following an external ref.
        # A referenced $schema otherwise selects the stock validator and loses
        # exact integer checks and the uint64 decimal-bound extension.
        resource = Resource.from_contents(
            {key: value for key, value in resource_schema.items() if key != "$schema"},
            default_specification=DRAFT202012,
        )
        registry = registry.with_resource(filename, resource)
        if "$id" in resource_schema:
            registry = registry.with_resource(resource_schema["$id"], resource)
    return cls(schema, registry=registry)


def validate_contract(value: dict[str, Any], contract: str, definition: str | None = None) -> None:
    errors = list(_validator(contract, definition).iter_errors(value))
    if errors:
        # Resource documents can contain credentials/location. Do not include
        # jsonschema's instance rendering in public exceptions.
        raise PicoWireError(f"Invalid Pico {contract} {definition or 'message'} shape")


def validate_diagnostic(value: dict[str, Any]) -> None:
    validate_contract(value, "observation")


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def wtp_frame(value: dict[str, Any]) -> bytes:
    payload = encode(value)
    if not 0 < len(payload) <= 65536:
        raise PicoWireError("WTP payload exceeds wire limit")
    return b"WTPF\x01\x01\x00\x00" + struct.pack(">II", len(payload), crc32c(payload)) + payload


def read_exact(stream: ByteStream, count: int, deadline: float) -> bytes:
    result = bytearray()
    while len(result) < count:
        if time.monotonic() >= deadline:
            raise TimeoutError("Pico response deadline")
        part = stream.read(count - len(result), deadline)
        if not part:
            raise ConnectionError("Pico transport disconnected")
        if len(part) > count - len(result):
            raise PicoWireError("Transport exceeded requested read")
        result.extend(part)
    return bytes(result)


class WtpClient:
    """One explicit WTP connection; responses and events share a bounded stream."""

    def __init__(self, stream: ByteStream) -> None:
        self.stream = stream
        self.session = uuid.uuid4().hex
        self.pending: deque[dict[str, Any]] = deque()
        self.sequence = 0

    def _receive(self, deadline: float) -> dict[str, Any]:
        header = read_exact(self.stream, 16, deadline)
        if header[:8] != b"WTPF\x01\x01\x00\x00":
            raise PicoWireError("Unsupported WTP framing")
        size, checksum = struct.unpack(">II", header[8:])
        if not 0 < size <= 65536:
            raise PicoWireError("Invalid WTP length")
        data = read_exact(self.stream, size, deadline)
        if crc32c(data) != checksum:
            raise PicoWireError("WTP CRC mismatch")
        value = loads(data)
        validate_contract(value, "wtp")
        if value["session_id"] != self.session or value["type"] not in {"response", "event"}:
            raise PicoWireError("WTP session/type mismatch")
        return value

    def request(self, operation: str, body: dict[str, Any], timeout: float = 8) -> dict[str, Any]:
        self.sequence += 1
        request_id = f"{self.sequence:032x}"
        request = dict(
            type="request",
            protocol="WTP/1",
            session_id=self.session,
            request_id=request_id,
            op=operation,
            body=body,
        )
        validate_contract(request, "wtp", "request")
        deadline = time.monotonic() + timeout
        self.stream.write(wtp_frame(request), deadline)
        while True:
            message = self._receive(deadline)
            if message["type"] == "event":
                if len(self.pending) >= 256:
                    raise PicoWireError("Unbounded WTP event backlog")
                self.pending.append(message)
                continue
            if message["request_id"] != request_id or message["op"] != operation:
                raise PicoWireError("WTP request/operation mismatch; outcome uncertain")
            if not message["ok"]:
                raise PicoWireError("Pico WTP refusal: " + message["error"]["code"])
            return dict(message["body"])

    def next_message(self, timeout: float) -> dict[str, Any]:
        if self.pending:
            return self.pending.popleft()
        message = self._receive(time.monotonic() + timeout)
        if message["type"] != "event":
            raise PicoWireError("Unsolicited WTP response")
        return message


class ManagementClient:
    """Read-only current Control/1 over UMP1, with mandatory resource ACKs."""

    def __init__(self, stream: ByteStream, device_id: str) -> None:
        if not _identifier(device_id):
            raise PicoWireError("Explicit Pico device identity must be 32 lowercase hex characters")
        self.stream, self.device_id = stream, device_id
        self.sequence = 0
        self.hello: dict[str, Any] | None = None
        self.etags: dict[str, str] = {}

    def _exchange(self, fields: dict[str, Any], deadline: float) -> dict[str, Any]:
        request = dict(schema="usb-management/1", id=self.sequence, **fields)
        if self.hello is not None:
            request.update(session=self.hello["session"], target=self.hello["target"])
        payload = encode(request)
        if not 0 < len(payload) <= 32768:
            raise PicoWireError("UMP1 request length")
        self.stream.write(b"UMP1" + struct.pack(">I", len(payload)) + payload, deadline)
        header = read_exact(self.stream, 8, deadline)
        size = int.from_bytes(header[4:], "big")
        if header[:4] != b"UMP1" or not 0 < size <= 65536:
            raise PicoWireError("UMP1 response framing/length")
        value = loads(read_exact(self.stream, size, deadline))
        if (
            set(value)
            != {"schema", "id", "status", "session", "target", "body", "etag", "delivery_required"}
            or value["schema"] != "usb-management/1"
            or type(value["id"]) is not int
            or value["id"] != self.sequence
            or type(value["status"]) is not int
            or not 100 <= value["status"] <= 599
            or type(value["delivery_required"]) is not bool
            or not isinstance(value["body"], str)
            or not isinstance(value["etag"], str)
            or not isinstance(value["session"], str)
            or not _identifier(value["session"])
            or not isinstance(value["target"], dict)
            or set(value["target"]) != {"scope", "device_id", "boot_id"}
            or value["target"]["scope"] != "member"
            or value["target"]["device_id"] != self.device_id
            or not _identifier(value["target"]["boot_id"])
        ):
            raise PicoWireError("UMP1 closed response/identity mismatch")
        if self.hello is not None and (
            value["session"] != self.hello["session"] or value["target"] != self.hello["target"]
        ):
            raise PicoWireError("UMP1 boot/session changed")
        return value

    def connect(self) -> dict[str, Any]:
        if self.hello is not None:
            raise PicoWireError("Management session already negotiated")
        self.sequence += 1
        value = self._exchange({"operation": "hello"}, time.monotonic() + 10)
        body = loads(value["body"])
        if (
            value["status"] != 200
            or value["delivery_required"] is not False
            or set(body)
            != {"schema", "product", "firmware", "max_request_bytes", "max_response_bytes"}
            or body["schema"] != "usb-management-discovery/1"
            or body["product"] != "WsprryPico"
            or not isinstance(body["firmware"], str)
            or not body["firmware"]
            or len(body["firmware"].encode("utf-8")) > 64
            or type(body["max_request_bytes"]) is not int
            or body["max_request_bytes"] != 32768
            or type(body["max_response_bytes"]) is not int
            or body["max_response_bytes"] != 65536
        ):
            raise PicoWireError("Unsupported USB management discovery")
        self.hello = value
        return body

    def get(self, resource: str, definition: str) -> dict[str, Any]:
        if self.hello is None or self.sequence >= 2147483647:
            raise PicoWireError("Management session unavailable")
        self.sequence += 1
        deadline = time.monotonic() + 10
        response = self._exchange(
            dict(
                operation="request",
                method="GET",
                path="/api/control/v1/" + resource,
                body="",
                if_match="",
            ),
            deadline,
        )
        if response["delivery_required"] is not True:
            raise PicoWireError("Resource response omitted delivery requirement")
        ack = self._exchange({"operation": "ack"}, deadline)
        if (
            ack["status"] != 200
            or ack["delivery_required"] is not False
            or loads(ack["body"]) != {"acknowledged": True}
        ):
            raise PicoWireError("UMP1 resource ACK unconfirmed")
        if response["status"] != 200:
            raise PicoWireError("Required Pico resource unavailable: " + resource)
        value = loads(response["body"])
        if definition == "diagnostic":
            validate_diagnostic(value)
        else:
            validate_contract(value, "control", definition)
            if value.get("contract") != "Wsprry-Control/1":
                raise PicoWireError("Control resource omitted its current contract")
        self.etags[resource] = response["etag"]
        return value


def _identifier(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)
    )
