import json
from importlib.resources import files
from pathlib import Path

import jsonschema

from wsprrypi_qualification.sdr_calibration import (
    PROFILE_SCHEMA_NAME,
    PROFILE_SCHEMA_VERSION,
    UPSTREAM_REVISION,
    UPSTREAM_SCHEMA_SHA256,
)

ROOT = Path(__file__).resolve().parents[2]


def test_packaged_schemas_match_review_facing_copies() -> None:
    for name in (
        "bench-profile.schema.json",
        "test-profile.schema.json",
        "receiver-run-profile.schema.json",
        "result.schema.json",
        "capture-metadata.schema.json",
        "carrier-analysis.schema.json",
        "audio-conversion.schema.json",
        "decoder-evidence.schema.json",
        "decode-summary.schema.json",
        "offline-failure.schema.json",
        "application-plan.schema.json",
        "cw-qualification-analysis.schema.json",
        "cw-mode-plan.schema.json",
        "cw-expected-events.schema.json",
        "cw-generated-observations.schema.json",
        "cw-mode-gate.schema.json",
        "cw-final-session.schema.json",
        "cw-mock-lifecycle.schema.json",
        "archive-inventory.schema.json",
        "cw-multi-capture-session.schema.json",
        "slice6-session-plan.schema.json",
        "slice6-session.schema.json",
        "slice6-offline-evidence-index.schema.json",
        "ssh-capability.schema.json",
        "soapy-capability.schema.json",
        "wsprrypi-process-capability.schema.json",
        "service-capability.schema.json",
        "gpio-quiescence-capability.schema.json",
        "si5351-quiescence-capability.schema.json",
        "resolved-capability-plan.schema.json",
        "real-capability-session.schema.json",
        "helper-request.schema.json",
        "helper-response.schema.json",
        "bounded-tone-helper-result.schema.json",
        "bounded-tone-failure-evidence.schema.json",
        "process-start-result.schema.json",
        "process-wait-result.schema.json",
        "process-stop-result.schema.json",
        "service-helper-result.schema.json",
        "gpio-helper-result.schema.json",
        "si5351-helper-result.schema.json",
        "helper-config.schema.json",
        "real-runtime-authorization.schema.json",
        "resolved-real-session-plan.schema.json",
        "real-session-stage-evidence.schema.json",
        "real-qualification-session.schema.json",
        "helper-deployment-config.schema.json",
        "deployment-provider-evidence.schema.json",
        "systemd-transaction-evidence.schema.json",
        "simulator-plan.schema.json",
        "resolved-simulator-plan.schema.json",
        "simulator-session.schema.json",
        "simulator-artifact-index.schema.json",
        "simulator-decode-summary.schema.json",
        "simulator-result.schema.json",
        "simulator-capabilities.schema.json",
        "simulator-runtime-confirmation.schema.json",
        "simulator-quiescence.schema.json",
        "actual-host-runtime-authorization.schema.json",
        "actual-host-identity-correction.schema.json",
        "actual-host-original-bundle-reference.schema.json",
        "actual-host-correction-request.schema.json",
        "actual-host-prior-correction-reference.schema.json",
        "actual-host-corrected-result.schema.json",
        "actual-host-controller-openssh.schema.json",
        "actual-host-correction-log.schema.json",
        "resolved-receiver-integration-plan.schema.json",
        "receiver-runtime-authorization.schema.json",
        "receiver-integration-stage.schema.json",
        "receiver-capture-evidence.schema.json",
        "receiver-capture-metadata.schema.json",
        "receiver-integration-session.schema.json",
        "receiver-integration-artifact-index.schema.json",
        "receiver-integration-result.schema.json",
        "resolved-transmitter-lifecycle-plan.schema.json",
        "transmitter-runtime-authorization.schema.json",
        "transmitter-lifecycle-session.schema.json",
        "transmitter-lifecycle-result.schema.json",
        "carrier-run-correction.schema.json",
        "carrier-runtime-authorization.schema.json",
        "bounded-carrier-original-anchors.schema.json",
        "sdr-calibration-profile.schema.json",
        "sdr-calibration-application-request.schema.json",
    ):
        packaged = json.loads(
            files("wsprrypi_qualification.schemas").joinpath(name).read_text(encoding="utf-8")
        )
        review_facing = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        assert packaged == review_facing


def test_packaged_cw_schemas_are_byte_identical() -> None:
    for name in (
        "cw-qualification-analysis.schema.json",
        "cw-mode-plan.schema.json",
        "cw-expected-events.schema.json",
        "cw-generated-observations.schema.json",
        "cw-mode-gate.schema.json",
        "cw-final-session.schema.json",
        "cw-mock-lifecycle.schema.json",
    ):
        review_facing = ROOT / "schemas" / name
        packaged = files("wsprrypi_qualification.schemas").joinpath(name)
        assert packaged.read_bytes() == review_facing.read_bytes()


def test_packaged_sdr_calibration_schemas_are_byte_identical() -> None:
    for name in (
        "sdr-calibration-profile.schema.json",
        "sdr-calibration-application-request.schema.json",
    ):
        review_facing = ROOT / "schemas" / name
        packaged = files("wsprrypi_qualification.schemas").joinpath(name)
        assert packaged.read_bytes() == review_facing.read_bytes()


def test_sdr_calibration_upstream_pin_matches_consumer_constants() -> None:
    pin = json.loads((ROOT / "schemas" / "SDR_CALIBRATION_UPSTREAM.json").read_text())
    assert pin["revision"] == UPSTREAM_REVISION
    assert pin["schema_name"] == PROFILE_SCHEMA_NAME
    assert pin["schema_version"] == PROFILE_SCHEMA_VERSION
    assert pin["schema_sha256"] == UPSTREAM_SCHEMA_SHA256
    assert pin["compatibility"] == "exact-version-only"


def test_all_schemas_are_valid_draft_2020_12() -> None:
    for path in (ROOT / "schemas").glob("*.json"):
        jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
