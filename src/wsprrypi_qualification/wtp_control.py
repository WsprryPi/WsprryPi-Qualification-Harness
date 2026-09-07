"""Device-neutral bounded WTP/1 transactions through an injected transport.

This module imports no USB, SSH or hardware library. Callers own physical
identity, receiver readiness, source interlocks and verified final quiescence.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol


class WtpPeer(Protocol):
    def request(
        self, operation: str, body: dict[str, Any], timeout: float = 8
    ) -> dict[str, Any]: ...
    def next_message(self, timeout: float) -> dict[str, Any]: ...


class WtpControlError(RuntimeError):
    """A transaction failed; evidence includes its cleanup attempt."""

    def __init__(self, message: str, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


def job_digest(job: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(job, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def validate_event_job(
    job: dict[str, Any], *, maximum_events: int = 512, maximum_duration_ns: int = 86_400_000_000_000
) -> None:
    """Validate transport-independent WTP event arithmetic before adapter access."""
    if set(job) != {
        "job_id",
        "profile",
        "mode",
        "events",
        "total_duration_ns",
        "allow_frequency_adjustment",
    }:
        raise ValueError("Unexpected WTP job fields")
    identifier = job["job_id"]
    if (
        not isinstance(identifier, str)
        or len(identifier) != 32
        or any(c not in "0123456789abcdef" for c in identifier)
        or int(identifier, 16) == 0
    ):
        raise ValueError("Invalid job identity")
    if (
        job["profile"] != "rf-events/1"
        or job["mode"] not in {"tone", "wspr", "qrss", "fskcw", "dfcw", "cw"}
        or type(job["allow_frequency_adjustment"]) is not bool
    ):
        raise ValueError("Invalid WTP job profile or mode")

    def decimal(value: Any) -> int:
        if (
            not isinstance(value, str)
            or not value.isascii()
            or not value.isdecimal()
            or len(value) > 20
            or (len(value) > 1 and value.startswith("0"))
        ):
            raise ValueError("Invalid WTP decimal")
        number = int(value)
        if number > 2**64 - 1:
            raise ValueError("WTP decimal exceeds uint64")
        return number

    total = decimal(job["total_duration_ns"])
    events = job["events"]
    if (
        not 0 < total <= maximum_duration_ns
        or not isinstance(events, list)
        or not 0 < len(events) <= maximum_events
    ):
        raise ValueError("Unbounded WTP event job")
    offset = 0
    for event in events:
        if not isinstance(event, dict) or type(event.get("rf_on")) is not bool:
            raise ValueError("Invalid RF state")
        fields = {"offset_ns", "duration_ns", "rf_on"}
        if event["rf_on"]:
            fields.add("frequency_nhz")
            if decimal(event.get("frequency_nhz")) == 0:
                raise ValueError("Zero RF frequency")
        if set(event) != fields:
            raise ValueError("Unexpected WTP event fields")
        duration = decimal(event["duration_ns"])
        if decimal(event["offset_ns"]) != offset or duration <= 0:
            raise ValueError("Discontinuous or empty WTP event")
        offset += duration
        if offset > total:
            raise ValueError("Event exceeds job bound")
    if offset != total:
        raise ValueError("Events do not fill the job")


def run_transaction(
    peer: WtpPeer,
    job: dict[str, Any],
    *,
    device_id: str,
    start_utc_ns: int,
    before_arm: Callable[[], dict[str, Any]],
    receiver_ready: Callable[[], None],
    verify_inactive: Callable[[], dict[str, Any]],
    wall_ns: Callable[[], int] = time.time_ns,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Load one complete job, arm locally, and require matching terminal evidence.

    The caller explicitly invokes this only inside its live authorization and
    source/receiver ownership boundary. Exceptions carry retained evidence;
    ABORT is attempted even when LOAD/ARM replies were lost. Physical disconnect
    remains the caller's fallback when transport cleanup is unavailable.
    """
    validate_event_job(job, maximum_events=162, maximum_duration_ns=110_592_000_000)
    if type(start_utc_ns) is not int or not wall_ns() < start_utc_ns < wall_ns() + 45_000_000_000:
        raise ValueError("Start is outside the bounded transaction window")
    value = copy.deepcopy(job)
    value["job_id"] = uuid.uuid4().hex
    record: dict[str, Any] = {
        "version": 1,
        "job": value,
        "job_sha256": job_digest(value),
        "start_utc_ns": start_utc_ns,
        "completed": False,
        "cleanup_verified": False,
    }
    loaded = False
    claim_attempted = False
    owner_id = uuid.uuid4().hex
    error: BaseException | None = None
    try:
        receiver_ready()
        record["hello"] = peer.request(
            "HELLO",
            {"versions": ["WTP/1"], "client_name": "qualification-harness", "client_version": "1"},
        )
        if record["hello"].get("device_id") != device_id:
            raise ValueError("WTP device identity mismatch")
        if (
            record["hello"].get("selected_version") != "WTP/1"
            or not isinstance(record["hello"].get("boot_id"), str)
            or len(record["hello"]["boot_id"]) != 32
            or any(c not in "0123456789abcdef" for c in record["hello"]["boot_id"])
        ):
            raise ValueError("Invalid WTP negotiation or boot identity")
        record["initial_idle"] = verify_inactive()
        if record["initial_idle"].get("output_active") is not False:
            raise ValueError("Initial output inactivity is not verified")
        claim_attempted = True
        record["owner_id"] = owner_id
        peer.request("CLAIM", {"owner_id": owner_id, "lease_ms": 60000})
        loaded = True  # A failed reply does not prove LOAD had no effect.
        record["load"] = peer.request("LOAD", value, timeout=30)
        if (
            record["load"].get("job_id") != value["job_id"]
            or record["load"].get("state") != "loaded"
        ):
            raise ValueError("LOAD acknowledgment does not match the submitted job")
        record["sync"] = before_arm()
        if not 100_000_000 < start_utc_ns - wall_ns() < 9_000_000_000:
            raise ValueError("Missed bounded ARM window")
        receiver_ready()
        record["arm"] = peer.request(
            "ARM",
            {
                "job_id": value["job_id"],
                "start_utc_ns": str(start_utc_ns),
                "max_start_uncertainty_ns": "20000000",
            },
        )
        if (
            record["arm"].get("job_id") != value["job_id"]
            or record["arm"].get("state") != "armed"
            or record["arm"].get("start_utc_ns") != str(start_utc_ns)
        ):
            raise ValueError("ARM acknowledgment does not match the requested job and start")
        deadline = (
            monotonic()
            + max(0, (start_utc_ns - wall_ns()) / 1e9)
            + int(value["total_duration_ns"]) / 1e9
            + 3
        )
        while monotonic() < deadline:
            receiver_ready()
            try:
                message = peer.next_message(min(1, deadline - monotonic()))
            except TimeoutError:
                continue
            body = message.get("body", {})
            if (
                message.get("type") == "event"
                and message.get("event") == "JOB_STATE"
                and body.get("job_id") == value["job_id"]
                and body.get("state") not in {"loaded", "armed", "running"}
            ):
                record["terminal"] = body
                break
        record["status"] = peer.request("STATUS", {})
        if record["status"].get("boot_id") != record["hello"]["boot_id"]:
            raise ValueError("WTP boot identity changed during the transaction")
        record["completed"] = (
            record.get("terminal", {}).get("state") == "complete"
            and record["terminal"].get("output_active") is False
            and record["status"].get("job_id") == value["job_id"]
            and record["status"].get("state") == "complete"
            and record["status"].get("output_active") is False
        )
    except BaseException as caught:
        error = caught
        record["error"] = str(caught)
    finally:
        try:
            if claim_attempted:
                status = peer.request("STATUS", {}, timeout=3)
                record["cleanup_status"] = status
                if (
                    loaded
                    and status.get("job_id") == value["job_id"]
                    and status.get("state")
                    in {
                        "loaded",
                        "armed",
                        "running",
                    }
                ):
                    record["abort"] = peer.request("ABORT", {"job_id": value["job_id"]}, timeout=6)
                    status = peer.request("STATUS", {}, timeout=3)
                if status.get("owner_id") == owner_id:
                    if status.get("output_active") is not False or status.get("state") in {
                        "armed",
                        "running",
                        "failed",
                    }:
                        raise ValueError("Owned transmitter cannot be safely released")
                    record["release"] = peer.request("RELEASE", {}, timeout=3)
                    status = peer.request("STATUS", {}, timeout=3)
                    record["released_status"] = status
                    if (
                        status.get("owner_id") == owner_id
                        or status.get("output_active") is not False
                    ):
                        raise ValueError("Ownership release or inactive output unverified")
            idle = verify_inactive()
            record["final_idle"] = idle
            record["cleanup_verified"] = idle.get("output_active") is False
        except BaseException as caught:
            record["cleanup_error"] = str(caught)
            error = error or caught
    if error is not None or not record["cleanup_verified"]:
        raise WtpControlError(str(error or "WTP cleanup unverified"), record) from error
    return record


def validate_transaction(record: dict[str, Any]) -> dict[str, Any]:
    """Validate job binding and derive completion from matching WTP evidence.

    This read-only validator does not establish RF or hardware qualification.
    Receiver measurements and independently observed quiescence remain necessary.
    """
    if not isinstance(record, dict):
        raise ValueError("WTP evidence must be an object")
    for field in ("status", "terminal", "final_idle", "hello", "load", "arm"):
        if field in record and not isinstance(record[field], dict):
            raise ValueError("WTP evidence field must be an object: " + field)
    job = record.get("job")
    if not isinstance(job, dict):
        raise ValueError("Missing WTP job")
    validate_event_job(job)
    if (
        type(record.get("version")) is not int
        or record["version"] != 1
        or record.get("job_sha256") != job_digest(job)
    ):
        raise ValueError("WTP job digest mismatch")
    start = record.get("start_utc_ns")
    if type(start) is not int or not 0 < start < 2**64:
        raise ValueError("Invalid WTP start")
    status = record.get("status", {})
    terminal = record.get("terminal", {})
    completed = (
        status.get("job_id") == job["job_id"]
        and terminal.get("job_id") == job["job_id"]
        and status.get("state") == terminal.get("state") == "complete"
        and terminal.get("output_active") is False
        and status.get("output_active") is False
    )
    cleanup = record.get("final_idle", {}).get("output_active") is False
    if record.get("completed") is not completed or record.get("cleanup_verified") is not cleanup:
        raise ValueError("WTP completion or cleanup claim contradicts observations")
    if completed:
        hello, load, arm = (record.get(k, {}) for k in ("hello", "load", "arm"))
        boot = hello.get("boot_id")
        if (
            hello.get("selected_version") != "WTP/1"
            or not isinstance(boot, str)
            or len(boot) != 32
            or any(c not in "0123456789abcdef" for c in boot)
            or status.get("boot_id") != boot
            or load.get("job_id") != job["job_id"]
            or load.get("state") != "loaded"
            or arm.get("job_id") != job["job_id"]
            or arm.get("state") != "armed"
            or arm.get("start_utc_ns") != str(start)
        ):
            raise ValueError("WTP acceptance, schedule or boot identity contradicts completion")
    if completed and ("error" in record or "cleanup_error" in record):
        raise ValueError("Failed transaction cannot claim complete success")
    return {
        "completed": completed,
        "cleanup_verified": cleanup,
        "job_sha256": record["job_sha256"],
        "qualification_claim": False,
    }
