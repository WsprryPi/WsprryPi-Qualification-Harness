"""Strict wire refusals and real Pico encoder/endpoint integration (no RF)."""

import copy
import json
import os
import queue
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

from wsprrypi_qualification.pico_wire import (
    ManagementClient,
    PicoWireError,
    WtpClient,
    crc32c,
    encode,
    loads,
    read_exact,
    validate_contract,
    wtp_frame,
)
from wsprrypi_qualification.wtp_control import validate_event_job


class MemoryStream:
    def __init__(self, data=b"", fragment=3):
        self.data = bytearray(data)
        self.writes = []
        self.fragment = fragment

    def read(self, maximum, deadline):
        size = min(maximum, self.fragment)
        part = bytes(self.data[:size])
        del self.data[:size]
        return part

    def write(self, data, deadline):
        self.writes.append(data)

    def close(self):
        pass


def test_crc_independent_standard_vector():
    assert crc32c(b"123456789") == 0xE3069283


@pytest.mark.parametrize("value", [b"[]", b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'])
def test_strict_json(value):
    with pytest.raises(ValueError):
        loads(value)


def test_exact_reader_partial_disconnect_and_deadline():
    assert read_exact(MemoryStream(b"abcdef", 1), 6, time.monotonic() + 1) == b"abcdef"
    with pytest.raises(ConnectionError):
        read_exact(MemoryStream(b"abc"), 6, time.monotonic() + 1)
    with pytest.raises(TimeoutError):
        read_exact(MemoryStream(b"abcdef"), 6, time.monotonic() - 1)


def response(client, body=None):
    return dict(
        type="response",
        protocol="WTP/1",
        session_id=client.session,
        request_id="0" * 31 + "1",
        op="PING",
        ok=True,
        body=body or {},
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m.update(protocol="WTP/2"),
        lambda m: m.update(session_id="f" * 32),
        lambda m: m.update(op="STATUS"),
        lambda m: m.update(request_id="e" * 32),
        lambda m: m.update(extra=True),
        lambda m: m.update(ok=1),
    ],
)
def test_bad_response_never_replays(mutation):
    stream = MemoryStream()
    client = WtpClient(stream)
    value = response(client)
    mutation(value)
    stream.data.extend(wtp_frame(value))
    with pytest.raises(PicoWireError):
        client.request("PING", {})
    assert len(stream.writes) == 1


@pytest.mark.parametrize("kind", ["crc", "magic", "length", "encoding"])
def test_bad_frames_refuse(kind):
    stream = MemoryStream()
    client = WtpClient(stream)
    frame = bytearray(wtp_frame(response(client)))
    if kind == "crc":
        frame[-1] ^= 1
    elif kind == "magic":
        frame[0] ^= 1
    elif kind == "encoding":
        frame[5] = 2
    else:
        frame[8:12] = struct.pack(">I", 65537)
    stream.data.extend(frame)
    with pytest.raises(PicoWireError):
        client.request("PING", {})


def test_wrong_request_refuses_before_write():
    stream = MemoryStream()
    with pytest.raises(PicoWireError):
        WtpClient(stream).request("ARM", {"job_id": "a" * 32, "extra": True})
    assert not stream.writes


def ump_reply(identifier, body, *, delivery=False, status=200):
    value = dict(
        schema="usb-management/1",
        id=identifier,
        status=status,
        session="c" * 32,
        target=dict(scope="member", device_id="a" * 32, boot_id="b" * 32),
        body=json.dumps(body),
        etag="revision",
        delivery_required=delivery,
    )
    return value


def ump_frame(value):
    payload = encode(value)
    return b"UMP1" + len(payload).to_bytes(4, "big") + payload


def ump_hello():
    return ump_reply(
        1,
        dict(
            schema="usb-management-discovery/1",
            product="WsprryPico",
            firmware="fixture",
            max_request_bytes=32768,
            max_response_bytes=65536,
        ),
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.update(id=True),
        lambda v: v.update(extra=True),
        lambda v: v.update(schema="usb-management/2"),
        lambda v: v.update(session="not-a-session"),
        lambda v: v["target"].update(boot_id="bad"),
        lambda v: v["target"].update(device_id="e" * 32),
        lambda v: v["target"].update(scope="fleet"),
        lambda v: v.update(delivery_required=0),
        lambda v: v.update(status=True),
        lambda v: v.update(etag=None),
    ],
)
def test_management_malformed_discovery_never_accesses_resources(change):
    hello = ump_hello()
    change(hello)
    stream = MemoryStream(ump_frame(hello))
    with pytest.raises(PicoWireError):
        ManagementClient(stream, "a" * 32).connect()
    assert len(stream.writes) == 1


def test_management_error_response_is_acked_without_replay():
    stream = MemoryStream(
        ump_frame(ump_hello())
        + ump_frame(ump_reply(2, {"error": "absent"}, delivery=True, status=404))
        + ump_frame(ump_reply(2, {"acknowledged": True}))
    )
    client = ManagementClient(stream, "a" * 32)
    client.connect()
    with pytest.raises(PicoWireError):
        client.get("config", "config-read")
    requests = [loads(frame[8:]) for frame in stream.writes]
    assert [r["operation"] for r in requests] == ["hello", "request", "ack"]
    assert requests[1]["id"] == requests[2]["id"] == 2


@pytest.mark.parametrize("kind", ["missing", "boot", "id", "status", "body"])
def test_management_lost_or_wrong_ack_is_not_success(kind):
    ack = ump_reply(2, {"acknowledged": True})
    if kind == "boot":
        ack["target"]["boot_id"] = "d" * 32
    if kind == "id":
        ack["id"] = 3
    if kind == "status":
        ack["status"] = 409
    if kind == "body":
        ack["body"] = '{"acknowledged":false}'
    wire = ump_frame(ump_hello()) + ump_frame(
        ump_reply(2, {"contract": "Wsprry-Control/1", "config": None}, delivery=True)
    )
    stream = MemoryStream(wire + (b"" if kind == "missing" else ump_frame(ack)))
    client = ManagementClient(stream, "a" * 32)
    client.connect()
    with pytest.raises((PicoWireError, ConnectionError)):
        client.get("config", "config-read")
    assert len(stream.writes) == 3


def test_bad_decimal_and_utf8_limits_are_strict():
    value = dict(
        job_id="a" * 32,
        state="complete",
        output_active=False,
        ended_monotonic_ns="18446744073709551616",
    )
    with pytest.raises(PicoWireError):
        validate_contract(value, "wtp", "terminalRecord")
    value = dict(versions=["WTP/1"], client_name="é" * 64, client_version="1")
    with pytest.raises(PicoWireError):
        validate_contract(value, "wtp", "helloRequest")


class ProcessStream:
    """Bounded test transport for actual production driver stdin/stdout."""

    def __init__(self, executable):
        self.process = subprocess.Popen(
            [str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.incoming = queue.Queue()
        self.buffer = bytearray()

        def reader():
            while True:
                data = self.process.stdout.read(1)
                self.incoming.put(data)
                if not data:
                    return

        self.reader = threading.Thread(target=reader, daemon=True)
        self.reader.start()

    def write(self, data, deadline):
        assert time.monotonic() < deadline
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def read(self, maximum, deadline):
        try:
            return self.incoming.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty as error:
            raise TimeoutError("Production driver response") from error

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        self.reader.join(timeout=3)
        self.process.stdout.close()
        self.process.stderr.close()


@pytest.fixture
def pico_build():
    value = os.environ.get("WSPQ_PICO_TEST_BUILD")
    if value is None:
        pytest.skip("Production Pico drivers require the explicit source-pair CI build")
    return Path(value)


def test_production_usb_management_ack_closed_control(pico_build):
    stream = ProcessStream(pico_build / "usb_management_driver")
    try:
        client = ManagementClient(stream, "a" * 32)
        assert client.connect()["product"] == "WsprryPico"
        caps = client.get("capabilities", "capabilities")
        assert caps["contract"] == "Wsprry-Control/1"
        config = client.get("config", "config-read")
        assert config["config"] is None
        status = client.get("status", "status")
        assert status["job"]["output_active"] is False
        assert client.sequence == 4  # ACK repeats the resource id.
        assert client.hello["target"]["boot_id"] == "b" * 32
    finally:
        stream.close()


class EndpointStream:
    def __init__(self, executable):
        self.pipe = ProcessStream(executable)
        self.pending = bytearray()
        self.command(dict(action="connect", principal="usb-physical"))
        self.command(dict(action="clock", synchronized=True, now_ns="0"))

    def command(self, value):
        deadline = time.monotonic() + 3
        self.pipe.write(encode(value) + b"\n", deadline)
        data = bytearray()
        while not data.endswith(b"\n"):
            data.extend(self.pipe.read(1, deadline))
        response = loads(data)
        assert response["closed"] is False
        self.pending.extend(bytes.fromhex(response["hex"]))

    def write(self, data, deadline):
        self.command(dict(action="send", hex=data.hex()))

    def read(self, maximum, deadline):
        if not self.pending:
            raise TimeoutError("No production endpoint output")
        result = bytes(self.pending[:maximum])
        del self.pending[:maximum]
        return result

    def close(self):
        self.pipe.close()


def test_production_usb_wtp_claim_load_arm_abort_release(pico_build):
    stream = EndpointStream(pico_build / "endpoint_driver")
    try:
        client = WtpClient(stream)
        hello = client.request(
            "HELLO", dict(versions=["WTP/1"], client_name="harness", client_version="1")
        )
        assert hello["device_id"] == "d" * 32
        caps = client.request("CAPS", {})
        assert "tone" in caps["modes"]
        client.request("CLAIM", dict(owner_id="c" * 32, lease_ms=60000))
        job = dict(
            job_id="a" * 32,
            profile="rf-events/1",
            mode="tone",
            total_duration_ns="1000000000",
            allow_frequency_adjustment=True,
            events=[
                dict(
                    offset_ns="0",
                    duration_ns="1000000000",
                    rf_on=True,
                    frequency_nhz="14097100000000000",
                )
            ],
        )
        validate_event_job(job)
        assert client.request("LOAD", job)["state"] == "loaded"
        assert (
            client.request(
                "ARM",
                dict(
                    job_id=job["job_id"],
                    start_utc_ns="1001000000000",
                    max_start_uncertainty_ns="20000000",
                ),
            )["state"]
            == "armed"
        )
        client.request("ABORT", dict(job_id=job["job_id"]))
        status = client.request("STATUS", {})
        assert status["state"] == "aborted" and status["output_active"] is False
        client.request("RELEASE", {})
        assert client.request("STATUS", {})["owner_id"] is None
    finally:
        stream.close()


def test_vendored_contracts_match_candidate():
    source = os.environ.get("WSPQ_PICO_SOURCE")
    if source is None:
        pytest.skip("Explicit source-pair integration only")
    root = Path(__file__).resolve().parents[2]
    for name in ("control", "wtp"):
        assert (root / "schemas" / f"pico-{name}.schema.json").read_bytes() == (
            Path(source) / "engineering/protocol" / f"{name}-1.schema.json"
        ).read_bytes()


def test_production_encoder_all_modes():
    executable = os.environ.get("WSPQ_PICO_COMPILER")
    if executable is None:
        pytest.skip("Production compiler requires the explicit source-pair CI build")
    base = dict(job_id="a" * 32, frequency_nhz="14097100000000000", allow_frequency_adjustment=True)
    for mode in ("tone", "wspr", "qrss", "fskcw", "dfcw"):
        value = dict(base, mode=mode)
        if mode == "tone":
            value["duration_ns"] = "12000000000"
            value.update(cycles=3, off_ns="2000000000")
        elif mode == "wspr":
            value.update(callsign="K1ABC", locator="FN42", power_dbm=37)
        else:
            value.update(
                message="TEST",
                space_frequency_nhz="14097095000000000" if mode != "qrss" else "0",
                timing=dict(
                    dot_ns="1000000000",
                    dash_ns="3000000000",
                    intra_gap_ns="1000000000",
                    character_gap_ns="3000000000",
                    word_gap_ns="7000000000",
                ),
                repeat_count=1,
                repeat_gap_ns="0",
            )
        result = subprocess.run(
            [executable],
            input=json.dumps(value),
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        compiled = loads(result.stdout)
        assert len(compiled["source_revision"]) == 40
        assert len(compiled["jobs"]) == 1
        job = compiled["jobs"][0]
        assert job["mode"] == mode
        validate_event_job(job)
        validate_contract(job, "wtp", "job")
        invalid = copy.deepcopy(value)
        invalid["unknown"] = True
        refused = subprocess.run(
            [executable], input=json.dumps(invalid), text=True, capture_output=True, timeout=5
        )
        assert refused.returncode != 0 and not refused.stdout


@pytest.mark.parametrize("mode", ["tone", "wspr", "qrss", "fskcw", "dfcw"])
def test_production_usb_complete_encoded_job(pico_build, mode):
    from tests.unit.test_pico_campaign import configuration
    from wsprrypi_qualification.pico_campaign import compiler_input

    compiler = os.environ.get("WSPQ_PICO_COMPILER")
    assert compiler, "Production driver coverage requires the actual source-pair compiler"
    result = subprocess.run(
        [compiler],
        input=json.dumps(compiler_input(configuration(), mode)),
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    job = loads(result.stdout)["jobs"][0]
    stream = EndpointStream(pico_build / "endpoint_driver")
    try:
        client = WtpClient(stream)
        client.request("HELLO", dict(versions=["WTP/1"], client_name="harness", client_version="1"))
        client.request("CLAIM", dict(owner_id="c" * 32, lease_ms=60000))
        client.request("LOAD", job)
        client.request(
            "ARM",
            dict(
                job_id=job["job_id"],
                start_utc_ns="1001000000000",
                max_start_uncertainty_ns="20000000",
            ),
        )
        stream.command(dict(action="clock", synchronized=True, now_ns="1000000000"))
        assert client.request("STATUS", {})["state"] == "running"
        # Renew while advancing the fixture's monotonic clock, never sleeping
        # for waveform edges or simulating a different JobService.
        end = 1_000_000_000 + int(job["total_duration_ns"])
        for now in range(21_000_000_000, end, 20_000_000_000):
            stream.command(dict(action="clock", synchronized=True, now_ns=str(now)))
            client.request("RENEW", dict(owner_id="c" * 32, lease_ms=60000))
        stream.command(dict(action="clock", synchronized=True, now_ns=str(end)))
        status = client.request("STATUS", {})
        assert status["state"] == "complete" and status["output_active"] is False
        assert status["terminal_records"][-1]["job_id"] == job["job_id"]
        client.request("RELEASE", {})
        assert client.request("STATUS", {})["owner_id"] is None
    finally:
        stream.close()


def test_production_wspr_matches_independent_wsjtx_oracle():
    source, compiler = os.environ.get("WSPQ_PICO_SOURCE"), os.environ.get("WSPQ_PICO_COMPILER")
    if source is None or compiler is None:
        pytest.skip("Explicit source-pair compiler required")
    oracle = loads((Path(source) / "tests/wspr_oracle_vectors.json").read_bytes())
    vector = next(v for v in oracle["vectors"] if v["type"] == 2 and v["accepted"])
    value = dict(
        mode="wspr",
        job_id="a" * 32,
        frequency_nhz="14097100000000000",
        callsign=vector["call"],
        locator=vector["locator"],
        power_dbm=vector["power"],
        allow_frequency_adjustment=True,
    )
    result = subprocess.run(
        [compiler], input=json.dumps(value), capture_output=True, text=True, check=True, timeout=5
    )
    events = loads(result.stdout)["jobs"][0]["events"]
    assert (
        "".join(
            str((int(e["frequency_nhz"]) - int(value["frequency_nhz"])) // 1464843750)
            for e in events
        )
        == vector["symbols"]
    )
    for index, event in enumerate(events):

        def boundary(n):
            return (n * 8192 * 1_000_000_000 + 6000) // 12000

        assert int(event["offset_ns"]) == boundary(index)
        assert int(event["duration_ns"]) == boundary(index + 1) - boundary(index)


def test_usb_audit_fault_never_blocks_cleanup(tmp_path, monkeypatch):
    import wsprrypi_qualification.pico_audit as module

    audit = module.WireAudit(tmp_path / "wire.jsonl")
    stream = MemoryStream()
    wire = module.AuditedStream(stream, audit, "wtp")

    def fail(_fd):
        raise OSError("injected full evidence disk")

    monkeypatch.setattr(module.os, "fsync", fail)
    with pytest.raises(PicoWireError, match="cleanup remains permitted"):
        wire.write(b"must not reach device", time.monotonic() + 1)
    assert stream.writes == []
    audit.cleaning_up = True
    wire.write(b"owned cleanup remains permitted", time.monotonic() + 1)
    assert stream.writes == [b"owned cleanup remains permitted"]
    with pytest.raises(PicoWireError, match="could not be sealed"):
        audit.close()


@pytest.mark.parametrize("lost", ["LOAD", "ARM"])
def test_production_usb_lost_mutation_reply_aborts_owned_job_without_replay(pico_build, lost):
    from tests.unit.test_pico_campaign import make_plan
    from wsprrypi_qualification.wtp_control import WtpControlError, run_transaction

    stream = EndpointStream(pico_build / "endpoint_driver")

    class LostReply(WtpClient):
        def __init__(self):
            super().__init__(stream)
            self.calls = []

        def request(self, operation, body, timeout=8):
            self.calls.append(operation)
            result = super().request(operation, body, timeout)
            if operation == lost:
                raise TimeoutError("Injected reply loss after production acceptance")
            return result

    client = LostReply()

    def inactive():
        status = client.request("STATUS", {})
        assert status["output_active"] is False
        return status

    try:
        with pytest.raises(WtpControlError) as caught:
            run_transaction(
                client,
                make_plan()["modes"][0]["jobs"][0],
                device_id="d" * 32,
                start_utc_ns=1_001_000_000_000,
                wall_ns=lambda: 1_000_000_000_000,
                before_arm=lambda: {},
                receiver_ready=lambda: None,
                verify_inactive=inactive,
            )
        assert caught.value.evidence["cleanup_verified"]
        assert caught.value.evidence["released_status"]["owner_id"] is None
        assert client.calls.count(lost) == 1
        assert client.calls.count("ABORT") == 1
        assert client.calls.count("ARM") == (1 if lost == "ARM" else 0)
    finally:
        stream.close()


def test_production_usb_audit_reconstructs_fragmented_management_and_wtp(pico_build, tmp_path):
    from wsprrypi_qualification.pico_audit import AuditedStream, WireAudit, validate_wire_audit

    audit = WireAudit(tmp_path / "wire.jsonl")
    management_stream = ProcessStream(pico_build / "usb_management_driver")
    wtp_stream = EndpointStream(pico_build / "endpoint_driver")
    try:
        management = ManagementClient(
            AuditedStream(management_stream, audit, "management"), "a" * 32
        )
        management.connect()
        management.get("status", "status")
        wtp = WtpClient(AuditedStream(wtp_stream, audit, "wtp"))
        wtp.request("HELLO", dict(versions=["WTP/1"], client_name="harness", client_version="1"))
        wtp.request("PING", {})
    finally:
        management_stream.close()
        wtp_stream.close()
        audit.close()
    validate_wire_audit(tmp_path / "wire.jsonl", set(), management.hello["target"])
    lines = (tmp_path / "wire.jsonl").read_text().splitlines()
    lines.insert(1, lines[-1])
    # Sequence is also checked; an early/reordered seal is never accepted.
    (tmp_path / "wire.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(PicoWireError):
        validate_wire_audit(tmp_path / "wire.jsonl", set(), management.hello["target"])
