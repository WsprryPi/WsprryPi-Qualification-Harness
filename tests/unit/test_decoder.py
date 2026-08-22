import subprocess
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wsprrypi_qualification import decoder as decoder_module
from wsprrypi_qualification.decoder import parse_wsprd_output, run_wsprd, summarize_decodes
from wsprrypi_qualification.models import WsprIdentity
from wsprrypi_qualification.offline import OfflineAnalysisError


def write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(12_000)
        handle.writeframes(b"\0\0" * 120)


def test_parse_preserves_line_and_exact_identity(tmp_path: Path) -> None:
    line = "0128 -18 -0.8   0.001500  0  Q0QQQ JJ00 0 "
    decoded = parse_wsprd_output("diagnostic\n" + line + "\n")
    assert decoded[0]["line_number"] == 2
    assert decoded[0]["raw"] == line
    assert decoded[0]["callsign"] == "Q0QQQ"


def test_parse_accepts_token_from_canonical_iso_wav_name() -> None:
    decoded = parse_wsprd_output("200Z -26 0.4 0.001402 0 Q0QQQ JJ00 0\n")
    assert decoded[0]["decoder_time_token"] == "200Z"
    assert decoded[0]["callsign"] == "Q0QQQ"


def test_decoder_time_token_is_opaque_and_never_authenticates_slot() -> None:
    identity = WsprIdentity("Q0QQQ", "JJ00", 0)
    for token in ("200Z", "999Z", "000Z"):
        decoded = parse_wsprd_output(f"{token} -26 0.4 0.001500 0 Q0QQQ JJ00 0\n")
        assert decoded[0]["decoder_time_token"] == token
        assert decoder_module._classify_expected_decodes(
            decoded,
            identity,
            datetime(2026, 8, 13, 20, 42, tzinfo=UTC),
            1500.0,
            100.0,
        ) == (True, True)


def test_fake_wsprd_success_wrong_identity_and_complete_logs(tmp_path: Path) -> None:
    wav = tmp_path / "slot with spaces.wav"
    write_wav(wav)
    good_script = tmp_path / "fake decoder.py"
    good_script.write_text(
        "import sys\n"
        "print('diagnostic retained')\n"
        "print('0128 -18 -0.8 0.001500 0 Q0QQQ JJ00 0')\n"
        "print('warning retained', file=sys.stderr)\n",
        encoding="utf-8",
    )
    identity = WsprIdentity("Q0QQQ", "JJ00", 0)
    good = run_wsprd(
        wav,
        tmp_path / "good.json",
        identity,
        executable=Path(sys.executable),
        extra_arguments=(str(good_script),),
    )
    assert good["gate_outcome"] == "passed"
    assert "diagnostic retained" in good["stdout"] and "warning retained" in good["stderr"]
    assert good["decodes"][0]["signal_role"] == "intended"
    wrong_slot = run_wsprd(
        wav,
        tmp_path / "wrong-slot.json",
        identity,
        executable=Path(sys.executable),
        extra_arguments=(str(good_script),),
        slot_utc=datetime(2026, 1, 1, 1, 30, tzinfo=UTC),
    )
    assert wrong_slot["gate_outcome"] == "passed"
    assert wrong_slot["slot_utc"] == "2026-01-01T01:30:00Z"
    assert wrong_slot["decodes"][0]["decoder_time_token"] == "0128"
    wrong = run_wsprd(
        wav,
        tmp_path / "wrong.json",
        WsprIdentity("N0CALL", "JJ00", 0),
        executable=Path(sys.executable),
        extra_arguments=(str(good_script),),
    )
    assert wrong["gate_outcome"] == "failed"


def test_decode_summary_rejects_unvalidated_caller_dictionaries() -> None:
    with pytest.raises(OfflineAnalysisError, match="file paths"):
        summarize_decodes([{"gate_outcome": "passed"}])  # type: ignore[list-item]


def test_fake_wsprd_nonzero_and_timeout_are_blocked(tmp_path: Path) -> None:
    wav = tmp_path / "slot.wav"
    write_wav(wav)
    failing = tmp_path / "fail.py"
    failing.write_text("import sys\nprint('kept failure')\nsys.exit(7)\n", encoding="utf-8")
    identity = WsprIdentity("Q0QQQ", "JJ00", 0)
    result = run_wsprd(
        wav,
        tmp_path / "failure.json",
        identity,
        executable=Path(sys.executable),
        extra_arguments=(str(failing),),
    )
    assert result["gate_outcome"] == "blocked"
    assert result["return_code"] == 7 and "kept failure" in result["stdout"]
    sleeping = tmp_path / "sleep.py"
    sleeping.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
    timeout = run_wsprd(
        wav,
        tmp_path / "timeout.json",
        identity,
        executable=Path(sys.executable),
        extra_arguments=(str(sleeping),),
        timeout_s=0.05,
    )
    assert timeout["gate_outcome"] == "blocked" and timeout["timed_out"] is True


def test_decoder_preflights_all_destinations_before_execution(tmp_path: Path) -> None:
    wav = tmp_path / "slot.wav"
    write_wav(wav)
    evidence = tmp_path / "existing.json"
    evidence.write_text("do not replace", encoding="utf-8")
    marker = tmp_path / "would-run"
    script = tmp_path / "marker.py"
    script.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8"
    )
    with pytest.raises(OfflineAnalysisError, match="overwrite"):
        run_wsprd(
            wav,
            evidence,
            WsprIdentity("Q0QQQ", "JJ00", 0),
            executable=Path(sys.executable),
            extra_arguments=(str(script),),
        )
    assert not marker.exists()


def test_decoder_rolls_back_directory_on_launch_and_publication_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wav = tmp_path / "slot.wav"
    write_wav(wav)
    identity = WsprIdentity("Q0QQQ", "JJ00", 0)

    def version_then_launch_error(arguments, **_kwargs):
        if arguments[-1] == "--version":
            return subprocess.CompletedProcess(arguments, 0, "fake 1.0\n", "")
        raise OSError("main launch failed")

    monkeypatch.setattr(decoder_module.subprocess, "run", version_then_launch_error)
    data = tmp_path / "launch-data"
    with pytest.raises(OfflineAnalysisError, match="could not be launched"):
        run_wsprd(
            wav,
            tmp_path / "launch.json",
            identity,
            executable=Path(sys.executable),
            data_directory=data,
        )
    assert not data.exists()

    def version_then_unexpected_process_error(arguments, **_kwargs):
        if arguments[-1] == "--version":
            return subprocess.CompletedProcess(arguments, 0, "fake 1.0\n", "")
        raise RuntimeError("unexpected process runner failure")

    monkeypatch.setattr(decoder_module.subprocess, "run", version_then_unexpected_process_error)
    unexpected_process_data = tmp_path / "unexpected-process-data"
    unexpected_process_evidence = tmp_path / "unexpected-process.json"
    with pytest.raises(OfflineAnalysisError, match="failed unexpectedly") as process_error:
        run_wsprd(
            wav,
            unexpected_process_evidence,
            identity,
            executable=Path(sys.executable),
            data_directory=unexpected_process_data,
        )
    assert process_error.value.cause.value == "decoder_failure"
    assert not unexpected_process_data.exists() and not unexpected_process_evidence.exists()

    monkeypatch.setattr(decoder_module.subprocess, "run", version_then_launch_error)
    real_resolve = Path.resolve
    argument_data = tmp_path / "argument-data"

    def unexpectedly_failing_resolve(path, *args, **kwargs):
        if path == argument_data:
            raise RuntimeError("unexpected argument resolution failure")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", unexpectedly_failing_resolve)
    argument_evidence = tmp_path / "argument.json"
    with pytest.raises(
        OfflineAnalysisError, match="arguments could not be resolved"
    ) as argument_error:
        run_wsprd(
            wav,
            argument_evidence,
            identity,
            executable=Path(sys.executable),
            data_directory=argument_data,
        )
    assert argument_error.value.cause.value == "decoder_failure"
    assert not argument_data.exists() and not argument_evidence.exists()
    monkeypatch.setattr(Path, "resolve", real_resolve)

    monkeypatch.setattr(
        decoder_module.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("version launch failed")),
    )
    version_data = tmp_path / "version-data"
    with pytest.raises(OfflineAnalysisError, match="version query"):
        run_wsprd(
            wav,
            tmp_path / "version.json",
            identity,
            executable=Path(sys.executable),
            data_directory=version_data,
        )
    assert not version_data.exists()

    def successful_process(arguments, **_kwargs):
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(decoder_module.subprocess, "run", successful_process)
    real_parser = decoder_module.parse_wsprd_output
    monkeypatch.setattr(
        decoder_module,
        "parse_wsprd_output",
        lambda _text: (_ for _ in ()).throw(RuntimeError("injected parse failure")),
    )
    parse_data = tmp_path / "parse-data"
    with pytest.raises(OfflineAnalysisError, match="could not be processed") as parse_error:
        run_wsprd(
            wav,
            tmp_path / "parse.json",
            identity,
            executable=Path(sys.executable),
            data_directory=parse_data,
        )
    assert parse_error.value.cause.value == "decoder_failure"
    assert not parse_data.exists() and not (tmp_path / "parse.json").exists()
    monkeypatch.setattr(decoder_module, "parse_wsprd_output", real_parser)

    real_artifact = decoder_module.artifact

    def process_with_artifact(arguments, **_kwargs):
        if arguments[-1] != "--version":
            (Path(arguments[2]) / "created.txt").write_text("data", encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def failing_artifact(path):
        if path.name == "created.txt":
            raise OSError("inspection failed")
        return real_artifact(path)

    monkeypatch.setattr(decoder_module.subprocess, "run", process_with_artifact)
    monkeypatch.setattr(decoder_module, "artifact", failing_artifact)
    inspect_data = tmp_path / "inspect-data"
    with pytest.raises(OfflineAnalysisError, match="could not be inspected"):
        run_wsprd(
            wav,
            tmp_path / "inspect.json",
            identity,
            executable=Path(sys.executable),
            data_directory=inspect_data,
        )
    assert not inspect_data.exists()

    def unexpectedly_failing_artifact(path):
        if path.name == "created.txt":
            raise RuntimeError("unexpected inspection failure")
        return real_artifact(path)

    monkeypatch.setattr(decoder_module, "artifact", unexpectedly_failing_artifact)
    unexpected_inspect_data = tmp_path / "unexpected-inspect-data"
    unexpected_evidence = tmp_path / "unexpected-inspect.json"
    with pytest.raises(OfflineAnalysisError, match="could not be inspected") as inspect_error:
        run_wsprd(
            wav,
            unexpected_evidence,
            identity,
            executable=Path(sys.executable),
            data_directory=unexpected_inspect_data,
        )
    assert inspect_error.value.cause.value == "decoder_failure"
    assert not unexpected_inspect_data.exists() and not unexpected_evidence.exists()

    monkeypatch.setattr(decoder_module.subprocess, "run", successful_process)
    monkeypatch.setattr(decoder_module, "artifact", real_artifact)
    real_builder = decoder_module._build_decoder_document
    monkeypatch.setattr(
        decoder_module,
        "_build_decoder_document",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("construction failed")),
    )
    construction_data = tmp_path / "construction-data"
    with pytest.raises(OfflineAnalysisError, match="could not be published") as build_error:
        run_wsprd(
            wav,
            tmp_path / "construction.json",
            identity,
            executable=Path(sys.executable),
            data_directory=construction_data,
        )
    assert build_error.value.cause.value == "decoder_failure"
    assert not construction_data.exists() and not (tmp_path / "construction.json").exists()
    monkeypatch.setattr(decoder_module, "_build_decoder_document", real_builder)

    def failed_publication(path, *_args, **_kwargs):
        path.write_text("partial", encoding="utf-8")
        raise OSError("publication failed")

    monkeypatch.setattr(decoder_module, "write_json_new", failed_publication)
    publish_data = tmp_path / "publish-data"
    with pytest.raises(OfflineAnalysisError, match="could not be published") as publish_error:
        run_wsprd(
            wav,
            tmp_path / "publication.json",
            identity,
            executable=Path(sys.executable),
            data_directory=publish_data,
        )
    assert publish_error.value.cause.value == "filesystem_failure"
    assert not publish_data.exists() and not (tmp_path / "publication.json").exists()


def test_decoder_rejects_malformed_wav_before_directory_creation(tmp_path: Path) -> None:
    wav = tmp_path / "bad.wav"
    wav.write_bytes(b"not a wav")
    data = tmp_path / "bad-data"
    with pytest.raises(OfflineAnalysisError, match="unreadable"):
        run_wsprd(
            wav,
            tmp_path / "bad.json",
            WsprIdentity("Q0QQQ", "JJ00", 0),
            executable=Path(sys.executable),
            data_directory=data,
        )
    assert not data.exists()


def test_decoder_rejects_invalid_slots_before_directory_creation(tmp_path: Path) -> None:
    wav = tmp_path / "slot.wav"
    write_wav(wav)
    identity = WsprIdentity("Q0QQQ", "JJ00", 0)
    naive_data = tmp_path / "naive-data"
    with pytest.raises(OfflineAnalysisError, match="UTC slot is invalid"):
        run_wsprd(
            wav,
            tmp_path / "naive.json",
            identity,
            executable=Path(sys.executable),
            slot_utc=datetime(2026, 1, 1, 0, 0),
            data_directory=naive_data,
        )
    assert not naive_data.exists() and not (tmp_path / "naive.json").exists()

    odd_data = tmp_path / "odd-data"
    with pytest.raises(OfflineAnalysisError, match="even UTC"):
        run_wsprd(
            wav,
            tmp_path / "odd.json",
            identity,
            executable=Path(sys.executable),
            slot_utc=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            audio_evidence={"present": True},
            data_directory=odd_data,
        )
    assert not odd_data.exists() and not (tmp_path / "odd.json").exists()
