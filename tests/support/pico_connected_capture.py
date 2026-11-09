"""Step 2: real capture worker/process/writer with an inhibited sample source."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.support import pico_governance as base
from wsprrypi_qualification.capture_metadata import load_capture_metadata
from wsprrypi_qualification.offline import artifact, write_json_new
from wsprrypi_qualification.real_capabilities import (
    LocalTransportLauncher,
    SoapyCaptureCapability,
    capability_plan_sha256,
    validate_capability_semantics,
)
from wsprrypi_qualification.wtp_control import validate_transaction


class Clock(base.Clock):
    def __init__(self):
        super().__init__()
        self.active = None
        self.expected_on = False
        self.worker_ready = threading.Event()
        self.worker_done = threading.Event()
        self.waits = []
        self.scenario = None

    def wait_for(self, predicate, label):
        started = time.monotonic()
        deadline = started + 5
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Five-second process handshake exceeded: {label}")
            time.sleep(0.001)
        self.waits.append(dict(label=label, seconds=time.monotonic() - started, limit_seconds=5))
        if sum(row["seconds"] for row in self.waits) > 60:
            raise TimeoutError("Aggregate process supervision exceeded sixty seconds")

    def advance(self, seconds):
        if self.expected_on:
            self.wait_for(self.worker_ready.is_set, "capture worker entry")
        super().advance(seconds)
        if self.active is None or self.worker_done.is_set():
            return
        if self.scenario == "receiver":
            self.wait_for(self.worker_done.is_set, "native preamble failure collected")
            return
        source, plan, start, sequence, _sent = self.active
        self.wait_for(lambda: (source / f"ack-{sequence}").exists(), "native writer acknowledgment")
        execution = self.stream.command(dict(action="inspect"))
        if self.scenario == "cancel" and execution["running"] and execution["executions"]:
            run = execution["executions"][-1]
            if int(run["until_ns"]) > int(run["start_ns"]):
                write_json_new(source / "injection.json", execution)
                (source / "crash").touch(exist_ok=False)
                self.wait_for(self.worker_done.is_set, "abrupt child exit collected")
                return
        count = min(plan.sample_count, round((self.ns - start) / 1e9 * plan.sample_rate_hz))
        self.feed(count, execution["executions"])

    def feed(self, count, executions):
        source, plan, start, sequence, sent = self.active
        if count <= sent:
            return
        samples = base.render_capture(plan, start, executions)
        while sent < count:
            self.wait_for(
                lambda sequence=sequence: (source / f"ack-{sequence}").exists(),
                "previous chunk written",
            )
            end = min(count, sent + 65536)
            temporary = source / f"chunk-{sequence}.tmp"
            samples[sent:end].tofile(temporary)
            temporary.rename(source / f"chunk-{sequence}")
            sent, sequence = end, sequence + 1
        self.active = source, plan, start, sequence, sent
        if sent == plan.sample_count:
            self.wait_for(self.worker_done.is_set, "complete native capture collected")
        else:
            self.wait_for(lambda: (source / f"ack-{sequence}").exists(), "current chunk written")

    def finish_capture(self):
        if self.scenario != "keyed" or self.active is None:
            return
        _, plan, _start, _, _ = self.active
        # RELEASE already completed in JobService. The sample clock advances through
        # the quiet tail; do not expire the controller request while its ACK is pending.
        execution = self.stream.command(dict(action="inspect"))
        self.feed(plan.sample_count, execution["executions"])


class EndpointStream(base.EndpointStream):
    def write(self, data, deadline):
        request = base.loads(data[16:])
        if request["op"] == "ARM":
            source, plan, _, _, _ = self.clock.active
            incomplete = Path(str(plan.output_path) + ".incomplete")
            write_json_new(
                source / "arm-barrier.json",
                dict(
                    retained_prefix_bytes=incomplete.stat().st_size,
                    required_prefix_bytes=2 * plan.sample_rate_hz * 8,
                    worker_done=self.clock.worker_done.is_set(),
                ),
            )
        super().write(data, deadline)
        if request["op"] == "RELEASE":
            self.clock.finish_capture()


class Adapter(base.Adapter):
    def _execute_capture(self, plan, source):
        started = time.monotonic()
        watchdog = threading.Timer(8, self.cancel.set)
        watchdog.daemon = True
        watchdog.start()
        try:
            return super()._capture(plan)
        finally:
            watchdog.cancel()
            watchdog.join(timeout=1)
            elapsed = time.monotonic() - started
            write_json_new(
                source / "runtime.json",
                dict(
                    elapsed_seconds=elapsed,
                    cancellation_deadline_seconds=8,
                    watchdog_joined=not watchdog.is_alive(),
                ),
            )
            if watchdog.is_alive() or elapsed >= 10:
                raise TimeoutError("Capture process supervision exceeded ten seconds")

    def _capture(self, plan):
        clock = self.fixture_clock
        source = Path(str(plan.output_path) + ".source")
        source.mkdir()
        start = clock.ns
        (source / "start-ms").write_text(
            str((base.EPOCH_NS + start) // 1_000_000), encoding="ascii"
        )
        if "rf-off" in plan.output_path.name:
            base.noise(plan.sample_count).tofile(source / "chunk-0")
            result = self._execute_capture(plan, source)
            write_json_new(source / "capability.json", result)
            base.Clock.advance(clock, plan.sample_count / plan.sample_rate_hz)
            clock.expected_on = True
            return result
        clock.active = source, plan, start, 0, 0
        if clock.scenario == "receiver":
            (source / "fail").touch(exist_ok=False)
        clock.worker_ready.set()
        try:
            result = self._execute_capture(plan, source)
            write_json_new(source / "capability.json", result)
            return result
        finally:
            clock.worker_done.set()


def configure(adapter, clock, scenario):
    clock.scenario = scenario
    adapter.fixture_clock = clock
    adapter.capture = SoapyCaptureCapability(LocalTransportLauncher())
    adapter.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="connected-capture")


def validate_case(root):
    receipt = base.loads((root / "software-gate.json").read_bytes())
    if receipt.get("connected_capture") is not True or receipt["qualification_claim"] is not False:
        raise ValueError("Connected software evidence cannot qualify hardware")
    if json.loads((root / "inventory.json").read_bytes()) != base.inventory_for(root):
        raise ValueError("Connected inventory differs")
    plan = base.loads((root / "plan.json").read_bytes())
    base.validate_pico_plan(plan)
    cleanup = receipt["process_cleanup"]
    if (
        not cleanup
        or not cleanup["process_exited"]
        or not cleanup["reader_joined"]
        or cleanup["returncode"] != 0
    ):
        raise ValueError("Endpoint process cleanup unverified")
    if not receipt["worker_joined"]:
        raise ValueError("Capture worker cleanup unverified")
    waits = receipt["process_waits"]
    if (
        any(w["seconds"] >= w["limit_seconds"] or w["limit_seconds"] > 10 for w in waits)
        or sum(w["seconds"] for w in waits) > 60
    ):
        raise ValueError("Process supervision time exceeded")
    directory = root / "observation"
    off_plan = base.loads((directory / "rf-off-plan.json").read_bytes())
    on_plan = base.loads((directory / "rf-on-plan.json").read_bytes())
    for capture in (off_plan, on_plan):
        for field in ("metadata_path", "output_path"):
            if Path(capture[field]).resolve().parent != directory.resolve():
                raise ValueError("Capture evidence escaped its observation directory")
    helper = base.loads((root / "controller.json").read_bytes())["tools"]["capture_helper"]
    off = load_capture_metadata(Path(off_plan["metadata_path"]))
    on_metadata = Path(on_plan["metadata_path"])
    on_output = Path(on_plan["output_path"])
    for capture in (off_plan, on_plan):
        runtime = base.loads(
            (Path(str(capture["output_path"]) + ".source") / "runtime.json").read_bytes()
        )
        if (
            not runtime["watchdog_joined"]
            or runtime["cancellation_deadline_seconds"] != 8
            or not 0 <= runtime["elapsed_seconds"] < 10
        ):
            raise ValueError("Unbounded native capture process supervision")
    off_capability = base.loads(
        (Path(str(off_plan["output_path"]) + ".source") / "capability.json").read_bytes()
    )
    validate_capability_semantics(off_capability)
    if (
        off_capability["helper"] != helper
        or off_capability["plan"] != off_plan
        or off_capability["capture_metadata"] != artifact(Path(off_plan["metadata_path"]))
        or off_capability["output"] != artifact(Path(off_plan["output_path"]))
    ):
        raise ValueError("RF-off capability is not bound to retained files")
    if off.primary_outcome != "success" or off.cleanup_outcome != "verified":
        raise ValueError("RF-off acquisition did not succeed")
    status = base.loads((root / "final-status.json").read_bytes())
    if status["output_active"] or status["owner_id"] is not None:
        raise ValueError("Transmitter remains active or owned")
    operations = [r["op"] for r in json.loads((root / "requests.json").read_bytes())]
    scenario = receipt["scenario"]
    if receipt["observation_outcome"] != {
        "keyed": "passed",
        "receiver": "blocked",
        "cancel": "cleanup_failed",
    }.get(scenario):
        raise ValueError("Negative-control pass must not promote an unsuccessful observation")
    source = Path(str(on_output) + ".source")
    if scenario == "keyed":
        if (
            receipt["receiver_cleanup_failed"]
            or receipt["close_error"]
            or receipt["error"]
            or receipt["result"]["outcome"] != "passed"
        ):
            raise ValueError("Connected observation did not pass")
        capability = base.loads((source / "capability.json").read_bytes())
        validate_capability_semantics(capability)
        if (
            capability["helper"] != helper
            or capability["plan"] != on_plan
            or capability["capture_metadata"] != artifact(on_metadata)
            or capability["output"] != artifact(on_output)
        ):
            raise ValueError("Native capability is not bound to retained files")
        base._validate_frame(
            directory, receipt["result"], plan, plan["modes"][0]["jobs"][0], set(), set()
        )
        if (
            operations.count("ARM") != 1
            or operations.count("RELEASE") != 1
            or "ABORT" in operations
        ):
            raise ValueError("Success did not govern exactly one job")
    elif scenario in {"receiver", "cancel"}:
        if receipt["result"] is not None or receipt["error"]["outcome"] != "blocked":
            raise ValueError("Receiver loss must remain an unsuccessful fixture observation")
        diagnostic = base.loads(Path(str(on_metadata) + ".execution.json").read_bytes())
        if diagnostic["helper"] != helper or diagnostic[
            "capability_plan_sha256"
        ] != capability_plan_sha256(on_plan):
            raise ValueError("Capture failure diagnostic is not bound to the capture plan/helper")
        if diagnostic["iq_retained"] or not diagnostic["execution"]["cleanup_verified"]:
            raise ValueError("Child not reaped or partial IQ retained")
        for suffix in ("", ".incomplete"):
            if Path(str(on_output) + suffix).exists():
                raise ValueError("Rejected IQ survives")
        if scenario == "receiver":
            if json.loads((root / "executions.json").read_bytes()):
                raise ValueError("Preamble refusal still executed a job")
            if (
                "ARM" in operations
                or "CLAIM" in operations
                or receipt["receiver_cleanup_failed"]
                or receipt["close_error"]
            ):
                raise ValueError("Preamble failure did not block ownership with native cleanup")
            native_path = Path(str(on_metadata) + ".failure.json")
            if diagnostic["native_failure_metadata"] != artifact(native_path):
                raise ValueError("Native failure receipt identity differs")
            metadata = load_capture_metadata(native_path)
            if metadata.primary_outcome == "success" or metadata.cleanup_outcome != "verified":
                raise ValueError("Native failure receipt invalid")
        else:
            if receipt["receiver_cleanup_failed"] is not True or not receipt["close_error"]:
                raise ValueError("Missing native cleanup proof was accepted")
            if (
                diagnostic["native_failure_metadata"] is not None
                or diagnostic["rejected_capture_metadata"] is not None
            ):
                raise ValueError("Abrupt exit unexpectedly has native cleanup proof")
            if (
                diagnostic["execution"]["return_code"] != 71
                or "abrupt exit" not in diagnostic["execution"]["stderr"]
            ):
                raise ValueError("Wrong failure injection")
            injection = base.loads((source / "injection.json").read_bytes())
            executions = injection["executions"]
            if (
                injection["running"] is not True
                or len(executions) != 1
                or int(executions[0]["until_ns"]) <= int(executions[0]["start_ns"])
            ):
                raise ValueError("Capture was not lost during running work")
            for op in ("LOAD", "ARM", "ABORT", "RELEASE"):
                if operations.count(op) != 1:
                    raise ValueError("Interrupted job not aborted and released exactly once")
            valid = validate_transaction(base.loads((directory / "transaction.json").read_bytes()))
            transaction = base.loads((directory / "transaction.json").read_bytes())
            if (
                transaction["job"]["job_id"] != executions[0]["job_id"]
                or transaction["cleanup_status"]["state"] != "running"
            ):
                raise ValueError("Failure injection was not bound to the running transaction")
            if valid["completed"] or not valid["cleanup_verified"]:
                raise ValueError("Transmitter cleanup or failure classification differs")
    else:
        raise ValueError("Unknown connected scenario")
    if scenario != "receiver":
        barrier = base.loads((source / "arm-barrier.json").read_bytes())
        if (
            barrier["worker_done"]
            or barrier["required_prefix_bytes"] != 2 * on_plan["sample_rate_hz"] * 8
            or barrier["retained_prefix_bytes"] < barrier["required_prefix_bytes"]
        ):
            raise ValueError("ARM preceded child-produced readiness")
    return receipt


def run_gate(source, native, wsprd, root):
    """Exactly three connected cases, independent validation, two evidence controls."""
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    cases = {}
    for scenario in ("keyed", "receiver", "cancel"):
        directory = root / scenario
        cases[scenario] = base.run_case(source, native, wsprd, directory, scenario, connected=True)
        checked = base.fresh_validation(directory)
        (root / f"{scenario}-validation.log").write_text(
            checked.stdout + checked.stderr, encoding="utf-8"
        )
        if checked.returncode:
            raise ValueError(f"Connected {scenario} retained validation failed: {checked.stderr}")
    waits = sum(sum(row["seconds"] for row in case["process_waits"]) for case in cases.values())
    if waits > 60:
        raise ValueError("Connected process-supervision aggregate exceeded sixty seconds")
    controls = []
    for name, scenario in (
        ("missing_native_metadata", "keyed"),
        ("forged_cleanup_success", "cancel"),
    ):
        directory = root / scenario
        if scenario == "keyed":
            plan = base.loads((directory / "observation/rf-on-plan.json").read_bytes())
            path = Path(plan["metadata_path"])
        else:
            path = directory / "software-gate.json"
        original = path.read_bytes()
        try:
            if scenario == "keyed":
                path.unlink()
            else:
                receipt = json.loads(original)
                receipt.update(receiver_cleanup_failed=False, close_error=None)
                path.write_text(json.dumps(receipt), encoding="utf-8")
            base.seal_inventory(directory)
            checked = base.fresh_validation(directory)
            if checked.returncode == 0:
                raise ValueError(f"Adversarial evidence control was accepted: {name}")
            controls.append(dict(injection=name, rejected=True, returncode=checked.returncode))
            (root / f"{name}-validation.log").write_text(
                checked.stdout + checked.stderr, encoding="utf-8"
            )
        finally:
            path.write_bytes(original)
            base.seal_inventory(directory)
    summary = dict(
        evidence_type="pico_connected_capture_gate",
        qualification_claim=False,
        outcome="passed",
        cases=cases,
        controls=controls,
        elapsed_seconds=time.monotonic() - started,
        process_wait_seconds=waits,
    )
    write_json_new(root / "gate-summary.json", summary)
    checked = base.fresh_validation(root)
    if checked.returncode:
        raise ValueError(f"Final connected retained validation failed: {checked.stderr}")
    return summary


def validate_gate(root):
    summary = base.loads((root / "gate-summary.json").read_bytes())
    if (
        summary["qualification_claim"] is not False
        or summary["outcome"] != "passed"
        or set(summary["cases"]) != {"keyed", "receiver", "cancel"}
    ):
        raise ValueError("Invalid connected summary")
    for scenario, retained in summary["cases"].items():
        if validate_case(root / scenario) != retained:
            raise ValueError("Connected summary differs from retained case")
    waits = sum(
        sum(row["seconds"] for row in case["process_waits"]) for case in summary["cases"].values()
    )
    if waits != summary["process_wait_seconds"] or waits > 60:
        raise ValueError("Connected supervision bound differs")
    if [entry["injection"] for entry in summary["controls"]] != [
        "missing_native_metadata",
        "forged_cleanup_success",
    ] or any(
        entry["rejected"] is not True or not entry["returncode"] for entry in summary["controls"]
    ):
        raise ValueError("Missing evidence rejection controls")
    return summary
