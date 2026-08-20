"""Loopback-only RFC 6455 mediation for WsprryPi bounded Tone transactions."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time
from dataclasses import dataclass
from typing import Any


class BoundedToneControlError(RuntimeError):
    """The bounded Tone transaction failed closed."""


@dataclass(frozen=True)
class BoundedToneEndpoint:
    host: str
    port: int
    path: str = "/"
    maximum_frame_bytes: int = 16_384

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "::1"}:
            raise ValueError("bounded Tone endpoint must be a literal loopback address")
        if not 1024 <= self.port <= 49151:
            raise ValueError("bounded Tone endpoint port is outside the allowed range")
        if not self.path.startswith("/") or any(c in self.path for c in "\r\n"):
            raise ValueError("bounded Tone endpoint path is invalid")
        if not 128 <= self.maximum_frame_bytes <= 1_048_576:
            raise ValueError("bounded Tone frame bound is invalid")


class LoopbackWebSocket:
    """Small synchronous WebSocket client with one absolute transaction budget."""

    def __init__(self, endpoint: BoundedToneEndpoint, deadline: float) -> None:
        self.endpoint = endpoint
        self.deadline = deadline
        self.sock: socket.socket | None = None

    def __enter__(self) -> LoopbackWebSocket:
        remaining = self._remaining()
        self.sock = socket.create_connection(
            (self.endpoint.host, self.endpoint.port), timeout=remaining
        )
        self.sock.settimeout(remaining)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        host = (
            f"[{self.endpoint.host}]:{self.endpoint.port}"
            if ":" in self.endpoint.host
            else f"{self.endpoint.host}:{self.endpoint.port}"
        )
        request = (
            f"GET {self.endpoint.path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self.sock.sendall(request)
        response = self._read_headers()
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode("ascii")
        lines = response.decode("latin-1").split("\r\n")
        headers = {
            name.strip().lower(): value.strip()
            for line in lines[1:]
            if ":" in line
            for name, value in [line.split(":", 1)]
        }
        if (
            not lines
            or lines[0] != "HTTP/1.1 101 Switching Protocols"
            or headers.get("upgrade", "").lower() != "websocket"
            or "upgrade" not in headers.get("connection", "").lower()
            or headers.get("sec-websocket-accept") != expected
        ):
            raise BoundedToneControlError("WebSocket upgrade response is invalid")
        return self

    def __exit__(self, *_: object) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def send_json(self, document: dict[str, object]) -> None:
        payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        if len(payload) > self.endpoint.maximum_frame_bytes:
            raise BoundedToneControlError("outbound WebSocket frame exceeds its bound")
        mask = os.urandom(4)
        if len(payload) < 126:
            header = bytes((0x81, 0x80 | len(payload)))
        else:
            header = bytes((0x81, 0x80 | 126)) + struct.pack("!H", len(payload))
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self._socket().settimeout(self._remaining())
        self._socket().sendall(header + mask + masked)

    def receive_json(self) -> dict[str, Any]:
        first, second = self._read_exact(2)
        if first != 0x81 or second & 0x80:
            raise BoundedToneControlError("server WebSocket frame contract is invalid")
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        if length > self.endpoint.maximum_frame_bytes:
            raise BoundedToneControlError("server WebSocket frame exceeds its bound")
        try:
            document = json.loads(self._read_exact(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BoundedToneControlError("server WebSocket payload is not JSON") from exc
        if not isinstance(document, dict):
            raise BoundedToneControlError("server WebSocket JSON must be an object")
        return document

    def _read_headers(self) -> bytes:
        data = bytearray()
        while not data.endswith(b"\r\n\r\n"):
            if len(data) >= 8192:
                raise BoundedToneControlError("WebSocket upgrade headers exceed their bound")
            data.extend(self._read_exact(1))
        return bytes(data)

    def _read_exact(self, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            self._socket().settimeout(self._remaining())
            try:
                chunk = self._socket().recv(count - len(data))
            except TimeoutError as exc:
                raise BoundedToneControlError("bounded Tone outer deadline expired") from exc
            if not chunk:
                raise BoundedToneControlError("WebSocket peer disconnected")
            data.extend(chunk)
        return bytes(data)

    def _remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BoundedToneControlError("bounded Tone outer deadline expired")
        return remaining

    def _socket(self) -> socket.socket:
        if self.sock is None:
            raise BoundedToneControlError("WebSocket is not connected")
        return self.sock


def run_bounded_tone_transaction(
    endpoint: BoundedToneEndpoint,
    *,
    request_id: str,
    frequency_hz: int,
    duration_ms: int,
    outer_timeout_s: float,
) -> dict[str, object]:
    """Run one product-bounded transaction and return non-qualifying evidence."""
    if (
        not request_id
        or len(request_id) > 128
        or not all(
            character.isascii() and (character.isalnum() or character in "-_.")
            for character in request_id
        )
    ):
        raise ValueError("request ID is invalid")
    if isinstance(frequency_hz, bool) or frequency_hz <= 0:
        raise ValueError("frequency must be a positive integer")
    if isinstance(duration_ms, bool) or not 1 <= duration_ms <= 60_000:
        raise ValueError("duration must be between 1 and 60000 milliseconds")
    if outer_timeout_s <= duration_ms / 1000:
        raise ValueError("outer timeout must exceed the product duration")
    outer_deadline = time.monotonic() + outer_timeout_s
    cleanup_reserve_s = min(1.0, (outer_timeout_s - duration_ms / 1000) / 2)
    transaction_deadline = outer_deadline - cleanup_reserve_s
    cleanup_attempted = False
    request_sent = False
    start_response: dict[str, Any] | None = None
    terminal_response: dict[str, Any] | None = None
    try:
        with LoopbackWebSocket(endpoint, transaction_deadline) as websocket:
            websocket.send_json(
                {
                    "command": "bounded_tone",
                    "request_id": request_id,
                    "duration_ms": duration_ms,
                    "frequency_source": "custom_rf",
                    "frequency_hz": frequency_hz,
                }
            )
            request_sent = True
            start_response = websocket.receive_json()
            if (
                start_response.get("command") != "bounded_tone"
                or start_response.get("request_id") != request_id
                or start_response.get("status") != "ok"
                or start_response.get("started") is not True
                or start_response.get("duration_ms") != duration_ms
            ):
                raise BoundedToneControlError("bounded Tone start was not acknowledged")
            terminal_response = websocket.receive_json()
            if (
                terminal_response.get("command") != "bounded_tone"
                or terminal_response.get("event") != "completed"
                or terminal_response.get("request_id") != request_id
                or terminal_response.get("status") != "ok"
                or terminal_response.get("stopped") is not True
                or terminal_response.get("scheduler_restored") is not True
            ):
                raise BoundedToneControlError("bounded Tone terminal evidence is invalid")
    except Exception:
        if request_sent:
            cleanup_attempted = True
            try:
                with LoopbackWebSocket(endpoint, outer_deadline) as cleanup:
                    cleanup.send_json({"command": "tone_end"})
                    cleanup.receive_json()
            except Exception:
                pass
        raise
    return {
        "schema_version": 1,
        "evidence_type": "bounded_tone_control",
        "request_id": request_id,
        "frequency_hz": frequency_hz,
        "duration_ms": duration_ms,
        "outer_timeout_s": outer_timeout_s,
        "loopback_host": endpoint.host,
        "port": endpoint.port,
        "start_response": start_response,
        "terminal_response": terminal_response,
        "cleanup_attempted": cleanup_attempted,
        "completed": True,
        "qualification_claim": False,
    }
