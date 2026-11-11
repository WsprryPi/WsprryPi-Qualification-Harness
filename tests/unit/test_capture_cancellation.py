"""Real owned processes and native failure receipts, with no SDR or RF."""

import json
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from wsprrypi_qualification.capture_metadata import load_capture_metadata
from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.pico_live import PicoLiveAdapter
from wsprrypi_qualification.real_capabilities import (
    CaptureCapabilityError,
    CaptureCapabilityPlan,
    LocalTransportLauncher,
    SoapyCaptureCapability,
)
from wsprrypi_qualification.transports import CommandPlan, LocalCommandTransport, TransportError


@pytest.mark.parametrize("scenario", ["cancel", "timeout", "early", "cleanup-fail", "crash"])
def test_native_cooperative_capture_retains_honest_cleanup_receipt(tmp_path, scenario):
    build = os.environ.get("WSPQ_PICO_GOVERNANCE_BUILD")
    if not build:
        pytest.skip("Explicit hardware-free native capture build required")
    directory = tmp_path / "capture with spaces é"
    directory.mkdir()
    helper = Path(build) / (
        "wspq-capture-connected.exe" if os.name == "nt" else "wspq-capture-connected"
    )
    plan = CaptureCapabilityPlan(
        helper,
        directory / "metadata.json",
        directory / "iq.cf32",
        "fixture",
        "FIXTURE",
        0,
        1000,
        1000,
        7040100,
        20,
        10000,
        100000,
        0.3 if scenario == "timeout" else 3,
        0.999,
    )
    source = Path(str(plan.output_path) + ".source")
    source.mkdir()
    (source / "start-ms").write_text("1794318000000")
    if scenario == "cleanup-fail":
        (source / "cleanup-fail").touch()
    adapter = object.__new__(PicoLiveAdapter)
    adapter.tools = {"capture_helper": artifact(helper)}
    adapter.capture = SoapyCaptureCapability(LocalTransportLauncher())
    adapter.cancel = threading.Event()
    adapter.receiver_cleanup_failed = False
    if scenario == "early":
        adapter.cancel.set()
    ready = threading.Event()

    def cancel_after_native_read():
        limit = time.monotonic() + 2
        while not (source / "ack-0").exists() and time.monotonic() < limit:
            time.sleep(0.002)
        if (source / "ack-0").exists():
            ready.set()
            if scenario == "crash":
                (source / "crash").touch()
            elif scenario != "timeout":
                adapter.cancel.set()

    worker = threading.Thread(target=cancel_after_native_read)
    if scenario != "early":
        worker.start()
    started = time.monotonic()
    try:
        with pytest.raises(CaptureCapabilityError) as caught:
            adapter._capture(plan)
    finally:
        if scenario != "early":
            worker.join(timeout=2)
            assert not worker.is_alive()
    assert time.monotonic() - started < 5
    diagnostic = json.loads(caught.value.diagnostic_path.read_text())
    assert diagnostic["execution"]["cleanup_verified"] is True
    assert not plan.output_path.exists()
    assert not Path(str(plan.output_path) + ".incomplete").exists()
    native = diagnostic["native_failure_metadata"]
    if scenario == "crash":
        assert native is None
        assert adapter.receiver_cleanup_failed is True
        assert diagnostic["execution"]["return_code"] == 71
    else:
        assert native is not None and artifact(Path(native["path"])) == native
        metadata = load_capture_metadata(Path(native["path"]))
        assert metadata.capture_id == plan.output_path.stem
        assert metadata.requested_device == {"driver": "fixture", "serial": "FIXTURE"}
        assert metadata.primary_failure_cause == "cancelled"
        assert metadata.cleanup_outcome == ("failed" if scenario == "cleanup-fail" else "verified")
        assert adapter.receiver_cleanup_failed is (scenario == "cleanup-fail")
        assert diagnostic["execution"]["timed_out"] is (scenario == "timeout")
        if scenario == "early":
            assert metadata.resolved_device is None  # Stop observed before configure.
        else:
            assert ready.is_set()


def test_unresponsive_cooperative_child_is_still_bounded(tmp_path):
    stop = tmp_path / "owned stop with spaces"
    plan = CommandPlan(
        Path(sys.executable).resolve(),
        ("-c", "import time; time.sleep(10)"),
        timeout_s=0.05,
        cooperative_cancel_path=stop,
        cooperative_stop_timeout_s=0.05,
    )
    started = time.monotonic()
    record = LocalCommandTransport().execute(plan)
    assert time.monotonic() - started < 2
    assert record.timed_out and record.cleanup_verified and record.return_code != 0
    assert stop.read_text() == "timed_out\n"


def test_cooperative_stop_path_collision_refuses_before_launch(tmp_path):
    stop = tmp_path / "foreign stop"
    stop.write_text("preserve")
    plan = CommandPlan(Path(sys.executable).resolve(), cooperative_cancel_path=stop)
    with pytest.raises(TransportError, match="cooperative"):
        LocalCommandTransport().execute(plan)
    assert stop.read_text() == "preserve"
    for path in (Path("relative"), tmp_path / "absent" / "stop"):
        with pytest.raises(TransportError, match="cooperative"):
            replace(plan, cooperative_cancel_path=path).validate()
