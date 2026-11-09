"""Bounded one-job control, connected offline acquisition, and refusal checks."""

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.pico_smoke import ManagementStream, run_case
from wsprrypi_qualification import pico_serial, pico_wire
from wsprrypi_qualification import pico_smoke as smoke
from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.pico_wire import PicoWireError, loads


def test_deadline_is_absolute_and_cannot_be_cleared():
    now = [10.0]
    token = smoke.DeadlineEvent(5, lambda: now[0])
    assert not token.is_set()
    now[0] = 15
    token.clear()
    assert token.is_set() and token.wait(0)


@pytest.mark.parametrize("operation", ["LOAD", "ARM"])
def test_ambiguous_write_consumes_the_only_attempt(operation):
    calls = []

    def request(*args):
        calls.append(args)
        raise TimeoutError("write may have reached firmware")

    peer = smoke.OneJobPeer(SimpleNamespace(request=request))
    with pytest.raises(TimeoutError):
        peer.request(operation, {})
    with pytest.raises(PicoWireError, match="already consumed"):
        peer.request(operation, {})
    assert len(calls) == 1


def test_repeated_cleanup_does_not_renew_deadline():
    adapter = object.__new__(smoke.SmokeAdapter)
    assigned = []
    adapter.audit = SimpleNamespace(cleaning_up=False)
    adapter.usb = SimpleNamespace(begin_cleanup=lambda: None, begin_operations=assigned.append)
    adapter.begin_cleanup()
    first = adapter.cleanup_cancel
    assert first is not None
    first.set()
    adapter.begin_cleanup()
    assert assigned == [first, first]
    assert assigned[-1].is_set()


def test_live_entry_requires_explicit_authority_before_any_adapter(monkeypatch, tmp_path):
    def forbidden(*args):
        pytest.fail("physical boundary crossed")

    monkeypatch.setattr(smoke, "SmokeAdapter", forbidden)
    with pytest.raises(PicoWireError, match="authorization"):
        smoke.run({}, tmp_path, "operator", "digest")


def test_fresh_worker_timeout_is_bounded(monkeypatch, tmp_path):
    def timed_out(command, **kwargs):
        assert kwargs["timeout"] == 0.1
        assert command[1:4] == ["-I", "-m", "wsprrypi_qualification.pico_smoke"]
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(smoke, "__name__", "__main__")
    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(subprocess.TimeoutExpired):
        smoke.worker(tmp_path, "analyze", 0.1)


def test_failed_fresh_validation_cannot_publish_a_pass(monkeypatch, tmp_path):
    result = dict(
        schema_version=1,
        evidence_type="pico_one_job_governance",
        qualification_claim=False,
        outcome="passed",
        cleanup_verified=True,
        settings_unchanged=True,
        frame={},
        errors=[],
        artifacts=[],
    )

    def refused(*args):
        raise ValueError("contradiction")

    monkeypatch.setattr(smoke, "worker", refused)
    smoke.publish(tmp_path, result)
    saved = loads((tmp_path / "smoke-result.json").read_bytes())
    assert saved["outcome"] == "blocked"
    assert saved["errors"][0]["phase"] == "validation"
    assert not (tmp_path / "candidate-result.json").exists()


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("keyed", "passed"),
        ("legacy-manual", "passed"),
        ("legacy-auto", "passed"),
        ("receiver", "blocked"),
        ("cancel", "cleanup_failed"),
        ("busy", "blocked"),
    ],
)
def test_connected_one_job_runner(tmp_path, scenario, expected):
    compiler = os.environ.get("WSPQ_PICO_COMPILER")
    source = os.environ.get("WSPQ_PICO_SOURCE")
    wsprd = os.environ.get("WSPQ_SMOKE_WSPRD")
    if not all((compiler, source, wsprd)):
        pytest.skip("Explicit offline compiler, source, and decoder artifacts required")
    root = tmp_path / "one job with spaces"
    result = run_case(Path(source), Path(compiler).parent, Path(wsprd), root, scenario)
    root = Path(result["output_directory"])
    assert result["outcome"] == expected, result["errors"]
    assert result["qualification_claim"] is False
    requests = json.loads((root / "requests.json").read_bytes())
    for operation in ("LOAD", "ARM"):
        assert sum(row["op"] == operation for row in requests) == (
            0 if scenario in {"receiver", "busy"} else 1
        )
    smoke.worker(root, "validate", 30)
    record = root / "smoke-result.json"
    original_record = record.read_bytes()
    if scenario == "keyed":
        transaction = root / "observation/transaction.json"
        original = transaction.read_bytes()
        try:
            value = loads(original)
            value["completed"] = False
            transaction.write_text(json.dumps(value))
            # Rehash to test semantic validation rather than only inventory hashing.
            saved = loads(original_record)
            saved["artifacts"] = [
                artifact(p) for p in sorted(root.rglob("*")) if p.is_file() and p != record
            ]
            record.write_text(json.dumps(saved))
            with pytest.raises(subprocess.CalledProcessError):
                smoke.worker(root, "validate", 30)
        finally:
            transaction.write_bytes(original)
            record.write_bytes(original_record)
        audit = root / "usb-io.jsonl"
        original_audit = audit.read_bytes()
        try:
            lines = [loads(line) for line in original_audit.splitlines()]
            lines = [
                row
                for row in lines
                if row["carrier"] != "management" or row["phase"] == "sealed" or row["io_id"] <= 3
            ]
            for index, row in enumerate(lines, 1):
                row["sequence"] = index
            audit.write_text("".join(json.dumps(row) + "\n" for row in lines))
            saved = loads(original_record)
            saved["artifacts"] = [
                artifact(p) for p in sorted(root.rglob("*")) if p.is_file() and p != record
            ]
            record.write_text(json.dumps(saved))
            with pytest.raises(subprocess.CalledProcessError):
                smoke.worker(root, "validate", 30)
        finally:
            audit.write_bytes(original_audit)
            record.write_bytes(original_record)
    if scenario == "legacy-auto":
        context = root / "validation-context.json"
        original = context.read_bytes()
        value = loads(original)
        assert value["prepared"]["atomic_input_capability"] == "unavailable"
        try:
            value["prepared"]["calibration_qualification"] = "passed"
            context.write_text(json.dumps(value))
            saved = loads(original_record)
            saved["artifacts"] = [
                artifact(p) for p in sorted(root.rglob("*")) if p.is_file() and p != record
            ]
            record.write_text(json.dumps(saved))
            with pytest.raises(subprocess.CalledProcessError):
                smoke.worker(root, "validate", 30)
        finally:
            context.write_bytes(original)
            record.write_bytes(original_record)
    if scenario == "cancel":
        try:
            saved = loads(original_record)
            saved.update(outcome="blocked", cleanup_verified=True)
            record.write_text(json.dumps(saved))
            with pytest.raises(subprocess.CalledProcessError):
                smoke.worker(root, "validate", 30)
        finally:
            record.write_bytes(original_record)
    smoke.worker(root, "validate", 30)


def test_management_requests_share_operation_deadline(monkeypatch):
    now = [0.0]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(pico_serial, "time", clock)
    monkeypatch.setattr(pico_wire, "time", clock)
    serial = object.__new__(pico_serial.SerialStream)
    serial.cancellation = smoke.DeadlineEvent(5, clock.monotonic)

    class Stream(ManagementStream):
        def read(self, maximum, deadline):
            now[0] += 0.5
            serial._remaining(deadline)
            return super().read(maximum, deadline)

    stream = Stream(SimpleNamespace(_wtp=None, fixture_scenario="keyed"))
    client = pico_wire.ManagementClient(stream, stream.snapshot["target"]["device_id"])
    client.connect()
    completed = 0
    with pytest.raises(InterruptedError):
        for _ in range(10):
            client.get("config", "config-read")
            completed += 1
    assert completed < 3
    assert now[0] == 5


def test_cleanup_rebinds_real_usb_stream_tokens_without_extending_deadline():
    adapter = object.__new__(smoke.SmokeAdapter)
    adapter.audit = SimpleNamespace(cleaning_up=False)
    adapter.usb = object.__new__(pico_serial.PicoUsb)
    adapter.usb.management = SimpleNamespace(cancellation=None)
    adapter.usb.wtp = SimpleNamespace(cancellation=None)
    adapter.begin_cleanup()
    token = adapter.cleanup_cancel
    token.set()
    adapter.begin_cleanup()
    assert adapter.usb.management.cancellation is token
    assert adapter.usb.wtp.cancellation is token
    assert token.is_set()


def test_total_budget_exhaustion_prevents_analysis():
    with pytest.raises(TimeoutError, match="total time budget"):
        smoke.remaining(0)
