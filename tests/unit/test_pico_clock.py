"""Usable GPS holdover, retained correction, and audited clock admission."""

import copy
import json
import os
from pathlib import Path

import pytest

from tests.unit.test_pico_campaign import (
    configuration,
    frozen_input_case,
    make_plan,
    make_snapshot,
)
from wsprrypi_qualification.pico_campaign import (
    check_admission,
    check_prepared_correction,
    validate_pico_plan,
)
from wsprrypi_qualification.pico_clock import CLOCK_POLICY, check_clock, validate_clock_admission
from wsprrypi_qualification.pico_live import _snapshot
from wsprrypi_qualification.pico_wire import PicoWireError


def plan(engine="pio-dma-gp2", automatic=False):
    value = make_plan(configuration(engine, True, automatic))
    value["gps_clock_admission"] = dict(CLOCK_POLICY)
    return value


@pytest.mark.parametrize("defect", ["version", "route", "unknown-field"])
def test_clock_policy_is_closed_and_bound_to_gps(defect):
    value = plan()
    if defect == "version":
        value["gps_clock_admission"]["version"] = 2
    elif defect == "route":
        value["configuration"]["pico"]["utc_source"] = "controller"
    else:
        value["gps_clock_admission"]["retry_seconds"] = 3
    with pytest.raises(ValueError):
        validate_pico_plan(value)


@pytest.mark.parametrize("engine", ["pio-dma-gp2", "si5351"])
@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("age", [2_999_999_999, 3_000_000_000, 3_459_048_982, 5_000_000_000])
def test_acquisition_gap_does_not_revoke_usable_clock(engine, automatic, age):
    value = make_snapshot(engine, True, automatic)
    value["capabilities"]["wtp"]["maximum_holdover_age_ns"] = "3600000000000"
    value["gps"]["runtime"].update(
        age_ns=str(age),
        error="pairing_acquisition",
        time_valid=False,
        serial_present=False,
        pps_present=False,
    )
    value["status"]["standalone"].update(clock_state="holdover", sync_age_ns=str(age))
    check_admission(plan(engine, automatic), value)
    with pytest.raises(PicoWireError):
        check_admission(make_plan(configuration(engine, True, automatic)), value)


@pytest.mark.parametrize("engine", ["pio-dma-gp2", "si5351"])
def test_advancing_learning_does_not_require_a_snapshot_retry(engine):
    value = make_snapshot(engine, True, True)
    value["rf-calibration"]["selection"].update(next_generation="8", next_ppb=999)
    calls = []

    class Client:
        def __init__(self):
            self.hello = {"target": value["target"]}
            self.etags = {"config": value["revision"]}

        def get(self, resource, definition):
            calls.append(resource)
            return copy.deepcopy(value[resource])

    observed = _snapshot(Client(), holdover=True)
    assert calls.count("gps") == calls.count("rf-calibration") == 1
    check_admission(plan(engine, True), observed)


@pytest.mark.parametrize("engine", ["pio-dma-gp2", "si5351"])
def test_prepared_job_uses_frozen_input_during_reacquisition(engine):
    selected, value, bracket, _ = frozen_input_case(engine)
    selected["gps_clock_admission"] = dict(CLOCK_POLICY)
    value["status"]["job"].update(owner_id="c" * 32, job_id="a" * 32, state="loaded")
    value["gps"]["runtime"].update(time_valid=False, error="pairing_acquisition")
    # Subsequent learning need not remain available to validate an accepted job.
    value["rf-calibration"]["selection"].update(reason="gps_calibration_required")
    check_admission(selected, value, owned=True)
    check_prepared_correction(
        selected,
        value,
        [value["gps"]],
        job_id="a" * 32,
        load_bracket=bracket,
        owner_id="c" * 32,
    )
    value["rf-calibration"]["selection"]["prepared_input"]["job_id"] = "f" * 32
    with pytest.raises(PicoWireError):
        check_prepared_correction(
            selected,
            value,
            [value["gps"]],
            job_id="a" * 32,
            load_bracket=bracket,
            owner_id="c" * 32,
        )


def clock_evidence():
    value = make_snapshot(gps=True)
    caps = value["capabilities"]["wtp"]
    caps["maximum_holdover_age_ns"] = "3600000000000"
    age = int(caps["maximum_holdover_age_ns"])
    clock = dict(
        state="holdover",
        utc_now_ns="1786449600000000000",
        monotonic_now_ns=str(age * 2),
        uncertainty_ns=caps["maximum_arm_uncertainty_ns"],
        sync_age_ns=str(age),
        leap="normal",
    )
    evidence = dict(
        schema_version=1,
        target=value["target"],
        job_id="a" * 32,
        clock=clock,
        capabilities=copy.deepcopy(caps),
        maximum_start_uncertainty_ns=caps["maximum_arm_uncertainty_ns"],
        source_before=copy.deepcopy(value["ntp-observations"]),
        source_after=copy.deepcopy(value["ntp-observations"]),
        clock_request_id="d" * 32,
        source_before_id=1,
        source_after_id=2,
        started_monotonic_ns="0",
        completed_monotonic_ns="1",
    )
    transaction = dict(job=dict(job_id="a" * 32), hello=value["target"])
    return evidence, value, transaction


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_advertised_holdover_boundary(delta):
    evidence, value, _ = clock_evidence()
    caps = value["capabilities"]["wtp"]
    evidence["clock"]["sync_age_ns"] = str(int(caps["maximum_holdover_age_ns"]) + delta)
    if delta <= 0:
        check_clock(evidence["clock"], caps, int(caps["maximum_arm_uncertainty_ns"]))
    else:
        with pytest.raises(PicoWireError, match="authority"):
            check_clock(evidence["clock"], caps, int(caps["maximum_arm_uncertainty_ns"]))


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_actual_arm_uncertainty_boundary(delta):
    e, s, _ = clock_evidence()
    limit = int(e["maximum_start_uncertainty_ns"]) - 1
    e["clock"]["uncertainty_ns"] = str(limit + delta)
    if delta <= 0:
        check_clock(e["clock"], s["capabilities"]["wtp"], limit)
    else:
        with pytest.raises(PicoWireError, match="uncertainty"):
            check_clock(e["clock"], s["capabilities"]["wtp"], limit)


@pytest.mark.parametrize("defect", ["unsynchronized", "leap", "uncertain", "disabled", "future"])
def test_clock_authority_refusals(defect):
    e, s, _ = clock_evidence()
    c, caps = e["clock"], s["capabilities"]["wtp"]
    limit = int(caps["maximum_arm_uncertainty_ns"])
    if defect == "unsynchronized":
        c["state"] = "unsynchronized"
    elif defect == "leap":
        c["leap"] = "unknown"
    elif defect == "uncertain":
        limit = int(c["uncertainty_ns"]) - 1
    elif defect == "disabled":
        caps["maximum_holdover_age_ns"] = "0"
    else:
        c["sync_age_ns"] = str(int(c["monotonic_now_ns"]) + 1)
    with pytest.raises(PicoWireError):
        check_clock(c, caps, limit)


@pytest.mark.parametrize("defect", [None, "source", "disagreement", "boot", "generation", "shape"])
def test_retained_clock_source_and_target_binding(defect):
    e, s, t = clock_evidence()
    if defect == "source":
        e["source_after"]["active_source"] = "sntp"
    elif defect == "disagreement":
        e["source_after"]["source_disagreement"] = True
    elif defect == "boot":
        e["target"]["boot_id"] = "f" * 32
    elif defect == "generation":
        e["source_before"]["clock_generation"] = "1"
        s["ntp-observations"]["clock_generation"] = "1"
        e["source_after"]["clock_generation"] = "0"
    elif defect == "shape":
        e["clock"]["unknown"] = True
    if defect is None:
        validate_clock_admission(e, s, t)
    else:
        with pytest.raises(ValueError):
            validate_clock_admission(e, s, t)


def test_clock_domain_fault_still_refuses():
    s = make_snapshot(gps=True)
    s["gps"]["runtime"]["error"] = "pps_capture_clock_changed"
    with pytest.raises(PicoWireError):
        check_admission(plan(), s)


@pytest.mark.parametrize(
    "scenario", ["keyed", "wspr", "cancel", "expired", "cold", "source", "clock-reply-loss"]
)
def test_native_coordinator_keeps_holdover_and_audits_actual_clock(tmp_path, monkeypatch, scenario):

    from tests.support import pico_governance as fixture
    from tests.unit.test_pico_wire import MemoryStream, ump_frame, ump_reply
    from wsprrypi_qualification import pico_audit
    from wsprrypi_qualification.pico_audit import AuditedStream, validate_wire_audit
    from wsprrypi_qualification.pico_wire import ManagementClient
    from wsprrypi_qualification.progress import ProgressReporter

    source = os.environ.get("WSPQ_PICO_SOURCE")
    native = os.environ.get("WSPQ_PICO_GOVERNANCE_BUILD")
    if native is None and os.environ.get("WSPQ_PICO_COMPILER"):
        native = str(Path(os.environ["WSPQ_PICO_COMPILER"]).parent)
    wsprd = os.environ.get("WSPQ_SMOKE_WSPRD")
    if not all((source, native, wsprd)):
        pytest.skip("Explicit source, native endpoint/compiler and offline decoder required")
    original_compose = fixture.compose_pico_campaign
    original_stream = fixture.EndpointStream
    reporters = []
    adapters = []

    def compose(config, *args, **kwargs):
        config["pico"].update(gps_enabled=True, utc_source="gps")
        return original_compose(config, *args, **kwargs)

    class Endpoint(original_stream):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, arguments=("--gps-holdover",))
            monkeypatch.setattr(pico_audit, "time", self.clock)
            self.clock.advance(10)
            self.command(
                dict(
                    action="clock",
                    now_ns=str(self.clock.ns),
                    clock_state="holdover",
                    sync_age_ns="3459048982",
                )
            )

    def snapshot(adapter, *, initial=False):
        value = make_snapshot(gps=True)
        clock = adapter.capture.clock
        if not hasattr(adapter.management, "get"):
            target = value["target"]

            class ManagementStream(MemoryStream):
                def __init__(self):
                    super().__init__(fragment=65536)
                    self.generation = 1

                def write(self, data, deadline):
                    request = json.loads(data[8:])
                    if request["operation"] == "hello":
                        body = dict(
                            schema="usb-management-discovery/1",
                            product="WsprryPico",
                            firmware="governance-fixture",
                            max_request_bytes=32768,
                            max_response_bytes=65536,
                        )
                    elif request["operation"] == "ack":
                        body = dict(acknowledged=True)
                    else:
                        body = copy.deepcopy(value["ntp-observations"])
                        body["clock_generation"] = str(self.generation)
                        self.generation += 1
                        if scenario == "source" and clock.stream.requests[-1]["op"] == "GET_CLOCK":
                            body["active_source"] = "sntp"
                        # Model one raw-timer tick of transport work; this is
                        # simulated I/O, not an admission/recovery wait.
                        clock.advance(0.000001)
                    reply = ump_reply(
                        request["id"], body, delivery=request["operation"] == "request"
                    )
                    reply["target"] = target
                    self.data.extend(ump_frame(reply))

            adapter.management = ManagementClient(
                AuditedStream(ManagementStream(), adapter.audit, "management"), target["device_id"]
            )
            adapter.management.connect()
            if scenario == "clock-reply-loss":
                original_request = adapter.wtp.request

                def lost_reply(operation, body, timeout=8):
                    result = original_request(operation, body, timeout)
                    if operation == "GET_CLOCK":
                        raise TimeoutError("Reply lost after native GET_CLOCK")
                    return result

                adapter.wtp.request = lost_reply
            adapter.reporter = ProgressReporter(tmp_path / "progress.jsonl")
            reporters.append(adapter.reporter)
            adapters.append(adapter)
            monkeypatch.setattr(pico_audit, "time", clock)
        status = adapter.wtp.request("STATUS", {})
        value["status"]["job"] = status
        value["capabilities"]["wtp"] = adapter.wtp.request("CAPS", {})
        value["status"]["standalone"].update(
            clock_state="holdover", sync_age_ns=str(clock.ns - 10_000_000_000 + 3_459_048_982)
        )
        value["gps"]["runtime"].update(
            time_valid=scenario == "wspr" and hasattr(adapter, "_clock_health"),
            age_ns="3459048982",
            error=""
            if scenario == "wspr" and hasattr(adapter, "_clock_health")
            else "pairing_acquisition",
        )
        value["ntp-observations"] = adapter.management.get("ntp-observations", "diagnostic")
        if status["state"] == "loaded":
            value["rf-calibration"]["selection"].update(
                prepared_source="manual", prepared_generation="0", prepared_ppb=0
            )
            if scenario in {"expired", "cold"}:
                clock.stream.command(
                    dict(
                        action="clock",
                        now_ns=str(clock.ns),
                        sync_age_ns=str(
                            int(value["capabilities"]["wtp"]["maximum_holdover_age_ns"]) + 1
                        ),
                        clock_state="unsynchronized" if scenario == "cold" else "holdover",
                    )
                )
        return value

    monkeypatch.setattr(fixture, "compose_pico_campaign", compose)
    monkeypatch.setattr(fixture, "EndpointStream", Endpoint)
    monkeypatch.setattr(fixture.Adapter, "snapshot", snapshot)
    root = tmp_path / "campaign with spaces"
    try:
        receipt = fixture.run_case(
            Path(source),
            Path(native),
            Path(wsprd),
            root,
            "keyed" if scenario in {"expired", "cold", "source", "clock-reply-loss"} else scenario,
        )
    finally:
        for reporter in reporters:
            reporter.close()
    requests = json.loads((root / "requests.json").read_text())
    if scenario in {"expired", "cold", "source", "clock-reply-loss"}:
        assert receipt["error"]["outcome"] == "blocked"
        assert not any(r["op"] == "ARM" for r in requests)
        assert receipt["receiver_cleanup_failed"] is False
        assert sum(r["op"] == "GET_CLOCK" for r in requests) <= 1
        return
    fixture.validate_case(root)
    assert any(r["op"] == "GET_CLOCK" for r in requests)
    if scenario == "cancel":
        assert receipt["error"]["outcome"] == "aborted"
    else:
        assert receipt["result"]["outcome"] == "passed"
    if scenario == "wspr":
        progress = [
            json.loads(line) for line in (tmp_path / "progress.jsonl").read_text().splitlines()
        ]
        notices = [p["detail"] for p in progress if p.get("stage") == "clock"]
        assert len(notices) == 2
        assert "pairing_acquisition" in notices[0] and "acquisition current" in notices[1]
    evidence = [json.loads(p.read_text()) for p in root.rglob("clock-admission.json")]
    transactions = [json.loads(p.read_text()) for p in root.rglob("transaction.json")]
    job_ids = {t["job"]["job_id"] for t in transactions}
    target = json.loads((root / "observation/before.json").read_text())["target"]
    validate_wire_audit(root / "wtp.jsonl", job_ids, target, clock_admissions=evidence)
    # Even a coherent-looking artifact rewrite cannot replace actual wire evidence.
    changed = copy.deepcopy(evidence)
    changed[0]["clock"]["sync_age_ns"] = "0"
    with pytest.raises(PicoWireError, match="audited GET_CLOCK"):
        validate_wire_audit(root / "wtp.jsonl", job_ids, target, clock_admissions=changed)
    if scenario == "keyed":
        # Each independently retained field must be tied to the actual wire,
        # even if the rewritten receipt remains schema-valid.
        for defect in ("caps", "limit", "source-id", "source-body", "time", "missing", "duplicate"):
            changed = copy.deepcopy(evidence)
            if defect == "caps":
                changed[0]["capabilities"]["maximum_holdover_age_ns"] = "1"
            elif defect == "limit":
                changed[0]["maximum_start_uncertainty_ns"] = "1"
            elif defect == "source-id":
                changed[0]["source_before_id"] = changed[0]["source_after_id"]
            elif defect == "source-body":
                changed[0]["source_after"]["clock_generation"] = "999999"
            elif defect == "time":
                changed[0]["completed_monotonic_ns"] = "0"
            elif defect == "missing":
                changed.pop()
            else:
                changed.append(copy.deepcopy(changed[0]))
            with pytest.raises(PicoWireError):
                validate_wire_audit(root / "wtp.jsonl", job_ids, target, clock_admissions=changed)
