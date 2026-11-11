"""Controller UTC continuity, native protocol, and retained-evidence refusals."""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.test_pico_campaign import make_snapshot
from tests.unit.test_pico_wire import (
    MemoryStream,
    ProcessStream,
    ump_frame,
    ump_hello,
    ump_reply,
)
from wsprrypi_qualification import pico_live, pico_wire
from wsprrypi_qualification.pico_live import PicoLiveAdapter
from wsprrypi_qualification.pico_time import (
    ControllerTimeRefresh,
    clock_error,
    submission,
    validate_time_evidence,
)
from wsprrypi_qualification.pico_wire import ManagementClient, PicoWireError, WtpClient, loads

EPOCH = 1786449600000000000


def test_clock_journal_close_failure_does_not_skip_usb_cleanup(tmp_path, monkeypatch):
    closed = []

    class Audit:
        def __init__(self, path):
            pass

        def close(self):
            closed.append("audit")

    class Usb:
        management = object()

        def __init__(self, *args):
            pass

        def close(self):
            closed.append("usb")

    class Refresh:
        def __init__(self, *args):
            pass

        def close(self):
            raise OSError("journal close failed")

    def fail_pool(**kwargs):
        raise RuntimeError("pool failed")

    helper = tmp_path / "capture"
    helper.write_bytes(b"fixture")
    monkeypatch.setattr(pico_live, "WireAudit", Audit)
    monkeypatch.setattr(pico_live, "PicoUsb", Usb)
    monkeypatch.setattr(pico_live, "AuditedStream", lambda *args: None)
    monkeypatch.setattr(
        pico_live, "ManagementClient", lambda *args, **kwargs: SimpleNamespace(connect=lambda: {})
    )
    monkeypatch.setattr(pico_live, "ControllerTimeRefresh", Refresh)
    monkeypatch.setattr(pico_live, "ChronyClock", lambda: None)
    monkeypatch.setattr(pico_live.concurrent.futures, "ThreadPoolExecutor", fail_pool)
    plan = dict(
        configuration=dict(
            receiver=dict(capture_helper=str(helper)), pico=dict(device_id="a" * 32)
        ),
        modes=[dict(mode="tone")],
        controller_time_refresh=pico_live.TIME_POLICY,
    )
    with pytest.raises(pico_live.PicoCleanupError, match=r"pool failed.*journal close failed"):
        PicoLiveAdapter(plan, tmp_path, None, None)
    assert closed == ["usb", "audit"]


class Clock:
    def __init__(self):
        self.ns = 0

    def monotonic(self):
        return self.ns / 1e9

    def time_ns(self):
        return EPOCH + self.ns

    def advance(self, seconds):
        self.ns += round(seconds * 1e9)


def observation(clock, *, leap="Normal"):
    ns = round(clock.monotonic() * 1e9)
    utc = clock.time_ns()
    csv = f"50505300,PPS,1,{utc / 1e9 - 10},0.000001,0,0,1,0,0.1,0.002,0.001,16,{leap}"
    value = dict(monotonic_ns=ns, utc_ns=utc, collection_ns=1_000_000, tracking_csv=csv)
    value["uncertainty_ns"] = clock_error(csv, value["collection_ns"], utc)
    return value


class Host:
    def __init__(self, clock, fail_after=None):
        self.clock, self.fail_after = clock, fail_after
        self.identity = dict(
            path="/qualified/chronyc", size_bytes=1, sha256="a" * 64, version="fixture"
        )

    def observe(self):
        if self.fail_after is not None and self.clock.monotonic() >= self.fail_after:
            raise PicoWireError("Host UTC lost qualification")
        self.clock.advance(0.001)
        return observation(self.clock)


class Management:
    def __init__(self, clock):
        self.clock = clock
        self.sequence = 1
        self.hello = ump_hello()
        self.exchanges = []
        self.last = None
        self.peer = None
        self.running_samples = 0

    def get(self, resource, _definition):
        self.sequence += 1
        s = make_snapshot()
        if self.peer is not None:
            s["status"]["job"] = self.peer.request("STATUS", {})
            for field in ("owner_id", "job_id", "state", "boot_id", "output_active"):
                s["status"]["standalone"][field] = s["status"]["job"][field]
            self.running_samples += s["status"]["job"]["state"] == "running"
        if resource == "status":
            return s[resource]
        return s[resource]

    def time_sample(self, deadline, observed):
        self.sequence += 1
        value = dict(sample=str(self.sequence), monotonic_ns=str(self.clock.ns))
        request = dict(
            id=self.sequence, method="GET", path="/api/usb/v1/time/sample", body="", if_match=""
        )
        self.exchanges.append((request, dict(body=json.dumps(value))))
        self.clock.advance(0.002)
        observed()
        self.clock.advance(0.002)
        return value

    def time_submit(self, value, deadline):
        self.sequence += 1
        self.clock.advance(0.002)
        self.last = self.clock.monotonic()
        response = dict(
            accepted=True,
            selected=True,
            source="controller",
            uncertainty_ns=value["uncertainty_ns"],
            age_ns="0",
        )
        self.exchanges.append(
            (
                dict(
                    id=self.sequence,
                    method="POST",
                    path="/api/usb/v1/time/submit",
                    body=json.dumps(value),
                    if_match="",
                ),
                dict(body=json.dumps(response)),
            )
        )
        return response


def refresh_fixture(tmp_path):
    clock = Clock()
    client = Management(clock)
    refresh = ControllerTimeRefresh(
        client, tmp_path / "time.jsonl", Host(clock), monotonic=clock.monotonic
    )
    return clock, client, refresh


def test_expired_initial_clock_and_multiple_lifetimes_refresh_without_real_waits(tmp_path):
    clock, client, refresh = refresh_fixture(tmp_path)
    clock.advance(215)  # Regression: setup's single observation had expired before admission.
    refresh.refresh(owned_job=False)
    for _ in range(400):
        clock.advance(1)
        refresh.refresh(owned_job=True)
        assert clock.monotonic() - client.last < 31
    refresh.close()
    assert refresh.sequence >= 14
    validate_time_evidence(
        tmp_path / "time.jsonl",
        client.hello["target"],
        client.exchanges,
        client.hello,
        [(216_000_000_000, clock.ns)],
    )


def test_missed_refresh_fails_owned_job_but_idle_analysis_can_reacquire(tmp_path):
    clock, _client, refresh = refresh_fixture(tmp_path)
    refresh.refresh(owned_job=False)
    clock.advance(91)
    with pytest.raises(PicoWireError, match="lifetime"):
        refresh.refresh(owned_job=True)
    refresh.refresh(owned_job=False)
    refresh.close()
    assert refresh.sequence == 2


@pytest.mark.parametrize(
    "kind", ["step", "regression", "late", "uncertainty", "leap", "local", "future", "nan"]
)
def test_host_or_interval_failure_refuses_submission(kind):
    clock = Clock()
    before = observation(clock)
    clock.advance(0.01)
    after = observation(clock)
    clock.advance(0.01)
    ack = observation(clock)
    if kind == "step":
        ack["utc_ns"] += 2_000_000
    elif kind == "regression":
        after["monotonic_ns"] = -1
    elif kind == "late":
        ack["monotonic_ns"] += 2_000_000_000
    elif kind == "uncertainty":
        before["uncertainty_ns"] = 0
    else:
        parts = before["tracking_csv"].split(",")
        if kind == "leap":
            parts[13] = "Not synchronised"
        if kind == "local":
            parts[0] = "7F7F0101"
        if kind == "future":
            parts[3] = str(before["utc_ns"] / 1e9 + 100)
        if kind == "nan":
            parts[11] = "nan"
        before["tracking_csv"] = ",".join(parts)
    with pytest.raises(PicoWireError):
        submission(dict(sample="1", monotonic_ns="0"), before, after, ack)


def test_asymmetric_interval_and_delayed_ack_do_not_recenter_sample():
    clock = Clock()
    before = observation(clock)
    clock.advance(0.1)  # Entire sample RTT may be in either direction.
    after = observation(clock)
    clock.advance(0.4)  # ACK delay is checked, not added to the sample midpoint.
    ack = observation(clock)
    result = submission(dict(sample="1", monotonic_ns="0"), before, after, ack)
    assert int(result["utc_ns"]) == EPOCH + 50_000_000
    assert int(result["uncertainty_ns"]) == before["uncertainty_ns"] + 50_000_000 + 100_000


@pytest.mark.parametrize("source", ["controller", "sntp", "gps"])
def test_wire_time_exchange_preserves_source_priority(source, monkeypatch, tmp_path):
    clock = Clock()
    monkeypatch.setattr(pico_wire, "time", clock)
    response = dict(
        accepted=source == "controller",
        selected=source == "controller",
        source=source,
        uncertainty_ns="100",
        age_ns="0",
    )
    stream = MemoryStream(
        ump_frame(ump_hello())
        + ump_frame(ump_reply(2, dict(sample="42", monotonic_ns="0"), delivery=True))
        + ump_frame(ump_reply(2, dict(acknowledged=True)))
        + ump_frame(ump_reply(3, response, delivery=True))
        + ump_frame(ump_reply(3, dict(acknowledged=True)))
    )
    client = ManagementClient(stream, "a" * 32)
    client.connect()
    refresh = ControllerTimeRefresh(
        client, tmp_path / "time.jsonl", Host(clock), monotonic=clock.monotonic
    )
    refresh.refresh(owned_job=False)
    refresh.close()
    sent = [loads(frame[8:]) for frame in stream.writes]
    assert [x["operation"] for x in sent] == ["hello", "request", "ack", "request", "ack"]
    assert sent[3]["method"] == "POST" and sent[3]["path"] == "/api/usb/v1/time/submit"
    assert (
        json.loads((tmp_path / "time.jsonl").read_text().splitlines()[-1])["response"]["source"]
        == source
    )


@pytest.mark.parametrize("failure", ["ack", "boot", "sample", "refusal"])
def test_lost_or_bad_time_exchange_never_replays(failure, monkeypatch, tmp_path):
    clock = Clock()
    monkeypatch.setattr(pico_wire, "time", clock)
    sample = ump_reply(2, dict(sample="42", monotonic_ns="0"), delivery=True)
    if failure == "sample":
        sample["body"] = json.dumps(dict(sample="42", monotonic_ns=True))
    final = ump_reply(3, dict(acknowledged=True))
    if failure == "boot":
        final["target"]["boot_id"] = "e" * 32
    body = ump_reply(
        3,
        dict(accepted=True, selected=True, source="controller", uncertainty_ns="100", age_ns="0"),
        delivery=True,
        status=409 if failure == "refusal" else 200,
    )
    stream = MemoryStream(
        ump_frame(ump_hello())
        + ump_frame(sample)
        + ump_frame(ump_reply(2, dict(acknowledged=True)))
        + ump_frame(body)
        + (b"" if failure == "ack" else ump_frame(final))
    )
    client = ManagementClient(stream, "a" * 32)
    client.connect()
    refresh = ControllerTimeRefresh(
        client, tmp_path / "time.jsonl", Host(clock), monotonic=clock.monotonic
    )
    with pytest.raises((PicoWireError, ConnectionError)):
        refresh.refresh(owned_job=False)
    refresh.close()
    assert len([x for x in stream.writes if loads(x[8:]).get("method") == "POST"]) <= 1


def test_adapter_refuses_foreign_owner_before_any_time_submission(tmp_path):
    _clock, client, refresh = refresh_fixture(tmp_path)
    adapter = object.__new__(PicoLiveAdapter)
    adapter.time_refresh = refresh
    adapter.management = client
    adapter._wtp = None
    adapter.audit = SimpleNamespace(cleaning_up=False)
    original = client.get

    def get(*args):
        value = original(*args)
        value["job"]["owner_id"] = "f" * 32
        return value

    client.get = get
    with pytest.raises(PicoWireError, match="ownership"):
        adapter._service_time()
    refresh.close()
    assert not client.exchanges


@pytest.mark.parametrize("mismatch", [None, "owner", "job", "unowned", "inactive"])
def test_refresh_accepts_only_the_matching_owned_output_projection(tmp_path, mismatch):
    clock, client, refresh = refresh_fixture(tmp_path)
    refresh.refresh(owned_job=False)
    clock.advance(30)
    adapter = object.__new__(PicoLiveAdapter)
    adapter.time_refresh = refresh
    adapter.management = client
    adapter._wtp = SimpleNamespace(owner_id="c" * 32)
    adapter.audit = SimpleNamespace(cleaning_up=False)
    status = make_snapshot()["status"]
    for projection in (status["job"], status["standalone"]):
        projection.update(owner_id="c" * 32, job_id="d" * 32, state="running", output_active=True)
    if mismatch == "owner":
        status["standalone"]["owner_id"] = "e" * 32
    if mismatch == "job":
        status["standalone"]["job_id"] = "e" * 32
    if mismatch == "unowned":
        status["job"]["owner_id"] = None
    if mismatch == "inactive":
        status["job"]["output_active"] = False
    client.get = lambda *args: status
    if mismatch is None:
        adapter._service_time()
        assert refresh.sequence == 2
    else:
        with pytest.raises(PicoWireError, match="ownership"):
            adapter._service_time()
        assert refresh.sequence == 1
    refresh.close()


@pytest.mark.parametrize(
    "change", ["arithmetic", "header", "ack_time", "sequence", "coverage", "extra"]
)
def test_rehashed_clock_evidence_cannot_hide_contradictions(tmp_path, change):
    _clock, client, refresh = refresh_fixture(tmp_path)
    refresh.refresh(owned_job=False)
    refresh.close()
    path = tmp_path / "time.jsonl"
    records = [json.loads(x) for x in path.read_text().splitlines()]
    intervals = []
    if change == "arithmetic":
        records[1]["submission"]["uncertainty_ns"] = "1"
    if change == "header":
        records[0]["target"]["target"]["boot_id"] = "e" * 32
    if change == "ack_time":
        records[2]["monotonic_ns"] += 2_000_000_000
    if change == "sequence":
        records[1]["sequence"] = True
    if change == "coverage":
        intervals = [(1_000_000_000, 111_000_000_000)]
    if change == "extra":
        records[1]["future_success"] = True
    path.write_text("\n".join(json.dumps(x) for x in records) + "\n")
    with pytest.raises(PicoWireError):
        validate_time_evidence(
            path, client.hello["target"], client.exchanges, client.hello, intervals
        )


def test_actual_production_usb_time_endpoint(tmp_path):
    from tests.unit.test_pico_wire import EndpointStream
    from wsprrypi_qualification.pico_audit import AuditedStream, WireAudit, validate_wire_audit

    build = os.environ.get("WSPQ_PICO_TEST_BUILD")
    if build is None:
        pytest.skip("Explicit hardware-free production USB build required")
    stream = ProcessStream(Path(build) / "usb_management_driver")
    peer_stream = EndpointStream(Path(build) / "endpoint_driver")
    audit = WireAudit(tmp_path / "wire.jsonl")

    class RealClock:
        monotonic = staticmethod(time.monotonic)
        time_ns = staticmethod(time.time_ns)

        @staticmethod
        def advance(_seconds):
            pass

    try:
        client = ManagementClient(AuditedStream(stream, audit, "management"), "a" * 32)
        client.connect()
        refresh = ControllerTimeRefresh(client, tmp_path / "time.jsonl", Host(RealClock()))
        refresh.refresh(owned_job=False)
        refresh.close()
        outcome = json.loads((tmp_path / "time.jsonl").read_text().splitlines()[-1])["response"]
        assert outcome["accepted"] and outcome["source"] == "controller"
        assert client.get("status", "status")["standalone"]["clock_state"] == "synchronized"
        peer = WtpClient(AuditedStream(peer_stream, audit, "wtp"))
        peer.request(
            "HELLO", dict(versions=["WTP/1"], client_name="time-audit", client_version="1")
        )
    finally:
        stream.close()
        peer_stream.close()
        audit.close()
    validate_wire_audit(
        tmp_path / "wire.jsonl",
        set(),
        client.hello["target"],
        time_evidence=tmp_path / "time.jsonl",
    )
    with pytest.raises(PicoWireError, match="bound host"):
        validate_wire_audit(tmp_path / "wire.jsonl", set(), client.hello["target"])

    # A correctly reframed/rehashed settings POST must remain forbidden.
    records = [json.loads(line) for line in (tmp_path / "wire.jsonl").read_text().splitlines()]
    for record in records:
        if record["carrier"] == "management" and record["phase"] == "write_requested":
            data = bytes.fromhex(record["data_hex"])
            request = loads(data[8:])
            if request.get("method") == "POST":
                request["path"] = "/api/control/v1/config/update"
                record["data_hex"] = ump_frame(request).hex()
    (tmp_path / "forged.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    with pytest.raises(PicoWireError, match="settings mutation"):
        validate_wire_audit(
            tmp_path / "forged.jsonl",
            set(),
            client.hello["target"],
            time_evidence=tmp_path / "time.jsonl",
        )


@pytest.mark.parametrize("failure", [None, "host", "cancel"])
def test_native_wspr_transactions_refresh_across_multiple_expiry_windows(
    tmp_path, monkeypatch, failure
):
    from tests.support import pico_governance as fixture
    from wsprrypi_qualification.pico_campaign import compose_pico_campaign
    from wsprrypi_qualification.wtp_control import WtpControlError, run_transaction

    source = os.environ.get("WSPQ_PICO_SOURCE")
    compiler = os.environ.get("WSPQ_PICO_COMPILER")
    native = os.environ.get("WSPQ_PICO_GOVERNANCE_BUILD")
    if not all([source, compiler, native]):
        pytest.skip("Explicit source/compiler/native endpoint build required")

    class FastStream(fixture.EndpointStream):
        def read(self, maximum, deadline):
            if not self.pending:
                self.clock.advance(min(1, max(0, deadline - self.clock.monotonic())))
            if not self.pending:
                raise TimeoutError("Simulated event poll")
            data = bytes(self.pending[:maximum])
            del self.pending[:maximum]
            return data

    clock = fixture.Clock()
    monkeypatch.setattr(pico_wire, "time", clock)
    stream = FastStream(Path(native) / "wspq-pico-governance", tmp_path, clock)
    try:
        peer = WtpClient(stream)
        negotiated = peer.request(
            "HELLO", dict(versions=["WTP/1"], client_name="time-test", client_version="1")
        )
        config = fixture.configuration()
        config["pico_source"] = source
        plan = compose_pico_campaign(config, Path(compiler), modes=("wspr",))
        job = plan["modes"][0]["jobs"][0]
        client = Management(clock)
        client.peer = peer
        refresh = ControllerTimeRefresh(
            client,
            tmp_path / "time.jsonl",
            Host(clock, fail_after=65 if failure == "host" else None),
            monotonic=clock.monotonic,
        )
        adapter = object.__new__(PicoLiveAdapter)
        adapter.time_refresh = refresh
        adapter.management = client
        adapter._wtp = peer
        adapter.audit = SimpleNamespace(cleaning_up=False)

        def ready():
            if failure == "cancel" and clock.monotonic() > 45:
                raise InterruptedError("Cancellation during WSPR")
            adapter._service_time()
            assert client.last is not None and clock.monotonic() - client.last < 90

        def inactive():
            return peer.request("STATUS", {})

        adapter._service_time()
        for _ in range(3 if failure is None else 1):
            args = dict(
                device_id=negotiated["device_id"],
                start_utc_ns=clock.time_ns() + 5_000_000_000,
                before_arm=lambda: {},
                receiver_ready=ready,
                verify_inactive=inactive,
                wall_ns=clock.time_ns,
                monotonic=clock.monotonic,
                expected_boot_id="b" * 32,
                maximum_duration_ns=110_592_000_000,
                maximum_start_uncertainty_ns=20_000_000,
            )
            if failure is None:
                result = run_transaction(peer, job, **args)
                assert result["completed"] and result["cleanup_verified"]
            else:
                with pytest.raises(WtpControlError) as caught:
                    run_transaction(peer, job, **args)
                assert caught.value.evidence["cleanup_verified"]
        refresh.close()
        state = peer.request("STATUS", {})
        assert state["owner_id"] is None and state["output_active"] is False
        arms = [x for x in stream.requests if x["op"] == "ARM"]
        assert len(arms) == (3 if failure is None else 1)
        if failure is None:
            assert clock.monotonic() > 330 and refresh.sequence >= 11
            assert client.running_samples > 0  # Native output is deliberately hardware-inhibited.
    finally:
        stream.close()


def test_nested_carrier_cleanup_does_not_disable_parent_wspr_clock_refresh(tmp_path, monkeypatch):
    from tests.support import pico_governance as fixture

    source = os.environ.get("WSPQ_PICO_SOURCE")
    native = os.environ.get("WSPQ_PICO_GOVERNANCE_BUILD")
    wsprd = os.environ.get("WSPQ_SMOKE_WSPRD")
    if not all((source, native, wsprd)):
        pytest.skip("Explicit native endpoint/compiler and offline decoder required")
    original_observe = PicoLiveAdapter.observe
    original_snapshot = fixture.Adapter.snapshot
    observed = []

    def snapshot(adapter, **kwargs):
        adapter._service_time()
        value = original_snapshot(adapter, **kwargs)
        refresh = adapter.time_refresh
        if refresh.last is None or adapter.capture.clock.monotonic() - refresh.last >= 90:
            value["ntp-observations"]["active_source"] = "none"
            value["status"]["standalone"]["clock_state"] = "unsynchronized"
        return value

    def observe(adapter, *args, **kwargs):
        outer = not hasattr(adapter, "time_refresh")
        if outer:
            clock = adapter.capture.clock
            client = Management(clock)
            client.peer = adapter.wtp
            adapter.management = client
            adapter.time_refresh = ControllerTimeRefresh(
                client, tmp_path / "clock.jsonl", Host(clock), monotonic=clock.monotonic
            )
            observed.append(adapter)
        try:
            return original_observe(adapter, *args, **kwargs)
        finally:
            if outer:
                adapter.time_refresh.close()

    monkeypatch.setattr(fixture.Adapter, "observe", observe)
    monkeypatch.setattr(fixture.Adapter, "snapshot", snapshot)
    receipt = fixture.run_case(
        Path(source), Path(native), Path(wsprd), tmp_path / "nested campaign", "wspr"
    )
    assert receipt["error"] is None, receipt["error"]
    assert receipt["result"]["outcome"] == "passed"
    assert receipt["receiver_cleanup_failed"] is False
    assert observed[0].time_refresh.sequence >= 6
    assert receipt["simulated_seconds"] > 220
    requests = json.loads((tmp_path / "nested campaign/requests.json").read_text())
    assert sum(row["op"] == "ARM" for row in requests) == 2  # Carrier plus full WSPR.
