import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wsprrypi_qualification.cli import main
from wsprrypi_qualification.offline import OfflineAnalysisError
from wsprrypi_qualification.real_session import (
    RealQualificationSession,
    RealRuntimeAuthorization,
    RealSessionError,
    ResolvedRealSessionPlan,
    resolved_real_plan_sha256,
    validate_real_session_document,
)

NOW = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)


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
        "host": "wspr5.local",
        "transport": "ssh",
        "remote_helper": executable,
        "wsprrypi": {**executable, "identity": "wsprrypi"},
        "source": {"parent_revision": "parent", "submodule_revision": "submodule"},
        "backend": "si5351",
        "output": "CLK0",
        "backend_contract": {
            "backend": "si5351",
            "output": "CLK0",
            "quiescence_provider_sha256": "3" * 64,
        },
        "services": ["wsprrypi"],
        "receiver": {
            "host": "wspr5.local",
            "driver": "sdrplay",
            "serial": "SERIAL",
            "channel": 0,
            "sample_format": "CF32",
            "sample_rate_hz": 250000,
            "bandwidth_hz": 200000,
            "gain_db": 10,
            "agc": False,
            "bias_tee": False,
        },
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
        "identity": {"callsign": "AA0NT", "grid": "EM18", "power_dbm": 20},
        "calibration": {"ppm": 2.3536},
        "drive": {"value": 2, "unit": "mA"},
        "mode": "WSPR",
        "frame_count": 3,
        "random_offset_enabled": False,
        "carrier": {"rf_off_sample_count": 2500000, "rf_on_sample_count": 2500000},
        "coherent_capture": {
            "duration_s": 370,
            "sample_rate_hz": 250000,
            "sample_count": 92500000,
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
            "ssh": "4" * 64,
            "soapy": "5" * 64,
            "wsprrypi": "a" * 64,
            "service": "7" * 64,
            "quiescence": "3" * 64,
        },
        "external_access_enabled": True,
        "rf_enabled": True,
    }
    document["remote_helper"]["plan_sha256"] = resolved_real_plan_sha256(document)
    return document


class FakeAdapters:
    def __init__(self, *, carrier="passed", decode="passed", fail=None):
        self.calls = []
        self.carrier, self.decode, self.fail = carrier, decode, fail

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
            details={
                key: plan["remote_helper"][key] for key in ("host", "path", "sha256", "identity")
            },
        )

    def inspect_services_and_ownership(self, plan):
        return self._call(
            "ownership",
            evidence_type="ownership",
            details={"host": plan["host"], "services": plan["services"], "conflicts": []},
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
        offset = 0 if self.carrier != "failed" else 200
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


def test_hardware_free_success_remains_inconclusive_and_is_packaged(tmp_path: Path):
    adapters = FakeAdapters()
    document = run(tmp_path, adapters)
    assert document["final_status"] == "inconclusive"
    assert document["carrier_gate"] == document["decode_gate"] == "passed"
    bundle = tmp_path / plan_document()["run_id"]
    assert (bundle / "result.json").is_file()
    assert (bundle / "SHA256SUMS").is_file()
    assert adapters.calls.index("install_cleanup") < adapters.calls.index("rf_on")


def test_failed_carrier_suppresses_frames(tmp_path: Path):
    adapters = FakeAdapters(carrier="failed")
    document = run(tmp_path, adapters)
    assert document["final_status"] == "unqualified_carrier"
    assert "frames" not in adapters.calls and "decode" not in adapters.calls


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
                },
            )

    document = run(tmp_path, ForgedCarrier())
    assert document["final_status"] == "aborted"
    assert document["carrier_gate"] == "not_run"


def test_blocked_decode_is_fixture_blocked(tmp_path: Path):
    document = run(tmp_path, FakeAdapters(decode="blocked"))
    assert document["final_status"] == "fixture_blocked"


def test_live_execution_mode_is_not_available():
    with pytest.raises(OfflineAnalysisError):
        ResolvedRealSessionPlan(plan_document(execution_mode="live")).validated()


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
    with pytest.raises(RealSessionError, match=r"lacks blocked|require inconclusive"):
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
    with pytest.raises(RealSessionError, match=r"aborted status|require inconclusive"):
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
    document["remote_helper"]["plan_sha256"] = resolved_real_plan_sha256(document)
    with pytest.raises(OfflineAnalysisError):
        ResolvedRealSessionPlan(document).validated()


def test_preserved_wspr_sample_contract_is_exact():
    document = plan_document()
    document["coherent_capture"] = {
        "duration_s": 370,
        "sample_rate_hz": 1000,
        "sample_count": 370000,
    }
    document["remote_helper"]["plan_sha256"] = resolved_real_plan_sha256(document)
    with pytest.raises(OfflineAnalysisError):
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
    assert document["final_status"] == "aborted"
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
