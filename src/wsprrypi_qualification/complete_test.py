"""Simple five-mode campaign composition above maintained coordinators."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import platform
import re
import secrets
import shutil
import socket
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, cast

from wsprrypi_qualification.application_shims import (
    ApplicationIdentity,
    CwProtocol,
    ProtocolMode,
    WsprryPiBackendConfig,
    WsprryPiShim,
)
from wsprrypi_qualification.cw_reference import generate_expected_events
from wsprrypi_qualification.keyed_session_contracts import (
    canonical_sha256,
    validate_keyed_result,
    validate_resolved_keyed_plan,
)
from wsprrypi_qualification.manifests import (
    build_manifest,
    render_manifest,
    validate_manifest_name,
    write_manifest,
)
from wsprrypi_qualification.offline import artifact, validate_document, write_json_new
from wsprrypi_qualification.real_session import (
    helper_configuration_plan_sha256,
    resolved_real_plan_sha256,
    validate_real_session_plan,
)
from wsprrypi_qualification.results import validate_result_document
from wsprrypi_qualification.tool_discovery import discover_executable
from wsprrypi_qualification.transports import CommandPlan, LocalCommandTransport

MODE_ORDER = ("TONE", "WSPR", "QRSS", "FSKCW", "DFCW")
DEFAULTS: dict[str, object] = {
    "band": "20m",
    "frequency_hz": 14_097_100,
    "callsign": "Q0QQQ",
    "grid": "JJ00",
    "power_dbm": 0,
    "message": "ET",
    "qrss_dot_seconds": 0.7,
    "fskcw_dot_seconds": 0.7,
    "dfcw_dot_seconds": 0.7,
    "fskcw_separation_hz": 5.0,
    "dfcw_separation_hz": 5.0,
    "keyed_observations": 3,
    "wspr_observations": 3,
}


class CompleteTestError(RuntimeError):
    """The complete campaign cannot be safely composed or executed."""


def receiver_is_local(receiver_host: str) -> bool:
    """Return whether the requested receiver names this execution host."""
    requested = receiver_host.rstrip(".").casefold()
    hostname = socket.gethostname().rstrip(".").casefold()
    fqdn = socket.getfqdn().rstrip(".").casefold()
    if "." in requested:
        return requested == fqdn
    return requested == hostname.split(".", 1)[0]


def delegate_complete_test(
    transmitter_host: str,
    receiver_host: str,
    sdr_selector: str,
    forwarded_arguments: list[str],
    *,
    configuration: Path | None = None,
    timeout_s: float = 7500.0,
) -> dict[str, Any]:
    """Run the complete command on a remote receiver without shell interpolation."""
    _, deployment = load_saved_configuration(transmitter_host, receiver_host, configuration)
    expected_delegation = deployment["receiver_delegation"]
    ssh = Path(expected_delegation["ssh"]["path"])
    known_hosts = Path(expected_delegation["known_hosts"]["path"])
    if _contains_symlink(ssh) or _contains_symlink(known_hosts):
        raise CompleteTestError("receiver delegation path contains a symbolic link")
    ssh_identity = artifact(ssh)
    known_hosts_identity = artifact(known_hosts)
    if (
        ssh_identity != expected_delegation["ssh"]
        or known_hosts_identity != expected_delegation["known_hosts"]
    ):
        raise CompleteTestError("receiver delegation local identity differs from deployment")
    remote_exec = expected_delegation["remote_exec"]["launcher"]["path"]
    if not PurePosixPath(remote_exec).is_absolute():
        raise CompleteTestError("receiver execution helper path must be absolute")
    common_ssh = (
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "--",
        receiver_host,
    )
    identity_record = LocalCommandTransport().execute(
        CommandPlan(ssh, (*common_ssh, remote_exec, "--identity"), timeout_s=30.0)
    )
    if identity_record.return_code != 0 or identity_record.timed_out:
        raise CompleteTestError("receiver execution helper identity is unavailable")
    try:
        remote_exec_identity = json.loads(identity_record.stdout)
    except json.JSONDecodeError as error:
        raise CompleteTestError("receiver execution helper identity is invalid") from error
    if remote_exec_identity != expected_delegation["remote_exec"]:
        raise CompleteTestError("receiver execution helper differs from deployment")
    qualification = expected_delegation["qualification"]["launcher"]["path"]
    if not PurePosixPath(qualification).is_absolute():
        raise CompleteTestError("receiver qualification executable path must be absolute")
    qualification_identity_argv = [qualification, "runtime-identity"]
    qualification_identity_encoded = base64.urlsafe_b64encode(
        json.dumps(qualification_identity_argv, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    qualification_identity_record = LocalCommandTransport().execute(
        CommandPlan(
            ssh,
            (
                *common_ssh,
                remote_exec,
                "--argv-base64",
                qualification_identity_encoded,
                "--timeout",
                "30",
            ),
            timeout_s=45.0,
        )
    )
    if qualification_identity_record.return_code != 0:
        raise CompleteTestError("receiver qualification identity is unavailable")
    try:
        qualification_identity = json.loads(qualification_identity_record.stdout)
    except json.JSONDecodeError as error:
        raise CompleteTestError("receiver qualification identity is invalid") from error
    if qualification_identity != expected_delegation["qualification"]:
        raise CompleteTestError("receiver qualification executable differs from deployment")
    delegation_receipt = {
        "receiver_host": receiver_host,
        "ssh": ssh_identity,
        "known_hosts": known_hosts_identity,
        "remote_exec": remote_exec_identity,
        "qualification": qualification_identity,
    }
    receipt_encoded = base64.urlsafe_b64encode(
        json.dumps(delegation_receipt, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    remote_argv = [
        qualification,
        "complete-test",
        transmitter_host,
        receiver_host,
        "--sdr",
        sdr_selector,
        "--receiver-local",
        "--delegated-output",
        "--delegation-receipt-base64",
        receipt_encoded,
        *forwarded_arguments,
    ]
    encoded = base64.urlsafe_b64encode(
        json.dumps(remote_argv, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    record = LocalCommandTransport().execute(
        CommandPlan(
            ssh,
            (
                *common_ssh,
                remote_exec,
                "--argv-base64",
                encoded,
                "--timeout",
                str(timeout_s),
            ),
            timeout_s=timeout_s + 30.0,
        )
    )
    if record.timed_out or record.disconnected:
        raise CompleteTestError(
            "remote receiver campaign failed before returning a result: "
            f"{record.stderr.strip() or record.return_code}"
        )
    try:
        result = json.loads(record.stdout)
    except json.JSONDecodeError as error:
        raise CompleteTestError("remote receiver returned invalid campaign JSON") from error
    if not isinstance(result, dict):
        raise CompleteTestError("remote receiver returned a non-object campaign result")
    if record.return_code not in {0, 3, 4, 5, 6}:
        raise CompleteTestError(
            "remote receiver campaign failed before returning a classified result: "
            f"{record.stderr.strip() or record.return_code}"
        )
    bundle = result.get("bundle")
    if not isinstance(bundle, str) or not bundle:
        raise CompleteTestError("remote receiver result does not identify its campaign bundle")
    validation_argv = [qualification, "validate-complete-test", bundle]
    validation_encoded = base64.urlsafe_b64encode(
        json.dumps(validation_argv, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    validation = LocalCommandTransport().execute(
        CommandPlan(
            ssh,
            (
                *common_ssh,
                remote_exec,
                "--argv-base64",
                validation_encoded,
                "--timeout",
                "120",
            ),
            timeout_s=150.0,
        )
    )
    if validation.return_code != 0 or validation.timed_out or validation.disconnected:
        raise CompleteTestError("remote receiver campaign bundle validation failed")
    try:
        validated_result = json.loads(validation.stdout)
    except json.JSONDecodeError as error:
        raise CompleteTestError("remote receiver validation returned invalid JSON") from error
    if validated_result != result.get("result"):
        raise CompleteTestError("remote receiver result differs from its retained campaign bundle")
    if artifact(ssh) != ssh_identity or artifact(known_hosts) != known_hosts_identity:
        raise CompleteTestError("receiver delegation identity changed during execution")
    final_identity_record = LocalCommandTransport().execute(
        CommandPlan(ssh, (*common_ssh, remote_exec, "--identity"), timeout_s=30.0)
    )
    if final_identity_record.return_code != 0:
        raise CompleteTestError("receiver execution helper identity could not be rechecked")
    try:
        final_remote_identity = json.loads(final_identity_record.stdout)
    except json.JSONDecodeError as error:
        raise CompleteTestError("receiver execution helper recheck is invalid") from error
    if final_remote_identity != remote_exec_identity:
        raise CompleteTestError("receiver execution helper changed during execution")
    final_qualification_identity = LocalCommandTransport().execute(
        CommandPlan(
            ssh,
            (
                *common_ssh,
                remote_exec,
                "--argv-base64",
                qualification_identity_encoded,
                "--timeout",
                "30",
            ),
            timeout_s=45.0,
        )
    )
    if final_qualification_identity.return_code != 0:
        raise CompleteTestError("receiver qualification identity could not be rechecked")
    try:
        final_qualification_document = json.loads(final_qualification_identity.stdout)
    except json.JSONDecodeError as error:
        raise CompleteTestError("receiver qualification identity recheck is invalid") from error
    if final_qualification_document != qualification_identity:
        raise CompleteTestError("receiver qualification executable changed during execution")
    if result["result"].get("delegation_receipt") != delegation_receipt:
        raise CompleteTestError("retained campaign omits receiver delegation evidence")
    return result


def _parse_sdr_selector(selector: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in selector.split(","):
        if item.count("=") != 1:
            raise CompleteTestError("--sdr must use comma-separated key=value fields")
        key, value = (part.strip() for part in item.split("=", 1))
        if not key or not value or key in fields:
            raise CompleteTestError("--sdr contains an empty or duplicate field")
        fields[key] = value
    if "serial" not in fields:
        raise CompleteTestError("--sdr must include a stable serial field")
    return fields


def resolve_local_sdr(selector: str, *, timeout_s: float = 20.0) -> dict[str, str]:
    """Resolve one exact SoapySDR selector on the receiver execution host."""
    utility = discover_executable("SoapySDRUtil")
    if utility is None:
        raise CompleteTestError("SoapySDRUtil is unavailable on the receiver host")
    record = LocalCommandTransport().execute(
        CommandPlan(utility, (f"--find={selector}",), timeout_s=timeout_s)
    )
    if record.return_code != 0 or record.timed_out:
        raise CompleteTestError(
            "SDR discovery failed: " + (record.stderr.strip() or str(record.return_code))
        )
    devices: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in record.stdout.splitlines():
        line = raw_line.strip()
        if line.lower().startswith("found device "):
            if current is not None:
                devices.append(current)
            current = {}
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()
    if current is not None:
        devices.append(current)
    requested = _parse_sdr_selector(selector)
    matches = [
        device
        for device in devices
        if all(device.get(key) == value for key, value in requested.items())
    ]
    if len(matches) != 1:
        raise CompleteTestError(
            f"SDR selector must resolve exactly one device; resolved {len(matches)}"
        )
    return matches[0]


@dataclass(frozen=True)
class CompleteTestOverrides:
    band: str = "20m"
    frequency_hz: int = 14_097_100
    callsign: str = "Q0QQQ"
    grid: str = "JJ00"
    power_dbm: int = 0
    message: str = "ET"
    qrss_dot_seconds: float = 0.7
    fskcw_dot_seconds: float = 0.7
    dfcw_dot_seconds: float = 0.7
    fskcw_separation_hz: float = 5.0
    dfcw_separation_hz: float = 5.0
    keyed_observations: int = 3
    wspr_observations: int = 3

    def validated(self) -> dict[str, object]:
        values = asdict(self)
        if not self.band.strip() or self.frequency_hz <= 1_500:
            raise CompleteTestError("band must be non-empty and frequency must exceed 1500 Hz")
        if not self.callsign.strip() or self.callsign != self.callsign.upper():
            raise CompleteTestError("callsign must be non-empty canonical uppercase")
        if not self.grid.strip() or self.grid != self.grid.upper():
            raise CompleteTestError("grid must be non-empty canonical uppercase")
        if self.power_dbm not in {
            0,
            3,
            7,
            10,
            13,
            17,
            20,
            23,
            27,
            30,
            33,
            37,
            40,
            43,
            47,
            50,
            53,
            57,
            60,
        }:
            raise CompleteTestError("power-dbm must be a standard WSPR encoded value")
        if not self.message.strip():
            raise CompleteTestError("keyed message must not be empty")
        numeric = (
            self.qrss_dot_seconds,
            self.fskcw_dot_seconds,
            self.dfcw_dot_seconds,
            self.fskcw_separation_hz,
            self.dfcw_separation_hz,
        )
        if any(value <= 0 for value in numeric):
            raise CompleteTestError("dot durations and tone separations must be positive")
        if self.keyed_observations != 3 or self.wspr_observations != 3:
            raise CompleteTestError("maintained qualification contracts require three observations")
        return values


def _validate_host(host: str, label: str) -> str:
    if not host.strip() or host in {".", ".."} or any(c in host for c in "/\\"):
        raise CompleteTestError(f"{label} must be one exact host name")
    return host


def _default_configuration_root() -> Path:
    override = os.environ.get("WSPQ_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and (program_data := os.environ.get("PROGRAMDATA")):
        return Path(program_data) / "wsprrypi-qualification"
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support/wsprrypi-qualification"
    return Path("/etc/wsprrypi-qualification")


def configuration_path(
    transmitter_host: str, receiver_host: str, explicit: Path | None = None
) -> Path:
    _validate_host(transmitter_host, "TRANSMITTER_HOST")
    _validate_host(receiver_host, "RECEIVER_HOST")
    if explicit is not None:
        return explicit.absolute()
    return (
        _default_configuration_root()
        / "complete-test"
        / f"{transmitter_host}--{receiver_host}.json"
    )


def _load(path: Path) -> dict[str, Any]:
    if _contains_symlink(path) or not path.is_file():
        raise CompleteTestError(f"configuration input is unavailable or unsafe: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompleteTestError(f"cannot load {path}: {error}") from error
    if not isinstance(value, dict):
        raise CompleteTestError(f"{path} must contain a JSON object")
    return value


def load_saved_configuration(
    transmitter_host: str, receiver_host: str, explicit: Path | None = None
) -> tuple[Path, dict[str, Any]]:
    path = configuration_path(transmitter_host, receiver_host, explicit)
    document = _load(path)
    validate_document(document, "complete-test-configuration.schema.json")
    if document["transmitter_host"] != transmitter_host:
        raise CompleteTestError("deployment transmitter differs from TRANSMITTER_HOST")
    if document["receiver_host"] != receiver_host:
        raise CompleteTestError("deployment receiver differs from RECEIVER_HOST")
    if document["topology"] != "split_host_ssh":
        raise CompleteTestError("unsupported_topology: Track C supports split_host_ssh only")
    if set(document["production_templates"]) != {"real_session", "keyed_session"}:
        raise CompleteTestError("saved configuration must bind both production template families")
    return path, document


def _mode_plan_path(config_path: Path, raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    candidate = candidate.absolute()
    if _contains_symlink(candidate):
        raise CompleteTestError(f"deployment path contains a symbolic link: {candidate}")
    return candidate


def _contains_symlink(path: Path) -> bool:
    current = path.absolute()
    while current != current.parent:
        if current.is_symlink():
            return True
        current = current.parent
    return False


def _set_real_digest(plan: dict[str, Any]) -> None:
    digest = helper_configuration_plan_sha256(plan)
    for field in ("remote_helper", "receiver_helper", "capture_helper", "wsprd", "wsprrypi"):
        plan[field]["plan_sha256"] = digest


def _resolve_real(
    template: dict[str, Any],
    mode: str,
    values: dict[str, object],
    *,
    campaign_id: str,
    now: datetime,
) -> dict[str, Any]:
    plan = deepcopy(template)
    if plan.get("backend") == "rp1_gpclk":
        raise CompleteTestError(
            "missing_capability: rp1_gpclk is not supported by the current "
            "main production contracts"
        )
    suffix = f"ct-{campaign_id.split('-')[1]}-{mode.lower()}"
    plan["run_id"] = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{suffix}"
    plan["test_id"] = suffix
    plan["frequency_hz"] = values["frequency_hz"]
    plan["receiver"]["center_frequency_hz"] = values["frequency_hz"]
    plan["band"] = values["band"]
    plan["identity"] = {
        "callsign": values["callsign"],
        "grid": values["grid"],
        "power_dbm": values["power_dbm"],
    }
    plan["mode"] = mode
    plan["frame_count"] = 0 if mode == "TONE" else values["wspr_observations"]
    if mode == "TONE":
        plan["session_kind"] = "cw_live_tone"
        arguments = plan["tone_server"]["arguments"]
        if "--socket-loopback-only" not in arguments:
            raise CompleteTestError("TONE server must use loopback-only control")
        if "--no-http" not in arguments:
            arguments.append("--no-http")
    else:
        plan.pop("session_kind", None)
        plan.pop("tone_schedule", None)
        plan.pop("cw_contract", None)
        plan.pop("tone_server", None)
        plan["remote_helper"].pop("bounded_tone_endpoint", None)
        plan["remote_helper"].pop("wsprrypi_revision", None)
        plan["carrier"]["rf_on_sample_count"] = plan["carrier"]["rf_off_sample_count"]
        plan["deadlines"]["transmitter_s"] = max(plan["deadlines"]["transmitter_s"], 380)
        plan["deadlines"]["receiver_s"] = max(plan["deadlines"]["receiver_s"], 390)
        plan["deadlines"]["overall_s"] = max(plan["deadlines"]["overall_s"], 500)
        boundary = datetime.fromtimestamp(((int(now.timestamp()) // 120) + 1) * 120, UTC)
        if (boundary - now).total_seconds() < plan["coherent_capture"][
            "margin_before_first_slot_s"
        ]:
            boundary += timedelta(seconds=120)
        plan["slots_utc"] = [
            (boundary + timedelta(seconds=120 * index)).isoformat().replace("+00:00", "Z")
            for index in range(3)
        ]
    _set_real_digest(plan)
    validate_real_session_plan(plan)
    return plan


def _resolve_keyed(
    template: dict[str, Any],
    mode: str,
    values: dict[str, object],
    *,
    campaign_id: str,
    sdr_selector: str,
) -> dict[str, Any]:
    plan = deepcopy(template)
    plan["session_id"] = f"ct-{campaign_id.split('-')[1]}-{mode.lower()}"
    frequency = float(cast(int, values["frequency_hz"]))
    dot = float(cast(float, values[f"{mode.lower()}_dot_seconds"]))
    separation = (
        None if mode == "QRSS" else float(cast(float, values[f"{mode.lower()}_separation_hz"]))
    )
    secondary = None if separation is None else frequency - separation
    backend = plan["application_plan"]["backend"]
    if backend not in {"gpio", "si5351"}:
        raise CompleteTestError(f"unsupported backend for maintained application shim: {backend}")
    identity_doc = plan["application_plan"]["identity"]
    identity = ApplicationIdentity(**identity_doc)
    backend_config = WsprryPiBackendConfig(**plan["application_plan"]["backend_contract"])
    application = (
        WsprryPiShim(identity, backend=backend, backend_config=backend_config)
        .resolve_plan(
            f"{plan['session_id']}-{mode.lower()}-application",
            CwProtocol(
                ProtocolMode(mode.lower()), str(values["message"]), dot, frequency, secondary
            ),
        )
        .to_document()
    )
    plan["mode"] = mode
    plan["transmitter"]["frequency_hz"] = values["frequency_hz"]
    plan["receiver"]["center_frequency_hz"] = values["frequency_hz"]
    device_identity = _parse_sdr_selector(sdr_selector)["serial"]
    plan["receiver"]["device"] = device_identity
    plan["receiver"]["identity_sha256"] = hashlib.sha256(
        device_identity.encode("utf-8")
    ).hexdigest()
    plan["application_plan"] = application
    plan["transaction_count"] = values["keyed_observations"]
    plan["message_repetitions_per_transaction"] = 1
    validate_resolved_keyed_plan(plan)
    return plan


def _materialize_cw_reference(
    plan: dict[str, Any], mode: str, destination: Path, *, now: datetime
) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    tone = mode == "TONE"
    application: dict[str, Any] = {} if tone else plan["application_plan"]
    protocol: dict[str, Any] = {} if tone else application["protocol_contract"]
    source_revision = (
        plan["source"]["parent_revision"] if tone else application["identity"]["source_revision"]
    )
    submodule_revision = (
        plan["source"]["submodule_revision"]
        if tone
        else application["identity"]["submodule_revision"]
    )
    normalized_mode = mode.lower()
    primary = plan["frequency_hz"] if tone else protocol["primary_frequency_hz"]
    secondary = None if tone else protocol["secondary_frequency_hz"]
    dot = None if tone else protocol["dot_seconds"]
    mode_plan = {
        "schema_version": 1,
        "evidence_type": "resolved_cw_mode_plan",
        "run_id": (
            plan["run_id"]
            if tone
            else f"{now.strftime('%Y%m%dT%H%M%SZ')}-{mode.lower()}-{secrets.token_hex(4)}"
        ),
        "mode": normalized_mode,
        "backend": plan["backend"] if tone else plan["transmitter"]["backend"],
        "hardware_profile": "complete-test-resolved-campaign",
        "band": plan.get("band", "20m"),
        "source": {
            "parent_revision": source_revision,
            "submodule_revision": submodule_revision,
        },
        "transmitter": {
            "host": plan["host"] if tone else plan["transmitter"]["host"],
            "output": plan["output"] if tone else plan["transmitter"]["output"],
            "model": "configured WsprryPi host",
            "drive_value": plan["drive"]["value"] if tone else plan["transmitter"]["drive"],
            "drive_unit": plan["drive"]["unit"] if tone else "power_level",
            "clock_reference": "configured transmitter clock",
        },
        "receiver": {
            "host": plan["receiver"]["host"],
            "driver": plan["receiver"]["driver"],
            "device_identity": (plan["receiver"]["serial"] if tone else plan["receiver"]["device"]),
        },
        "rf_path": {
            "attenuation_db": plan["rf_path"].get("attenuation_db"),
            "filter_state": plan["rf_path"].get("filter")
            or plan["rf_path"].get("filter_state")
            or "not specified for radiated path",
            "termination": plan["rf_path"].get("termination") or "radiated receiver path",
            "antenna_state": (
                "connected" if plan["rf_path"].get("antenna_connected") else "disconnected"
            ),
            "safe_input_basis": plan["rf_path"]["safe_input_basis"],
        },
        "protocol": {
            "definition": (
                "wspq-tone@v1"
                if tone
                else "wsprrypi-dfcw@v1"
                if normalized_mode == "dfcw"
                else f"wspq-{normalized_mode}@v1"
            ),
            "message": None if tone else protocol["message"],
            "dot_seconds": dot,
            "repetitions": None if tone else 1,
            "primary_frequency_hz": primary,
            "secondary_frequency_hz": secondary,
            "pre_quiet_seconds": plan["tone_schedule"]["off_seconds"] if tone else 2.0,
            "post_quiet_seconds": plan["tone_schedule"]["off_seconds"] if tone else 2.0,
            "intra_element_gap_units": (
                None if tone else 0.333333 if normalized_mode == "dfcw" else 1.0
            ),
            "inter_character_gap_units": (
                None if tone else 1.0 if normalized_mode == "dfcw" else 3.0
            ),
            "inter_word_gap_units": (None if tone else 3.0 if normalized_mode == "dfcw" else 7.0),
            "tone_cycles": plan["tone_schedule"]["cycles"] if tone else None,
            "tone_on_seconds": plan["tone_schedule"]["on_seconds"] if tone else None,
            "tone_off_seconds": plan["tone_schedule"]["off_seconds"] if tone else None,
        },
        "capture_contract": {
            "format": "CF32LE",
            "sample_rate_hz": plan["receiver"]["sample_rate_hz"],
            "center_frequency_hz": plan["receiver"]["center_frequency_hz"],
            "sample_count": (
                plan["carrier"]["rf_on_sample_count"]
                if tone
                else plan["receiver"]["sample_rate_hz"] * 600
            ),
            "overflow_max": 0,
            "fixed_gain": True,
            "agc_enabled": False,
            "bias_tee_enabled": False,
            "first_read_discarded": True,
        },
        "thresholds": {
            "frequency_tolerance_hz": 1.0,
            "spacing_tolerance_hz": 1.0,
            "minimum_contrast_db": 10.0,
            "timing_tolerance_s": 0.15,
            "maximum_transition_s": 0.25,
            "maximum_clipping_fraction": 0.01,
        },
        "resolved_utc": now.isoformat().replace("+00:00", "Z"),
    }
    events = generate_expected_events(mode_plan)
    if not tone:
        duration = float(events[-1]["end_s"])
        mode_plan["capture_contract"]["sample_count"] = math.ceil(
            duration * plan["receiver"]["sample_rate_hz"]
        )
        events = generate_expected_events(mode_plan)
    plan_path = destination / "mode-plan.json"
    expected_path = destination / "expected-events.json"
    write_json_new(plan_path, mode_plan, schema_name="cw-mode-plan.schema.json")
    expected = {
        "schema_version": 1,
        "evidence_type": "cw_expected_events",
        "run_id": mode_plan["run_id"],
        "mode": normalized_mode,
        "plan": artifact(plan_path),
        "generator": {
            "origin": "harness_generated",
            "name": "wsprrypi-qualification-cw-reference",
            "version": "1",
            "source_revision": plan.get("analyzer_revision", source_revision),
        },
        "protocol_definition": mode_plan["protocol"]["definition"],
        "events": events,
    }
    write_json_new(expected_path, expected, schema_name="cw-expected-events.schema.json")
    if tone:
        plan["cw_contract"]["plan"] = artifact(plan_path)
        plan["cw_contract"]["expected_events"] = artifact(expected_path)
    else:
        plan["reference"] = {
            "plan": artifact(plan_path),
            "expected_events": artifact(expected_path),
        }


def _materialize_real_profiles(plan: dict[str, Any], destination: Path, *, now: datetime) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    receiver = plan["receiver"]
    rf_path = plan["rf_path"]
    termination = rf_path.get("termination")
    if (
        rf_path["path_type"] != "conducted"
        or not isinstance(termination, str)
        or re.match(r"^50(?:\.0+)?[ -]?ohm\b", termination, re.IGNORECASE) is None
    ):
        raise CompleteTestError(
            "automatic complete-test requires an explicit 50-ohm conducted termination"
        )
    bench = {
        "schema_version": 1,
        "bench_id": "complete-test-conducted",
        "receiver": {
            "transport": "local",
            "host": receiver["host"],
            "driver": receiver["driver"],
            "serial": receiver["serial"],
            "channel": receiver["channel"],
            "sample_rate_hz": receiver["sample_rate_hz"],
            "bandwidth_hz": receiver["bandwidth_hz"],
            "sample_format": receiver["sample_format"],
            "agc": receiver["agc"],
            "bias_tee": receiver["bias_tee"],
        },
        "rf_path": {
            "path_type": rf_path["path_type"],
            "antenna_connected": rf_path["antenna_connected"],
            "termination_ohms": 50,
            "attenuation_db": rf_path["attenuation_db"],
            "filter_description": rf_path["filter"] or "none",
            "safe_input_description": rf_path["safe_input_basis"],
        },
    }
    tone = plan["mode"] == "TONE"
    transmitter = {
        "transport": "ssh",
        "host": plan["host"],
        "backend": plan["backend"],
        "output": plan["output"],
        "source_revision": plan["source"]["parent_revision"],
        "submodule_revision": plan["source"]["submodule_revision"],
    }
    if plan["backend"] == "gpio":
        transmitter.update(
            {
                "gpio_pin": plan["backend_contract"]["gpio_pin"],
                "power_level": plan["backend_contract"]["drive_or_power_level"],
                "pacing_clocks": 1,
            }
        )
    test = {
        "schema_version": 1,
        "test_id": f"complete-test-{plan['mode'].lower()}",
        "transmitter": transmitter,
        "band": plan["band"],
        "mode": plan["mode"],
        "frequency_hz": plan["frequency_hz"],
        "receiver_center_hz": receiver["center_frequency_hz"],
        "receiver_gain_db": receiver["gain_db"],
        "ppm": plan["calibration"]["ppm"],
        "identity": plan["identity"],
        "gates": {
            "carrier_offset_max_hz": plan["carrier"]["offset_gate_hz"],
            "best_20hz_share_min": plan["carrier"]["best_20hz_share_min"],
            "required_consecutive_decodes": 0 if tone else 3,
        },
        "stopping_procedure": {
            "transmitter_termination": plan["stopping_procedure"]["transmitter"],
            "receiver_termination": plan["stopping_procedure"]["receiver"],
            "operator_abort": "bounded supervisor cancellation",
            "cleanup_expectation": plan["stopping_procedure"]["cleanup"],
            "emergency_stop_note": "stop the owned process and verify GPIO quiescence",
        },
        "frame_count": plan["frame_count"],
        "bounded_duration_s": 14 if tone else 370,
        "random_offset_enabled": False,
    }
    duration = 14 if tone else 370
    run_profile = {
        "schema_version": 1,
        "run_id": f"{now.strftime('%Y%m%dT%H%M%SZ')}-receiver-{plan['mode'].lower()}",
        "bench_id": bench["bench_id"],
        "receiver": bench["receiver"],
        "center_frequency_hz": receiver["center_frequency_hz"],
        "gain_db": receiver["gain_db"],
        "duration_s": duration,
        "rf_path": bench["rf_path"],
        "limits": {
            "sample_count": duration * receiver["sample_rate_hz"],
            "read_timeout_us": receiver["read_timeout_us"],
            "helper_deadline_s": duration + 10,
            "external_deadline_s": duration + 20,
        },
        "authorization": {
            "scope": "single_run",
            "reference": "complete-test deliberate invocation",
            "recorded_utc": now.isoformat().replace("+00:00", "Z"),
        },
        "ownership_and_cleanup": "capture is exact-count and owned by this campaign",
    }
    documents = {
        "bench": (bench, "bench-profile.schema.json"),
        "test": (test, "test-profile.schema.json"),
        "receiver_run": (run_profile, "receiver-run-profile.schema.json"),
    }
    for name, (document, schema) in documents.items():
        path = destination / f"{name}.json"
        write_json_new(path, document, schema_name=schema)
        record = artifact(path)
        binding = {
            "id": document.get(f"{name}_id", name),
            "path": record["path"],
            "sha256": record["sha256"],
        }
        plan["requested_profiles"][name] = binding
        plan["resolved_profiles"][name] = binding


def compose_complete_test_plan(
    transmitter_host: str,
    receiver_host: str,
    sdr_selector: str,
    *,
    configuration: Path | None = None,
    overrides: CompleteTestOverrides | None = None,
    discovered_sdr: dict[str, str] | None = None,
    delegation_receipt: dict[str, Any] | None = None,
    live: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    _validate_host(transmitter_host, "TRANSMITTER_HOST")
    _validate_host(receiver_host, "RECEIVER_HOST")
    if not sdr_selector.strip() or "\x00" in sdr_selector:
        raise CompleteTestError("--sdr must be one non-empty exact device selector")
    selector_fields = _parse_sdr_selector(sdr_selector)
    if live and (
        discovered_sdr is None
        or any(discovered_sdr.get(key) != value for key, value in selector_fields.items())
    ):
        raise CompleteTestError("live complete-test requires the exact discovered SDR")
    config_path, config = load_saved_configuration(transmitter_host, receiver_host, configuration)
    if config["sdr_selector"] != sdr_selector:
        raise CompleteTestError("specified SDR differs from the deployed receiver binding")
    values = (overrides or CompleteTestOverrides()).validated()
    composed_at = datetime.now(UTC) if now is None else now.astimezone(UTC)
    campaign_id = validate_manifest_name(
        f"{composed_at.strftime('%Y%m%dT%H%M%S%fZ')}-{secrets.token_hex(4)}-{config['campaign_id']}"
    )
    bindings: list[dict[str, Any]] = []
    ssh_path = _mode_plan_path(config_path, config["ssh_executable"])
    if ssh_path.is_symlink() or not ssh_path.is_file():
        raise CompleteTestError("saved SSH executable is unavailable or unsafe")
    work_path = _mode_plan_path(config_path, config["work_directory"])
    output_path = _mode_plan_path(config_path, config["output_parent"])
    for mode in MODE_ORDER:
        family = "real_session" if mode in {"TONE", "WSPR"} else "keyed_session"
        source = _mode_plan_path(config_path, config["production_templates"][family])
        template = _load(source)
        plan = (
            _resolve_real(template, mode, values, campaign_id=campaign_id, now=composed_at)
            if mode in {"TONE", "WSPR"}
            else _resolve_keyed(
                template,
                mode,
                values,
                campaign_id=campaign_id,
                sdr_selector=sdr_selector,
            )
        )
        if mode != "WSPR":
            reference_directory = work_path / f"{campaign_id}-inputs" / mode.lower()
            _materialize_cw_reference(plan, mode, reference_directory, now=composed_at)
        if mode in {"TONE", "WSPR"}:
            profile_directory = work_path / f"{campaign_id}-inputs" / mode.lower() / "profiles"
            _materialize_real_profiles(plan, profile_directory, now=composed_at)
        if mode in {"TONE", "WSPR"}:
            _set_real_digest(plan)
            validate_real_session_plan(plan)
        else:
            validate_resolved_keyed_plan(plan)
        resolved_host = plan["host"] if mode in {"TONE", "WSPR"} else plan["transmitter"]["host"]
        if resolved_host != transmitter_host:
            raise CompleteTestError(f"{mode} plan transmitter differs from TRANSMITTER_HOST")
        if plan["receiver"]["host"] != receiver_host:
            raise CompleteTestError(f"{mode} plan receiver differs from RECEIVER_HOST")
        receiver = plan["receiver"]
        expected_driver = selector_fields.get("driver")
        if expected_driver is not None and receiver["driver"] != expected_driver:
            raise CompleteTestError(f"{mode} plan receiver driver differs from --sdr")
        plan_device = receiver["serial"] if mode in {"TONE", "WSPR"} else receiver["device"]
        expected_device = selector_fields["serial"]
        if plan_device != expected_device:
            raise CompleteTestError(f"{mode} plan receiver serial differs from --sdr")
        bindings.append(
            {
                "mode": mode,
                "source": artifact(source),
                "plan": plan,
                "plan_sha256": resolved_real_plan_sha256(plan)
                if mode in {"TONE", "WSPR"}
                else canonical_sha256(plan),
                "production_route": "real_session" if mode in {"TONE", "WSPR"} else "live_keyed",
            }
        )
    document = {
        "schema_version": 1,
        "evidence_type": "resolved_complete_test_plan",
        "campaign_id": campaign_id,
        "transmitter_host": transmitter_host,
        "receiver_host": receiver_host,
        "sdr_selector": sdr_selector,
        "sdr_discovery": discovered_sdr,
        "delegation_receipt": delegation_receipt,
        "execution_policy": "live" if live else "hardware_free",
        "authorization": "deliberate_invocation",
        "configuration": {"artifact": artifact(config_path), "document": config},
        "defaults": DEFAULTS,
        "resolved_values": values,
        "derived_frequencies": {
            "wspr_dial_frequency_hz": int(cast(int, values["frequency_hz"])) - 1_500,
            "wspr_audio_offset_hz": 1_500,
            "fskcw_secondary_frequency_hz": float(cast(int, values["frequency_hz"]))
            - float(cast(float, values["fskcw_separation_hz"])),
            "dfcw_secondary_frequency_hz": float(cast(int, values["frequency_hz"]))
            - float(cast(float, values["dfcw_separation_hz"])),
        },
        "mode_order": list(MODE_ORDER),
        "mode_plans": bindings,
        "topology": config["topology"],
        "transport": "ssh",
        "campaign_deadline_s": config["campaign_deadline_s"],
        "execution_paths": {
            "ssh_executable": artifact(ssh_path),
            "work_directory": str(work_path),
            "output_parent": str(output_path),
        },
        "production_adapters_constructed": False,
        "qualification_claim": False,
    }
    validate_complete_test_plan(document)
    return document


def validate_complete_test_plan(document: dict[str, Any]) -> dict[str, Any]:
    validate_document(document, "resolved-complete-test-plan.schema.json")
    if document["mode_order"] != list(MODE_ORDER):
        raise CompleteTestError("complete-test mode order changed")
    values = document["resolved_values"]
    if document["defaults"] != DEFAULTS:
        raise CompleteTestError("complete-test canonical defaults changed")
    try:
        validated_values = CompleteTestOverrides(**values).validated()
    except (TypeError, CompleteTestError) as error:
        raise CompleteTestError("complete-test resolved values are invalid") from error
    if validated_values != values:
        raise CompleteTestError("complete-test resolved values are not canonical")
    selector_fields = _parse_sdr_selector(document["sdr_selector"])
    if document["execution_policy"] == "live":
        discovered = document["sdr_discovery"]
        if not isinstance(discovered, dict) or any(
            discovered.get(key) != value for key, value in selector_fields.items()
        ):
            raise CompleteTestError("complete-test retained SDR discovery is invalid")
    expected_derived = {
        "wspr_dial_frequency_hz": int(cast(int, values["frequency_hz"])) - 1_500,
        "wspr_audio_offset_hz": 1_500,
        "fskcw_secondary_frequency_hz": float(values["frequency_hz"])
        - float(values["fskcw_separation_hz"]),
        "dfcw_secondary_frequency_hz": float(values["frequency_hz"])
        - float(values["dfcw_separation_hz"]),
    }
    if document["derived_frequencies"] != expected_derived:
        raise CompleteTestError("derived frequency evidence contradicts resolved overrides")
    if [entry["mode"] for entry in document["mode_plans"]] != list(MODE_ORDER):
        raise CompleteTestError(
            "complete-test subordinate plans are missing, duplicated, or reordered"
        )
    if document["topology"] != "split_host_ssh" or document["transport"] != "ssh":
        raise CompleteTestError(
            "unsupported_topology: local production transport belongs to Track D"
        )
    minimum_campaign = sum(
        entry["plan"]["deadlines"]["overall_s"] for entry in document["mode_plans"]
    )
    if document["campaign_deadline_s"] < minimum_campaign:
        raise CompleteTestError("campaign deadline cannot contain all subordinate deadlines")
    if document["campaign_deadline_s"] > 7200:
        raise CompleteTestError("campaign deadline exceeds the maintained two-hour safety bound")
    config_binding = document["configuration"]
    config_document = config_binding["document"]
    if (
        config_document["transmitter_host"] != document["transmitter_host"]
        or config_document["receiver_host"] != document["receiver_host"]
        or config_document["sdr_selector"] != document["sdr_selector"]
    ):
        raise CompleteTestError("complete-test deployment binding changed")
    receipt = document["delegation_receipt"]
    if receipt is not None:
        expected_receipt = {
            "receiver_host": document["receiver_host"],
            **config_document["receiver_delegation"],
        }
        if receipt != expected_receipt:
            raise CompleteTestError("receiver delegation evidence differs from deployment")
    config_path = Path(config_binding["artifact"]["path"])
    if _contains_symlink(config_path) or not config_path.is_file():
        raise CompleteTestError("bound saved configuration is unavailable or unsafe")
    current_config_artifact = artifact(config_path)
    if (
        any(
            current_config_artifact[field] != config_binding["artifact"][field]
            for field in ("size_bytes", "sha256")
        )
        or _load(config_path) != config_binding["document"]
    ):
        raise CompleteTestError("bound saved configuration changed")
    ssh_binding = document["execution_paths"]["ssh_executable"]
    ssh_path = Path(ssh_binding["path"])
    if _contains_symlink(ssh_path) or not ssh_path.is_file():
        raise CompleteTestError("bound SSH executable is unavailable or unsafe")
    current_ssh = artifact(ssh_path)
    if any(current_ssh[field] != ssh_binding[field] for field in ("size_bytes", "sha256")):
        raise CompleteTestError("bound SSH executable changed")
    for entry in document["mode_plans"]:
        mode = entry["mode"]
        child = entry["plan"]
        transmitter_host = (
            child["host"] if mode in {"TONE", "WSPR"} else child["transmitter"]["host"]
        )
        receiver = child["receiver"]
        if transmitter_host != document["transmitter_host"]:
            raise CompleteTestError(f"{mode} retained transmitter binding changed")
        if receiver["host"] != document["receiver_host"]:
            raise CompleteTestError(f"{mode} retained receiver binding changed")
        if selector_fields.get("driver", receiver["driver"]) != receiver["driver"]:
            raise CompleteTestError(f"{mode} retained SDR driver binding changed")
        expected_device = selector_fields["serial"]
        actual_device = receiver["serial"] if mode in {"TONE", "WSPR"} else receiver["device"]
        if actual_device != expected_device:
            raise CompleteTestError(f"{mode} retained SDR identity binding changed")
        source_path = Path(entry["source"]["path"])
        if _contains_symlink(source_path) or not source_path.is_file():
            raise CompleteTestError(f"{mode} source template is unavailable or unsafe")
        current_source = artifact(source_path)
        if any(
            current_source[field] != entry["source"][field] for field in ("size_bytes", "sha256")
        ):
            raise CompleteTestError(f"{mode} source template changed")
        expected = (
            resolved_real_plan_sha256(child)
            if mode in {"TONE", "WSPR"}
            else canonical_sha256(validate_resolved_keyed_plan(child))
        )
        if entry["plan_sha256"] != expected:
            raise CompleteTestError(f"{mode} subordinate plan digest mismatch")
        if mode in {"TONE", "WSPR"}:
            validate_real_session_plan(child)
            if (
                child["frequency_hz"] != values["frequency_hz"]
                or child["band"] != values["band"]
                or child["identity"]
                != {
                    "callsign": values["callsign"],
                    "grid": values["grid"],
                    "power_dbm": values["power_dbm"],
                }
            ):
                raise CompleteTestError(f"{mode} plan contradicts resolved campaign values")
        else:
            validate_resolved_keyed_plan(child)
            protocol = child["application_plan"]["protocol_contract"]
            if (
                protocol["message"] != values["message"]
                or protocol["dot_seconds"] != values[f"{mode.lower()}_dot_seconds"]
                or protocol["primary_frequency_hz"] != values["frequency_hz"]
                or protocol["secondary_frequency_hz"]
                != (
                    None
                    if mode == "QRSS"
                    else expected_derived[f"{mode.lower()}_secondary_frequency_hz"]
                )
            ):
                raise CompleteTestError(f"{mode} plan contradicts resolved campaign values")
    return deepcopy(document)


def complete_test_sha256(document: dict[str, Any]) -> str:
    return canonical_sha256(validate_complete_test_plan(document))


def _campaign_status(entries: list[dict[str, Any]]) -> str:
    statuses = [
        entry["final_status"]
        for entry in entries
        if entry["state"] in {"attempted", "attempted_unverified"}
    ]
    stopped_statuses = [
        entry["final_status"] for entry in entries if entry["state"] == "not_attempted"
    ]
    if statuses and all(status == "qualified" for status in statuses) and len(statuses) == 5:
        return "qualified"
    precedence = {
        "qualified": 0,
        "inconclusive": 1,
        "unqualified_carrier": 2,
        "unqualified_decode": 2,
        "unqualified_keyed": 2,
        "fixture_blocked": 3,
        "preflight_failed": 4,
        "aborted": 5,
        "cleanup_failed": 6,
    }
    candidates = statuses + stopped_statuses
    return max(candidates, key=precedence.__getitem__) if candidates else "inconclusive"


def _stops_campaign(mode: str, status: str) -> bool:
    del mode
    return status in {
        "cleanup_failed",
        "aborted",
        "preflight_failed",
        "fixture_blocked",
        "inconclusive",
    }


def validate_complete_test_bundle(bundle: Path) -> dict[str, Any]:
    """Recompute an aggregate and all linked subordinate result identities."""
    if _contains_symlink(bundle):
        raise CompleteTestError("complete-test bundle is unavailable or unsafe")
    root = bundle.resolve()
    if not root.is_dir():
        raise CompleteTestError("complete-test bundle is unavailable or unsafe")
    manifest = root / "SHA256SUMS"
    if manifest.is_symlink() or not manifest.is_file():
        raise CompleteTestError("complete-test manifest is unavailable or unsafe")
    if manifest.read_text(encoding="utf-8") != render_manifest(build_manifest(root)):
        raise CompleteTestError("complete-test manifest does not match bundle")
    plan = validate_complete_test_plan(_load(root / "resolved-plan.json"))
    result = _load(root / "result.json")
    validate_document(result, "complete-test-result.schema.json")
    if (
        result["campaign_id"] != plan["campaign_id"]
        or result["campaign_plan_sha256"] != complete_test_sha256(plan)
        or result["transmitter_host"] != plan["transmitter_host"]
        or result["receiver_host"] != plan["receiver_host"]
        or result["sdr_selector"] != plan["sdr_selector"]
        or result["delegation_receipt"] != plan["delegation_receipt"]
        or result["mode_order"] != list(MODE_ORDER)
        or [entry["mode"] for entry in result["modes"]] != list(MODE_ORDER)
    ):
        raise CompleteTestError("complete-test result does not bind its exact plan and order")
    for mode_result, mode_plan in zip(result["modes"], plan["mode_plans"], strict=True):
        if mode_result["plan_sha256"] != mode_plan["plan_sha256"]:
            raise CompleteTestError("complete-test result substitutes a subordinate plan")
        artifact_document = mode_result["result_artifact"]
        if mode_result["state"] == "attempted":
            child_relative = PurePosixPath(mode_result["authoritative_bundle"])
            if child_relative.is_absolute() or ".." in child_relative.parts:
                raise CompleteTestError("unsafe subordinate bundle relationship")
            expected_result_path = (child_relative / "result.json").as_posix()
            expected_manifest_path = (child_relative / "SHA256SUMS").as_posix()
            if artifact_document["path"] != expected_result_path:
                raise CompleteTestError("subordinate result does not belong to its bundle")
            unresolved_source = root.parent / artifact_document["path"]
            if _contains_symlink(unresolved_source):
                raise CompleteTestError("subordinate result artifact is unavailable or unsafe")
            source = unresolved_source.resolve()
            try:
                source.relative_to(root.parent)
            except ValueError as error:
                raise CompleteTestError(
                    "subordinate result artifact escapes output parent"
                ) from error
            if not source.is_file():
                raise CompleteTestError("subordinate result artifact is unavailable or unsafe")
            identity = artifact(source)
            if any(
                identity[field] != artifact_document[field] for field in ("size_bytes", "sha256")
            ):
                raise CompleteTestError("subordinate result artifact changed")
            if mode_result["result_sha256"] != identity["sha256"]:
                raise CompleteTestError("subordinate result digest changed")
            manifest_document = mode_result["bundle_manifest_artifact"]
            if manifest_document["path"] != expected_manifest_path:
                raise CompleteTestError("subordinate manifest does not belong to its bundle")
            manifest_source = root.parent / manifest_document["path"]
            if _contains_symlink(manifest_source) or not manifest_source.is_file():
                raise CompleteTestError("subordinate bundle manifest is unavailable or unsafe")
            manifest_identity = artifact(manifest_source)
            if any(
                manifest_identity[field] != manifest_document[field]
                for field in ("size_bytes", "sha256")
            ):
                raise CompleteTestError("subordinate bundle manifest changed")
            child_root = manifest_source.parent
            if manifest_source.read_text(encoding="utf-8") != render_manifest(
                build_manifest(child_root)
            ):
                raise CompleteTestError("subordinate bundle evidence changed")
    expected_status = _campaign_status(result["modes"])
    expected_claim = expected_status == "qualified" and all(
        entry["state"] == "attempted" for entry in result["modes"]
    )
    if result["final_status"] != expected_status or result["qualification_claim"] != expected_claim:
        raise CompleteTestError(
            "complete-test aggregate status or qualification claim is broader than evidence"
        )
    if result["cleanup_precedence_applied"] != any(
        entry["final_status"] == "cleanup_failed" for entry in result["modes"]
    ):
        raise CompleteTestError("complete-test cleanup precedence contradicts subordinate results")
    cleanup_authoritative = not any(
        entry["state"] == "attempted_unverified" for entry in result["modes"]
    )
    if (
        result["campaign_cleanup"]["subordinate_cleanup_authoritative"] != cleanup_authoritative
        or result["campaign_cleanup"]["restoration_authoritative"] != cleanup_authoritative
    ):
        raise CompleteTestError("complete-test cleanup authority exceeds retained evidence")
    all_authenticated = all(
        entry["result_sha256"] is not None
        for entry in result["modes"]
        if entry["state"] in {"attempted", "attempted_unverified"}
    ) and not any(entry["state"] == "attempted_unverified" for entry in result["modes"])
    if result["campaign_cleanup"]["all_attempted_modes_authenticated"] != all_authenticated:
        raise CompleteTestError("complete-test authentication claim exceeds retained evidence")
    return result


def _publish(
    plan: dict[str, Any], output_parent: Path, entries: list[dict[str, Any]]
) -> dict[str, Any]:
    parent = output_parent.resolve()
    final = parent / validate_manifest_name(plan["campaign_id"])
    temporary = parent / f".incomplete-{plan['campaign_id']}"
    if final.exists() or temporary.exists():
        raise CompleteTestError("campaign destination is not new")
    parent.mkdir(parents=True, exist_ok=True)
    temporary.mkdir()
    statuses = [
        entry["final_status"]
        for entry in entries
        if entry["state"] in {"attempted", "attempted_unverified"}
    ]
    final_status = _campaign_status(entries)
    result = {
        "schema_version": 1,
        "evidence_type": "complete_test_result",
        "campaign_id": plan["campaign_id"],
        "campaign_plan_sha256": complete_test_sha256(plan),
        "transmitter_host": plan["transmitter_host"],
        "receiver_host": plan["receiver_host"],
        "sdr_selector": plan["sdr_selector"],
        "delegation_receipt": plan["delegation_receipt"],
        "authorization": plan["authorization"],
        "mode_order": list(MODE_ORDER),
        "modes": entries,
        "final_status": final_status,
        "cleanup_precedence_applied": any(s == "cleanup_failed" for s in statuses),
        "campaign_cleanup": {
            "subordinate_cleanup_authoritative": not any(
                entry["state"] == "attempted_unverified" for entry in entries
            ),
            "restoration_authoritative": not any(
                entry["state"] == "attempted_unverified" for entry in entries
            ),
            "all_attempted_modes_authenticated": all(
                entry["result_sha256"] is not None
                for entry in entries
                if entry["state"] in {"attempted", "attempted_unverified"}
            )
            and not any(entry["state"] == "attempted_unverified" for entry in entries),
        },
        "qualification_scope": {
            "transmitter_host": plan["transmitter_host"],
            "receiver_host": plan["receiver_host"],
            "sdr_selector": plan["sdr_selector"],
            "band": plan["resolved_values"]["band"],
            "frequency_hz": plan["resolved_values"]["frequency_hz"],
            "modes": list(MODE_ORDER),
            "topology": plan["topology"],
        },
        "qualification_claim": final_status == "qualified" and len(statuses) == 5,
    }
    validate_document(result, "complete-test-result.schema.json")
    try:
        write_json_new(
            temporary / "resolved-plan.json",
            plan,
            schema_name="resolved-complete-test-plan.schema.json",
        )
        write_json_new(
            temporary / "result.json", result, schema_name="complete-test-result.schema.json"
        )
        write_manifest(temporary)
        validate_complete_test_bundle(temporary)
        temporary.replace(final)
        validate_complete_test_bundle(final)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        shutil.rmtree(final, ignore_errors=True)
        raise
    return {"bundle": str(final), "result": result}


def rehearse_complete_test(plan: dict[str, Any], output_parent: Path) -> dict[str, Any]:
    resolved = validate_complete_test_plan(plan)
    if resolved["execution_policy"] != "hardware_free":
        raise CompleteTestError("rehearsal requires a hardware_free complete-test plan")
    entries = [
        {
            "mode": mode,
            "state": "not_attempted",
            "final_status": "inconclusive",
            "plan_sha256": entry["plan_sha256"],
            "authoritative_bundle": None,
            "stopping_reason": "hardware-free routing rehearsal; no coordinator executed",
            "result_sha256": None,
            "result_artifact": None,
            "bundle_manifest_artifact": None,
        }
        for mode, entry in zip(MODE_ORDER, resolved["mode_plans"], strict=True)
    ]
    return _publish(resolved, output_parent, entries)


def run_complete_test(
    plan: dict[str, Any],
    output_parent: Path,
    *,
    ssh_executable: Path,
    work_directory: Path,
    dispatcher: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved = validate_complete_test_plan(plan)
    if resolved["execution_policy"] != "live":
        raise CompleteTestError("live complete-test requires a live plan")
    execution = resolved["execution_paths"]
    if ssh_executable.resolve() != Path(execution["ssh_executable"]["path"]):
        raise CompleteTestError("runtime SSH executable differs from the campaign binding")
    if work_directory.resolve() != Path(execution["work_directory"]):
        raise CompleteTestError("runtime work directory differs from the campaign binding")
    if output_parent.resolve() != Path(execution["output_parent"]):
        raise CompleteTestError("runtime output parent differs from the campaign binding")
    production_dispatch = dispatcher is None
    if production_dispatch:
        from wsprrypi_qualification.turnkey_campaign import run_live_campaign

        dispatcher = run_live_campaign
    assert dispatcher is not None
    entries: list[dict[str, Any]] = []
    stopped: str | None = None
    campaign_started = time.monotonic()
    for entry in resolved["mode_plans"]:
        mode = entry["mode"]
        child_campaign_id = f"ct-{resolved['campaign_id'].split('-')[1]}-{mode.lower()}"
        if (
            stopped is None
            and time.monotonic() - campaign_started >= resolved["campaign_deadline_s"]
        ):
            stopped = "campaign deadline elapsed before the next mode"
        child_deadline = entry["plan"]["deadlines"]["overall_s"]
        remaining = resolved["campaign_deadline_s"] - (time.monotonic() - campaign_started)
        if stopped is None and remaining < child_deadline:
            stopped = "campaign deadline cannot contain the next bounded mode"
        if stopped is not None:
            entries.append(
                {
                    "mode": mode,
                    "state": "not_attempted",
                    "final_status": "inconclusive",
                    "plan_sha256": entry["plan_sha256"],
                    "authoritative_bundle": None,
                    "stopping_reason": stopped,
                    "result_sha256": None,
                    "result_artifact": None,
                    "bundle_manifest_artifact": None,
                }
            )
            continue
        if production_dispatch:
            from wsprrypi_qualification.turnkey_campaign import compose_resolved_campaign_plan

            generated = (
                work_directory.resolve().parent
                / f"{resolved['campaign_id']}-generated"
                / mode.lower()
            )
            generated.mkdir(parents=True, exist_ok=False)
            request_path = generated / "request.json"
            child_path = generated / "resolved-mode-plan.json"
            request = {
                "schema_version": 1,
                "evidence_type": "turnkey_campaign_request",
                "campaign_id": child_campaign_id,
                "mode": mode,
                "execution_policy": "live",
            }
            write_json_new(
                request_path, request, schema_name="turnkey-campaign-request.schema.json"
            )
            write_json_new(child_path, entry["plan"])
            child_wrapper = compose_resolved_campaign_plan(request_path, child_path)
        else:
            child_wrapper = {
                "schema_version": 1,
                "evidence_type": "resolved_turnkey_campaign_plan",
                "campaign_id": child_campaign_id,
                "mode": mode,
                "execution_policy": "live",
                "request": resolved["configuration"],
                "mode_plan": {"artifact": entry["source"], "document": entry["plan"]},
                "production_route": entry["production_route"],
                "production_adapters_constructed": False,
                "qualification_claim": False,
            }
        # The internal exact digest is an execution binding, not an operator ceremony.
        from wsprrypi_qualification.turnkey_campaign import canonical_sha256 as wrapper_digest

        digest = wrapper_digest(child_wrapper)
        try:
            outcome = dispatcher(
                child_wrapper,
                output_parent,
                operator="complete-test-invocation",
                confirmed_plan_sha256=digest,
                ssh_executable=ssh_executable,
                work_directory=(
                    work_directory.resolve() / resolved["campaign_id"] / mode.lower()
                    if production_dispatch
                    else work_directory
                ),
            )
        except (Exception, KeyboardInterrupt) as error:
            stopped = f"{mode} coordinator blocked before authoritative publication: {error}"
            blocked_status = "aborted" if isinstance(error, KeyboardInterrupt) else "cleanup_failed"
            entries.append(
                {
                    "mode": mode,
                    "state": "attempted_unverified",
                    "final_status": blocked_status,
                    "plan_sha256": entry["plan_sha256"],
                    "authoritative_bundle": None,
                    "stopping_reason": stopped,
                    "result_sha256": None,
                    "result_artifact": None,
                    "bundle_manifest_artifact": None,
                }
            )
            continue
        unresolved_bundle = Path(str(outcome["authoritative_bundle"])).absolute()
        if _contains_symlink(unresolved_bundle):
            raise CompleteTestError("authoritative subordinate bundle is unavailable or unsafe")
        bundle = unresolved_bundle.resolve()
        try:
            relative = bundle.relative_to(output_parent.resolve()).as_posix()
        except ValueError as error:
            raise CompleteTestError(
                "authoritative subordinate bundle escapes output parent"
            ) from error
        if PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
            raise CompleteTestError("unsafe subordinate bundle path")
        result_path = bundle / "result.json"
        if bundle.is_symlink() or result_path.is_symlink() or not result_path.is_file():
            raise CompleteTestError("authoritative subordinate result is unavailable or unsafe")
        manifest_path = bundle / "SHA256SUMS"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise CompleteTestError("authoritative subordinate manifest is unavailable or unsafe")
        if manifest_path.read_text(encoding="utf-8") != render_manifest(build_manifest(bundle)):
            raise CompleteTestError("authoritative subordinate manifest does not match bundle")
        result_document = _load(result_path)
        if mode in {"TONE", "WSPR"}:
            status = validate_result_document(result_document).value
            session_document = _load(bundle / "session.json")
            if outcome["underlying_result"] != session_document:
                raise CompleteTestError("returned real-session result differs from its bundle")
        else:
            authorization = _load(bundle / "runtime-authorization.json")
            aggregate = _load(bundle / "aggregate-session.json")
            validate_keyed_result(entry["plan"], authorization, aggregate, result_document)
            status = str(result_document["final_status"])
            if outcome["underlying_result"].get("result") != result_document:
                raise CompleteTestError("returned keyed result differs from its bundle")
        result_identity = artifact(result_path)
        manifest_identity = artifact(manifest_path)
        entries.append(
            {
                "mode": mode,
                "state": "attempted",
                "final_status": status,
                "plan_sha256": entry["plan_sha256"],
                "authoritative_bundle": relative,
                "stopping_reason": None,
                "result_sha256": result_identity["sha256"],
                "result_artifact": {
                    **result_identity,
                    "path": f"{relative}/result.json",
                },
                "bundle_manifest_artifact": {
                    **manifest_identity,
                    "path": f"{relative}/SHA256SUMS",
                },
            }
        )
        if _stops_campaign(mode, status):
            stopped = f"{mode} ended with {status}"
    return _publish(resolved, output_parent, entries)
