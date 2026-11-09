"""Connected software gate fixtures; never imported by a live CLI.

Only the device/receiver/management/time boundaries are substituted. All WTP
requests cross the real framed client and a native production Endpoint/JobService.
"""

from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tests.unit.test_capture_metadata import capture_document, failure_document
from tests.unit.test_pico_campaign import configuration, make_snapshot
from wsprrypi_qualification import pico_live, pico_wire
from wsprrypi_qualification.capture_metadata import load_capture_metadata
from wsprrypi_qualification.offline import artifact, write_json_new
from wsprrypi_qualification.pico_audit import AuditedStream, WireAudit
from wsprrypi_qualification.pico_campaign import compose_pico_campaign, validate_pico_plan
from wsprrypi_qualification.pico_live import PicoLiveAdapter
from wsprrypi_qualification.pico_results import _validate_frame
from wsprrypi_qualification.pico_wire import WtpClient, encode, loads
from wsprrypi_qualification.real_capabilities import CaptureCapabilityError
from wsprrypi_qualification.transports import (
    CommandPlan,
    LocalCommandTransport,
    validate_execution_record,
)
from wsprrypi_qualification.wtp_control import validate_transaction

EPOCH_NS = 1786449600000000000
SUBSTITUTIONS = [
    "USB replaced by bounded stdin/stdout to the production WTP endpoint",
    "RF engine replaced by inhibited execution recorder",
    "management resources supplied by a fixture with live endpoint job status",
    "receiver and acquisition worker replaced by deterministic virtual capture",
    "UTC, monotonic time, readiness and cancellation waits use simulated time",
]


def stamp(ns):
    return datetime.fromtimestamp(ns / 1e9, UTC).isoformat().replace("+00:00", "Z")


class Clock:
    def __init__(self):
        self.ns = 0
        self.stream = None
        self.cancel = None
        self.cancel_at = None

    def time_ns(self):
        return EPOCH_NS + self.ns

    def monotonic(self):
        return self.ns / 1e9

    def advance(self, seconds):
        self.ns += round(seconds * 1e9)
        if self.stream is not None:
            self.stream.command(dict(action="clock", now_ns=str(self.ns)))
        if self.cancel_at is not None and self.ns >= self.cancel_at:
            self.cancel.set()


class Cancellation:
    def __init__(self, clock):
        self.clock, self.cancelled = clock, False

    def is_set(self):
        return self.cancelled

    def set(self):
        self.cancelled = True

    def wait(self, seconds):
        if not self.cancelled:
            self.clock.advance(seconds)
        return self.cancelled


class EndpointStream:
    """Application-bounded subprocess; only its pipe waits use wall time."""

    def __init__(self, executable, directory, clock, cancel_running=False):
        self.clock = clock
        self.pending = bytearray()
        self.cancel_running = cancel_running
        self.requests = []
        self.log = (directory / "endpoint.jsonl").open("x", encoding="utf-8")
        self.stderr = (directory / "endpoint.stderr").open("xb")
        self.process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            cwd=directory,
            env={"LANG": "C"},
        )
        self.incoming = queue.Queue()

        def reader():
            for line in iter(self.process.stdout.readline, b""):
                self.incoming.put(line)
            self.incoming.put(b"")

        self.reader = threading.Thread(target=reader, daemon=True)
        self.reader.start()
        try:
            self.command(dict(action="connect"))
        except BaseException:
            self.close()
            raise
        clock.stream = self

    def command(self, value):
        self.process.stdin.write(encode(value) + b"\n")
        self.process.stdin.flush()
        try:
            raw = self.incoming.get(timeout=3)
        except queue.Empty as error:
            raise TimeoutError("Native fixture response exceeded three seconds") from error
        if not raw:
            raise RuntimeError("Native fixture disconnected")
        response = loads(raw)
        self.log.write(json.dumps(dict(request=value, response=response)) + "\n")
        self.log.flush()
        if response["closed"]:
            raise RuntimeError("Native endpoint closed")
        self.pending.extend(bytes.fromhex(response["hex"]))
        return response

    def write(self, data, deadline):
        request = loads(data[16:])
        self.requests.append(request)
        self.command(dict(action="send", hex=data.hex()))
        if request["op"] == "ARM" and self.cancel_running:
            self.clock.cancel_at = int(request["body"]["start_utc_ns"]) - EPOCH_NS + 100_000_000

    def read(self, maximum, deadline):
        if not self.pending:
            self.clock.advance(min(0.1, max(0, deadline - self.clock.monotonic())))
        if not self.pending:
            raise TimeoutError("Virtual endpoint poll")
        data = bytes(self.pending[:maximum])
        del self.pending[:maximum]
        return data

    def close(self):
        started = time.monotonic()
        with contextlib.suppress(BrokenPipeError):
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        self.reader.join(timeout=1)
        self.process.stdout.close()
        self.stderr.close()
        self.log.close()
        if self.reader.is_alive() or self.process.returncode is None:
            raise RuntimeError("Fixture process/reader cleanup is unverified")
        return dict(
            process_exited=True,
            reader_joined=True,
            returncode=self.process.returncode,
            elapsed_seconds=time.monotonic() - started,
        )


def noise(count):
    rng = np.random.default_rng(1701)
    # Stable prefix independent of requested count.
    return (rng.normal(0, 0.001, (count, 2)) @ np.array([1, 1j])).astype("<c8")


def render_capture(plan, start, executions):
    samples = noise(plan.sample_count)
    for run in executions:
        run_start, until = int(run["start_ns"]), int(run["until_ns"])
        phase = 0.0
        for event in run["events"]:
            begin = run_start + int(event["offset_ns"])
            end = min(begin + int(event["duration_ns"]), until)
            if not event["rf_on"] or end <= begin:
                continue
            first = max(0, round((begin - start) / 1e9 * plan.sample_rate_hz))
            last = min(plan.sample_count, round((end - start) / 1e9 * plan.sample_rate_hz))
            if last <= first:
                continue
            frequency = int(event["frequency_nhz"]) / 1e9 - plan.center_frequency_hz
            angles = phase + 2 * np.pi * frequency * np.arange(last - first) / plan.sample_rate_hz
            samples[first:last] += (0.2 * np.exp(1j * angles)).astype("<c8")
            phase += 2 * np.pi * frequency * (last - first) / plan.sample_rate_hz
    return samples


def capture_metadata(plan, start, *, failed=False):
    value = failure_document("cancelled", 130) if failed else capture_document()
    value["capture_id"] = plan.output_path.stem
    device = dict(driver=plan.driver, serial=plan.serial)
    value["requested_device"] = value["resolved_device"] = device
    for key in ("requested_settings", "actual_settings"):
        value[key].update(
            {
                name: getattr(plan, name)
                for name in (
                    "sample_rate_hz",
                    "bandwidth_hz",
                    "center_frequency_hz",
                    "gain_db",
                    "channel",
                )
            }
        )
    value["requested_sample_count"] = plan.sample_count
    value["retained_sample_count"] = 0 if failed else plan.sample_count
    value["elapsed_duration_s"] = 0 if failed else plan.sample_count / plan.sample_rate_hz
    value["limits"]["max_elapsed_duration_s"] = plan.maximum_elapsed_s
    value["timestamps"] = {name: stamp(EPOCH_NS + start) for name in value["timestamps"]}
    for name in (
        "retained_capture_complete_utc",
        "cleanup_start_utc",
        "cleanup_complete_utc",
        "helper_complete_utc",
    ):
        value["timestamps"][name] = stamp(
            EPOCH_NS + start + round(value["elapsed_duration_s"] * 1e9)
        )
    if not failed:
        value["output"].update(artifact(plan.output_path))
    else:
        value["output"].update(removed_incomplete_size_bytes=0, removed_incomplete_sha256=None)
    value["output"]["path"] = str(plan.output_path)
    value["cleanup"]["attempted_steps"] = ["virtual_receiver_release"]
    write_json_new(plan.metadata_path, value, schema_name="capture-metadata.schema.json")


class Receiver:
    def __init__(self, clock, stream, unavailable=False):
        self.clock, self.stream, self.unavailable = clock, stream, unavailable
        self.starts = {}

    def execute(self, plan, authorization, cancel):
        start = self.starts.get(plan.output_path, self.clock.ns)
        incomplete = Path(str(plan.output_path) + ".incomplete")
        if cancel.is_set() or self.unavailable:
            incomplete.unlink(missing_ok=True)
            capture_metadata(plan, start, failed=True)
            diagnostic = plan.metadata_path.with_suffix(".diagnostic.json")
            write_json_new(
                diagnostic,
                dict(
                    execution=dict(cleanup_verified=True),
                    native_failure_metadata=artifact(plan.metadata_path),
                    rejected_capture_metadata=None,
                ),
            )
            raise CaptureCapabilityError("Virtual receiver cancelled or unavailable", diagnostic)
        if "rf-off" in plan.output_path.name:
            self.clock.advance(plan.sample_count / plan.sample_rate_hz)
        executions = self.stream.command(dict(action="inspect"))["executions"]
        render_capture(plan, start, executions).tofile(plan.output_path)
        incomplete.unlink(missing_ok=True)
        capture_metadata(plan, start)
        return dict(cleanup_verified=True)


class CaptureFuture:
    def __init__(self, function, plan, receiver, cancel):
        self.function, self.plan, self.receiver, self.cancel = function, plan, receiver, cancel
        self.completed, self.error, self.value = False, None, None
        # Millisecond capture origin is exactly representable at 12 kHz and in UTC metadata.
        receiver.clock.advance((-receiver.clock.ns % 1_000_000) / 1e9)
        self.start = receiver.clock.ns
        receiver.starts[plan.output_path] = self.start
        if receiver.unavailable:
            self.result()
        else:
            noise(2 * plan.sample_rate_hz).tofile(Path(str(plan.output_path) + ".incomplete"))
            receiver.clock.advance(2)

    def done(self):
        if not self.completed and self.receiver.clock.ns >= self.start + round(
            self.plan.sample_count / self.plan.sample_rate_hz * 1e9
        ):
            with contextlib.suppress(Exception):
                self.result()
        return self.completed

    def result(self, timeout=None):
        if not self.completed:
            if not self.cancel.is_set() and not self.receiver.unavailable:
                end = self.start + round(self.plan.sample_count / self.plan.sample_rate_hz * 1e9)
                self.receiver.clock.advance(max(0, (end - self.receiver.clock.ns) / 1e9))
            try:
                self.value = self.function(self.plan)
            except BaseException as error:
                self.error = error
            self.completed = True
        if self.error:
            raise self.error
        return self.value


class VirtualPool:
    def __init__(self, receiver, cancel):
        self.receiver, self.cancel = receiver, cancel

    def submit(self, function, plan):
        # Submission itself does not wait for a capture outcome.
        if self.receiver.unavailable:
            future = object.__new__(CaptureFuture)
            future.function, future.plan = function, plan
            future.receiver, future.cancel = self.receiver, self.cancel
            future.completed, future.error, future.value = False, None, None
            future.start = self.receiver.clock.ns
            with contextlib.suppress(CaptureCapabilityError):
                future.result()
            return future
        return CaptureFuture(function, plan, self.receiver, self.cancel)


class Adapter(PicoLiveAdapter):
    def snapshot(self, *, initial=False):
        value = make_snapshot()
        status = self.wtp.request("STATUS", {})
        value["status"]["job"] = status
        value["capabilities"]["wtp"] = self.wtp.request("CAPS", {})
        selection = value["rf-calibration"]["selection"]
        if status["state"] == "loaded":
            selection.update(prepared_source="manual", prepared_generation="0", prepared_ppb=0)
        return value


def deny(*args, **kwargs):
    raise AssertionError("Physical transport construction forbidden by governance gate")


def binary_path(native, stem):
    candidates = [
        native / stem,
        native / (stem + ".exe"),
        native / "Release" / stem,
        native / "Release" / (stem + ".exe"),
    ]
    return next(path.resolve(strict=True) for path in candidates if path.is_file())


def run_case(source, native, wsprd, root, scenario, *, connected=False):
    from tests.support import pico_connected_capture as connection

    root.mkdir(parents=True, exist_ok=False)
    clock = connection.Clock() if connected else Clock()
    cancel = Cancellation(clock)
    clock.cancel = cancel
    started = time.monotonic()
    stream = None
    audit = None
    result = None
    caught = None
    close_error = None
    worker_joined = False
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pico_live, "PicoUsb", deny)
        patch.setattr(pico_live, "SoapyCaptureCapability", deny)
        patch.setattr(pico_live, "LocalTransportLauncher", deny)
        patch.setattr(pico_live, "time", clock)
        patch.setattr(pico_wire, "time", clock)
        config = configuration()
        config["pico_source"] = str(source)
        config["protocol"].update(message="ET", dot_seconds=0.7, tone_seconds=3)
        config["receiver"].update(
            capture_helper=str(
                binary_path(
                    native, "wspq-capture-connected" if connected else "wspq-pico-governance"
                )
            ),
            wsprd=str(wsprd),
        )
        mode = "wspr" if scenario == "wspr" else "qrss"
        plan = compose_pico_campaign(
            config, binary_path(native, "wspq-pico-compile"), modes=(mode,)
        )
        write_json_new(root / "plan.json", plan)
        try:
            stream = (connection.EndpointStream if connected else EndpointStream)(
                binary_path(native, "wspq-pico-governance"),
                root,
                clock,
                scenario == "cancel" and not connected,
            )
            identity = stream.command(dict(action="inspect"))
            expected_inputs = [
                {
                    "path": str(Path(item["path"]).relative_to(source / "src").as_posix()),
                    "sha256": item["sha256"],
                }
                for item in plan["encoder_sources"]
            ]
            if (
                identity.get("fixture_contract") != "wspq-pico-governance/1"
                or identity.get("source_revision") != plan["source_revision"]
                or identity.get("source_files") != expected_inputs
            ):
                raise ValueError("Native fixture and compiler source identities differ")
            write_json_new(root / "fixture-identity.json", identity)
            audit = WireAudit(root / "wtp.jsonl")
            adapter = object.__new__(connection.Adapter if connected else Adapter)
            adapter.plan, adapter.cancel, adapter.audit = plan, cancel, audit
            adapter._wtp = WtpClient(AuditedStream(stream, audit, "wtp"))
            adapter.wtp.request(
                "HELLO", dict(versions=["WTP/1"], client_name="governance", client_version="1")
            )
            adapter.management = SimpleNamespace(hello=dict(target=dict(boot_id="b" * 32)))
            adapter.discovery = dict(firmware="governance-fixture")
            adapter.usb = SimpleNamespace(
                begin_operations=lambda _: None, begin_cleanup=lambda: None, close=lambda: None
            )
            if connected:
                connection.configure(adapter, clock, scenario)
            else:
                adapter.capture = Receiver(clock, stream, scenario == "receiver")
                adapter.pool = VirtualPool(adapter.capture, cancel)
            adapter.tools = {
                "capture_helper": artifact(Path(config["receiver"]["capture_helper"])),
                "wsprd": artifact(wsprd),
            }
            write_json_new(root / "controller.json", dict(tools=adapter.tools))
            adapter.jobs_attempted, adapter.receiver_cleanup_failed, adapter.futures = (
                False,
                False,
                [],
            )
            row = plan["modes"][0]
            try:
                result = adapter.observe(
                    mode, row["jobs"][0], root / "observation", row["carrier_job"]
                )
            except Exception as error:
                caught = dict(
                    kind=type(error).__name__,
                    message=str(error),
                    outcome=pico_live._error_outcome(error),
                )
            finally:
                cancel.set()
                for future in adapter.futures:
                    with contextlib.suppress(Exception):
                        future.result(timeout=5 if connected else 1)
                if connected:
                    adapter.pool.shutdown(wait=True, cancel_futures=True)
                    worker_joined = True
            status = adapter.inactive()
            executions = stream.command(dict(action="inspect"))["executions"]
            write_json_new(root / "executions.json", executions)
            write_json_new(root / "final-status.json", status)
            write_json_new(root / "requests.json", stream.requests)
            if connected:
                try:
                    adapter.close()
                except Exception as error:
                    close_error = str(error)
        finally:
            try:
                if audit is not None and not audit.file.closed:
                    audit.close()
            finally:
                cleanup = stream.close() if stream is not None else None
    receipt = dict(
        schema_version=1,
        evidence_type="pico_software_governance",
        qualification_claim=False,
        scenario=scenario,
        substitutions=SUBSTITUTIONS,
        result=result,
        error=caught,
        process_cleanup=cleanup,
        elapsed_seconds=time.monotonic() - started,
        simulated_seconds=clock.monotonic(),
        receiver_cleanup_failed=adapter.receiver_cleanup_failed,
    )
    if connected:
        receipt.update(
            connected_capture=True,
            close_error=close_error,
            worker_joined=worker_joined,
            process_waits=clock.waits,
            observation_outcome=(
                "cleanup_failed"
                if adapter.receiver_cleanup_failed or close_error
                else result["outcome"]
                if result
                else caught["outcome"]
            ),
        )
        receipt["substitutions"] = [
            s for s in SUBSTITUTIONS if "receiver and acquisition" not in s
        ] + [
            "Native sample source supplied from executed JobService intervals; "
            "real capture worker, launcher and writer"
        ]
    if scenario == "cancel" and not connected:
        receipt["supervision"] = exercise_process_cancellation(root)
        receipt["assertion_cleanup"] = exercise_assertion_cleanup(native, root)
    receipt["elapsed_seconds"] = time.monotonic() - started
    write_json_new(root / "software-gate.json", receipt)
    seal_inventory(root)
    return receipt


def validate_case(root):
    if loads((root / "software-gate.json").read_bytes()).get("connected_capture"):
        from tests.support.pico_connected_capture import validate_case as validate_connected

        return validate_connected(root)
    receipt = loads((root / "software-gate.json").read_bytes())
    if (
        receipt["qualification_claim"] is not False
        or receipt["evidence_type"] != "pico_software_governance"
    ):
        raise ValueError("Software fixture cannot qualify hardware")
    inventory = json.loads((root / "inventory.json").read_bytes())
    if inventory != inventory_for(root):
        raise ValueError("Retained software gate inventory differs")
    if receipt["receiver_cleanup_failed"] is not False:
        raise ValueError("Receiver cleanup unverified")
    plan = loads((root / "plan.json").read_bytes())
    validate_pico_plan(plan)
    cleanup = receipt["process_cleanup"]
    if (
        not cleanup
        or not cleanup["process_exited"]
        or not cleanup["reader_joined"]
        or cleanup["returncode"] != 0
    ):
        raise ValueError("Fixture process cleanup unverified")
    scenario = receipt["scenario"]
    if scenario in {"keyed", "wspr"}:
        if receipt["error"] or receipt["result"]["outcome"] != "passed":
            raise ValueError(
                "Connected observation failed: " + str(receipt["error"] or receipt["result"])
            )
        row = plan["modes"][0]
        _validate_frame(root / "observation", receipt["result"], plan, row["jobs"][0], set(), set())
    elif scenario in {"receiver", "cancel"}:
        expected = "blocked" if scenario == "receiver" else "aborted"
        if receipt["result"] is not None or receipt["error"]["outcome"] != expected:
            raise ValueError("Incorrect unsuccessful classification")
        requests = json.loads((root / "requests.json").read_bytes())
        operations = [r["op"] for r in requests]
        status = loads((root / "final-status.json").read_bytes())
        if status["output_active"] or status["owner_id"] is not None:
            raise ValueError("Owned work remains active")
        if scenario == "receiver":
            if json.loads((root / "executions.json").read_bytes()):
                raise ValueError("Receiver refusal still executed a job")
            if "ARM" in operations or "CLAIM" in operations:
                raise ValueError("Receiver refusal did not prevent ownership/ARM")
        else:
            for operation in ("LOAD", "ARM", "ABORT", "RELEASE"):
                if operations.count(operation) != 1:
                    raise ValueError("Cancellation did not govern one owned job")
            transaction = loads((root / "observation/transaction.json").read_bytes())
            valid = validate_transaction(transaction)
            if valid["completed"] or not valid["cleanup_verified"]:
                raise ValueError("Cancellation transaction evidence differs")
            executions = json.loads((root / "executions.json").read_bytes())
            if len(executions) != 1 or int(executions[0]["until_ns"]) <= int(
                executions[0]["start_ns"]
            ):
                raise ValueError("Cancellation did not interrupt running work")
            assertion_cleanup = receipt["assertion_cleanup"]
            if (
                not assertion_cleanup["process_exited"]
                or not assertion_cleanup["reader_joined"]
                or assertion_cleanup["returncode"] != 0
                or assertion_cleanup["elapsed_seconds"] >= 10
            ):
                raise ValueError("Assertion-failure worker cleanup was not demonstrated")
            execution = receipt["supervision"]["execution"]
            validate_execution_record(execution)
            if (
                not execution["cancelled"]
                or not execution["cleanup_verified"]
                or execution["return_code"] is None
            ):
                raise ValueError("Real receiver-process supervision failed")
            if receipt["supervision"]["elapsed_seconds"] >= 10:
                raise ValueError("Process supervision exceeded its bound")
        failures = list((root / "observation").glob("*-metadata.json"))
        if not failures:
            raise ValueError("Required native receiver cleanup evidence missing")
        failure_seen = False
        for path in failures:
            metadata = load_capture_metadata(path)
            if metadata.cleanup_outcome != "verified":
                raise ValueError("Native receiver cleanup unverified")
            failure_seen |= metadata.primary_outcome != "success"
        if not failure_seen:
            raise ValueError("Missing native receiver failure receipt")
    else:
        raise ValueError("Unknown software gate scenario")
    return receipt


def inventory_for(root):
    return [
        artifact(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != root / "inventory.json"
    ]


def seal_inventory(root):
    (root / "inventory.json").write_text(
        json.dumps(inventory_for(root), indent=2) + "\n", encoding="utf-8"
    )


def exercise_process_cancellation(root):
    """Real production process supervision; at most 5 seconds, no RF/slot waits."""
    started = time.monotonic()
    ready = root / "child-ready"
    cancel = threading.Event()
    records = []
    errors = []
    child = (
        "import pathlib,sys,threading; pathlib.Path(sys.argv[1]).write_text('ready'); "
        "threading.Event().wait(5)"
    )
    plan = CommandPlan(
        Path(sys.executable).resolve(),
        ("-I", "-c", child, str(ready)),
        working_directory=root,
        timeout_s=3,
    )

    def run():
        try:
            records.append(LocalCommandTransport().execute(plan, cancellation=cancel).to_dict())
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        deadline = started + 2
        while not ready.exists() and time.monotonic() < deadline:
            cancel.wait(0.005)
        if not ready.exists():
            raise RuntimeError("Supervision child did not establish readiness")
    finally:
        cancel.set()
        worker.join(timeout=3)
    if worker.is_alive() or errors or not records:
        raise RuntimeError("Supervision worker did not finish cleanly")
    return dict(execution=records[0], elapsed_seconds=time.monotonic() - started)


def fresh_validation(root):
    script = Path(__file__).resolve().parents[2] / "scripts/pico_governance_gate.py"
    return subprocess.run(
        [sys.executable, "-I", str(script), "--worker", "--validate", "--output", str(root)],
        cwd=root.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def reject_corruptions(root, cancellation_root):
    """Mutate only newly generated scratch evidence, restoring bytes in finally."""
    findings = []
    transaction = root / "observation/transaction.json"
    receipt = root / "software-gate.json"
    native = next(p for p in (cancellation_root / "observation").glob("rf-on-*-metadata.json"))
    for name, directory, path in (
        ("missing_transaction", root, transaction),
        ("rehashed_contradiction", root, transaction),
        ("hardware_claim", root, receipt),
        ("missing_receiver_cleanup", cancellation_root, native),
    ):
        original = path.read_bytes()
        inventory = (directory / "inventory.json").read_bytes()
        try:
            if name in {"missing_transaction", "missing_receiver_cleanup"}:
                path.unlink()
            else:
                value = loads(original)
                if name == "rehashed_contradiction":
                    value["terminal"]["state"] = "aborted"
                else:
                    value["qualification_claim"] = True
                path.write_text(json.dumps(value), encoding="utf-8")
            # A fresh inventory ensures semantic rejection, not a stale hash rejection.
            seal_inventory(directory)
            result = fresh_validation(directory)
            if result.returncode == 0:
                raise AssertionError("Corrupted evidence was accepted: " + name)
            findings.append(
                dict(
                    injection=name,
                    rejected=True,
                    returncode=result.returncode,
                    diagnostic=result.stderr,
                )
            )
        finally:
            path.write_bytes(original)
            (directory / "inventory.json").write_bytes(inventory)
    return findings


def run_gate(source, native, wsprd, root):
    started = time.monotonic()
    root.mkdir(parents=True, exist_ok=False)
    cases = {}
    for scenario in ("keyed", "wspr", "receiver", "cancel"):
        case = root / scenario
        cases[scenario] = run_case(source, native, wsprd, case, scenario)
        result = fresh_validation(case)
        (root / (scenario + "-validation.stdout")).write_text(result.stdout, encoding="utf-8")
        (root / (scenario + "-validation.stderr")).write_text(result.stderr, encoding="utf-8")
        if result.returncode:
            raise AssertionError(f"{scenario} retained validation failed: {result.stderr}")
    corruptions = reject_corruptions(root / "keyed", root / "cancel")
    summary = dict(
        schema_version=1,
        evidence_type="pico_software_governance_gate",
        qualification_claim=False,
        outcome="passed",
        cases=cases,
        corruptions=corruptions,
        elapsed_seconds=time.monotonic() - started,
    )
    write_json_new(root / "gate-summary.json", summary)
    return summary


def exercise_assertion_cleanup(native, root):
    directory = root / "assertion-cleanup"
    directory.mkdir()
    stream = EndpointStream(binary_path(native, "wspq-pico-governance"), directory, Clock())
    try:
        try:
            raise AssertionError("deliberate assertion after native worker creation")
        finally:
            cleanup = stream.close()
    except AssertionError:
        pass
    return cleanup


def validate_gate(root):
    if not (root / "gate-summary.json").is_file():
        return validate_case(root)
    summary = loads((root / "gate-summary.json").read_bytes())
    if summary.get("evidence_type") == "pico_connected_capture_gate":
        from tests.support.pico_connected_capture import validate_gate as validate_connected_gate

        return validate_connected_gate(root)
    if (
        summary["qualification_claim"] is not False
        or summary["outcome"] != "passed"
        or summary["evidence_type"] != "pico_software_governance_gate"
        or set(summary["cases"]) != {"keyed", "wspr", "receiver", "cancel"}
    ):
        raise ValueError("Invalid nonqualifying governance gate summary")
    for scenario, retained in summary["cases"].items():
        if validate_case(root / scenario) != retained:
            raise ValueError("Summary differs from retained scenario")
    if [entry["injection"] for entry in summary["corruptions"]] != [
        "missing_transaction",
        "rehashed_contradiction",
        "hardware_claim",
        "missing_receiver_cleanup",
    ] or any(
        entry["rejected"] is not True or not entry["returncode"] for entry in summary["corruptions"]
    ):
        raise ValueError("Negative acceptance evidence incomplete")
    return summary
