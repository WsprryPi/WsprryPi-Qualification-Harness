"""Production Pico USB/Soapy coordinator used by complete-test.

Setup is deliberately read-only. Every selected configuration must already be
active; no flash, engine switching, service operation, or implicit clock input
is hidden in a measurement. All waveform jobs execute locally on RP2350.
"""

from __future__ import annotations

import concurrent.futures
import copy
import math
import platform
import socket
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from wsprrypi_qualification.audio import AudioParameters, create_slot_wav, slot_wav_name
from wsprrypi_qualification.capture_metadata import load_capture_metadata
from wsprrypi_qualification.carrier import (
    CarrierParameters,
    _aligned_tone_intervals,
    analyze_carrier,
)
from wsprrypi_qualification.cw_iq import analyze_synthetic_iq
from wsprrypi_qualification.cw_reference import write_expected_events
from wsprrypi_qualification.decoder import run_wsprd
from wsprrypi_qualification.models import WsprIdentity
from wsprrypi_qualification.noise import detect
from wsprrypi_qualification.offline import artifact, validate_document, write_json_new
from wsprrypi_qualification.pico_audit import AuditedStream, WireAudit
from wsprrypi_qualification.pico_campaign import (
    check_admission,
    check_prepared_correction,
    cw_analysis_plan,
    measurement_binding,
    plan_digest,
    validate_pico_plan,
)
from wsprrypi_qualification.pico_results import settings_match, validate_pico_bundle
from wsprrypi_qualification.pico_serial import PicoCleanupError, PicoUsb, require_supported_platform
from wsprrypi_qualification.pico_wire import (
    ManagementClient,
    PicoWireError,
    WtpClient,
    loads,
)
from wsprrypi_qualification.progress import ProgressReporter
from wsprrypi_qualification.real_capabilities import (
    CaptureCapabilityError,
    CaptureCapabilityPlan,
    LocalTransportLauncher,
    RuntimeAuthorization,
    SoapyCaptureCapability,
    capability_plan_sha256,
)
from wsprrypi_qualification.receiver_calibration import interpret_frequency, validate_live_binding
from wsprrypi_qualification.wtp_control import WtpControlError, run_transaction, validate_event_job


class PicoFixtureError(PicoWireError):
    """Receiver, prerequisite, or fixture cannot support a transmitter claim."""


class PicoMeasurementError(PicoWireError):
    def __init__(self, outcome: str) -> None:
        super().__init__("Pico measurement " + outcome + "; retained analysis classifies evidence")
        self.outcome = outcome


def _error_outcome(error: BaseException) -> str:
    if isinstance(error, WtpControlError) and not error.evidence.get("cleanup_verified"):
        return "cleanup_failed"
    cause: BaseException | None = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        if isinstance(cause, (KeyboardInterrupt, InterruptedError)):
            return "aborted"
        seen.add(id(cause))
        cause = cause.__cause__
    if isinstance(error, PicoMeasurementError):
        return error.outcome
    if isinstance(error, WtpControlError):
        status = error.evidence.get("status", {})
        if isinstance(status, dict) and status.get("state") in {"failed", "missed"}:
            return "failed"
    return "blocked"


def _snapshot(client: ManagementClient, *, require_idle: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    gps_deadline: float | None = None

    def gps_observation() -> dict[str, Any]:
        nonlocal gps_deadline
        value = client.get("gps", "gps")
        active = value["boot_active"]
        if active is None or active["enabled"] is not True:
            return value
        if gps_deadline is None:
            gps_deadline = time.monotonic() + 2
        while True:
            runtime = value["runtime"]
            if (
                runtime is not None
                and all(runtime[k] is True for k in ("serial_present", "pps_present", "time_valid"))
                and int(runtime["age_ns"]) <= 3_000_000_000
                and runtime["error"] == ""
            ):
                return value
            remaining = gps_deadline - time.monotonic()
            if remaining <= 0:
                return value  # Admission retains its strict refusal and reason.
            time.sleep(min(0.02, remaining))
            value = client.get("gps", "gps")

    for resource, definition in (
        ("status", "status"),
        ("capabilities", "capabilities"),
        ("config", "config-read"),
        ("hardware", "application/$defs/hardware_read"),
        ("si5351", "si5351"),
        ("ntp-observations", "diagnostic"),
        ("gps", "gps"),
        ("rf-calibration", "diagnostic"),
        ("message-schedule", "diagnostic"),
    ):
        result[resource] = (
            gps_observation() if resource == "gps" else client.get(resource, definition)
        )
        if resource == "status" and require_idle:
            job = result[resource]["job"]
            if (
                job["owner_id"] is not None
                or job["output_active"]
                or job["state"] not in {"empty", "complete", "aborted", "missed"}
            ):
                raise PicoFixtureError(
                    "Pico is occupied; leave its existing owner and output unchanged"
                )
    # GPS estimates can advance between separate read-only resources. Retain
    # one matching current pair; never relax generation equality at admission.
    for _ in range(3):
        selection = result["rf-calibration"]["selection"]
        if selection is None or selection["next_source"] != "gps":
            break
        runtime = result["gps"]["runtime"]
        key = (
            "synthesizer_calibration"
            if result["rf-calibration"]["execution_engine"] == "si5351"
            else "timer_calibration"
        )
        estimate = runtime[key] if runtime is not None else None
        if estimate is not None and estimate["generation"] == selection["next_generation"]:
            break
        result["gps"] = gps_observation()
        result["rf-calibration"] = client.get("rf-calibration", "diagnostic")
    if client.hello is None:
        raise PicoWireError("Management hello missing")
    result["target"] = client.hello["target"]
    result["revision"] = client.etags["config"]
    verified = client.get("config", "config-read")
    if verified != result["config"] or client.etags["config"] != result["revision"]:
        raise PicoWireError("Configuration changed during Pico resource snapshot")
    return result


def _wait_for_arm_preparation(
    start_ns: int, receiver_ready: Callable[[], None], cancel: threading.Event
) -> None:
    """Keep the final resource snapshot fresh inside the unchanged ARM bound."""
    deadline = time.monotonic() + 45
    while True:
        receiver_ready()
        if cancel.is_set():
            raise InterruptedError("Pico controller cancelled before ARM preparation")
        remaining = (start_ns - time.time_ns()) / 1e9 - 7
        if remaining <= 0:
            return
        if time.monotonic() >= deadline:
            raise PicoFixtureError("Controller UTC moved during ARM preparation")
        cancel.wait(min(0.1, remaining))


def _private_destination(parent: Path) -> Path:
    parent = parent.absolute()
    for ancestor in (parent, *parent.parents):
        if ancestor.is_symlink() or (ancestor / ".git").exists():
            raise PicoWireError("Pico evidence must be outside checkouts and symlinked directories")
    parent.mkdir(parents=True, exist_ok=True)
    output = parent / ("pico-" + uuid.uuid4().hex)
    output.mkdir(mode=0o700)
    return output


def _authenticate_plan(plan: dict[str, Any]) -> None:
    validate_document(plan, "pico-campaign-plan.schema.json")
    for item in (
        plan["compiler"],
        plan["installer"],
        *plan["encoder_sources"],
        *plan["analyzer_source"]["files"],
    ):
        if artifact(Path(item["path"])) != item:
            raise PicoWireError("Selected compiler/source binding changed before live access")
    receiver = plan["configuration"]["receiver"]
    if receiver["host"].lower().removesuffix(".local") not in {
        "localhost",
        "127.0.0.1",
        socket.gethostname().lower().split(".")[0],
        socket.getfqdn().lower().split(".")[0],
    }:
        raise PicoWireError(
            "Run Pico complete-test on the selected USB/SDR host; "
            "remote live delegation is unsupported"
        )
    for name in (
        ("capture_helper", "wsprd")
        if any(row["mode"] == "wspr" for row in plan["modes"])
        else ("capture_helper",)
    ):
        if not Path(receiver[name]).is_absolute() or not Path(receiver[name]).is_file():
            raise PicoWireError("Pico campaign requires installed absolute capture/decoder paths")
    # Current maintained wsprd identity parser qualifies ordinary Type 1 WSPR.
    # The production compiler still validates Type 2/3 plans without claiming
    # that the Type 1 decoder can qualify paired messages.
    if any(row["mode"] == "wspr" and len(row["jobs"]) != 1 for row in plan["modes"]):
        raise PicoWireError("Live Pico WSPR qualification currently requires a Type 1 station")
    validate_live_binding(
        plan["receiver_calibration"],
        receiver={
            **receiver,
            "device": receiver["serial"],
        },
        indicated_frequencies_hz=[plan["configuration"]["protocol"]["frequency_hz"]],
    )


class PicoLiveAdapter:
    """Own bounded USB and capture lifetimes, including failed acquisitions."""

    def __init__(
        self,
        plan: dict[str, Any],
        output: Path,
        reporter: ProgressReporter,
        cancellation: threading.Event,
    ) -> None:
        self.plan, self.output, self.reporter, self.cancel = plan, output, reporter, cancellation
        receiver = plan["configuration"]["receiver"]
        self.tools = {
            name: artifact(Path(receiver[name]))
            for name in (
                ("capture_helper", "wsprd")
                if any(row["mode"] == "wspr" for row in plan["modes"])
                else ("capture_helper",)
            )
        }
        write_json_new(
            output / "controller.json",
            dict(python=sys.version, platform=platform.platform(), tools=self.tools),
        )
        self.audit = WireAudit(output / "usb-io.jsonl")
        try:
            self.usb = PicoUsb(plan, cancellation)
            self.management = ManagementClient(
                AuditedStream(self.usb.management, self.audit, "management"),
                plan["configuration"]["pico"]["device_id"],
            )
            self.discovery = self.management.connect()
            self._wtp: WtpClient | None = None
            self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        except BaseException as primary:
            errors = []
            try:
                if hasattr(self, "usb"):
                    self.usb.close()
            except BaseException as cleanup:
                errors.append(cleanup)
            try:
                self.audit.close()
            except BaseException as cleanup:
                errors.append(cleanup)
            if errors:
                raise PicoCleanupError(primary, errors[0]) from primary
            raise
        self.capture = SoapyCaptureCapability(LocalTransportLauncher())
        self.jobs_attempted = False
        self.futures: list[concurrent.futures.Future[dict[str, object]]] = []
        self.receiver_cleanup_failed = False

    def snapshot(self, *, initial: bool = False) -> dict[str, Any]:
        return _snapshot(self.management, require_idle=initial)

    @property
    def wtp(self) -> WtpClient:
        if self._wtp is None:
            raise PicoWireError("Pico WTP requires successful management admission first")
        return self._wtp

    def start_wtp(self) -> None:
        self._wtp = WtpClient(AuditedStream(self.usb.open_wtp(self.cancel), self.audit, "wtp"))

    def begin_cleanup(self) -> None:
        self.audit.cleaning_up = True
        self.usb.begin_cleanup()

    def inactive(self) -> dict[str, Any]:
        status = self.wtp.request("STATUS", {}, timeout=3)
        if (
            self.management.hello is None
            or status["boot_id"] != self.management.hello["target"]["boot_id"]
        ):
            raise PicoWireError("Pico boot changed during inactivity verification")
        if status["output_active"] or status["state"] in {"loaded", "armed", "running", "failed"}:
            raise PicoWireError("Pico job/output is not quiescent")
        if self.plan["configuration"]["pico"]["engine"] == "si5351":
            si = self.management.get("si5351", "si5351")
            if (
                si["target"] != self.management.hello["target"]
                or si["runtime"] is None
                or si["runtime"]["transmit"] != "register_disabled"
            ):
                raise PicoWireError("Independent Si5351 register-disable observation failed")
            status["si5351_transmit"] = si["runtime"]["transmit"]
        return status

    def _capture_plan(self, directory: Path, name: str, duration: float) -> CaptureCapabilityPlan:
        r = self.plan["configuration"]["receiver"]
        stem = name + "-" + uuid.uuid4().hex
        plan = CaptureCapabilityPlan(
            Path(r["capture_helper"]),
            directory / f"{stem}-metadata.json",
            directory / f"{stem}.cf32",
            r["driver"],
            r["serial"],
            r["channel"],
            r["sample_rate_hz"],
            r["bandwidth_hz"],
            r["center_frequency_hz"],
            r["gain_db"],
            math.ceil(duration * r["sample_rate_hz"]),
            1_000_000,
            duration + 20,
            0.999,
        )
        write_json_new(directory / f"{name}-plan.json", plan.document())
        return plan

    def _capture(self, plan: CaptureCapabilityPlan) -> dict[str, object]:
        if artifact(plan.helper) != self.tools["capture_helper"]:
            raise PicoFixtureError("Capture helper changed during the campaign")
        authorization = RuntimeAuthorization(
            capability_plan_sha256(plan.document()),
            "pico-complete-test",
            datetime.now(UTC),
            True,
            False,
        )
        try:
            return self.capture.execute(plan, authorization, self.cancel)
        except CaptureCapabilityError as error:
            verified = False
            try:
                if error.diagnostic_path is not None:
                    diagnostic = loads(error.diagnostic_path.read_bytes())
                    native = (
                        diagnostic["native_failure_metadata"]
                        or diagnostic["rejected_capture_metadata"]
                    )
                    verified = (
                        diagnostic["execution"]["cleanup_verified"] is True
                        and native is not None
                        and load_capture_metadata(Path(native["path"])).cleanup_outcome
                        == "verified"
                    )
            except (OSError, ValueError, KeyError, TypeError):
                pass
            self.receiver_cleanup_failed |= not verified
            raise

    def observe(
        self,
        mode: str,
        job: dict[str, Any],
        directory: Path,
        carrier_job: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        directory.mkdir()
        self.audit.cleaning_up = False
        self.usb.begin_operations(self.cancel)
        acquired_frequency = None
        if mode == "wspr":
            if carrier_job is None:
                raise PicoWireError("WSPR requires its planned carrier acquisition gate")
            carrier_result = self.observe("tone", carrier_job, directory / "carrier")
            if carrier_result["outcome"] != "passed":
                raise PicoMeasurementError(carrier_result["outcome"])
            acquired_frequency = loads((directory / "carrier/carrier.json").read_bytes())[
                "metrics"
            ]["strongest_transmitter_added_frequency_hz"]
        snapshot = self.snapshot()
        check_admission(self.plan, snapshot)
        write_json_new(directory / "before.json", snapshot)
        hello = self.wtp.request(
            "HELLO",
            dict(versions=["WTP/1"], client_name="qualification-harness", client_version="1"),
        )
        if (
            hello["boot_id"] != snapshot["target"]["boot_id"]
            or hello["device_id"] != snapshot["target"]["device_id"]
            or hello["firmware_version"] != self.discovery["firmware"]
        ):
            raise PicoWireError("WTP and management discovery identities differ")
        caps = self.wtp.request("CAPS", {})
        expected_engine = self.plan["configuration"]["pico"]["engine"]
        if (
            caps != snapshot["capabilities"]["wtp"]
            or caps["engine"] != expected_engine
            or mode not in caps["modes"]
            or "rf-events/1" not in caps["profiles"]
        ):
            raise PicoWireError("WTP capabilities differ from selected Pico engine/mode")
        validate_event_job(
            job,
            maximum_events=caps["max_events"],
            maximum_duration_ns=int(caps["max_job_duration_ns"]),
        )
        for event in job["events"]:
            if event["rf_on"] and not any(
                int(r["minimum_nhz"]) <= int(event["frequency_nhz"]) <= int(r["maximum_nhz"])
                for r in caps["frequency_ranges"]
            ):
                raise PicoWireError("Job frequency lies outside advertised Pico ranges")
        write_json_new(directory / "capabilities.json", caps)
        off_plan = self._capture_plan(directory, "rf-off", 2)
        self._capture(off_plan)
        slot: datetime | None = None
        gps_wspr = mode == "wspr" and self.plan["configuration"]["pico"]["gps_enabled"]
        if mode == "wspr":
            # LOAD and the full GPS snapshot can each take several seconds.
            # Start capture/preparation earlier without moving RF off its slot.
            capture_lead_ns = 20_000_000_000 if gps_wspr else 8_000_000_000
            slot_notice_ns = 30_000_000_000 if gps_wspr else 15_000_000_000
            slot_ns = (
                math.ceil((time.time_ns() + slot_notice_ns) / 120_000_000_000) * 120_000_000_000
            )
            slot = datetime.fromtimestamp(slot_ns / 1e9, UTC)
            wait_deadline = time.monotonic() + (160 if gps_wspr else 130)
            while time.time_ns() < slot_ns - capture_lead_ns:
                if time.monotonic() >= wait_deadline:
                    raise PicoFixtureError("Controller UTC moved during WSPR slot preparation")
                if self.cancel.wait(0.1):
                    raise InterruptedError("Pico controller cancelled before WSPR acquisition")
        duration = 140 if mode == "wspr" else int(job["total_duration_ns"]) / 1e9 + 20
        capture_plan = self._capture_plan(directory, "rf-on", duration)
        future = self.pool.submit(self._capture, capture_plan)
        self.futures.append(future)
        incomplete = Path(str(capture_plan.output_path) + ".incomplete")

        def ready() -> None:
            if self.cancel.is_set():
                raise InterruptedError("Pico controller cancelled")
            if future.done():
                future.result()
                raise PicoFixtureError(
                    "Receiver ended before the complete transaction and quiet margin"
                )

        record: dict[str, Any] | None = None
        try:
            deadline = time.monotonic() + 12
            while (
                not incomplete.exists()
                or incomplete.stat().st_size < 2 * capture_plan.sample_rate_hz * 8
            ):
                ready()
                if time.monotonic() >= deadline:
                    raise PicoFixtureError("Receiver readiness/preamble deadline")
                self.cancel.wait(0.05)
            ready()
            prefix = np.fromfile(incomplete, dtype="<c8", count=2 * capture_plan.sample_rate_hz)
            if (
                prefix.size != 2 * capture_plan.sample_rate_hz
                or not np.all(np.isfinite(prefix))
                or np.any(np.abs(prefix.real) >= 0.999)
                or np.any(np.abs(prefix.imag) >= 0.999)
            ):
                raise PicoFixtureError("Receiver preamble is incomplete, nonfinite, or clipped")
            p = self.plan["configuration"]["protocol"]
            _channel, active, noise = detect(
                prefix,
                capture_plan.sample_rate_hz,
                capture_plan.center_frequency_hz,
                p["frequency_hz"],
                p["frequency_hz"] - p["shift_hz"] if mode in {"fskcw", "dfcw"} else None,
                1000,
                2,
                10,
            )
            if (
                set(noise["issues"])
                & {
                    "contaminated_noise_reference",
                    "nonstationary_noise_reference",
                    "unusable_noise_reference",
                }
                or float(np.mean(active)) > 0.01
            ):
                raise PicoFixtureError("Receiver RF-off preamble is not quiet in the DUT channel")
            prefix_path = directory / "preamble.cf32"
            prefix.tofile(prefix_path)
            write_json_new(
                directory / "readiness.json",
                dict(prefix=artifact(prefix_path), noise=noise, output_active=False),
            )
            if (
                slot is not None
                and time.time_ns() > int(slot.timestamp()) * 1_000_000_000 - 3_000_000_000
            ):
                raise PicoFixtureError("WSPR setup missed the five-second pre-slot capture margin")
            start_ns = (
                int(slot.timestamp() * 1e9) + 2_000_000_000
                if slot
                else time.time_ns()
                + (
                    7_000_000_000
                    if self.plan["configuration"]["pico"]["gps_enabled"]
                    else 5_000_000_000
                )
            )

            load_gps: list[dict[str, Any]] = []

            def before_load() -> None:
                if self.plan["configuration"]["pico"]["automatic_correction"]:
                    value = self.management.get("gps", "gps")
                    if value["target"] != snapshot["target"]:
                        raise PicoWireError("GPS target changed before LOAD")
                    write_json_new(directory / "load-gps-before.json", value)
                    load_gps.append(value)

            def before_arm() -> dict[str, Any]:
                if self.plan["configuration"]["pico"]["automatic_correction"]:
                    gps = self.management.get("gps", "gps")
                    write_json_new(directory / "load-gps-after.json", gps)
                    load_gps.append(gps)
                if gps_wspr:
                    # Preserve the immediate post-LOAD GPS input above; defer
                    # only the general snapshot until the bounded ARM window.
                    _wait_for_arm_preparation(start_ns, ready, self.cancel)
                value = self.snapshot()
                check_admission(self.plan, value, owned=True)
                check_prepared_correction(
                    self.plan, value, [snapshot["gps"], *load_gps, value["gps"]]
                )
                write_json_new(directory / "prepared.json", value)
                return dict(value["ntp-observations"])

            self.jobs_attempted = True
            record = run_transaction(
                self.wtp,
                job,
                device_id=snapshot["target"]["device_id"],
                start_utc_ns=start_ns,
                before_arm=before_arm,
                receiver_ready=ready,
                verify_inactive=self.inactive,
                expected_boot_id=snapshot["target"]["boot_id"],
                maximum_events=caps["max_events"],
                maximum_duration_ns=int(caps["max_job_duration_ns"]),
                maximum_start_uncertainty_ns=int(caps["maximum_arm_uncertainty_ns"]),
                minimum_arm_lead_ns=int(caps["minimum_arm_lead_ns"]),
                maximum_arm_ahead_ns=min(9_000_000_000, int(caps["maximum_arm_ahead_ns"])),
                before_cleanup=self.begin_cleanup,
                before_load=before_load,
            )
            if not record["completed"] or not record["cleanup_verified"]:
                raise WtpControlError(
                    "Pico transaction did not complete with verified cleanup", record
                )
            future.result(timeout=max(1, capture_plan.maximum_elapsed_s))
            metadata = load_capture_metadata(capture_plan.metadata_path)
            if metadata.retained_capture_start_utc is None:
                raise PicoFixtureError("Capture lacks retained UTC provenance")
            pre_quiet = start_ns / 1e9 - metadata.retained_capture_start_utc.timestamp()
            if pre_quiet < 2 or duration - pre_quiet - int(job["total_duration_ns"]) / 1e9 < 2:
                raise PicoFixtureError("Capture does not contain preamble/job/post-job silence")
            return self.analyze(
                mode,
                directory,
                snapshot,
                capture_plan,
                off_plan,
                slot,
                pre_quiet,
                acquired_frequency,
            )
        except WtpControlError as error:
            record = error.evidence
            raise
        finally:
            if not future.done():
                self.cancel.set()
            try:
                future.result(timeout=15)
            except BaseException as error:
                # The capture capability retains its complete failure receipt.
                # The original transaction/analysis exception remains primary.
                if record is not None:
                    record["receiver_cleanup_pending"] = not future.done()
                write_json_new(
                    directory / "receiver-error.json",
                    dict(
                        kind=type(error).__name__,
                        detail=str(error),
                        cleanup_pending=not future.done(),
                    ),
                )
            if record is not None:
                write_json_new(directory / "transaction.json", record)

    def analyze(
        self,
        mode: str,
        directory: Path,
        snapshot: dict[str, Any],
        capture: CaptureCapabilityPlan,
        off: CaptureCapabilityPlan,
        slot: datetime | None,
        pre_quiet: float,
        acquired_frequency: float | None = None,
    ) -> dict[str, Any]:
        config = self.plan["configuration"]
        p, r = config["protocol"], config["receiver"]
        if mode == "wspr":
            if slot is None:
                raise PicoFixtureError("WSPR slot missing")
            metadata = load_capture_metadata(capture.metadata_path)
            if metadata.retained_capture_start_utc is None:
                raise PicoFixtureError("WSPR capture UTC missing")
            captured = {
                **artifact(capture.metadata_path),
                "capture_id": metadata.capture_id,
                "retained_capture_start_utc": (
                    metadata.retained_capture_start_utc.isoformat().replace("+00:00", "Z")
                ),
                "retained_sample_count": metadata.retained_sample_count,
            }
            wav = directory / slot_wav_name(slot)
            create_slot_wav(
                capture.output_path,
                metadata.retained_capture_start_utc,
                slot,
                wav,
                directory / "audio.json",
                AudioParameters(
                    r["sample_rate_hz"],
                    r["center_frequency_hz"],
                    acquired_frequency if acquired_frequency is not None else p["frequency_hz"],
                ),
                capture_evidence=captured,
                profile_evidence=measurement_binding(self.plan),
            )
            if artifact(Path(r["wsprd"])) != self.tools["wsprd"]:
                raise PicoFixtureError("Decoder changed during the campaign")
            result = run_wsprd(
                wav,
                directory / "decode.json",
                WsprIdentity(p["callsign"], p["locator"][:4], p["power_dbm"]),
                executable=Path(r["wsprd"]),
                slot_utc=slot,
                capture_evidence=captured,
                profile_evidence=measurement_binding(self.plan),
                audio_evidence=artifact(directory / "audio.json"),
                data_directory=directory / "wsprd-data",
            )
            return dict(
                outcome=result["gate_outcome"],
                analysis=artifact(directory / "decode.json"),
                directory=str(directory),
            )
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-pico-" + uuid.uuid4().hex[:8]
        cw = cw_analysis_plan(self.plan, mode, run_id, pre_quiet)
        cw["capture_contract"]["sample_count"] = capture.sample_count
        cw["transmitter"]["drive_value"] = (
            snapshot["si5351"]["boot_active"]["drive_ma"]
            if config["pico"]["engine"] == "si5351"
            else snapshot["status"]["standalone"]["gpio_drive_ma"]
        )
        cw["transmitter"]["drive_unit"] = "mA"
        cw["transmitter"]["clock_reference"] = (
            str(snapshot["ntp-observations"]["nominal_sample_hz"])
            + " Hz nominal timer; RF reference retained in before.json"
        )
        write_json_new(directory / "mode-plan.json", cw, schema_name="cw-mode-plan.schema.json")
        write_expected_events(
            directory / "mode-plan.json",
            directory / "expected.json",
            source_revision=self.plan["analyzer_source"]["revision"],
        )
        acquired = dict(
            schema_version=1,
            evidence_type="cw_acquired_capture",
            run_id=run_id,
            mode=mode,
            plan=artifact(directory / "mode-plan.json"),
            expected_events=artifact(directory / "expected.json"),
            capture=artifact(capture.output_path),
            format="CF32LE",
            sample_count=capture.sample_count,
            sample_rate_hz=capture.sample_rate_hz,
            center_frequency_hz=capture.center_frequency_hz,
            acquired_sample_count=capture.sample_count,
            overflow_count=0,
            fixed_gain=True,
            agc_enabled=False,
            bias_tee_enabled=False,
            first_read_discarded=True,
            receiver=cw["receiver"],
            acquired_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            synthetic=False,
        )
        write_json_new(
            directory / "acquired.json", acquired, schema_name="cw-acquired-capture.schema.json"
        )
        observations, _gate = analyze_synthetic_iq(
            directory / "mode-plan.json",
            directory / "expected.json",
            directory / "acquired.json",
            directory / "observations.json",
            directory / "gate.json",
            source_revision=self.plan["analyzer_source"]["revision"],
            _metadata_schema="cw-acquired-capture.schema.json",
            _synthetic=False,
        )
        outcome = observations["analysis_outcome"]
        if mode == "tone":
            fft_size = min(262144, 2 ** int(math.log2(2 * r["sample_rate_hz"])))
            expected = loads((directory / "expected.json").read_bytes())
            carrier = analyze_carrier(
                off.output_path,
                capture.output_path,
                CarrierParameters(
                    r["sample_rate_hz"],
                    r["center_frequency_hz"],
                    p["frequency_hz"],
                    fft_size=fft_size,
                    temporal_on_intervals_s=_aligned_tone_intervals(
                        capture.output_path, cw, expected
                    ),
                ),
                directory / "carrier.json",
                rf_off_metadata_path=off.metadata_path,
                rf_on_metadata_path=capture.metadata_path,
                acquired=True,
                profile_evidence=measurement_binding(self.plan),
                temporal_cw_reference={
                    "alignment_policy": "independent_tone_duration_v1",
                    "plan": artifact(directory / "mode-plan.json"),
                    "expected_events": artifact(directory / "expected.json"),
                },
                receiver_calibration=self.plan["receiver_calibration"],
            )
            if carrier["gate_outcome"] != "passed":
                outcome = carrier["gate_outcome"]
        measured = [
            v["frequency_hz"]
            for v in observations["observations"]
            if v.get("frequency_hz") is not None
        ]
        write_json_new(
            directory / "receiver-interpretation.json",
            {
                "receiver_calibration": self.plan["receiver_calibration"],
                "frequencies": [
                    interpret_frequency(self.plan["receiver_calibration"], f) for f in measured
                ],
            },
        )
        return dict(
            outcome=outcome, analysis=artifact(directory / "gate.json"), directory=str(directory)
        )

    def close(self) -> None:
        self.begin_cleanup()
        self.cancel.set()
        pending = any(not future.done() for future in self.futures)
        self.pool.shutdown(wait=False, cancel_futures=True)
        try:
            try:
                self.usb.close()
            finally:
                self.audit.close()
        finally:
            if pending or self.receiver_cleanup_failed:
                raise PicoFixtureError("Receiver cleanup is still pending; qualification refused")


def run_pico_campaign(
    plan: dict[str, Any],
    output_parent: Path,
    reporter: ProgressReporter,
    *,
    enable_rf: bool,
    operator: str,
    rehearse: bool = False,
) -> dict[str, Any]:
    validate_pico_plan(plan)
    plan = copy.deepcopy(plan)
    if rehearse and enable_rf:
        raise PicoWireError("Rehearsal conflicts with RF authorization")
    if not rehearse and (not enable_rf or not operator.strip()):
        raise PicoWireError("Live Pico campaign requires explicit RF authority and operator")
    if not rehearse:
        require_supported_platform()
        _authenticate_plan(plan)
    output = _private_destination(output_parent)
    write_json_new(
        output / "requested-plan.json", plan, schema_name="pico-campaign-plan.schema.json"
    )
    result: dict[str, Any] = dict(
        schema_version=1,
        target_kind="pico",
        plan_sha256=plan_digest(plan),
        rehearsal=rehearse,
        qualification_claim=False,
        outcome="inconclusive",
        cleanup_verified=rehearse,
        settings_unchanged=None,
        transmitter_transaction_attempted=False,
        modes=[],
        errors=[],
        artifacts=[],
        output_directory=str(output),
    )
    adapter: PicoLiveAdapter | None = None
    cancellation = threading.Event()
    initial = None
    try:
        if rehearse:
            write_json_new(output / "plan.json", plan, schema_name="pico-campaign-plan.schema.json")
            result["modes"] = [
                dict(mode=row["mode"], observations=[], outcome="unattempted")
                for row in plan["modes"]
            ]
            result["outcome"] = "rehearsed"
        else:
            adapter = PicoLiveAdapter(plan, output, reporter, cancellation)
            initial = adapter.snapshot(initial=True)
            write_json_new(output / "initial.json", initial)
            if plan["configuration"]["pico"]["configuration_revision"] is None:
                plan["configuration"]["pico"]["configuration_revision"] = initial["revision"]
            result["plan_sha256"] = plan_digest(plan)
            write_json_new(output / "plan.json", plan, schema_name="pico-campaign-plan.schema.json")
            check_admission(plan, initial)
            write_json_new(output / "usb-topology.json", adapter.usb.identity)
            adapter.start_wtp()
            for row in plan["modes"]:
                entry: dict[str, Any] = dict(
                    mode=row["mode"], observations=[], outcome="inconclusive"
                )
                result["modes"].append(entry)
                reporter.emit("mode", "started", "Pico mode started", mode=row["mode"].upper())
                for number in range(1, 4):
                    observation: dict[str, Any] = dict(
                        number=number, frames=[], outcome="inconclusive"
                    )
                    entry["observations"].append(observation)
                    for index, job in enumerate(row["jobs"]):
                        reporter.emit(
                            "capture",
                            "started",
                            "Pico independent observation",
                            mode=row["mode"].upper(),
                            item=number,
                            item_count=3,
                        )
                        measured = adapter.observe(
                            row["mode"],
                            job,
                            output / f"{row['mode']}-{number}-{index + 1}",
                            carrier_job=row["carrier_job"],
                        )
                        observation["frames"].append(measured)
                        if measured["outcome"] != "passed":
                            observation["outcome"] = measured["outcome"]
                            entry["outcome"] = measured["outcome"]
                            raise PicoMeasurementError(measured["outcome"])
                    observation["outcome"] = "passed"
                entry["outcome"] = "passed"
                reporter.emit("mode", "completed", "Pico mode completed", mode=row["mode"].upper())
            result["outcome"] = "passed"
    except BaseException as error:
        result["errors"].append(dict(kind=type(error).__name__, detail=str(error)))
        result["outcome"] = _error_outcome(error)
        if adapter is None and not isinstance(error, PicoCleanupError):
            # Construction closes its owned interfaces before rethrowing.
            # No transmitter transaction has been attempted at this point.
            result["cleanup_verified"] = True
    finally:
        if adapter is not None:
            result["transmitter_transaction_attempted"] = adapter.jobs_attempted
            try:
                adapter.begin_cleanup()
                if adapter.jobs_attempted:
                    adapter.inactive()
                if adapter.jobs_attempted or initial is not None:
                    final = adapter.snapshot()
                    write_json_new(output / "final.json", final)
                    if initial is None:
                        raise PicoWireError("Initial settings snapshot unavailable")
                    result["settings_unchanged"] = settings_match(initial, final)
                result["cleanup_verified"] = (
                    result["settings_unchanged"] is True if adapter.jobs_attempted else True
                )
            except BaseException as error:
                result["errors"].append(dict(kind=type(error).__name__, detail=str(error)))
                result["outcome"] = "cleanup_failed"
            finally:
                try:
                    adapter.close()
                except BaseException as error:
                    result["errors"].append(dict(kind=type(error).__name__, detail=str(error)))
                    result["outcome"] = "cleanup_failed"
                    result["cleanup_verified"] = False
        if not (output / "plan.json").exists():
            write_json_new(output / "plan.json", plan, schema_name="pico-campaign-plan.schema.json")
        if not result["cleanup_verified"] and not rehearse:
            result["outcome"] = "cleanup_failed"
        result["qualification_claim"] = result["outcome"] == "passed" and result["cleanup_verified"]
        result["artifacts"] = [
            artifact(path) for path in sorted(output.rglob("*")) if path.is_file()
        ]
        try:
            validate_pico_bundle(output, result_document=result)
        except (OSError, ValueError, KeyError, TypeError) as error:
            result["errors"].append(dict(kind=type(error).__name__, detail=str(error)))
            result["outcome"] = "blocked" if result["cleanup_verified"] else "cleanup_failed"
            result["qualification_claim"] = False
        write_json_new(
            output / "result.json", result, schema_name="pico-campaign-result.schema.json"
        )
        reporter.emit("campaign", "completed", "Pico campaign " + result["outcome"])
    return result
