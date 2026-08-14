import hashlib
import json
from pathlib import Path

import pytest

from wsprrypi_qualification.cw_qualification import CwQualificationError, load_cw_qualification


def _write(tmp_path: Path, *, mode: str = "qrss", synthetic: bool = False) -> Path:
    capture = tmp_path / "capture with spaces.cf32"
    capture.write_bytes(b"12345678")
    secondary = 137_510.0 if mode in {"fskcw", "dfcw"} else None
    observations = [
        {
            "kind": "primary",
            "measured_frequency_hz": 137_500.2,
            "key_contrast_db": 30.0,
            "timing_error_s": 0.01,
            "carrier_continuous": True,
        }
    ]
    if mode == "qrss":
        observations += [
            {
                "kind": "key_down",
                "measured_frequency_hz": 137_500.2,
                "key_contrast_db": 30.0,
                "timing_error_s": 0.01,
                "carrier_continuous": True,
            },
            {
                "kind": "key_up",
                "measured_frequency_hz": None,
                "key_contrast_db": None,
                "timing_error_s": 0.01,
                "carrier_continuous": False,
            },
            {
                "kind": "transition",
                "measured_frequency_hz": None,
                "key_contrast_db": None,
                "timing_error_s": 0.01,
                "carrier_continuous": False,
            },
        ]
    elif secondary is not None:
        observations += [
            {
                "kind": "secondary",
                "measured_frequency_hz": secondary + 0.2,
                "key_contrast_db": 30.0,
                "timing_error_s": 0.01,
                "carrier_continuous": True,
            },
            {
                "kind": "transition",
                "measured_frequency_hz": None,
                "key_contrast_db": None,
                "timing_error_s": 0.01,
                "carrier_continuous": True,
            },
        ]
    status = "inconclusive"
    causes = ["synthetic_capture"] if synthetic else ["raw_iq_analysis_unimplemented"]
    document = {
        "schema_version": 1,
        "evidence_type": "cw_qualification_analysis",
        "mode": mode,
        "backend": "gpio",
        "hardware_profile": "legacy-500mhz-plld",
        "band": "2200m",
        "source": {"parent_revision": "a" * 40, "submodule_revision": "b" * 40},
        "capture": {
            "path": capture.name,
            "size_bytes": 8,
            "sha256": hashlib.sha256(b"12345678").hexdigest(),
            "sample_rate_hz": 250000,
            "synthetic": synthetic,
        },
        "thresholds": {
            "primary_frequency_hz": 137500.0,
            "secondary_frequency_hz": secondary,
            "frequency_tolerance_hz": 1.0,
            "spacing_tolerance_hz": 0.5,
            "minimum_key_contrast_db": 10.0,
            "timing_tolerance_s": 0.1,
        },
        "observations": observations,
        "cleanup_verified": True,
        "failure_causes": causes,
        "final_status": status,
        "qualification_claim": status == "qualified",
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.mark.parametrize("mode", ["tone", "qrss", "fskcw", "dfcw"])
def test_mode_specific_summary_remains_inconclusive_without_analyzer(
    mode: str, tmp_path: Path
) -> None:
    document = load_cw_qualification(_write(tmp_path, mode=mode))
    assert document["final_status"] == "inconclusive"
    assert document["qualification_claim"] is False


def test_synthetic_evidence_cannot_qualify(tmp_path: Path) -> None:
    assert load_cw_qualification(_write(tmp_path, synthetic=True))["qualification_claim"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda d: d["capture"].update(sha256="0" * 64), "SHA-256"),
        (lambda d: d.update(final_status="qualified", qualification_claim=True), "final_status"),
        (
            lambda d: d["observations"].__setitem__(
                2,
                {
                    **d["observations"][2],
                    "measured_frequency_hz": 137500.0,
                    "carrier_continuous": True,
                },
            ),
            "measured evidence",
        ),
    ],
)
def test_tampering_and_false_claims_fail_closed(tmp_path: Path, mutation, message: str) -> None:
    path = _write(tmp_path, synthetic=True)
    document = json.loads(path.read_text(encoding="utf-8"))
    mutation(document)
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CwQualificationError, match=message):
        load_cw_qualification(path)


def test_shifted_tones_must_be_distinct_and_correctly_spaced(tmp_path: Path) -> None:
    path = _write(tmp_path, mode="fskcw")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["thresholds"]["secondary_frequency_hz"] = 137500.2
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CwQualificationError, match="distinctly representable"):
        load_cw_qualification(path)


def test_cleanup_failure_has_precedence(tmp_path: Path) -> None:
    path = _write(tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document.update(
        cleanup_verified=False,
        final_status="cleanup_failed",
        qualification_claim=False,
        failure_causes=["cleanup_unverified"],
    )
    path.write_text(json.dumps(document), encoding="utf-8")
    assert load_cw_qualification(path)["final_status"] == "cleanup_failed"


def test_duplicate_or_mode_confused_observations_are_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["observations"].append(document["observations"][0])
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CwQualificationError, match="exactly once"):
        load_cw_qualification(path)


def test_tone_requires_carrier_contrast(tmp_path: Path) -> None:
    path = _write(tmp_path, mode="tone")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["observations"][0]["key_contrast_db"] = 1.0
    document.update(
        final_status="unqualified", qualification_claim=False, failure_causes=["primary_contrast"]
    )
    path.write_text(json.dumps(document), encoding="utf-8")
    assert load_cw_qualification(path)["final_status"] == "unqualified"


def test_non_finite_sample_rate_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["capture"]["sample_rate_hz"] = float("nan")
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CwQualificationError, match="sample_rate_hz must be a finite number"):
        load_cw_qualification(path)


def test_missing_key_up_and_timing_drift_cannot_pass(tmp_path: Path) -> None:
    path = _write(tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["observations"] = [
        observation for observation in document["observations"] if observation["kind"] != "key_up"
    ]
    next(
        observation
        for observation in document["observations"]
        if observation["kind"] == "transition"
    )["timing_error_s"] = 1.0
    document.update(
        final_status="unqualified",
        qualification_claim=False,
        failure_causes=["missing_key_up_observation", "transition_timing"],
    )
    path.write_text(json.dumps(document), encoding="utf-8")
    assert load_cw_qualification(path)["final_status"] == "unqualified"


def test_shifted_tone_swap_and_interruption_cannot_pass(tmp_path: Path) -> None:
    path = _write(tmp_path, mode="fskcw")
    document = json.loads(path.read_text(encoding="utf-8"))
    primary = next(item for item in document["observations"] if item["kind"] == "primary")
    secondary = next(item for item in document["observations"] if item["kind"] == "secondary")
    primary["measured_frequency_hz"], secondary["measured_frequency_hz"] = (
        secondary["measured_frequency_hz"],
        primary["measured_frequency_hz"],
    )
    next(
        observation
        for observation in document["observations"]
        if observation["kind"] == "transition"
    )["carrier_continuous"] = False
    document.update(
        final_status="unqualified",
        qualification_claim=False,
        failure_causes=["carrier_interruption", "primary_frequency", "secondary_frequency"],
    )
    path.write_text(json.dumps(document), encoding="utf-8")
    assert load_cw_qualification(path)["final_status"] == "unqualified"
