import json
import subprocess
import sys
from pathlib import Path


def test_fixture_generator_is_deterministic_and_refuses_reuse(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "generate_slice3_fixtures.py"
    first, second = tmp_path / "first", tmp_path / "second"
    subprocess.run([sys.executable, str(script), str(first)], check=True)
    subprocess.run([sys.executable, str(script), str(second)], check=True)
    first_meta = json.loads((first / "fixture.json").read_text(encoding="utf-8"))
    second_meta = json.loads((second / "fixture.json").read_text(encoding="utf-8"))
    assert first_meta == second_meta
    assert (first / "rf-on.cf32").read_bytes() == (second / "rf-on.cf32").read_bytes()
    failed = subprocess.run([sys.executable, str(script), str(first)], check=False)
    assert failed.returncode != 0
