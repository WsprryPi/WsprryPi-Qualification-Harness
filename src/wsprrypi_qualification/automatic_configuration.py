"""Ephemeral complete-test configuration from discovered deployment facts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from wsprrypi_qualification.cw_defaults import CANONICAL_KEYED_TEST_MESSAGE
from wsprrypi_qualification.cw_reference import (
    generate_expected_events,
    required_keyed_capture_sample_count,
    validate_keyed_capture_margin,
)
from wsprrypi_qualification.offline import artifact, validate_document, write_json_new
from wsprrypi_qualification.real_session import helper_configuration_plan_sha256
from wsprrypi_qualification.receiver_calibration import disabled_binding


class AutomaticConfigurationError(RuntimeError):
    """Discovered facts cannot form a maintained complete-test deployment."""


def _record(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AutomaticConfigurationError(f"{label} identity is unavailable")
    record = cast(dict[str, Any], value)
    if (
        set(record) != {"path", "size_bytes", "sha256"}
        or not isinstance(record["path"], str)
        or not Path(record["path"]).is_absolute()
        or not isinstance(record["size_bytes"], int)
        or record["size_bytes"] < 0
        or not isinstance(record["sha256"], str)
        or len(record["sha256"]) != 64
    ):
        raise AutomaticConfigurationError(f"{label} identity is invalid")
    return record


def _executable(
    record: dict[str, Any], host: str, identity: str, plan_sha256: str = "0" * 64
) -> dict[str, Any]:
    return {
        "host": host,
        "path": record["path"],
        "sha256": record["sha256"],
        "version": "staged",
        "protocol_version": 1,
        "identity": identity,
        "plan_sha256": plan_sha256,
    }


def _profile_binding(record: dict[str, Any], name: str) -> dict[str, Any]:
    return {"id": f"ephemeral-{name}", "path": record["path"], "sha256": record["sha256"]}


def write_automatic_configuration(facts_path: Path, destination: Path) -> Path:
    """Write one ephemeral saved-format configuration using only discovered facts."""
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    if not isinstance(facts, dict):
        raise AutomaticConfigurationError("automatic deployment facts must be an object")
    destination.mkdir(parents=True, exist_ok=False)
    tx_host = str(facts["transmitter_host"])
    rx_host = str(facts["receiver_host"])
    sdr = cast(dict[str, str], facts["sdr"])
    artifacts = {
        name: _record(facts["artifacts"][name], name)
        for name in (
            "ssh",
            "ssh_keygen",
            "known_hosts",
            "tx_helper",
            "tx_helper_config",
            "tx_keyed_helper_config",
            "tx_sudo",
            "tx_systemctl",
            "tx_gpio",
            "tx_wsprrypi",
            "tx_git",
            "rx_helper",
            "rx_helper_config",
            "rx_systemctl",
            "rx_gpio",
            "capture_helper",
            "wsprd",
            "tone_ini_source",
            "tone_ini",
        )
    }
    revisions = facts["source"]
    if any(
        not isinstance(revisions.get(name), str) or len(revisions[name]) != 40
        for name in ("parent_revision", "submodule_revision")
    ):
        raise AutomaticConfigurationError("WsprryPi source identity is invalid")
    receiver = {
        "host": rx_host,
        "observed_local_hostname": str(facts["receiver_hostname"]),
        "driver": sdr["driver"],
        "serial": sdr["serial"],
        "channel": 0,
        "sample_format": "CF32",
        "sample_rate_hz": 250_000,
        "bandwidth_hz": 200_000,
        "center_frequency_hz": 14_097_100,
        "gain_db": 20,
        "agc": False,
        "bias_tee": False,
        "read_timeout_us": 500_000,
        "clipping_threshold": 0.999,
        "clock_source": "internal",
        "frequency_correction_ppm": 0.0,
        "driver_version": str(facts.get("sdr_driver_version", "runtime-discovered")),
        "firmware_version": None,
        "antenna_port": None,
        "tuner_path": None,
        "binding_extension": {},
    }
    rf_path = facts.get("rf_confirmation")
    if not isinstance(rf_path, dict) or rf_path != {
        "path_type": "conducted",
        "antenna_connected": False,
        "termination": "50 ohm direct SDR input through attenuator",
        "attenuation_db": 20,
        "filter": "none",
        "safe_input_basis": (
            "explicit --enable-rf confirmation of the documented conducted 20 dB default path"
        ),
        "authorization_scope": "single_run",
    }:
        raise AutomaticConfigurationError("the documented conducted RF path was not confirmed")
    templates = destination / "templates"
    templates.mkdir(parents=True, exist_ok=False)
    profile_seed = _profile_binding(artifacts["rx_helper_config"], "seed")
    tx_helper = {
        **_executable(artifacts["tx_helper"], tx_host, "complete-test-transmitter"),
        "config_path": artifacts["tx_helper_config"]["path"],
        "config_sha256": artifacts["tx_helper_config"]["sha256"],
        "privilege_wrapper_path": artifacts["tx_sudo"]["path"],
        "privilege_wrapper_sha256": artifacts["tx_sudo"]["sha256"],
        "bounded_tone_endpoint": {
            "host": "::1",
            "port": 31416,
            "path": "/",
            "maximum_frame_bytes": 16_384,
        },
        "wsprrypi_revision": revisions["parent_revision"],
    }
    rx_helper = {
        **_executable(artifacts["rx_helper"], rx_host, "complete-test-receiver"),
        "config_path": artifacts["rx_helper_config"]["path"],
        "config_sha256": artifacts["rx_helper_config"]["sha256"],
        "privilege_wrapper_path": None,
        "privilege_wrapper_sha256": None,
    }
    real: dict[str, Any] = {
        "schema_version": 1,
        "plan_type": "resolved_real_qualification_session",
        "execution_mode": "live",
        "run_id": "20260823T000000Z-complete-test-template",
        "test_id": "complete-test-template",
        "requested_profiles": {name: profile_seed for name in ("bench", "test", "receiver_run")},
        "resolved_profiles": {name: profile_seed for name in ("bench", "test", "receiver_run")},
        "host": tx_host,
        "transport": "ssh",
        "transport_identity": {
            "controller_hostname": facts["receiver_hostname"],
            "known_hosts_path": artifacts["known_hosts"]["path"],
            "known_hosts_sha256": artifacts["known_hosts"]["sha256"],
            "transmitter_host_key_sha256": facts["transmitter_host_key_sha256"],
            "ssh_keygen_path": artifacts["ssh_keygen"]["path"],
            "ssh_keygen_sha256": artifacts["ssh_keygen"]["sha256"],
        },
        "remote_helper": tx_helper,
        "receiver_helper": rx_helper,
        "capture_helper": _executable(artifacts["capture_helper"], rx_host, "wspq-capture-soapy"),
        "wsprd": _executable(artifacts["wsprd"], rx_host, "wsprd"),
        "wsprrypi": _executable(artifacts["tx_wsprrypi"], tx_host, "wsprrypi"),
        "source": {
            **revisions,
            "repository_path": str(facts["transmitter_source_path"]),
            "submodule_path": "src/WSPR-Transmitter",
            "git_path": artifacts["tx_git"]["path"],
            "git_sha256": artifacts["tx_git"]["sha256"],
        },
        "backend": "gpio",
        "output": "GPIO4",
        "backend_contract": {
            "backend": "gpio",
            "output": "GPIO4",
            "gpio_pin": 4,
            "drive_or_power_level": 0,
            "quiescence_provider_sha256": artifacts["tx_gpio"]["sha256"],
        },
        "services": {
            "transmitter": ["wsprrypi.service"],
            "receiver": ["ssh.service"],
            "receiver_required": ["ssh.service"],
        },
        "receiver": receiver,
        "receiver_calibration": disabled_binding(),
        "rf_path": rf_path,
        "frequency_hz": 14_097_100,
        "band": "20m",
        "identity": {"callsign": "Q0QQQ", "grid": "JJ00", "power_dbm": 0},
        "calibration": {"ppm": 0.0},
        "drive": {"value": 0, "unit": "power_level"},
        "mode": "TONE",
        "frame_count": 0,
        "random_offset_enabled": False,
        "carrier": {
            "rf_off_sample_count": 2_500_000,
            "rf_on_sample_count": 3_500_000,
            "offset_gate_hz": 100,
            "best_20hz_share_min": 0.5,
        },
        "coherent_capture": {
            "duration_s": 370,
            "sample_rate_hz": 250_000,
            "sample_count": 92_500_000,
            "margin_before_first_slot_s": 5,
        },
        "slots_utc": [
            "2026-08-23T00:00:00Z",
            "2026-08-23T00:02:00Z",
            "2026-08-23T00:04:00Z",
        ],
        "deadlines": {
            "helper_s": 5,
            "transmitter_s": 20,
            "receiver_s": 20,
            "cleanup_s": 10,
            "overall_s": 60,
        },
        "stopping_procedure": {
            "transmitter": "owned stop",
            "receiver": "exact count",
            "cleanup": "verified quiescence",
        },
        "raw_iq_retention": "retain",
        "capability_bindings": {
            "transmitter_ssh": artifacts["ssh"]["sha256"],
            "receiver_transport": artifacts["rx_helper"]["sha256"],
            "soapy": artifacts["capture_helper"]["sha256"],
            "wsprrypi": artifacts["tx_wsprrypi"]["sha256"],
            "transmitter_service": artifacts["tx_systemctl"]["sha256"],
            "receiver_service": artifacts["rx_systemctl"]["sha256"],
            "quiescence": artifacts["tx_gpio"]["sha256"],
            "decoder": artifacts["wsprd"]["sha256"],
        },
        "external_access_enabled": True,
        "rf_enabled": True,
        "session_kind": "cw_live_tone",
        "tone_schedule": {
            "cycles": 3,
            "off_seconds": 2,
            "on_seconds": 2,
            "maximum_rf_on_seconds": 6,
        },
        "cw_contract": {
            "plan": artifacts["rx_helper_config"],
            "expected_events": artifacts["rx_helper_config"],
            "analyzer_source_revision": revisions["parent_revision"],
        },
        "tone_server": {
            "protected_source_roots": [str(facts["transmitter_source_path"])],
            "working_directory": str(Path(artifacts["tone_ini"]["path"]).parent),
            "configuration_source": artifacts["tone_ini_source"],
            "configuration": artifacts["tone_ini"],
            "arguments": [
                artifacts["tx_wsprrypi"]["path"],
                "-i",
                artifacts["tone_ini"]["path"],
                "--socket-port",
                "31416",
                "--socket-loopback-only",
            ],
        },
    }
    # Retained plan evidence records the protocol-derived readiness envelope;
    # execution waits on the cadence itself rather than this informational field.
    real["tone_server"]["startup_seconds"] = real["tone_schedule"]["off_seconds"]
    helper_plan = helper_configuration_plan_sha256(real)
    for name in ("remote_helper", "receiver_helper", "capture_helper", "wsprd", "wsprrypi"):
        real[name]["plan_sha256"] = helper_plan

    application = {
        "schema_version": 1,
        "evidence_type": "application_plan",
        "plan_id": "complete-test-keyed-template",
        "identity": {
            "application": "wsprrypi",
            "executable": artifacts["tx_wsprrypi"]["path"],
            "source_revision": revisions["parent_revision"],
            "submodule_revision": revisions["submodule_revision"],
        },
        "backend": "gpio",
        "backend_contract": {
            "output": "GPIO4",
            "ppm": 0,
            "drive_or_power_level": 0,
            "gpio_pin": 4,
        },
        "protocol": "qrss",
        "protocol_contract": {
            "message": CANONICAL_KEYED_TEST_MESSAGE,
            "dot_seconds": 0.7,
            "primary_frequency_hz": 14_097_100,
            "secondary_frequency_hz": None,
        },
        "arguments": [
            artifacts["tx_wsprrypi"]["path"],
            "--backend",
            "gpio",
            "--transmit-gpio",
            "4",
            "--gpio-power-level",
            "0",
            "--gpio-manual-ppm",
            "0",
            "--no-offset",
            "--qrss-message",
            CANONICAL_KEYED_TEST_MESSAGE,
            "--qrss-frequency",
            "14097100",
            "--qrss-dot-seconds",
            "0.7",
        ],
        "self_terminating_request": True,
        "supervisor_required": True,
        "random_offset_enabled": False,
        "execution_authorized": False,
        "stopping_contract": "supervisor deadline and application termination",
        "cleanup_contract": "backend-specific disable and verified quiescence",
    }
    seed_plan = {
        "schema_version": 1,
        "evidence_type": "resolved_cw_mode_plan",
        "run_id": "20260823T000000Z-complete-test-keyed-seed",
        "mode": "qrss",
        "backend": "gpio",
        "hardware_profile": "complete-test-keyed-seed",
        "band": "20m",
        "source": revisions,
        "transmitter": {
            "host": tx_host,
            "output": "GPIO4",
            "model": "configured WsprryPi host",
            "drive_value": 0,
            "drive_unit": "power_level",
            "clock_reference": "configured transmitter clock",
        },
        "receiver": {
            "host": rx_host,
            "driver": sdr["driver"],
            "device_identity": facts["sdr_selector"],
        },
        "rf_path": {
            "attenuation_db": 20,
            "filter_state": "none",
            "termination": rf_path["termination"],
            "antenna_state": "disconnected",
            "safe_input_basis": rf_path["safe_input_basis"],
        },
        "protocol": {
            "definition": "wspq-qrss@v1",
            "message": CANONICAL_KEYED_TEST_MESSAGE,
            "dot_seconds": 0.7,
            "repetitions": 1,
            "primary_frequency_hz": 14_097_100,
            "secondary_frequency_hz": None,
            "pre_quiet_seconds": 2.0,
            "post_quiet_seconds": 2.0,
            "intra_element_gap_units": 1.0,
            "inter_character_gap_units": 3.0,
            "inter_word_gap_units": 7.0,
            "tone_cycles": None,
            "tone_on_seconds": None,
            "tone_off_seconds": None,
        },
        "capture_contract": {
            "format": "CF32LE",
            "sample_rate_hz": 250_000,
            "center_frequency_hz": 14_097_100,
            "sample_count": 150_000_000,
            "overflow_max": 0,
            "fixed_gain": True,
            "agc_enabled": False,
            "bias_tee_enabled": False,
            "first_read_discarded": True,
        },
        "thresholds": {
            "frequency_tolerance_hz": 2.0,
            "spacing_tolerance_hz": 2.0,
            "minimum_contrast_db": 10.0,
            "timing_tolerance_s": 0.15,
            "maximum_transition_s": 0.25,
            "maximum_alignment_shift_s": 0.75,
            "maximum_clipping_fraction": 0.01,
        },
        "resolved_utc": "2026-08-23T00:00:00Z",
    }
    seed_events = generate_expected_events(seed_plan)
    seed_plan["capture_contract"]["sample_count"] = required_keyed_capture_sample_count(seed_plan)
    validate_keyed_capture_margin(seed_plan)
    seed_plan_path = templates / "keyed-seed-plan.json"
    seed_events_path = templates / "keyed-seed-events.json"
    write_json_new(seed_plan_path, seed_plan, schema_name="cw-mode-plan.schema.json")
    write_json_new(
        seed_events_path,
        {
            "schema_version": 1,
            "evidence_type": "cw_expected_events",
            "run_id": seed_plan["run_id"],
            "mode": "qrss",
            "plan": artifact(seed_plan_path),
            "generator": {
                "origin": "harness_generated",
                "name": "wsprrypi-qualification-cw-reference",
                "version": "1",
                "source_revision": revisions["parent_revision"],
            },
            "protocol_definition": "wspq-qrss@v1",
            "events": seed_events,
        },
        schema_name="cw-expected-events.schema.json",
    )
    keyed = {
        "schema_version": 1,
        "evidence_type": "resolved_keyed_session_plan",
        "session_id": "complete-test-keyed-template",
        "mode": "QRSS",
        "transmitter": {
            "host": tx_host,
            "backend": "gpio",
            "output": "GPIO4",
            "frequency_hz": 14_097_100,
            "drive": 0,
            "executable": artifacts["tx_wsprrypi"],
            "protected_source_roots": [str(facts["transmitter_source_path"])],
            "git": artifacts["tx_git"],
            "runtime_working_directory": str(Path(artifacts["tx_wsprrypi"]["path"]).parent),
        },
        "receiver": {
            "host": rx_host,
            "driver": sdr["driver"],
            "device": sdr["serial"],
            "identity_sha256": "0" * 64,
            "sample_rate_hz": 250_000,
            "bandwidth_hz": 200_000,
            "center_frequency_hz": 14_097_100,
            "gain_db": 20,
            "channel": 0,
            "read_timeout_us": 100_000,
            "clipping_threshold": 0.999,
            "clock_source": "internal",
            "frequency_correction_ppm": 0.0,
            "driver_version": receiver["driver_version"],
            "firmware_version": None,
            "antenna_port": None,
            "tuner_path": None,
            "binding_extension": {},
        },
        "receiver_calibration": disabled_binding(),
        "rf_path": {
            "antenna_connected": False,
            "attenuation_db": 20,
            "termination": rf_path["termination"],
            "filter_state": "none",
            "routing": "user-confirmed conducted connection",
            "safe_input_basis": rf_path["safe_input_basis"],
        },
        "reference": {
            "plan": artifact(seed_plan_path),
            "expected_events": artifact(seed_events_path),
        },
        "application_plan": application,
        "target_revision": revisions["parent_revision"],
        "target_submodule_revision": revisions["submodule_revision"],
        "analyzer_revision": revisions["parent_revision"],
        "message_repetitions_per_transaction": 1,
        "capability_bindings": {
            "ssh": artifacts["ssh"],
            "known_hosts": artifacts["known_hosts"],
            "transmitter_helper": artifacts["tx_helper"],
            "transmitter_helper_config": artifacts["tx_keyed_helper_config"],
            "transmitter_helper_identity": "complete-test-transmitter",
            "transmitter_process_privilege_wrapper": artifacts["tx_sudo"],
            "receiver_helper": artifacts["rx_helper"],
            "receiver_helper_config": artifacts["rx_helper_config"],
            "receiver_helper_identity": "complete-test-receiver",
            "capture_helper": artifacts["capture_helper"],
            "services": ["tx:wsprrypi.service", "rx:ssh.service"],
            "required_receiver_services": ["rx:ssh.service"],
            "quiescence": "gpio",
        },
        "deadlines": {"transaction_s": 120, "cleanup_s": 10, "overall_s": 390},
        "stopping_procedure": "owned stop and verified GPIO quiescence",
        "transaction_count": 3,
    }
    real_path = templates / "real.json"
    keyed_path = templates / "keyed.json"
    write_json_new(real_path, real, schema_name="resolved-real-session-plan.schema.json")
    write_json_new(keyed_path, keyed, schema_name="resolved-keyed-session-plan.schema.json")
    configuration = {
        "schema_version": 1,
        "evidence_type": "complete_test_configuration",
        "campaign_id": "complete-five-mode",
        "transmitter_host": tx_host,
        "receiver_host": rx_host,
        "sdr_selector": facts["sdr_selector"],
        "receiver_delegation": facts["receiver_delegation"],
        "topology": "split_host_ssh",
        "production_templates": {
            "real_session": "templates/real.json",
            "keyed_session": "templates/keyed.json",
        },
        "ssh_executable": artifacts["ssh"]["path"],
        "work_directory": str(facts["work_directory"]),
        "output_parent": str(facts["output_parent"]),
    }
    validate_document(configuration, "complete-test-configuration.schema.json")
    configuration_path = destination / "complete-test.json"
    write_json_new(
        configuration_path,
        configuration,
        schema_name="complete-test-configuration.schema.json",
    )
    return configuration_path
