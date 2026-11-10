"""Owned benign processes only. No device, service, network, or RF API."""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve(strict=True)
spec = importlib.util.spec_from_file_location("gate", root / "pico_negative_gate.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
started = time.monotonic()
children = []
handles = []
checks = []


def alarm(*args):
    raise TimeoutError("Eight-second benign-test bound")


signal.signal(signal.SIGALRM, alarm)
signal.alarm(8)
try:
    g.platform_check()
    checks.append("real Linux pidfd capability")
    helper = root / "benign"
    p = dict(
        helper=str(helper),
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
        output_path=str(root / "rf-on-dummy.cf32"),
        metadata_path=str(root / "rf-on-dummy.json"),
    )
    args = [*g.capture_argv(p), Path(p["output_path"]).stem]
    target = subprocess.Popen(args)
    children.append(target)
    decoy_args = list(args)
    decoy_args[-1] = "rf-on-decoy"
    decoy = subprocess.Popen(decoy_args)
    children.append(decoy)
    controller = g.identity(os.getpid())
    ti = g.identity(target.pid)
    di = g.identity(decoy.pid)
    sha = g.digest(helper)
    found = [i for i in (ti, di) if g.candidate_matches(i, controller, p, sha)]
    assert g.choose(found) == ti
    checks.append("real argv target selected, decoy rejected")
    for field, value in [("ppid", 0), ("uid", -1), ("executable", "/wrong")]:
        bad = dict(ti)
        bad[field] = value
        assert not g.candidate_matches(bad, controller, p, sha)
    checks.append("wrong parent, user, executable rejected")
    bad = dict(ti)
    bad["start"] = "changed"
    try:
        g.HeldProcess(target.pid, bad)
    except ValueError:
        checks.append("changed lifetime refused")
    else:
        raise AssertionError("Wrong lifetime accepted")
    held = g.HeldProcess(target.pid, ti)
    handles.append(held)

    def guard():
        held.verify()
        now = time.monotonic_ns()
        return dict(checked_monotonic_ns=now, valid_until_monotonic_ns=now + 250_000_000)

    one = g.OneSignal()
    outcome = one.send(
        held,
        root / "benign-signal-intent.json",
        dict(execution_kind="offline_benign", target=ti, signal="SIGKILL"),
        guard,
    )
    assert outcome["injected"] and outcome["window_proven"]
    assert target.wait(timeout=1) == -signal.SIGKILL and decoy.poll() is None
    checks.append("one pidfd SIGKILL reached only target; target reaped; decoy survives")
    try:
        one.send(held, root / "forbidden-second-intent.json", {}, guard)
    except ValueError:
        checks.append("second signal refused")
    else:
        raise AssertionError("Second allowance used")
    try:
        held.verify()
    except ValueError:
        checks.append("exited held process refused")
    else:
        raise AssertionError("Dead handle accepted")
    dh = g.HeldProcess(decoy.pid, di)
    handles.append(dh)

    def expired():
        now = time.monotonic_ns()
        return dict(checked_monotonic_ns=now, valid_until_monotonic_ns=now - 1)

    attempt = g.OneSignal()
    try:
        attempt.send(
            dh, root / "expired-intent.json", dict(execution_kind="offline_benign"), expired
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Expired signal permitted")
    assert attempt.used and decoy.poll() is None
    checks.append("expired window refused before signal, allowance consumed")
finally:
    for child in children:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=1)
    for held in handles:
        held.close()
    signal.alarm(0)
assert all(child.poll() is not None for child in children)
assert time.monotonic() - started < 10
value = dict(
    passed=True,
    execution_kind="offline_benign",
    hardware_state="unassessed",
    candidate_sha256=g.digest(root / "pico_negative_gate.py"),
    checks=checks,
    all_children_reaped=True,
    children=[dict(pid=p.pid, returncode=p.returncode) for p in children],
    elapsed_s=time.monotonic() - started,
)
g.write(root / "linux-result.json", value)
print(json.dumps(value, indent=2))
