from importlib.resources import files
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]


def test_packaged_schemas_match_review_facing_copies() -> None:
    for name in ("bench-profile.schema.json", "test-profile.schema.json", "result.schema.json"):
        packaged = files("wsprrypi_qualification.schemas").joinpath(name).read_bytes()
        assert packaged == (ROOT / "schemas" / name).read_bytes()


def test_all_schemas_are_valid_draft_2020_12() -> None:
    for path in (ROOT / "schemas").glob("*.json"):
        jsonschema.Draft202012Validator.check_schema(
            __import__("json").loads(path.read_text(encoding="utf-8"))
        )
