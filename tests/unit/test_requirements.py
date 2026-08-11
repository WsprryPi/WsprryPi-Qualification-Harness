import tomllib
from pathlib import Path


def test_requirements_match_project_runtime_dependencies() -> None:
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [
        line
        for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert requirements == project["project"]["dependencies"]
