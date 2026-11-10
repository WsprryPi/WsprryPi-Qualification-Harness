"""Real watcher against synthetic WTP and an owned inert child. No physical runner."""

import importlib.util
import json
import os
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

from wsprrypi_qualification.pico_wire import crc32c, encode

base = Path(sys.argv[1]).resolve(strict=True)
spec = importlib.util.spec_from_file_location("gate", base / "pico_negative_gate.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

started = time.monotonic()
children = []


def alarm(*args):
    raise TimeoutError("Eight-second benign watcher bound")


signal.signal(signal.SIGALRM, alarm)
signal.alarm(8)
root = base / "watch-case"
root.mkdir(mode=0o700)
try:
    fixture = g.read(base / "watch-fixture.json")
    assert fixture["execution_kind"] == "offline_benign"
    plan = fixture["plan"]
    helper = str(base / "benign")
    run = root / "runs/pico-benign"
    obs = run / "observation"
    obs.mkdir(parents=True)
    p = dict(
        helper=helper,
        driver="fixture",
        serial="dummy",
        center_frequency_hz=14072100,
        sample_count=100,
        gain_db=20,
        sample_rate_hz=250000,
        bandwidth_hz=200000,
        channel=0,
        agc=False,
        bias_tee=False,
        read_timeout_us=1000000,
        maximum_elapsed_s=4.900001,
        sample_format="CF32",
        clipping_threshold=0.999,
        output_path=str(obs / "rf-on-dummy.cf32"),
        metadata_path=str(obs / "rf-on-dummy.json"),
    )
    receiver = plan["configuration"]["receiver"]
    for key in (
        "driver",
        "serial",
        "center_frequency_hz",
        "gain_db",
        "sample_rate_hz",
        "bandwidth_hz",
        "channel",
    ):
        receiver[key] = p[key]
    receiver["capture_helper"] = helper
    g.write(root / "plan.json", plan)
    g.write(obs / "rf-on-plan.json", p)
    pd = g.plan_digest(plan)
    sha = g.digest(base / "pico_negative_gate.py")
    now = time.monotonic_ns()
    offset = time.time_ns() - now
    g.write(
        root / "resolved-gate.json",
        dict(plan_sha256=pd, capture_helper=dict(path=helper, sha256=g.digest(helper))),
    )
    observation, clock = g.collect_clock(Path("/usr/bin/chronyc"))
    g.write(
        root / "host-preflight.json",
        dict(
            passed=True,
            scope="local_process_and_clock_only",
            execution_kind="physical_inputs",
            device_admission="pending_production_runner",
            candidate_sha256=sha,
            plan_sha256=pd,
            host_boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            sampled_monotonic_ns=now,
            clock=clock,
            observation=observation,
        ),
    )
    controller = g.identity(os.getpid())
    g.write(
        root / "invocation.json",
        dict(
            id="a" * 32,
            execution_kind="offline_benign",
            host_boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            source_sha256=sha,
            plan_sha256=pd,
            supervisor=controller,
            started_ns=now,
        ),
    )
    target = subprocess.Popen([*g.capture_argv(p), Path(p["output_path"]).stem])
    children.append(target)
    decoy_args = [*g.capture_argv(p), "rf-on-decoy"]
    decoy = subprocess.Popen(decoy_args)
    children.append(decoy)
    watcher = subprocess.Popen(
        [
            sys.executable,
            "-I",
            str(base / "pico_negative_gate.py"),
            "watch",
            str(root),
            "--enable-physical-negative-gate",
        ],
        stdout=(root / "watcher.stdout").open("x"),
        stderr=(root / "watcher.stderr").open("x"),
    )
    children.append(watcher)
    deadline = time.monotonic() + 2
    while not (root / "injector-ready.json").exists():
        assert watcher.poll() is None and time.monotonic() < deadline
        time.sleep(0.005)
    g.write(root / "controller.json", controller)
    # Fresh synthetic scheduling coordinates; never sent to a transmitter.
    mono = time.monotonic_ns()
    wall = time.time_ns()
    start = wall - 100_000_000
    rows = []
    io = 0
    for io, item in enumerate(fixture["frames"], 1):
        f = item["frame"]
        direction = item["direction"]
        if f.get("op") == "ARM":
            f["body"]["start_utc_ns"] = str(start)
            if direction == "in":
                f["body"]["start_monotonic_ns"] = "11000000000"
                f["body"]["clock"].update(
                    utc_now_ns=str(start - 1_000_000_000),
                    monotonic_now_ns="10000000000",
                    uncertainty_ns="1",
                    sync_age_ns="0",
                )
        if f.get("op") == "CLAIM" and direction == "in":
            f["body"]["expires_monotonic_ns"] = "60000000000"
        payload = encode(f)
        data = b"WTPF\x01\x01\x00\x00" + struct.pack(">II", len(payload), crc32c(payload)) + payload
        rows.append(
            dict(
                schema_version=1,
                sequence=len(rows) + 1,
                monotonic_ns=str(mono),
                carrier="wtp",
                phase="write_requested" if direction == "out" else "read",
                io_id=io,
                data_hex=data.hex(),
                error=None,
            )
        )
        if direction == "out":
            rows.append(
                dict(
                    schema_version=1,
                    sequence=len(rows) + 1,
                    monotonic_ns=str(mono),
                    carrier="wtp",
                    phase="write_complete",
                    io_id=io,
                    data_hex="",
                    error=None,
                )
            )
    (run / "usb-io.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert watcher.wait(timeout=2) == 0
    outcome = g.read(root / "injector-finished.json")
    assert outcome.get("injected") is True and outcome.get("window_proven") is True, outcome
    assert (
        outcome["target_pid"] == target.pid
        and target.wait(timeout=1) == -9
        and decoy.poll() is None
    )
finally:
    for child in children:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=1)
    signal.alarm(0)
assert all(child.poll() is not None for child in children)
value = dict(
    passed=True,
    execution_kind="offline_benign",
    hardware_state="unassessed",
    candidate_sha256=g.digest(base / "pico_negative_gate.py"),
    watcher_used=True,
    real_pidfd_signal=True,
    synthetic_wtp=True,
    decoy_survived_injection=True,
    all_children_reaped=True,
    elapsed_s=time.monotonic() - started,
    children=[dict(pid=c.pid, returncode=c.returncode) for c in children],
)
g.write(base / "linux-watch-result.json", value)
print(json.dumps(value, indent=2))
