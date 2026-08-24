import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wsprrypi_qualification.cli import main
from wsprrypi_qualification.live_adapters import ProductionRealSessionAdapters
from wsprrypi_qualification.offline import OfflineAnalysisError
from wsprrypi_qualification.real_session import (
    RealQualificationSession,
    RealRuntimeAuthorization,
    RealSessionError,
    ResolvedRealSessionPlan,
    _validate_stage,
    helper_configuration_plan_sha256,
    helper_verification_contract,
    helper_verification_deadline,
    resolved_real_plan_sha256,
    validate_real_session_document,
    validate_real_session_plan,
)
from wsprrypi_qualification.receiver_calibration import disabled_binding

NOW = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)


def test_failed_cleanup_retains_actual_deadline_overrun():
    document = {
        "schema_version": 1,
        "evidence_type": "cleanup",
        "plan_sha256": "a" * 64,
        "outcome": "failed",
        "elapsed_s": 6.0,
        "deadline_s": 5.0,
        "details": {"actions_complete": False, "helper_absent": False},
    }
    _validate_stage(document, "cleanup", "a" * 64, "failed", 5.0)


def plan_document(*, execution_mode: str = "hardware_free_validation") -> dict:
    executable = {
        "host": "wspr5.local",
        "path": "/opt/Wsprry Pi/helper",
        "sha256": "a" * 64,
        "version": "1",
        "protocol_version": 1,
        "identity": "helper-1",
        "plan_sha256": "b" * 64,
    }
    document = {
        "schema_version": 1,
        "plan_type": "resolved_real_qualification_session",
        "execution_mode": execution_mode,
        "run_id": "20260812T200000Z-real-session",
        "test_id": "real-session",
        "requested_profiles": {
            name: {"id": name, "path": f"{name}.json", "sha256": "1" * 64}
            for name in ("bench", "test", "receiver_run")
        },
        "resolved_profiles": {
            name: {"id": f"resolved-{name}", "path": f"resolved-{name}.json", "sha256": "2" * 64}
            for name in ("bench", "test", "receiver_run")
        },
        "host": "wspr4.local",
        "transport": "ssh",
        "transport_identity": {
            "controller_hostname": "wspr5",
            "known_hosts_path": "/etc/wsprrypi-qualification/known_hosts",
            "known_hosts_sha256": "e" * 64,
            "transmitter_host_key_sha256": "SHA256:" + "A" * 43,
            "ssh_keygen_path": "/usr/bin/ssh-keygen",
            "ssh_keygen_sha256": "0" * 64,
        },
        "remote_helper": {
            **executable,
            "host": "wspr4.local",
            "config_path": "/etc/wsprrypi-qualification/helper.json",
            "config_sha256": "c" * 64,
            "privilege_wrapper_path": "/usr/bin/sudo",
            "privilege_wrapper_sha256": "a" * 64,
        },
        "receiver_helper": {
            **executable,
            "identity": "receiver-helper",
            "config_path": "/etc/wsprrypi-qualification/helper.json",
            "config_sha256": "d" * 64,
            "privilege_wrapper_path": None,
            "privilege_wrapper_sha256": None,
        },
        "capture_helper": {**executable, "identity": "soapy-capture", "sha256": "5" * 64},
        "wsprd": {**executable, "identity": "wsprd", "sha256": "6" * 64},
        "wsprrypi": {**executable, "host": "wspr4.local", "identity": "wsprrypi"},
        "source": {
            "parent_revision": "1" * 40,
            "submodule_revision": "2" * 40,
            "repository_path": "/home/pi/WsprryPi",
            "submodule_path": "WiringPi",
            "git_path": "/usr/bin/git",
            "git_sha256": "f" * 64,
        },
        "backend": "si5351",
        "output": "CLK0",
        "backend_contract": {
            "backend": "si5351",
            "output": "CLK0",
            "i2c_bus": 1,
            "i2c_address": "0x60",
            "reference_frequency_hz": 25000000,
            "drive_or_power_level": 2,
            "quiescence_provider_sha256": "3" * 64,
        },
        "services": {"transmitter": ["wsprrypi"], "receiver": ["SoapySDRServer"]},
        "receiver": {
            "host": "wspr5.local",
            "observed_local_hostname": "wspr5",
            "driver": "sdrplay",
            "serial": "SERIAL",
            "channel": 0,
            "sample_format": "CF32",
            "sample_rate_hz": 250000,
            "bandwidth_hz": 200000,
            "center_frequency_hz": 1813100,
            "gain_db": 10,
            "agc": False,
            "bias_tee": False,
            "read_timeout_us": 500000,
            "clipping_threshold": 0.999,
            "clock_source": "internal",
            "frequency_correction_ppm": 0.0,
            "driver_version": "test-driver",
            "firmware_version": None,
            "antenna_port": "A",
            "tuner_path": None,
            "binding_extension": {},
        },
        "receiver_calibration": disabled_binding(),
        "rf_path": {
            "path_type": "radiated",
            "antenna_connected": True,
            "termination": None,
            "attenuation_db": None,
            "filter": None,
            "safe_input_basis": "receiver-only path reviewed for this run",
            "authorization_scope": "single_run",
        },
        "frequency_hz": 1838100,
        "band": "160m",
        "identity": {"callsign": "Q0QQQ", "grid": "JJ00", "power_dbm": 0},
        "calibration": {"ppm": 2.3536},
        "drive": {"value": 2, "unit": "mA"},
        "mode": "WSPR",
        "frame_count": 3,
        "random_offset_enabled": False,
        "carrier": {
            "rf_off_sample_count": 2500000,
            "rf_on_sample_count": 2500000,
            "offset_gate_hz": 100,
            "best_20hz_share_min": 0.5,
        },
        "coherent_capture": {
            "duration_s": 370,
            "sample_rate_hz": 250000,
            "sample_count": 92500000,
            "margin_before_first_slot_s": 5,
        },
        "slots_utc": [
            "2026-08-12T20:00:00Z",
            "2026-08-12T20:02:00Z",
            "2026-08-12T20:04:00Z",
        ],
        "deadlines": {
            "helper_s": 5,
            "transmitter_s": 380,
            "receiver_s": 390,
            "cleanup_s": 10,
            "overall_s": 500,
        },
        "stopping_procedure": {
            "transmitter": "owned stop",
            "receiver": "exact count",
            "cleanup": "verified quiescence",
        },
        "raw_iq_retention": "retain",
        "capability_bindings": {
            "transmitter_ssh": "4" * 64,
            "receiver_transport": "9" * 64,
            "soapy": "5" * 64,
            "wsprrypi": "a" * 64,
            "transmitter_service": "7" * 64,
            "receiver_service": "8" * 64,
            "quiescence": "3" * 64,
            "decoder": "6" * 64,
        },
        "external_access_enabled": True,
        "rf_enabled": True,
    }
    digest = helper_configuration_plan_sha256(document)
    for field in ("remote_helper", "receiver_helper", "capture_helper", "wsprd", "wsprrypi"):
        document[field]["plan_sha256"] = digest
    return document


def tone_plan_document(
    *, execution_mode: str = "hardware_free_validation", gpio: bool = False
) -> dict:
    document = plan_document(execution_mode=execution_mode)
    if gpio:
        document["backend"] = "gpio"
        document["output"] = "GPIO4"
        document["backend_contract"] = {
            "backend": "gpio",
            "output": "GPIO4",
            "gpio_pin": 4,
            "drive_or_power_level": 2,
            "quiescence_provider_sha256": "3" * 64,
        }
    tone_arguments = [
        document["wsprrypi"]["path"],
        "-i",
        "/carrier-session/wsprrypi-tone.ini",
        "--socket-port",
        "31416",
        "--socket-loopback-only",
    ]
    if gpio:
        tone_arguments = [
            document["wsprrypi"]["path"],
            "--no-system-clock-frequency-estimate",
            *tone_arguments[1:],
            "--gpio-manual-ppm",
            "2.3536",
        ]
    document.update(
        {
            "session_kind": "cw_live_tone",
            "mode": "TONE",
            "frame_count": 0,
            "tone_schedule": {
                "cycles": 3,
                "off_seconds": 2,
                "on_seconds": 2,
                "maximum_rf_on_seconds": 6,
            },
            "cw_contract": {
                "plan": {
                    "path": "/carrier-session/tone-plan.json",
                    "size_bytes": 1,
                    "sha256": "a" * 64,
                },
                "expected_events": {
                    "path": "/carrier-session/tone-events.json",
                    "size_bytes": 1,
                    "sha256": "b" * 64,
                },
                "analyzer_source_revision": "3" * 40,
            },
            "tone_server": {
                "configuration": {
                    "path": "/carrier-session/wsprrypi-tone.ini",
                    "size_bytes": 1,
                    "sha256": "c" * 64,
                },
                "arguments": tone_arguments,
                "startup_seconds": 0.25,
            },
        }
    )
    document["carrier"]["rf_on_sample_count"] = 3_500_000
    document["remote_helper"].update(
        {
            "bounded_tone_endpoint": {
                "host": "::1",
                "port": 31416,
                "path": "/",
                "maximum_frame_bytes": 16384,
            },
            "wsprrypi_revision": document["source"]["parent_revision"],
        }
    )
    document["deadlines"] = {
        "helper_s": 5,
        "transmitter_s": 20,
        "receiver_s": 20,
        "cleanup_s": 10,
        "overall_s": 60,
    }
    digest = helper_configuration_plan_sha256(document)
    for field in ("remote_helper", "receiver_helper", "capture_helper", "wsprd", "wsprrypi"):
        document[field]["plan_sha256"] = digest
    return document


def test_tone_plan_binds_loopback_endpoint_and_wsprrypi_revision() -> None:
    document = tone_plan_document()
    validate_real_session_plan(document)
    document["remote_helper"]["wsprrypi_revision"] = "f" * 40
    with pytest.raises(RealSessionError, match="revision differs"):
        validate_real_session_plan(document)


def test_tone_plan_rejects_missing_or_mismatched_manual_ppm_before_adapters() -> None:
    for mutation in ("missing", "mismatched", "estimate"):
        document = tone_plan_document(gpio=True)
        arguments = document["tone_server"]["arguments"]
        if mutation == "missing":
            position = arguments.index("--gpio-manual-ppm")
            del arguments[position : position + 2]
        elif mutation == "mismatched":
            arguments[arguments.index("--gpio-manual-ppm") + 1] = "9"
        else:
            arguments[arguments.index("--no-system-clock-frequency-estimate")] = "-n"
        with pytest.raises(RealSessionError, match="server arguments"):
            validate_real_session_plan(document)


def test_tone_helper_bindings_change_every_plan_digest() -> None:
    before = tone_plan_document()
    helper_digest = helper_configuration_plan_sha256(before)
    operator_digest = resolved_real_plan_sha256(before)
    before["remote_helper"]["bounded_tone_endpoint"]["port"] += 1
    assert helper_configuration_plan_sha256(before) != helper_digest
    assert resolved_real_plan_sha256(before) != operator_digest


def test_helper_digest_breaks_cycle_but_operator_digest_binds_config_hashes() -> None:
    document = plan_document()
    helper_digest = helper_configuration_plan_sha256(document)
    operator_digest = resolved_real_plan_sha256(document)
    document["remote_helper"]["config_sha256"] = "3" * 64
    document["receiver_helper"]["config_sha256"] = "4" * 64
    assert helper_configuration_plan_sha256(document) == helper_digest
    assert resolved_real_plan_sha256(document) != operator_digest


class FakeAdapters:
    execution_mode = "hardware_free_validation"

    def __init__(self, *, carrier="passed", decode="passed", fail=None):
        self.calls = []
        self.carrier, self.decode, self.fail = carrier, decode, fail

    def close(self):
        self.calls.append("close")
        return True

    def _call(self, name, outcome="passed", evidence_type=None, details=None, deadline_s=5):
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError(f"injected {name}")
        return {
            "schema_version": 1,
            "evidence_type": evidence_type or name,
            "plan_sha256": self.digest,
            "outcome": outcome,
            "elapsed_s": 0.01,
            "deadline_s": deadline_s,
            "details": details or {},
        }

    digest = ""

    def discover_capabilities(self, plan):
        self.digest = resolved_real_plan_sha256(plan)
        return self._call(
            "discover",
            evidence_type="capabilities",
            details={"bindings": plan["capability_bindings"]},
        )

    def verify_helper(self, plan):
        return self._call(
            "helper",
            evidence_type="helper",
            deadline_s=helper_verification_deadline(plan),
            details={
                side: {
                    key: plan[field][key]
                    for key in (
                        "host",
                        "path",
                        "sha256",
                        "identity",
                        "config_path",
                        "config_sha256",
                    )
                }
                for side, field in (
                    ("transmitter", "remote_helper"),
                    ("receiver", "receiver_helper"),
                )
            }
            | {"verification_contract": helper_verification_contract(plan)},
        )

    def inspect_services_and_ownership(self, plan):
        return self._call(
            "ownership",
            evidence_type="ownership",
            details={
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
            },
        )

    def verify_rf_idle(self, plan):
        return self._call(
            "idle",
            evidence_type="rf_idle",
            details={"backend": plan["backend"], "output": plan["output"], "verified": True},
        )

    def install_cleanup(self, plan):
        return self._call(
            "install_cleanup",
            evidence_type="cleanup_registration",
            details={"installed": True, "deadline_s": plan["deadlines"]["cleanup_s"]},
            deadline_s=plan["deadlines"]["cleanup_s"],
        )

    def capture_rf_off(self, plan):
        document = self._capture(plan, "rf_off", plan["carrier"]["rf_off_sample_count"])
        if self.fail == "rf_off_blocked":
            document["outcome"] = "blocked"
        return document

    def transmit_carrier_and_capture_rf_on(self, plan, authorization):
        assert authorization.rf_authorized
        return self._capture(plan, "rf_on", plan["carrier"]["rf_on_sample_count"])

    def analyze_carrier(self, plan, rf_off, rf_on):
        self.calls.append("carrier_analysis")
        offset = 0 if self.carrier != "failed" else 600
        return self._call(
            "carrier_analysis",
            "completed",
            "carrier_analysis",
            {
                "gate_outcome": self.carrier,
                "requested_frequency_hz": plan["frequency_hz"],
                "strongest_frequency_hz": plan["frequency_hz"] + offset,
                "offset_hz": offset,
                "best_20hz_fraction": 0.9 if self.carrier != "failed" else 0.4,
                "strongest_contrast_db": 20.0,
                "carrier_gate_policy": "target_window_relative_carrier_acquisition_v2",
                "relative_acquisition_offset_gate_hz": 500.0,
                "relative_acquisition_contrast_gate_db": 10.0,
                "mode_gate": "not_applicable",
            },
            deadline_s=plan["deadlines"]["overall_s"],
        )

    def transmit_frames_and_capture(self, plan, authorization):
        return self._capture(plan, "coherent", plan["coherent_capture"]["sample_count"])

    def create_wavs_and_decode(self, plan, coherent_capture):
        self.calls.append("decode")
        identity = plan["identity"]
        return self._call(
            "decode",
            "completed",
            "decode_summary",
            {
                "gate_outcome": self.decode,
                "slots": [
                    {
                        "slot_utc": slot,
                        **identity,
                        "matched": self.decode == "passed",
                        "wsprd_log_sha256": "9" * 64,
                    }
                    for slot in plan["slots_utc"]
                ],
            },
            deadline_s=plan["deadlines"]["overall_s"],
        )

    def cleanup(self, plan):
        outcome = "failed" if self.fail == "cleanup_outcome" else "verified"
        return self._call(
            "cleanup",
            outcome,
            "cleanup",
            {"actions_complete": outcome == "verified", "helper_absent": outcome == "verified"},
            deadline_s=plan["deadlines"]["cleanup_s"],
        )

    def verify_quiescence(self, plan):
        outcome = "failed" if self.fail == "quiescence_outcome" else "verified"
        return self._call(
            "quiescence",
            outcome,
            "quiescence",
            {
                "backend": plan["backend"],
                "output": plan["output"],
                "verified": outcome == "verified",
            },
            deadline_s=plan["deadlines"]["cleanup_s"],
        )

    def _capture(self, plan, kind, count):
        receiver = plan["receiver"]
        return self._call(
            kind,
            "completed",
            "capture",
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
                "artifact_sha256": "a" * 64,
            },
            deadline_s=plan["deadlines"]["receiver_s"],
        )


def authorizations(plan):
    return (
        RealRuntimeAuthorization("external_access", "operator", NOW, plan.sha256, True),
        RealRuntimeAuthorization("rf", "operator", NOW, plan.sha256, True),
    )


def run(tmp_path: Path, adapters: FakeAdapters):
    plan = ResolvedRealSessionPlan(plan_document())
    external, rf = authorizations(plan)
    return RealQualificationSession(plan, adapters, now=NOW).run(external, rf, tmp_path)


def test_run_chronology_uses_current_clock_not_confirmation_time(tmp_path: Path):
    plan = ResolvedRealSessionPlan(plan_document())
    external, rf = authorizations(plan)
    tick = 0

    def clock() -> datetime:
        nonlocal tick
        tick += 1
        return NOW.replace(second=tick)

    document = RealQualificationSession(plan, FakeAdapters(), now=NOW, clock=clock).run(
        external, rf, tmp_path
    )
    timestamps = [item["timestamp_utc"] for item in document["events"]]
    assert len(set(timestamps)) == len(timestamps)
    result = json.loads(
        (tmp_path / plan.validated()["run_id"] / "result.json").read_text(encoding="utf-8")
    )
    assert result["completed_utc"] > result["started_utc"]


def test_hardware_free_success_remains_inconclusive_and_is_packaged(tmp_path: Path):
    adapters = FakeAdapters()
    document = run(tmp_path, adapters)
    assert document["final_status"] == "inconclusive"
    assert document["carrier_gate"] == document["decode_gate"] == "passed"
    bundle = tmp_path / plan_document()["run_id"]
    assert (bundle / "result.json").is_file()
    assert (bundle / "SHA256SUMS").is_file()
    assert adapters.calls.index("install_cleanup") < adapters.calls.index("rf_on")


def test_helper_stage_accepts_sequential_aggregate_with_individual_bound(tmp_path: Path):
    class SixSecondAggregate(FakeAdapters):
        def verify_helper(self, plan):
            document = super().verify_helper(plan)
            document["elapsed_s"] = 6
            return document

    assert run(tmp_path, SixSecondAggregate())["final_status"] == "inconclusive"


def test_legacy_helper_stage_retains_original_deadline_semantics(tmp_path: Path):
    document = run(tmp_path, FakeAdapters())
    helper = document["evidence"]["helper"]
    del helper["details"]["verification_contract"]
    helper["deadline_s"] = document["resolved_plan"]["deadlines"]["helper_s"]
    validate_real_session_document(document)


def test_helper_stage_rejects_aggregate_overrun(tmp_path: Path):
    class AggregateOverrun(FakeAdapters):
        def verify_helper(self, plan):
            document = super().verify_helper(plan)
            document["elapsed_s"] = helper_verification_deadline(plan) + 0.01
            return document

    document = run(tmp_path, AggregateOverrun())
    assert document["final_status"] == "preflight_failed"
    assert "recorded hard deadline" in document["failure_causes"][0]
    assert document["cleanup"] is None


def test_helper_preflight_failure_closes_partial_live_sessions(tmp_path: Path, monkeypatch):
    observed = FakeAdapters(fail="helper")
    adapters = object.__new__(ProductionRealSessionAdapters)
    monkeypatch.setattr(adapters, "begin_session", lambda plan: None)
    monkeypatch.setattr(adapters, "discover_capabilities", observed.discover_capabilities)
    monkeypatch.setattr(adapters, "verify_helper", observed.verify_helper)
    monkeypatch.setattr(adapters, "close", observed.close)
    monkeypatch.setattr(adapters, "publish_artifacts", lambda root: [])
    monkeypatch.setattr(adapters, "validate_published_artifacts", lambda root: None)
    plan = ResolvedRealSessionPlan(plan_document(execution_mode="live"))
    external, rf = authorizations(plan)
    document = RealQualificationSession(plan, adapters, now=NOW).run(external, rf, tmp_path)
    assert document["final_status"] == "preflight_failed"
    assert observed.calls[-1] == "close"


def test_overall_deadline_must_contain_aggregate_helper_verification() -> None:
    document = plan_document()
    document["deadlines"]["overall_s"] = helper_verification_deadline(document)
    with pytest.raises(RealSessionError, match="overall deadline"):
        ResolvedRealSessionPlan(document).validated()


def test_failed_carrier_suppresses_frames(tmp_path: Path):
    adapters = FakeAdapters(carrier="failed")
    document = run(tmp_path, adapters)
    assert document["final_status"] == "unqualified_carrier"
    assert "frames" not in adapters.calls and "decode" not in adapters.calls


def test_carrier_only_session_never_advances_to_wspr_frames(tmp_path: Path):
    plan = ResolvedRealSessionPlan(tone_plan_document())
    adapters = FakeAdapters()
    external, rf = authorizations(plan)
    document = RealQualificationSession(plan, adapters, now=NOW).run(external, rf, tmp_path)
    assert document["final_status"] == "inconclusive"
    assert document["carrier_gate"] == "passed"
    assert document["decode_gate"] == "not_run"
    assert "frames" not in adapters.calls and "decode" not in adapters.calls


def test_carrier_only_failed_carrier_accepts_not_applicable_mode_gate(tmp_path: Path):
    class FailedToneCarrier(FakeAdapters):
        def analyze_carrier(self, plan, rf_off, rf_on):
            document = super().analyze_carrier(plan, rf_off, rf_on)
            document["details"]["mode_gate"] = "not_applicable"
            return document

    plan = ResolvedRealSessionPlan(tone_plan_document())
    adapters = FailedToneCarrier(carrier="failed")
    external, rf = authorizations(plan)
    document = RealQualificationSession(plan, adapters, now=NOW).run(external, rf, tmp_path)
    assert document["final_status"] == "unqualified_carrier"
    assert document["carrier_gate"] == "failed"
    assert document["decode_gate"] == "not_run"
    assert "frames" not in adapters.calls and "decode" not in adapters.calls


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda plan: plan.update({"frame_count": 3}), "zero frames"),
        (
            lambda plan: plan["tone_schedule"].update({"maximum_rf_on_seconds": 7}),
            "RF-on bound",
        ),
        (
            lambda plan: plan["carrier"].update({"rf_on_sample_count": 3_499_999}),
            "capture count",
        ),
        (lambda plan: plan.pop("cw_contract"), "analyzer contract"),
    ],
)
def test_carrier_only_plan_rejects_contradictions(mutation, message):
    document = tone_plan_document()
    mutation(document)
    with pytest.raises(RealSessionError, match=message):
        ResolvedRealSessionPlan(document).validated()


def test_required_receiver_services_must_be_in_receiver_allowlist():
    document = plan_document()
    document["services"]["receiver_required"] = ["sdrplay.service"]

    with pytest.raises(RealSessionError, match="receiver service allowlist"):
        ResolvedRealSessionPlan(document).validated()


def test_carrier_gate_is_recomputed_from_metrics(tmp_path: Path):
    class ForgedCarrier(FakeAdapters):
        def analyze_carrier(self, plan, rf_off, rf_on):
            return self._call(
                "carrier_analysis",
                "completed",
                "carrier_analysis",
                {
                    "gate_outcome": "passed",
                    "requested_frequency_hz": plan["frequency_hz"],
                    "strongest_frequency_hz": plan["frequency_hz"] + 10000,
                    "offset_hz": 10000,
                    "best_20hz_fraction": 0.01,
                    "strongest_contrast_db": 1.0,
                    "carrier_gate_policy": "target_window_relative_carrier_acquisition_v2",
                    "relative_acquisition_offset_gate_hz": 500.0,
                    "relative_acquisition_contrast_gate_db": 10.0,
                },
            )

    document = run(tmp_path, ForgedCarrier())
    assert document["final_status"] == "aborted"
    assert document["carrier_gate"] == "not_run"


def test_blocked_decode_is_fixture_blocked(tmp_path: Path):
    document = run(tmp_path, FakeAdapters(decode="blocked"))
    assert document["final_status"] == "fixture_blocked"


def test_live_plan_refuses_hardware_free_adapter(tmp_path: Path):
    plan = ResolvedRealSessionPlan(plan_document(execution_mode="live"))
    with pytest.raises(RealSessionError, match="adapter execution mode"):
        RealQualificationSession(plan, FakeAdapters(), now=NOW).run(None, None, tmp_path)


def test_live_plan_refuses_self_declared_fake_adapter(tmp_path: Path):
    class SelfDeclaredLive(FakeAdapters):
        execution_mode = "live"

    plan = ResolvedRealSessionPlan(plan_document(execution_mode="live"))
    with pytest.raises(RealSessionError, match="sealed production adapter"):
        RealQualificationSession(plan, SelfDeclaredLive(), now=NOW).run(None, None, tmp_path)


def test_live_cli_is_unavailable_without_every_enable_flag(tmp_path: Path):
    path = tmp_path / "live plan.json"
    path.write_text(json.dumps(plan_document(execution_mode="live")), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(
            [
                "run-live-session",
                str(path),
                str(tmp_path / "runs"),
                "--work-directory",
                str(tmp_path / "work"),
                "--ssh",
                "/usr/bin/ssh",
                "--operator",
                "operator",
            ]
        )


def test_stage_cannot_select_a_deadline_larger_than_resolved_plan(tmp_path: Path):
    class LooseHelper(FakeAdapters):
        def verify_helper(self, plan):
            document = super().verify_helper(plan)
            document["elapsed_s"] = 6
            document["deadline_s"] = 500
            return document

    document = run(tmp_path, LooseHelper())
    assert document["final_status"] == "preflight_failed"
    assert "deadline differs" in document["failure_causes"][0]


@pytest.mark.parametrize(
    ("field", "value"),
    [("deadline_s", 500), ("plan_sha256", "f" * 64), ("evidence_type", "ownership")],
)
def test_retained_stage_evidence_is_rebound_to_plan(tmp_path: Path, field: str, value: object):
    document = run(tmp_path, FakeAdapters())
    document["evidence"]["helper"][field] = value
    with pytest.raises((RealSessionError, OfflineAnalysisError)):
        validate_real_session_document(document)


@pytest.mark.parametrize("key", ["helper", "carrier", "decode"])
def test_stage_event_requires_corresponding_retained_evidence(tmp_path: Path, key: str):
    document = run(tmp_path, FakeAdapters())
    del document["evidence"][key]
    with pytest.raises(RealSessionError, match="presence disagree"):
        validate_real_session_document(document)


def test_event_outcome_must_match_retained_stage(tmp_path: Path):
    document = run(tmp_path, FakeAdapters())
    next(item for item in document["events"] if item["phase"] == "helper_verified")["outcome"] = (
        "failed"
    )
    with pytest.raises(RealSessionError, match="outcome differs"):
        validate_real_session_document(document)


def test_fixture_blocked_status_requires_blocked_evidence(tmp_path: Path):
    document = run(tmp_path, FakeAdapters())
    document["final_status"] = "fixture_blocked"
    with pytest.raises(RealSessionError, match=r"lacks blocked|passed gates"):
        validate_real_session_document(document)


def test_cleanup_event_outcome_must_match_retained_evidence(tmp_path: Path):
    document = run(tmp_path, FakeAdapters())
    next(item for item in document["events"] if item["phase"] == "cleanup")["outcome"] = "failed"
    with pytest.raises(RealSessionError, match="outcome differs"):
        validate_real_session_document(document)


def test_completed_gates_cannot_be_relabelled_aborted(tmp_path: Path):
    document = run(tmp_path, FakeAdapters())
    document["final_status"] = "aborted"
    document["failure_causes"] = ["invented"]
    with pytest.raises(RealSessionError, match=r"aborted status|passed gates"):
        validate_real_session_document(document)


@pytest.mark.parametrize("carrier", ["failed"])
def test_failed_carrier_cannot_be_relabelled_aborted(tmp_path: Path, carrier: str):
    document = run(tmp_path, FakeAdapters(carrier=carrier))
    document["final_status"] = "aborted"
    document["failure_causes"] = ["invented"]
    with pytest.raises(RealSessionError, match="unqualified-carrier"):
        validate_real_session_document(document)


def test_failed_decode_cannot_be_relabelled_aborted(tmp_path: Path):
    document = run(tmp_path, FakeAdapters(decode="failed"))
    document["final_status"] = "aborted"
    document["failure_causes"] = ["invented"]
    with pytest.raises(RealSessionError, match="unqualified-decode"):
        validate_real_session_document(document)


def test_typed_fixture_blockage_cannot_be_relabelled_aborted(tmp_path: Path):
    document = run(tmp_path, FakeAdapters(fail="rf_off_blocked"))
    document["final_status"] = "aborted"
    with pytest.raises(RealSessionError, match="typed fixture"):
        validate_real_session_document(document)


@pytest.mark.parametrize("kind", ["external_access", "rf"])
def test_authorization_separation_refuses_before_adapter_call(tmp_path: Path, kind: str):
    plan = ResolvedRealSessionPlan(plan_document())
    external, rf = authorizations(plan)
    if kind == "external_access":
        external = RealRuntimeAuthorization("rf", "operator", NOW, plan.sha256, True)
    else:
        rf = RealRuntimeAuthorization("external_access", "operator", NOW, plan.sha256, True)
    adapters = FakeAdapters()
    document = RealQualificationSession(plan, adapters, now=NOW).run(external, rf, tmp_path)
    assert document["final_status"] == "preflight_failed"
    assert adapters.calls == []


@pytest.mark.parametrize("failure", ["cleanup_outcome", "quiescence_outcome"])
def test_cleanup_failure_has_precedence(tmp_path: Path, failure: str):
    document = run(tmp_path, FakeAdapters(fail=failure))
    assert document["final_status"] == "cleanup_failed"


def test_single_use_and_output_collision(tmp_path: Path):
    plan = ResolvedRealSessionPlan(plan_document())
    external, rf = authorizations(plan)
    session = RealQualificationSession(plan, FakeAdapters(), now=NOW)
    session.run(external, rf, tmp_path)
    with pytest.raises(RealSessionError, match="single-use"):
        session.run(external, rf, tmp_path)


def test_tampered_session_cleanup_is_rejected(tmp_path: Path):
    document = run(tmp_path, FakeAdapters())
    document["cleanup"]["outcome"] = "failed"
    with pytest.raises(RealSessionError, match=r"outcome differs|precedence"):
        validate_real_session_document(document)


def test_plan_only_cli_makes_no_external_calls(tmp_path: Path, capsys):
    path = tmp_path / "plan with spaces.json"
    path.write_text(json.dumps(plan_document()), encoding="utf-8")
    assert main(["real-session", str(path), "--plan-only"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["external_calls"] == 0
    assert output["plan_only"] is True
    assert main(["real-session", str(path), "--enable-rf"]) == 2


@pytest.mark.parametrize("run_id", ["../escape", "/private/tmp/escape"])
def test_run_id_cannot_escape_evidence_parent(run_id: str):
    document = plan_document()
    document["run_id"] = run_id
    digest = helper_configuration_plan_sha256(document)
    document["remote_helper"]["plan_sha256"] = digest
    document["receiver_helper"]["plan_sha256"] = digest
    document["capture_helper"]["plan_sha256"] = digest
    document["wsprd"]["plan_sha256"] = digest
    document["wsprrypi"]["plan_sha256"] = digest
    with pytest.raises(OfflineAnalysisError):
        ResolvedRealSessionPlan(document).validated()


def test_preserved_wspr_sample_contract_is_exact():
    document = plan_document()
    document["coherent_capture"] = {
        "duration_s": 370,
        "sample_rate_hz": 1000,
        "sample_count": 370000,
        "margin_before_first_slot_s": 5,
    }
    digest = helper_configuration_plan_sha256(document)
    document["remote_helper"]["plan_sha256"] = digest
    document["receiver_helper"]["plan_sha256"] = digest
    document["capture_helper"]["plan_sha256"] = digest
    document["wsprd"]["plan_sha256"] = digest
    document["wsprrypi"]["plan_sha256"] = digest
    with pytest.raises(OfflineAnalysisError):
        ResolvedRealSessionPlan(document).validated()


def test_split_transmitter_and_receiver_hosts_are_explicitly_supported():
    document = plan_document()
    assert document["host"] == "wspr4.local"
    assert document["receiver"]["host"] == "wspr5.local"
    ResolvedRealSessionPlan(document).validated()


def test_receiver_tools_must_bind_to_receiver_host():
    document = plan_document()
    document["capture_helper"]["host"] = "wspr4.local"
    with pytest.raises(RealSessionError, match="receiver tool host"):
        ResolvedRealSessionPlan(document).validated()


def test_one_decoder_invocation_cannot_pass(tmp_path: Path):
    class BadDecode(FakeAdapters):
        def create_wavs_and_decode(self, plan, coherent_capture):
            return {
                "schema_version": 1,
                "evidence_type": "decode_summary",
                "plan_sha256": self.digest,
                "outcome": "completed",
                "elapsed_s": 0.01,
                "deadline_s": 500,
                "details": {"gate_outcome": "passed", "slots": []},
            }

    document = run(tmp_path, BadDecode())
    assert document["final_status"] == "aborted"
    assert document["decode_gate"] == "not_run"


def test_partial_cleanup_install_failure_still_runs_cleanup_and_quiescence(tmp_path: Path):
    adapters = FakeAdapters(fail="install_cleanup")
    document = run(tmp_path, adapters)
    assert document["final_status"] == "preflight_failed"
    assert adapters.calls[-2:] == ["cleanup", "quiescence"]


def test_publication_failure_rolls_back_and_retry_succeeds(tmp_path: Path, monkeypatch):
    import wsprrypi_qualification.real_session as module

    original = module.write_manifest

    def fail_manifest(_path):
        raise OSError("injected manifest failure")

    monkeypatch.setattr(module, "write_manifest", fail_manifest)
    with pytest.raises(RealSessionError, match="rolled back"):
        run(tmp_path, FakeAdapters())
    assert not any(tmp_path.iterdir())
    monkeypatch.setattr(module, "write_manifest", original)
    assert run(tmp_path, FakeAdapters())["final_status"] == "inconclusive"
