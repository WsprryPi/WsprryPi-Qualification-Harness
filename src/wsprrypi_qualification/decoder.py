"""Bounded, structured invocation and exact-identity parsing for WSJT-X wsprd."""

from __future__ import annotations

import re
import shutil
import subprocess
import wave
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from wsprrypi_qualification.audio import (
    CONJUGATE_POLICY,
    AudioParameters,
    render_slot_pcm,
)
from wsprrypi_qualification.cf32 import inspect_cf32
from wsprrypi_qualification.models import WsprIdentity
from wsprrypi_qualification.offline import (
    FailureCause,
    OfflineAnalysisError,
    artifact,
    load_json_document,
    require_new_file,
    sha256_file,
    write_json_new,
)
from wsprrypi_qualification.offline_context import load_profile_context, validate_acquired_capture
from wsprrypi_qualification.timing import (
    is_even_wspr_slot,
    require_aware_utc,
    sample_index_at_utc,
)
from wsprrypi_qualification.tool_discovery import discover_executable

DECODE_RE = re.compile(
    r"^\s*(?P<utc>\d{4}|\d{3}Z)\s+(?P<snr>[+-]?\d+)\s+(?P<dt>[+-]?\d+(?:\.\d+)?)\s+"
    r"(?P<frequency>\d+(?:\.\d+)?)\s+(?P<drift>[+-]?\d+)\s+"
    r"(?P<callsign>[A-Z0-9/]{3,12})\s+(?P<grid>[A-R]{2}\d{2}(?:[A-X]{2})?)\s+(?P<power>[+-]?\d+)\s*$"
)


@dataclass(frozen=True)
class AcquiredAudioEvidence:
    document: dict[str, Any]
    slot_utc: datetime
    identity: WsprIdentity
    required_decodes: int
    frame_count: int


@dataclass(frozen=True)
class AcquiredDecoderEvidence:
    path: Path
    document: dict[str, Any]
    slot_utc: datetime
    required_decodes: int
    frame_count: int


def discover_wsprd(explicit: Path | None = None) -> Path:
    if explicit is not None:
        if not explicit.is_file():
            raise OfflineAnalysisError(
                f"wsprd executable is unavailable: {explicit}",
                cause=FailureCause.DEPENDENCY_UNAVAILABLE,
                gate_outcome="blocked",
            )
        return explicit.resolve()
    found = discover_executable("wsprd")
    if found is None:
        raise OfflineAnalysisError(
            "wsprd dependency is unavailable on PATH and supported platform bundle locations",
            cause=FailureCause.DEPENDENCY_UNAVAILABLE,
            gate_outcome="blocked",
        )
    return found


def parse_wsprd_output(text: str) -> list[dict[str, Any]]:
    decoded: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        match = DECODE_RE.match(line)
        if match:
            values = match.groupdict()
            decoded.append(
                {
                    "line_number": line_number,
                    "raw": line,
                    "decoder_time_token": values["utc"],
                    "snr_db": int(values["snr"]),
                    "dt_s": float(values["dt"]),
                    "frequency_mhz": float(values["frequency"]),
                    "drift_hz_per_min": int(values["drift"]),
                    "callsign": values["callsign"],
                    "grid": values["grid"],
                    "power_dbm": int(values["power"]),
                }
            )
    return decoded


def malformed_wsprd_lines(text: str) -> list[dict[str, Any]]:
    return [
        {"line_number": number, "raw": line}
        for number, line in enumerate(text.splitlines(), 1)
        if line.strip() and line[:4].strip().isdigit() and not DECODE_RE.match(line)
    ]


def _classify_expected_decodes(
    decodes: list[dict[str, Any]],
    identity: WsprIdentity,
    slot: datetime | None,
    target_audio_hz: float,
    image_tolerance_hz: float,
) -> tuple[bool, bool]:
    del slot
    identity_found = False
    intended_found = False
    for item in decodes:
        audio_hz = item["frequency_mhz"] * 1_000_000
        intended = abs(audio_hz - target_audio_hz) <= image_tolerance_hz
        item["signal_role"] = "intended" if intended else "companion_or_conjugate_image"
        expected = (
            item["callsign"] == identity.callsign
            and item["grid"] == identity.grid
            and item["power_dbm"] == identity.power_dbm
        )
        identity_found = identity_found or expected
        intended_found = intended_found or (expected and intended)
    return identity_found, intended_found


def run_wsprd(
    wav_path: Path,
    evidence_path: Path,
    identity: WsprIdentity,
    *,
    executable: Path | None = None,
    timeout_s: float = 60.0,
    extra_arguments: tuple[str, ...] = (),
    target_audio_hz: float = 1500.0,
    image_tolerance_hz: float = 100.0,
    slot_utc: datetime | None = None,
    profile_evidence: dict[str, Any] | None = None,
    capture_evidence: dict[str, Any] | None = None,
    audio_evidence: dict[str, Any] | None = None,
    data_directory: Path | None = None,
) -> dict[str, Any]:
    require_new_file(evidence_path)
    if timeout_s <= 0:
        raise OfflineAnalysisError(
            "decoder timeout must be positive", cause=FailureCause.INVALID_ARGUMENTS
        )
    if not all(isinstance(argument, str) for argument in extra_arguments):
        raise OfflineAnalysisError(
            "decoder arguments must be strings", cause=FailureCause.INVALID_ARGUMENTS
        )
    try:
        slot = require_aware_utc(slot_utc) if slot_utc is not None else None
    except ValueError as error:
        raise OfflineAnalysisError(
            f"decoder UTC slot is invalid: {error}", cause=FailureCause.INVALID_ARGUMENTS
        ) from error
    if audio_evidence is not None and (slot is None or not is_even_wspr_slot(slot)):
        raise OfflineAnalysisError(
            "acquired decoder slot must be an even UTC boundary",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
    if data_directory is not None:
        if data_directory.exists():
            raise OfflineAnalysisError(
                f"refusing to reuse decoder data directory: {data_directory}",
                cause=FailureCause.OUTPUT_CONFLICT,
            )
        if not data_directory.parent.is_dir():
            raise OfflineAnalysisError(
                f"decoder data parent does not exist: {data_directory.parent}",
                cause=FailureCause.FILESYSTEM_FAILURE,
            )
    tool = discover_wsprd(executable)
    version = _query_version(tool)
    if version["launch_error"] is not None:
        raise OfflineAnalysisError(
            "decoder version query could not be launched",
            cause=FailureCause.DEPENDENCY_UNAVAILABLE,
            gate_outcome="blocked",
        )
    try:
        with wave.open(str(wav_path), "rb") as wav:
            wav_format = {
                "channels": wav.getnchannels(),
                "sample_width_bytes": wav.getsampwidth(),
                "sample_rate_hz": wav.getframerate(),
                "frame_count": wav.getnframes(),
            }
    except (OSError, wave.Error) as error:
        raise OfflineAnalysisError(
            f"decoder WAV is unreadable: {error}", cause=FailureCause.INVALID_FIXTURE
        ) from error
    if data_directory is not None:
        data_directory.mkdir()
    try:
        data_arguments = ("-a", str(data_directory.resolve())) if data_directory is not None else ()
        arguments = [str(tool), *data_arguments, *extra_arguments, str(wav_path.resolve())]
    except Exception as error:
        evidence_path.unlink(missing_ok=True)
        _rollback_decoder_directory(data_directory)
        raise OfflineAnalysisError(
            f"decoder arguments could not be resolved: {error}",
            cause=(
                FailureCause.FILESYSTEM_FAILURE
                if isinstance(error, OSError)
                else FailureCause.DECODER_FAILURE
            ),
            gate_outcome="blocked",
        ) from error
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
            shell=False,
        )
        timed_out = False
        stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as error:
        timed_out = True
        stdout = error.stdout if isinstance(error.stdout, str) else ""
        stderr = error.stderr if isinstance(error.stderr, str) else ""
        returncode = None
    except OSError as error:
        evidence_path.unlink(missing_ok=True)
        _rollback_decoder_directory(data_directory)
        raise OfflineAnalysisError(
            f"decoder process could not be launched: {error}",
            cause=FailureCause.DECODER_FAILURE,
            gate_outcome="blocked",
        ) from error
    except Exception as error:
        evidence_path.unlink(missing_ok=True)
        _rollback_decoder_directory(data_directory)
        raise OfflineAnalysisError(
            f"decoder process failed unexpectedly: {error}",
            cause=FailureCause.DECODER_FAILURE,
            gate_outcome="blocked",
        ) from error
    try:
        decodes = parse_wsprd_output(stdout)
        identity_found, intended_found = _classify_expected_decodes(
            decodes, identity, slot, target_audio_hz, image_tolerance_hz
        )
        passed = intended_found and returncode == 0 and not timed_out
        malformed_lines = malformed_wsprd_lines(stdout)
    except Exception as error:
        evidence_path.unlink(missing_ok=True)
        _rollback_decoder_directory(data_directory)
        raise OfflineAnalysisError(
            f"decoder output could not be processed: {error}",
            cause=FailureCause.DECODER_FAILURE,
            gate_outcome="blocked",
        ) from error
    try:
        wav_artifact = artifact(wav_path)
        tool_hash = sha256_file(tool)
        if data_directory is not None and any(
            not path.is_file() for path in data_directory.iterdir()
        ):
            raise OSError("decoder data directory contains a non-file entry")
        data_artifacts = (
            [artifact(path) for path in sorted(data_directory.iterdir()) if path.is_file()]
            if data_directory is not None
            else []
        )
    except Exception as error:
        evidence_path.unlink(missing_ok=True)
        _rollback_decoder_directory(data_directory)
        raise OfflineAnalysisError(
            f"decoder artifacts could not be inspected: {error}",
            cause=(
                FailureCause.FILESYSTEM_FAILURE
                if isinstance(error, (OSError, ValueError))
                else FailureCause.DECODER_FAILURE
            ),
            gate_outcome="blocked",
        ) from error
    try:
        document = _build_decoder_document(
            wav_artifact=wav_artifact,
            slot=slot,
            wav_path=wav_path,
            wav_format=wav_format,
            tool=tool,
            tool_hash=tool_hash,
            version=version,
            arguments=arguments,
            timeout_s=timeout_s,
            timed_out=timed_out,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            decodes=decodes,
            malformed_lines=malformed_lines,
            identity=identity,
            identity_found=identity_found,
            intended_found=intended_found,
            passed=passed,
            profile_evidence=profile_evidence,
            capture_evidence=capture_evidence,
            audio_evidence=audio_evidence,
            data_directory=data_directory,
            data_artifacts=data_artifacts,
        )
        write_json_new(
            evidence_path,
            document,
            schema_name="decoder-evidence.schema.json" if audio_evidence is not None else None,
        )
    except OfflineAnalysisError:
        evidence_path.unlink(missing_ok=True)
        _rollback_decoder_directory(data_directory)
        raise
    except Exception as error:
        evidence_path.unlink(missing_ok=True)
        _rollback_decoder_directory(data_directory)
        raise OfflineAnalysisError(
            f"decoder evidence could not be published: {error}",
            cause=(
                FailureCause.FILESYSTEM_FAILURE
                if isinstance(error, OSError)
                else FailureCause.DECODER_FAILURE
            ),
            gate_outcome="blocked",
        ) from error
    return document


def _rollback_decoder_directory(data_directory: Path | None) -> None:
    if data_directory is not None and data_directory.exists():
        shutil.rmtree(data_directory)


def _build_decoder_document(
    *,
    wav_artifact: dict[str, Any],
    slot: datetime | None,
    wav_path: Path,
    wav_format: dict[str, int],
    tool: Path,
    tool_hash: str,
    version: dict[str, Any],
    arguments: list[str],
    timeout_s: float,
    timed_out: bool,
    returncode: int | None,
    stdout: str,
    stderr: str,
    decodes: list[dict[str, Any]],
    malformed_lines: list[dict[str, Any]],
    identity: WsprIdentity,
    identity_found: bool,
    intended_found: bool,
    passed: bool,
    profile_evidence: dict[str, Any] | None,
    capture_evidence: dict[str, Any] | None,
    audio_evidence: dict[str, Any] | None,
    data_directory: Path | None,
    data_artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evidence_type": "wsprd_execution",
        "wav": wav_artifact,
        "slot_utc": slot.isoformat().replace("+00:00", "Z") if slot is not None else None,
        "wav_filename": wav_path.name,
        "wav_format": wav_format,
        "tool": {"path": str(tool), "sha256": tool_hash, "version_query": version},
        "arguments": arguments,
        "timeout_s": timeout_s,
        "timed_out": timed_out,
        "return_code": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "decodes": decodes,
        "malformed_candidate_lines": malformed_lines,
        "expected_identity": asdict(identity),
        "expected_identity_found": identity_found,
        "expected_intended_signal_found": intended_found,
        "gate_outcome": "passed"
        if passed
        else ("blocked" if timed_out or returncode not in (0, None) else "failed"),
        "failure_causes": (
            []
            if passed
            else [
                "decoder_timeout"
                if timed_out
                else "decoder_nonzero_return"
                if returncode not in (0, None)
                else "malformed_decoder_output"
                if malformed_lines
                else "expected_intended_signal_missing"
                if identity_found
                else "expected_identity_missing"
            ]
        ),
        "profiles": profile_evidence,
        "capture": capture_evidence,
        "audio_evidence": audio_evidence,
        "decoder_data_directory": (
            str(data_directory.resolve()) if data_directory is not None else None
        ),
        "decoder_data_artifacts": data_artifacts,
    }


def _query_version(tool: Path) -> dict[str, Any]:
    arguments = [str(tool), "--version"]
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            shell=False,
        )
        record: dict[str, Any] = {
            "arguments": arguments,
            "return_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": False,
            "launch_error": None,
        }
    except subprocess.TimeoutExpired as error:
        record = {
            "arguments": arguments,
            "return_code": None,
            "stdout": error.stdout if isinstance(error.stdout, str) else "",
            "stderr": error.stderr if isinstance(error.stderr, str) else "",
            "timed_out": True,
            "launch_error": None,
        }
    except OSError as error:
        record = {
            "arguments": arguments,
            "return_code": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "launch_error": f"{type(error).__name__}: {error}",
        }
    return {**record, **_interpret_version_query(record)}


def _interpret_version_query(record: dict[str, Any]) -> dict[str, str | None]:
    if record["launch_error"] is not None:
        return {"version": None, "unavailable_reason": "version query launch failed"}
    if record["timed_out"]:
        return {"version": None, "unavailable_reason": "version query timed out"}
    combined = (record["stdout"] + "\n" + record["stderr"]).strip()
    if record["return_code"] == 0 and combined:
        return {"version": combined.splitlines()[0], "unavailable_reason": None}
    return {"version": None, "unavailable_reason": "version query unsupported or empty"}


def _same_artifact(record: dict[str, Any], path: Path) -> bool:
    actual = artifact(path)
    return (
        Path(record["path"]).resolve() == path.resolve()
        and record["size_bytes"] == actual["size_bytes"]
        and record["sha256"] == actual["sha256"]
    )


def load_audio_evidence(path: Path, wav_path: Path) -> AcquiredAudioEvidence:
    try:
        return _load_audio_evidence(path, wav_path)
    except OfflineAnalysisError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, wave.Error) as error:
        raise OfflineAnalysisError(
            f"audio evidence is incomplete or unreadable: {error}",
            cause=FailureCause.INCOMPLETE_EVIDENCE,
        ) from error


def _load_audio_evidence(path: Path, wav_path: Path) -> AcquiredAudioEvidence:
    document = load_json_document(path, "audio-conversion.schema.json")
    slot = datetime.fromisoformat(document["slot_utc"].replace("Z", "+00:00"))
    if not is_even_wspr_slot(slot):
        raise OfflineAnalysisError(
            "audio evidence slot is not an even UTC boundary",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
    canonical_wav = wav_path.resolve(strict=True)
    expected_name = slot.strftime("%Y%m%dT%H%M%SZ.wav")
    if canonical_wav.name != expected_name or not _same_artifact(document["output"], canonical_wav):
        raise OfflineAnalysisError(
            "WAV path, name, size, or hash differs from audio evidence",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
    contract = document["contract"]
    with wave.open(str(canonical_wav), "rb") as wav:
        actual_format = (
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
            wav.getnframes(),
        )
        retained_pcm = wav.readframes(wav.getnframes())
    expected_format = (1, 2, contract["output_rate_hz"], contract["output_sample_count"])
    if actual_format != expected_format:
        raise OfflineAnalysisError(
            "WAV header differs from audio conversion contract",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
    try:
        bench_path = Path(document["profiles"]["bench"]["path"])
        test_path = Path(document["profiles"]["test"]["path"])
        capture_path = Path(document["capture"]["path"])
        iq_path = Path(document["input"]["path"])
        context = load_profile_context(bench_path, test_path)
        metadata = validate_acquired_capture(capture_path, iq_path, context)
        inspection = inspect_cf32(iq_path, clipping_threshold=metadata.clipping_threshold)
    except (OSError, ValueError) as error:
        raise OfflineAnalysisError(
            f"retained audio input evidence is unavailable or invalid: {error}",
            cause=FailureCause.INCOMPLETE_EVIDENCE,
        ) from error

    expected_profiles = context.evidence()
    expected_input = {**asdict(inspection), "path": str(iq_path.resolve(strict=True))}
    expected_capture = {
        **artifact(capture_path),
        "capture_id": metadata.capture_id,
        "retained_capture_start_utc": metadata.retained_capture_start_utc.isoformat().replace(
            "+00:00", "Z"
        )
        if metadata.retained_capture_start_utc is not None
        else None,
        "retained_sample_count": metadata.retained_sample_count,
    }
    receiver = context.bench.receiver
    expected_start = (
        sample_index_at_utc(metadata.retained_capture_start_utc, slot, receiver.sample_rate_hz)
        if metadata.retained_capture_start_utc is not None
        else -1
    )
    expected_contract = {
        "sample_rate_hz": receiver.sample_rate_hz,
        "center_frequency_hz": context.test.receiver_center_hz,
        "selected_frequency_hz": context.test.frequency_hz,
        "output_rate_hz": 12_000,
        "target_audio_hz": 1_500.0,
        "frame_duration_s": 120,
        "filter_taps": 64,
        "required_margin_s": 5,
        "margin_policy": "required_before_slot_complete_frame_required_after_start",
        "mix_hz": (context.test.frequency_hz - context.test.receiver_center_hz) - 1_500.0,
        "mix_formula": "real(iq[n] * exp(-j*2*pi*mix_hz*n/fs))",
        "resampler": "windowed_sinc_hann",
        "wav_format": "mono_pcm_s16le",
        "input_start_sample": expected_start,
        "input_sample_count": receiver.sample_rate_hz * 120,
        "output_sample_count": 12_000 * 120,
    }
    margin = receiver.sample_rate_hz * 5
    parameters = AudioParameters(
        sample_rate_hz=receiver.sample_rate_hz,
        center_frequency_hz=context.test.receiver_center_hz,
        selected_frequency_hz=context.test.frequency_hz,
    )
    expected_pcm, expected_scale = render_slot_pcm(
        iq_path,
        expected_start,
        receiver.sample_rate_hz * 120,
        parameters,
    )
    contract_matches = all(contract.get(key) == value for key, value in expected_contract.items())
    if (
        document["profiles"] != expected_profiles
        or document["input"] != expected_input
        or document["capture"] != expected_capture
        or not contract_matches
        or contract.get("normalization_scale") != expected_scale
        or document["conjugate_policy"] != CONJUGATE_POLICY
        or retained_pcm != expected_pcm
        or expected_start < margin
        or expected_start + receiver.sample_rate_hz * 120 > metadata.retained_sample_count
        or metadata.requested_sample_count != metadata.retained_sample_count
    ):
        raise OfflineAnalysisError(
            "audio conversion evidence contradicts authenticated profiles or capture",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
    return AcquiredAudioEvidence(
        document,
        slot,
        context.test.identity,
        context.test.gates.required_consecutive_decodes,
        context.test.frame_count,
    )


def _recomputed_decoder_outcome(
    document: dict[str, Any], target_audio_hz: float, image_tolerance_hz: float
) -> tuple[str, list[str], list[dict[str, Any]], bool, bool]:
    expected = document["expected_identity"]
    slot = datetime.fromisoformat(document["slot_utc"].replace("Z", "+00:00"))
    decodes = parse_wsprd_output(document["stdout"])
    identity = WsprIdentity(expected["callsign"], expected["grid"], expected["power_dbm"])
    identity_found, intended_found = _classify_expected_decodes(
        decodes, identity, slot, target_audio_hz, image_tolerance_hz
    )
    if document["timed_out"]:
        return "blocked", ["decoder_timeout"], decodes, identity_found, intended_found
    if document["return_code"] != 0:
        return "blocked", ["decoder_nonzero_return"], decodes, identity_found, intended_found
    if intended_found:
        return "passed", [], decodes, identity_found, True
    return (
        "failed",
        [
            "malformed_decoder_output"
            if document["malformed_candidate_lines"]
            else "expected_intended_signal_missing"
            if identity_found
            else "expected_identity_missing"
        ],
        decodes,
        identity_found,
        False,
    )


def load_decoder_evidence(path: Path) -> AcquiredDecoderEvidence:
    try:
        return _load_decoder_evidence(path)
    except OfflineAnalysisError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, wave.Error) as error:
        raise OfflineAnalysisError(
            f"decoder evidence is incomplete or unreadable: {error}",
            cause=FailureCause.INCOMPLETE_EVIDENCE,
        ) from error


def _load_decoder_evidence(path: Path) -> AcquiredDecoderEvidence:
    document = load_json_document(path, "decoder-evidence.schema.json")
    slot = datetime.fromisoformat(document["slot_utc"].replace("Z", "+00:00"))
    wav_path = Path(document["wav"]["path"])
    if (
        not wav_path.is_file()
        or not _same_artifact(document["wav"], wav_path)
        or document["wav_filename"] != wav_path.name
    ):
        raise OfflineAnalysisError(
            "decoder WAV artifact changed", cause=FailureCause.INCOMPLETE_EVIDENCE
        )
    audio_context = document["audio_evidence"]
    audio_path = Path(audio_context["path"])
    if not audio_path.is_file() or not _same_artifact(audio_context, audio_path):
        raise OfflineAnalysisError(
            "decoder audio-evidence artifact changed", cause=FailureCause.INCOMPLETE_EVIDENCE
        )
    acquired = load_audio_evidence(audio_path, wav_path)
    tool_path = Path(document["tool"]["path"])
    if not tool_path.is_file() or sha256_file(tool_path) != document["tool"]["sha256"]:
        raise OfflineAnalysisError(
            "decoder executable artifact changed", cause=FailureCause.INCOMPLETE_EVIDENCE
        )
    data_directory = Path(document["decoder_data_directory"])
    expected_directory = path.resolve().parent / f"{wav_path.stem}-wsprd-data"
    if (
        not data_directory.is_dir()
        or data_directory.resolve() != expected_directory.resolve()
        or any(not item.is_file() for item in data_directory.iterdir())
    ):
        raise OfflineAnalysisError(
            "decoder data directory is unavailable or contradictory",
            cause=FailureCause.INCOMPLETE_EVIDENCE,
        )
    recorded_paths: set[Path] = set()
    for record in document["decoder_data_artifacts"]:
        artifact_path = Path(record["path"])
        if (
            not artifact_path.is_file()
            or artifact_path.resolve().parent != data_directory.resolve()
            or not _same_artifact(record, artifact_path)
        ):
            raise OfflineAnalysisError(
                "decoder data artifact changed", cause=FailureCause.INCOMPLETE_EVIDENCE
            )
        recorded_paths.add(artifact_path.resolve())
    actual_paths = {item.resolve() for item in data_directory.iterdir() if item.is_file()}
    if recorded_paths != actual_paths:
        raise OfflineAnalysisError(
            "decoder data artifact inventory is incomplete or contains foreign files",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
    target = acquired.document["contract"]["target_audio_hz"]
    outcome, causes, parsed_decodes, identity_found, intended_found = _recomputed_decoder_outcome(
        document, target, 100.0
    )
    with wave.open(str(wav_path), "rb") as wav:
        wav_format = {
            "channels": wav.getnchannels(),
            "sample_width_bytes": wav.getsampwidth(),
            "sample_rate_hz": wav.getframerate(),
            "frame_count": wav.getnframes(),
        }
    version_query = document["tool"]["version_query"]
    version_consistent = version_query["arguments"] == [str(tool_path), "--version"] and {
        "version": version_query["version"],
        "unavailable_reason": version_query["unavailable_reason"],
    } == _interpret_version_query(version_query)
    expected_arguments = [
        str(tool_path),
        "-a",
        str(data_directory.resolve()),
        str(wav_path.resolve()),
    ]
    if (
        acquired.slot_utc != slot
        or document["profiles"] != acquired.document["profiles"]
        or document["capture"] != acquired.document["capture"]
        or document["expected_identity"] != asdict(acquired.identity)
        or document["expected_identity_found"] != identity_found
        or document["expected_intended_signal_found"] != intended_found
        or document["wav_format"] != wav_format
        or document["gate_outcome"] != outcome
        or document["failure_causes"] != causes
        or document["decodes"] != parsed_decodes
        or document["malformed_candidate_lines"] != malformed_wsprd_lines(document["stdout"])
        or document["arguments"] != expected_arguments
        or not version_consistent
    ):
        raise OfflineAnalysisError(
            "decoder evidence semantics contradict retained inputs",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
    return AcquiredDecoderEvidence(
        path.resolve(strict=True),
        document,
        slot,
        acquired.required_decodes,
        acquired.frame_count,
    )


def summarize_decodes(
    slot_evidence_paths: list[Path], evidence_path: Path | None = None
) -> dict[str, Any]:
    if not all(isinstance(path, Path) for path in slot_evidence_paths):
        raise OfflineAnalysisError(
            "decode summary requires decoder evidence file paths",
            cause=FailureCause.INVALID_ARGUMENTS,
        )
    acquired = [load_decoder_evidence(path) for path in slot_evidence_paths]
    slot_documents = [item.document for item in acquired]
    if not acquired:
        raise OfflineAnalysisError("at least one slot document is required")
    required = acquired[0].required_decodes
    frame_count = acquired[0].frame_count
    if any(
        item.required_decodes != required or item.frame_count != frame_count
        for item in acquired[1:]
    ):
        raise OfflineAnalysisError(
            "decoder requirements differ", cause=FailureCause.CONTRADICTORY_EVIDENCE
        )
    return _summarize_validated(slot_documents, acquired, required, frame_count, evidence_path)


def _summarize_validated(
    slot_documents: list[dict[str, Any]],
    acquired: list[AcquiredDecoderEvidence],
    required: int,
    frame_count: int,
    evidence_path: Path | None = None,
) -> dict[str, Any]:
    if required <= 0:
        raise OfflineAnalysisError("required decode count must be positive")
    if not slot_documents:
        raise OfflineAnalysisError("at least one slot document is required")
    if len(slot_documents) != frame_count:
        raise OfflineAnalysisError(
            "decode evidence count "
            f"{len(slot_documents)} differs from planned frame count {frame_count}",
            cause=FailureCause.INCOMPLETE_EVIDENCE,
        )
    slots = [
        datetime.fromisoformat(document["slot_utc"].replace("Z", "+00:00"))
        for document in slot_documents
    ]
    if len(set(slots)) != len(slots):
        raise OfflineAnalysisError("duplicate decoder slots are forbidden")
    if slots != sorted(slots) or any(b - a != timedelta(minutes=2) for a, b in pairwise(slots)):
        raise OfflineAnalysisError("decoder slots must be strictly ordered and consecutive")
    fingerprints = [(document["profiles"], document["capture"]) for document in slot_documents]
    if any(item != fingerprints[0] for item in fingerprints[1:]):
        raise OfflineAnalysisError("decoder slot contexts differ")
    outcomes = [document["gate_outcome"] for document in slot_documents]
    maximum = run = 0
    for outcome in outcomes:
        run = run + 1 if outcome == "passed" else 0
        maximum = max(maximum, run)
    blocked = any(outcome == "blocked" for outcome in outcomes)
    gate_outcome = "blocked" if blocked else ("passed" if maximum >= required else "failed")
    failure_causes = (
        ["dependency_or_fixture_blocked"]
        if blocked
        else []
        if gate_outcome == "passed"
        else ["insufficient_consecutive_decodes"]
    )
    document = {
        "schema_version": 1,
        "evidence_type": "decode_summary",
        "slot_count": len(outcomes),
        "required_consecutive_decodes": required,
        "maximum_consecutive_correct_decodes": maximum,
        "planned_frame_count": frame_count,
        "gate_outcome": gate_outcome,
        "slots": [document["slot_utc"] for document in slot_documents],
        "slot_evidence": [artifact(item.path) for item in acquired],
        "profiles": slot_documents[0].get("profiles"),
        "capture": slot_documents[0].get("capture"),
        "failure_causes": failure_causes,
    }
    if evidence_path is not None:
        write_json_new(evidence_path, document, schema_name="decode-summary.schema.json")
    return document


def run_wsprd_acquired(
    wav_path: Path,
    audio_evidence_path: Path,
    evidence_path: Path,
    *,
    executable: Path | None = None,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    require_new_file(evidence_path)
    data_directory = evidence_path.parent / f"{wav_path.stem}-wsprd-data"
    if data_directory.exists():
        raise OfflineAnalysisError(
            f"refusing to reuse decoder data directory: {data_directory}",
            cause=FailureCause.OUTPUT_CONFLICT,
        )
    acquired = load_audio_evidence(audio_evidence_path, wav_path)
    document = run_wsprd(
        wav_path,
        evidence_path,
        acquired.identity,
        executable=executable,
        timeout_s=timeout_s,
        slot_utc=acquired.slot_utc,
        profile_evidence=acquired.document["profiles"],
        capture_evidence=acquired.document["capture"],
        audio_evidence=artifact(audio_evidence_path),
        data_directory=data_directory,
    )
    return document
