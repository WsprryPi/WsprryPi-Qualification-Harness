"""Profile- and capture-bound context for acquired offline evidence."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from wsprrypi_qualification.capture_metadata import CaptureMetadata, load_capture_metadata
from wsprrypi_qualification.manifests import build_manifest, render_manifest
from wsprrypi_qualification.models import BenchProfile, TestProfile
from wsprrypi_qualification.offline import FailureCause, OfflineAnalysisError, artifact
from wsprrypi_qualification.profiles import load_bench_profile, load_test_profile


@dataclass(frozen=True)
class ProfileContext:
    bench_path: Path
    test_path: Path
    bench: BenchProfile
    test: TestProfile

    def evidence(self) -> dict[str, Any]:
        return {
            "bench": {
                **artifact(self.bench_path),
                "id": self.bench.bench_id,
                "schema_version": self.bench.schema_version,
            },
            "test": {
                **artifact(self.test_path),
                "id": self.test.test_id,
                "schema_version": self.test.schema_version,
            },
            "resolved": {
                "receiver": asdict(self.bench.receiver),
                "requested_frequency_hz": self.test.frequency_hz,
                "receiver_center_hz": self.test.receiver_center_hz,
                "receiver_gain_db": self.test.receiver_gain_db,
                "identity": asdict(self.test.identity),
                "gates": asdict(self.test.gates),
                "frame_count": self.test.frame_count,
                "random_offset_enabled": self.test.random_offset_enabled,
            },
        }


def load_profile_context(bench_path: Path, test_path: Path) -> ProfileContext:
    bench = load_bench_profile(bench_path)
    test = load_test_profile(test_path)
    if test.random_offset_enabled:
        raise OfflineAnalysisError("random frequency offset must be disabled")
    receiver = bench.receiver
    if receiver.sample_rate_hz <= 0 or receiver.sample_format != "CF32":
        raise OfflineAnalysisError("bench receiver must define a valid CF32 contract")
    if test.receiver_center_hz != test.receiver_center_hz or test.frequency_hz != test.frequency_hz:
        raise OfflineAnalysisError("profile frequencies must be finite")
    return ProfileContext(bench_path, test_path, bench, test)


def validate_acquired_capture(
    metadata_path: Path,
    iq_path: Path,
    context: ProfileContext,
    *,
    relocation_bundle: Path | None = None,
) -> CaptureMetadata:
    metadata = load_capture_metadata(metadata_path)
    if metadata.evidence_type != "capture_success" or metadata.process_exit_code != 0:
        raise OfflineAnalysisError("capture evidence is not successful")
    if metadata.cleanup_outcome != "verified":
        raise OfflineAnalysisError("capture cleanup is not verified")
    if metadata.overflow_count or metadata.timeout_count or metadata.clipped_samples:
        raise OfflineAnalysisError("capture reports overflow, timeout, or clipping")
    if metadata.requested_sample_count != metadata.retained_sample_count:
        raise OfflineAnalysisError("capture sample count is not exact")
    record = artifact(iq_path)
    recorded_output_raw = metadata.output.path
    recorded_output = _local_recorded_output(
        recorded_output_raw, metadata_path, platform_name=os.name
    )
    if (
        metadata.output.path == ""
        or metadata.output.sha256 != record["sha256"]
        or metadata.output.size_bytes != record["size_bytes"]
    ):
        raise OfflineAnalysisError(
            "capture IQ hash or size differs from evidence",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
    try:
        if recorded_output is not None and recorded_output.exists():
            if recorded_output.resolve(strict=True) != iq_path.resolve(strict=True):
                raise OfflineAnalysisError(
                    "capture IQ path differs from available original evidence",
                    cause=FailureCause.CONTRADICTORY_EVIDENCE,
                )
        elif relocation_bundle is None:
            raise OfflineAnalysisError(
                "capture metadata output path is unavailable and no authenticated "
                "relocation was supplied",
                cause=FailureCause.INCOMPLETE_EVIDENCE,
            )
        else:
            _validate_relocated_artifact(relocation_bundle, recorded_output_raw, iq_path, record)
    except OSError as error:
        raise OfflineAnalysisError(
            f"capture IQ path cannot be authenticated: {error}",
            cause=FailureCause.INCOMPLETE_EVIDENCE,
        ) from error
    if metadata.output.size_bytes != metadata.retained_sample_count * 8:
        raise OfflineAnalysisError("capture IQ byte size does not match CF32 sample count")
    expected_wire = {
        "sample_format": "CF32",
        "component_type": "IEEE754_binary32",
        "interleave": "real_imaginary",
        "byte_order": "little_endian",
        "bytes_per_complex_sample": 8,
    }
    if metadata.wire_format != expected_wire:
        raise OfflineAnalysisError("capture wire format is incompatible")
    actual = metadata.actual_settings
    if actual is None:
        raise OfflineAnalysisError("capture lacks actual receiver settings")
    receiver = context.bench.receiver
    expected = {
        "format": "CF32",
        "sample_rate_hz": receiver.sample_rate_hz,
        "bandwidth_hz": receiver.bandwidth_hz,
        "center_frequency_hz": context.test.receiver_center_hz,
        "gain_db": context.test.receiver_gain_db,
        "channel": receiver.channel,
        "agc": receiver.agc,
        "bias_tee": receiver.bias_tee,
    }
    if actual != expected or metadata.requested_settings != expected:
        raise OfflineAnalysisError("capture settings differ from resolved profiles")
    requested_device = {"driver": receiver.driver, "serial": receiver.serial or ""}
    if (
        metadata.requested_device != requested_device
        or metadata.resolved_device != requested_device
    ):
        raise OfflineAnalysisError("capture receiver identity differs from bench profile")
    return metadata


def _local_recorded_output(
    raw_path: str, metadata_path: Path, *, platform_name: str
) -> Path | None:
    """Interpret a recorded path only when it belongs to the current path flavor."""
    if platform_name == "nt":
        if PureWindowsPath(raw_path).is_absolute():
            return Path(raw_path)
        if PurePosixPath(raw_path).is_absolute():
            return None
    else:
        if PurePosixPath(raw_path).is_absolute():
            return Path(raw_path)
        if PureWindowsPath(raw_path).is_absolute():
            return None
    return metadata_path.resolve().parent / Path(raw_path)


def _validate_relocated_artifact(
    bundle: Path, original_path: str, retained: Path, identity: dict[str, object]
) -> None:
    root = bundle.resolve(strict=True)
    manifest = root / "SHA256SUMS"
    index_path = root / "live-artifact-index.json"
    if manifest.read_text(encoding="utf-8") != render_manifest(build_manifest(root)):
        raise OfflineAnalysisError(
            "relocation bundle manifest is invalid", cause=FailureCause.CONTRADICTORY_EVIDENCE
        )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    expected_retained = retained.resolve(strict=True)
    if not expected_retained.is_relative_to(root):
        raise OfflineAnalysisError(
            "relocated IQ is outside its authenticated bundle",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
    matches = [
        record
        for record in index.get("artifacts", [])
        if record.get("source", {}).get("path") == original_path
        and (root / record.get("retained", {}).get("path", "")).resolve() == expected_retained
        and all(
            record.get("retained", {}).get(key) == identity[key] for key in ("size_bytes", "sha256")
        )
        and all(
            record.get("source", {}).get(key) == identity[key] for key in ("size_bytes", "sha256")
        )
    ]
    if len(matches) != 1:
        raise OfflineAnalysisError(
            "capture relocation is absent or ambiguous in the authenticated artifact index",
            cause=FailureCause.CONTRADICTORY_EVIDENCE,
        )
