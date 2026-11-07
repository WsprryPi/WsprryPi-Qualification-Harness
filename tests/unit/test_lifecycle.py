"""Lifecycle contract checks use real bounded children and isolated Git inputs."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest

from wsprrypi_qualification import lifecycle as lc


@pytest.fixture
def recipe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(lc, "controller_lock_root", lambda: tmp_path / "locks")
    source = tmp_path / "source with spaces"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "user.name=fixture",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    exe = str(Path(sys.executable).resolve())
    cmd = {
        "argv": [exe, "-c", 'import json; print(json.dumps({"safe": True}))'],
        "cwd": str(tmp_path),
        "timeout_s": 5,
        "inputs": [{"path": exe, "sha256": lc.file_sha(Path(exe))}],
        "environment": {},
    }

    def step(name: str, phase: str) -> dict:
        return {
            "id": name,
            "component": "pi",
            "phase": phase,
            "check": name,
            "command": copy.deepcopy(cmd),
            "expect_exit": 0,
            "expect_json": {"safe": True},
            "mutates": ["pi"] if phase == "effect" else [],
            "replay_safe": phase != "effect",
        }

    return {
        "schema_version": 1,
        "operation": "test",
        "scope": "pi",
        "profile": "fixture",
        "rf_enabled": False,
        "sources": [lc.source_identity("pi", source)],
        "artifacts": [],
        "inputs": {"board": "fixture", "windows_path": r"C:\Users\test user\bundle"},
        "resources": ["host:fixture"],
        "state_root": str(tmp_path / "state"),
        "protected_paths": [str(source)],
        "final_states": {"pi": "absent"},
        "required_checks": ["final"],
        "steps": [step("inspect", "inspect"), step("install", "effect"), step("final", "verify")],
        "ci": [],
    }


def test_plan_is_read_only_and_requires_independent_assertions(recipe: dict) -> None:
    plan = lc.compose(recipe)
    assert not Path(recipe["state_root"]).exists()
    assert plan["request"]["rf_enabled"] is False
    recipe["steps"][-1]["expect_json"] = {}
    with pytest.raises(lc.LifecycleError, match="independent JSON"):
        lc.compose(recipe)


def test_real_execution_and_authenticated_report(recipe: dict) -> None:
    plan = lc.compose(recipe)
    directory = Path(recipe["state_root"]) / "run with spaces"
    result = lc.run(plan, directory, confirm=plan["plan_sha256"])
    assert result["status"] == "pass"
    assert lc.report(directory) == result
    (directory / result["records"][0]["log"]).write_text("{}")
    with pytest.raises(lc.LifecycleError, match="log missing or changed"):
        lc.report(directory)


def test_source_untracked_bytes_and_artifact_changes_invalidate(recipe: dict) -> None:
    plan = lc.compose(recipe)
    (Path(recipe["sources"][0]["path"]) / "new.txt").write_text("new")
    with pytest.raises(lc.LifecycleError, match="source identity changed"):
        lc.verify_plan(plan)


def test_missing_checks_scope_and_digest_fail_closed(recipe: dict) -> None:
    recipe["required_checks"].append("physical-usb")
    plan = lc.compose(recipe)
    result = lc.run(plan, Path(recipe["state_root"]) / "run", confirm=plan["plan_sha256"])
    assert result["status"] == "blocked_prerequisite"
    assert result["completed"] == []
    assert not result["recovery_required"]
    recipe["steps"][1]["mutates"].append("pico")
    with pytest.raises(lc.LifecycleError, match="unselected companion"):
        lc.compose(recipe)


def test_ci_reuse_binds_entire_inputs_and_required_jobs(recipe: dict) -> None:
    key = lc.compose(recipe)["input_sha256"]
    recipe["ci"] = [
        {
            "component": "pi",
            "workflow": "canonical",
            "input_sha256": key,
            "conclusion": "success",
            "url": "https://example.invalid/run",
            "required_jobs": ["native", "usb"],
            "successful_jobs": ["native", "usb"],
        }
    ]
    assert lc.compose(recipe)["ci_selection"][0]["reuse"]
    recipe["inputs"]["dependency"] = "different"
    assert not lc.compose(recipe)["ci_selection"][0]["reuse"]
    recipe["ci"][0]["successful_jobs"] = ["native"]
    assert not lc.compose(recipe)["ci_selection"][0]["reuse"]


def test_publication_validation_and_protected_state(recipe: dict) -> None:
    recipe["steps"][1]["phase"] = "publish"
    assert lc.compose(recipe)["blockers"]
    recipe["state_root"] = recipe["sources"][0]["path"]
    with pytest.raises(lc.LifecycleError, match="protected"):
        lc.compose(recipe)


def test_concurrency_never_steals_lock(tmp_path: Path) -> None:
    with lc.ownership(tmp_path, ["device:one"]):  # noqa: SIM117
        with (
            pytest.raises(lc.LifecycleError, match="already owned"),
            lc.ownership(tmp_path, ["device:one"]),
        ):
            pass
    assert not list(tmp_path.iterdir())


def test_interrupted_effect_cannot_be_blindly_replayed(recipe: dict) -> None:
    plan = lc.compose(recipe)
    directory = Path(recipe["state_root"]) / "run"
    lc.run(plan, directory, confirm=plan["plan_sha256"])
    checkpoint = lc.load(directory / "checkpoint.json")
    checkpoint["completed"] = ["inspect"]
    checkpoint["pending"] = "install"
    lc.save(directory / "checkpoint.json", checkpoint)
    with pytest.raises(lc.LifecycleError, match="non-replayable"):
        lc.run(plan, directory, confirm=plan["plan_sha256"], resume=True, inspection="inspect")


def test_failure_never_passes_missing_required_checks(recipe: dict) -> None:
    recipe["steps"][1]["command"]["argv"][-1] = "raise SystemExit(7)"
    plan = lc.compose(recipe)
    result = lc.run(plan, Path(recipe["state_root"]) / "run", confirm=plan["plan_sha256"])
    assert result["status"] == "unexpected_failure"
    assert result["recovery_required"]
    assert result["missing_checks"] == ["final"]


def test_json_assertions_do_not_accept_missing_null_or_false_as_zero(recipe: dict) -> None:
    step = recipe["steps"][0]
    step["expect_json"] = {"missing": None}
    assert not lc.execute_command(step)["assertions_passed"]
    step["expect_json"] = {"safe": 1}
    assert not lc.execute_command(step)["assertions_passed"]


def test_mutating_effect_is_never_replay_safe(recipe: dict) -> None:
    recipe["steps"][1]["replay_safe"] = True
    with pytest.raises(lc.LifecycleError, match="automatically replayed"):
        lc.compose(recipe)


def test_failed_check_cannot_hide_cleanup_failure(recipe: dict) -> None:
    recipe["steps"][1]["command"]["argv"][-1] = "raise SystemExit(7)"
    cleanup = copy.deepcopy(recipe["steps"][1])
    cleanup.update(id="cleanup", phase="cleanup")
    recipe["steps"].insert(2, cleanup)
    plan = lc.compose(recipe)
    directory = Path(recipe["state_root"]) / "run"
    result = lc.run(plan, directory, confirm=plan["plan_sha256"])
    assert result["status"] == "cleanup_failure"
    assert any(r["step"] == "cleanup" for r in result["records"])
    assert result["recovery_required"]


def test_result_cannot_claim_unattempted_required_check_passed(recipe: dict) -> None:
    plan = lc.compose(recipe)
    directory = Path(recipe["state_root"]) / "run"
    lc.run(plan, directory, confirm=plan["plan_sha256"])
    result = lc.load(directory / "result.json")
    result["records"] = result["records"][:-1]
    lc.save(directory / "result.json", result)
    with pytest.raises(lc.LifecycleError, match="lacks successful execution"):
        lc.report(directory)


def test_partial_publication_is_checkpointed_without_republishing(recipe: dict) -> None:
    recipe["operation"] = "release"
    recipe["steps"][1]["phase"] = "publish"
    recipe["steps"][-1]["command"]["argv"][-1] = "raise SystemExit(8)"
    plan = lc.compose(recipe, publication_selected=True)
    directory = Path(recipe["state_root"]) / "run"
    with pytest.raises(lc.LifecycleError, match="enable-publication"):
        lc.run(plan, directory, confirm=plan["plan_sha256"])
    result = lc.run(plan, directory, confirm=plan["plan_sha256"], enable_publication=True)
    assert result["status"] == "unexpected_failure"
    assert result["published_components"] == ["pi"]
    assert result["recovery_required"]


def test_existing_tag_movement_is_guarded_by_exact_remote_oid() -> None:
    from wsprrypi_qualification.lifecycle_release import tag_push_arguments

    args = tag_push_arguments(
        "origin", "v1.0.0", "a" * 40, expected_old_oid="b" * 40, move_selected=True
    )
    assert args[1] == "--force-with-lease=refs/tags/v1.0.0:" + ("b" * 40)
    assert "--force" not in tag_push_arguments("origin", "v1.0.0", "a" * 40)
    with pytest.raises(lc.LifecycleError):
        tag_push_arguments("origin", "v1.0.0", "a" * 40, expected_old_oid="b" * 40)


def release_request() -> dict:
    from wsprrypi_qualification.lifecycle_release import ci_input_key

    components = {}
    for name in ("pi", "pico", "rp1"):
        inputs = {
            "source": "a" * 40,
            "version": "1.0.0-rc.1",
            "board": "fixture",
            "profile": "test",
            "compiler": "pinned",
            "dependencies": {"sdk": "fixture"},
            "generator": "fixture",
        }
        value = {
            k: "a" * 40 for k in ("source", "tooling_source", "test_source", "embedded_source")
        }
        value.update(
            build_inputs=inputs,
            version="1.0.0-rc.1",
            tag="v1.0.0-rc.1",
            prior_remote_oid=None,
            changed=True,
            move_existing_tag=False,
            published=False,
            artifact_sha256="b" * 64,
            build_inputs_sha256=lc.digest(inputs),
            verified_build_inputs_sha256=lc.digest(inputs),
            canonical_ci="canonical",
            ci_conclusion="success",
            required_jobs=["native"],
            successful_jobs=["native"],
        )
        components[name] = value
    for name, value in components.items():
        value["ci_input_sha256"] = ci_input_key(name, components)
        value["successful_ci_input_sha256"] = value["ci_input_sha256"]
    return {
        "scope": "system",
        "components": components,
        "publication_selected": False,
        "pi_dependency_selections": {"pico": "b" * 64, "rp1": "b" * 64},
    }


def test_release_order_reuse_and_partial_publication() -> None:
    from wsprrypi_qualification.lifecycle_release import release_graph

    value = release_request()
    value["components"]["pico"]["published"] = True
    plan = release_graph(value)
    assert plan["order"] == ["pico", "rp1", "pi"]
    assert plan["partial_delivery"] == ["pico"]
    assert "pico:publish" not in plan["phases"]
    assert "pico:inspect-public-state" in plan["phases"]
    assert all(x["ci_action"] == "reuse" for x in plan["selection"])
    assert plan["phases"].index("rp1:public-consumer-acceptance") < plan["phases"].index(
        "pi:validate"
    )
    value["scope"] = "pico"
    with pytest.raises(ValueError, match="unselected companion"):
        release_graph(value)


def test_stale_companion_cannot_reuse_green_release_ci() -> None:
    from wsprrypi_qualification.lifecycle_release import release_graph

    value = release_request()
    value["components"]["pico"]["test_source"] = "c" * 40
    with pytest.raises(lc.LifecycleError, match="CI key omits"):
        release_graph(value)


def test_foreign_controller_lock_is_never_deleted(tmp_path: Path) -> None:
    path = tmp_path / (lc.digest("host:one") + ".lock")
    with (
        pytest.raises(lc.LifecycleError, match="controller cleanup failed"),
        lc.ownership(tmp_path, ["host:one"]),
    ):
        lc.save(path, {"foreign": "new owner"})
    assert lc.load(path) == {"foreign": "new owner"}


def test_controller_unlock_failure_overrides_passing_checks(
    recipe: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.unlink

    def fail_unlock(path: Path, *args, **kwargs):
        if path.suffix == ".lock":
            raise OSError("fixture cannot retire controller lock")
        return original(path, *args, **kwargs)

    plan = lc.compose(recipe)
    monkeypatch.setattr(Path, "unlink", fail_unlock)
    directory = Path(recipe["state_root"]) / "run"
    result = lc.run(plan, directory, confirm=plan["plan_sha256"])
    assert result["status"] == "cleanup_failure"
    assert result["controller_cleanup"]
    assert result["recovery_required"]
    assert lc.report(directory) == result


def test_changed_input_during_child_execution_is_retained_as_failure(
    recipe: dict, tmp_path: Path
) -> None:
    marker = tmp_path / "immutable input"
    marker.write_text("before")
    step = recipe["steps"][1]
    step["command"]["inputs"].append({"path": str(marker), "sha256": lc.file_sha(marker)})
    step["command"]["argv"][-1] = (
        f'from pathlib import Path;Path({str(marker)!r}).write_text("after");'
        "print('{\"safe\":true}')"
    )
    observed = lc.execute_command(step)
    assert observed["return_code"] == 0
    assert not observed["assertions_passed"]
    assert observed["input_integrity_error"]
    assert observed["stdout"].strip() == '{"safe":true}'


def test_unlisted_git_repository_cannot_hold_controller_state(recipe: dict, tmp_path: Path) -> None:
    other = tmp_path / "unrelated repo"
    other.mkdir()
    subprocess.run(["git", "init", str(other)], check=True, capture_output=True)
    recipe["state_root"] = str(other / "evidence")
    with pytest.raises(lc.LifecycleError, match="protected repository"):
        lc.compose(recipe)


def test_resume_rejects_completed_steps_without_records_before_inspection(
    recipe: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = lc.compose(recipe)
    directory = Path(recipe["state_root"]) / "run"
    lc.run(plan, directory, confirm=plan["plan_sha256"])
    checkpoint = lc.load(directory / "checkpoint.json")
    checkpoint["records"] = []
    lc.save(directory / "checkpoint.json", checkpoint)
    monkeypatch.setattr(
        lc, "execute_command", lambda _: pytest.fail("unverified resume contacted adapter")
    )
    with pytest.raises(lc.LifecycleError, match="lack verified execution"):
        lc.run(plan, directory, confirm=plan["plan_sha256"], resume=True, inspection="inspect")


def test_blocked_preflight_never_runs_cleanup_for_unowned_baseline(recipe: dict) -> None:
    recipe["steps"][0]["command"]["argv"][-1] = "raise SystemExit(3)"
    cleanup = copy.deepcopy(recipe["steps"][1])
    cleanup.update(id="cleanup", phase="cleanup")
    recipe["steps"].insert(-1, cleanup)
    plan = lc.compose(recipe)
    directory = Path(recipe["state_root"]) / "run"
    result = lc.run(plan, directory, confirm=plan["plan_sha256"])
    assert result["status"] == "blocked_prerequisite"
    assert len(result["records"]) == 1
    assert not result["recovery_required"]
