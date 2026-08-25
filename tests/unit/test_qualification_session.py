import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from wsprrypi_qualification.application_shims import (
    ApplicationIdentity,
    WsprProtocol,
    WsprryPiBackendConfig,
    WsprryPiShim,
    validate_application_plan,
)
from wsprrypi_qualification.audio import create_slot_wav_acquired
from wsprrypi_qualification.carrier import analyze_carrier_acquired
from wsprrypi_qualification.decoder import run_wsprd_acquired, summarize_decodes
from wsprrypi_qualification.manifests import build_manifest, render_manifest
from wsprrypi_qualification.models import (
    AuthorizationScope,
    Backend,
    BenchProfile,
    PathType,
    QualificationGates,
    ReceiverConfig,
    ReceiverRunAuthorization,
    ReceiverRunLimits,
    ReceiverRunProfile,
    RfPathConfig,
    StoppingProcedure,
    TransmitterConfig,
    Transport,
    WsprIdentity,
)
from wsprrypi_qualification.models import (
    TestProfile as QualificationTestProfile,
)
from wsprrypi_qualification.offline import OfflineAnalysisError, validate_document
from wsprrypi_qualification.qualification_session import (
    Injection,
    OfflineEvidenceSet,
    QualificationSession,
    QualificationSessionPlan,
    RuntimeConfirmation,
    SessionError,
    resolved_plan_sha256,
    validate_published_bundle,
    validate_session_document,
    validate_session_plan,
)

NOW = datetime(2026, 8, 11, 12, 2, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fast_retained_pcm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep coordinator tests fast; audio.py owns the numerical converter tests."""

    def render(_path: Path, _start: int, count: int, parameters: object) -> tuple[bytes, float]:
        output_rate = int(parameters.output_rate_hz)
        sample_rate = int(parameters.sample_rate_hz)
        return bytes(2 * count * output_rate // sample_rate), 1.0

    monkeypatch.setattr("wsprrypi_qualification.audio.render_slot_pcm", render)
    monkeypatch.setattr("wsprrypi_qualification.decoder.render_slot_pcm", render)


def metadata(root: Path, name: str, iq: Path, *, rate: int, center: float) -> Path:
    settings = {
        "format": "CF32",
        "sample_rate_hz": rate,
        "bandwidth_hz": rate,
        "center_frequency_hz": center,
        "gain_db": 10,
        "channel": 0,
        "agc": False,
        "bias_tee": False,
    }
    device = {"driver": "sdrplay", "serial": "2404058C60"}
    timestamp_names = (
        "helper_start_utc",
        "configuration_start_utc",
        "configuration_complete_utc",
        "first_read_start_utc",
        "first_read_complete_utc",
        "retained_capture_start_utc",
        "retained_capture_complete_utc",
        "cleanup_start_utc",
        "cleanup_complete_utc",
        "helper_complete_utc",
    )
    document = {
        "schema_version": 1,
        "helper_version": "test",
        "evidence_type": "capture_success",
        "capture_id": Path(name).stem,
        "timestamps": {
            key: f"2026-08-11T12:01:{50 + index:02d}.000Z"
            for index, key in enumerate(timestamp_names)
        },
        "elapsed_duration_s": 1.0,
        "limits": {
            "read_timeout_us": 2_000_000,
            "max_elapsed_duration_s": 400.0,
            "max_read_calls": 1_000_000,
        },
        "requested_device": device,
        "resolved_device": device,
        "requested_settings": settings,
        "actual_settings": settings,
        "wire_format": {
            "sample_format": "CF32",
            "component_type": "IEEE754_binary32",
            "interleave": "real_imaginary",
            "byte_order": "little_endian",
            "bytes_per_complex_sample": 8,
        },
        "first_read": {
            "attempted": True,
            "discarded": True,
            "sample_count": 7,
            "included_in_overflow_and_clipping_statistics": False,
        },
        "requested_sample_count": iq.stat().st_size // 8,
        "retained_sample_count": iq.stat().st_size // 8,
        "read_call_count": 4,
        "partial_read_count": 1,
        "timeout_count": 0,
        "overflow_count": 0,
        "clipping": {"threshold": 0.999, "sample_count": 0},
        "output": {
            "path": iq.name,
            "present": True,
            "complete": True,
            "size_bytes": iq.stat().st_size,
            "sha256": hashlib.sha256(iq.read_bytes()).hexdigest(),
            "removed_incomplete_size_bytes": 0,
            "removed_incomplete_sha256": None,
        },
        "primary_outcome": "success",
        "primary_failure_cause": None,
        "failure_causes": [],
        "cleanup": {"outcome": "verified", "attempted_steps": ["mock_release"], "failed_steps": []},
        "process_exit_code": 0,
    }
    path = root / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def session_plan(executable: Path = Path("/opt/Wsprry Pi/wsprrypi")) -> QualificationSessionPlan:
    receiver = ReceiverConfig(
        Transport.SSH,
        "sdrplay",
        4_000,
        4_000,
        "CF32",
        False,
        host="wspr5.local",
        serial="2404058C60",
        channel=0,
        bias_tee=False,
    )
    rf_path = RfPathConfig(PathType.CONDUCTED, False, 50, 30, "160 m LPF", "verified")
    bench = BenchProfile(1, "wspr5-rsp1b-conducted", receiver, rf_path)
    transmitter = TransmitterConfig(
        Transport.SSH,
        "wspr5.local",
        Backend.SI5351,
        "CLK0",
        source_revision="parent-sha",
        submodule_revision="submodule-sha",
        i2c_bus=1,
        i2c_address="0x60",
        reference_frequency_hz=27_000_000,
        drive_ma=2,
    )
    test = QualificationTestProfile(
        1,
        "qualification-fixture",
        transmitter,
        "160m",
        11_000,
        10_000,
        10,
        WsprIdentity("Q0QQQ", "JJ00", 0),
        QualificationGates(100, 0.5, 3),
        StoppingProcedure("bounded", "exact count", "cancel", "verify", "bench stop"),
        ppm=2.3536,
        frame_count=3,
        bounded_duration_s=370,
        random_offset_enabled=False,
    )
    receiver_run = ReceiverRunProfile(
        1,
        "20260812T160000Z-qualification-fixture",
        bench.bench_id,
        receiver,
        10_000,
        10,
        370,
        rf_path,
        ReceiverRunLimits(1_480_000, 2_000_000, 375, 380),
        ReceiverRunAuthorization(AuthorizationScope.SINGLE_RUN, "mock receiver only", NOW),
        "mock-only ownership and cleanup",
    )
    application = WsprryPiShim(
        ApplicationIdentity("wsprrypi", executable, "parent-sha", "submodule-sha"),
        backend="si5351",
        backend_config=WsprryPiBackendConfig(
            "CLK0",
            2.3536,
            i2c_bus=1,
            i2c_address="0x60",
            reference_frequency_hz=27_000_000,
            drive_or_power_level=1,
        ),
    ).resolve_plan("qualification-fixture", WsprProtocol("Q0QQQ", "JJ00", 0, 11_000, 3, 1500))
    return QualificationSessionPlan(
        receiver_run.run_id, bench, test, receiver_run, application, NOW, 120, 380
    )


def retained_evidence(
    tmp_path: Path,
    plan: QualificationSessionPlan,
    *,
    carrier_tone_hz: int = 1_000,
    correct_decode: bool = True,
    carrier_plot: bool = False,
) -> OfflineEvidenceSet:
    root = tmp_path / "retained qualification evidence"
    root.mkdir(parents=True)
    resolved = plan.resolved_document()
    bench_path, test_path = root / "bench.json", root / "test.json"
    bench_path.write_text(json.dumps(resolved["bench"]), encoding="utf-8")
    test_path.write_text(json.dumps(resolved["test"]), encoding="utf-8")

    off, on = root / "off.cf32", root / "on.cf32"
    np.zeros(3_000, dtype="<c8").tofile(off)
    samples = np.arange(3_000)
    np.asarray(0.25 * np.exp(2j * np.pi * carrier_tone_hz * samples / 4_000), dtype="<c8").tofile(
        on
    )
    carrier_path = root / "carrier.json"
    analyze_carrier_acquired(
        off,
        on,
        metadata(root, "off.json", off, rate=4_000, center=10_000),
        metadata(root, "on.json", on, rate=4_000, center=10_000),
        bench_path,
        test_path,
        carrier_path,
        fft_size=1_000,
        dc_exclusion_hz=25,
        plot_path=root / "carrier.png" if carrier_plot else None,
    )

    iq = root / "coherent.cf32"
    coherent_samples = np.arange(1_480_000)
    np.asarray(0.1 * np.exp(2j * np.pi * 1_000 * coherent_samples / 4_000), dtype="<c8").tofile(iq)
    capture = metadata(root, "coherent.json", iq, rate=4_000, center=10_000)
    wav_root = root / "wav"
    wav_root.mkdir()
    audio_paths: list[Path] = []
    decoder_paths: list[Path] = []
    python_version = subprocess.run(
        [sys.executable, "--version"], capture_output=True, text=True, check=False
    )

    def fake_wsprd(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[-1] == "--version":
            return subprocess.CompletedProcess(
                arguments, python_version.returncode, python_version.stdout, python_version.stderr
            )
        slot = Path(arguments[-1]).stem[9:13]
        identity = "Q0QQQ JJ00 0" if correct_decode else "N0CALL EM00 10"
        return subprocess.CompletedProcess(
            arguments, 0, f"{slot} -18 0.8 0.001500 0 {identity}\n", ""
        )

    with patch("wsprrypi_qualification.decoder.subprocess.run", side_effect=fake_wsprd):
        for index, minute in enumerate((2, 4, 6)):
            slot = datetime(2026, 8, 11, 12, minute, tzinfo=UTC)
            audio_path = root / f"audio-{index}.json"
            create_slot_wav_acquired(iq, capture, bench_path, test_path, slot, wav_root, audio_path)
            decoder_path = root / f"decoder-{index}.json"
            run_wsprd_acquired(
                wav_root / f"20260811T12{minute:02d}00Z.wav",
                audio_path,
                decoder_path,
                executable=Path(sys.executable),
            )
            audio_paths.append(audio_path)
            decoder_paths.append(decoder_path)
    summary_path = root / "decode-summary.json"
    summarize_decodes(decoder_paths, summary_path)
    return OfflineEvidenceSet(
        carrier_path,
        tuple(audio_paths),  # type: ignore[arg-type]
        tuple(decoder_paths),  # type: ignore[arg-type]
        summary_path,
    )


def confirmation(plan: QualificationSessionPlan) -> RuntimeConfirmation:
    return RuntimeConfirmation(NOW, "test operator", resolved_plan_sha256(plan), True)


def run(tmp_path: Path, injection: Injection = Injection.NONE) -> dict[str, object]:
    plan = session_plan()
    if injection not in {
        Injection.INVALID_PLAN,
        Injection.MISSING_CAPABILITY,
        Injection.MISSING_DEPENDENCY,
        Injection.CONFIRMATION_MISMATCH,
        Injection.UNSAFE_RF_PATH,
        Injection.SOURCE_MISMATCH,
        Injection.RECEIVER_MISMATCH,
        Injection.OWNERSHIP_CONFLICT,
    }:
        plan = replace(plan, offline_evidence=retained_evidence(tmp_path, plan))
    return QualificationSession(plan, now=NOW).run(
        confirmation(plan), tmp_path / "evidence with spaces", injection=injection
    )


def test_successful_mock_remains_inconclusive_and_packages_manifest(tmp_path: Path) -> None:
    output = run(tmp_path)
    result = output["result"]
    session = output["session"]
    assert isinstance(result, dict) and result["status"] == "inconclusive"
    assert isinstance(session, dict) and session["frames_started"] is True
    bundle = Path(str(output["bundle"]))
    assert (bundle / "SHA256SUMS").is_file()
    assert "result.json" in (bundle / "SHA256SUMS").read_text(encoding="utf-8")
    assert "carrier-analysis.json" in (bundle / "SHA256SUMS").read_text(encoding="utf-8")
    index = json.loads((bundle / "offline-evidence-index.json").read_text(encoding="utf-8"))
    assert len(index["artifacts"]) >= 8
    assert json.loads((bundle / "session.json").read_text(encoding="utf-8")) == session


def test_requested_carrier_plot_is_indexed_manifested_and_fail_closed(tmp_path: Path) -> None:
    base = session_plan()
    plan = replace(base, offline_evidence=retained_evidence(tmp_path, base, carrier_plot=True))
    output = QualificationSession(plan, now=NOW).run(confirmation(plan), tmp_path / "bundle")
    bundle = Path(str(output["bundle"]))
    index = json.loads((bundle / "offline-evidence-index.json").read_text(encoding="utf-8"))
    plot_records = [
        item
        for item in index["artifacts"]
        if item["disposition"] == "bundled" and item["retained_path"].endswith("carrier.png")
    ]
    assert len(plot_records) == 1
    retained_plot = bundle / plot_records[0]["retained_path"]
    assert plot_records[0]["retained_path"] in (bundle / "SHA256SUMS").read_text(encoding="utf-8")
    validate_published_bundle(bundle)

    original = retained_plot.read_bytes()
    retained_plot.write_bytes(original + b"tampered")
    with pytest.raises(SessionError, match="contradicts its index"):
        validate_published_bundle(bundle)
    retained_plot.write_bytes(original)
    unexpected = bundle / "unexpected-plot.png"
    unexpected.write_bytes(original)
    with pytest.raises(SessionError, match="manifest is non-canonical"):
        validate_published_bundle(bundle)
    unexpected.unlink()
    retained_unexpected = bundle / "retained-artifacts" / "unexpected-plot.png"
    retained_unexpected.write_bytes(original)
    (bundle / "SHA256SUMS").write_text(render_manifest(build_manifest(bundle)), encoding="utf-8")
    with pytest.raises(SessionError, match="artifact set is incomplete or unexpected"):
        validate_published_bundle(bundle)
    retained_unexpected.unlink()
    (bundle / "SHA256SUMS").write_text(render_manifest(build_manifest(bundle)), encoding="utf-8")
    retained_plot.unlink()
    with pytest.raises((SessionError, FileNotFoundError)):
        validate_published_bundle(bundle)


@pytest.mark.parametrize(
    ("injection", "status", "frames"),
    (
        (Injection.MISSING_CAPABILITY, "fixture_blocked", False),
        (Injection.MISSING_DEPENDENCY, "fixture_blocked", False),
        (Injection.UNSAFE_RF_PATH, "fixture_blocked", False),
        (Injection.RECEIVER_MISMATCH, "fixture_blocked", False),
        (Injection.OWNERSHIP_CONFLICT, "fixture_blocked", False),
        (Injection.SOURCE_MISMATCH, "preflight_failed", False),
        (Injection.RF_IDLE_FAILURE, "fixture_blocked", False),
        (Injection.CANCELLED, "aborted", False),
        (Injection.CLEANUP_FAILED, "cleanup_failed", False),
        (Injection.QUIESCENCE_FAILED, "cleanup_failed", False),
        (Injection.SERVICE_RESTORE_FAILED, "cleanup_failed", False),
        (Injection.RECEIVER_LAUNCH_FAILED, "fixture_blocked", False),
        (Injection.TRANSMITTER_LAUNCH_FAILED, "fixture_blocked", False),
        (Injection.CHILD_TIMEOUT, "fixture_blocked", False),
    ),
)
def test_failure_matrix(tmp_path: Path, injection: Injection, status: str, frames: bool) -> None:
    output = run(tmp_path, injection)
    assert output["result"]["status"] == status  # type: ignore[index]
    assert output["session"]["frames_started"] is frames  # type: ignore[index]


def test_carrier_failure_comes_from_retained_analysis(tmp_path: Path) -> None:
    base = session_plan()
    plan = replace(base, offline_evidence=retained_evidence(tmp_path, base, carrier_tone_hz=-1_000))
    output = QualificationSession(plan, now=NOW).run(confirmation(plan), tmp_path / "bundle")
    assert output["result"]["status"] == "unqualified_carrier"  # type: ignore[index]
    assert output["session"]["frames_started"] is False  # type: ignore[index]
    bundle = Path(str(output["bundle"]))
    assert not list(bundle.glob("audio-*.json"))
    assert not list(bundle.glob("decoder-*.json"))
    assert not (bundle / "decode-summary.json").exists()


def test_retained_slots_must_equal_planned_slots(tmp_path: Path) -> None:
    base = session_plan()
    evidence = retained_evidence(tmp_path, base)
    shifted = replace(base, first_slot_utc=base.first_slot_utc + timedelta(hours=2))
    plan = replace(shifted, offline_evidence=evidence)
    output = QualificationSession(plan, now=NOW).run(confirmation(plan), tmp_path / "bundle")
    assert output["result"]["status"] == "inconclusive"  # type: ignore[index]
    assert output["session"]["frames_started"] is False  # type: ignore[index]


@pytest.mark.parametrize("ppm", (2.0, 2.3536))
def test_integral_and_fractional_ppm_reconcile(tmp_path: Path, ppm: float) -> None:
    base = session_plan()
    transmitter = replace(base.test.transmitter)
    test = replace(base.test, transmitter=transmitter, ppm=ppm)
    application = WsprryPiShim(
        base.application.identity,
        backend="si5351",
        backend_config=WsprryPiBackendConfig(
            "CLK0",
            ppm,
            i2c_bus=1,
            i2c_address="0x60",
            reference_frequency_hz=27_000_000,
            drive_or_power_level=1,
        ),
    ).resolve_plan("qualification-fixture", WsprProtocol("Q0QQQ", "JJ00", 0, 11_000, 3, 1500))
    plan = replace(base, test=test, application=application)
    validate_session_plan(plan)


@pytest.mark.parametrize(
    "injection",
    (
        Injection.COPY_FAILED,
        Injection.INDEX_FAILED,
        Injection.MANIFEST_FAILED,
        Injection.PROMOTION_FAILED,
    ),
)
def test_publication_failure_rolls_back_and_retry_succeeds(
    tmp_path: Path, injection: Injection
) -> None:
    base = session_plan()
    plan = replace(base, offline_evidence=retained_evidence(tmp_path, base))
    parent = tmp_path / "publication"
    with pytest.raises(SessionError, match="injected"):
        QualificationSession(plan, now=NOW).run(confirmation(plan), parent, injection=injection)
    assert not (parent / plan.run_id).exists()
    assert not (parent / f".incomplete-{plan.run_id}").exists()
    output = QualificationSession(plan, now=NOW).run(confirmation(plan), parent)
    assert Path(str(output["bundle"])).is_dir()


def test_bundled_derivatives_survive_fixture_removal(tmp_path: Path) -> None:
    base = session_plan()
    evidence = retained_evidence(tmp_path, base)
    plan = replace(base, offline_evidence=evidence)
    output = QualificationSession(plan, now=NOW).run(confirmation(plan), tmp_path / "bundle")
    bundle = Path(str(output["bundle"]))
    source_root = evidence.carrier_analysis.parent
    shutil.rmtree(source_root)
    validate_published_bundle(bundle)
    index = json.loads((bundle / "offline-evidence-index.json").read_text(encoding="utf-8"))
    bundled = [item for item in index["artifacts"] if item["disposition"] == "bundled"]
    assert bundled
    assert all((bundle / item["retained_path"]).is_file() for item in bundled)
    assert all(
        hashlib.sha256((bundle / item["retained_path"]).read_bytes()).hexdigest()
        == item["retained"]["sha256"]
        for item in bundled
    )
    assert any(item["disposition"] == "external" for item in index["artifacts"])


def test_failing_carrier_does_not_require_frame_evidence(tmp_path: Path) -> None:
    base = session_plan()
    evidence = retained_evidence(tmp_path, base, carrier_tone_hz=-1_000)
    evidence.decode_summary.unlink()
    plan = replace(base, offline_evidence=evidence)
    output = QualificationSession(plan, now=NOW).run(confirmation(plan), tmp_path / "bundle")
    assert output["result"]["status"] == "unqualified_carrier"  # type: ignore[index]


def test_index_rejects_bundled_record_without_retained_hash() -> None:
    with pytest.raises(OfflineAnalysisError):
        validate_document(
            {
                "schema_version": 1,
                "evidence_type": "offline_evidence_index",
                "artifacts": [
                    {
                        "role": "x",
                        "source": {"path": "x", "size_bytes": 1, "sha256": "0" * 64},
                        "disposition": "bundled",
                        "retained_path": "x",
                    }
                ],
            },
            "offline-evidence-index.schema.json",
        )


def test_cleanup_status_must_agree_with_supervisors(tmp_path: Path) -> None:
    output = run(tmp_path)
    document = json.loads(json.dumps(output["session"]))
    document["final_status"] = "cleanup_failed"
    with pytest.raises(SessionError, match="failed supervisor"):
        validate_session_document(document)


def test_decode_failure_comes_from_retained_decoder_summary(tmp_path: Path) -> None:
    base = session_plan()
    plan = replace(base, offline_evidence=retained_evidence(tmp_path, base, correct_decode=False))
    output = QualificationSession(plan, now=NOW).run(confirmation(plan), tmp_path / "bundle")
    assert output["result"]["status"] == "unqualified_decode"  # type: ignore[index]
    assert output["session"]["frames_started"] is True  # type: ignore[index]


def test_contradictory_retained_summary_is_refused(tmp_path: Path) -> None:
    base = session_plan()
    evidence = retained_evidence(tmp_path, base)
    changed = json.loads(evidence.decode_summary.read_text(encoding="utf-8"))
    changed["gate_outcome"] = "failed"
    evidence.decode_summary.write_text(json.dumps(changed), encoding="utf-8")
    plan = replace(base, offline_evidence=evidence)
    output = QualificationSession(plan, now=NOW).run(confirmation(plan), tmp_path / "bundle")
    assert output["result"]["status"] == "inconclusive"  # type: ignore[index]


def test_cleanup_is_installed_before_rf_idle_boundary(tmp_path: Path) -> None:
    output = run(tmp_path)
    phases = [item["phase"] for item in output["session"]["events"]]  # type: ignore[index]
    assert phases.index("cleanup_installed") < phases.index("rf_idle_verified")


def test_missing_or_mismatched_confirmation_fails_preflight(tmp_path: Path) -> None:
    plan = session_plan()
    missing = QualificationSession(plan, now=NOW).run(None, tmp_path / "missing")
    assert missing["result"]["status"] == "preflight_failed"  # type: ignore[index]
    wrong = replace(confirmation(plan), resolved_plan_sha256="0" * 64)
    mismatch = QualificationSession(plan, now=NOW).run(wrong, tmp_path / "mismatch")
    assert mismatch["result"]["status"] == "preflight_failed"  # type: ignore[index]


def test_single_use_and_immutable_directory(tmp_path: Path) -> None:
    plan = session_plan()
    coordinator = QualificationSession(plan, now=NOW)
    coordinator.run(confirmation(plan), tmp_path)
    with pytest.raises(SessionError, match="single-use"):
        coordinator.run(confirmation(plan), tmp_path)
    with pytest.raises(SessionError, match="reuse"):
        QualificationSession(plan, now=NOW).run(confirmation(plan), tmp_path)


def test_windows_executable_with_spaces_is_evidence_not_execution(tmp_path: Path) -> None:
    plan = session_plan(Path(r"C:\Program Files\WsprryPi\wsprrypi.exe"))
    output = QualificationSession(plan, now=NOW).run(confirmation(plan), tmp_path)
    document = json.loads(
        (Path(str(output["bundle"])) / "resolved-session-plan.json").read_text(encoding="utf-8")
    )
    assert document["application"]["arguments"][0] == str(plan.application.identity.executable)


def test_non_wspr_and_mismatched_receiver_are_rejected(tmp_path: Path) -> None:
    plan = session_plan()
    bad_receiver = replace(plan.receiver_run, center_frequency_hz=plan.test.receiver_center_hz + 1)
    bad = replace(plan, receiver_run=bad_receiver)
    output = QualificationSession(bad, now=NOW).run(confirmation(bad), tmp_path)
    assert output["result"]["status"] == "preflight_failed"  # type: ignore[index]


def test_resolved_backend_application_plan_validates_semantically() -> None:
    document = session_plan().application.to_document()
    validate_application_plan(document)
    changed = json.loads(json.dumps(document))
    changed["backend_contract"]["drive_or_power_level"] = 2
    with pytest.raises(ValueError, match="backend contract"):
        validate_application_plan(changed)
