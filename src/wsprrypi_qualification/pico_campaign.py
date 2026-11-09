"""Portable Pico five-mode composition and read-only admission decisions.

An engine/GPS/correction configuration is one separately identified campaign.
Three observations mean three complete WTP/capture transactions per mode;
paired WSPR messages stay inside an observation rather than inflating its count.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

from wsprrypi_qualification.cw_reference import generate_expected_events
from wsprrypi_qualification.offline import artifact, validate_document
from wsprrypi_qualification.pico_wire import (
    PicoWireError,
    loads,
    validate_contract,
    validate_diagnostic,
)
from wsprrypi_qualification.receiver_calibration import compose_binding, disabled_binding
from wsprrypi_qualification.wtp_control import job_digest, validate_event_job

MODE_ORDER = ("tone", "wspr", "qrss", "fskcw", "dfcw")


def analyzer_identity() -> dict[str, Any]:
    """Content identity of the maintained Harness controller and signal analyzers."""
    package = Path(__file__).parent
    records = [artifact(path) for path in sorted(package.glob("*.py"))]
    content = [dict(name=Path(item["path"]).name, sha256=item["sha256"]) for item in records]
    digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    return dict(revision="sha256:" + digest, files=records)


def ns(value: int | float | Decimal) -> str:
    number = Decimal(str(value)) * 1_000_000_000
    if number != number.to_integral_value() or not 0 <= number < 2**64:
        raise PicoWireError("Value is not exactly representable in uint64 nanounits")
    return str(int(number))


def validate_configuration(config: dict[str, Any]) -> None:
    validate_document(config, "pico-campaign-configuration.schema.json")
    target, receiver, protocol = (config[k] for k in ("pico", "receiver", "protocol"))
    for container, names in (
        (config, ("schema_version",)),
        (target, ("manual_correction_ppb",)),
        (receiver, ("sample_rate_hz", "bandwidth_hz", "channel")),
        (protocol, ("frequency_hz", "power_dbm")),
    ):
        if any(type(container[name]) is not int for name in names):
            raise PicoWireError("Pico configuration requires exact integer types")
    if (
        hashlib.sha256(b"WsprryPico/1" + bytes.fromhex(target["serial"])).hexdigest()[:32]
        != target["device_id"]
    ):
        raise PicoWireError("Pico serial/device binding differs")
    if target["management_port"] == target["wtp_port"]:
        raise PicoWireError("WTP and management must select separate CDC roles")
    if target["gps_enabled"] != (target["utc_source"] == "gps") or (
        target["automatic_correction"] and not target["gps_enabled"]
    ):
        raise PicoWireError("GPS/time/correction configuration is inconsistent")
    if receiver["bandwidth_hz"] > receiver["sample_rate_hz"]:
        raise PicoWireError("Receiver bandwidth exceeds the sampled channel")
    frequency = protocol["frequency_hz"]
    if target["engine"] == "pio-dma-gp2" and frequency + protocol["shift_hz"] >= 69_000_000:
        raise PicoWireError("GPIO campaign exceeds the ordinary 138 MHz profile")
    if (
        abs(frequency - receiver["center_frequency_hz"]) + protocol["shift_hz"] + 1000
        >= min(receiver["bandwidth_hz"], receiver["sample_rate_hz"]) / 2
    ):
        raise PicoWireError("Transmitter frequencies/acquisition guard exceed receiver coverage")
    if abs(frequency - receiver["center_frequency_hz"]) < 2000:
        raise PicoWireError("Select a receiver center with the DUT outside the DC guard")
    if config["rf_path"]["antenna_state"] != "disconnected":
        raise PicoWireError("Pico campaign requires a conducted RF path and disconnected antenna")


def compiler_input(config: dict[str, Any], mode: str) -> dict[str, Any]:
    protocol = config["protocol"]
    value: dict[str, Any] = dict(
        mode=mode,
        job_id=uuid.uuid4().hex,
        frequency_nhz=ns(protocol["frequency_hz"]),
        allow_frequency_adjustment=True,
    )
    if mode == "tone":
        value["duration_ns"] = ns(protocol["tone_seconds"])
        value.update(off_ns="2000000000", cycles=3)
    elif mode == "wspr":
        value.update(
            callsign=protocol["callsign"],
            locator=protocol["locator"],
            power_dbm=protocol["power_dbm"],
        )
    else:
        dot = Decimal(str(protocol["dot_seconds"]))
        dfcw = mode == "dfcw"
        value.update(
            message=protocol["message"],
            space_frequency_nhz=ns(
                Decimal(protocol["frequency_hz"]) - Decimal(str(protocol["shift_hz"]))
            )
            if mode != "qrss"
            else "0",
            timing=dict(
                dot_ns=ns(dot),
                dash_ns=ns(dot * 3),
                intra_gap_ns=ns(dot * Decimal("0.333333")) if dfcw else ns(dot),
                character_gap_ns=ns(dot if dfcw else dot * 3),
                word_gap_ns=ns(dot * 3 if dfcw else dot * 7),
            ),
            repeat_count=1,
            repeat_gap_ns="0",
        )
    return value


def compose_pico_campaign(
    configuration: dict[str, Any], compiler: Path, modes: tuple[str, ...] = MODE_ORDER
) -> dict[str, Any]:
    validate_configuration(configuration)
    if not modes or len(set(modes)) != len(modes) or not set(modes) <= set(MODE_ORDER):
        raise PicoWireError("Empty, duplicate, or unsupported Pico mode selection")
    compiler = compiler.resolve(strict=True)
    identity = artifact(compiler)
    config = copy.deepcopy(configuration)
    source = Path(config["pico_source"]).resolve(strict=True)
    for name in ("control", "wtp"):
        if (source / "engineering/protocol" / f"{name}-1.schema.json").read_bytes() != files(
            "wsprrypi_qualification.schemas"
        ).joinpath(f"pico-{name}.schema.json").read_bytes():
            raise PicoWireError("Selected Pico contract differs from the current Harness contract")
    if (source / "engineering/protocol/network-diagnostics.schema.json").read_bytes() != files(
        "wsprrypi_qualification.schemas"
    ).joinpath("pico-network-diagnostics.schema.json").read_bytes():
        raise PicoWireError("Selected Pico network diagnostics differ from the Harness contract")
    installer = artifact(source / "scripts/usb_installer.py")
    encoders: list[dict[str, Any]] | None = None
    cal = config["receiver_calibration"]
    calibration = (
        disabled_binding()
        if cal is None
        else compose_binding(Path(cal["profile"]), Path(cal["request"]))
    )
    rows = []
    revision = None
    carrier_job = None
    for mode in MODE_ORDER:
        if mode not in modes and not (mode == "tone" and "wspr" in modes):
            continue
        specification = compiler_input(config, mode)
        result = subprocess.run(
            [str(compiler)],
            input=json.dumps(specification),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode:
            raise PicoWireError("Production Pico encoder refused " + mode)
        value = loads(result.stdout)
        validate_document(value, "pico-compiler-output.schema.json")
        compiled_sources = []
        seen: set[str] = set()
        for record in value["source_files"]:
            relative = PurePosixPath(record["path"])
            if ".." in relative.parts or str(relative) in seen:
                raise PicoWireError("Compiler input is duplicated or outside its source tree")
            seen.add(str(relative))
            path = source / "src" / Path(*relative.parts)
            if not path.resolve(strict=True).is_relative_to(source / "src"):
                raise PicoWireError("Compiler input escapes the selected source tree")
            identity_record = artifact(path)
            if identity_record["sha256"] != record["sha256"]:
                raise PicoWireError(
                    "Compiler was built against different encoder inputs; rebuild it"
                )
            compiled_sources.append(identity_record)
        if encoders is not None and encoders != compiled_sources:
            raise PicoWireError("Compiler input identity changed during composition")
        encoders = compiled_sources
        if revision is not None and revision != value["source_revision"]:
            raise PicoWireError("Compiler source changed during composition")
        revision = value["source_revision"]
        for job in value["jobs"]:
            validate_event_job(job, maximum_duration_ns=3_600_000_000_000)
            validate_contract(job, "wtp", "job")
            if job["mode"] != mode:
                raise PicoWireError("Compiler returned a different mode")
        if mode == "tone":
            carrier_job = copy.deepcopy(value["jobs"][0])
        if mode not in modes:
            continue
        rows.append(
            dict(
                mode=mode,
                specification=specification,
                jobs=value["jobs"],
                independent_observations=3,
                carrier_job=carrier_job if mode == "wspr" else None,
            )
        )
    if artifact(compiler) != identity:
        raise PicoWireError("Compiler changed during composition")
    plan = dict(
        schema_version=1,
        target_kind="pico",
        configuration=config,
        compiler=identity,
        installer=installer,
        encoder_sources=encoders,
        analyzer_source=analyzer_identity(),
        source_revision=revision,
        receiver_calibration=calibration,
        modes=rows,
    )
    validate_pico_plan(plan)
    return plan


def validate_pico_plan(plan: dict[str, Any]) -> None:
    validate_document(plan, "pico-campaign-plan.schema.json")
    validate_configuration(plan["configuration"])
    modes = [row["mode"] for row in plan["modes"]]
    if len(set(modes)) != len(modes) or modes != [m for m in MODE_ORDER if m in modes]:
        raise PicoWireError("Pico mode identities/order are not canonical")
    for row in plan["modes"]:
        expected = compiler_input(plan["configuration"], row["mode"])
        expected["job_id"] = row["specification"]["job_id"]
        if expected != row["specification"]:
            raise PicoWireError("Pico compiler input differs from its configuration")
        for job in row["jobs"]:
            validate_event_job(job, maximum_duration_ns=3_600_000_000_000)
        if row["mode"] == "wspr":
            carrier = row["carrier_job"]
            if carrier is None or carrier["mode"] != "tone":
                raise PicoWireError("WSPR carrier gate is missing or has the wrong mode")
            carrier_plan = dict(plan)
            carrier_spec = compiler_input(plan["configuration"], "tone")
            carrier_spec["job_id"] = carrier["job_id"]
            carrier_plan["modes"] = [
                dict(
                    mode="tone",
                    specification=carrier_spec,
                    jobs=[carrier],
                    independent_observations=3,
                    carrier_job=None,
                )
            ]
            validate_pico_plan(carrier_plan)
            for job in row["jobs"]:
                if len(job["events"]) != 162 or job["total_duration_ns"] != "110592000000":
                    raise PicoWireError("WSPR requires its complete 162-symbol production frame")
                for index, event in enumerate(job["events"]):
                    begin = (index * 8192 * 1_000_000_000 + 6000) // 12000
                    end = ((index + 1) * 8192 * 1_000_000_000 + 6000) // 12000
                    delta = int(event.get("frequency_nhz", "0")) - int(expected["frequency_nhz"])
                    if (
                        not event["rf_on"]
                        or int(event["offset_ns"]) != begin
                        or int(event["duration_ns"]) != end - begin
                        or delta not in {0, 1464843750, 2929687500, 4394531250}
                    ):
                        raise PicoWireError("WSPR symbol timing or four-tone geometry differs")
            continue
        reference = cw_analysis_plan(plan, row["mode"], "20000101T000000Z-pico-reference", 2)
        timeline = generate_expected_events(reference)[1:-1]
        events = []
        for event in timeline:
            # Reference generation uses float seconds. Quantize its boundaries,
            # not each accumulated duration, to the transport's integer ns grid.
            start = round((event["start_s"] - 2) * 1_000_000_000)
            end_boundary = round((event["end_s"] - 2) * 1_000_000_000)
            duration = str(end_boundary - start)
            value: dict[str, Any] = dict(
                offset_ns=str(start), duration_ns=duration, rf_on=event["rf_state"] != "off"
            )
            if value["rf_on"]:
                value["frequency_nhz"] = (
                    expected["space_frequency_nhz"]
                    if event["rf_state"] == "secondary"
                    else expected["frequency_nhz"]
                )
            events.append(value)
        end = int(events[-1]["offset_ns"]) + int(events[-1]["duration_ns"])
        events.append(dict(offset_ns=str(end), duration_ns="1000", rf_on=False))
        if (
            len(row["jobs"]) != 1
            or row["jobs"][0]["events"] != events
            or int(row["jobs"][0]["total_duration_ns"]) != end + 1000
        ):
            raise PicoWireError("Compiled Pico job differs from the independent mode reference")


def check_admission(plan: dict[str, Any], snapshot: dict[str, Any], *, owned: bool = False) -> None:
    """Validate all resource shapes before access; never writes or acquires ownership."""
    validate_document(plan, "pico-campaign-plan.schema.json")
    validate_configuration(plan["configuration"])
    resources = {
        "capabilities": "capabilities",
        "config": "config-read",
        "status": "status",
        "gps": "gps",
        "si5351": "si5351",
        "hardware": "application/$defs/hardware_read",
    }
    if set(snapshot) != {
        *resources,
        "ntp-observations",
        "rf-calibration",
        "message-schedule",
        "target",
        "revision",
    }:
        raise PicoWireError("Pico snapshot resource inventory mismatch")
    for key, definition in resources.items():
        validate_contract(snapshot[key], "control", definition)
        if snapshot[key].get("contract") != "Wsprry-Control/1":
            raise PicoWireError("Snapshot resource omitted its current contract")
    for key in ("ntp-observations", "rf-calibration", "message-schedule"):
        validate_diagnostic(snapshot[key])
    expected = plan["configuration"]["pico"]
    target = snapshot["target"]
    validate_contract(target, "control", "application/$defs/target")
    if target["device_id"] != expected["device_id"]:
        raise PicoWireError("Snapshot belongs to another Pico")
    for name in (
        "gps",
        "si5351",
        "hardware",
        "ntp-observations",
        "rf-calibration",
        "message-schedule",
    ):
        if snapshot[name]["target"] != target:
            raise PicoWireError("Resource boot/device binding differs")
    status = snapshot["status"]
    if (
        status["job"]["boot_id"] != target["boot_id"]
        or status["standalone"]["boot_id"] != target["boot_id"]
    ):
        raise PicoWireError("Status boot differs from management session")
    if not owned and (
        status["job"]["owner_id"] is not None
        or status["job"]["state"] not in {"empty", "complete", "aborted", "missed"}
    ):
        raise PicoWireError("Pico is occupied; no takeover is permitted")
    if status["job"]["output_active"] or status["standalone"]["output_active"]:
        raise PicoWireError("Pico output inactivity is unverified")
    saved = snapshot["config"]["config"]
    if saved is None or saved["enabled"] or saved["schedules"]:
        raise PicoWireError("Standalone schedules must be explicitly disabled before a campaign")
    messages = snapshot["message-schedule"]
    if (
        (messages["saved"] is not None and messages["saved"]["enabled"])
        or messages["pause_pending"]
        or messages["next_start_utc_ns"] is not None
        or messages["state"] not in {"not_configured", "paused"}
        or (not owned and not messages["writable"])
    ):
        raise PicoWireError("Keyed-message schedule is enabled, queued, or unresolved")
    if snapshot["revision"] != expected["configuration_revision"]:
        raise PicoWireError("Pico configuration revision changed")
    hardware = snapshot["hardware"]
    if (
        hardware["pending_restart"]
        or not hardware["storage_healthy"]
        or hardware["application_error"] is not None
    ):
        raise PicoWireError("Hardware settings are not active/healthy")
    pins = hardware["active"]
    if pins is None or hardware["saved"] != pins or saved["pins"] != pins:
        raise PicoWireError("Saved and active Pico pin assignments differ")
    if expected["engine"] == "pio-dma-gp2" and (pins["engine"] != "direct" or pins["rf_gp"] != 2):
        raise PicoWireError("Selected GPIO engine requires its actual GP2 output")
    if expected["engine"] == "si5351" and (pins["engine"] != "i2c" or pins["i2c_pair"] != 1):
        raise PicoWireError("Selected Si5351 profile requires its supported I2C pair")
    if (
        hardware["execution_engine"] != expected["engine"]
        or status["standalone"]["engine"] != expected["engine"]
        or snapshot["capabilities"]["wtp"]["engine"] != expected["engine"]
    ):
        raise PicoWireError("Selected Pico engine is not active")
    if status["standalone"]["reboot_required"] or not status["standalone"]["storage_healthy"]:
        raise PicoWireError("Pico requires a verified setup before testing")
    if (
        status["standalone"]["enabled"]
        or status["standalone"]["schedules"]
        or (not owned and status["standalone"]["suspended"])
    ):
        raise PicoWireError("Standalone execution or a suspension/maintenance hold is unresolved")
    gps = snapshot["gps"]
    active = gps["boot_active"]
    enabled = active is not None and active["enabled"]
    if gps["pending_restart"] or not gps["storage_healthy"] or enabled != expected["gps_enabled"]:
        raise PicoWireError("GPS enablement does not match the selected row")
    utc = snapshot["ntp-observations"]
    if utc["active_source"] != expected["utc_source"] or utc["source_disagreement"]:
        raise PicoWireError("UTC source missing, mismatched, or conflicting")
    if (
        status["standalone"]["clock_state"] not in {"synchronized", "holdover"}
        or int(status["standalone"]["sync_age_ns"]) > 3_600_000_000_000
    ):
        raise PicoWireError("Pico UTC is unqualified or stale")
    if enabled:
        runtime = gps["runtime"]
        if (
            not active["pairing_qualified"]
            or runtime is None
            or not all(runtime[k] for k in ("serial_present", "pps_present", "time_valid"))
        ):
            raise PicoWireError("GPS serial/PPS pairing is not usable")
        if int(runtime["age_ns"]) > 3_000_000_000 or runtime["error"]:
            raise PicoWireError("GPS observation stale or refused")
        if (
            active["rx_gp"] == active["pps_gp"]
            or not 0
            <= active["maximum_message_delay_ms"] - active["minimum_message_delay_ms"]
            <= 999
        ):
            raise PicoWireError("GPS pin/pairing window allocation is invalid")
    automatic = expected["automatic_correction"]
    si = snapshot["si5351"]
    if expected["engine"] == "si5351":
        if (
            not si["physically_qualified"]
            or si["pending_restart"]
            or not si["storage_healthy"]
            or si["boot_active"] is None
        ):
            raise PicoWireError("Supported Si5351 profile is not active")
        if (
            si["runtime"] is None
            or si["runtime"]["transmit"] != "register_disabled"
            or si["runtime"]["communication"] != "register_control_verified"
            or si["runtime"]["inhibited"]
        ):
            raise PicoWireError("Si5351 register-disable observation unavailable")
        configured_auto = si["boot_active"]["automatic_calibration"]
        manual = si["boot_active"]["manual_correction_ppb"]
        if automatic and (
            si["boot_active"]["loopback_output"] != 2 or si["boot_active"]["loopback_input_gp"] != 7
        ):
            raise PicoWireError("Si5351 correction requires supported CLK2/input-only GP7 feedback")
    else:
        configured_auto = enabled and active["automatic_rf_calibration"]
        if snapshot["rf-calibration"]["saved"] is None:
            raise PicoWireError("Saved manual correction unavailable")
        manual = int(Decimal(snapshot["rf-calibration"]["saved"]["manual_ppm"]) * 1000)
    if configured_auto != automatic or manual != expected["manual_correction_ppb"]:
        raise PicoWireError("Automatic/manual correction differs from the selected subcase")
    selection = snapshot["rf-calibration"]["selection"]
    if (
        selection is None
        or selection["next_source"] != ("gps" if automatic else "manual")
        or selection["reason"] != ("gps_calibrated" if automatic else "manual_mode")
        or snapshot["rf-calibration"]["execution_engine"] != expected["engine"]
        or not snapshot["rf-calibration"]["storage_healthy"]
    ):
        raise PicoWireError("Product refused selected correction; no fallback permitted")
    if automatic:
        estimate = gps["runtime"][
            "synthesizer_calibration" if expected["engine"] == "si5351" else "timer_calibration"
        ]
        if (
            estimate is None
            or not estimate["fresh"]
            or estimate["generation"] != selection["next_generation"]
            or int(estimate["age_ns"]) > active["calibration_maximum_age_s"] * 1_000_000_000
        ):
            raise PicoWireError("Correction observation missing, stale, or generation-mismatched")
        product = estimate["oscillator_error_ppb"] * manual
        residual = (abs(product) + 500_000_000) // 1_000_000_000 * (-1 if product < 0 else 1)
        correction = estimate["oscillator_error_ppb"] + manual + residual
    else:
        correction = manual
    if selection["next_ppb"] != correction:
        raise PicoWireError("Effective correction contradicts its independent manual/GPS inputs")


def cw_analysis_plan(
    plan: dict[str, Any], mode: str, run_id: str, pre_quiet_s: float
) -> dict[str, Any]:
    config = plan["configuration"]
    p, receiver, target = (config[k] for k in ("protocol", "receiver", "pico"))
    tone = mode == "tone"
    result: dict[str, Any] = dict(
        schema_version=1,
        evidence_type="resolved_cw_mode_plan",
        run_id=run_id,
        mode=mode,
        backend="pico_si5351" if target["engine"] == "si5351" else "pico_gpio",
        hardware_profile="pico2w-preconfigured",
        band="selected-frequency",
        source=dict(
            target_kind="pico",
            source_revision=plan["source_revision"],
            compiler_sha256=plan["compiler"]["sha256"],
        ),
        transmitter=dict(
            host=target["serial"],
            output="CLK0" if target["engine"] == "si5351" else "GP2",
            model="Pico 2 W / RP2350",
            drive_value=0,
            drive_unit="observed-in-snapshot",
            clock_reference="observed-in-snapshot",
        ),
        receiver=dict(
            host=receiver["host"], driver=receiver["driver"], device_identity=receiver["serial"]
        ),
        rf_path=config["rf_path"],
        protocol=dict(
            definition="wsprrypi-dfcw@v1" if mode == "dfcw" else f"wspq-{mode}@v1",
            message=None if tone else p["message"],
            dot_seconds=None if tone else p["dot_seconds"],
            repetitions=None if tone else 1,
            primary_frequency_hz=p["frequency_hz"],
            secondary_frequency_hz=p["frequency_hz"] - p["shift_hz"]
            if mode in {"fskcw", "dfcw"}
            else None,
            pre_quiet_seconds=pre_quiet_s,
            post_quiet_seconds=2,
            intra_element_gap_units=None if tone else 0.333333 if mode == "dfcw" else 1,
            inter_character_gap_units=None if tone else 1 if mode == "dfcw" else 3,
            inter_word_gap_units=None if tone else 3 if mode == "dfcw" else 7,
            tone_cycles=3 if tone else None,
            tone_on_seconds=p["tone_seconds"] if tone else None,
            tone_off_seconds=2 if tone else None,
        ),
        capture_contract=dict(
            format="CF32LE",
            sample_rate_hz=receiver["sample_rate_hz"],
            center_frequency_hz=receiver["center_frequency_hz"],
            sample_count=receiver["sample_rate_hz"] * 4000,
            overflow_max=0,
            fixed_gain=True,
            agc_enabled=False,
            bias_tee_enabled=False,
            first_read_discarded=True,
        ),
        thresholds=dict(
            frequency_acquisition_half_width_hz=1000,
            frequency_tolerance_hz=2,
            spacing_tolerance_hz=2,
            minimum_contrast_db=10,
            timing_tolerance_s=0.15,
            maximum_transition_s=0.25,
            maximum_alignment_shift_s=0.75,
            maximum_clipping_fraction=0,
        ),
        resolved_utc=datetime.now(UTC).isoformat(),
    )
    events = generate_expected_events(result)
    duration = float(events[-1]["end_s"])
    result["capture_contract"]["sample_count"] = math.ceil(
        (duration + 1) * receiver["sample_rate_hz"]
    )
    validate_document(result, "cw-mode-plan.schema.json")
    return result


def plan_digest(plan: dict[str, Any]) -> str:
    return job_digest(plan)


def measurement_binding(plan: dict[str, Any]) -> dict[str, str]:
    """Native Pico context for common acquired carrier/audio/decode evidence."""
    return {
        "target_kind": "pico",
        "configuration_id": plan["configuration"]["configuration_id"],
        "plan_sha256": plan_digest(plan),
    }


def validate_measurement_binding(value: dict[str, Any], plan: dict[str, Any]) -> None:
    validate_document(value, "pico-measurement-binding.schema.json")
    if value != measurement_binding(plan):
        raise PicoWireError("Measurement context differs from the frozen Pico plan")


def check_prepared_correction(
    plan: dict[str, Any],
    prepared: dict[str, Any],
    gps_observations: list[dict[str, Any]],
) -> None:
    """Match the frozen correction to an exact retained current-contract input."""
    selection = prepared["rf-calibration"]["selection"]
    automatic = plan["configuration"]["pico"]["automatic_correction"]
    source = "gps" if automatic else "manual"
    if (
        selection is None
        or selection["prepared_source"] != source
        or selection["prepared_generation"] is None
    ):
        raise PicoWireError("Prepared correction provenance missing")
    manual = plan["configuration"]["pico"]["manual_correction_ppb"]
    expected = manual
    if automatic:
        key = (
            "synthesizer_calibration"
            if plan["configuration"]["pico"]["engine"] == "si5351"
            else "timer_calibration"
        )
        matched = []
        for observation in gps_observations:
            validate_contract(observation, "control", "gps")
            if observation["target"] != prepared["target"]:
                raise PicoWireError("Correction observation target differs from prepared job")
            runtime = observation["runtime"]
            estimate = runtime[key] if runtime is not None else None
            if estimate is not None and estimate["generation"] == selection["prepared_generation"]:
                if not estimate["fresh"]:
                    raise PicoWireError("Frozen correction observation is stale")
                matched.append(estimate["oscillator_error_ppb"])
        if not matched or len(set(matched)) != 1:
            raise PicoWireError(
                "Frozen correction generation lacks a consistent retained observation"
            )
        product = matched[0] * manual
        residual = (abs(product) + 500_000_000) // 1_000_000_000 * (-1 if product < 0 else 1)
        expected = matched[0] + manual + residual
    if selection["prepared_ppb"] != expected:
        raise PicoWireError("Frozen job correction differs from retained inputs")
