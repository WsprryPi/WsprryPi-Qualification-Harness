import json
from copy import deepcopy
from pathlib import Path

import pytest

from wsprrypi_qualification.capture_metadata import (
    CaptureMetadataError,
    load_capture_metadata,
    validate_capture_metadata,
)


def capture_document() -> dict[str, object]:
    settings = {
        "format": "CF32",
        "sample_rate_hz": 250000.0,
        "bandwidth_hz": 200000.0,
        "center_frequency_hz": 10140200.0,
        "gain_db": 10.0,
        "channel": 0,
        "agc": False,
        "bias_tee": False,
    }
    device = {"driver": "mock", "serial": "MOCK-0001"}
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
    timestamps = {
        name: f"2026-08-11T12:00:{index:02d}.000Z" for index, name in enumerate(timestamp_names)
    }
    return {
        "schema_version": 1,
        "helper_version": "0.2.0",
        "evidence_type": "capture_success",
        "capture_id": "20260811T120000Z-mock-capture",
        "timestamps": timestamps,
        "elapsed_duration_s": 1.0,
        "limits": {
            "read_timeout_us": 2_000_000,
            "max_elapsed_duration_s": 10.0,
            "max_read_calls": 100,
        },
        "requested_device": device.copy(),
        "resolved_device": device.copy(),
        "requested_settings": settings.copy(),
        "actual_settings": settings.copy(),
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
        "requested_sample_count": 10,
        "retained_sample_count": 10,
        "read_call_count": 4,
        "partial_read_count": 1,
        "timeout_count": 0,
        "overflow_count": 0,
        "clipping": {"threshold": 0.999, "sample_count": 0},
        "output": {
            "path": "directory with spaces/capture.cf32",
            "present": True,
            "complete": True,
            "size_bytes": 80,
            "sha256": "a" * 64,
            "removed_incomplete_size_bytes": 0,
            "removed_incomplete_sha256": None,
        },
        "primary_outcome": "success",
        "primary_failure_cause": None,
        "failure_causes": [],
        "cleanup": {"outcome": "verified", "attempted_steps": ["mock_release"], "failed_steps": []},
        "process_exit_code": 0,
    }


def failure_document(cause: str = "short_read", exit_code: int = 1) -> dict[str, object]:
    document = deepcopy(capture_document())
    document.update(
        {
            "evidence_type": "capture_failure",
            "retained_sample_count": 4,
            "primary_outcome": "failed",
            "primary_failure_cause": cause,
            "failure_causes": [cause],
            "process_exit_code": exit_code,
        }
    )
    document["output"] = {
        "path": "capture.cf32",
        "present": False,
        "complete": False,
        "size_bytes": 0,
        "sha256": None,
        "removed_incomplete_size_bytes": 32,
        "removed_incomplete_sha256": "c" * 64,
    }
    return document


def test_valid_success_and_failure_load_typed_models(tmp_path: Path) -> None:
    for name, document in (
        ("success.json", capture_document()),
        ("capture.failure.json", failure_document()),
    ):
        path = tmp_path / name
        path.write_text(json.dumps(document), encoding="utf-8")
        metadata = load_capture_metadata(path)
        assert metadata.output.present is (name == "success.json")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retained_sample_count", 9),
        ("failure_causes", ["short_read"]),
        ("primary_failure_cause", "short_read"),
        ("process_exit_code", 1),
    ],
)
def test_success_contradictions_are_rejected(field: str, value: object) -> None:
    document = capture_document()
    document[field] = value
    with pytest.raises(CaptureMetadataError):
        validate_capture_metadata(document)


def test_success_cleanup_clipping_and_size_contradictions() -> None:
    variants = []
    cleanup = capture_document()
    cleanup["cleanup"] = {
        "outcome": "failed",
        "attempted_steps": ["close"],
        "failed_steps": ["close"],
    }
    variants.append(cleanup)
    clipped = capture_document()
    clipped["clipping"] = {"threshold": 0.999, "sample_count": 1}
    variants.append(clipped)
    size = capture_document()
    assert isinstance(size["output"], dict)
    size["output"]["size_bytes"] = 79
    variants.append(size)
    for document in variants:
        with pytest.raises(CaptureMetadataError):
            validate_capture_metadata(document)


def test_failure_requires_nonzero_exit_and_no_complete_output() -> None:
    zero = failure_document()
    zero["process_exit_code"] = 0
    complete = failure_document()
    assert isinstance(complete["output"], dict)
    complete["output"].update(
        {"present": True, "complete": True, "size_bytes": 32, "sha256": "b" * 64}
    )
    for document in (zero, complete):
        with pytest.raises(CaptureMetadataError):
            validate_capture_metadata(document)


def test_cleanup_failure_requires_exit_9_and_cleanup_cause() -> None:
    valid = failure_document("short_read", 9)
    valid["failure_causes"] = ["short_read", "cleanup"]
    valid["cleanup"] = {
        "outcome": "failed",
        "attempted_steps": ["close"],
        "failed_steps": ["close"],
    }
    assert validate_capture_metadata(valid).cleanup_outcome == "failed"
    for mutation in (
        lambda doc: doc.update(process_exit_code=1),
        lambda doc: doc.update(failure_causes=["short_read"]),
    ):
        invalid = deepcopy(valid)
        mutation(invalid)
        with pytest.raises(CaptureMetadataError, match="cleanup"):
            validate_capture_metadata(invalid)


def test_wrong_device_and_setting_mismatch_require_different_or_missing_actuals() -> None:
    wrong = failure_document("wrong_device", 6)
    mismatch = failure_document("settings_mismatch", 7)
    for document in (wrong, mismatch):
        with pytest.raises(CaptureMetadataError, match="matching"):
            validate_capture_metadata(document)
    wrong["resolved_device"] = None
    mismatch["actual_settings"] = None
    assert validate_capture_metadata(wrong).process_exit_code == 6
    assert validate_capture_metadata(mismatch).process_exit_code == 7


def test_non_finite_and_non_utc_values_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(capture_document()).replace("250000.0", "NaN", 1), encoding="utf-8")
    with pytest.raises(CaptureMetadataError, match="non-standard"):
        load_capture_metadata(path)
    document = capture_document()
    assert isinstance(document["timestamps"], dict)
    document["timestamps"]["helper_start_utc"] = "2026-08-11T12:00:00+01:00"
    with pytest.raises(CaptureMetadataError):
        validate_capture_metadata(document)
