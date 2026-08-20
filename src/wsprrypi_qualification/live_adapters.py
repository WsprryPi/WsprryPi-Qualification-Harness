"""Concrete split-host adapter composition for the reviewed real-session lifecycle.

The coordinator is intended to run on the receiver host.  Receiver capture and
offline processing are local; the transmitter is owned through the persistent
SSH capability helper.  Construction is explicit so a CLI cannot silently
substitute mocks or a different transport.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TypeVar, cast

from wsprrypi_qualification.application_shims import (
    ApplicationIdentity,
    ApplicationPlan,
    ToneProtocol,
    WsprProtocol,
    WsprryPiBackendConfig,
    WsprryPiShim,
)
from wsprrypi_qualification.cw_iq import analyze_synthetic_iq
from wsprrypi_qualification.cw_reference import generate_expected_events
from wsprrypi_qualification.offline import artifact, load_json_document, write_json_new
from wsprrypi_qualification.offline_context import load_profile_context
from wsprrypi_qualification.real_capabilities import (
    CaptureCapabilityPlan,
    GpioQuiescenceCapability,
    HelperServiceProvider,
    JsonHelperClient,
    LocalTransportLauncher,
    OwnedProcess,
    PersistentHelperTransport,
    RuntimeAuthorization,
    Si5351QuiescenceCapability,
    SoapyCaptureCapability,
    SshOwnedProcessLauncher,
    capability_plan_sha256,
)
from wsprrypi_qualification.real_session import (
    HELPER_VERIFICATION_OPERATIONS,
    RealSessionError,
    helper_configuration_plan_sha256,
    helper_verification_contract,
    helper_verification_deadline,
    resolved_real_plan_sha256,
)

_COHERENT_CAPTURE_STARTUP_GUARD_S = 2.0
_T = TypeVar("_T")


def _stage_bound_artifact(binding: dict[str, object], destination: Path) -> dict[str, Any]:
    """Authenticate a sealed input and retain a new private copy for live analysis."""
    source = Path(str(binding["path"]))
    try:
        source_identity = artifact(source)
    except OSError as error:
        raise RealSessionError(f"sealed live artifact is unavailable: {source}") from error
    if any(source_identity[key] != binding[key] for key in ("size_bytes", "sha256")):
        raise RealSessionError(f"sealed live artifact identity changed: {source}")
    if destination.exists():
        raise RealSessionError(f"refusing to overwrite retained live artifact: {destination}")
    try:
        shutil.copyfile(source, destination)
    except OSError as error:
        raise RealSessionError(f"could not retain sealed live artifact: {source}") from error
    retained = artifact(destination)
    if any(retained[key] != binding[key] for key in ("size_bytes", "sha256")):
        raise RealSessionError(f"retained live artifact identity changed: {destination}")
    return retained


def _derive_rebound_expected_events(
    sealed_destination: Path,
    derived_destination: Path,
    retained_plan: dict[str, Any],
) -> dict[str, Any]:
    """Derive an analysis-local expected-events copy from a retained sealed source."""
    expected = load_json_document(sealed_destination, "cw-expected-events.schema.json")
    expected["plan"] = retained_plan
    write_json_new(
        derived_destination,
        expected,
        schema_name="cw-expected-events.schema.json",
    )
    return artifact(derived_destination)


def _coherent_capture_launch_epoch(first_slot: datetime, required_margin_s: float) -> float:
    """Start early enough for receiver setup while preserving the retained-data margin."""
    return first_slot.timestamp() - required_margin_s - _COHERENT_CAPTURE_STARTUP_GUARD_S


def _retained_capture_has_margin(
    metadata: dict[str, Any], first_slot: datetime, required_margin_s: float
) -> bool:
    retained_start = datetime.fromisoformat(
        metadata["timestamps"]["retained_capture_start_utc"].replace("Z", "+00:00")
    )
    return retained_start <= first_slot - timedelta(seconds=required_margin_s)


def _intentional_carrier_stop_verified(result: object) -> bool:
    execution = cast(Any, result)
    return bool(
        execution.stop_requested
        and execution.running_before_stop is True
        and execution.cancelled
        and not execution.timed_out
        and not execution.disconnected
        and execution.cleanup_verified
    )


def _owned_process_released(result: object) -> bool:
    """Return whether the helper proved that it no longer owns the process."""
    execution = cast(Any, result)
    return bool(execution.stop_requested and execution.cleanup_verified)


@dataclass(frozen=True)
class LiveAdapterPaths:
    work_directory: Path
    bench_profile: Path
    test_profile: Path
    receiver_run_profile: Path
    capture_helper: Path
    wsprd: Path


def _artifact_references(value: object) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    if isinstance(value, dict):
        if {"path", "size_bytes", "sha256"} <= value.keys():
            records.append(value)
        for child in value.values():
            records.extend(_artifact_references(child))
    elif isinstance(value, list):
        for child in value:
            records.extend(_artifact_references(child))
    return records


class ProductionRealSessionAdapters:
    """Sealed production adapter; no subclass or fake may opt into live mode."""

    execution_mode = "live"

    def __init__(
        self,
        *,
        transmitter_client: JsonHelperClient,
        receiver_client: JsonHelperClient,
        transmitter_launcher: SshOwnedProcessLauncher,
        source_launcher: SshOwnedProcessLauncher,
        capture_capability: SoapyCaptureCapability,
        paths: LiveAdapterPaths,
    ) -> None:
        if type(self) is not ProductionRealSessionAdapters:
            raise TypeError("production real-session adapter is sealed")
        for path in (
            paths.bench_profile,
            paths.test_profile,
            paths.receiver_run_profile,
            paths.capture_helper,
            paths.wsprd,
        ):
            if not path.is_absolute() or not path.is_file():
                raise RealSessionError("production adapter paths must be existing absolute files")
        if not paths.work_directory.is_absolute():
            raise RealSessionError("production adapter work directory must be absolute")
        self.tx_client = transmitter_client
        self.rx_client = receiver_client
        self.tx_launcher = transmitter_launcher
        self.source_launcher = source_launcher
        self.capture_capability = capture_capability
        self.paths = paths
        self.tx_services = HelperServiceProvider(transmitter_client, "systemd")
        self.rx_services = HelperServiceProvider(receiver_client, "systemd")
        self._initial_services: dict[tuple[str, str], bool] = {}
        self._changed_services: list[tuple[str, str]] = []
        self._owned: list[OwnedProcess] = []
        self._capture_tasks: list[tuple[threading.Thread, threading.Event]] = []
        self._cleanup_installed = False
        self._artifacts: list[Path] = [
            paths.bench_profile,
            paths.test_profile,
            paths.receiver_run_profile,
            paths.capture_helper,
            paths.wsprd,
        ]
        self._capture_artifacts: dict[str, tuple[Path, Path]] = {}
        self._final_quiescence: bool | None = None
        self._session_deadline: float | None = None
        self._cleanup_reserve_s = 0.0
        self._closed = False

    def begin_session(self, plan: dict[str, Any]) -> None:
        if self._session_deadline is not None:
            raise RealSessionError("production adapter session already began")
        self._session_deadline = time.monotonic() + plan["deadlines"]["overall_s"]
        self._cleanup_reserve_s = plan["deadlines"]["cleanup_s"]

    def _remaining(self, requested: float, *, reserve_cleanup: bool = False) -> float:
        if self._session_deadline is None:
            raise RealSessionError("production adapter session deadline is not installed")
        remaining = (
            self._session_deadline
            - time.monotonic()
            - (self._cleanup_reserve_s if reserve_cleanup else 0.0)
        )
        if remaining <= 0:
            raise RealSessionError("real qualification overall deadline expired")
        return min(requested, remaining)

    def _stage(
        self,
        plan: dict[str, Any],
        kind: str,
        outcome: str,
        details: dict[str, object],
        deadline: float,
        started: float,
    ) -> dict[str, object]:
        elapsed = time.monotonic() - started
        if elapsed > deadline:
            raise RealSessionError(f"{kind} exceeded its hard deadline")
        return {
            "schema_version": 1,
            "evidence_type": kind,
            "plan_sha256": resolved_real_plan_sha256(plan),
            "outcome": outcome,
            "elapsed_s": elapsed,
            "deadline_s": deadline,
            "details": details,
        }

    def discover_capabilities(self, plan: dict[str, Any]) -> dict[str, object]:
        started = time.monotonic()
        if socket.gethostname() != plan["receiver"]["observed_local_hostname"]:
            raise RealSessionError("controller host differs from the resolved receiver host")
        expected = {
            "soapy": artifact(self.paths.capture_helper)["sha256"],
            "decoder": artifact(self.paths.wsprd)["sha256"],
        }
        if any(plan["capability_bindings"][key] != value for key, value in expected.items()):
            raise RealSessionError("local capture or decoder executable identity changed")
        for name, path in (
            ("bench", self.paths.bench_profile),
            ("test", self.paths.test_profile),
            ("receiver_run", self.paths.receiver_run_profile),
        ):
            if artifact(path)["sha256"] != plan["resolved_profiles"][name]["sha256"]:
                raise RealSessionError(f"resolved {name} profile identity changed")
        if plan.get("session_kind") == "cw_live_tone":
            for role in ("plan", "expected_events"):
                binding = plan["cw_contract"][role]
                path = Path(binding["path"])
                if not path.is_absolute() or artifact(path) != binding:
                    raise RealSessionError(f"pinned CW {role} identity changed")
            self._validate_tone_contract(plan)
        context = load_profile_context(self.paths.bench_profile, self.paths.test_profile)
        if (
            context.test.frequency_hz != plan["frequency_hz"]
            or context.test.receiver_center_hz != plan["receiver"]["center_frequency_hz"]
            or context.test.receiver_gain_db != plan["receiver"]["gain_db"]
            or context.test.transmitter.host != plan["host"]
            or context.test.transmitter.backend.value != plan["backend"]
            or context.test.transmitter.output != plan["output"]
            or context.test.band != plan["band"]
            or context.test.identity.callsign != plan["identity"]["callsign"]
            or context.test.identity.grid != plan["identity"]["grid"]
            or context.test.identity.power_dbm != plan["identity"]["power_dbm"]
            or context.test.ppm != plan["calibration"]["ppm"]
            or context.test.gates.carrier_offset_max_hz != plan["carrier"]["offset_gate_hz"]
            or context.test.gates.best_20hz_share_min != plan["carrier"]["best_20hz_share_min"]
        ):
            raise RealSessionError("resolved profile measurement contract differs from the plan")
        return self._stage(
            plan,
            "capabilities",
            "passed",
            {"bindings": plan["capability_bindings"]},
            plan["deadlines"]["helper_s"],
            started,
        )

    @staticmethod
    def _validate_tone_contract(plan: dict[str, Any]) -> None:
        binding = plan["cw_contract"]
        mode_plan = load_json_document(Path(binding["plan"]["path"]), "cw-mode-plan.schema.json")
        expected = load_json_document(
            Path(binding["expected_events"]["path"]), "cw-expected-events.schema.json"
        )
        schedule = plan["tone_schedule"]
        protocol = mode_plan["protocol"]
        capture = mode_plan["capture_contract"]
        receiver = plan["receiver"]
        expected_plan = Path(expected["plan"]["path"])
        if not expected_plan.is_absolute():
            expected_plan = Path(binding["expected_events"]["path"]).parent / expected_plan
        if (
            mode_plan["run_id"] != plan["run_id"]
            or expected["run_id"] != plan["run_id"]
            or mode_plan["mode"] != "tone"
            or expected["mode"] != "tone"
            or artifact(expected_plan) != binding["plan"]
            or expected["protocol_definition"] != protocol["definition"]
            or mode_plan["backend"] != plan["backend"]
            or mode_plan["transmitter"]["host"] != plan["host"]
            or mode_plan["transmitter"]["output"] != plan["output"]
            or mode_plan["transmitter"]["drive_value"] != plan["drive"]["value"]
            or mode_plan["transmitter"]["drive_unit"] != plan["drive"]["unit"]
            or protocol["primary_frequency_hz"] != plan["frequency_hz"]
            or protocol["tone_cycles"] != schedule["cycles"]
            or protocol["tone_on_seconds"] != schedule["on_seconds"]
            or protocol["tone_off_seconds"] != schedule["off_seconds"]
            or protocol["pre_quiet_seconds"] != schedule["off_seconds"]
            or protocol["post_quiet_seconds"] != schedule["off_seconds"]
            or capture["sample_count"] != plan["carrier"]["rf_on_sample_count"]
            or capture["sample_rate_hz"] != receiver["sample_rate_hz"]
            or capture["center_frequency_hz"] != receiver["center_frequency_hz"]
            or mode_plan["receiver"]["host"] != receiver["host"]
            or mode_plan["receiver"]["driver"] != receiver["driver"]
            or mode_plan["receiver"]["device_identity"] != receiver["serial"]
        ):
            raise RealSessionError("pinned CW tone contract differs from the live plan")
        if expected["events"] != generate_expected_events(mode_plan):
            raise RealSessionError("pinned CW expected events differ from the reference encoder")

    def verify_helper(self, plan: dict[str, Any]) -> dict[str, object]:
        started = time.monotonic()
        # A signed, plan-bound response proves both persistent helper sessions.
        for operation_name, provider, services in (
            (
                HELPER_VERIFICATION_OPERATIONS[0],
                self.tx_services,
                plan["services"]["transmitter"],
            ),
            (
                HELPER_VERIFICATION_OPERATIONS[1],
                self.rx_services,
                plan["services"]["receiver"],
            ),
        ):
            if not services:
                raise RealSessionError("each live host requires an inspectable service binding")

            def inspect_service(
                provider: HelperServiceProvider = provider, service: str = services[0]
            ) -> object:
                return provider.inspect(service)

            self._bounded_helper_operation(
                operation_name,
                plan["deadlines"]["helper_s"],
                inspect_service,
            )
        source = plan["source"]
        observed = []
        for operation_name, arguments in (
            (
                HELPER_VERIFICATION_OPERATIONS[2],
                (
                    source["git_path"],
                    "-c",
                    f"safe.directory={source['repository_path']}",
                    "-C",
                    source["repository_path"],
                    "rev-parse",
                    "HEAD",
                ),
            ),
            (
                HELPER_VERIFICATION_OPERATIONS[3],
                (
                    source["git_path"],
                    "-c",
                    f"safe.directory={source['repository_path']}",
                    "-C",
                    source["repository_path"],
                    "rev-parse",
                    f"HEAD:{source['submodule_path']}",
                ),
            ),
        ):

            def inspect_revision(arguments: tuple[str, ...] = arguments) -> Any:
                process = self.source_launcher.begin(arguments)
                return process.wait(self._remaining(plan["deadlines"]["helper_s"]), None)

            result = self._bounded_helper_operation(
                operation_name, plan["deadlines"]["helper_s"], inspect_revision
            )
            if result.return_code != 0 or not result.cleanup_verified:
                raise RealSessionError(f"helper verification {operation_name} failed")
            observed.append(result.stdout.strip())
        if observed != [source["parent_revision"], source["submodule_revision"]]:
            raise RealSessionError("remote source revisions differ from the resolved plan")
        source_evidence = self.paths.work_directory / "source-revisions.json"
        source_evidence.write_text(
            json.dumps(
                {
                    "host": plan["host"],
                    "repository_path": source["repository_path"],
                    "submodule_path": source["submodule_path"],
                    "parent_revision": observed[0],
                    "submodule_revision": observed[1],
                    "git_sha256": source["git_sha256"],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self._artifacts.append(source_evidence)
        details: dict[str, object] = {
            side: {
                key: plan[field][key]
                for key in ("host", "path", "sha256", "identity", "config_path", "config_sha256")
            }
            for side, field in (("transmitter", "remote_helper"), ("receiver", "receiver_helper"))
        }
        details["verification_contract"] = helper_verification_contract(plan)
        return self._stage(
            plan, "helper", "passed", details, helper_verification_deadline(plan), started
        )

    @staticmethod
    def _bounded_helper_operation(name: str, deadline_s: float, operation: Callable[[], _T]) -> _T:
        started = time.monotonic()
        try:
            result = operation()
        except Exception as error:
            raise RealSessionError(
                f"helper verification {name} failed: {type(error).__name__}: {error}"
            ) from error
        if time.monotonic() - started > deadline_s:
            raise RealSessionError(f"helper verification {name} exceeded its hard deadline")
        return result

    def inspect_services_and_ownership(self, plan: dict[str, Any]) -> dict[str, object]:
        started = time.monotonic()
        for side, provider in (("transmitter", self.tx_services), ("receiver", self.rx_services)):
            for name in plan["services"][side]:
                state = provider.inspect(name)
                self._initial_services[(side, name)] = state.running
        details: dict[str, object] = {
            "transmitter": {
                "host": plan["host"],
                "services": plan["services"]["transmitter"],
                "conflicts": [],
            },
            "receiver": {
                "host": plan["receiver"]["host"],
                "services": plan["services"]["receiver"],
                "conflicts": [],
            },
        }
        return self._stage(
            plan, "ownership", "passed", details, plan["deadlines"]["helper_s"], started
        )

    def _quiescence(self, plan: dict[str, Any], authorization: RuntimeAuthorization) -> bool:
        contract = plan["backend_contract"]
        if plan["backend"] == "gpio":
            result = GpioQuiescenceCapability(
                __import__(
                    "wsprrypi_qualification.real_capabilities", fromlist=["HelperGpioProvider"]
                ).HelperGpioProvider(self.tx_client)
            ).inspect(contract["gpio_pin"], authorization)
        else:
            result = Si5351QuiescenceCapability(
                __import__(
                    "wsprrypi_qualification.real_capabilities", fromlist=["HelperSi5351Provider"]
                ).HelperSi5351Provider(self.tx_client)
            ).inspect(
                contract["i2c_bus"],
                contract["i2c_address"],
                (plan["output"],),
                authorization,
            )
        return result["outcome"] == "verified"

    def verify_rf_idle(self, plan: dict[str, Any]) -> dict[str, object]:
        started = time.monotonic()
        inspection_plan = (
            {
                "pin": plan["backend_contract"]["gpio_pin"],
                "expected_direction": "input",
                "read_only": True,
            }
            if plan["backend"] == "gpio"
            else {
                "bus": plan["backend_contract"]["i2c_bus"],
                "address": plan["backend_contract"]["i2c_address"],
                "required_outputs": [plan["output"]],
                "read_only": True,
            }
        )
        auth = RuntimeAuthorization(
            capability_plan_sha256(inspection_plan), "real-session", datetime.now(UTC), True, False
        )
        if not self._quiescence(plan, auth):
            raise RealSessionError("transmitter backend is not initially quiescent")
        return self._stage(
            plan,
            "rf_idle",
            "passed",
            {"backend": plan["backend"], "output": plan["output"], "verified": True},
            plan["deadlines"]["helper_s"],
            started,
        )

    def install_cleanup(self, plan: dict[str, Any]) -> dict[str, object]:
        started = time.monotonic()
        self._cleanup_installed = True
        required = set(plan["services"].get("receiver_required", []))
        for name in plan["services"]["receiver"]:
            key = ("receiver", name)
            initial_running = self._initial_services.get(key)
            requested_running = name in required
            if initial_running is not requested_running:
                self._changed_services.append(key)
                self.rx_services.set_running(name, requested_running)
                if self.rx_services.inspect(name).running is not requested_running:
                    requested = "started" if requested_running else "stopped"
                    raise RealSessionError(f"receiver service could not be {requested}")
        return self._stage(
            plan,
            "cleanup_registration",
            "passed",
            {"installed": True, "deadline_s": plan["deadlines"]["cleanup_s"]},
            plan["deadlines"]["cleanup_s"],
            started,
        )

    def _capture(
        self,
        plan: dict[str, Any],
        kind: str,
        count: int,
        cancellation: threading.Event | None = None,
    ) -> dict[str, object]:
        if not self._cleanup_installed:
            raise RealSessionError("capture refused before cleanup registration")
        started = time.monotonic()
        stem = f"{kind}-{len(self._artifacts)}"
        output = self.paths.work_directory / f"{stem}.cf32"
        metadata = self.paths.work_directory / f"{stem}-metadata.json"
        receiver = plan["receiver"]
        capture_plan = CaptureCapabilityPlan(
            self.paths.capture_helper,
            metadata,
            output,
            receiver["driver"],
            receiver["serial"],
            receiver["channel"],
            receiver["sample_rate_hz"],
            receiver["bandwidth_hz"],
            receiver["center_frequency_hz"],
            receiver["gain_db"],
            count,
            receiver["read_timeout_us"],
            plan["deadlines"]["receiver_s"],
            receiver["clipping_threshold"],
        )
        auth = RuntimeAuthorization(
            capability_plan_sha256(capture_plan.document()),
            "real-session",
            datetime.now(UTC),
            True,
            False,
        )
        result = self.capture_capability.execute(capture_plan, auth, cancellation)
        self._artifacts.extend((output, metadata))
        self._capture_artifacts[kind] = (output, metadata)
        return self._stage(
            plan,
            "capture",
            "completed",
            {
                "capture_kind": kind,
                "sample_format": "CF32",
                "sample_rate_hz": 250000,
                "sample_count": count,
                "overflow_count": 0,
                "timeout_count": 0,
                "clipped_samples": 0,
                "receiver_host": receiver["host"],
                "driver": receiver["driver"],
                "serial": receiver["serial"],
                "artifact_sha256": cast(dict[str, object], result["output"])["sha256"],
            },
            plan["deadlines"]["receiver_s"],
            started,
        )

    def capture_rf_off(self, plan: dict[str, Any]) -> dict[str, object]:
        return self._capture(plan, "rf_off", plan["carrier"]["rf_off_sample_count"])

    def _application(self, plan: dict[str, Any], *, tone: bool) -> ApplicationPlan:
        contract = plan["backend_contract"]
        config = WsprryPiBackendConfig(
            plan["output"],
            plan["calibration"]["ppm"],
            i2c_bus=contract.get("i2c_bus"),
            i2c_address=contract.get("i2c_address"),
            reference_frequency_hz=contract.get("reference_frequency_hz"),
            drive_or_power_level=contract["drive_or_power_level"],
            gpio_pin=contract.get("gpio_pin"),
        )
        shim = WsprryPiShim(
            ApplicationIdentity(
                "wsprrypi",
                plan["wsprrypi"]["path"],
                plan["source"]["parent_revision"],
                plan["source"]["submodule_revision"],
            ),
            backend=plan["backend"],
            backend_config=config,
        )
        protocol = (
            ToneProtocol(plan["frequency_hz"])
            if tone
            else WsprProtocol(
                plan["identity"]["callsign"],
                plan["identity"]["grid"],
                plan["identity"]["power_dbm"],
                plan["frequency_hz"],
                3,
                1500.0,
            )
        )
        return shim.resolve_plan(f"{plan['run_id']}-{'carrier' if tone else 'frames'}", protocol)

    def _begin_transmitter(
        self, plan: dict[str, Any], tone: bool, *, cycle: int | None = None
    ) -> OwnedProcess:
        if not self._cleanup_installed:
            raise RealSessionError("transmitter refused before cleanup registration")
        for name in plan["services"]["transmitter"]:
            key = ("transmitter", name)
            if self._initial_services.get(key) is True and key not in self._changed_services:
                raise RealSessionError("transmitter service was not prepared before RF cadence")
        application = self._application(plan, tone=tone)
        plan_path = self.paths.work_directory / (
            f"carrier-cycle-{cycle}-application-plan.json"
            if tone and cycle is not None
            else "carrier-application-plan.json"
            if tone
            else "frames-application-plan.json"
        )
        write_json_new(
            plan_path, application.to_document(), schema_name="application-plan.schema.json"
        )
        self._artifacts.append(plan_path)
        launcher = self.tx_launcher
        if tone and cycle is not None:
            launcher = SshOwnedProcessLauncher(
                self.tx_client,
                plan["tone_schedule"]["on_seconds"],
                plan["wsprrypi"]["sha256"],
            )
        process = launcher.begin(application.arguments)
        self._owned.append(process)
        return process

    def _prepare_transmitter_services(self, plan: dict[str, Any]) -> None:
        if not self._cleanup_installed:
            raise RealSessionError("transmitter preparation refused before cleanup registration")
        for name in plan["services"]["transmitter"]:
            key = ("transmitter", name)
            if self._initial_services.get(key) is not True or key in self._changed_services:
                continue
            self._changed_services.append(key)
            self.tx_services.set_running(name, False)
            if self.tx_services.inspect(name).running:
                raise RealSessionError("transmitter service could not be stopped")

    def _retain_transmitter_result(
        self,
        plan: dict[str, Any],
        *,
        tone: bool,
        result: object,
        cycle: int | None = None,
    ) -> None:
        execution = cast(Any, result)
        path = self.paths.work_directory / (
            f"carrier-cycle-{cycle}-transmitter-execution.json"
            if tone and cycle is not None
            else "carrier-transmitter-execution.json"
            if tone
            else "frames-transmitter-execution.json"
        )
        write_json_new(
            path,
            {
                "schema_version": 1,
                "evidence_type": "transmitter_execution",
                "run_id": plan["run_id"],
                "plan_sha256": resolved_real_plan_sha256(plan),
                "mode": "tone" if tone else "wspr",
                "handle_id": execution.handle_id,
                "return_code": execution.return_code,
                "stdout": execution.stdout,
                "stderr": execution.stderr,
                "timed_out": execution.timed_out,
                "cancelled": execution.cancelled,
                "disconnected": execution.disconnected,
                "cleanup_verified": execution.cleanup_verified,
                "stop_requested": execution.stop_requested,
                "running_before_stop": execution.running_before_stop,
                "outcome": (
                    "stopped_by_harness_after_capture"
                    if tone and _intentional_carrier_stop_verified(execution)
                    else "completed"
                    if not tone
                    and execution.return_code == 0
                    and not execution.timed_out
                    and not execution.cancelled
                    and not execution.disconnected
                    and execution.cleanup_verified
                    else "failed"
                ),
            },
        )
        self._artifacts.append(path)

    def transmit_carrier_and_capture_rf_on(
        self, plan: dict[str, Any], authorization: RuntimeAuthorization
    ) -> dict[str, object]:
        del authorization
        self._prepare_transmitter_services(plan)
        captured: list[dict[str, object]] = []
        errors: list[BaseException] = []
        cancellation = threading.Event()
        worker = threading.Thread(
            target=lambda: self._capture_into(
                captured,
                errors,
                plan,
                "rf_on",
                plan["carrier"]["rf_on_sample_count"],
                cancellation,
            ),
            name="wspq-rf-on-capture",
        )
        self._capture_tasks.append((worker, cancellation))
        worker.start()
        self._wait_capture_ready(plan, "rf_on", worker, errors)
        if plan.get("session_kind") == "cw_live_tone":
            return self._run_tone_pattern(plan, worker, cancellation, captured, errors)
        try:
            process = self._begin_transmitter(plan, True)
        except Exception as exc:
            worker.join(self._remaining(plan["deadlines"]["receiver_s"], reserve_cleanup=True))
            if worker.is_alive():
                cancellation.set()
                raise RealSessionError(
                    "carrier launch failed and receiver cleanup remains unverified"
                ) from exc
            raise
        try:
            worker.join(self._remaining(plan["deadlines"]["receiver_s"], reserve_cleanup=True))
            if worker.is_alive() or errors or not captured:
                cancellation.set()
                raise RealSessionError("RF-on receiver did not complete its exact capture")
            return captured[0]
        finally:
            result = process.stop()
            self._retain_transmitter_result(plan, tone=True, result=result)
            if _owned_process_released(result):
                self._owned.remove(process)
            if not _intentional_carrier_stop_verified(result):
                raise RealSessionError(
                    "carrier transmitter did not satisfy the intentional owned-stop contract"
                )
            if not worker.is_alive() and (worker, cancellation) in self._capture_tasks:
                self._capture_tasks.remove((worker, cancellation))

    def _run_tone_pattern(
        self,
        plan: dict[str, Any],
        worker: threading.Thread,
        cancellation: threading.Event,
        captured: list[dict[str, object]],
        errors: list[BaseException],
    ) -> dict[str, object]:
        if not self._cleanup_installed:
            raise RealSessionError("bounded Tone server refused before cleanup registration")
        server_plan = plan["tone_server"]
        epoch = time.monotonic()
        launcher = SshOwnedProcessLauncher(
            self.tx_client,
            plan["deadlines"]["transmitter_s"],
            plan["wsprrypi"]["sha256"],
            pinned_arguments={
                server_plan["configuration"]["path"]: server_plan["configuration"]["sha256"]
            },
        )
        server = launcher.begin(tuple(server_plan["arguments"]))
        self._owned.append(server)
        try:
            return self._run_tone_pattern_cycles(
                plan, worker, cancellation, captured, errors, epoch=epoch
            )
        finally:
            result = server.stop()
            self._retain_transmitter_result(plan, tone=True, result=result)
            if _owned_process_released(result):
                self._owned.remove(server)
            if not _intentional_carrier_stop_verified(result):
                raise RealSessionError("bounded Tone server did not satisfy owned-stop cleanup")

    def _run_tone_pattern_cycles(
        self,
        plan: dict[str, Any],
        worker: threading.Thread,
        cancellation: threading.Event,
        captured: list[dict[str, object]],
        errors: list[BaseException],
        *,
        epoch: float | None = None,
    ) -> dict[str, object]:
        schedule = plan["tone_schedule"]
        epoch = time.monotonic() if epoch is None else epoch
        self._sleep_until(epoch + plan["tone_server"]["startup_seconds"])
        interval = schedule["off_seconds"] + schedule["on_seconds"]
        reserved_rf_on = 0.0
        try:
            for cycle in range(1, schedule["cycles"] + 1):
                enable_at = epoch + schedule["off_seconds"] + ((cycle - 1) * interval)
                self._sleep_until(enable_at)
                if errors or not worker.is_alive():
                    raise RealSessionError("tone-pattern capture ended before RF enable")
                next_reserved_rf_on = reserved_rf_on + schedule["on_seconds"]
                if next_reserved_rf_on > schedule["maximum_rf_on_seconds"]:
                    raise RealSessionError("tone pattern exceeds its cumulative RF-on bound")
                reserved_rf_on = next_reserved_rf_on
                outer_timeout_s = min(
                    plan["deadlines"]["transmitter_s"],
                    schedule["on_seconds"] + min(1.0, schedule["off_seconds"] / 2),
                )
                try:
                    evidence = self.tx_client.request_evidence(
                        "bounded-tone",
                        {
                            "frequency_hz": int(plan["frequency_hz"]),
                            "duration_ms": int(schedule["on_seconds"] * 1000),
                            "outer_timeout_s": outer_timeout_s,
                        },
                    )
                except Exception as exc:
                    raise RealSessionError(f"bounded Tone cycle {cycle} failed") from exc
                result = cast(dict[str, Any], evidence["result"])
                endpoint = plan["remote_helper"]["bounded_tone_endpoint"]
                if (
                    result["wsprrypi_revision"] != plan["remote_helper"]["wsprrypi_revision"]
                    or result["loopback_host"] != endpoint["host"]
                    or result["port"] != endpoint["port"]
                    or result["path"] != endpoint["path"]
                    or result["maximum_frame_bytes"] != endpoint["maximum_frame_bytes"]
                    or evidence["plan_sha256"] != plan["remote_helper"]["plan_sha256"]
                    or evidence["helper_identity"] != plan["remote_helper"]["identity"]
                ):
                    raise RealSessionError("bounded Tone helper evidence contradicts the plan")
                evidence_path = (
                    self.paths.work_directory / f"carrier-cycle-{cycle}-bounded-tone.json"
                )
                write_json_new(evidence_path, evidence, schema_name="helper-response.schema.json")
                self._artifacts.append(evidence_path)
            closing_at = epoch + (schedule["cycles"] * interval) + schedule["off_seconds"]
            self._sleep_until(closing_at, allow_capture_completion=True, worker=worker)
            worker.join(self._remaining(plan["deadlines"]["receiver_s"], reserve_cleanup=True))
            if worker.is_alive() or errors or not captured:
                cancellation.set()
                raise RealSessionError("tone-pattern receiver did not complete its exact capture")
            return captured[0]
        finally:
            if worker.is_alive():
                cancellation.set()
            if not worker.is_alive() and (worker, cancellation) in self._capture_tasks:
                self._capture_tasks.remove((worker, cancellation))

    def _sleep_until(
        self,
        deadline: float,
        *,
        allow_capture_completion: bool = False,
        worker: threading.Thread | None = None,
    ) -> None:
        requested = deadline - time.monotonic()
        if requested < 0:
            raise RealSessionError("tone pattern could not meet its absolute cadence")
        available = self._remaining(requested, reserve_cleanup=True)
        if available + 1e-9 < requested:
            raise RealSessionError("tone pattern would consume the cleanup deadline reserve")
        time.sleep(requested)
        if worker is not None and not worker.is_alive() and not allow_capture_completion:
            raise RealSessionError("tone-pattern capture ended before a scheduled transition")

    def _capture_into(
        self,
        captured: list[dict[str, object]],
        errors: list[BaseException],
        plan: dict[str, Any],
        kind: str,
        count: int,
        cancellation: threading.Event,
    ) -> None:
        try:
            captured.append(self._capture(plan, kind, count, cancellation))
        except BaseException as exc:
            errors.append(exc)

    def _wait_capture_ready(
        self,
        plan: dict[str, Any],
        kind: str,
        worker: threading.Thread,
        errors: list[BaseException],
    ) -> None:
        deadline = time.monotonic() + plan["deadlines"]["helper_s"]
        output = self.paths.work_directory / f"{kind}-{len(self._artifacts)}.cf32"
        expected = Path(str(output) + ".incomplete")
        while time.monotonic() < deadline:
            if expected.exists():
                return
            if errors or not worker.is_alive():
                break
            time.sleep(0.01)
        raise RealSessionError("receiver capture did not establish its retained output before RF")

    def analyze_carrier(
        self, plan: dict[str, Any], rf_off: dict[str, object], rf_on: dict[str, object]
    ) -> dict[str, object]:
        del rf_off, rf_on
        started = time.monotonic()
        try:
            off, off_metadata = self._capture_artifacts["rf_off"]
            on, on_metadata = self._capture_artifacts["rf_on"]
        except KeyError as exc:
            raise RealSessionError("carrier analysis lacks an explicit capture binding") from exc
        evidence = self.paths.work_directory / "carrier-analysis.json"
        self._run_offline(
            (
                "analyze-carrier",
                str(off),
                str(on),
                str(evidence),
                "--rf-off-metadata",
                str(off_metadata),
                "--rf-on-metadata",
                str(on_metadata),
                "--bench-profile",
                str(self.paths.bench_profile),
                "--test-profile",
                str(self.paths.test_profile),
            ),
            self._remaining(plan["deadlines"]["overall_s"], reserve_cleanup=True),
        )
        document = load_json_document(evidence, "carrier-analysis.schema.json")
        self._artifacts.append(evidence)
        metrics = document["metrics"]
        gate_outcome = document["gate_outcome"]
        mode_gate = "not_applicable"
        if plan.get("session_kind") == "cw_live_tone":
            native_metadata = load_json_document(on_metadata, "capture-metadata.schema.json")
            contract = plan["cw_contract"]
            retained_plan = self.paths.work_directory / "tone-plan.json"
            # The byte-exact sealed input is retained as an authenticated provenance
            # payload, not as the active JSON contract.  Its original dependency
            # paths are intentionally preserved and the separately derived JSON
            # document below is the relocatable analysis contract.
            sealed_expected = self.paths.work_directory / "tone-expected-events.sealed.source"
            retained_expected = self.paths.work_directory / "tone-expected-events.json"
            retained_plan_ref = _stage_bound_artifact(contract["plan"], retained_plan)
            self._artifacts.append(retained_plan)
            _stage_bound_artifact(contract["expected_events"], sealed_expected)
            self._artifacts.append(sealed_expected)
            retained_expected_ref = _derive_rebound_expected_events(
                sealed_expected,
                retained_expected,
                retained_plan_ref,
            )
            self._artifacts.append(retained_expected)
            acquired = self.paths.work_directory / "tone-acquired-capture.json"
            observations = self.paths.work_directory / "tone-observations.json"
            mode_gate_path = self.paths.work_directory / "tone-mode-gate.json"
            write_json_new(
                acquired,
                {
                    "schema_version": 1,
                    "evidence_type": "cw_acquired_capture",
                    "run_id": plan["run_id"],
                    "mode": "tone",
                    "plan": retained_plan_ref,
                    "expected_events": retained_expected_ref,
                    "capture": artifact(on),
                    "format": "CF32LE",
                    "sample_count": plan["carrier"]["rf_on_sample_count"],
                    "sample_rate_hz": plan["receiver"]["sample_rate_hz"],
                    "center_frequency_hz": plan["receiver"]["center_frequency_hz"],
                    "acquired_sample_count": native_metadata["retained_sample_count"],
                    "overflow_count": native_metadata["overflow_count"],
                    "fixed_gain": True,
                    "agc_enabled": plan["receiver"]["agc"],
                    "bias_tee_enabled": plan["receiver"]["bias_tee"],
                    "first_read_discarded": native_metadata["first_read"]["discarded"],
                    "receiver": {
                        "host": plan["receiver"]["host"],
                        "driver": plan["receiver"]["driver"],
                        "device_identity": plan["receiver"]["serial"],
                    },
                    "acquired_utc": native_metadata["timestamps"]["retained_capture_start_utc"],
                    "synthetic": False,
                },
                schema_name="cw-acquired-capture.schema.json",
            )
            self._artifacts.append(acquired)
            _, generated_gate = analyze_synthetic_iq(
                retained_plan,
                retained_expected,
                acquired,
                observations,
                mode_gate_path,
                source_revision=contract["analyzer_source_revision"],
                _metadata_schema="cw-acquired-capture.schema.json",
                _synthetic=False,
                _artifact_root=self.paths.work_directory,
            )
            mode_gate = generated_gate["mode_gate"]
            self._artifacts.extend((observations, mode_gate_path))
            if gate_outcome == "passed":
                gate_outcome = generated_gate["carrier_gate"]
        return self._stage(
            plan,
            "carrier_analysis",
            "completed",
            {
                "gate_outcome": gate_outcome,
                "mode_gate": mode_gate,
                "requested_frequency_hz": plan["frequency_hz"],
                "strongest_frequency_hz": metrics["strongest_transmitter_added_frequency_hz"],
                "offset_hz": metrics["strongest_offset_hz"],
                "best_20hz_fraction": metrics["best_20hz_resolved_power_share"],
                "strongest_contrast_db": metrics["strongest_feature_contrast_db"],
                "carrier_gate_policy": document["contract"]["gate_policy"],
                "relative_acquisition_offset_gate_hz": document["contract"][
                    "relative_acquisition_offset_gate_hz"
                ],
                "relative_acquisition_contrast_gate_db": document["contract"][
                    "relative_acquisition_contrast_gate_db"
                ],
            },
            plan["deadlines"]["overall_s"],
            started,
        )

    def transmit_frames_and_capture(
        self, plan: dict[str, Any], authorization: RuntimeAuthorization
    ) -> dict[str, object]:
        del authorization
        first_slot = datetime.fromisoformat(plan["slots_utc"][0].replace("Z", "+00:00"))
        capture_start = _coherent_capture_launch_epoch(
            first_slot, plan["coherent_capture"]["margin_before_first_slot_s"]
        )
        delay = capture_start - time.time()
        if delay < 0 or delay > self._remaining(
            plan["deadlines"]["overall_s"], reserve_cleanup=True
        ):
            raise RealSessionError("coherent capture cannot meet its resolved UTC margin")
        if delay:
            time.sleep(delay)
        captured: list[dict[str, object]] = []
        capture_error: list[BaseException] = []
        cancellation = threading.Event()

        def capture_worker() -> None:
            try:
                captured.append(
                    self._capture(
                        plan,
                        "coherent",
                        plan["coherent_capture"]["sample_count"],
                        cancellation,
                    )
                )
            except BaseException as exc:
                capture_error.append(exc)

        worker = threading.Thread(target=capture_worker, name="wspq-coherent-capture")
        self._capture_tasks.append((worker, cancellation))
        worker.start()
        self._wait_capture_ready(plan, "coherent", worker, capture_error)
        try:
            process = self._begin_transmitter(plan, False)
        except Exception as exc:
            worker.join(self._remaining(plan["deadlines"]["receiver_s"], reserve_cleanup=True))
            if worker.is_alive():
                raise RealSessionError(
                    "transmitter launch failed and receiver cleanup remains unverified"
                ) from exc
            raise
        try:
            worker.join(self._remaining(plan["deadlines"]["receiver_s"], reserve_cleanup=True))
            if worker.is_alive():
                cancellation.set()
                raise RealSessionError("coherent receiver exceeded its hard deadline")
            if capture_error:
                raise RealSessionError(f"coherent capture failed: {capture_error[0]}")
            metadata_path = self._capture_artifacts["coherent"][1]
            metadata = load_json_document(metadata_path, "capture-metadata.schema.json")
            if not _retained_capture_has_margin(
                metadata,
                first_slot,
                plan["coherent_capture"]["margin_before_first_slot_s"],
            ):
                raise RealSessionError(
                    "coherent capture retained start missed its resolved UTC margin"
                )
            wait = process.wait(
                self._remaining(plan["deadlines"]["transmitter_s"], reserve_cleanup=True), None
            )
            self._retain_transmitter_result(plan, tone=False, result=wait)
            if wait.return_code != 0 or not wait.cleanup_verified:
                raise RealSessionError("bounded WSPR transmitter did not exit cleanly")
            self._owned.remove(process)
            if (worker, cancellation) in self._capture_tasks:
                self._capture_tasks.remove((worker, cancellation))
            return captured[0]
        except Exception:
            cancellation.set()
            stopped = process.stop()
            execution_path = self.paths.work_directory / "frames-transmitter-execution.json"
            if not execution_path.exists():
                self._retain_transmitter_result(plan, tone=False, result=stopped)
            if process in self._owned:
                self._owned.remove(process)
            raise

    def create_wavs_and_decode(
        self, plan: dict[str, Any], coherent_capture: dict[str, object]
    ) -> dict[str, object]:
        del coherent_capture
        started = time.monotonic()
        try:
            iq, metadata = self._capture_artifacts["coherent"]
        except KeyError as exc:
            raise RealSessionError("decoder pipeline lacks a coherent capture binding") from exc
        slot_documents: list[dict[str, Any]] = []
        slot_paths: list[Path] = []
        for index, slot_text in enumerate(plan["slots_utc"]):
            audio_evidence = self.paths.work_directory / f"audio-{index}.json"
            self._run_offline(
                (
                    "make-slot-wav",
                    str(iq),
                    str(metadata),
                    str(self.paths.work_directory),
                    str(audio_evidence),
                    "--slot",
                    slot_text,
                    "--bench-profile",
                    str(self.paths.bench_profile),
                    "--test-profile",
                    str(self.paths.test_profile),
                ),
                self._remaining(plan["deadlines"]["overall_s"], reserve_cleanup=True),
            )
            audio = load_json_document(audio_evidence, "audio-conversion.schema.json")
            wav = Path(cast(str, cast(dict[str, object], audio["output"])["path"]))
            decoder_evidence = self.paths.work_directory / f"decoder-{index}.json"
            self._run_offline(
                (
                    "decode-wspr",
                    str(wav),
                    str(audio_evidence),
                    str(decoder_evidence),
                    "--wsprd",
                    str(self.paths.wsprd),
                    "--timeout",
                    str(plan["deadlines"]["helper_s"]),
                ),
                self._remaining(plan["deadlines"]["helper_s"] + 5, reserve_cleanup=True),
            )
            decoded = load_json_document(decoder_evidence, "decoder-evidence.schema.json")
            slot_documents.append(decoded)
            slot_paths.append(decoder_evidence)
            self._artifacts.extend((wav, audio_evidence, decoder_evidence))
            self._artifacts.extend(Path(item["path"]) for item in decoded["decoder_data_artifacts"])
        summary_path = self.paths.work_directory / "decode-summary.json"
        self._run_offline(
            ("summarize-decodes", str(summary_path), *(str(path) for path in slot_paths)),
            self._remaining(plan["deadlines"]["overall_s"], reserve_cleanup=True),
        )
        summary = load_json_document(summary_path, "decode-summary.schema.json")
        self._artifacts.append(summary_path)
        slots = [
            {
                "slot_utc": document["slot_utc"],
                "callsign": plan["identity"]["callsign"],
                "grid": plan["identity"]["grid"],
                "power_dbm": plan["identity"]["power_dbm"],
                "matched": document["gate_outcome"] == "passed",
                "wsprd_log_sha256": artifact(slot_paths[index])["sha256"],
            }
            for index, document in enumerate(slot_documents)
        ]
        return self._stage(
            plan,
            "decode_summary",
            "completed",
            {"gate_outcome": summary["gate_outcome"], "slots": slots},
            plan["deadlines"]["overall_s"],
            started,
        )

    @staticmethod
    def _run_offline(arguments: tuple[str, ...], timeout_s: float) -> None:
        try:
            result = subprocess.run(
                (sys.executable, "-m", "wsprrypi_qualification", *arguments),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RealSessionError(
                f"offline stage exceeded its hard deadline: {arguments[0]}"
            ) from exc
        if result.returncode != 0:
            raise RealSessionError(
                f"offline stage failed ({arguments[0]}): {result.stderr.strip()}"
            )

    def cleanup(self, plan: dict[str, Any]) -> dict[str, object]:
        started = time.monotonic()
        cleanup_deadline = min(
            started + plan["deadlines"]["cleanup_s"],
            self._session_deadline if self._session_deadline is not None else float("inf"),
        )

        def time_left() -> bool:
            return bool(time.monotonic() < cleanup_deadline)

        def apply_budget(client: JsonHelperClient) -> None:
            remaining = cleanup_deadline - time.monotonic()
            if remaining <= 0:
                raise RealSessionError("cleanup deadline expired")
            client.timeout_s = remaining
            if hasattr(client.transport, "cleanup_timeout_s"):
                client.transport.cleanup_timeout_s = remaining

        ok = True
        failures: list[str] = []
        failures.extend(self._cancel_capture_tasks(cleanup_deadline))
        ok &= not failures
        for process in tuple(self._owned):
            if not time_left():
                ok = False
                failures.append("cleanup deadline expired before process stop")
                break
            try:
                apply_budget(self.tx_client)
                stopped = process.stop().cleanup_verified
                ok &= stopped
                if stopped:
                    self._owned.remove(process)
                else:
                    failures.append("owned process stop was not verified")
            except Exception as exc:
                ok = False
                failures.append(f"process stop: {type(exc).__name__}: {exc}")
        for side, name in reversed(self._changed_services):
            if not time_left():
                ok = False
                failures.append("cleanup deadline expired before service restoration")
                break
            provider = self.tx_services if side == "transmitter" else self.rx_services
            try:
                apply_budget(self.tx_client if side == "transmitter" else self.rx_client)
                provider.set_running(name, self._initial_services[(side, name)])
                apply_budget(self.tx_client if side == "transmitter" else self.rx_client)
                restored = provider.inspect(name).running == self._initial_services[(side, name)]
                ok &= restored
                if not restored:
                    failures.append(f"service restoration unverified: {side}:{name}")
            except Exception as exc:
                ok = False
                failures.append(f"service restoration {side}:{name}: {type(exc).__name__}: {exc}")
        inspection = (
            {
                "pin": plan["backend_contract"]["gpio_pin"],
                "expected_direction": "input",
                "read_only": True,
            }
            if plan["backend"] == "gpio"
            else {
                "bus": plan["backend_contract"]["i2c_bus"],
                "address": plan["backend_contract"]["i2c_address"],
                "required_outputs": [plan["output"]],
                "read_only": True,
            }
        )
        auth = RuntimeAuthorization(
            capability_plan_sha256(inspection),
            "real-session",
            datetime.now(UTC),
            True,
            False,
        )
        try:
            if not time_left():
                raise RealSessionError("cleanup deadline expired before quiescence")
            apply_budget(self.tx_client)
            self._final_quiescence = self._quiescence(plan, auth)
            ok &= self._final_quiescence
        except Exception as exc:
            self._final_quiescence = False
            ok = False
            failures.append(f"quiescence: {type(exc).__name__}: {exc}")
        helper_absent = self.close(max(0.0, cleanup_deadline - time.monotonic()))
        if not helper_absent:
            failures.append("persistent helper closure was not verified")
        ok &= helper_absent
        elapsed = time.monotonic() - started
        return {
            "schema_version": 1,
            "evidence_type": "cleanup",
            "plan_sha256": resolved_real_plan_sha256(plan),
            "outcome": "verified" if ok else "failed",
            "elapsed_s": elapsed,
            "deadline_s": plan["deadlines"]["cleanup_s"],
            "details": {
                "actions_complete": ok,
                "helper_absent": helper_absent and not self._owned and not self._capture_tasks,
            },
        }

    def _cancel_capture_tasks(self, deadline: float) -> list[str]:
        failures: list[str] = []
        for worker, cancellation in tuple(self._capture_tasks):
            cancellation.set()
            worker.join(max(0.0, deadline - time.monotonic()))
            if worker.is_alive():
                failures.append("receiver capture process cleanup was not verified")
            else:
                self._capture_tasks.remove((worker, cancellation))
        return failures

    def close(self, deadline_s: float | None = None) -> bool:
        if self._closed:
            return True
        deadline = time.monotonic() + (
            self._cleanup_reserve_s if deadline_s is None else deadline_s
        )
        verified = True
        for client in (self.tx_client, self.rx_client):
            if time.monotonic() >= deadline:
                return False
            close = getattr(client.transport, "close", None)
            if close is None:
                verified = False
                continue
            try:
                if hasattr(client.transport, "cleanup_timeout_s"):
                    client.transport.cleanup_timeout_s = max(0.001, deadline - time.monotonic())
                close()
            except Exception:
                verified = False
        self._closed = verified
        return verified

    def verify_quiescence(self, plan: dict[str, Any]) -> dict[str, object]:
        started = time.monotonic()
        if self._final_quiescence is None:
            raise RealSessionError("final quiescence was not captured before helper shutdown")
        verified = self._final_quiescence
        return self._stage(
            plan,
            "quiescence",
            "verified" if verified else "failed",
            {"backend": plan["backend"], "output": plan["output"], "verified": verified},
            plan["deadlines"]["cleanup_s"],
            started,
        )

    def publish_artifacts(self, destination: Path) -> list[dict[str, object]]:
        retained: list[dict[str, object]] = []
        records: list[dict[str, object]] = []
        unique_sources = list(dict.fromkeys(path.resolve() for path in self._artifacts))
        for index, source in enumerate(unique_sources):
            target = destination / "retained-artifacts" / f"{index:03d}-{source.name}"
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(source, target)
            identity = artifact(target)
            identity["path"] = target.relative_to(destination).as_posix()
            retained.append(identity)
            records.append({"source": artifact(source), "retained": identity})
        index_path = destination / "live-artifact-index.json"
        index_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "evidence_type": "live_artifact_index",
                    "artifacts": records,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        index_identity = artifact(index_path)
        index_identity["path"] = index_path.name
        retained.append(index_identity)
        return retained

    @staticmethod
    def validate_published_artifacts(bundle: Path) -> None:
        index = json.loads((bundle / "live-artifact-index.json").read_text(encoding="utf-8"))
        if set(index) != {"schema_version", "evidence_type", "artifacts"}:
            raise RealSessionError("live artifact index structure is invalid")
        if index["schema_version"] != 1 or index["evidence_type"] != "live_artifact_index":
            raise RealSessionError("live artifact index identity is invalid")
        mapping: dict[str, dict[str, object]] = {}
        for record in index["artifacts"]:
            if set(record) != {"source", "retained"} or any(
                set(record[key]) != {"path", "size_bytes", "sha256"}
                for key in ("source", "retained")
            ):
                raise RealSessionError("live artifact index record is invalid")
            retained = bundle / record["retained"]["path"]
            if (
                retained.is_symlink()
                or not retained.is_file()
                or not retained.resolve().is_relative_to(bundle.resolve())
            ):
                raise RealSessionError("retained live artifact escapes its bundle")
            actual = artifact(retained)
            actual["path"] = record["retained"]["path"]
            if actual != record["retained"]:
                raise RealSessionError("retained live artifact identity changed")
            mapping[record["source"]["path"]] = record["retained"]
        for record in index["artifacts"]:
            retained_path = bundle / record["retained"]["path"]
            if retained_path.suffix != ".json":
                continue
            document = json.loads(retained_path.read_text(encoding="utf-8"))
            for reference in _artifact_references(document):
                reference_path = reference["path"]
                if not isinstance(reference_path, str):
                    raise RealSessionError("published artifact reference path is invalid")
                source_reference = Path(reference_path)
                if not source_reference.is_absolute():
                    source_reference = Path(record["source"]["path"]).parent / source_reference
                matched = mapping.get(str(source_reference.resolve()))
                if matched is None or any(
                    matched[key] != reference[key] for key in ("size_bytes", "sha256")
                ):
                    raise RealSessionError("published JSON has an unauthenticated dependency")


def build_production_adapters(
    plan: dict[str, Any], *, ssh_executable: Path, work_directory: Path
) -> ProductionRealSessionAdapters:
    """Build the one reviewed topology: local wspr5 receiver, SSH wspr4 transmitter."""
    if plan["transport"] != "ssh" or plan["execution_mode"] != "live":
        raise RealSessionError("production composition requires an SSH live plan")
    if not ssh_executable.is_absolute() or not ssh_executable.is_file():
        raise RealSessionError("OpenSSH executable must be an existing absolute file")
    if artifact(ssh_executable)["sha256"] != plan["capability_bindings"]["transmitter_ssh"]:
        raise RealSessionError("OpenSSH executable identity differs from the resolved plan")
    known_hosts = Path(plan["transport_identity"]["known_hosts_path"])
    if (
        not known_hosts.is_absolute()
        or not known_hosts.is_file()
        or artifact(known_hosts)["sha256"] != plan["transport_identity"]["known_hosts_sha256"]
    ):
        raise RealSessionError("pinned SSH known-hosts identity changed")
    ssh_keygen = Path(plan["transport_identity"]["ssh_keygen_path"])
    if (
        not ssh_keygen.is_absolute()
        or not ssh_keygen.is_file()
        or artifact(ssh_keygen)["sha256"] != plan["transport_identity"]["ssh_keygen_sha256"]
    ):
        raise RealSessionError("pinned ssh-keygen identity changed")
    selected = subprocess.run(
        (str(ssh_keygen), "-F", plan["host"], "-f", str(known_hosts)),
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
        check=False,
    )
    selected_lines = [line for line in selected.stdout.splitlines() if not line.startswith("#")]
    if selected.returncode != 0 or not selected_lines:
        raise RealSessionError("transmitter destination is absent from pinned known-hosts")
    with TemporaryDirectory(prefix="wspq-host-key-") as directory:
        selected_path = Path(directory) / "selected-known-hosts"
        selected_path.write_text("\n".join(selected_lines) + "\n", encoding="utf-8")
        fingerprint = subprocess.run(
            (str(ssh_keygen), "-lf", str(selected_path)),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=False,
        )
    parsed_fingerprints = [
        fields[1]
        for fields in (line.split() for line in fingerprint.stdout.splitlines())
        if len(fields) >= 2 and fields[1].startswith("SHA256:")
    ]
    if (
        fingerprint.returncode != 0
        or not parsed_fingerprints
        or any(
            value != plan["transport_identity"]["transmitter_host_key_sha256"]
            for value in parsed_fingerprints
        )
    ):
        raise RealSessionError("transmitter SSH server key fingerprint is not pinned")
    for helper in (plan["remote_helper"], plan["receiver_helper"]):
        if any(
            re.fullmatch(r"/[A-Za-z0-9._/+:-]+", helper[field]) is None
            for field in ("path", "config_path")
        ):
            raise RealSessionError("helper or configuration path is unsafe for execution")
    remote_wrapper = Path(plan["remote_helper"]["privilege_wrapper_path"])
    if (
        not remote_wrapper.is_absolute()
        or not remote_wrapper.is_file()
        or artifact(remote_wrapper)["sha256"] != plan["remote_helper"]["privilege_wrapper_sha256"]
    ):
        raise RealSessionError("remote helper privilege wrapper identity changed")
    if plan["receiver_helper"]["privilege_wrapper_path"] is not None:
        raise RealSessionError("local receiver helper must not use a privilege wrapper")
    receiver_helper = Path(plan["receiver_helper"]["path"])
    receiver_config = Path(plan["receiver_helper"]["config_path"])
    if artifact(receiver_helper)["sha256"] != plan["receiver_helper"]["sha256"]:
        raise RealSessionError("local receiver helper identity changed")
    if artifact(receiver_config)["sha256"] != plan["receiver_helper"]["config_sha256"]:
        raise RealSessionError("local receiver helper configuration identity changed")
    paths = LiveAdapterPaths(
        work_directory.resolve(),
        Path(plan["resolved_profiles"]["bench"]["path"]).resolve(),
        Path(plan["resolved_profiles"]["test"]["path"]).resolve(),
        Path(plan["resolved_profiles"]["receiver_run"]["path"]).resolve(),
        Path(plan["capture_helper"]["path"]).resolve(),
        Path(plan["wsprd"]["path"]).resolve(),
    )
    for path in (
        paths.bench_profile,
        paths.test_profile,
        paths.receiver_run_profile,
        paths.capture_helper,
        paths.wsprd,
    ):
        if not path.is_file():
            raise RealSessionError(f"required local live input is unavailable: {path}")
    work_directory.mkdir(parents=True, exist_ok=False)
    remote_command = (
        f"{remote_wrapper} -n {plan['remote_helper']['path']} --serve "
        f"--config {plan['remote_helper']['config_path']}"
    )
    tx_transport: PersistentHelperTransport | None = None
    rx_transport: PersistentHelperTransport | None = None
    try:
        tx_transport = PersistentHelperTransport(
            (
                str(ssh_executable),
                "-o",
                f"ConnectTimeout={plan['deadlines']['helper_s']:g}",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                f"UserKnownHostsFile={known_hosts}",
                "--",
                plan["host"],
                remote_command,
            ),
            cleanup_timeout_s=plan["deadlines"]["cleanup_s"],
        )
        rx_transport = PersistentHelperTransport(
            (str(receiver_helper), "--serve", "--config", str(receiver_config)),
            cleanup_timeout_s=plan["deadlines"]["cleanup_s"],
        )
    except Exception:
        if tx_transport is not None:
            tx_transport.close()
        shutil.rmtree(work_directory)
        raise
    assert tx_transport is not None and rx_transport is not None
    digest = helper_configuration_plan_sha256(plan)
    tx_client = JsonHelperClient(
        ssh_executable,
        tx_transport,
        plan["deadlines"]["helper_s"],
        digest,
        plan["remote_helper"]["identity"],
        plan["capability_bindings"]["transmitter_ssh"],
    )
    rx_client = JsonHelperClient(
        receiver_helper,
        rx_transport,
        plan["deadlines"]["helper_s"],
        digest,
        plan["receiver_helper"]["identity"],
        plan["receiver_helper"]["sha256"],
    )
    try:
        return ProductionRealSessionAdapters(
            transmitter_client=tx_client,
            receiver_client=rx_client,
            transmitter_launcher=SshOwnedProcessLauncher(
                tx_client,
                plan["deadlines"]["transmitter_s"],
                plan["wsprrypi"]["sha256"],
            ),
            source_launcher=SshOwnedProcessLauncher(
                tx_client,
                plan["deadlines"]["helper_s"],
                plan["source"]["git_sha256"],
            ),
            capture_capability=SoapyCaptureCapability(LocalTransportLauncher()),
            paths=paths,
        )
    except Exception:
        tx_transport.close()
        rx_transport.close()
        shutil.rmtree(work_directory)
        raise
