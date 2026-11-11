"""Qualified host UTC observations through the owned Pico management channel.

Clock services are observed, never configured. This adapter currently discovers
chronyc; other hosts fail closed for controller UTC and may select GPS or SNTP.
The synchronous coordinator services refreshes, avoiding a second USB owner.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Any

from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.pico_wire import (
    ManagementClient,
    PicoWireError,
    loads,
    validate_time_response,
    validate_time_submission,
    wire_uint64,
)

TIME_POLICY = {
    "version": 1,
    "refresh_seconds": 30,
    "source_lifetime_seconds": 90,
    "exchange_seconds": 1,
    "processing_margin_ns": 100_000,
}


def clock_error(csv: str, elapsed_ns: int, utc_ns: int) -> int:
    """Recompute chrony's conditional host bound from its raw tracking CSV."""
    fields = csv.strip().split(",")
    if (
        len(fields) != 14
        or fields[13] != "Normal"
        or fields[0].upper() in {"00000000", "7F7F0101", "4C4F434C"}
    ):
        raise PicoWireError("Host UTC is not synchronized to a qualified reference")
    try:
        stratum = int(fields[2])
        values = [float(value) for value in fields[3:13]]
    except ValueError as error:
        raise PicoWireError("Malformed chrony tracking observation") from error
    if (
        not 1 <= stratum <= 15
        or not all(math.isfinite(value) for value in values)
        or elapsed_ns < 0
        or elapsed_ns > 200_000_000
        or float(fields[11]) < 0
        or float(fields[9]) < 0
        or float(fields[3]) <= 0
    ):
        raise PicoWireError("Unusable chrony tracking observation")
    error_s = abs(float(fields[4])) + abs(float(fields[10])) / 2 + float(fields[11])
    # Reference freshness is expressed by dispersion, not an arbitrary age gate.
    if float(fields[3]) > utc_ns / 1e9 + error_s + elapsed_ns / 1e9:
        raise PicoWireError("Host reference timestamp is in the future")
    return math.ceil(error_s * 1e9) + elapsed_ns


class ChronyClock:
    """Explicit external-tool capability, with no OS-specific core assumptions."""

    def __init__(self) -> None:
        found = shutil.which("chronyc")
        if found is None:
            raise PicoWireError(
                "Controller UTC requires a qualified chronyc host-time capability; "
                "leave NTP running or select the supported Pico SNTP/GPS route"
            )
        self.path = Path(found).resolve(strict=True)
        self.identity: dict[str, Any] = artifact(self.path)
        try:
            version = subprocess.run(
                [str(self.path), "-v"], capture_output=True, text=True, timeout=1, check=True
            )
            self.identity["version"] = version.stdout.strip() or version.stderr.strip()
        except (OSError, subprocess.SubprocessError):
            self.identity["version"] = "unavailable"

    def observe(self) -> dict[str, Any]:
        if artifact(self.path) != {
            key: self.identity[key] for key in ("path", "size_bytes", "sha256")
        }:
            raise PicoWireError("Host time executable identity changed")
        begin = time.monotonic_ns()
        result = subprocess.run(
            [str(self.path), "-n", "-c", "tracking"],
            capture_output=True,
            text=True,
            timeout=0.2,
            check=True,
        )
        end, utc = time.monotonic_ns(), time.time_ns()
        return dict(
            monotonic_ns=end,
            utc_ns=utc,
            collection_ns=end - begin,
            tracking_csv=result.stdout.strip(),
            uncertainty_ns=clock_error(result.stdout, end - begin, utc),
        )


def validate_host_observation(value: dict[str, Any]) -> None:
    if set(value) != {"monotonic_ns", "utc_ns", "collection_ns", "tracking_csv", "uncertainty_ns"}:
        raise PicoWireError("Host clock observation shape differs")
    if any(
        type(value[key]) is not int or not 0 <= value[key] < 2**64
        for key in ("monotonic_ns", "utc_ns", "collection_ns", "uncertainty_ns")
    ) or not isinstance(value["tracking_csv"], str):
        raise PicoWireError("Host clock observation types differ")
    if value["uncertainty_ns"] != clock_error(
        value["tracking_csv"], value["collection_ns"], value["utc_ns"]
    ):
        raise PicoWireError("Host clock uncertainty does not reproduce")


def submission(
    sample: dict[str, Any],
    before: dict[str, Any],
    response: dict[str, Any],
    confirmed: dict[str, Any],
) -> dict[str, Any]:
    for observation in (before, response, confirmed):
        validate_host_observation(observation)
    elapsed = response["monotonic_ns"] - before["monotonic_ns"]
    wall = response["utc_ns"] - before["utc_ns"]
    ack_elapsed = confirmed["monotonic_ns"] - response["monotonic_ns"]
    ack_wall = confirmed["utc_ns"] - response["utc_ns"]
    step = abs(elapsed - wall) + abs(ack_elapsed - ack_wall)
    if (
        min(elapsed, wall, ack_elapsed, ack_wall) < 0
        or confirmed["monotonic_ns"] - before["monotonic_ns"] > 1_000_000_000
        or step > 1_000_000
    ):
        raise PicoWireError("Host UTC regressed, stepped, or USB sample expired")
    wire_uint64(sample["monotonic_ns"])
    uncertainty = (
        max(value["uncertainty_ns"] for value in (before, response, confirmed))
        + (elapsed + 1) // 2
        + step
        + TIME_POLICY["processing_margin_ns"]
    )
    value = dict(
        sample=sample["sample"],
        utc_ns=str(before["utc_ns"] + wall // 2),
        uncertainty_ns=str(uncertainty),
        leap="normal",
    )
    validate_time_submission(value)
    return value


class ControllerTimeRefresh:
    def __init__(
        self,
        client: ManagementClient,
        path: Path,
        host: ChronyClock,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client, self.host, self.monotonic = client, host, monotonic
        self.last: float | None = None
        self.sequence = 0
        self.file = path.open("x", encoding="utf-8")
        try:
            self.write(
                dict(phase="header", policy=TIME_POLICY, target=client.hello, tool=host.identity)
            )
        except BaseException:
            self.file.close()
            raise

    def write(self, value: dict[str, Any]) -> None:
        self.file.write(json.dumps(value, sort_keys=True) + "\n")
        self.file.flush()
        os.fsync(self.file.fileno())

    def due(self) -> bool:
        now = self.monotonic()
        if self.last is not None and now < self.last:
            raise PicoWireError("Controller monotonic clock regressed")
        return self.last is None or now - self.last >= TIME_POLICY["refresh_seconds"]

    def refresh(self, *, owned_job: bool) -> None:
        if not self.due():
            return
        now = self.monotonic()
        if owned_job and (self.last is None or now - self.last >= 89):
            raise PicoWireError("Controller UTC refresh lifetime missed during an owned job")
        deadline = now + TIME_POLICY["exchange_seconds"]
        self.sequence += 1
        record: dict[str, Any] = dict(phase="prepared", sequence=self.sequence)
        try:
            before = self.host.observe()
            after: list[dict[str, Any]] = []
            sample_id = self.client.sequence + 1
            sample = self.client.time_sample(deadline, lambda: after.append(self.host.observe()))
            confirmed = self.host.observe()
            value = submission(sample, before, after[0], confirmed)
            if self.monotonic() >= deadline:
                raise PicoWireError("USB time sample exchange expired before submission")
            record.update(
                sample=sample,
                before=before,
                after_response=after[0],
                after_ack=confirmed,
                submission=value,
                sample_request_id=sample_id,
                submit_request_id=self.client.sequence + 1,
            )
            self.write(record)  # Intent precedes the only mutation; never replay it.
            response = self.client.time_submit(value, deadline)
            completed = self.monotonic()
            if completed >= deadline:
                raise PicoWireError("USB time submission ACK was late")
            self.write(
                dict(
                    phase="completed",
                    sequence=self.sequence,
                    response=response,
                    monotonic_ns=round(completed * 1e9),
                )
            )
            self.last = completed
        except BaseException as error:
            self.write(dict(phase="failed", sequence=self.sequence, error=type(error).__name__))
            raise

    def close(self) -> None:
        self.file.close()


def validate_time_evidence(
    path: Path,
    target: dict[str, Any],
    exchanges: list[tuple[dict[str, Any], dict[str, Any]]],
    hello: dict[str, Any],
    owned_intervals: list[tuple[int, int]],
) -> None:
    """Recompute observations and correlate them to the already validated byte audit."""
    raw = path.read_bytes()
    if not raw.endswith(b"\n"):
        raise PicoWireError("Controller time evidence is truncated")
    records = [loads(line) for line in raw.splitlines()]
    if (
        not records
        or records[0].get("phase") != "header"
        or records[0].get("policy") != TIME_POLICY
    ):
        raise PicoWireError("Controller time policy/header differs")
    header = records[0]
    if (
        set(header) != {"phase", "policy", "target", "tool"}
        or header["target"] != hello
        or hello["target"] != target
        or any(type(header["policy"][key]) is not int for key in TIME_POLICY)
    ):
        raise PicoWireError("Controller time evidence belongs to another boot/device")
    tool = header["tool"]
    if (
        set(tool) != {"path", "size_bytes", "sha256", "version"}
        or not isinstance(tool["path"], str)
        or not (Path(tool["path"]).is_absolute() or PureWindowsPath(tool["path"]).is_absolute())
        or type(tool["size_bytes"]) is not int
        or tool["size_bytes"] <= 0
        or not isinstance(tool["sha256"], str)
        or len(tool["sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in tool["sha256"])
        or not isinstance(tool["version"], str)
        or not tool["version"]
    ):
        raise PicoWireError("Controller host-time tool identity is malformed")
    if len(records) < 3 or len(records) % 2 != 1 or len(exchanges) != len(records) - 1:
        raise PicoWireError("Controller time refresh/audit coverage differs")
    anchors: list[tuple[int, int]] = []
    used_samples: set[str] = set()
    for i, (prepared, completed) in enumerate(zip(records[1::2], records[2::2], strict=True), 1):
        if (
            set(prepared)
            != {
                "phase",
                "sequence",
                "sample",
                "before",
                "after_response",
                "after_ack",
                "submission",
                "sample_request_id",
                "submit_request_id",
            }
            or set(completed) != {"phase", "sequence", "response", "monotonic_ns"}
            or type(prepared["sequence"]) is not int
            or type(completed["sequence"]) is not int
            or type(prepared["sample_request_id"]) is not int
            or type(prepared["submit_request_id"]) is not int
            or prepared["submit_request_id"] != prepared["sample_request_id"] + 1
            or set(prepared["sample"]) != {"sample", "monotonic_ns"}
            or not isinstance(prepared["sample"]["sample"], str)
            or prepared["sample"]["sample"] in used_samples
        ):
            raise PicoWireError("Malformed or repeated controller time observation")
        used_samples.add(prepared["sample"]["sample"])
        if (
            prepared.get("phase") != "prepared"
            or completed.get("phase") != "completed"
            or prepared.get("sequence") != i
            or completed.get("sequence") != i
        ):
            raise PicoWireError("Controller time refresh failed or has missing outcomes")
        expected = submission(
            prepared["sample"],
            prepared["before"],
            prepared["after_response"],
            prepared["after_ack"],
        )
        if expected != prepared["submission"]:
            raise PicoWireError("Retained controller time arithmetic differs")
        sample, submit = exchanges[(i - 1) * 2 : i * 2]
        if (
            sample[0]["id"] != prepared["sample_request_id"]
            or sample[0]["method"] != "GET"
            or sample[0]["path"] != "/api/usb/v1/time/sample"
            or sample[0]["body"] != ""
            or sample[0]["if_match"] != ""
            or loads(sample[1]["body"]) != prepared["sample"]
            or submit[0]["id"] != prepared["submit_request_id"]
            or submit[0]["method"] != "POST"
            or submit[0]["path"] != "/api/usb/v1/time/submit"
            or submit[0]["if_match"] != ""
            or loads(submit[0]["body"]) != expected
            or loads(submit[1]["body"]) != completed["response"]
        ):
            raise PicoWireError("Controller time evidence differs from original USB exchange")
        validate_time_response(completed["response"])
        end = completed["monotonic_ns"]
        if (
            type(end) is not int
            or not prepared["after_ack"]["monotonic_ns"]
            <= end
            <= prepared["before"]["monotonic_ns"] + 1_000_000_000
        ):
            raise PicoWireError("Controller time completion exceeds the bounded exchange")
        if anchors and end <= anchors[-1][0]:
            raise PicoWireError("Controller time refresh chronology regressed")
        anchors.append((end, prepared["before"]["monotonic_ns"] + 90_000_000_000))
    for begin, end in owned_intervals:
        prior = [a for a in anchors if a[0] <= begin]
        if not prior:
            raise PicoWireError("Armed job lacks prior qualified controller time")
        until = prior[-1][1]
        for completed_at, valid_until in anchors:
            if begin < completed_at <= end:
                if completed_at > until:
                    raise PicoWireError("Controller time expired during an owned RF interval")
                until = valid_until
        if end > until:
            raise PicoWireError("Controller time does not cover the complete RF interval")
