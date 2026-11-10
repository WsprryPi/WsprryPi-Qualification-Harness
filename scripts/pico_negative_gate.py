"""Test-only local receiver-loss supervisor; production runner remains unchanged."""

import argparse
import collections
import copy
import csv
import hashlib
import json
import os
import select
import signal
import stat
import subprocess
import sys
import time
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from wsprrypi_qualification.pico_audit import _frames, validate_wire_audit
from wsprrypi_qualification.pico_campaign import plan_digest
from wsprrypi_qualification.pico_results import settings_match
from wsprrypi_qualification.pico_smoke import (
    check_scope,
    validate_management_snapshots,
    validate_retained,
)
from wsprrypi_qualification.pico_wire import loads, validate_contract

FRESH_NS = 250_000_000
CLOCK_MARGIN_NS = 250_000_000
STEP_NS = 20_000_000
PREFLIGHT_AGE_NS = 60_000_000_000


def clock_from_observation(observation):
    """Recompute chrony's documented UTC error estimate, projected through freshness.

    This is conditional on chrony's reference being correct, not calibration proof.
    Chronyc CSV tracking: reference, name, stratum, reference time, system offset,
    last offset, RMS offset, frequency, residual frequency, skew, delay, dispersion,
    update interval, leap. No clock or service is changed.
    """
    fields = next(csv.reader([observation["tracking_csv"]]))
    if len(fields) != 14 or fields[13] != "Normal" or not 1 <= int(fields[2]) <= 15:
        raise ValueError("Chrony has no synchronized normal-leap reference")
    if fields[0].upper() in {"00000000", "7F7F0101"}:
        raise ValueError("Absent or local unsynchronized chrony reference")
    values = [Decimal(v) for v in fields[3:13]]
    if not all(v.is_finite() for v in values):
        raise ValueError("Non-finite chrony observation")
    before, after = observation["before_ns"], observation["after_ns"]
    wall = observation["wall_ns"]
    if any(type(v) is not int or v <= 0 for v in (before, after, wall)):
        raise ValueError("Invalid observation timestamps")
    elapsed = after - before
    if not 0 <= elapsed <= 2_000_000_000:
        raise ValueError("Clock observation exceeded its two-second bound")
    reference, system, _, _, frequency, residual, skew, delay, dispersion, interval = values
    if min(skew, delay, dispersion, interval) < 0:
        raise ValueError("Negative chrony uncertainty term")
    # Chrony's dispersion already accounts for reference age. Do not introduce
    # another arbitrary reference-update-age gate; use its reported error budget.
    error = abs(system) + dispersion + delay / 2
    projection = (abs(frequency) + abs(residual) + skew) * Decimal(60) / Decimal(10**6)
    if reference > Decimal(wall) / Decimal(10**9) + error + Decimal(elapsed) / 10**9:
        raise ValueError("Chrony reference time contradicts its clock error estimate")
    uncertainty = int(((error + projection) * 10**9).to_integral_value(rounding=ROUND_CEILING))
    return dict(
        state="synchronized",
        leap="normal",
        uncertainty_ns=uncertainty + elapsed,
        sampled_monotonic_ns=after,
        wall_offset_ns=wall - after,
    )


def collect_clock(chronyc):
    executable = Path(chronyc).resolve(strict=True)
    # Version is provenance, never a whitelist or a clock-eligibility decision.
    version = "unavailable"
    try:
        reported = subprocess.run(
            [str(executable), "-v"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        if reported.returncode == 0 and reported.stdout.strip():
            version = reported.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    before = time.monotonic_ns()
    result = subprocess.run(
        [str(executable), "-n", "-c", "tracking"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    wall = time.time_ns()
    after = time.monotonic_ns()
    observation = dict(
        tool=dict(path=str(executable), sha256=digest(executable), version=version),
        tracking_csv=result.stdout.strip(),
        before_ns=before,
        after_ns=after,
        wall_ns=wall,
    )
    return observation, clock_from_observation(observation)


def prepare(plan_path, root, chronyc, *, observe_host=False):
    """Prepare local process/clock inputs, never device admission or RF authority."""
    if not observe_host:
        raise ValueError("Explicit read-only host observation enablement is required")
    platform_check()
    plan = read(plan_path)
    check_scope(plan)
    helper = Path(plan["configuration"]["receiver"]["capture_helper"]).resolve(strict=True)
    if str(helper) != plan["configuration"]["receiver"]["capture_helper"]:
        raise ValueError("Capture helper must use its resolved absolute path")
    if plan["configuration"]["receiver"]["driver"] == "fixture":
        raise ValueError("Synthetic receiver cannot prepare physical inputs")
    root = Path(root).resolve()
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    observation, clock = collect_clock(chronyc)
    pd = plan_digest(plan)
    write(root / "plan.json", plan)
    write(
        root / "resolved-gate.json",
        dict(
            plan_sha256=pd,
            capture_helper=dict(path=str(helper), sha256=digest(helper)),
        ),
    )
    write(
        root / "host-preflight.json",
        dict(
            passed=True,
            scope="local_process_and_clock_only",
            execution_kind="physical_inputs",
            device_admission="pending_production_runner",
            plan_sha256=pd,
            candidate_sha256=digest(__file__),
            host_boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            sampled_monotonic_ns=time.monotonic_ns(),
            clock=clock,
            observation=observation,
        ),
    )
    return dict(
        prepared=True,
        root=str(root),
        plan_sha256=pd,
        device_admission="pending",
        rf_authorized=False,
        qualification_claim=False,
    )


def validate_clock_evidence(pre):
    if (
        pre.get("scope") != "local_process_and_clock_only"
        or pre.get("execution_kind") != "physical_inputs"
    ):
        raise ValueError("Preparation scope or execution kind is not physical input preparation")
    if pre.get("device_admission") != "pending_production_runner":
        raise ValueError("Preparation cannot claim device admission")
    if clock_from_observation(pre["observation"]) != pre["clock"]:
        raise ValueError("Host clock summary differs from retained observation")


def platform_check():
    if sys.platform != "linux" or not all(
        (
            hasattr(os, "pidfd_open"),
            hasattr(signal, "pidfd_send_signal"),
            hasattr(signal, "pthread_sigmask"),
        )
    ):
        raise ValueError("Linux pidfd and signal masking are required before readiness")
    fd = os.pidfd_open(os.getpid())
    os.close(fd)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return loads(Path(path).read_bytes())


def write(path, value):
    path = Path(path)
    pending = path.with_name(".pending-" + os.urandom(16).hex())
    try:
        with pending.open("x") as f:
            json.dump(value, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.link(pending, path)  # Atomic publication without replacing an existing receipt.
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        pending.unlink(missing_ok=True)


def identity(pid):
    p = Path("/proc") / str(pid)
    stat = (p / "stat").read_text().rsplit(")", 1)[1].split()
    return dict(
        pid=pid,
        ppid=int(stat[1]),
        start=stat[19],
        uid=p.stat().st_uid,
        executable=str((p / "exe").resolve(strict=True)),
        argv=(p / "cmdline").read_bytes().rstrip(b"\0").decode().split("\0"),
    )


class HeldProcess:
    def __init__(self, pid, expected):
        self.fd = os.pidfd_open(pid)
        self.pid = pid
        self.expected = expected
        try:
            self.verify()
        except BaseException:
            self.close()
            raise

    def alive(self):
        return not select.select([self.fd], [], [], 0)[0]

    def verify(self):
        if not self.alive() or identity(self.pid) != self.expected:
            raise ValueError("Process identity or lifetime changed")

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class OneSignal:
    def __init__(self):
        self.used = False

    def send(self, target, receipt, intent, guard):
        if self.used:
            raise ValueError("Signal allowance consumed")
        self.used = True
        target.verify()
        write(receipt, intent)
        target.verify()
        proof = guard()  # After fsync; reads latest state and checks cancellation.
        before = time.monotonic_ns()
        if not proof["checked_monotonic_ns"] <= before <= proof["valid_until_monotonic_ns"]:
            raise ValueError("Signal window expired after durable intent")
        signal.pidfd_send_signal(target.fd, signal.SIGKILL)
        after = time.monotonic_ns()
        return dict(
            injected=True,
            target_pid=target.pid,
            signal="SIGKILL",
            intent_sha256=digest(receipt),
            before_ns=before,
            after_ns=after,
            after_wall_ns=time.time_ns(),
            target=copy.deepcopy(getattr(target, "expected", None)),
            proof=proof,
            window_proven=after <= proof["valid_until_monotonic_ns"],
        )


def capture_argv(p):
    return [
        p["helper"],
        "--enable-physical-sdr",
        p["driver"],
        p["serial"],
        f"{p['center_frequency_hz']:g}",
        str(p["sample_count"]),
        f"{p['gain_db']:g}",
        str(p["sample_rate_hz"]),
        str(p["bandwidth_hz"]),
        str(p["channel"]),
        str(p["agc"]).lower(),
        str(p["bias_tee"]).lower(),
        str(p["read_timeout_us"]),
        f"{p['maximum_elapsed_s']:g}",
        p["output_path"],
        p["metadata_path"],
    ]


def candidate_matches(i, controller, p, helper_sha):
    return (
        i["ppid"] == controller["pid"]
        and i["uid"] == controller["uid"]
        and i["executable"] == str(Path(p["helper"]).resolve())
        and len(i["argv"]) == 17
        and i["argv"][:-1] == capture_argv(p)
        and i["argv"][-1] == Path(p["output_path"]).stem
        and digest(i["executable"]) == helper_sha
    )


def capture_matches_configuration(p, plan):
    receiver = plan["configuration"]["receiver"]
    return (
        all(
            p[key] == receiver[key]
            for key in (
                "driver",
                "serial",
                "channel",
                "sample_rate_hz",
                "bandwidth_hz",
                "center_frequency_hz",
                "gain_db",
            )
        )
        and p["helper"] == receiver["capture_helper"]
        and p["agc"] is False
        and p["bias_tee"] is False
        and p["sample_format"] == "CF32"
        and type(p["sample_count"]) is int
        and 0 < p["sample_count"] <= 128 * 1024 * 1024 // 8
    )


def choose(candidates):
    if len(candidates) != 1:
        raise ValueError("Capture child selection is missing or ambiguous")
    return candidates[0]


class Audit:
    def __init__(self, plan, host_uncertainty_ns=None):
        self.plan = plan
        self.partial = b""
        self.buffers = {"out": b"", "in": b""}
        self.frames = []
        self.sequence = 0
        self.last_ns = 0
        self.requests = {}
        self.acks = {}
        self.events = set()
        self.running = None
        self.disarmed = False
        self.session = None
        self.boot = None
        self.job = None
        self.host_uncertainty_ns = host_uncertainty_ns
        self.event_id = -1

    def feed(self, data):
        self.partial += data
        while b"\n" in self.partial:
            line, self.partial = self.partial.split(b"\n", 1)
            r = loads(line)
            validate_contract(r, "usb-io")
            if r["sequence"] != self.sequence + 1 or int(r["monotonic_ns"]) < self.last_ns:
                self.disarmed = True
                raise ValueError("Audit ordering changed")
            self.sequence = r["sequence"]
            self.last_ns = int(r["monotonic_ns"])
            # Bounded empty polls are normal while waiting for a scheduled job.
            if r["phase"] in ("read_failed", "write_failed") or (
                r["error"] is not None and r["phase"] != "read_timeout"
            ):
                self.disarmed = True
            if r["carrier"] != "wtp" or r["phase"] not in ("write_requested", "read"):
                continue
            direction = "out" if r["phase"] == "write_requested" else "in"
            self.buffers[direction] += bytes.fromhex(r["data_hex"])
            while len(self.buffers[direction]) >= 16:
                b = self.buffers[direction]
                n = int.from_bytes(b[8:12], "big")
                if n > 65536 or n == 0:
                    raise ValueError("Bad frame size")
                if len(b) < 16 + n:
                    break
                frame = _frames(b[: 16 + n], "wtp", direction)[0]
                self.buffers[direction] = b[16 + n :]
                self.frames.append((direction, frame, self.last_ns, r["sequence"]))
                self.accept(direction, frame, self.last_ns, r["sequence"])

    def accept(self, d, f, stamp, sequence):
        if self.session is None:
            self.session = f["session_id"]
        if f["session_id"] != self.session:
            self.disarmed = True
            raise ValueError("Mixed WTP session")
        if d == "out":
            if f["type"] != "request" or f["request_id"] in self.requests:
                raise ValueError("Duplicate or invalid request")
            self.requests[f["request_id"]] = f
            if (
                f["op"] in ("CLAIM", "ARM")
                and sum(r["op"] == f["op"] for r in self.requests.values()) != 1
            ):
                self.disarmed = True
            if f["op"] in ("ABORT", "RELEASE"):
                self.disarmed = True
            if f["op"] == "LOAD":
                if self.job is not None:
                    raise ValueError("Multiple LOAD attempts")
                expected = dict(self.plan["modes"][0]["jobs"][0])
                expected["job_id"] = f["body"]["job_id"]
                if expected != f["body"]:
                    raise ValueError("Loaded job differs from plan")
                self.job = f["body"]
            return
        if f["type"] == "response":
            req = self.requests.get(f["request_id"])
            if req is None or req["op"] != f["op"] or f["request_id"] in self.acks:
                raise ValueError("Unbound response")
            self.acks[f["request_id"]] = f
            if not f["ok"]:
                self.disarmed = True
            if f["ok"] and f["op"] == "CLAIM" and f["body"]["owner_id"] != req["body"]["owner_id"]:
                self.disarmed = True
            if f["ok"] and f["op"] == "STATUS" and self.running is not None:
                body = f["body"]
                owner = next(
                    (v["body"]["owner_id"] for v in self.requests.values() if v["op"] == "CLAIM"),
                    None,
                )
                if (
                    body["boot_id"] != self.boot
                    or body["owner_id"] != owner
                    or body["job_id"] != self.job["job_id"]
                    or body["state"] != "running"
                    or not body["output_active"]
                ):
                    self.disarmed = True
            if f["op"] == "HELLO" and f["ok"]:
                body = f["body"]
                if body["device_id"] != self.plan["configuration"]["pico"]["device_id"]:
                    raise ValueError("Wrong device")
                if self.boot is not None and self.boot != body["boot_id"]:
                    raise ValueError("Boot changed")
                self.boot = body["boot_id"]
            return
        key = (f["boot_id"], f["event_id"])
        if f["boot_id"] != self.boot or key in self.events or int(f["event_id"]) <= self.event_id:
            self.disarmed = True
            raise ValueError("Wrong boot or unordered event")
        self.event_id = int(f["event_id"])
        self.events.add(key)
        if f["event"] != "JOB_STATE":
            self.disarmed = True
        if f["event"] == "JOB_STATE" and self.job is not None:
            b = f["body"]
            if b.get("job_id") == self.job["job_id"]:
                if b["state"] == "running" and b["output_active"]:
                    self.running = (stamp, sequence, f)
                if b["state"] not in ("loaded", "armed", "running"):
                    self.disarmed = True
                if self.running is not None and (b["state"] != "running" or not b["output_active"]):
                    self.disarmed = True
            else:
                self.disarmed = True

    def eligible(self, now_mono, now_wall, wall_offset):
        if self.disarmed or self.running is None or self.job is None:
            return False
        if self.partial or any(self.buffers.values()):
            return False
        if type(self.host_uncertainty_ns) is not int or self.host_uncertainty_ns < 0:
            self.disarmed = True
            return False
        age = now_mono - self.running[0]
        if not 0 <= age <= FRESH_NS or abs(now_wall - now_mono - wall_offset) > STEP_NS:
            self.disarmed = True
            return False
        acks = {f["op"]: f for f in self.acks.values() if f["ok"]}
        if not all(op in acks for op in ("LOAD", "ARM", "CLAIM")):
            return False
        claim = acks["CLAIM"]["body"]
        claim_request = next(r for r in self.requests.values() if r["op"] == "CLAIM")["body"]
        if (
            claim["owner_id"] != claim_request["owner_id"]
            or not 5000 <= claim["granted_lease_ms"] <= claim_request["lease_ms"]
        ):
            self.disarmed = True
            return False
        arm = acks["ARM"]["body"]
        load = acks["LOAD"]["body"]
        if arm["job_id"] != self.job["job_id"] or load["job_id"] != self.job["job_id"]:
            return False
        if arm["state"] != "armed" or load["state"] != "loaded":
            return False
        req = next(r for r in self.requests.values() if r["op"] == "ARM")
        if arm["start_utc_ns"] != req["body"]["start_utc_ns"]:
            return False
        clock = arm["clock"]
        if (
            clock["state"] != "synchronized"
            or clock["leap"] != "normal"
            or int(clock["uncertainty_ns"]) > 100_000_000
        ):
            return False
        device_now = int(clock["monotonic_now_ns"]) + now_wall - int(clock["utc_now_ns"])
        if device_now + CLOCK_MARGIN_NS + self.host_uncertainty_ns >= int(
            claim["expires_monotonic_ns"]
        ):
            self.disarmed = True
            return False
        start = int(arm["start_utc_ns"])
        end = start + int(self.job["total_duration_ns"])
        valid = (
            start + self.host_uncertainty_ns <= now_wall + CLOCK_MARGIN_NS
            and end - (now_mono + wall_offset) - CLOCK_MARGIN_NS - self.host_uncertainty_ns
            >= 2_000_000_000
        )
        if not valid:
            self.disarmed = True
        return valid

    def valid_until(self, wall_offset):
        acks = {f["op"]: f["body"] for f in self.acks.values() if f["ok"]}
        arm = acks["ARM"]
        clock = arm["clock"]
        end = int(arm["start_utc_ns"]) + int(self.job["total_duration_ns"])
        lease_utc = (
            int(acks["CLAIM"]["expires_monotonic_ns"])
            - int(clock["monotonic_now_ns"])
            + int(clock["utc_now_ns"])
        )
        return min(
            self.running[0] + FRESH_NS,
            end - wall_offset - CLOCK_MARGIN_NS - self.host_uncertainty_ns - 2_000_000_000,
            lease_utc - wall_offset - CLOCK_MARGIN_NS - self.host_uncertainty_ns - 1,
        )


class AuditTail:
    """Bound one append-only regular audit file, including its consumed prefix."""

    def __init__(self, path):
        self.path = Path(path)
        self.inode = None
        self.prefix = b""

    def poll(self, audit):
        if self.path.is_symlink():
            raise ValueError("Symlink audit")
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            observed = os.fstat(f.fileno())
            if not stat.S_ISREG(observed.st_mode):
                raise ValueError("Audit must be a regular file")
            key = (observed.st_dev, observed.st_ino)
            if self.inode is not None and key != self.inode:
                raise ValueError("Audit replaced")
            if not 0 <= observed.st_size <= 16 * 1024 * 1024:
                raise ValueError("Audit size limit")
            data = f.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024 or not data.startswith(self.prefix):
            raise ValueError("Audit truncated or changed")
        audit.feed(data[len(self.prefix) :])
        self.prefix = data
        self.inode = key
        return dict(
            bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), sequence=audit.sequence
        )


def validate_preflight(root, plan, resolution):
    platform_check()
    pre = read(root / "host-preflight.json")
    validate_clock_evidence(pre)
    now = time.monotonic_ns()
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if (
        pre.get("passed") is not True
        or pre.get("plan_sha256") != plan_digest(plan)
        or pre.get("candidate_sha256") != digest(__file__)
        or pre.get("host_boot_id") != boot
        or not 0 <= now - pre["sampled_monotonic_ns"] <= PREFLIGHT_AGE_NS
    ):
        raise ValueError("Preflight is missing, stale, or not bound to this candidate/plan/boot")
    clock = pre["clock"]
    if (
        clock["state"] != "synchronized"
        or clock["leap"] != "normal"
        or type(clock["uncertainty_ns"]) is not int
        or clock["uncertainty_ns"] < 0
        or not 0 <= now - clock["sampled_monotonic_ns"] <= PREFLIGHT_AGE_NS
        or abs(time.time_ns() - now - clock["wall_offset_ns"]) > STEP_NS
    ):
        raise ValueError("Current bounded host clock evidence is required")
    if resolution["plan_sha256"] != plan_digest(plan):
        raise ValueError("Resolved plan differs")
    helper = resolution["capture_helper"]
    if helper["path"] != plan["configuration"]["receiver"]["capture_helper"]:
        raise ValueError("Helper differs from selected receiver")
    if digest(helper["path"]) != helper["sha256"]:
        raise ValueError("Helper changed")
    return pre


def watch(root, *, enable_physical=False):
    if enable_physical is not True:
        raise ValueError("Explicit signal enablement is required")
    root = Path(root)
    controller = target = supervisor = None
    one = OneSignal()
    outcome = dict(injected=False, reason="window_or_controller_end")
    try:
        plan = read(root / "plan.json")
        resolution = read(root / "resolved-gate.json")
        pre = validate_preflight(root, plan, resolution)
        invocation = read(root / "invocation.json")
        if invocation["source_sha256"] != digest(__file__) or invocation[
            "plan_sha256"
        ] != plan_digest(plan):
            raise ValueError("Invocation binding changed")
        if invocation["host_boot_id"] != pre["host_boot_id"]:
            raise ValueError("Invocation belongs to another host boot")
        supervisor = HeldProcess(invocation["supervisor"]["pid"], invocation["supervisor"])
        offset = pre["clock"]["wall_offset_ns"]
        audit = Audit(plan, host_uncertainty_ns=pre["clock"]["uncertainty_ns"])
        deadline = time.monotonic() + 120
        tail = None
        run = None
        write(
            root / "injector-ready.json",
            dict(
                pid=os.getpid(),
                source_sha256=digest(__file__),
                invocation_id=invocation["id"],
                plan_sha256=plan_digest(plan),
                monotonic_ns=time.monotonic_ns(),
                platform="linux-pidfd",
                freshness_ns=FRESH_NS,
                clock_margin_ns=CLOCK_MARGIN_NS,
            ),
        )
        while time.monotonic() < deadline:
            if (root / "disarm").exists():
                outcome["reason"] = "supervisor_disarm"
                break
            supervisor.verify()
            if controller is None:
                if not (root / "controller.json").exists():
                    time.sleep(0.01)
                    continue
                bound = read(root / "controller.json")
                controller = HeldProcess(bound["pid"], bound)
                write(root / "injector-bound.json", bound)
            controller.verify()
            runs = list((root / "runs").glob("pico-*"))
            if len(runs) > 1:
                raise ValueError("Multiple run directories")
            if not runs:
                time.sleep(0.01)
                continue
            if run is None:
                run = runs[0]
                if run.is_symlink():
                    raise ValueError("Symlink run")
            elif run != runs[0]:
                raise ValueError("Run directory changed")
            path = run / "usb-io.jsonl"
            if not path.exists():
                if tail is not None:
                    raise ValueError("Audit disappeared")
                time.sleep(0.01)
                continue
            if tail is None:
                tail = AuditTail(path)
            tail.poll(audit)
            if audit.disarmed:
                break
            if (run / "observation/receiver-error.json").exists():
                outcome["reason"] = "receiver_failed_before_injection"
                break
            if audit.eligible(time.monotonic_ns(), time.time_ns(), offset):
                p = read(run / "observation/rf-on-plan.json")
                matches = []
                if not capture_matches_configuration(p, plan):
                    raise ValueError("Capture differs from selected receiver")
                for key in ("output_path", "metadata_path"):
                    if Path(p[key]).parent.resolve() != (run / "observation").resolve():
                        raise ValueError("Capture escapes run")
                if p["helper"] != resolution["capture_helper"]["path"]:
                    raise ValueError("Wrong capture helper")
                for entry in Path("/proc").iterdir():
                    if not entry.name.isdigit():
                        continue
                    try:
                        i = identity(int(entry.name))
                        if candidate_matches(
                            i, controller.expected, p, resolution["capture_helper"]["sha256"]
                        ):
                            matches.append(i)
                    except (OSError, ValueError):
                        continue
                selected = choose(matches)
                target = HeldProcess(selected["pid"], selected)
                intent = dict(
                    run=str(run),
                    plan_sha256=plan_digest(plan),
                    controller=controller.expected,
                    target=target.expected,
                    helper_sha256=resolution["capture_helper"]["sha256"],
                    job_id=audit.job["job_id"],
                    boot_id=audit.boot,
                    session_id=audit.session,
                    trigger_sequence=audit.running[1],
                    trigger=audit.running[2],
                    signal="SIGKILL",
                    monotonic_ns=time.monotonic_ns(),
                    wall_offset_ns=offset,
                    invocation_id=invocation["id"],
                    source_sha256=digest(__file__),
                )

                def guard(
                    controller=controller, target=target, p=p, intent=intent, tail=tail, run=run
                ):
                    if (root / "disarm").exists():
                        raise ValueError("Disarmed")
                    supervisor.verify()
                    controller.verify()
                    target.verify()
                    if (
                        digest(__file__) != invocation["source_sha256"]
                        or digest(p["helper"]) != intent["helper_sha256"]
                    ):
                        raise ValueError("Candidate or helper changed")
                    snapshot = tail.poll(audit)
                    if (root / "disarm").exists() or (
                        run / "observation/receiver-error.json"
                    ).exists():
                        raise ValueError("Disarmed or receiver already failed")
                    mono = time.monotonic_ns()
                    wall = time.time_ns()
                    if not 0 <= mono - pre["clock"]["sampled_monotonic_ns"] <= PREFLIGHT_AGE_NS:
                        raise ValueError("Host clock observation expired")
                    if not audit.eligible(mono, wall, offset):
                        raise ValueError("Final eligibility refused")
                    return dict(
                        checked_monotonic_ns=mono,
                        checked_wall_ns=wall,
                        wall_offset_ns=offset,
                        valid_until_monotonic_ns=audit.valid_until(offset),
                        audit=snapshot,
                        host_uncertainty_ns=audit.host_uncertainty_ns,
                        controller=controller.expected,
                    )

                outcome = one.send(target, root / "signal-intent.json", intent, guard)
                outcome["reason"] = "signal_returned"
                write(root / "signal-outcome.json", outcome)
                break
            time.sleep(0.01)
    except BaseException as error:
        outcome = dict(
            injected=False,
            reason=type(error).__name__,
            detail=str(error),
            signal_allowance_consumed=one.used,
        )
    finally:
        for process in (target, controller, supervisor):
            if process:
                process.close()
        write(root / "injector-finished.json", outcome)
    return outcome


def supervise(root, *, enable_physical=False, operator=""):
    if enable_physical is not True:
        raise ValueError("Separate explicit physical negative-gate enablement is required")
    if not isinstance(operator, str) or not operator.strip():
        raise ValueError("Explicit operator identity is required")
    root = Path(root)
    plan = read(root / "plan.json")
    check_scope(plan)
    if plan["configuration"]["receiver"]["driver"] == "fixture":
        raise ValueError("Synthetic receiver cannot launch the physical runner")
    resolution = read(root / "resolved-gate.json")
    pre = validate_preflight(root, plan, resolution)
    # Refuse a stale/partly used directory before constructing any subprocess.
    for name in (
        "invocation.json",
        "injector-ready.json",
        "controller.json",
        "physical-launch-intent.json",
        "disarm",
        "runs",
    ):
        if (root / name).exists():
            raise ValueError("Run directory already used: " + name)
    invocation = dict(
        id=os.urandom(16).hex(),
        source_sha256=digest(__file__),
        plan_sha256=plan_digest(plan),
        supervisor=identity(os.getpid()),
        started_ns=time.monotonic_ns(),
        execution_kind="physical",
        host_boot_id=pre["host_boot_id"],
    )
    write(root / "invocation.json", invocation)
    runner = watcher = None
    start = time.monotonic()
    cancelled = False
    cancel_sent = False
    errors = []
    code = None

    def disarm():
        try:
            if not (root / "disarm").exists():
                write(root / "disarm", dict(monotonic_ns=time.monotonic_ns()))
        except FileExistsError:
            pass

    def forward_cancel():
        nonlocal cancel_sent
        if runner is not None and runner.poll() is None and not cancel_sent:
            cancel_sent = True
            runner.send_signal(signal.SIGINT)

    def cancel(signum=None, frame=None):
        nonlocal cancelled
        cancelled = True
        try:
            disarm()
        except Exception as error:
            errors.append("disarm: " + str(error))
        finally:
            try:
                forward_cancel()
            except Exception as error:
                errors.append("runner cancellation: " + str(error))

    previous = {s: signal.signal(s, cancel) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        watcher = subprocess.Popen(
            [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "watch",
                str(root),
                "--enable-physical-negative-gate",
            ],
            stdout=(root / "injector.stdout").open("x"),
            stderr=(root / "injector.stderr").open("x"),
        )
        while not (root / "injector-ready.json").exists():
            if cancelled or watcher.poll() is not None or time.monotonic() - start > 3:
                raise ValueError("Injector not ready")
            time.sleep(0.01)
        ready = read(root / "injector-ready.json")
        if (
            cancelled
            or watcher.poll() is not None
            or ready["pid"] != watcher.pid
            or ready["source_sha256"] != digest(__file__)
            or ready["invocation_id"] != invocation["id"]
            or ready["plan_sha256"] != plan_digest(plan)
            or ready["platform"] != "linux-pidfd"
            or not invocation["started_ns"] <= ready["monotonic_ns"] <= time.monotonic_ns()
            or time.monotonic_ns() - ready["monotonic_ns"] > 3_000_000_000
        ):
            raise ValueError("Cancelled, stale, or invalid injector readiness")
        write(
            root / "physical-launch-intent.json",
            dict(
                plan_sha256=plan_digest(plan),
                consumed=True,
                invocation_id=invocation["id"],
                monotonic_ns=time.monotonic_ns(),
            ),
        )
        args = [
            sys.executable,
            "-I",
            "-m",
            "wsprrypi_qualification.pico_smoke",
            "run",
            "--plan",
            str(root / "plan.json"),
            "--output-parent",
            str(root / "runs"),
            "--operator",
            operator,
            "--expected-plan-sha256",
            plan_digest(plan),
            "--enable-rf",
        ]
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            if cancelled or (root / "disarm").exists():
                raise ValueError("Cancelled before launch")
            runner = subprocess.Popen(
                args,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=(root / "runner.stdout").open("x"),
                stderr=(root / "runner.stderr").open("x"),
            )
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        if cancelled:
            forward_cancel()
        write(root / "controller.json", identity(runner.pid))
        while runner.poll() is None:
            if cancelled:
                break
            if time.monotonic() - start >= 300:
                raise TimeoutError("Outer runner deadline")
            if watcher.poll() is not None:
                finished = read(root / "injector-finished.json")
                if (
                    finished.get("injected") is not True
                    or finished.get("window_proven") is not True
                ):
                    raise ValueError("Injector ended without a proved injection")
            time.sleep(0.02)
        code = runner.poll()
    except BaseException as error:
        errors.append(type(error).__name__ + ": " + str(error))
        cancel()
    finally:
        if runner is not None and runner.poll() is None:
            cancel()
            try:
                code = runner.wait(timeout=45)
            except Exception as error:
                errors.append("runner cleanup unverified: " + str(error))
        try:
            disarm()
        except Exception as error:
            errors.append("final disarm: " + str(error))
        if watcher is not None:
            try:
                try:
                    watcher.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    watcher.terminate()
                    try:
                        watcher.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        watcher.kill()
                        watcher.wait(timeout=1)
            except Exception as error:
                errors.append("injector cleanup: " + str(error))
        # A fast runner exit can bypass the polling loop. Verify completion here too.
        try:
            finished = read(root / "injector-finished.json")
            if (
                watcher is None
                or watcher.returncode != 0
                or finished.get("injected") is not True
                or finished.get("window_proven") is not True
            ):
                errors.append("injector did not finish with a proved injection")
        except Exception as error:
            errors.append("injector completion unavailable: " + str(error))
        if type(code) is not int or code == 0:
            errors.append("expected unsuccessful production result is absent")
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        value = dict(
            assessment="pending",
            runner_returncode=code,
            injector_returncode=None if watcher is None else watcher.returncode,
            elapsed_s=time.monotonic() - start,
            errors=errors,
            cancelled=cancelled,
            invocation_id=invocation["id"],
            runner_reaped=runner is not None and runner.poll() is not None,
        )
        write(root / "supervisor-finished.json", value)
    return value


def _assess(root, *, offline_fixture=False):
    root = Path(root)
    run = choose(list((root / "runs").glob("pico-*")))
    if root.is_symlink() or run.is_symlink():
        raise ValueError("Symlink evidence root")
    plan = read(root / "plan.json")
    result = validate_retained(run)
    t = read(run / "observation/transaction.json")
    initial = read(run / "initial.json")
    final = read(run / "final.json")
    invocation = read(root / "invocation.json")
    intent = read(root / "signal-intent.json")
    sent = read(root / "signal-outcome.json")
    pre = read(root / "host-preflight.json")
    ready = read(root / "injector-ready.json")
    resolution = read(root / "resolved-gate.json")
    supervised = read(root / "supervisor-finished.json")
    kind = "offline_fixture" if offline_fixture else "physical"
    failures = []

    def require(ok, reason):
        if not ok:
            failures.append(reason)

    require(invocation["execution_kind"] == kind, "execution kind cannot be promoted")
    require(
        isinstance(pre.get("host_boot_id"), str)
        and bool(pre["host_boot_id"])
        and invocation.get("host_boot_id") == pre["host_boot_id"],
        "host boot binding",
    )
    require(
        invocation["source_sha256"]
        == digest(__file__)
        == intent["source_sha256"]
        == ready["source_sha256"],
        "candidate identity",
    )
    require(
        pre["candidate_sha256"] == digest(__file__) and pre["passed"] is True, "candidate preflight"
    )
    if not offline_fixture:
        validate_clock_evidence(pre)
    require(
        plan_digest(plan)
        == invocation["plan_sha256"]
        == intent["plan_sha256"]
        == pre["plan_sha256"]
        == resolution["plan_sha256"]
        == ready["plan_sha256"],
        "plan binding",
    )
    require(read(run / "requested-plan.json") == plan, "production requested plan binding")
    require(
        invocation["id"]
        == intent["invocation_id"]
        == ready["invocation_id"]
        == supervised["invocation_id"],
        "invocation identity",
    )
    launch = read(root / "physical-launch-intent.json")
    require(
        launch["consumed"] is True
        and launch["invocation_id"] == invocation["id"]
        and launch["plan_sha256"] == plan_digest(plan),
        "one launch intent",
    )
    require(
        invocation["started_ns"]
        <= ready["monotonic_ns"]
        <= launch["monotonic_ns"]
        <= intent["monotonic_ns"],
        "launch ordering",
    )
    require(
        ready["platform"] == "linux-pidfd"
        and ready["freshness_ns"] == FRESH_NS
        and ready["clock_margin_ns"] == CLOCK_MARGIN_NS,
        "readiness policy",
    )
    require(
        0 <= launch["monotonic_ns"] - ready["monotonic_ns"] <= 3_000_000_000,
        "fresh injector readiness",
    )
    require(intent["run"] == str(run), "injection run binding")
    controller = read(root / "controller.json")
    require(
        controller
        == intent["controller"]
        == read(root / "injector-bound.json")
        == sent["proof"]["controller"],
        "controller identity binding",
    )
    require(
        intent["target"] == sent["target"] and sent["target_pid"] == intent["target"]["pid"],
        "child lifetime binding",
    )
    require(
        intent["target"]["pid"] != controller["pid"]
        and intent["target"]["ppid"] == controller["pid"]
        and intent["target"]["uid"] == controller["uid"],
        "owned child identity",
    )
    require(
        bool(intent["target"]["start"]) and bool(controller["start"]), "process lifetime present"
    )
    require(
        sent["injected"] is True and intent["signal"] == sent["signal"] == "SIGKILL",
        "one intended signal",
    )
    require(
        sent["intent_sha256"] == digest(root / "signal-intent.json"),
        "signal receipt binds durable intent",
    )
    require(
        read(root / "injector-finished.json") == sent,
        "final injector receipt matches signal outcome",
    )
    proof = sent["proof"]
    clock = pre["clock"]
    offset = clock["wall_offset_ns"]
    for value in (
        intent["monotonic_ns"],
        proof["checked_monotonic_ns"],
        proof["checked_wall_ns"],
        sent["before_ns"],
        sent["after_ns"],
        sent["after_wall_ns"],
    ):
        require(type(value) is int and value > 0, "typed positive timestamps")
    require(
        intent["monotonic_ns"]
        <= proof["checked_monotonic_ns"]
        <= sent["before_ns"]
        <= sent["after_ns"],
        "signal time order",
    )
    require(offset == intent["wall_offset_ns"] == proof["wall_offset_ns"], "clock offset binding")
    require(
        clock["state"] == "synchronized"
        and clock["leap"] == "normal"
        and type(clock["uncertainty_ns"]) is int
        and clock["uncertainty_ns"] >= 0,
        "bounded host clock",
    )
    require(clock["uncertainty_ns"] == proof["host_uncertainty_ns"], "clock uncertainty binding")
    require(
        0 <= sent["after_ns"] - clock["sampled_monotonic_ns"] <= PREFLIGHT_AGE_NS,
        "fresh host clock",
    )
    require(
        0 <= launch["monotonic_ns"] - pre["sampled_monotonic_ns"] <= PREFLIGHT_AGE_NS,
        "fresh preflight",
    )
    require(
        abs(proof["checked_wall_ns"] - proof["checked_monotonic_ns"] - offset) <= STEP_NS
        and abs(sent["after_wall_ns"] - sent["after_ns"] - offset) <= STEP_NS,
        "host clock step",
    )
    raw = (run / "usb-io.jsonl").read_bytes()
    prefix = raw[: proof["audit"]["bytes"]]
    require(
        0 < len(prefix) <= len(raw)
        and hashlib.sha256(prefix).hexdigest() == proof["audit"]["sha256"],
        "audit prefix identity",
    )
    a = Audit(plan, host_uncertainty_ns=clock["uncertainty_ns"])
    a.feed(prefix)
    require(
        not a.partial and not any(a.buffers.values()) and a.sequence == proof["audit"]["sequence"],
        "complete audit prefix",
    )
    require(
        a.running is not None
        and a.running[1] == intent["trigger_sequence"]
        and a.running[2] == intent["trigger"],
        "running trigger binding",
    )
    require(
        a.job is not None
        and a.job["job_id"] == intent["job_id"] == t["job"]["job_id"]
        and a.boot == intent["boot_id"] == initial["target"]["boot_id"]
        and a.session == intent["session_id"],
        "job/boot/session identity",
    )
    # Replay persisted observations; no receipt boolean establishes eligibility.
    require(
        a.eligible(proof["checked_monotonic_ns"], proof["checked_wall_ns"], offset),
        "eligible at final check",
    )
    require(
        a.eligible(sent["before_ns"], sent["before_ns"] + offset, offset), "eligible before signal"
    )
    require(a.eligible(sent["after_ns"], sent["after_wall_ns"], offset), "eligible after signal")
    require(
        proof["valid_until_monotonic_ns"] == a.valid_until(offset)
        and sent["after_ns"] <= a.valid_until(offset),
        "independently reconstructed deadline",
    )
    require(sent["window_proven"] is True, "signal window receipt")
    # Catch a competing terminal/transport/ownership event arriving during the call.
    boundary = Audit(plan, host_uncertainty_ns=clock["uncertainty_ns"])
    for line in raw.splitlines(keepends=True):
        if int(loads(line)["monotonic_ns"]) <= sent["after_ns"]:
            boundary.feed(line)
    require(
        boundary.eligible(sent["after_ns"], sent["after_wall_ns"], offset),
        "no contradictory event during signal",
    )
    full = Audit(plan)
    full.feed(raw)
    requests = [(f, stamp) for d, f, stamp, _ in full.frames if d == "out"]
    counts = dict(collections.Counter(f["op"] for f, _ in requests))
    require(
        all(counts.get(k) == 1 for k in ("CLAIM", "LOAD", "ARM", "ABORT", "RELEASE")),
        "operation counts",
    )
    validate_wire_audit(run / "usb-io.jsonl", {t["job"]["job_id"]}, initial["target"])
    validate_management_snapshots(run)
    aborts = [stamp for f, stamp in requests if f["op"] == "ABORT"]
    require(len(aborts) == 1 and sent["after_ns"] <= aborts[0], "ABORT after completed signal call")
    abort_ack = [
        (f, stamp)
        for d, f, stamp, _ in full.frames
        if d == "in" and f.get("type") == "response" and f["op"] == "ABORT"
    ]
    require(
        len(abort_ack) == 1
        and abort_ack[0][0]["ok"] is True
        and abort_ack[0][0]["body"] == t["abort"],
        "ABORT acknowledged and bound",
    )
    release = [stamp for f, stamp in requests if f["op"] == "RELEASE"]
    require(
        len(release) == 1 and abort_ack[0][1] <= release[0], "RELEASE follows ABORT acknowledgment"
    )
    require(
        any(
            f.get("event") == "JOB_STATE"
            and f["body"].get("job_id") == t["job"]["job_id"]
            and f["body"].get("state") == "aborted"
            and f["body"].get("output_active") is False
            and stamp >= aborts[0]
            for _, f, stamp, _ in full.frames
        ),
        "aborted inactive evidence",
    )
    require(
        t.get("cleanup_verified") is True
        and t.get("completed") is False
        and t.get("cleanup_status", {}).get("state") == "running",
        "active transmitter cleanup",
    )
    require(
        result["outcome"] == "cleanup_failed" and result["cleanup_verified"] is False,
        "honest production classification",
    )
    require(
        settings_match(initial, final) and result["settings_unchanged"] is True,
        "unchanged settings",
    )
    for status in (t["released_status"], read(run / "final-status.json")):
        require(
            status["owner_id"] is None
            and status["output_active"] is False
            and status["boot_id"] == intent["boot_id"],
            "released inactive same boot",
        )
    p = read(run / "observation/rf-on-plan.json")
    require(capture_matches_configuration(p, plan), "selected receiver binding")
    require(
        offline_fixture or (p["driver"] != "fixture" and not (run / "fixture.json").exists()),
        "offline evidence cannot establish a physical gate",
    )
    for key in ("output_path", "metadata_path"):
        if Path(p[key]).parent.resolve() != (run / "observation").resolve():
            raise ValueError("Capture path escapes observation")
    diag = read(p["metadata_path"] + ".execution.json")
    require(
        diag["execution"]["return_code"] == -signal.SIGKILL
        and diag["execution"]["timed_out"] is False
        and diag["execution"]["cancelled"] is False
        and diag["execution"]["disconnected"] is False,
        "receiver signal cause without competing failure",
    )
    require(
        diag["arguments"]
        == intent["target"]["argv"]
        == [*capture_argv(p), Path(p["output_path"]).stem],
        "production capture argv",
    )
    require(
        p["helper"]
        == intent["target"]["executable"]
        == resolution["capture_helper"]["path"]
        == diag["helper"]["path"]
        and diag["helper"]["sha256"]
        == intent["helper_sha256"]
        == resolution["capture_helper"]["sha256"],
        "helper artifact binding",
    )
    require(
        diag["native_failure_metadata"] is None and diag["rejected_capture_metadata"] is None,
        "no competing native failure receipt",
    )
    require(
        diag["iq_retained"] is False
        and not Path(p["output_path"]).exists()
        and not Path(p["output_path"] + ".incomplete").exists(),
        "invalid IQ rejected",
    )
    require(
        supervised["injector_returncode"] == 0
        and supervised["runner_reaped"] is True
        and type(supervised["runner_returncode"]) is int
        and supervised["runner_returncode"] != 0
        and supervised["errors"] == []
        and supervised["cancelled"] is False,
        "supervision completed without competing cancellation",
    )
    return dict(
        assessment="passed" if not failures else "failed",
        failures=failures,
        operation_counts=counts,
        execution_kind=kind,
        production_outcome=result["outcome"],
        transmitter_released=t["cleanup_verified"],
        hardware_state="unassessed" if offline_fixture else "receiver_cleanup_unverified",
        bench_available=None if offline_fixture else False,
        receiver_cleanup="unverified",
        run=str(run),
    )


def assess(root, *, offline_fixture=False):
    try:
        return _assess(root, offline_fixture=offline_fixture)
    except Exception as error:
        return dict(
            assessment="inconclusive",
            failures=[type(error).__name__ + ": " + str(error)],
            execution_kind="offline_fixture" if offline_fixture else "physical",
            hardware_state="unassessed",
            bench_available=None if offline_fixture else False,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Maintained Linux receiver-loss governance tool; "
            "physical execution requires separate authorization."
        )
    )
    parser.add_argument("action", choices=("prepare", "watch", "run", "assess"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--chronyc", type=Path)
    parser.add_argument("--operator", default="")
    parser.add_argument("--enable-read-only-host-observation", action="store_true")
    parser.add_argument("--enable-physical-negative-gate", action="store_true")
    parser.add_argument("--offline-fixture", action="store_true")
    args = parser.parse_args()
    if args.action == "prepare":
        if args.plan is None or args.chronyc is None or args.offline_fixture:
            parser.error("prepare requires --plan and --chronyc; no fixture promotion")
        value = prepare(
            args.plan, args.root, args.chronyc, observe_host=args.enable_read_only_host_observation
        )
        print(json.dumps(value, indent=2))
    elif args.action == "watch":
        watch(args.root, enable_physical=args.enable_physical_negative_gate)
    elif args.action == "run":
        if args.offline_fixture:
            parser.error("Fixture execution is test-only, never a physical fallback")
        value = supervise(
            args.root, enable_physical=args.enable_physical_negative_gate, operator=args.operator
        )
        print(json.dumps(value, indent=2))
        sys.exit(bool(value["errors"]))
    else:
        value = assess(args.root, offline_fixture=args.offline_fixture)
        write(args.root / "negative-assessment.json", value)
        print(json.dumps(value, indent=2))
        sys.exit(value["assessment"] != "passed")
