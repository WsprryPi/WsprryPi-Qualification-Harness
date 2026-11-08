"""Retained-release state boundaries, exact-byte handoff and partial continuation.

The service is a deterministic publication transport fixture. It never contacts
GitHub, builds firmware or qualifies hardware.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from wsprrypi_qualification import lifecycle as lc
from wsprrypi_qualification import prepared_release as release


class Service:
    def __init__(self, directory: Path, *, fail_publish: str = "", fail_accept: str = "") -> None:
        self.directory = directory
        directory.mkdir(exist_ok=True)
        self.fail_publish = fail_publish
        self.fail_accept = fail_accept
        self.events: list[str] = []

    def prepare(self, name: str, spec: dict[str, Any], root: Path) -> dict[str, Any]:
        self.events.append("prepare:" + name)
        destination = root / name / "artifacts"
        destination.mkdir(parents=True)
        revision = release.source(Path(spec["source"]), name)["commit"]
        (destination / "payload.bin").write_bytes((name + revision + spec["version"]).encode())
        return {
            "component": name,
            "repository": release.REPOSITORIES[name],
            "directory": str(destination),
            "source": revision,
            "version": spec["version"],
            "tag": spec["tag"],
            "validation": {"status": "PASS", "class": "contract fixture"},
            "consumer_source_commit": (
                release.source(Path(spec["consumer_source"]), "pi")["commit"]
                if "consumer_source" in spec
                else None
            ),
            "ci_pico_source": spec.get("pico_source"),
            "default_rp1_tag": "v1.0.0-test.1",
        }

    def inspect_public(self, packet: dict[str, Any], root: Path) -> dict[str, Any]:
        self.events.append("inspect:" + packet["component"])
        path = self.directory / packet["component"]
        if path.exists():
            assert release.inventory(path) == packet["files"]
            return {"state": "published", "packet_sha256": packet["packet_sha256"]}
        if not packet.get("publish_selected", True):
            # Explicit fixed-companion fixture.
            path.mkdir()
            for name in packet["files"]:
                (path / name).write_bytes((Path(packet["directory"]) / name).read_bytes())
            return {"state": "published", "packet_sha256": packet["packet_sha256"]}
        return {"state": "absent", "packet_sha256": packet["packet_sha256"]}

    def publish(self, packet: dict[str, Any], root: Path) -> dict[str, Any]:
        self.events.append("publish:" + packet["component"])
        if packet["component"] == self.fail_publish:
            raise lc.LifecycleError("injected interrupted owning publication")
        path = self.directory / packet["component"]
        path.mkdir()
        for name in packet["files"]:
            (path / name).write_bytes((Path(packet["directory"]) / name).read_bytes())
        return self.inspect_public(packet, root)

    def accept(self, packet: dict[str, Any], root: Path) -> dict[str, Any]:
        self.events.append("accept:" + packet["component"])
        if packet["component"] == self.fail_accept:
            raise lc.LifecycleError("injected consumer acceptance failure")
        assert release.inventory(self.directory / packet["component"]) == packet["files"]
        return {"status": "PASS", "packet_sha256": packet["packet_sha256"]}


def repository(directory: Path, role: str) -> Path:
    directory.mkdir()
    subprocess.run(["git", "init", str(directory)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(directory), "config", "core.autocrlf", "false"], check=True)
    (directory / "source.txt").write_text(role + "\n")
    subprocess.run(["git", "-C", str(directory), "add", "source.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(directory),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(directory),
            "remote",
            "add",
            "origin",
            "https://github.com/" + release.REPOSITORIES[role] + ".git",
        ],
        check=True,
    )
    return directory


def request_for(root: Path, scope: str = "system") -> dict[str, Any]:
    root = root.resolve()
    root.mkdir(exist_ok=True)
    repos = {
        n: repository(root / (n + " source café with spaces"), n) for n in release.REPOSITORIES
    }
    version = "1.0.0-test.1"
    components = {
        "pi": {
            "source": str(repos["pi"]),
            "version": version,
            "tag": "v" + version,
            "publish": scope in ("system", "pi"),
            "ci_run": 1,
            "mode": "build",
            "public_branch": "devel",
            "pico_source": release.source(repos["pico"], "pico")["commit"],
        },
        "pico": {
            "source": str(repos["pico"]),
            "tooling_source": str(repos["pico"]),
            "version": version,
            "tag": "v" + version,
            "publish": scope in ("system", "pico"),
            "ci_run": 1,
            "attempt": 1,
            "mode": "ci",
            "branch": "release",
        },
        "rp1": {
            "source": str(repos["rp1"]),
            "consumer_source": str(repos["pi"]),
            "version": version,
            "tag": "v" + version,
            "publish": scope in ("system", "rp1"),
            "ci_run": 1,
            "mode": "build",
            "image": "sha256:" + "a" * 64,
        },
    }
    if scope in ("pico", "rp1"):
        components = {scope: components[scope]}
    selections = (
        {
            n: {"source": release.source(repos[n], n)["commit"], "version": version}
            for n in ("pico", "rp1")
        }
        if "pi" in components
        else {}
    )
    return {
        "schema_version": 1,
        "scope": scope,
        "components": components,
        "pi_dependencies": selections,
    }


@pytest.mark.parametrize("scope", ["pi", "pico", "rp1", "system"])
def test_prepare_then_later_publish_exact_bytes_and_independent_scope(
    tmp_path: Path, scope: str
) -> None:
    root = tmp_path.resolve()
    request = request_for(root / "inputs", scope)
    state = root / "durable state with spaces"
    plan = release.plan(request, state)
    assert not state.exists()
    service = Service(root / "service")
    ready = release.prepare(plan, plan["plan_sha256"], service)
    assert ready["status"] == "prepared"
    assert not any(e.startswith("publish:") for e in service.events)
    sealed = release.verify_retained(state)
    # Original worktrees can advance; retained source copies stay exact.
    original = Path(request["components"][scope if scope != "system" else "pi"]["source"])
    (original / "new work.txt").write_text("preserve later work")
    assert release.status(state)["status"] == "prepared"
    readonly = release.publish(state, sealed["prepared_sha256"], service, validate_only=True)
    assert readonly["status"] == "validated" and readonly["publication_performed"] is False
    result = release.publish(state, sealed["prepared_sha256"], service, enable_publication=True)
    assert result["status"] == "complete"
    emitted = [e for e in service.events if e.startswith("publish:")]
    assert emitted == (
        ["publish:pico", "publish:rp1", "publish:pi"] if scope == "system" else ["publish:" + scope]
    )
    assert (original / "new work.txt").read_text() == "preserve later work"


@pytest.mark.parametrize("change", ["changed", "missing", "extra", "source", "evidence"])
def test_changed_missing_or_substituted_inputs_refuse_before_publication(
    tmp_path: Path, change: str
) -> None:
    root = tmp_path.resolve()
    request = request_for(root / "inputs", "pico")
    state = root / "state"
    plan = release.plan(request, state)
    service = Service(root / "service")
    ready = release.prepare(plan, plan["plan_sha256"], service)
    sealed = release.verify_retained(state)
    file = state / "pico/artifacts/payload.bin"
    if change == "changed":
        file.write_bytes(b"unvalidated replacement")
    elif change == "missing":
        file.unlink()
    elif change == "extra":
        (file.parent / "unexpected.bin").write_bytes(b"extra")
    elif change == "source":
        (state / "sources/pico/source.txt").write_text("changed source")
    else:
        (state / "evidence").mkdir(exist_ok=True)
        (state / "evidence/invented.json").write_text("{}")
    assert release.status(state)["status"] == "invalidated"
    with pytest.raises((ValueError, OSError)):
        release.publish(state, ready["prepared_sha256"], service, enable_publication=True)
    assert not any(e.startswith("publish:") for e in service.events)
    assert sealed["components"]["pico"]["source"] != ""


def test_incompatible_dependency_pair_never_becomes_ready(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    request = request_for(root / "inputs")
    request["pi_dependencies"]["pico"]["source"] = "f" * 40
    plan = release.plan(request, root / "state")
    service = Service(root / "service")
    result = release.prepare(plan, plan["plan_sha256"], service)
    assert result["status"] == "not_ready"
    assert "selection" in result["errors"][0]
    assert not (root / "state/prepared.json").exists()
    assert not any(e.startswith("publish:") for e in service.events)


def test_partial_publication_continues_only_unfinished_owners(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    request = request_for(root / "inputs")
    state = root / "state"
    plan = release.plan(request, state)
    service = Service(root / "service", fail_publish="rp1")
    ready = release.prepare(plan, plan["plan_sha256"], service)
    result = release.publish(state, ready["prepared_sha256"], service, enable_publication=True)
    assert result["status"] == "partially_published"
    assert result["pending"]["component"] == "rp1"
    assert result["publication"]["pico"]["acceptance"]["status"] == "PASS"
    with pytest.raises(ValueError, match="resume"):
        release.publish(state, ready["prepared_sha256"], service, enable_publication=True)
    service.fail_publish = ""
    before = service.events.count("accept:pico")
    assert (
        release.publish(
            state, ready["prepared_sha256"], service, enable_publication=True, resume=True
        )["status"]
        == "complete"
    )
    assert service.events.count("publish:pico") == 1
    assert service.events.count("accept:pico") == before
    assert (root / "service/pico/payload.bin").read_bytes() == (
        state / "pico/artifacts/payload.bin"
    ).read_bytes()


def test_published_receipt_survives_consumer_failure(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    request = request_for(root / "inputs", "rp1")
    state = root / "state"
    plan = release.plan(request, state)
    service = Service(root / "service", fail_accept="rp1")
    ready = release.prepare(plan, plan["plan_sha256"], service)
    first = release.publish(state, ready["prepared_sha256"], service, enable_publication=True)
    assert first["status"] == "acceptance_pending"
    assert first["publication"]["rp1"]["publication"]["state"] == "published"
    service.fail_accept = ""
    assert (
        release.publish(
            state, ready["prepared_sha256"], service, enable_publication=True, resume=True
        )["status"]
        == "complete"
    )
    assert service.events.count("publish:rp1") == 1


def test_publication_requires_separate_explicit_selection_and_original_digest(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    request = request_for(root / "inputs", "rp1")
    state = root / "state"
    plan = release.plan(request, state)
    service = Service(root / "service")
    ready = release.prepare(plan, plan["plan_sha256"], service)
    for flags in ({}, {"enable_publication": True, "validate_only": True}):
        with pytest.raises(ValueError):
            release.publish(state, ready["prepared_sha256"], service, **flags)
    with pytest.raises(ValueError, match="not confirmed"):
        release.publish(state, "0" * 64, service, enable_publication=True)
    assert not any(e.startswith("publish:") for e in service.events)


@pytest.mark.parametrize("scope", ["pi", "pico", "rp1", "system"])
def test_real_separate_process_handoff(tmp_path: Path, scope: str) -> None:
    root = tmp_path.resolve()
    request = request_for(root / "inputs", scope)
    lc.save(root / "request.json", request, new=True)
    driver = root / "invoke.py"
    driver.write_text(
        """import json,sys
from pathlib import Path
sys.path.insert(0, TESTS)
from test_prepared_release import Service
from wsprrypi_qualification import lifecycle as lc,prepared_release as r
root=Path(sys.argv[1]);service=Service(root/'service');state=root/'state'
if sys.argv[2]=='prepare':
 p=r.plan(lc.load(root/'request.json'),state);out=r.prepare(p,p['plan_sha256'],service)
else:
 p=r.verify_retained(state);out=r.publish(state,p['prepared_sha256'],service,enable_publication=True)
print(json.dumps(out))
""".replace("TESTS", repr(str(Path(__file__).parent)))
    )
    for phase in ("prepare", "publish"):
        if phase == "publish":
            # A completely separate publisher process must refuse changed AND
            # missing bytes before it can call any publication transport.
            component = next(iter(request["components"]))
            payload = root / "state" / component / "artifacts/payload.bin"
            original = payload.read_bytes()
            for mutation in ("changed", "missing"):
                if mutation == "changed":
                    payload.write_bytes(b"replacement")
                else:
                    payload.unlink()
                rejected = subprocess.run(
                    [sys.executable, str(driver), str(root), phase], capture_output=True, text=True
                )
                assert rejected.returncode != 0
                assert not (root / "service" / component).exists()
                payload.write_bytes(original)
        result = subprocess.run(
            [sys.executable, str(driver), str(root), phase],
            capture_output=True,
            text=True,
            check=True,
        )
        observed = json.loads(result.stdout)
        assert observed["status"] == ("prepared" if phase == "prepare" else "complete")
    for name in request["components"]:
        assert (root / "service" / name / "payload.bin").read_bytes() == (
            root / "state" / name / "artifacts/payload.bin"
        ).read_bytes()


def test_fixed_public_consumer_failure_prevents_any_new_publication(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    request = request_for(root / "inputs", "pi")
    state = root / "state"
    plan = release.plan(request, state)
    service = Service(root / "service", fail_accept="rp1")
    ready = release.prepare(plan, plan["plan_sha256"], service)
    with pytest.raises(ValueError, match="consumer acceptance"):
        release.publish(state, ready["prepared_sha256"], service, enable_publication=True)
    assert not any(event.startswith("publish:") for event in service.events)
    assert not (root / "service/pi").exists()


def test_corrupt_continuation_shape_refuses_before_public_transport(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    request = request_for(root / "inputs", "pico")
    state = root / "state"
    plan = release.plan(request, state)
    service = Service(root / "service")
    ready = release.prepare(plan, plan["plan_sha256"], service)
    checkpoint = lc.load(state / "state.json")
    checkpoint["pending"] = "fabricated acknowledgement"
    lc.save(state / "state.json", checkpoint)
    service.events.clear()
    with pytest.raises(ValueError, match="prepared-release-state"):
        release.publish(state, ready["prepared_sha256"], service, enable_publication=True)
    assert not service.events
