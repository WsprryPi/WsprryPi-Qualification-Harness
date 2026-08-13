import hashlib
import json
import struct
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from wsprrypi_qualification.cli import main
from wsprrypi_qualification.manifests import build_manifest, write_manifest
from wsprrypi_qualification.simulator import (
    SimulationError,
    SimulatorPlan,
    run_simulation,
    validate_simulator_bundle,
    validate_simulator_decode_summary,
    validate_simulator_session,
)


def plan(tmp_path: Path, suffix: str = "success", injection: str = "none") -> SimulatorPlan:
    return SimulatorPlan(
        f"20260812T120000Z-{suffix}", tmp_path / "output parent with spaces", injection=injection
    )


def test_bounded_success_is_inconclusive_and_retained(tmp_path: Path) -> None:
    result = run_simulation(plan(tmp_path))
    session = result["session"]
    root = Path(result["run_directory"])
    assert session["final_status"] == "inconclusive"
    assert session["carrier_gate"] == session["decode_gate"] == "passed"
    assert session["qualification_claim"] is False
    assert session["timing"]["actual_elapsed_s"] < session["timing"]["overall_deadline_s"]
    assert (root / "SHA256SUMS").is_file()
    assert len(list(root.glob("*.wav"))) == 3
    assert len(list(root.glob("decoder-*.json"))) == 3
    assert all(child["cleanup_verified"] for child in session["children"])
    validate_simulator_bundle(root)


def test_failed_carrier_suppresses_frames(tmp_path: Path) -> None:
    session = run_simulation(plan(tmp_path, "carrier", "carrier_fail"))["session"]
    assert session["final_status"] == "unqualified_carrier"
    assert session["decode_gate"] == "not_run"
    assert "frames" not in [event["phase"] for event in session["events"]]


def test_cleanup_failure_overrides_and_never_qualifies(tmp_path: Path) -> None:
    session = run_simulation(plan(tmp_path, "cleanup", "cleanup_fail"))["session"]
    assert session["final_status"] == "cleanup_failed"
    assert session["final_status"] != "qualified"


def test_output_collision_and_live_options_fail_closed(tmp_path: Path) -> None:
    selected = plan(tmp_path, "collision")
    run_simulation(selected)
    with pytest.raises(SimulationError, match="reuse"):
        run_simulation(selected)
    assert (
        main(["simulate-qualification", str(tmp_path), "--run-id", selected.run_id, "--enable-rf"])
        == 2
    )


@pytest.mark.parametrize(
    "injection",
    [
        "rf_off_timeout",
        "rf_off_nonzero",
        "carrier_timeout",
        "carrier_nonzero",
        "frame_timeout",
        "frame_nonzero",
    ],
)
def test_required_child_failure_stops_without_promotion(tmp_path: Path, injection: str) -> None:
    selected = plan(tmp_path, injection, injection)
    result = run_simulation(selected)
    assert result["session"]["final_status"] in {"aborted", "cleanup_failed"}
    parent = selected.output_parent
    root = parent / selected.run_id
    assert root.is_dir()
    validate_simulator_bundle(root)
    if injection.startswith("rf_off"):
        assert not (root / "rf-off.cf32").exists()
    if injection.startswith("carrier"):
        assert not (root / "carrier-analysis.json").exists()
    if injection.startswith("frame"):
        assert not list(root.glob("*.wav"))


def test_tiny_overall_deadline_is_rejected_before_outputs(tmp_path: Path) -> None:
    selected = SimulatorPlan(
        "20260812T120000Z-tiny", tmp_path / "tiny output", overall_timeout_s=0.001
    )
    with pytest.raises((SimulationError, ValueError)):
        run_simulation(selected)
    assert not selected.output_parent.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(final_status="qualified"),
        lambda d: d.update(qualification_claim=True),
        lambda d: d["events"].pop(1),
        lambda d: d["events"].reverse(),
        lambda d: d.update(cleanup_outcome="failed"),
        lambda d: d.update(carrier_gate="failed"),
        lambda d: d["events"].pop(),
        lambda d: d["children"][0].update(
            arguments=["ssh", "host"], timed_out=True, return_code=None, cleanup_verified=False
        ),
    ],
)
def test_session_tampering_is_rejected(tmp_path: Path, mutation) -> None:
    document = run_simulation(plan(tmp_path, f"tamper-{id(mutation)}"))["session"]
    changed = json.loads(json.dumps(document))
    mutation(changed)
    with pytest.raises((SimulationError, ValueError)):
        validate_simulator_session(changed)


@pytest.mark.parametrize("target", ["result.json", "decode-summary.json", "20260812T000000Z.wav"])
def test_bundle_tampering_is_rejected(tmp_path: Path, target: str) -> None:
    root = Path(run_simulation(plan(tmp_path, f"bundle-{target.split('.')[0]}"))["run_directory"])
    path = root / target
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises((SimulationError, ValueError)):
        validate_simulator_bundle(root)


def test_duplicate_nonconsecutive_slots_and_carrier_metric_forgery_are_rejected(
    tmp_path: Path,
) -> None:
    root = Path(run_simulation(plan(tmp_path, "semantic-tamper"))["run_directory"])
    summary_path = root / "decode-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["slots"][1]["slot_utc"] = summary["slots"][0]["slot_utc"]
    with pytest.raises(SimulationError, match="consecutive"):
        validate_simulator_decode_summary(summary, root)

    carrier_path = root / "carrier-analysis.json"
    carrier = json.loads(carrier_path.read_text(encoding="utf-8"))
    carrier["metrics"]["strongest_offset_hz"] = 9999
    carrier_path.write_text(json.dumps(carrier), encoding="utf-8")
    write_manifest(root)
    with pytest.raises(SimulationError, match="carrier gate"):
        validate_simulator_bundle(root)


def _reauthenticate_bundle(root: Path) -> None:
    index_path = root / "artifact-index.json"
    index_path.unlink()
    (root / "SHA256SUMS").unlink()
    index = {
        "schema_version": 1,
        "evidence_type": "simulator_artifact_index",
        "simulated": True,
        "artifacts": [asdict(item) for item in build_manifest(root)],
    }
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_manifest(root)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rebind_first_wav(root: Path) -> Path:
    summary_path = root / "decode-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    slot = summary["slots"][0]
    wav = root / slot["wav"]["path"]
    decoder_path = root / slot["decoder"]["path"]
    decoder = json.loads(decoder_path.read_text(encoding="utf-8"))
    wav_record = {
        "path": str(wav.resolve()),
        "size_bytes": wav.stat().st_size,
        "sha256": _sha256(wav),
    }
    decoder["wav"].update(wav_record)
    decoder_path.write_text(json.dumps(decoder, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    slot["wav"].update({"size_bytes": wav.stat().st_size, "sha256": _sha256(wav)})
    slot["decoder"].update(
        {"size_bytes": decoder_path.stat().st_size, "sha256": _sha256(decoder_path)}
    )
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _reauthenticate_bundle(root)
    return wav


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("carrier_gate", "failed"),
        ("decode_gate", "not_run"),
        ("cleanup_outcome", "failed"),
        ("cause", "forged"),
        ("plan_sha256", "f" * 64),
    ],
)
def test_result_fields_are_derived_from_session(tmp_path: Path, field: str, value: str) -> None:
    root = Path(run_simulation(plan(tmp_path, f"result-{field}"))["run_directory"])
    result_path = root / "result.json"
    document = json.loads(result_path.read_text(encoding="utf-8"))
    document[field] = value
    result_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _reauthenticate_bundle(root)
    with pytest.raises(SimulationError, match="result contradicts"):
        validate_simulator_bundle(root)


@pytest.mark.parametrize("sample_count", [100, 242_999, 243_001])
def test_coherent_fixture_exact_count_is_semantically_verified(
    tmp_path: Path, sample_count: int
) -> None:
    root = Path(run_simulation(plan(tmp_path, f"iq-{sample_count}"))["run_directory"])
    coherent = root / "coherent-compact.cf32"
    data = coherent.read_bytes()
    if sample_count <= 243_000:
        coherent.write_bytes(data[: sample_count * 8])
    else:
        coherent.write_bytes(data + b"\0" * 8)
    _reauthenticate_bundle(root)
    with pytest.raises(SimulationError, match="CF32 fixture size"):
        validate_simulator_bundle(root)


@pytest.mark.parametrize(
    "injection", ["carrier_analysis_hang", "wav_hang", "decoder_hang", "publication_hang"]
)
def test_outer_worker_deadline_bounds_blocking_offline_stage(
    tmp_path: Path, injection: str
) -> None:
    selected = SimulatorPlan(
        f"20260812T120000Z-{injection}",
        tmp_path / "deadline output",
        overall_timeout_s=2,
        injection=injection,
    )
    started = time.monotonic()
    with pytest.raises(SimulationError, match="hard outer deadline"):
        run_simulation(selected)
    assert time.monotonic() - started < 3.5
    assert not (selected.output_parent / selected.run_id).exists()


def test_relative_output_parent_is_canonicalized_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    selected = SimulatorPlan("20260812T120000Z-relative", Path("runs with spaces"))
    result = run_simulation(selected)
    root = Path(result["run_directory"])
    assert root == (tmp_path / "runs with spaces" / selected.run_id).resolve()
    requested = json.loads((root / "requested-plan.json").read_text(encoding="utf-8"))
    assert Path(requested["output_parent"]) == (tmp_path / "runs with spaces").resolve()


@pytest.mark.parametrize(
    ("target", "mutation"),
    [
        ("capabilities.json", lambda d: d.update(network=True)),
        ("runtime-confirmation.json", lambda d: d.update(rf_authorized=True)),
        ("runtime-confirmation.json", lambda d: d.update(plan_sha256="f" * 64)),
        ("quiescence.json", lambda d: d.update(verified=False)),
        ("quiescence.json", lambda d: d.update(gpio_inspection="mutating")),
        ("requested-plan.json", lambda d: d.update(time_scale=0.5)),
    ],
)
def test_foundational_evidence_tampering_survives_no_reauthentication(
    tmp_path: Path, target: str, mutation
) -> None:
    root = Path(
        run_simulation(plan(tmp_path, f"foundation-{target.split('.')[0]}"))["run_directory"]
    )
    path = root / target
    document = json.loads(path.read_text(encoding="utf-8"))
    mutation(document)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _reauthenticate_bundle(root)
    with pytest.raises((SimulationError, ValueError)):
        validate_simulator_bundle(root)


@pytest.mark.parametrize("target", ["capabilities.json", "runtime-confirmation.json"])
def test_missing_foundational_evidence_is_rejected_after_reauthentication(
    tmp_path: Path, target: str
) -> None:
    root = Path(run_simulation(plan(tmp_path, f"missing-{target.split('.')[0]}"))["run_directory"])
    (root / target).unlink()
    _reauthenticate_bundle(root)
    with pytest.raises((SimulationError, ValueError, FileNotFoundError)):
        validate_simulator_bundle(root)


def test_unexpected_artifact_is_rejected_after_reauthentication(tmp_path: Path) -> None:
    root = Path(run_simulation(plan(tmp_path, "unexpected-artifact"))["run_directory"])
    (root / "unexpected.txt").write_text("not evidence\n", encoding="utf-8")
    _reauthenticate_bundle(root)
    with pytest.raises(SimulationError, match="artifact set"):
        validate_simulator_bundle(root)


def test_wav_trailing_bytes_rejected_after_all_evidence_is_reauthenticated(
    tmp_path: Path,
) -> None:
    root = Path(run_simulation(plan(tmp_path, "wav-trailer"))["run_directory"])
    wav = root / "20260812T000000Z.wav"
    wav.write_bytes(wav.read_bytes() + b"TRAILER")
    _rebind_first_wav(root)
    with pytest.raises(SimulationError, match="RIFF size"):
        validate_simulator_bundle(root)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data[:10],
        lambda data: data[:13],
        lambda data: data[:100],
        lambda data: data[:4] + struct.pack("<I", len(data) + 20) + data[8:],
        lambda data: data[:4] + struct.pack("<I", len(data) - 12) + data[8:],
        lambda data: data[:20] + struct.pack("<H", 3) + data[22:],
        lambda data: data[:22] + struct.pack("<I", 999) + data[26:],
        lambda data: data[:32] + struct.pack("<H", 4) + data[34:],
        lambda data: data[:34] + struct.pack("<H", 8) + data[36:],
        lambda data: data[:36] + data[12:36] + data[36:],
        lambda data: data[:44] + data[36:44] + data[44:],
    ],
)
def test_malformed_wav_container_rejected_after_reauthentication(tmp_path: Path, mutation) -> None:
    root = Path(run_simulation(plan(tmp_path, f"wav-malformed-{id(mutation)}"))["run_directory"])
    wav = root / "20260812T000000Z.wav"
    wav.write_bytes(mutation(wav.read_bytes()))
    _rebind_first_wav(root)
    with pytest.raises((SimulationError, ValueError)):
        validate_simulator_bundle(root)
