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
    review_names = {path.name for path in (ROOT / "schemas").glob("*.schema.json")}
    packaged_root = files("wsprrypi_qualification.schemas")
    packaged_names = {
        item.name for item in packaged_root.iterdir() if item.name.endswith(".schema.json")
    }
    assert packaged_names == review_names
    for name in sorted(review_names):
        assert packaged_root.joinpath(name).read_bytes() == (ROOT / "schemas" / name).read_bytes()


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


def test_packaged_keyed_session_schemas_are_byte_identical() -> None:
    for name in (
        "resolved-keyed-session-plan.schema.json",
        "keyed-runtime-authorization.schema.json",
        "keyed-transaction.schema.json",
        "keyed-aggregate-session.schema.json",
        "keyed-result.schema.json",
        "keyed-artifact-index.schema.json",
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
