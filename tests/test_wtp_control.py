"""Transport-injected WTP success, lost replies and stale-event rejection."""

import copy

import pytest

from wsprrypi_qualification.wtp_control import (
    WtpControlError,
    run_transaction,
    validate_event_job,
)

JOB = dict(
    job_id="a" * 32,
    profile="rf-events/1",
    mode="tone",
    events=[
        dict(offset_ns="0", duration_ns="1000000000", rf_on=True, frequency_nhz="1000000000000")
    ],
    total_duration_ns="1000000000",
    allow_frequency_adjustment=True,
)


class Peer:
    def __init__(self, fail=None):
        self.calls = []
        self.job = None
        self.state = "empty"
        self.fail = fail
        self.clock = 0
        self.owner = None

    def request(self, op, body, timeout=8):
        self.calls.append(op)
        if op == "HELLO":
            return {"device_id": "board", "selected_version": "WTP/1", "boot_id": "b" * 32}
        if op == "CLAIM":
            if self.owner is not None:
                raise RuntimeError("BUSY")
            self.owner = body["owner_id"]
        if op == "RELEASE":
            self.owner = None
            self.state = "empty"
        if op == "LOAD":
            self.job = body
            self.state = "loaded"
        if op == "ARM":
            self.state = "armed"
        if op == "ABORT":
            self.state = "aborted"
        if op == self.fail:
            self.fail = None
            raise TimeoutError(op + " reply lost")
        if op == "STATUS":
            return dict(
                job_id=self.job["job_id"] if self.job else None,
                state=self.state,
                output_active=False,
                owner_id=self.owner,
                boot_id="b" * 32,
            )
        if op == "LOAD":
            return dict(job_id=self.job["job_id"], state="loaded")
        if op == "ARM":
            return dict(job_id=self.job["job_id"], state="armed", start_utc_ns=body["start_utc_ns"])
        return {}

    def next_message(self, timeout):
        self.clock += 1
        self.state = "complete"
        return dict(
            type="event",
            event="JOB_STATE",
            body=dict(state="complete", job_id=self.job["job_id"], output_active=False),
        )


def run(peer, **kwargs):
    return run_transaction(
        peer,
        JOB,
        device_id="board",
        start_utc_ns=5000000000,
        wall_ns=lambda: 1000000000,
        monotonic=lambda: peer.clock,
        before_arm=lambda: {},
        receiver_ready=lambda: None,
        verify_inactive=kwargs.get("idle", lambda: dict(output_active=False)),
    )


def test_success():
    peer = Peer()
    result = run(peer)
    assert result["completed"] and result["cleanup_verified"]
    assert result["job"]["job_id"] != JOB["job_id"]
    assert "ABORT" not in peer.calls


@pytest.mark.parametrize("operation", ["LOAD", "ARM"])
def test_lost_mutating_reply_aborts(operation):
    peer = Peer(operation)
    with pytest.raises(WtpControlError) as caught:
        run(peer)
    assert "ABORT" in peer.calls
    assert caught.value.evidence["cleanup_verified"]
    assert not caught.value.evidence["completed"]


def test_cleanup_failure_overrides_success():
    with pytest.raises(WtpControlError):
        run(Peer(), idle=lambda: dict(output_active=True))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda j: j.update(total_duration_ns="2"),
        lambda j: j["events"][0].update(offset_ns="1"),
        lambda j: j["events"][0].update(rf_on=False),
        lambda j: j["events"][0].update(duration_ns="-1"),
        lambda j: j.update(allow_frequency_adjustment=1),
        lambda j: j.update(total_duration_ns=str(2**64)),
    ],
)
def test_bad_jobs_have_no_transport_access(mutation):
    job = copy.deepcopy(JOB)
    mutation(job)
    with pytest.raises(ValueError):
        validate_event_job(job)


def test_stale_terminal_does_not_qualify():
    peer = Peer()

    def stale(timeout):
        peer.clock += 100
        return dict(type="event", event="JOB_STATE", body=dict(state="complete", job_id="f" * 32))

    peer.next_message = stale
    result = run(peer)
    assert not result["completed"]
    assert "ABORT" in peer.calls


def test_unsafe_initial_state_prevents_mutation():
    peer = Peer()
    with pytest.raises(WtpControlError):
        run(peer, idle=lambda: dict(output_active=True))
    assert "LOAD" not in peer.calls and "ARM" not in peer.calls


def test_transaction_validator_rejects_changed_job_and_false_pass():
    from wsprrypi_qualification.wtp_control import validate_transaction

    result = run(Peer())
    assert validate_transaction(result)["completed"]
    changed = copy.deepcopy(result)
    changed["job"]["events"][0]["frequency_nhz"] = "2000000000000"
    with pytest.raises(ValueError):
        validate_transaction(changed)
    changed = copy.deepcopy(result)
    changed["terminal"]["job_id"] = "f" * 32
    with pytest.raises(ValueError):
        validate_transaction(changed)


def test_consecutive_short_transactions_release_ownership():
    peer = Peer()
    first = run(peer)
    second = run(peer)
    assert first["completed"] and second["completed"]
    assert peer.calls.count("RELEASE") == 2
    assert peer.owner is None


def test_lost_claim_reply_releases_only_our_lease():
    peer = Peer("CLAIM")
    with pytest.raises(WtpControlError) as caught:
        run(peer)
    assert "RELEASE" in peer.calls and "LOAD" not in peer.calls
    assert caught.value.evidence["cleanup_verified"]
    assert peer.owner is None


def test_foreign_owner_is_never_released():
    peer = Peer()
    peer.owner = "another-owner"
    with pytest.raises(WtpControlError):
        run(peer)
    assert "RELEASE" not in peer.calls and "LOAD" not in peer.calls
    assert peer.owner == "another-owner"


@pytest.mark.parametrize(
    "operation,field,value",
    [
        ("HELLO", "selected_version", "WTP/2"),
        ("LOAD", "job_id", "f" * 32),
        ("ARM", "start_utc_ns", "1"),
        ("STATUS", "boot_id", "c" * 32),
    ],
)
def test_mismatched_acceptance_or_boot_cannot_complete(operation, field, value):
    peer = Peer()
    request = peer.request

    def altered(op, body, timeout=8):
        response = request(op, body, timeout)
        if op == operation:
            response[field] = value
        return response

    peer.request = altered
    with pytest.raises(WtpControlError) as caught:
        run(peer)
    assert not caught.value.evidence["completed"]
    if operation in {"HELLO", "LOAD"}:
        assert "ARM" not in peer.calls
    if operation in {"LOAD", "ARM"}:
        assert "ABORT" in peer.calls


@pytest.mark.parametrize("field", ["hello", "load", "arm"])
def test_missing_acceptance_cannot_validate_as_complete(field):
    from wsprrypi_qualification.wtp_control import validate_transaction

    record = run(Peer())
    del record[field]
    with pytest.raises(ValueError):
        validate_transaction(record)


def test_contradictory_terminal_output_does_not_complete():
    from wsprrypi_qualification.wtp_control import validate_transaction

    peer = Peer()
    message = peer.next_message

    def inconsistent(timeout):
        value = message(timeout)
        value["body"]["output_active"] = True
        return value

    peer.next_message = inconsistent
    record = run(peer)
    assert not record["completed"]
    assert not validate_transaction(record)["completed"]


@pytest.mark.parametrize("field", ["status", "terminal", "final_idle", "hello", "load", "arm"])
@pytest.mark.parametrize("value", [None, [], "invalid"])
def test_malformed_nested_evidence_returns_structured_cli_failure(tmp_path, capsys, field, value):
    import json

    from wsprrypi_qualification.cli import main

    record = run(Peer())
    record[field] = value
    path = tmp_path / "transaction.json"
    path.write_text(json.dumps(record))
    assert main(["validate-wtp-transaction", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["valid"] is False


def test_boolean_evidence_version_rejected():
    from wsprrypi_qualification.wtp_control import validate_transaction

    record = run(Peer())
    record["version"] = True
    with pytest.raises(ValueError):
        validate_transaction(record)
