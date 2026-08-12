import json
from importlib.resources import files
from pathlib import Path

import jsonschema

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
    ):
        packaged = json.loads(
            files("wsprrypi_qualification.schemas").joinpath(name).read_text(encoding="utf-8")
        )
        review_facing = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        assert packaged == review_facing


def test_all_schemas_are_valid_draft_2020_12() -> None:
    for path in (ROOT / "schemas").glob("*.json"):
        jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
