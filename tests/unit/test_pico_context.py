"""Compatibility is purpose-bound; missing optional telemetry cannot hide unsafe state."""

import copy
import os
from pathlib import Path

import pytest

from tests.unit.test_pico_campaign import configuration, frozen_input_case, make_plan, make_snapshot
from wsprrypi_qualification.pico_campaign import check_admission, check_prepared_correction
from wsprrypi_qualification.pico_context import assessment, validate_diagnostic_context
from wsprrypi_qualification.pico_wire import PicoWireError, loads, validate_diagnostic


def test_original_captured_response_is_accepted_without_rewriting():
    selected = os.environ.get("WSPQ_CAPTURED_PICO_DIAGNOSTIC")
    if not selected:
        pytest.skip("Explicit private captured response required; never embed bench records")
    path = Path(selected)
    original = path.read_bytes()
    value = loads(original)
    before = copy.deepcopy(value)
    assert "prepared_input" not in value["selection"]
    validate_diagnostic(value, purpose="governance")
    with pytest.raises(PicoWireError):
        validate_diagnostic(value)
    assert value == before and path.read_bytes() == original


def test_governance_projection_is_not_an_exact_document_comparison():
    value = make_snapshot(gps=True, automatic=True)
    selection = value["rf-calibration"]["selection"]
    del selection["prepared_input"]
    del selection["candidate"]
    selection["future_telemetry"] = {"unassessed": "preserved"}
    del value["ntp-observations"]["network"]
    value["ntp-observations"]["future_counter"] = 123
    original = copy.deepcopy(value)
    check_admission(make_plan(configuration(gps=True, automatic=True)), value, purpose="governance")
    context = assessment(value, "admission")
    assert context["atomic_input_capability"] == "unavailable"
    assert context["calibration_qualification"] == "not_assessed"
    assert value == original
    with pytest.raises(PicoWireError):
        check_admission(make_plan(configuration(gps=True, automatic=True)), value)


@pytest.mark.parametrize(
    "resource,key,bad",
    [
        ("rf-calibration", "contract", "unknown/2"),
        ("ntp-observations", "active_source", "unknown"),
        ("ntp-observations", "nominal_sample_hz", True),
        ("ntp-observations", "clock_generation", "18446744073709551616"),
        ("message-schedule", "pause_pending", "false"),
    ],
)
def test_consumed_fields_remain_strict(resource, key, bad):
    value = make_snapshot()[resource]
    value[key] = bad
    with pytest.raises(PicoWireError, match="purpose=governance"):
        validate_diagnostic(value, purpose="governance")


@pytest.mark.parametrize(
    "key,bad",
    [
        ("next_ppb", True),
        ("next_ppb", 100001),
        ("prepared_input", {}),
        ("prepared_generation", "18446744073709551616"),
    ],
)
def test_optional_atomic_input_is_strict_when_present(key, bad):
    value = make_snapshot()["rf-calibration"]
    value["selection"][key] = bad
    with pytest.raises(PicoWireError):
        validate_diagnostic(value, purpose="governance")


def prepared_case():
    plan = make_plan(configuration(gps=True, automatic=True))
    value = make_snapshot(gps=True, automatic=True)
    value["status"]["job"].update(state="loaded", job_id="a" * 32, owner_id="b" * 32)
    selection = value["rf-calibration"]["selection"]
    del selection["prepared_input"]
    selection.update(prepared_source="gps", prepared_generation="7", prepared_ppb=25)
    return plan, value


def verify(plan, value):
    check_admission(plan, value, owned=True, purpose="governance")
    check_prepared_correction(
        plan,
        value,
        [value["gps"]],
        job_id="a" * 32,
        load_bracket=[value["gps"], value["gps"]],
        purpose="governance",
        owner_id="b" * 32,
    )


def test_legacy_prepared_summary_is_governance_only():
    plan, value = prepared_case()
    verify(plan, value)
    with pytest.raises(PicoWireError):
        check_prepared_correction(
            plan, value, [value["gps"]], job_id="a" * 32, load_bracket=[value["gps"], value["gps"]]
        )


@pytest.mark.parametrize(
    "part,key,bad",
    [
        ("job", "job_id", "c" * 32),
        ("job", "owner_id", None),
        ("job", "owner_id", "d" * 32),
        ("job", "state", "complete"),
        ("job", "output_active", True),
        ("selection", "prepared_ppb", None),
        ("selection", "prepared_source", "manual"),
        ("selection", "prepared_generation", None),
        ("selection", "prepared_generation", "999"),
        ("selection", "prepared_ppb", 26),
        ("selection", "prepared_input", None),
    ],
)
def test_contextual_contradictions_refuse_even_with_legacy_document(part, key, bad):
    plan, value = prepared_case()
    target = value["status"]["job"] if part == "job" else value["rf-calibration"]["selection"]
    target[key] = bad
    with pytest.raises(PicoWireError):
        verify(plan, value)


def test_available_atomic_evidence_cannot_be_downgraded():
    plan, value, bracket, _ = frozen_input_case()
    value["status"]["job"].update(state="loaded", job_id="a" * 32, owner_id="b" * 32)
    value["rf-calibration"]["selection"]["prepared_input"]["job_id"] = "c" * 32
    with pytest.raises(PicoWireError, match="accepted job"):
        check_prepared_correction(
            plan,
            value,
            [value["gps"]],
            job_id="a" * 32,
            load_bracket=bracket,
            purpose="governance",
            owner_id="b" * 32,
        )


def test_errors_identify_missing_requirement_without_exposing_values():
    value = make_snapshot()["rf-calibration"]
    del value["selection"]["prepared_ppb"]
    value["private-extension-secret"] = "secret-password"
    with pytest.raises(PicoWireError) as error:
        validate_diagnostic(value, purpose="governance", stage="prepared")
    assert "prepared_ppb" in str(error.value) and "stage=prepared" in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("purpose,stage", [("unknown", "read"), ("governance", "unknown")])
def test_unknown_policy_refuses(purpose, stage):
    with pytest.raises(PicoWireError):
        validate_diagnostic_context(make_snapshot()["rf-calibration"], purpose, stage)


def test_capability_cannot_disappear_between_admission_and_arm():
    from wsprrypi_qualification.pico_context import check_transition

    before = make_snapshot()
    after = copy.deepcopy(before)
    del after["rf-calibration"]["selection"]["prepared_input"]
    with pytest.raises(PicoWireError, match="capability changed"):
        check_transition(before, after)


def test_captured_management_bytes_replay_through_real_client_and_ack():
    from tests.unit.test_pico_wire import MemoryStream
    from wsprrypi_qualification.pico_audit import _frames
    from wsprrypi_qualification.pico_wire import ManagementClient

    selected = os.environ.get("WSPQ_CAPTURED_PICO_AUDIT")
    if not selected:
        pytest.skip("Explicit private wire capture required; never embed bench records")
    original = Path(selected).read_bytes()
    records = [loads(line) for line in original.splitlines()]
    incoming = b"".join(
        bytes.fromhex(row["data_hex"])
        for row in records
        if row["carrier"] == "management" and row["phase"] == "read"
    )
    outgoing = b"".join(
        bytes.fromhex(row["data_hex"])
        for row in records
        if row["carrier"] == "management" and row["phase"] == "write_requested"
    )
    responses = _frames(incoming, "management", "in")
    requests = _frames(outgoing, "management", "out")
    stream = MemoryStream(incoming, fragment=113)
    client = ManagementClient(stream, responses[0]["target"]["device_id"], purpose="governance")
    client.connect()
    definitions = {
        "status": "status",
        "capabilities": "capabilities",
        "config": "config-read",
        "hardware": "application/$defs/hardware_read",
        "gps": "gps",
        "si5351": "si5351",
    }
    seen = []
    for request in requests:
        if request["operation"] != "request":
            continue
        resource = request["path"].rsplit("/", 1)[1]
        value = client.get(resource, definitions.get(resource, "diagnostic"))
        seen.append(resource)
        if resource == "rf-calibration":
            assert "prepared_input" not in value["selection"]
    assert "rf-calibration" in seen
    assert not stream.data
    assert b"".join(stream.writes) == outgoing
    assert Path(selected).read_bytes() == original


def test_wrong_diagnostic_resource_is_refused_after_delivery_ack():
    from tests.unit.test_pico_wire import MemoryStream, ump_frame, ump_hello, ump_reply
    from wsprrypi_qualification.pico_wire import ManagementClient

    stream = MemoryStream(
        ump_frame(ump_hello())
        + ump_frame(ump_reply(2, make_snapshot()["rf-calibration"], delivery=True))
        + ump_frame(ump_reply(2, {"acknowledged": True}))
    )
    client = ManagementClient(stream, "a" * 32, purpose="governance")
    client.connect()
    with pytest.raises(PicoWireError, match="requested resource"):
        client.get("ntp-observations", "diagnostic")
    assert loads(stream.writes[-1][8:])["operation"] == "ack"


def test_legacy_summary_can_use_an_intervening_unobserved_generation():
    plan, value, bracket, _ = frozen_input_case()
    value["status"]["job"].update(state="loaded", job_id="a" * 32, owner_id="b" * 32)
    del value["rf-calibration"]["selection"]["prepared_input"]
    check_prepared_correction(
        plan,
        value,
        [value["gps"]],
        job_id="a" * 32,
        load_bracket=bracket,
        purpose="governance",
        owner_id="b" * 32,
    )


@pytest.mark.parametrize("mutation", ["missing", "wrong-boot", "missing-estimate"])
def test_legacy_summary_requires_bound_bracket_evidence(mutation):
    plan, value = prepared_case()
    bracket = [copy.deepcopy(value["gps"]) for _ in range(2)]
    if mutation == "missing":
        bracket.pop()
    elif mutation == "wrong-boot":
        bracket[0]["target"]["boot_id"] = "d" * 32
    else:
        bracket[0]["runtime"]["timer_calibration"] = None
    with pytest.raises(PicoWireError):
        check_prepared_correction(
            plan,
            value,
            [value["gps"]],
            job_id="a" * 32,
            load_bracket=bracket,
            purpose="governance",
            owner_id="b" * 32,
        )
