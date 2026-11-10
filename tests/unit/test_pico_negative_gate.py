"""Offline contract tests; no hardware constructors, host access, or real signals."""

import contextlib
import copy
import importlib.util
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.pico_wire import crc32c, encode
from wsprrypi_qualification.real_capabilities import capability_plan_sha256

DRAFT = Path(__file__).resolve().parents[2] / "scripts/pico_negative_gate.py"
spec = importlib.util.spec_from_file_location("gate", DRAFT)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
CAPTURE = RUN = NEGATIVE = None
pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux adapter POSIX offline tests")


def active_wire(raw):
    rows = [g.loads(line) for line in raw.splitlines()]
    incoming = [row for row in rows if row["carrier"] == "wtp" and row["phase"] == "read"]
    # Preserve original transport fragmentation, adjusting only the changed frame's end.
    original = b"".join(bytes.fromhex(row["data_hex"]) for row in incoming)
    frames = g._frames(original, "wtp", "in")
    changes = []
    old_position = 0
    encoded = []
    for frame in frames:
        old_size = 16 + int.from_bytes(original[old_position + 8 : old_position + 12], "big")
        old_position += old_size
        if frame.get("event") == "JOB_STATE" and frame["body"]["state"] == "running":
            frame["body"]["output_active"] = True
        payload = encode(frame)
        block = (
            b"WTPF\x01\x01\x00\x00" + struct.pack(">II", len(payload), crc32c(payload)) + payload
        )
        changes.append((old_position, len(block) - old_size))
        encoded.append(block)
    wire = b"".join(encoded)
    old_position = position = 0
    for row in incoming:
        count = len(bytes.fromhex(row["data_hex"]))
        adjusted = count + sum(
            delta for end, delta in changes if old_position < end <= old_position + count
        )
        row["data_hex"] = wire[position : position + adjusted].hex()
        position += adjusted
        old_position += count
    assert position == len(wire)
    return b"".join((json.dumps(row) + "\n").encode() for row in rows)


@pytest.fixture(scope="module", autouse=True)
def generated_inputs(tmp_path_factory):
    global CAPTURE, RUN, NEGATIVE
    from tests.support.pico_smoke import run_case

    source, compiler, wsprd = (
        os.environ.get(key)
        for key in ("WSPQ_PICO_SOURCE", "WSPQ_PICO_COMPILER", "WSPQ_SMOKE_WSPRD")
    )
    if not all((source, compiler, wsprd)):
        pytest.skip("Explicit inhibited Pico source/compiler and wsprd required")
    CAPTURE = tmp_path_factory.mktemp("negative-gate-generated")
    result = run_case(Path(source), Path(compiler).parent, Path(wsprd), CAPTURE / "keyed", "keyed")
    assert result["outcome"] == "passed"
    RUN = Path(result["output_directory"])
    (CAPTURE / "plan.json").write_bytes((RUN / "requested-plan.json").read_bytes())
    (RUN / "usb-io.jsonl").write_bytes(active_wire((RUN / "usb-io.jsonl").read_bytes()))
    result = run_case(
        Path(source), Path(compiler).parent, Path(wsprd), CAPTURE / "cancel", "cancel"
    )
    assert result["outcome"] == "cleanup_failed"
    NEGATIVE = Path(result["output_directory"])


@pytest.fixture
def running():
    a = g.Audit(g.read(CAPTURE / "plan.json"), host_uncertainty_ns=100_000_000)
    for line in (RUN / "usb-io.jsonl").read_bytes().splitlines(keepends=True):
        a.feed(line)
        if a.running:
            return a
    pytest.fail("Retained capture has no running event")


def clocks(a):
    arm = next(f for f in a.acks.values() if f["op"] == "ARM")["body"]
    mono = a.running[0]
    wall = int(arm["start_utc_ns"]) + 10_000_000
    return mono, wall, wall - mono


def signal_guard():
    now = g.time.monotonic_ns()
    return dict(checked_monotonic_ns=now, valid_until_monotonic_ns=now + 100_000_000)


def test_recorded_running_is_eligible(running):
    assert running.eligible(*clocks(running))


def test_host_uncertainty_consumes_actual_window_not_fixed_threshold(running):
    mono, wall, offset = clocks(running)
    running.host_uncertainty_ns = 200_000_000
    assert running.eligible(mono, wall, offset)
    running.host_uncertainty_ns = 10_000_000_000
    assert not running.eligible(mono, wall, offset)
    running.host_uncertainty_ns = 0
    assert not running.eligible(mono, wall, offset)  # Refusal remains latched.


def test_delayed_running_delivery_does_not_renew_job_interval(running):
    mono, wall, offset = clocks(running)
    delayed = mono + 10_000_000_000
    running.running = (delayed, running.running[1], running.running[2])
    assert not running.eligible(delayed, wall + 10_000_000_000, offset)


def test_host_uncertainty_reduces_lease_deadline(running):
    mono, wall, offset = clocks(running)
    claim = next(f["body"] for f in running.acks.values() if f["op"] == "CLAIM")
    clock = next(f["body"]["clock"] for f in running.acks.values() if f["op"] == "ARM")
    device_now = int(clock["monotonic_now_ns"]) + wall - int(clock["utc_now_ns"])
    claim["expires_monotonic_ns"] = str(device_now + 400_000_000)
    running.host_uncertainty_ns = 200_000_000
    assert not running.eligible(mono, wall, offset)


def test_preparation_roundtrip_with_real_plan_validation(monkeypatch, tmp_path):
    from tests.unit.test_pico_negative_preparation import observation

    plan = g.read(CAPTURE / "plan.json")
    plan["configuration"]["receiver"]["driver"] = "sdrplay"
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(plan))
    raw = observation()
    clock = g.clock_from_observation(raw)
    monkeypatch.setattr(g, "platform_check", lambda: None)
    monkeypatch.setattr(g, "collect_clock", lambda _: (raw, clock))
    monkeypatch.setattr(g.time, "monotonic_ns", lambda: raw["after_ns"])
    monkeypatch.setattr(g.time, "time_ns", lambda: raw["wall_ns"])
    real = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **kw: (
            "fixture-boot" if str(p) == "/proc/sys/kernel/random/boot_id" else real(p, *a, **kw)
        ),
    )
    root = tmp_path / "prepared"
    g.prepare(source, root, Path("/fixture/chronyc"), observe_host=True)
    pre = g.validate_preflight(root, plan, g.read(root / "resolved-gate.json"))
    assert pre["clock"] == clock
    pre["observation"]["tracking_csv"] = pre["observation"]["tracking_csv"].replace(
        "Normal", "Unknown"
    )
    (root / "host-preflight.json").write_text(json.dumps(pre))
    with pytest.raises(ValueError):
        g.validate_preflight(root, plan, g.read(root / "resolved-gate.json"))


def test_physical_runner_rejects_fixture_before_processes(monkeypatch, tmp_path):
    g.write(tmp_path / "plan.json", g.read(CAPTURE / "plan.json"))
    monkeypatch.setattr(g.subprocess, "Popen", lambda *a, **kw: pytest.fail("Process created"))
    with pytest.raises(ValueError, match="Synthetic receiver"):
        g.supervise(tmp_path, enable_physical=True, operator="offline-test")


@pytest.mark.parametrize("chunk_size", [1, 17, 97, 65536])
def test_fragmented_replay_matches_full_replay(chunk_size):
    raw = (RUN / "usb-io.jsonl").read_bytes()
    a = g.Audit(g.read(CAPTURE / "plan.json"), host_uncertainty_ns=100_000_000)
    for offset in range(0, len(raw), chunk_size):
        a.feed(raw[offset : offset + chunk_size])
    assert len(a.frames) > 0 and a.disarmed and not a.partial
    assert sum(d == "out" and f.get("op") == "ARM" for d, f, _, _ in a.frames) == 1
    assert all(not value for value in a.buffers.values())
    rows = [g.loads(line) for line in raw.splitlines()]
    for direction, phase in (("out", "write_requested"), ("in", "read")):
        wire = b"".join(
            bytes.fromhex(row["data_hex"])
            for row in rows
            if row["carrier"] == "wtp" and row["phase"] == phase
        )
        assert [f for d, f, _, _ in a.frames if d == direction] == g._frames(wire, "wtp", direction)


@pytest.mark.parametrize(
    "mutation",
    [
        "stale",
        "future",
        "clock_step",
        "late",
        "uncertain",
        "not_sync",
        "bad_leap",
        "wrong_arm_job",
        "wrong_load_job",
        "unacknowledged",
        "disarmed",
    ],
)
def test_eligibility_refusals(running, mutation):
    mono, wall, offset = clocks(running)
    arm = next(f for f in running.acks.values() if f["op"] == "ARM")
    load = next(f for f in running.acks.values() if f["op"] == "LOAD")
    if mutation == "stale":
        mono += g.FRESH_NS + 1
        wall += g.FRESH_NS + 1
    elif mutation == "future":
        mono -= 1
        wall -= 1
    elif mutation == "clock_step":
        wall += g.STEP_NS + 1
    elif mutation == "late":
        wall += 4_000_000_000
        offset = wall - mono
    elif mutation == "uncertain":
        arm["body"]["clock"]["uncertainty_ns"] = "100000001"
    elif mutation == "not_sync":
        arm["body"]["clock"]["state"] = "unsynchronized"
    elif mutation == "bad_leap":
        arm["body"]["clock"]["leap"] = "unknown"
    elif mutation == "wrong_arm_job":
        arm["body"]["job_id"] = "f" * 32
    elif mutation == "wrong_load_job":
        load["body"]["job_id"] = "f" * 32
    elif mutation == "unacknowledged":
        del running.acks[arm["request_id"]]
    elif mutation == "disarmed":
        running.disarmed = True
    assert not running.eligible(mono, wall, offset)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_boot",
        "wrong_session",
        "duplicate",
        "regressing_id",
        "different_job",
        "inactive_running",
        "owner_lost",
    ],
)
def test_conflicting_event_disarms(running, mutation):
    event = copy.deepcopy(running.running[2])
    event["event_id"] = str(int(event["event_id"]) + 1)
    if mutation == "wrong_boot":
        event["boot_id"] = "f" * 32
    elif mutation == "wrong_session":
        event["session_id"] = "f" * 32
    elif mutation == "duplicate":
        event["event_id"] = running.running[2]["event_id"]
    elif mutation == "regressing_id":
        event["event_id"] = "1"
    elif mutation == "different_job":
        event["body"]["job_id"] = "f" * 32
    elif mutation == "inactive_running":
        event["body"]["output_active"] = False
    elif mutation == "owner_lost":
        owner = next(f for f in running.requests.values() if f["op"] == "CLAIM")["body"]["owner_id"]
        event["event"] = "OWNER_RELEASED"
        event["body"] = {"owner_id": owner, "reason": "lease_expired", "output_active": False}
    g.validate_contract(event, "wtp")
    try:
        running.accept("in", event, running.running[0], running.sequence + 1)
    except ValueError:
        return
    assert not running.eligible(*clocks(running))


def test_claim_ack_must_match_requested_owner(running):
    ack = next(f for f in running.acks.values() if f["op"] == "CLAIM")
    ack["body"]["owner_id"] = "f" * 32
    g.validate_contract(ack, "wtp")
    assert not running.eligible(*clocks(running))


def test_expired_lease_cannot_authorize_injection(running):
    ack = next(f for f in running.acks.values() if f["op"] == "CLAIM")
    arm = next(f for f in running.acks.values() if f["op"] == "ARM")
    ack["body"]["expires_monotonic_ns"] = arm["body"]["start_monotonic_ns"]
    g.validate_contract(ack, "wtp")
    assert not running.eligible(*clocks(running))


@pytest.mark.parametrize("phase", ["read_failed", "write_failed"])
def test_usb_failure_disarms(running, phase):
    record = dict(
        schema_version=1,
        sequence=running.sequence + 1,
        monotonic_ns=str(running.last_ns),
        carrier="wtp",
        phase=phase,
        io_id=999,
        data_hex="",
        error="OSError",
    )
    running.feed((json.dumps(record) + "\n").encode())
    assert not running.eligible(*clocks(running))


def test_actual_production_child_matches():
    p = g.read(NEGATIVE / "observation/rf-on-plan.json")
    diag = g.read(p["metadata_path"] + ".execution.json")
    controller = {"pid": 100, "uid": 501}
    child = dict(
        pid=101,
        ppid=100,
        uid=501,
        start="123",
        executable=str(Path(p["helper"]).resolve()),
        argv=diag["arguments"],
    )
    assert g.candidate_matches(child, controller, p, diag["helper"]["sha256"])


def test_numeric_argv_matches_production():
    p = g.read(NEGATIVE / "observation/rf-on-plan.json")
    diag = g.read(p["metadata_path"] + ".execution.json")
    assert g.capture_argv(p) == diag["arguments"][:-1]


@pytest.mark.parametrize("count", [0, 2, 3])
def test_ambiguous_or_missing_child_refused(count):
    with pytest.raises(ValueError):
        g.choose([{}] * count)


def test_exactly_one_child_selected():
    child = {"pid": 1}
    assert g.choose([child]) is child


def test_signal_allowance_consumed_on_ambiguous_error(monkeypatch, tmp_path):
    calls = []

    def fail(fd, sig):
        calls.append((fd, sig))
        raise OSError("Ambiguous syscall")

    monkeypatch.setattr(g.signal, "pidfd_send_signal", fail, raising=False)
    one = g.OneSignal()
    target = SimpleNamespace(fd=123, verify=lambda: None)
    with pytest.raises(OSError):
        one.send(target, tmp_path / "intent.json", {}, signal_guard)
    with pytest.raises(ValueError):
        one.send(target, tmp_path / "intent2.json", {}, signal_guard)
    assert len(calls) == 1 and one.used


def test_identity_rechecked_after_durable_intent(monkeypatch, tmp_path):
    alive = [True]
    calls = []

    def verify():
        if not alive[0]:
            raise ValueError("Process exited while fsync blocked")

    real_write = g.write

    def delayed_write(path, value):
        real_write(path, value)
        alive[0] = False

    monkeypatch.setattr(g, "write", delayed_write)
    monkeypatch.setattr(
        g.signal, "pidfd_send_signal", lambda *args: calls.append(args), raising=False
    )
    with contextlib.suppress(ValueError):
        g.OneSignal().send(
            SimpleNamespace(fd=123, verify=verify), tmp_path / "intent.json", {}, signal_guard
        )
    assert calls == []


def test_no_signal_if_intent_write_fails(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        g.signal, "pidfd_send_signal", lambda *args: calls.append(args), raising=False
    )
    path = tmp_path / "intent.json"
    path.write_text("{}")
    with pytest.raises(FileExistsError):
        g.OneSignal().send(SimpleNamespace(fd=1, verify=lambda: None), path, {}, signal_guard)
    assert calls == []


def test_durable_intent_delay_cannot_cross_signal_window(monkeypatch, tmp_path, running):
    mono, wall, offset = clocks(running)
    now = [mono, wall]
    assert running.eligible(*now, offset)
    calls = []
    real_write = g.write

    def delayed_write(path, value):
        real_write(path, value)
        now[0] += 4_000_000_000
        now[1] += 4_000_000_000

    monkeypatch.setattr(g, "write", delayed_write)
    monkeypatch.setattr(
        g.signal, "pidfd_send_signal", lambda *args: calls.append(args), raising=False
    )

    def guard():
        if not running.eligible(*now, offset):
            raise ValueError("Window expired")
        return signal_guard()

    with pytest.raises(ValueError, match="Window expired"):
        g.OneSignal().send(
            SimpleNamespace(fd=1, verify=lambda: None), tmp_path / "intent.json", {}, guard
        )
    assert not running.eligible(*now, offset)
    assert calls == [], "Signal was still issued after the eligible window elapsed"


def test_held_process_closes_handle_on_identity_failure(monkeypatch):
    closed = []
    monkeypatch.setattr(g.os, "pidfd_open", lambda pid: 12, raising=False)
    monkeypatch.setattr(g.select, "select", lambda *args: ([], [], []))
    monkeypatch.setattr(g, "identity", lambda pid: {"pid": pid, "start": "other"})
    monkeypatch.setattr(g.os, "close", closed.append)
    with pytest.raises(ValueError):
        g.HeldProcess(1, {"pid": 1, "start": "original"})
    assert closed == [12]


def test_missing_assessment_evidence_is_inconclusive(tmp_path):
    # The contract requires a classification, not a traceback or a false pass.
    result = g.assess(tmp_path)
    assert result["assessment"] == "inconclusive" and not result["bench_available"]


def test_existing_negative_fixture_validates_without_promoting_cleanup():
    result = g.validate_retained(NEGATIVE)
    assert result["outcome"] == "cleanup_failed" and result["cleanup_verified"] is False


@pytest.fixture
def fabricated_assessment(tmp_path):
    """Scratch-only adversarial evidence, explicitly fabricated, never a live receipt.

    Retain production validation rather than mocking it. Copy a nonhardware
    negative fixture, relocate paths and rehash so semantic defects are tested.
    """
    root = tmp_path / "fabricated offline assessment"
    run = root / "runs" / NEGATIVE.name
    shutil.copytree(NEGATIVE, run)
    for path in run.rglob("*.json"):
        path.write_text(path.read_text().replace(str(NEGATIVE), str(run)))

    def save(path, value):
        path.write_text(json.dumps(value, indent=2) + "\n")

    plan = g.read(run / "plan.json")
    save(root / "plan.json", plan)
    p = g.read(run / "observation/rf-on-plan.json")
    diag_path = Path(p["metadata_path"] + ".execution.json")
    diag = g.read(diag_path)
    diag["execution"]["return_code"] = -9
    diag["capability_plan_sha256"] = capability_plan_sha256(p)
    save(diag_path, diag)
    # Update relocated artifact hashes. Do not alter original retained evidence.
    result_path = run / "smoke-result.json"
    result = g.read(result_path)
    result["artifacts"] = [
        artifact(path) for path in sorted(run.rglob("*")) if path.is_file() and path != result_path
    ]
    save(result_path, result)
    assert g.validate_retained(run)["outcome"] == "cleanup_failed"
    a = g.Audit(plan)
    a.feed((run / "usb-io.jsonl").read_bytes())
    _d, event, stamp, seq = next(
        row
        for row in a.frames
        if row[1].get("event") == "JOB_STATE" and row[1]["body"]["state"] == "running"
    )
    intent = dict(
        run=str(run),
        plan_sha256=g.plan_digest(plan),
        controller={"pid": 123, "uid": 501},
        target=dict(
            pid=456, ppid=123, uid=501, start="123", executable=p["helper"], argv=diag["arguments"]
        ),
        helper_sha256=diag["helper"]["sha256"],
        job_id=a.job["job_id"],
        boot_id=a.boot,
        session_id=a.session,
        trigger_sequence=seq,
        trigger=event,
        signal="SIGKILL",
        monotonic_ns=stamp + 1,
        wall_offset_ns=0,
    )
    save(root / "signal-intent.json", intent)
    save(root / "signal-outcome.json", dict(injected=True, target_pid=456))
    save(root / "injector-finished.json", dict(injected=True))
    save(root / "supervisor-finished.json", dict(injector_returncode=0))
    return root


@pytest.mark.parametrize(
    "mutation",
    [
        "inactive_trigger",
        "impossible_time",
        "wrong_parent",
        "wrong_signal",
        "missing_signal_time",
        "changed_identity",
        "timeout_competes",
        "production_pass",
        "missing_receipt",
    ],
)
def test_assessor_rejects_fabricated_or_contradictory_proof(fabricated_assessment, mutation):
    root = fabricated_assessment
    intent_path = root / "signal-intent.json"
    intent = g.read(intent_path)
    if mutation == "impossible_time":
        intent["monotonic_ns"] = 0
    elif mutation == "wrong_parent":
        intent["target"]["ppid"] = 999
    elif mutation == "wrong_signal":
        intent["signal"] = "SIGTERM"
    elif mutation == "changed_identity":
        intent["target"]["start"] = "forged"
    elif mutation == "missing_receipt":
        (root / "signal-outcome.json").unlink()
    elif mutation in {"timeout_competes", "production_pass"}:
        run = next((root / "runs").glob("pico-*"))
        result_path = run / "smoke-result.json"
        result = g.read(result_path)
        if mutation == "timeout_competes":
            p = g.read(run / "observation/rf-on-plan.json")
            diag_path = Path(p["metadata_path"] + ".execution.json")
            diag = g.read(diag_path)
            diag["execution"]["timed_out"] = True
            diag_path.write_text(json.dumps(diag))
        else:
            result["outcome"] = "passed"
            result["cleanup_verified"] = True
        result["artifacts"] = [
            artifact(path)
            for path in sorted(run.rglob("*"))
            if path.is_file() and path != result_path
        ]
        result_path.write_text(json.dumps(result))
    intent_path.write_text(json.dumps(intent))
    try:
        result = g.assess(root)
    except (ValueError, KeyError, OSError):
        # Refusal is safe here; graceful missing-evidence classification has its own test.
        return
    assert result["assessment"] != "passed", f"False pass for {mutation}: {result}"


class FakeChild:
    def __init__(self, pid, code=None):
        self.pid = pid
        self.returncode = code
        self.signals = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise g.subprocess.TimeoutExpired("fixture", timeout)
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)
        self.returncode = -int(sig)

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def supervisor_fixture(
    monkeypatch, root, *, before_ready=None, identity_error=False, during_launch=None
):
    plan = g.read(CAPTURE / "plan.json")
    plan["configuration"]["receiver"]["driver"] = "sdrplay"
    g.write(root / "plan.json", plan)
    g.write(root / "resolved-gate.json", {})
    watcher = FakeChild(111)
    runner = FakeChild(222)
    handlers = {}
    launched = []
    monkeypatch.setattr(g, "validate_preflight", lambda *args: {"host_boot_id": "fixture-boot"})

    def identity(pid):
        if pid == runner.pid and identity_error:
            raise OSError("Controller identity failed after launch")
        return dict(pid=pid, ppid=1, start="123", uid=501, executable="/fixture", argv=["fixture"])

    monkeypatch.setattr(g, "identity", identity)
    monkeypatch.setattr(g.signal, "signal", lambda sig, handler: handlers.update({sig: handler}))
    monkeypatch.setattr(g.signal, "pthread_sigmask", lambda *args: set())
    real_read = g.read

    def checked_read(path):
        value = real_read(path)
        if Path(path).name == "injector-ready.json" and before_ready:
            before_ready(handlers, watcher, value)
        return value

    monkeypatch.setattr(g, "read", checked_read)

    def popen(args, **kwargs):
        launched.append(args)
        if len(launched) == 1:
            invocation = real_read(root / "invocation.json")
            g.write(
                root / "injector-ready.json",
                dict(
                    pid=111,
                    source_sha256=g.digest(DRAFT),
                    invocation_id=invocation["id"],
                    plan_sha256=invocation["plan_sha256"],
                    platform="linux-pidfd",
                    monotonic_ns=g.time.monotonic_ns(),
                ),
            )
            return watcher
        if during_launch:
            during_launch(handlers)
        if not identity_error and not during_launch:
            runner.returncode = 1
            watcher.returncode = 0
            g.write(root / "injector-finished.json", dict(injected=True, window_proven=True))
        return runner

    monkeypatch.setattr(g.subprocess, "Popen", popen)
    return launched, runner, watcher


def test_supervisor_valid_positive_launch_and_reap(monkeypatch, tmp_path):
    launched, runner, watcher = supervisor_fixture(monkeypatch, tmp_path)
    result = g.supervise(tmp_path, enable_physical=True, operator="offline-test")
    assert len(launched) == 2 and not result["errors"] and result["runner_reaped"]
    assert not result["cancelled"] and runner.signals == [] and watcher.poll() is not None


def test_supervisor_cancellation_before_launch_prevents_launch(monkeypatch, tmp_path):
    def cancel(handlers, watcher, ready):
        handlers[g.signal.SIGINT](None, None)

    launched, _runner, _watcher = supervisor_fixture(monkeypatch, tmp_path, before_ready=cancel)
    result = g.supervise(tmp_path, enable_physical=True, operator="offline-test")
    assert len(launched) == 1 and result["cancelled"] and result["errors"]


def test_supervisor_setup_error_cancels_started_runner(monkeypatch, tmp_path):
    launched, runner, _watcher = supervisor_fixture(monkeypatch, tmp_path, identity_error=True)
    result = g.supervise(tmp_path, enable_physical=True, operator="offline-test")
    assert len(launched) == 2 and runner.signals == [g.signal.SIGINT]
    assert result["runner_reaped"] and result["errors"]


def test_supervisor_cancellation_during_launch_reaches_child(monkeypatch, tmp_path):
    launched, runner, _watcher = supervisor_fixture(
        monkeypatch, tmp_path, during_launch=lambda h: h[g.signal.SIGINT](None, None)
    )
    result = g.supervise(tmp_path, enable_physical=True, operator="offline-test")
    assert len(launched) == 2 and runner.signals == [g.signal.SIGINT] and result["runner_reaped"]


@pytest.mark.parametrize("fault", ["wrong_nonce", "wrong_source", "dead", "old_time"])
def test_stale_readiness_refuses_launch(monkeypatch, tmp_path, fault):
    def alter(handlers, watcher, ready):
        if fault == "wrong_nonce":
            ready["invocation_id"] = "f" * 32
        if fault == "wrong_source":
            ready["source_sha256"] = "0" * 64
        if fault == "dead":
            watcher.returncode = 1
        if fault == "old_time":
            ready["monotonic_ns"] = 0

    launched, _runner, _watcher = supervisor_fixture(monkeypatch, tmp_path, before_ready=alter)
    result = g.supervise(tmp_path, enable_physical=True, operator="offline-test")
    assert len(launched) == 1 and result["errors"]


def test_no_physical_enable_no_processes(monkeypatch, tmp_path):
    monkeypatch.setattr(g.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unauthorized launch"))
    with pytest.raises(ValueError, match="explicit"):
        g.supervise(tmp_path)


@pytest.mark.parametrize("fault", ["replace", "truncate", "rewrite", "symlink"])
def test_audit_tail_refuses_changes(tmp_path, fault):
    path = tmp_path / "audit"
    original = (RUN / "usb-io.jsonl").read_bytes().splitlines(keepends=True)[0]
    path.write_bytes(original)
    a = g.Audit(g.read(CAPTURE / "plan.json"))
    tail = g.AuditTail(path)
    tail.poll(a)
    if fault == "replace":
        path.rename(tmp_path / "old")
        path.write_bytes(original)
    if fault == "truncate":
        path.write_bytes(b"")
    if fault == "rewrite":
        path.write_bytes(original.replace(b"management", b"xxxxxxxxxx"))
    if fault == "symlink":
        path.rename(tmp_path / "old")
        path.symlink_to(tmp_path / "old")
    with pytest.raises(ValueError):
        tail.poll(a)


def test_unknown_host_clock_refuses(running):
    running.host_uncertainty_ns = None
    assert not running.eligible(*clocks(running))


def test_disarm_cannot_rearm(running):
    event = copy.deepcopy(running.running[2])
    event["event_id"] = "100"
    event["body"]["output_active"] = False
    running.accept("in", event, running.running[0], running.sequence + 1)
    event["event_id"] = "101"
    event["body"]["output_active"] = True
    running.accept("in", event, running.running[0], running.sequence + 2)
    assert not running.eligible(*clocks(running))


# These fixtures are explicitly synthetic and never establish a physical pass.


def save_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def rehash_run(run):
    path = run / "smoke-result.json"
    result = g.read(path)
    result["artifacts"] = [artifact(p) for p in sorted(run.rglob("*")) if p.is_file() and p != path]
    save_json(path, result)


@pytest.fixture
def valid_assessment(tmp_path):
    root = tmp_path / "valid synthetic assessment"
    run = root / "runs" / NEGATIVE.name
    shutil.copytree(NEGATIVE, run)
    for path in run.rglob("*.json"):
        path.write_text(path.read_text().replace(str(NEGATIVE), str(run)))
    plan = g.read(run / "requested-plan.json")
    save_json(root / "plan.json", plan)
    raw_rows = [g.loads(line) for line in (run / "usb-io.jsonl").read_bytes().splitlines()]
    incoming = [row for row in raw_rows if row["carrier"] == "wtp" and row["phase"] == "read"]
    original_wire = b"".join(bytes.fromhex(row["data_hex"]) for row in incoming)
    frames = g._frames(original_wire, "wtp", "in")
    old_pos = 0
    changed_end = None
    for frame in frames:
        old_pos += 16 + int.from_bytes(original_wire[old_pos + 8 : old_pos + 12], "big")
        if frame.get("event") == "JOB_STATE" and frame["body"]["state"] == "running":
            frame["body"]["output_active"] = True
            changed_end = old_pos
    wire = b""
    for frame in frames:
        payload = encode(frame)
        wire += (
            b"WTPF\x01\x01\x00\x00" + struct.pack(">II", len(payload), crc32c(payload)) + payload
        )
    assert len(wire) == len(original_wire) - 1
    position = old_pos = 0
    for row in incoming:
        old_count = len(bytes.fromhex(row["data_hex"]))
        count = old_count
        if old_pos < changed_end <= old_pos + old_count:
            count -= 1
        row["data_hex"] = wire[position : position + count].hex()
        position += count
        old_pos += old_count
    assert position == len(wire)
    raw = b"".join((json.dumps(row) + "\n").encode() for row in raw_rows)
    (run / "usb-io.jsonl").write_bytes(raw)
    a = g.Audit(plan, host_uncertainty_ns=100_000_000)
    prefix = b""
    for line in raw.splitlines(keepends=True):
        a.feed(line)
        prefix += line
        if a.running:
            break
    event_time = a.running[0]
    arm = next(f["body"] for f in a.acks.values() if f["op"] == "ARM")
    offset = int(arm["start_utc_ns"]) + 100_000_000 - event_time
    started = int(raw_rows[0]["monotonic_ns"]) - 100_000_000
    pd = g.plan_digest(plan)
    source = g.digest(DRAFT)
    inv = "a" * 32
    controller = dict(
        pid=100, ppid=90, uid=501, start="1", executable="/fixture/controller", argv=["fixture"]
    )
    p = g.read(run / "observation/rf-on-plan.json")
    dp = Path(p["metadata_path"] + ".execution.json")
    diag = g.read(dp)
    diag["capability_plan_sha256"] = capability_plan_sha256(p)
    diag["execution"]["return_code"] = -9
    save_json(dp, diag)
    target = dict(
        pid=101, ppid=100, uid=501, start="2", executable=p["helper"], argv=diag["arguments"]
    )
    intent = dict(
        run=str(run),
        plan_sha256=pd,
        controller=controller,
        target=target,
        helper_sha256=diag["helper"]["sha256"],
        job_id=a.job["job_id"],
        boot_id=a.boot,
        session_id=a.session,
        trigger_sequence=a.running[1],
        trigger=a.running[2],
        signal="SIGKILL",
        monotonic_ns=event_time + 1000,
        wall_offset_ns=offset,
        invocation_id=inv,
        source_sha256=source,
    )
    save_json(root / "signal-intent.json", intent)
    proof = dict(
        controller=controller,
        checked_monotonic_ns=event_time + 2000,
        checked_wall_ns=event_time + 2000 + offset,
        wall_offset_ns=offset,
        host_uncertainty_ns=100_000_000,
        # Independent oracle: fixture has 4.9s remaining; freshness is the limiting bound.
        valid_until_monotonic_ns=event_time + 250_000_000,
        audit=dict(
            bytes=len(prefix),
            sha256=__import__("hashlib").sha256(prefix).hexdigest(),
            sequence=a.sequence,
        ),
    )
    sent = dict(
        injected=True,
        target_pid=101,
        target=target,
        signal="SIGKILL",
        intent_sha256=g.digest(root / "signal-intent.json"),
        before_ns=event_time + 3000,
        after_ns=event_time + 4000,
        after_wall_ns=event_time + 4000 + offset,
        proof=proof,
        window_proven=True,
        reason="signal_returned",
    )
    save_json(root / "signal-outcome.json", sent)
    save_json(root / "injector-finished.json", sent)
    save_json(
        root / "invocation.json",
        dict(
            id=inv,
            source_sha256=source,
            plan_sha256=pd,
            started_ns=started,
            execution_kind="offline_fixture",
            host_boot_id="fixture-boot",
        ),
    )
    save_json(
        root / "host-preflight.json",
        dict(
            passed=True,
            plan_sha256=pd,
            candidate_sha256=source,
            sampled_monotonic_ns=started - 1000,
            host_boot_id="fixture-boot",
            clock=dict(
                state="synchronized",
                leap="normal",
                uncertainty_ns=100_000_000,
                sampled_monotonic_ns=started - 1000,
                wall_offset_ns=offset,
            ),
        ),
    )
    save_json(root / "resolved-gate.json", dict(plan_sha256=pd, capture_helper=diag["helper"]))
    save_json(
        root / "injector-ready.json",
        dict(
            pid=102,
            source_sha256=source,
            invocation_id=inv,
            plan_sha256=pd,
            platform="linux-pidfd",
            monotonic_ns=started + 1000,
            freshness_ns=250_000_000,
            clock_margin_ns=250_000_000,
        ),
    )
    save_json(
        root / "physical-launch-intent.json",
        dict(consumed=True, invocation_id=inv, plan_sha256=pd, monotonic_ns=started + 2000),
    )
    save_json(root / "controller.json", controller)
    save_json(root / "injector-bound.json", controller)
    save_json(
        root / "supervisor-finished.json",
        dict(
            invocation_id=inv,
            injector_returncode=0,
            runner_returncode=1,
            runner_reaped=True,
            errors=[],
            cancelled=False,
        ),
    )
    rehash_run(run)
    result = g.assess(root, offline_fixture=True)
    assert result["assessment"] == "passed", result
    return root


def fresh_assess(root, *, offline=True):
    args = [__import__("sys").executable, "-I", str(DRAFT), "assess", str(root)]
    if offline:
        args.append("--offline-fixture")
    result = subprocess.run(args, text=True, capture_output=True, timeout=10, cwd=root)
    assert result.returncode in (0, 1), result.stderr
    return result.returncode, json.loads(result.stdout)


def test_assessor_positive_in_fresh_process(valid_assessment):
    code, result = fresh_assess(valid_assessment)
    assert code == 0 and result["assessment"] == "passed"
    assert (
        result["execution_kind"] == "offline_fixture" and result["hardware_state"] == "unassessed"
    )
    assert result["bench_available"] is None and result["production_outcome"] == "cleanup_failed"


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_parent",
        "wrong_start",
        "wrong_signal",
        "impossible_time",
        "late_call",
        "clock_step",
        "missing_receipt",
        "wrong_source",
        "wrong_plan",
        "wrong_prefix_hash",
        "wrong_trigger",
        "unknown_clock",
        "clock_expired",
        "competing_timeout",
        "cleanup_promotion",
        "unreaped_runner",
        "supervisor_cancelled",
        "receipt_disagrees",
        "false_deadline",
        "abort_before_signal",
        "partial_prefix",
        "missing_abort",
        "fixture_promotion",
    ],
)
def test_isolated_corruption_from_valid_assessment(valid_assessment, mutation):
    root = valid_assessment
    run = next((root / "runs").glob("pico-*"))
    ip = root / "signal-intent.json"
    intent = g.read(ip)
    sp = root / "signal-outcome.json"
    sent = g.read(sp)
    if mutation == "wrong_parent":
        intent["target"]["ppid"] = 999
    elif mutation == "wrong_start":
        intent["target"]["start"] = "999"
    elif mutation == "wrong_signal":
        intent["signal"] = "SIGTERM"
    elif mutation == "impossible_time":
        intent["monotonic_ns"] = 0
    elif mutation == "late_call":
        sent["after_ns"] = sent["proof"]["valid_until_monotonic_ns"] + 1
        sent["after_wall_ns"] = sent["after_ns"] + sent["proof"]["wall_offset_ns"]
    elif mutation == "clock_step":
        sent["after_wall_ns"] += 20_000_001
    elif mutation == "wrong_prefix_hash":
        sent["proof"]["audit"]["sha256"] = "0" * 64
    elif mutation == "wrong_trigger":
        intent["trigger"]["body"]["output_active"] = False
    elif mutation == "unknown_clock":
        p = root / "host-preflight.json"
        value = g.read(p)
        value["clock"]["uncertainty_ns"] = None
        save_json(p, value)
    elif mutation == "clock_expired":
        p = root / "host-preflight.json"
        value = g.read(p)
        value["clock"]["sampled_monotonic_ns"] -= 60_000_000_001
        save_json(p, value)
    elif mutation == "wrong_source":
        intent["source_sha256"] = "0" * 64
    elif mutation == "wrong_plan":
        intent["plan_sha256"] = "0" * 64
    elif mutation == "competing_timeout":
        p = g.read(run / "observation/rf-on-plan.json")
        dp = Path(p["metadata_path"] + ".execution.json")
        value = g.read(dp)
        value["execution"]["timed_out"] = True
        save_json(dp, value)
        rehash_run(run)
    elif mutation == "cleanup_promotion":
        p = run / "smoke-result.json"
        value = g.read(p)
        value["cleanup_verified"] = True
        save_json(p, value)
    elif mutation in ("unreaped_runner", "supervisor_cancelled"):
        p = root / "supervisor-finished.json"
        value = g.read(p)
        value["runner_reaped" if mutation == "unreaped_runner" else "cancelled"] = (
            mutation != "unreaped_runner"
        )
        save_json(p, value)
    elif mutation == "false_deadline":
        sent["proof"]["valid_until_monotonic_ns"] += 1
    elif mutation == "abort_before_signal":
        a = g.Audit(g.read(root / "plan.json"))
        a.feed((run / "usb-io.jsonl").read_bytes())
        when = next(stamp for d, f, stamp, seq in a.frames if d == "out" and f["op"] == "ABORT")
        sent["after_ns"] = when + 1
        sent["after_wall_ns"] = when + 1 + sent["proof"]["wall_offset_ns"]
    elif mutation == "partial_prefix":
        raw = (run / "usb-io.jsonl").read_bytes()
        sent["proof"]["audit"]["bytes"] -= 1
        sent["proof"]["audit"]["sha256"] = (
            __import__("hashlib").sha256(raw[: sent["proof"]["audit"]["bytes"]]).hexdigest()
        )
    elif mutation == "missing_abort":
        p = run / "observation/transaction.json"
        value = g.read(p)
        value["abort"] = None
        save_json(p, value)
        rehash_run(run)
    elif mutation == "fixture_promotion":
        p = root / "invocation.json"
        value = g.read(p)
        value["execution_kind"] = "physical"
        save_json(p, value)
    save_json(ip, intent)
    # Rehash dependent receipt binding to exercise semantics, not just a stale digest.
    sent["intent_sha256"] = g.digest(ip)
    save_json(sp, sent)
    save_json(root / "injector-finished.json", sent)
    if mutation == "receipt_disagrees":
        p = root / "injector-finished.json"
        value = g.read(p)
        value["target_pid"] = 999
        save_json(p, value)
    if mutation == "missing_receipt":
        sp.unlink()
    code, result = fresh_assess(root, offline=mutation != "fixture_promotion")
    assert code == 1 and result["assessment"] != "passed", result


@pytest.mark.parametrize("cause", ["stale", "clock_step", "lease"])
def test_timing_refusal_is_permanent(running, cause):
    mono, wall, offset = clocks(running)
    if cause == "stale":
        running.eligible(mono + 250_000_001, wall + 250_000_001, offset)
    elif cause == "clock_step":
        running.eligible(mono, wall + 20_000_001, offset)
    else:
        claim = next(f for f in running.acks.values() if f["op"] == "CLAIM")
        original = claim["body"]["expires_monotonic_ns"]
        claim["body"]["expires_monotonic_ns"] = "0"
        running.eligible(mono, wall, offset)
        claim["body"]["expires_monotonic_ns"] = original
    assert running.disarmed and not running.eligible(mono, wall, offset)


@pytest.mark.parametrize("op", ["CLAIM", "ARM"])
def test_second_control_attempt_disarms(running, op):
    request = copy.deepcopy(next(r for r in running.requests.values() if r["op"] == op))
    request["request_id"] = "f" * 32
    running.accept("out", request, running.last_ns, running.sequence + 1)
    assert not running.eligible(*clocks(running))


def test_direct_watcher_requires_explicit_enablement(tmp_path):
    with pytest.raises(ValueError, match="Explicit"):
        g.watch(tmp_path)


def test_fifo_audit_refused_without_blocking(tmp_path):
    path = tmp_path / "audit"
    g.os.mkfifo(path)
    with pytest.raises(ValueError, match="regular"):
        g.AuditTail(path).poll(g.Audit({}))


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "stale",
        "unknown_clock",
        "wrong_boot",
        "wrong_candidate",
        "wrong_plan",
        "clock_step",
        "wrong_helper",
    ],
)
def test_preflight_is_current_and_bound(monkeypatch, tmp_path, fault):
    plan = g.read(CAPTURE / "plan.json")
    plan["configuration"]["receiver"]["capture_helper"] = str(DRAFT)
    resolution = dict(
        plan_sha256=g.plan_digest(plan),
        capture_helper=dict(path=str(DRAFT), sha256=g.digest(DRAFT)),
    )
    now = 100_000_000_000
    wall = 1_700_000_000_000_000_000
    pre = dict(
        passed=True,
        plan_sha256=g.plan_digest(plan),
        candidate_sha256=g.digest(DRAFT),
        host_boot_id="boot",
        sampled_monotonic_ns=now,
        clock=dict(
            state="synchronized",
            leap="normal",
            uncertainty_ns=1,
            sampled_monotonic_ns=now,
            wall_offset_ns=wall - now,
        ),
    )
    if fault == "stale":
        pre["sampled_monotonic_ns"] -= 60_000_000_001
    elif fault == "unknown_clock":
        pre["clock"]["uncertainty_ns"] = None
    elif fault == "wrong_boot":
        pre["host_boot_id"] = "other"
    elif fault == "wrong_candidate":
        pre["candidate_sha256"] = "0" * 64
    elif fault == "wrong_plan":
        pre["plan_sha256"] = "0" * 64
    elif fault == "clock_step":
        pre["clock"]["wall_offset_ns"] -= 20_000_001
    elif fault == "wrong_helper":
        plan["configuration"]["receiver"]["capture_helper"] = "/other"
    g.write(tmp_path / "host-preflight.json", pre)
    monkeypatch.setattr(g, "platform_check", lambda: None)
    monkeypatch.setattr(g, "validate_clock_evidence", lambda pre: None)
    monkeypatch.setattr(g.time, "monotonic_ns", lambda: now)
    monkeypatch.setattr(g.time, "time_ns", lambda: wall)
    real = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **kw: (
            "boot" if str(p) == "/proc/sys/kernel/random/boot_id" else real(p, *a, **kw)
        ),
    )
    if fault is None:
        assert g.validate_preflight(tmp_path, plan, resolution) == pre
    else:
        with pytest.raises(ValueError):
            g.validate_preflight(tmp_path, plan, resolution)


@pytest.mark.parametrize(
    "fault", ["missing", "no_injection", "late", "watcher_failure", "production_success"]
)
def test_fast_runner_exit_cannot_bypass_injection_checks(monkeypatch, tmp_path, fault):
    launched, runner, watcher = supervisor_fixture(monkeypatch, tmp_path)
    fake = g.subprocess.Popen

    def popen(*args, **kwargs):
        child = fake(*args, **kwargs)
        if len(launched) == 2:
            path = tmp_path / "injector-finished.json"
            if fault == "missing":
                path.unlink()
            elif fault in ("no_injection", "late"):
                save_json(
                    path, dict(injected=fault != "no_injection", window_proven=fault != "late")
                )
            elif fault == "watcher_failure":
                watcher.returncode = 1
            elif fault == "production_success":
                runner.returncode = 0
        return child

    monkeypatch.setattr(g.subprocess, "Popen", popen)
    result = g.supervise(tmp_path, enable_physical=True, operator="offline-test")
    assert result["errors"] and result["assessment"] == "pending"
