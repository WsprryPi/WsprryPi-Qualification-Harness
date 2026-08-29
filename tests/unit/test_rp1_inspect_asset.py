from __future__ import annotations

import json
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast


def test_enrollments_are_recorded_without_becoming_an_eligibility_whitelist(
    tmp_path: Path,
) -> None:
    script = (
        Path(__file__).resolve().parents[2] / "deployment" / "raspberry-pi-os" / "wspq-rp1-inspect"
    )
    namespace = runpy.run_path(str(script))
    observe = cast(Callable[[Path], list[dict[str, object]]], namespace["observed_enrollments"])
    assert observe(tmp_path) == []
    first = tmp_path / "old.json"
    second = tmp_path / "new.json"
    first.write_text(json.dumps({"sourceCommit": "a" * 40, "route": "gpio20"}))
    second.write_text(json.dumps({"sourceCommit": "b" * 40, "route": "gpio20"}))
    observations = observe(tmp_path)
    assert [item["source_commit"] for item in observations] == ["b" * 40, "a" * 40]
    assert all(item["status"] == "parsed" for item in observations)
