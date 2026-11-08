"""A failed Docker CLI cannot leave an unexamined daemon child or remove a foreign one."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from wsprrypi_qualification import lifecycle as lc
from wsprrypi_qualification.prepared_release_adapters import CanonicalReleaseAdapter

IMAGE = "sha256:" + "a" * 64


class Daemon(CanonicalReleaseAdapter):
    def __init__(self, *, failure: str = "") -> None:
        super().__init__({})
        self.failure = failure
        self.exists = False
        self.owner = ""
        self.calls: list[list[str]] = []

    def command(self, name: str) -> list[str]:
        return [name]

    def run(self, argv: list[str], root: Path, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(argv)
        if argv[1] == "run":
            self.owner = argv[argv.index("--label") + 1].split("=", 1)[1]
            if self.failure:
                self.exists = True
                raise lc.LifecycleError("interrupted canonical build")
            return {"return_code": 0, "stdout": "{}", "stderr": ""}
        if argv[1] == "rm":
            if self.failure != "cleanup":
                self.exists = False
            return {"return_code": 0, "stdout": "", "stderr": ""}
        assert argv[1:4] == ["inspect", "--type", "container"]
        if not self.exists:
            return {"return_code": 1, "stdout": "[]", "stderr": "Error: No such object"}
        labels = {
            "wsprrypi.qualification.owner": "foreign" if self.failure == "foreign" else self.owner
        }
        if self.failure == "unlabelled":
            labels = None
        value = [
            {
                "Id": "b" * 64,
                "Image": IMAGE,
                "Config": {"Labels": labels},
            }
        ]
        return {"return_code": 0, "stdout": json.dumps(value), "stderr": ""}


def test_success_still_verifies_daemon_container_absent(tmp_path: Path) -> None:
    adapter = Daemon()
    adapter.docker(IMAGE, {}, ["python3", "--version"], tmp_path)
    assert any(call[1] == "inspect" for call in adapter.calls)
    assert not any(call[1] == "rm" for call in adapter.calls)


@pytest.mark.parametrize("failure", ["owned", "foreign", "unlabelled", "cleanup"])
def test_interrupted_cli_inspects_ownership_and_verifies_cleanup(
    tmp_path: Path, failure: str
) -> None:
    adapter = Daemon(failure=failure)
    message = {
        "owned": "interrupted canonical",
        "foreign": "foreign ownership",
        "unlabelled": "foreign ownership",
        "cleanup": "cleanup could not verify",
    }[failure]
    with pytest.raises(ValueError, match=message):
        adapter.docker(IMAGE, {}, ["python3", "--version"], tmp_path)
    assert any(call[1] == "inspect" for call in adapter.calls)
    assert any(call[1] == "rm" for call in adapter.calls) is (
        failure not in {"foreign", "unlabelled"}
    )
    assert adapter.exists is (failure != "owned")


@pytest.mark.parametrize(
    ("source", "run", "comparison", "allowed"),
    [
        ("a" * 40, 1, "", True),
        ("a" * 40, 2, "", True),
        ("a" * 40, 3, "", False),
        ("b" * 40, 3, "ahead", True),
        ("b" * 40, 3, "behind", False),
        ("b" * 40, 3, "diverged", False),
    ],
)
def test_default_pico_preflight_refuses_an_untested_bookmark_before_publication(
    tmp_path: Path, source: str, run: int, comparison: str, allowed: bool
) -> None:
    class Index(CanonicalReleaseAdapter):
        def command(self, name: str) -> list[str]:
            return [name]

        def api(self, repository: str, endpoint: str, root: Path, **kwargs: Any) -> Any:
            assert kwargs.get("method", "GET") == "GET"
            if endpoint.startswith("compare/"):
                return {"status": comparison}
            assert endpoint == "releases/tags/ci-devel"
            return {"draft": False, "prerelease": True, "assets": [], "body": "owner index"}

        def run(self, argv: list[str], root: Path, **kwargs: Any) -> dict[str, Any]:
            return {"stdout": json.dumps({"source": source, "run_id": run, "attempt": 1})}

    adapter = Index({})
    packet = {
        "branch": "devel",
        "repository": "WsprryPi/WsprryPico",
        "source": "a" * 40,
        "helper_source": str(tmp_path),
        "owner_plan": {"receipt": {"run_id": 2, "attempt": 1}},
    }
    if allowed:
        adapter.pico_default_preflight(packet, tmp_path)
    else:
        with pytest.raises(ValueError, match="return to preparation"):
            adapter.pico_default_preflight(packet, tmp_path)
