"""Private bounded USB byte evidence; evidence failures never prevent cleanup."""

from __future__ import annotations

import json
import os
import struct
import threading
import time
from pathlib import Path
from typing import Any

from wsprrypi_qualification.pico_wire import (
    ByteStream,
    PicoWireError,
    crc32c,
    loads,
    validate_contract,
)


class WireAudit:
    def __init__(self, path: Path) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.file = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        self.lock = threading.Lock()
        self.sequence = 0
        self.error: str | None = None
        self.cleaning_up = False

    def record(
        self, carrier: str, phase: str, io_id: int, data: bytes = b"", error: str | None = None
    ) -> None:
        with self.lock:
            self.sequence += 1
            value = dict(
                schema_version=1,
                sequence=self.sequence,
                monotonic_ns=str(time.monotonic_ns()),
                carrier=carrier,
                phase=phase,
                io_id=io_id,
                data_hex=data.hex(),
                error=error,
            )
            try:
                self.file.write(json.dumps(value, separators=(",", ":")) + "\n")
                self.file.flush()
                os.fsync(self.file.fileno())
            except OSError as caught:
                self.error = str(caught)
        if self.error is not None and not self.cleaning_up:
            raise PicoWireError(
                "Pico wire evidence unavailable; cleanup remains permitted: " + self.error
            )

    def close(self) -> None:
        self.cleaning_up = True
        try:
            self.record("management", "sealed", 0)
        finally:
            self.file.close()
        if self.error is not None:
            raise PicoWireError("Pico wire evidence could not be sealed: " + self.error)


class AuditedStream:
    def __init__(self, stream: ByteStream, audit: WireAudit, carrier: str) -> None:
        self.stream, self.audit, self.carrier = stream, audit, carrier
        self.io_id = 0

    def read(self, maximum: int, deadline: float) -> bytes:
        self.io_id += 1
        try:
            data = self.stream.read(maximum, deadline)
        except BaseException as error:
            self.audit.record(
                self.carrier,
                "read_timeout" if isinstance(error, TimeoutError) else "read_failed",
                self.io_id,
                error=type(error).__name__,
            )
            raise
        self.audit.record(self.carrier, "read", self.io_id, data)
        return data

    def write(self, data: bytes, deadline: float) -> None:
        self.io_id += 1
        self.audit.record(self.carrier, "write_requested", self.io_id, data)
        try:
            self.stream.write(data, deadline)
        except BaseException as error:
            self.audit.record(self.carrier, "write_failed", self.io_id, error=type(error).__name__)
            raise
        self.audit.record(self.carrier, "write_complete", self.io_id)

    def close(self) -> None:
        self.stream.close()


def _frames(data: bytes, carrier: str, direction: str) -> list[dict[str, Any]]:
    result = []
    while data:
        header_size = 16 if carrier == "wtp" else 8
        if len(data) < header_size:
            raise PicoWireError("Truncated USB evidence frame")
        if carrier == "wtp":
            if data[:8] != b"WTPF\x01\x01\x00\x00":
                raise PicoWireError("Invalid WTP evidence framing")
            count, checksum = struct.unpack(">II", data[8:16])
        else:
            if data[:4] != b"UMP1":
                raise PicoWireError("Invalid management evidence framing")
            count = int.from_bytes(data[4:8], "big")
        limit = 32768 if carrier == "management" and direction == "out" else 65536
        if not 0 < count <= limit or len(data) < header_size + count:
            raise PicoWireError("Invalid USB evidence length")
        payload, data = data[header_size : header_size + count], data[header_size + count :]
        if carrier == "wtp" and crc32c(payload) != checksum:
            raise PicoWireError("USB evidence CRC mismatch")
        value = loads(payload)
        if carrier == "wtp":
            validate_contract(value, "wtp")
        result.append(value)
    return result


def _validate_wire_audit(path: Path, job_ids: set[str], target: dict[str, Any]) -> None:
    raw = path.read_bytes()
    if not raw.endswith(b"\n"):
        raise PicoWireError("USB evidence is unsealed or truncated")
    records = [loads(line) for line in raw.splitlines()]
    if not records or records[-1]["phase"] != "sealed":
        raise PicoWireError("USB evidence lacks its final seal")
    streams = {(c, d): bytearray() for c in ("wtp", "management") for d in ("in", "out")}
    pending: set[tuple[str, int]] = set()
    seen_io: set[tuple[str, int]] = set()
    previous_ns = 0
    for index, record in enumerate(records, 1):
        validate_contract(record, "usb-io")
        if (
            set(record)
            != {
                "schema_version",
                "sequence",
                "monotonic_ns",
                "carrier",
                "phase",
                "io_id",
                "data_hex",
                "error",
            }
            or type(record["schema_version"]) is not int
            or record["schema_version"] != 1
            or record["sequence"] != index
        ):
            raise PicoWireError("USB evidence shape or sequence differs")
        carrier, phase = record["carrier"], record["phase"]
        if carrier not in {"wtp", "management"} or int(record["monotonic_ns"]) < previous_ns:
            raise PicoWireError("USB evidence carrier or time ordering differs")
        previous_ns = int(record["monotonic_ns"])
        key = (carrier, record["io_id"])
        if phase == "sealed" and index != len(records):
            raise PicoWireError("USB evidence contains an early seal")
        if phase in {"write_requested", "read", "read_timeout"}:
            if key in seen_io:
                raise PicoWireError("USB IO identity reused")
            seen_io.add(key)
        if phase == "write_requested":
            if key in pending:
                raise PicoWireError("USB write identity reused")
            pending.add(key)
            streams[(carrier, "out")].extend(bytes.fromhex(record["data_hex"]))
        elif phase == "write_complete":
            if key not in pending:
                raise PicoWireError("USB write completion has no matching request")
            pending.remove(key)
        elif phase == "read":
            streams[(carrier, "in")].extend(bytes.fromhex(record["data_hex"]))
        elif phase not in {"read_timeout", "sealed"}:
            raise PicoWireError("Passing campaign contains failed USB IO")
    if pending:
        raise PicoWireError("USB evidence contains ambiguous writes")
    outgoing = _frames(bytes(streams[("wtp", "out")]), "wtp", "out")
    incoming = _frames(bytes(streams[("wtp", "in")]), "wtp", "in")
    if any(m["type"] != "request" for m in outgoing):
        raise PicoWireError("Outgoing WTP is not a request")
    requests = {m["request_id"]: m for m in outgoing}
    if (
        len(requests) != len(outgoing)
        or not outgoing
        or len({m["session_id"] for m in outgoing + incoming}) != 1
    ):
        raise PicoWireError("WTP evidence request/session identity differs")
    responses = [m for m in incoming if m["type"] == "response"]
    if len(responses) != len(requests) or {m["request_id"] for m in responses} != set(requests):
        raise PicoWireError("WTP response coverage is incomplete or duplicated")
    for message in responses:
        if not message["ok"] or message["op"] != requests[message["request_id"]]["op"]:
            raise PicoWireError("WTP response refuses or mismatches its request")
    for operation in ("LOAD", "ARM"):
        ids = [m["body"]["job_id"] for m in outgoing if m["op"] == operation]
        if set(ids) != job_ids or len(ids) != len(job_ids):
            raise PicoWireError("USB audit contains missing, repeated, or unrecorded RF jobs")
    sent = _frames(bytes(streams[("management", "out")]), "management", "out")
    received = _frames(bytes(streams[("management", "in")]), "management", "in")
    if len(sent) != len(received) or not sent:
        raise PicoWireError("Management response/ACK coverage is incomplete")
    session = received[0]["session"]
    awaiting_ack = False
    for index, (request, response) in enumerate(zip(sent, received, strict=True)):
        operation = request["operation"]
        if operation != ("hello" if index == 0 else "ack" if awaiting_ack else "request"):
            raise PicoWireError("Management delivery sequence differs")
        if operation == "request":
            if response["delivery_required"] is not True:
                raise PicoWireError("Management response lacks delivery confirmation")
            awaiting_ack = True
        else:
            awaiting_ack = False
        if (
            response["id"] != request["id"]
            or response["target"] != target
            or response["session"] != session
            or response["status"] != 200
        ):
            raise PicoWireError("Management response binding differs")
        if request["operation"] == "request" and request["method"] != "GET":
            raise PicoWireError("Read-only Pico campaign contains a management mutation")
        if request["operation"] == "ack" and (
            response["delivery_required"] is not False
            or loads(response["body"]) != {"acknowledged": True}
        ):
            raise PicoWireError("Management delivery ACK is unconfirmed")

    if awaiting_ack:
        raise PicoWireError("Management delivery remains unacknowledged")


def validate_wire_audit(path: Path, job_ids: set[str], target: dict[str, Any]) -> None:
    try:
        _validate_wire_audit(path, job_ids, target)
    except (KeyError, TypeError, IndexError, OverflowError, struct.error, UnicodeError) as error:
        raise PicoWireError("Malformed Pico USB evidence") from error
