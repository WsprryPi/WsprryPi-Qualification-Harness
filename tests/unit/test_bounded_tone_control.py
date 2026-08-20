import base64
import hashlib
import json
import socket
import struct
import threading
import time

import pytest

from wsprrypi_qualification.bounded_tone_control import (
    BoundedToneControlError,
    BoundedToneEndpoint,
    run_bounded_tone_transaction,
)


def _read_headers(connection: socket.socket) -> bytes:
    data = bytearray()
    while not data.endswith(b"\r\n\r\n"):
        data.extend(connection.recv(1))
    return bytes(data)


def _upgrade(connection: socket.socket) -> None:
    request = _read_headers(connection).decode("ascii")
    key = next(
        line.split(":", 1)[1].strip()
        for line in request.splitlines()
        if line.lower().startswith("sec-websocket-key:")
    )
    accept = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()
    connection.sendall(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Accept: " + accept + "\r\n\r\n"
        ).encode()
    )


def _receive_client_json(connection: socket.socket) -> dict:
    first, second = connection.recv(2)
    assert first == 0x81 and second & 0x80
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", connection.recv(2))[0]
    mask = connection.recv(4)
    payload = bytearray()
    while len(payload) < length:
        payload.extend(connection.recv(length - len(payload)))
    return json.loads(bytes(value ^ mask[index % 4] for index, value in enumerate(payload)))


def _send_json(connection: socket.socket, document: dict, *, masked: bool = False) -> None:
    payload = json.dumps(document).encode()
    mask_bit = 0x80 if masked else 0
    header = (
        bytes((0x81, mask_bit | len(payload)))
        if len(payload) < 126
        else bytes((0x81, mask_bit | 126)) + struct.pack("!H", len(payload))
    )
    connection.sendall(header + (b"abcd" if masked else b"") + payload)


class FakeServer:
    def __init__(self, behavior: str = "success") -> None:
        self.listener = socket.socket()
        for candidate in range(39000, 40000):
            try:
                self.listener.bind(("127.0.0.1", candidate))
                break
            except OSError:
                continue
        else:
            raise RuntimeError("no test WebSocket port is available")
        self.listener.listen(2)
        self.port = self.listener.getsockname()[1]
        self.behavior = behavior
        self.requests: list[dict] = []
        self.thread = threading.Thread(target=self._run)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.listener.close()
        self.thread.join(timeout=2)
        assert not self.thread.is_alive()

    def _run(self) -> None:
        connection, _ = self.listener.accept()
        with connection:
            _upgrade(connection)
            request = _receive_client_json(connection)
            self.requests.append(request)
            if self.behavior == "disconnect":
                return
            if self.behavior == "delay":
                time.sleep(0.2)
                return
            if self.behavior == "malformed":
                connection.sendall(b"\x81\x01{")
            elif self.behavior == "oversized":
                connection.sendall(b"\x81\x7e" + struct.pack("!H", 20000))
            elif self.behavior == "rejected":
                _send_json(
                    connection,
                    {
                        "command": "bounded_tone",
                        "request_id": request["request_id"],
                        "duration_ms": request["duration_ms"],
                        "status": "error",
                        "started": False,
                    },
                )
            else:
                _send_json(
                    connection,
                    {
                        "command": "bounded_tone",
                        "request_id": request["request_id"],
                        "duration_ms": request["duration_ms"],
                        "status": "ok",
                        "started": True,
                    },
                )
                terminal_id = "wrong" if self.behavior == "wrong_id" else request["request_id"]
                _send_json(
                    connection,
                    {
                        "command": "bounded_tone",
                        "event": "completed",
                        "request_id": terminal_id,
                        "status": "ok",
                        "stopped": True,
                        "scheduler_restored": True,
                    },
                    masked=self.behavior == "masked",
                )
        if self.behavior in {"wrong_id", "masked", "malformed", "oversized", "rejected"}:
            cleanup, _ = self.listener.accept()
            with cleanup:
                _upgrade(cleanup)
                self.requests.append(_receive_client_json(cleanup))
                _send_json(cleanup, {"command": "tone_end", "status": "ok"})


def _run(server: FakeServer, timeout: float = 1.0) -> dict:
    return run_bounded_tone_transaction(
        BoundedToneEndpoint("127.0.0.1", server.port),
        request_id="phase7-001",
        frequency_hz=14_097_100,
        duration_ms=20,
        outer_timeout_s=timeout,
    )


def test_success_is_correlated_and_non_qualifying() -> None:
    with FakeServer() as server:
        evidence = _run(server)
    assert evidence["completed"] is True
    assert evidence["qualification_claim"] is False
    assert server.requests[0]["command"] == "bounded_tone"


@pytest.mark.parametrize("behavior", ["wrong_id", "masked", "malformed", "oversized", "rejected"])
def test_invalid_terminal_fails_closed_and_attempts_cleanup(behavior: str) -> None:
    with FakeServer(behavior) as server, pytest.raises(BoundedToneControlError):
        _run(server)
    assert server.requests[-1] == {"command": "tone_end"}


@pytest.mark.parametrize("behavior", ["disconnect", "delay"])
def test_missing_start_or_deadline_fails_closed(behavior: str) -> None:
    with FakeServer(behavior) as server, pytest.raises(BoundedToneControlError):
        _run(server, 0.05)


def test_endpoint_and_request_bounds_fail_before_network() -> None:
    with pytest.raises(ValueError, match="literal loopback"):
        BoundedToneEndpoint("localhost", 31416)
    with pytest.raises(ValueError, match="duration"):
        run_bounded_tone_transaction(
            BoundedToneEndpoint("127.0.0.1", 31416),
            request_id="x",
            frequency_hz=1,
            duration_ms=60001,
            outer_timeout_s=70,
        )
