from __future__ import annotations

import hashlib
import json
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast


def test_enrollment_digest_preserves_all_non_lifecycle_manifest_fields() -> None:
    script = (
        Path(__file__).resolve().parents[2] / "deployment" / "raspberry-pi-os" / "wspq-rp1-inspect"
    )
    namespace = runpy.run_path(str(script))
    digest = cast(Callable[[dict[str, object]], str], namespace["enrollment_manifest_sha256"])
    manifest: dict[str, object] = {
        "sourceCommit": "a" * 40,
        "route": "gpio4",
        "uapiIdentity": {"sha256": "b" * 64},
        "developmentState": "development-loaded",
        "parameters": {"live_output": 0},
    }
    enrolled = {
        **manifest,
        "developmentState": "development-loaded",
        "parameters": {"live_output": 0},
    }
    expected = hashlib.sha256(
        (json.dumps(enrolled, indent=2, sort_keys=True) + "\n").encode()
    ).hexdigest()

    assert digest(manifest) == expected
    changed_identity = {**manifest, "sourceCommit": "c" * 40}
    assert digest(changed_identity) != expected
