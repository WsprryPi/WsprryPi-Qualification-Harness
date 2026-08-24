import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from tests.unit.test_capture_metadata import capture_document
from tests.unit.test_profiles import load_example
from wsprrypi_qualification.audio import create_slot_wav_acquired
from wsprrypi_qualification.carrier import (
    analyze_carrier_acquired,
    load_acquired_carrier_evidence,
)
from wsprrypi_qualification.cli import main
from wsprrypi_qualification.decoder import (
    load_audio_evidence,
    load_decoder_evidence,
    run_wsprd_acquired,
    summarize_decodes,
)
from wsprrypi_qualification.offline import OfflineAnalysisError, artifact
from wsprrypi_qualification.offline_context import (
    _local_recorded_output,
    load_profile_context,
    validate_acquired_capture,
)


def profiles(tmp_path: Path, *, rate: int, center: float, frequency: float) -> tuple[Path, Path]:
    bench = load_example("bench-wspr5-rsp1b.json")
    bench["receiver"].update(sample_rate_hz=rate, bandwidth_hz=rate)
    test = load_example("test-si5351-160m.json")
    test.update(receiver_center_hz=center, frequency_hz=frequency, receiver_gain_db=10)
    bench_path, test_path = tmp_path / "bench.json", tmp_path / "test.json"
    bench_path.write_text(json.dumps(bench), encoding="utf-8")
    test_path.write_text(json.dumps(test), encoding="utf-8")
    return bench_path, test_path


def metadata(tmp_path: Path, name: str, iq: Path, *, rate: int, center: float) -> Path:
    document = capture_document()
    document["capture_id"] = Path(name).stem
    timestamp_names = tuple(document["timestamps"])
    document["timestamps"] = {
        key: f"2026-08-11T12:01:{50 + index:02d}.000Z" for index, key in enumerate(timestamp_names)
    }
    document["requested_device"] = {"driver": "sdrplay", "serial": "2404058C60"}
    document["resolved_device"] = {"driver": "sdrplay", "serial": "2404058C60"}
    for key in ("requested_settings", "actual_settings"):
        settings = document[key]
        assert isinstance(settings, dict)
        settings.update(
            sample_rate_hz=rate,
            bandwidth_hz=rate,
            center_frequency_hz=center,
            gain_db=10,
        )
    count = iq.stat().st_size // 8
    document.update(requested_sample_count=count, retained_sample_count=count)
    output = document["output"]
    assert isinstance(output, dict)
    output.update(
        path=iq.name,
        size_bytes=iq.stat().st_size,
        sha256=hashlib.sha256(iq.read_bytes()).hexdigest(),
    )
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_acquired_carrier_uses_profiles_and_capture_contract(tmp_path: Path) -> None:
    rate, center, frequency, count = 4096, 10_000.0, 11_000.0, 4096
    bench, test = profiles(tmp_path, rate=rate, center=center, frequency=frequency)
    off = tmp_path / "off.cf32"
    on = tmp_path / "on.cf32"
    np.zeros(count, dtype="<c8").tofile(off)
    n = np.arange(count)
    np.asarray(0.5 * np.exp(2j * np.pi * 1000 * n / rate), dtype="<c8").tofile(on)
    result = analyze_carrier_acquired(
        off,
        on,
        metadata(tmp_path, "off.json", off, rate=rate, center=center),
        metadata(tmp_path, "on.json", on, rate=rate, center=center),
        bench,
        test,
        tmp_path / "carrier.json",
        fft_size=1024,
        dc_exclusion_hz=100,
    )
    assert result["contract"]["profiles"]["test"]["id"] == "si5351-160m-production-example"
    assert result["metrics"]["requested_frequency_hz"] == frequency
    assert result["gate_outcome"] == "passed"
    evidence_path = tmp_path / "carrier.json"
    acquired = load_acquired_carrier_evidence(evidence_path)
    assert acquired.rf_off_path == off.resolve()
    for record in (
        result["inputs"]["rf_off"],
        result["inputs"]["rf_on"],
        result["contract"]["capture_metadata_validation"]["rf_off"],
        result["contract"]["capture_metadata_validation"]["rf_on"],
    ):
        assert Path(record["path"]).is_absolute()

    changed = json.loads(evidence_path.read_text(encoding="utf-8"))
    changed["metrics"]["strongest_offset_hz"] += 1
    changed_path = tmp_path / "changed-carrier.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="metrics or contract"):
        load_acquired_carrier_evidence(changed_path)

    original_metadata = off_meta_text = (tmp_path / "off.json").read_text(encoding="utf-8")
    metadata_document = json.loads(off_meta_text)
    metadata_document["capture_id"] = "changed-capture"
    (tmp_path / "off.json").write_text(json.dumps(metadata_document), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError):
        load_acquired_carrier_evidence(evidence_path)
    (tmp_path / "off.json").write_text(original_metadata, encoding="utf-8")

    on_metadata_path = tmp_path / "on.json"
    original_on_metadata = on_metadata_path.read_text(encoding="utf-8")
    duplicate_id = json.loads(original_on_metadata)
    duplicate_id["capture_id"] = "off"
    on_metadata_path.write_text(json.dumps(duplicate_id), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="distinct capture"):
        analyze_carrier_acquired(
            off,
            on,
            tmp_path / "off.json",
            on_metadata_path,
            bench,
            test,
            tmp_path / "duplicate-id.json",
            fft_size=1024,
            dc_exclusion_hz=100,
        )
    on_metadata_path.write_text(original_on_metadata, encoding="utf-8")

    copied_iq = tmp_path / "copied-off.cf32"
    copied_iq.write_bytes(off.read_bytes())
    copied_metadata = metadata(tmp_path, "copied-off.json", copied_iq, rate=rate, center=center)
    with pytest.raises(OfflineAnalysisError, match="distinct capture"):
        analyze_carrier_acquired(
            off,
            copied_iq,
            tmp_path / "off.json",
            copied_metadata,
            bench,
            test,
            tmp_path / "duplicate-iq.json",
            fft_size=1024,
            dc_exclusion_hz=100,
        )

    wrong_output = json.loads(original_metadata)
    wrong_output["output"]["path"] = on.name
    (tmp_path / "off.json").write_text(json.dumps(wrong_output), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="available original"):
        load_acquired_carrier_evidence(evidence_path)
    (tmp_path / "off.json").write_text(original_metadata, encoding="utf-8")

    old_cwd = Path.cwd()
    try:
        import os

        os.chdir(tmp_path.parent)
        load_acquired_carrier_evidence(evidence_path)
    finally:
        os.chdir(old_cwd)


def test_acquired_carrier_plot_is_recomputed_and_tampering_is_rejected(tmp_path: Path) -> None:
    rate, center, frequency, count = 4096, 10_000.0, 11_000.0, 2048
    bench, test = profiles(tmp_path, rate=rate, center=center, frequency=frequency)
    off, on = tmp_path / "off.cf32", tmp_path / "on.cf32"
    np.zeros(count, dtype="<c8").tofile(off)
    samples = np.arange(count)
    np.asarray(0.5 * np.exp(2j * np.pi * 1000 * samples / rate), dtype="<c8").tofile(on)
    analysis, plot = tmp_path / "carrier.json", tmp_path / "carrier.png"
    analyze_carrier_acquired(
        off,
        on,
        metadata(tmp_path, "off.json", off, rate=rate, center=center),
        metadata(tmp_path, "on.json", on, rate=rate, center=center),
        bench,
        test,
        analysis,
        fft_size=1024,
        dc_exclusion_hz=100,
        plot_path=plot,
    )
    loaded = load_acquired_carrier_evidence(analysis)
    assert loaded.document["plot"]["artifact"]["path"] == str(plot.resolve())

    original = plot.read_bytes()
    plot.write_bytes(original[:-32] + b"0" * 32)
    with pytest.raises(OfflineAnalysisError, match="identity changed"):
        load_acquired_carrier_evidence(analysis)
    plot.write_bytes(original)

    changed = json.loads(analysis.read_text(encoding="utf-8"))
    changed["plot"]["normalization"]["calibrated"] = True
    contradictory = tmp_path / "contradictory.json"
    contradictory.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="violates schema"):
        load_acquired_carrier_evidence(contradictory)


def test_analyze_carrier_cli_writes_requested_plot(tmp_path: Path) -> None:
    rate, center, frequency, count = 4096, 10_000.0, 11_000.0, 1024
    bench, test = profiles(tmp_path, rate=rate, center=center, frequency=frequency)
    off, on = tmp_path / "off.cf32", tmp_path / "on.cf32"
    np.zeros(count, dtype="<c8").tofile(off)
    samples = np.arange(count)
    np.asarray(0.5 * np.exp(2j * np.pi * 1000 * samples / rate), dtype="<c8").tofile(on)
    off_metadata = metadata(tmp_path, "off.json", off, rate=rate, center=center)
    on_metadata = metadata(tmp_path, "on.json", on, rate=rate, center=center)
    analysis, plot = tmp_path / "analysis.json", tmp_path / "carrier.svg"
    assert (
        main(
            [
                "analyze-carrier",
                str(off),
                str(on),
                str(analysis),
                "--bench-profile",
                str(bench),
                "--test-profile",
                str(test),
                "--rf-off-metadata",
                str(off_metadata),
                "--rf-on-metadata",
                str(on_metadata),
                "--fft-size",
                "1024",
                "--dc-exclusion-hz",
                "100",
                "--plot",
                str(plot),
            ]
        )
        == 0
    )
    document = json.loads(analysis.read_text(encoding="utf-8"))
    assert document["plot"]["media_type"] == "image/svg+xml"
    assert plot.is_file()


def test_capture_metadata_output_path_is_metadata_relative_and_authenticated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rate, center = 1000, 10_000.0
    bench, test = profiles(tmp_path, rate=rate, center=center, frequency=center + 100)
    iq_directory = tmp_path / "directory with spaces"
    iq_directory.mkdir()
    iq = iq_directory / "capture.cf32"
    np.zeros(1000, dtype="<c8").tofile(iq)
    metadata_path = metadata(tmp_path, "path-capture.json", iq, rate=rate, center=center)
    document = json.loads(metadata_path.read_text(encoding="utf-8"))
    document["output"]["path"] = str(Path("directory with spaces") / iq.name)
    metadata_path.write_text(json.dumps(document), encoding="utf-8")
    context = load_profile_context(bench, test)

    monkeypatch.chdir(tmp_path.parent)
    validate_acquired_capture(metadata_path, iq, context)

    document["output"]["path"] = str(iq.resolve())
    metadata_path.write_text(json.dumps(document), encoding="utf-8")
    validate_acquired_capture(metadata_path, iq, context)

    alternate = tmp_path / "same-content.cf32"
    alternate.write_bytes(iq.read_bytes())
    document["output"]["path"] = str(alternate)
    metadata_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="available original"):
        validate_acquired_capture(metadata_path, iq, context)

    document["output"]["path"] = "/unavailable/original/capture.cf32"
    metadata_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="no authenticated relocation"):
        validate_acquired_capture(metadata_path, iq, context)

    iq.write_bytes(iq.read_bytes() + b"changed")
    with pytest.raises(OfflineAnalysisError, match="hash or size"):
        validate_acquired_capture(metadata_path, iq, context)


def test_recorded_output_preserves_foreign_absolute_path_flavor(tmp_path: Path) -> None:
    metadata_path = tmp_path / "capture.json"
    assert (
        _local_recorded_output("/home/pi/capture.cf32", metadata_path, platform_name="nt") is None
    )
    assert (
        _local_recorded_output(r"C:\capture\capture.cf32", metadata_path, platform_name="posix")
        is None
    )
    assert (
        _local_recorded_output("relative/capture.cf32", metadata_path, platform_name="nt")
        == tmp_path / "relative" / "capture.cf32"
    )


def test_acquired_audio_uses_capture_utc_and_canonical_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DSP fidelity and deterministic resampling are covered in test_audio.py.
    # This integration test exercises acquired-evidence, timestamp, decoder,
    # and tamper contracts without rendering three full 120-second signals.
    from wsprrypi_qualification import audio as audio_module
    from wsprrypi_qualification import decoder as decoder_module

    def stub_pcm(_path, _start, _count, parameters):
        return (
            bytes(parameters.output_rate_hz * parameters.frame_duration_s * 2),
            1.0,
        )

    monkeypatch.setattr(audio_module, "render_slot_pcm", stub_pcm)
    monkeypatch.setattr(decoder_module, "render_slot_pcm", stub_pcm)
    rate, center, frequency = 1000, 10_000.0, 10_100.0
    bench, test = profiles(tmp_path, rate=rate, center=center, frequency=frequency)
    iq = tmp_path / "coherent.cf32"
    n = np.arange(rate * 370)
    np.asarray(0.1 * np.exp(2j * np.pi * 100 * n / rate), dtype="<c8").tofile(iq)
    output = tmp_path / "wav output"
    output.mkdir()
    capture_metadata = metadata(tmp_path, "capture.json", iq, rate=rate, center=center)
    decoder_documents: list[Path] = []
    for index, minute in enumerate((2, 4, 6)):
        slot = datetime(2026, 8, 11, 12, minute, tzinfo=UTC)
        result = create_slot_wav_acquired(
            iq,
            capture_metadata,
            bench,
            test,
            slot,
            output,
            tmp_path / f"audio-{index}.json",
        )
        assert result["contract"]["input_start_sample"] == (5 + index * 120) * rate
        assert result["profiles"]["test"]["id"] == "si5351-160m-production-example"
        if index == 0:
            conjugate_root = tmp_path / "conjugate"
            conjugate_root.mkdir()

            def conjugate_decoder(arguments, **_kwargs):
                stdout = (
                    "fake version\n"
                    if arguments[-1] == "--version"
                    else "1202 -18 -0.8 0.002000 0 Q0QQQ JJ00 0\n"
                )
                return subprocess.CompletedProcess(arguments, 0, stdout, "")

            with monkeypatch.context() as context:
                context.setattr(decoder_module.subprocess, "run", conjugate_decoder)
                conjugate = run_wsprd_acquired(
                    output / "20260811T120200Z.wav",
                    tmp_path / "audio-0.json",
                    conjugate_root / "decoder.json",
                    executable=Path(sys.executable),
                )
            assert conjugate["expected_identity_found"] is True
            assert conjugate["expected_intended_signal_found"] is False
            assert conjugate["gate_outcome"] == "failed"
            loaded_conjugate = load_decoder_evidence(conjugate_root / "decoder.json")
            assert loaded_conjugate.document["failure_causes"] == [
                "expected_intended_signal_missing"
            ]
        if index == 0:
            conflict = tmp_path / "existing-decoder.json"
            conflict.write_text("preserve", encoding="utf-8")
            expected_data = tmp_path / f"20260811T12{minute:02d}00Z-wsprd-data"
            with pytest.raises(OfflineAnalysisError, match="overwrite"):
                run_wsprd_acquired(
                    output / f"20260811T12{minute:02d}00Z.wav",
                    tmp_path / f"audio-{index}.json",
                    conflict,
                    executable=Path(sys.executable),
                )
            assert not expected_data.exists()
        decoder_path = tmp_path / f"decoder-{index}.json"
        decoded = run_wsprd_acquired(
            output / f"20260811T12{minute:02d}00Z.wav",
            tmp_path / f"audio-{index}.json",
            decoder_path,
            executable=Path(sys.executable),
        )
        assert decoded["gate_outcome"] == "blocked"
        decoder_documents.append(decoder_path)
    assert sorted(path.name for path in output.iterdir()) == [
        "20260811T120200Z.wav",
        "20260811T120400Z.wav",
        "20260811T120600Z.wav",
    ]
    summary = summarize_decodes(decoder_documents, tmp_path / "decode-summary.json")
    assert summary["gate_outcome"] == "blocked"
    assert summary["slots"] == [
        "2026-08-11T12:02:00Z",
        "2026-08-11T12:04:00Z",
        "2026-08-11T12:06:00Z",
    ]
    assert all(item["path"].endswith(".json") for item in summary["slot_evidence"])
    assert summary["planned_frame_count"] == 3
    assert summary["failure_causes"] == ["dependency_or_fixture_blocked"]

    with pytest.raises(OfflineAnalysisError, match="planned frame count"):
        summarize_decodes(decoder_documents[:2])
    with pytest.raises(OfflineAnalysisError, match="planned frame count"):
        summarize_decodes([*decoder_documents, decoder_documents[-1]])
    with pytest.raises(OfflineAnalysisError, match="ordered"):
        summarize_decodes([decoder_documents[1], decoder_documents[0], decoder_documents[2]])

    audio_path = tmp_path / "audio-0.json"
    wav_path = output / "20260811T120200Z.wav"
    original_audio = json.loads(audio_path.read_text(encoding="utf-8"))
    mutations = (
        ("center_frequency_hz", 10_001.0),
        ("selected_frequency_hz", 10_101.0),
        ("target_audio_hz", 1_499.0),
        ("input_start_sample", 5_001),
        ("normalization_scale", original_audio["contract"]["normalization_scale"] + 0.01),
    )
    for index, (field, value) in enumerate(mutations):
        changed = json.loads(json.dumps(original_audio))
        changed["contract"][field] = value
        changed_path = tmp_path / f"changed-audio-{index}.json"
        changed_path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(OfflineAnalysisError):
            load_audio_evidence(changed_path, wav_path)

    for index, (section, field, value) in enumerate(
        (
            ("capture", "retained_capture_start_utc", "2026-08-11T12:01:49Z"),
            ("capture", "retained_sample_count", 369_999),
            ("profiles", "resolved", None),
        )
    ):
        changed = json.loads(json.dumps(original_audio))
        if field == "resolved":
            changed[section][field] = value
        else:
            changed[section][field] = value
        changed_path = tmp_path / f"changed-context-{index}.json"
        changed_path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(OfflineAnalysisError):
            load_audio_evidence(changed_path, wav_path)

    changed = json.loads(json.dumps(original_audio))
    changed["conjugate_policy"] = "different policy"
    changed_path = tmp_path / "changed-policy.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError):
        load_audio_evidence(changed_path, wav_path)

    original_wav = wav_path.read_bytes()
    replaced = bytearray(original_wav)
    replaced[-1] ^= 1
    wav_path.write_bytes(replaced)
    changed = json.loads(json.dumps(original_audio))
    changed["output"] = artifact(wav_path)
    changed_path = tmp_path / "changed-pcm.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="contradicts"):
        load_audio_evidence(changed_path, wav_path)
    wav_path.write_bytes(original_wav)

    missing_wav = tmp_path / "20260811T120200Z-missing.wav"
    with pytest.raises(OfflineAnalysisError) as missing_error:
        load_audio_evidence(audio_path, missing_wav)
    assert missing_error.value.cause.value == "incomplete_evidence"

    wav_path.write_bytes(b"not a wav")
    malformed = json.loads(json.dumps(original_audio))
    malformed["output"] = artifact(wav_path)
    malformed_path = tmp_path / "malformed-wav.json"
    malformed_path.write_text(json.dumps(malformed), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError) as malformed_error:
        load_audio_evidence(malformed_path, wav_path)
    assert malformed_error.value.cause.value == "incomplete_evidence"
    wav_path.write_bytes(original_wav)

    original_profile = test.read_text(encoding="utf-8")
    changed_profile = json.loads(original_profile)
    changed_profile["identity"]["callsign"] = "N0CALL"
    test.write_text(json.dumps(changed_profile), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError):
        load_audio_evidence(audio_path, wav_path)
    test.write_text(original_profile, encoding="utf-8")

    decoder_path = decoder_documents[0]
    decoder = json.loads(decoder_path.read_text(encoding="utf-8"))
    changed_decoder = json.loads(json.dumps(decoder))
    changed_decoder["arguments"][2] = str(tmp_path / "other data")
    changed_path = tmp_path / "changed-arguments.json"
    changed_path.write_text(json.dumps(changed_decoder), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="semantics"):
        load_decoder_evidence(changed_path)

    changed_decoder = json.loads(json.dumps(decoder))
    changed_decoder["malformed_candidate_lines"] = [{"line_number": 1, "raw": "0000 bad"}]
    changed_path = tmp_path / "changed-malformed.json"
    changed_path.write_text(json.dumps(changed_decoder), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="semantics"):
        load_decoder_evidence(changed_path)

    changed_decoder = json.loads(json.dumps(decoder))
    changed_decoder["tool"]["version_query"]["arguments"][-1] = "--help"
    changed_path = tmp_path / "changed-version.json"
    changed_path.write_text(json.dumps(changed_decoder), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="semantics"):
        load_decoder_evidence(changed_path)

    changed_decoder = json.loads(json.dumps(decoder))
    changed_decoder["tool"]["version_query"]["stdout"] = "altered"
    changed_path = tmp_path / "changed-version-output.json"
    changed_path.write_text(json.dumps(changed_decoder), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="semantics"):
        load_decoder_evidence(changed_path)

    for field in ("expected_identity_found", "expected_intended_signal_found"):
        changed_decoder = json.loads(json.dumps(decoder))
        changed_decoder[field] = not changed_decoder[field]
        changed_path = tmp_path / f"changed-{field}.json"
        changed_path.write_text(json.dumps(changed_decoder), encoding="utf-8")
        with pytest.raises(OfflineAnalysisError, match="semantics"):
            load_decoder_evidence(changed_path)

    data_directory = Path(decoder["decoder_data_directory"])
    extra = data_directory / "unrecorded file.txt"
    extra.write_text("unexpected", encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="inventory"):
        load_decoder_evidence(decoder_path)
    decoder_with_file = json.loads(json.dumps(decoder))
    decoder_with_file["decoder_data_artifacts"].append(artifact(extra))
    recorded_path = tmp_path / "decoder-recording-extra.json"
    recorded_path.write_text(json.dumps(decoder_with_file), encoding="utf-8")
    load_decoder_evidence(recorded_path)
    outside = json.loads(json.dumps(decoder_with_file))
    outside["decoder_data_artifacts"][0] = artifact(wav_path)
    outside_path = tmp_path / "decoder-outside-artifact.json"
    outside_path.write_text(json.dumps(outside), encoding="utf-8")
    with pytest.raises(OfflineAnalysisError, match="artifact changed"):
        load_decoder_evidence(outside_path)
    extra.unlink()

    for record in (original_audio["input"], original_audio["output"], original_audio["capture"]):
        assert Path(record["path"]).is_absolute()
    previous = Path.cwd()
    try:
        import os

        os.chdir(output)
        load_audio_evidence(audio_path, wav_path)
    finally:
        os.chdir(previous)
