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
    ):
        packaged = json.loads(
            files("wsprrypi_qualification.schemas").joinpath(name).read_text(encoding="utf-8")
        )
        review_facing = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        assert packaged == review_facing


def test_all_schemas_are_valid_draft_2020_12() -> None:
    for path in (ROOT / "schemas").glob("*.json"):
        jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
