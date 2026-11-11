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


def _validate_wire_audit(
    path: Path,
    job_ids: set[str],
    target: dict[str, Any],
    time_evidence: Path | None = None,
    clock_admissions: list[dict[str, Any]] | None = None,
) -> None:
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
    armed_at: int | None = None
    owned_intervals: list[tuple[int, int]] = []
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
            if carrier == "wtp":
                frame = _frames(bytes.fromhex(record["data_hex"]), carrier, "out")
                if len(frame) != 1:
                    raise PicoWireError("WTP request write does not contain exactly one frame")
                if frame[0].get("op") == "ARM":
                    if armed_at is not None:
                        raise PicoWireError("Overlapping armed jobs in controller time evidence")
                    armed_at = int(record["monotonic_ns"])
                elif frame[0].get("op") == "RELEASE" and armed_at is not None:
                    owned_intervals.append((armed_at, int(record["monotonic_ns"])))
                    armed_at = None
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
    time_exchanges: list[tuple[dict[str, Any], dict[str, Any]]] = []
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
        if request["operation"] == "request":
            if request["path"] in {"/api/usb/v1/time/sample", "/api/usb/v1/time/submit"}:
                time_exchanges.append((request, response))
                if time_evidence is None:
                    raise PicoWireError("USB time observations lack bound host-clock evidence")
            if request["method"] != "GET" and (
                request["method"] != "POST" or request["path"] != "/api/usb/v1/time/submit"
            ):
                raise PicoWireError("Pico campaign contains a forbidden settings mutation")
        if request["operation"] == "ack" and (
            response["delivery_required"] is not False
            or loads(response["body"]) != {"acknowledged": True}
        ):
            raise PicoWireError("Management delivery ACK is unconfirmed")

    if awaiting_ack:
        raise PicoWireError("Management delivery remains unacknowledged")
    if clock_admissions is not None:
        _validate_clock_wire(
            clock_admissions, records, outgoing, responses, sent, received, job_ids
        )
    if time_evidence is not None:
        from wsprrypi_qualification.pico_time import validate_time_evidence

        if armed_at is not None or len(owned_intervals) != len(job_ids):
            raise PicoWireError("Controller time evidence lacks complete owned RF intervals")
        validate_time_evidence(time_evidence, target, time_exchanges, received[0], owned_intervals)


def _validate_clock_wire(
    evidence: list[dict[str, Any]],
    records: list[dict[str, Any]],
    outgoing: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    sent: list[dict[str, Any]],
    received: list[dict[str, Any]],
    job_ids: set[str],
) -> None:
    """Correlate actual source brackets and one owned GET_CLOCK before each ARM."""
    if len(evidence) != len(job_ids) or {e["job_id"] for e in evidence} != job_ids:
        raise PicoWireError("Clock admission coverage differs from armed jobs")
    writes = {}
    for record in records:
        if record["phase"] == "write_requested":
            for frame in _frames(bytes.fromhex(record["data_hex"]), record["carrier"], "out"):
                if record["carrier"] == "wtp":
                    key = ("wtp", frame["request_id"])
                elif frame["operation"] == "request":
                    key = ("management", frame["id"])
                else:
                    continue
                writes[key] = record
    replies = {m["request_id"]: m for m in responses}
    resources = {
        q["id"]: (q, r) for q, r in zip(sent, received, strict=True) if q["operation"] == "request"
    }
    seen: set[str] = set()
    for entry in evidence:
        request_id = entry["clock_request_id"]
        if request_id in seen:
            raise PicoWireError("Clock request reused across jobs")
        seen.add(request_id)
        position = next((i for i, q in enumerate(outgoing) if q["request_id"] == request_id), -1)
        if (
            position < 0
            or outgoing[position]["op"] != "GET_CLOCK"
            or replies[request_id]["body"] != entry["clock"]
        ):
            raise PicoWireError("Clock admission does not match audited GET_CLOCK")
        preceding_loads = [q for q in outgoing[:position] if q["op"] == "LOAD"]
        capability_reads = [q for q in outgoing[:position] if q["op"] == "CAPS"]
        if (
            not capability_reads
            or replies[capability_reads[-1]["request_id"]]["body"] != entry["capabilities"]
        ):
            raise PicoWireError("Clock limits differ from audited capabilities")
        following = next(
            (q for q in outgoing[position + 1 :] if q["op"] in {"LOAD", "ARM", "ABORT", "RELEASE"}),
            None,
        )
        if (
            not preceding_loads
            or preceding_loads[-1]["body"]["job_id"] != entry["job_id"]
            or following is None
            or following["op"] != "ARM"
            or following["body"]["job_id"] != entry["job_id"]
            or following["body"]["max_start_uncertainty_ns"]
            != entry["maximum_start_uncertainty_ns"]
        ):
            raise PicoWireError("Clock sample is outside its loaded-to-ARM job interval")
        before_id, after_id = entry["source_before_id"], entry["source_after_id"]
        for field, identity in (("source_before", before_id), ("source_after", after_id)):
            query, response = resources[identity]
            if (
                query["method"] != "GET"
                or query["path"] != "/api/control/v1/ntp-observations"
                or loads(response["body"]) != entry[field]
            ):
                raise PicoWireError("Clock source differs from audited management response")
        before = writes[("management", before_id)]
        clock = writes[("wtp", request_id)]
        after = writes[("management", after_id)]
        arm = writes[("wtp", following["request_id"])]
        if not before["sequence"] < clock["sequence"] < after["sequence"] < arm["sequence"]:
            raise PicoWireError("Clock/source collection order differs from wire evidence")
        if (
            not int(entry["started_monotonic_ns"])
            <= int(clock["monotonic_ns"])
            <= int(after["monotonic_ns"])
            <= int(entry["completed_monotonic_ns"])
            <= int(arm["monotonic_ns"])
        ):
            raise PicoWireError("Clock collection timing differs from wire evidence")


def validate_wire_audit(
    path: Path,
    job_ids: set[str],
    target: dict[str, Any],
    *,
    time_evidence: Path | None = None,
    clock_admissions: list[dict[str, Any]] | None = None,
) -> None:
    try:
        _validate_wire_audit(path, job_ids, target, time_evidence, clock_admissions)
    except (KeyError, TypeError, IndexError, OverflowError, struct.error, UnicodeError) as error:
        raise PicoWireError("Malformed Pico USB evidence") from error
