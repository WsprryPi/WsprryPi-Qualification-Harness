import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

import wsprrypi_qualification.audio as audio_module
from wsprrypi_qualification.audio import (
    AudioParameters,
    _resample_mixed,
    create_slot_wav,
    slot_wav_name,
)
from wsprrypi_qualification.offline import OfflineAnalysisError


def test_slot_wav_timestamp_format_and_conversion_with_spaces(tmp_path: Path) -> None:
    rate = 1000
    capture_start = datetime(2026, 12, 31, 23, 58, tzinfo=UTC)
    slot = datetime(2027, 1, 1, 0, 0, tzinfo=UTC)
    count = rate * 250
    n = np.arange(count)
    iq = (0.1 * np.exp(2j * np.pi * 100 * n / rate)).astype("<c8")
    source = tmp_path / "input with spaces.cf32"
    iq.tofile(source)
    wav = tmp_path / "slot with spaces.wav"
    evidence = tmp_path / "audio evidence.json"
    result = create_slot_wav(
        source,
        capture_start,
        slot,
        wav,
        evidence,
        AudioParameters(rate, 10_000, 10_100, output_rate_hz=200, target_audio_hz=50),
    )
    assert slot_wav_name(slot) == "20270101T000000Z.wav"
    with wave.open(str(wav), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 200
        assert handle.getnframes() == 24_000
    assert result["contract"]["input_start_sample"] == 120_000
    assert result["contract"]["mix_hz"] == 50
    with wave.open(str(wav), "rb") as handle:
        pcm = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(float)
    spectrum = np.abs(np.fft.rfft(pcm * np.hanning(len(pcm))))
    peak_hz = np.argmax(spectrum) * 200 / len(pcm)
    assert peak_hz == pytest.approx(50, abs=0.05)


def test_slot_requires_complete_margin_and_even_boundary(tmp_path: Path) -> None:
    source = tmp_path / "short.cf32"
    np.zeros(1000, dtype="<c8").tofile(source)
    parameters = AudioParameters(1000, 10_000, 10_100, output_rate_hz=200)
    with pytest.raises(OfflineAnalysisError, match="complete"):
        create_slot_wav(
            source,
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
            tmp_path / "x.wav",
            tmp_path / "x.json",
            parameters,
        )
    with pytest.raises(OfflineAnalysisError, match="even"):
        create_slot_wav(
            source,
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            tmp_path / "y.wav",
            tmp_path / "y.json",
            parameters,
        )


def test_slot_requires_pre_margin_but_not_an_extra_post_margin(tmp_path: Path) -> None:
    rate = 1000
    slot = datetime(2026, 1, 1, tzinfo=UTC)
    source = tmp_path / "exact-window.cf32"
    np.zeros(rate * 125, dtype="<c8").tofile(source)
    result = create_slot_wav(
        source,
        slot - timedelta(seconds=5),
        slot,
        tmp_path / "slot.wav",
        tmp_path / "slot.json",
        AudioParameters(rate, 10_000, 10_100, output_rate_hz=200),
    )
    assert result["contract"]["required_margin_s"] == 5
    assert (
        result["contract"]["margin_policy"]
        == "required_before_slot_complete_frame_required_after_start"
    )


def test_translation_below_center_and_transactional_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rate = 1000
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    slot = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
    n = np.arange(rate * 250)
    source = tmp_path / "below.cf32"
    np.asarray(0.2 * np.exp(-2j * np.pi * 100 * n / rate), dtype="<c8").tofile(source)
    wav = tmp_path / "20260101T000200Z.wav"
    evidence = tmp_path / "evidence.json"
    create_slot_wav(
        source,
        start,
        slot,
        wav,
        evidence,
        AudioParameters(rate, 10_000, 9_900, output_rate_hz=200, target_audio_hz=50),
    )
    with wave.open(str(wav), "rb") as handle:
        pcm = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(float)
    peak_hz = np.argmax(np.abs(np.fft.rfft(pcm * np.hanning(len(pcm))))) * 200 / len(pcm)
    assert peak_hz == pytest.approx(50, abs=0.05)

    second_wav = tmp_path / "rollback.wav"
    monkeypatch.setattr(
        audio_module,
        "write_json_new",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected evidence failure")),
    )
    with pytest.raises(OSError, match="injected"):
        create_slot_wav(
            source,
            start,
            slot,
            second_wav,
            tmp_path / "rollback.json",
            AudioParameters(rate, 10_000, 9_900, output_rate_hz=200, target_audio_hz=50),
        )
    assert not second_wav.exists()
    assert not list(tmp_path.glob("*.incomplete-*"))


def test_resampler_suppresses_out_of_band_alias_and_is_deterministic() -> None:
    rate, output_rate, count = 1000, 200, 120_000
    n = np.arange(count)
    iq = np.asarray(
        0.1 * np.exp(2j * np.pi * 100 * n / rate) + 0.1 * np.exp(2j * np.pi * 350 * n / rate),
        dtype="<c8",
    )
    parameters = AudioParameters(
        rate, 10_000, 10_100, output_rate_hz=output_rate, target_audio_hz=50
    )
    first = _resample_mixed(iq, 0, count, parameters)
    second = _resample_mixed(iq, 0, count, parameters)
    assert np.array_equal(first, second)
    spectrum = np.abs(np.fft.rfft(first * np.hanning(len(first))))
    wanted = spectrum[round(50 * len(first) / output_rate)]
    aliased = spectrum[round(100 * len(first) / output_rate)]
    assert 20 * np.log10(aliased / wanted) < -60
