"""Production recipe selection must not acquire a host or fabricate handoff proof."""

from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from wsprrypi_qualification import lifecycle as lc
from wsprrypi_qualification import lifecycle_recipes as recipes


def target_fixture() -> dict[str, str]:
    return {
        "machine_id_sha256": "a" * 64,
        "hostname": "physical-fixture",
        "os_id": "fixture",
        "os_version": "1",
        "kernel": "fixture",
        "board_model": "fixture",
    }


def source(tmp_path: Path) -> Path:
    path = tmp_path / "source with spaces"
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_pico_transaction_root_cannot_contain_harness_staging(tmp_path: Path) -> None:
    config = tmp_path / "bench.json"
    keys = (
        "host",
        "known_hosts",
        "parent",
        "installer_local",
        "schema_local",
        "remote_installer",
        "remote_schema",
        "remote_adapter",
        "transaction_state",
        "picotool",
        "evidence_root",
        "baseline_bundle",
        "candidate_bundle",
    )
    values = {k: "/bench/" + k for k in keys}
    values.update(
        target_identity=target_fixture(),
        serial="0123456789ABCDEF",
        transaction_state="/private/state",
        remote_installer="/private/state/installer.py",
    )
    config.write_text(json.dumps(values))
    with pytest.raises(ValueError, match="outside the production transaction"):
        recipes.pico_request(config, tmp_path / "output", tmp_path, "fresh")


def test_usb_safety_checks_survive_python_optimization() -> None:
    assets = Path(recipes.__file__).parent / "lifecycle_assets"
    for path in assets.glob("*.py.txt"):
        tree = ast.parse(path.read_text())
        assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree)), path


def test_pico_request_pins_remote_adapter_and_leaves_outputs_uncreated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: sys.executable)
    checkout = source(tmp_path)
    core = tmp_path / "core.py"
    core.write_text("fixture")
    schema = tmp_path / "schema.json"
    schema.write_text("{}")
    known = tmp_path / "known hosts"
    known.write_text("fixture")
    config = {
        "host": "fixture",
        "known_hosts": str(known),
        "serial": "0123456789ABCDEF",
        "parent": "1-2",
        "installer_local": str(core),
        "schema_local": str(schema),
        "remote_installer": "/bench/core.py",
        "remote_schema": "/bench/ci/schema.json",
        "remote_adapter": "/bench/cycle.py",
        "transaction_state": "/transactions",
        "picotool": "/tools/picotool",
        "evidence_root": "/evidence/fresh",
        "baseline_bundle": "/bundles/old",
        "candidate_bundle": "/bundles/new",
    }
    config["target_identity"] = target_fixture()
    path = tmp_path / "bench.json"
    path.write_text(json.dumps(config))
    state = tmp_path / "durable state"
    request = recipes.pico_request(path, state, checkout, "fresh")
    plan = lc.compose(request)
    assert not state.exists()
    assert "host:" + config["target_identity"]["machine_id_sha256"] in request["resources"]
    assert "host:" + config["host"] not in request["resources"]
    assert not plan["blockers"]
    assert "Pico adapter changed" in request["steps"][0]["command"]["argv"][-1]
    assert "full-erase-blank" in request["required_checks"]
    assert request["rf_enabled"] is False
    changed = copy.deepcopy(request)
    changed["steps"][1]["mutates"].append("pi")
    with pytest.raises(lc.LifecycleError, match="unselected companion"):
        lc.compose(changed)


def test_station_fixture_cannot_select_backend_or_enable_output() -> None:
    from wsprrypi_qualification.lifecycle_pi import configuration_script, settings_fixture

    with pytest.raises(ValueError, match="station/message"):
        settings_fixture({"settings_fixture": {"Operation": {"Transmit": True}}})
    fixture = settings_fixture({})
    assert len(fixture["CW"]["Message"]) == 32
    ast.parse(configuration_script("seed", fixture))
    ast.parse(configuration_script("restart", fixture))


def test_documented_lifecycle_commands_parse_without_execution() -> None:
    import shlex

    from wsprrypi_qualification.cli import _parser

    root = Path(recipes.__file__).parents[2]
    guide = (root / "docs/development/lifecycle-release.md").read_text()
    for line in guide.splitlines():
        if line.startswith("wsprrypi-qualification lifecycle ") and not line.endswith("--help"):
            arguments = shlex.split(line)[1:]
            _parser().parse_args(arguments)


def test_handoff_validator_rejects_changed_boot_and_unbound_observations(tmp_path: Path) -> None:
    import hashlib

    script = Path(recipes.__file__).parent / "lifecycle_assets/pi_usb_handoff.py.txt"
    before = {
        "safe": True,
        "rf_enabled": False,
        "serial": "fixture",
        "parent": "1-2",
        "target": {"device_id": "a" * 32, "boot_id": "b" * 32},
        "semantics": {"hostname": "fixture.local"},
    }
    old = tmp_path / "before.json"
    old.write_text(json.dumps(before))
    new = tmp_path / "after.json"
    new.write_text(json.dumps(before))
    proof = {
        "verified": True,
        "before_sha256": hashlib.sha256(old.read_bytes()).hexdigest(),
        "observation": {"device_id": "a" * 32, "boot_id": "b" * 32, "output_active": False},
    }
    probe = tmp_path / "probe.json"
    probe.write_text(json.dumps(proof))
    command = [
        sys.executable,
        str(script),
        "verify",
        "--before",
        str(old),
        "--after",
        str(new),
        "--probe",
        str(probe),
        "--output",
        str(tmp_path / "valid.json"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    changed = copy.deepcopy(before)
    changed["target"]["boot_id"] = "c" * 32
    new.write_text(json.dumps(changed))
    command[-1] = str(tmp_path / "invalid.json")
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert not Path(command[-1]).exists()
    new.write_text(json.dumps(before))
    proof["before_sha256"] = "f" * 64
    probe.write_text(json.dumps(proof))
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0


def test_public_system_keeps_component_gates_and_requires_real_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: sys.executable)
    checkout = source(tmp_path)
    (checkout / "scripts").mkdir()
    (checkout / "scripts/install.sh").write_text("fixture")
    known = tmp_path / "known"
    known.write_text("fixture")
    binary = tmp_path / "baseline"
    binary.write_text("fixture")
    core = tmp_path / "core.py"
    core.write_text("fixture")
    schema = tmp_path / "schema.json"
    schema.write_text("{}")
    pi = {
        "host": "fixture",
        "expected_hostname": "fixture",
        "home": "/home/operator",
        "known_hosts": str(known),
        "evidence_root": "/bench",
        "remote_lock": "/bench/lock",
        "baseline_local": str(binary),
        "baseline_remote": "/bench/baseline",
        "baseline_sha256": lc.file_sha(binary),
        "candidate_sha256": "b" * 64,
        "provider_source": "a" * 40,
        "provider_tooling_sha256": "c" * 64,
        "eligible_kernels": ["fixture"],
        "owned_paths": [],
    }
    pico = {
        "host": "fixture",
        "known_hosts": str(known),
        "serial": "0123456789ABCDEF",
        "parent": "1-2",
        "installer_local": str(core),
        "schema_local": str(schema),
        "remote_installer": "/bench/core.py",
        "remote_schema": "/bench/ci/schema.json",
        "remote_adapter": "/bench/cycle.py",
        "transaction_state": "/transactions",
        "picotool": "/tools/picotool",
        "evidence_root": "/evidence",
        "baseline_bundle": "/bundles/old",
        "candidate_bundle": "/bundles/new",
    }
    pi["target_identity"] = target_fixture()
    pico["target_identity"] = target_fixture()
    pi_path = tmp_path / "pi.json"
    pi_path.write_text(json.dumps(pi))
    pico_path = tmp_path / "pico.json"
    pico_path.write_text(json.dumps(pico))
    state = tmp_path / "state"
    request = recipes.system_public_request(pi_path, pico_path, state, checkout, checkout, checkout)
    plan = lc.compose(request)
    assert not state.exists()
    assert plan["blockers"] == []
    checks = {step["check"] for step in request["steps"]}
    assert set(recipes.required_checks("pi-public-eight")).issubset(checks)
    assert set(recipes.required_checks("system")).issubset(checks)
    request["steps"] = [s for s in request["steps"] if s["check"] != "usb-handoff"]
    assert "missing required check: usb-handoff" in lc.compose(request)["blockers"]
    pi["candidate_release_tag"] = "v4.0.0-beta.5"
    pi_path.write_text(json.dumps(pi))
    with pytest.raises(ValueError, match="cannot pin"):
        recipes.pi_public_request(pi_path, state, checkout, checkout)
    feature = recipes.pi_public_request(
        pi_path, state, checkout, checkout, installer_branch="codex/lifecycle-release"
    )
    assert feature["profile"] == "pi-streamed-eight"
    assert feature["inputs"]["default_public_acceptance"] is False
    assert lc.compose(feature)["blockers"] == []
    pi["retained_paths"] = {"/var/lib/rp1-gpclk-dkms": {}}
    with pytest.raises(ValueError, match="Additional properties"):
        lc.validate(pi, "lifecycle-pi-bench")


def test_lifecycle_run_named_directory_is_not_mistaken_for_rf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wsprrypi_qualification import cli, lifecycle_cli

    observed = []
    monkeypatch.setattr(
        lifecycle_cli, "dispatch", lambda args: observed.append(args.run_directory) or 0
    )
    assert cli.main(["lifecycle", "status", "run"]) == 0
    assert observed == [Path("run")]
    with pytest.raises(SystemExit):
        cli.main(
            [
                "lifecycle",
                "execute",
                "plan.json",
                "run",
                "--confirm-plan-sha256",
                "a" * 64,
                "--enable-rf",
            ]
        )
    assert len(observed) == 1


@pytest.mark.parametrize("http_refusal", [False, True])
def test_system_probe_uses_production_mutation_intent_header(
    tmp_path, monkeypatch, http_refusal
) -> None:
    """Discovery identify is read-only in effect but uses the protected POST contract."""
    import io
    import os
    import runpy
    import sys
    import urllib.request

    before = tmp_path / "before.json"
    output = tmp_path / "probe.json"
    before.write_text(
        json.dumps(
            {
                "safe": True,
                "rf_enabled": False,
                "serial": "0123456789ABCDEF",
                "target": {"device_id": "device", "boot_id": "boot"},
            }
        )
    )
    calls = []

    def urlopen(request, *, timeout):
        calls.append(request)
        if isinstance(request, str):
            value = {
                "usb": {
                    "candidates": [
                        {
                            "id": "usb-device",
                            "settings": {
                                "USB Serial": "0123456789ABCDEF",
                                "Transport": "usb",
                            },
                        }
                    ]
                }
            }
        else:
            assert request.get_method() == "POST"
            assert request.get_header("Origin") == "http://127.0.0.1:31415"
            assert request.get_header("Content-type") == "application/json"
            assert request.get_header("X-wsprrypico-request") == "1"
            assert json.loads(request.data)["settings"]["Device ID"] == "device"
            if http_refusal:
                import urllib.error

                raise urllib.error.HTTPError(
                    request.full_url, 409, "Conflict", {}, io.BytesIO(b"shared_lock_refusal")
                )
            value = {
                "binding": "usb",
                "output_active": False,
                "device_id": "device",
                "boot_id": "boot",
            }
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(
        sys,
        "argv",
        ["pi_usb_handoff.py", "probe", "--before", str(before), "--output", str(output)],
    )
    prior_umask = os.umask(0o077)
    try:
        asset = str(Path(recipes.__file__).parent / "lifecycle_assets/pi_usb_handoff.py.txt")
        if http_refusal:
            with pytest.raises(RuntimeError, match=r"409.*shared_lock_refusal"):
                runpy.run_path(asset)
        else:
            runpy.run_path(asset)
    finally:
        os.umask(prior_umask)
    assert len(calls) == 2
    if http_refusal:
        assert not output.exists()
    else:
        assert json.loads(output.read_text())["verified"] is True


def idle_provider(route=None):
    return {
        "state": "exact_ready" if route else "neutral_ready",
        "routeSelected": bool(route),
        "routes": {"active": route},
        "outputEnabled": False,
        "safety": {
            "clock": "quiescent",
            "dma": "quiescent",
            "gpio": "quiescent",
            "endpointOpen": False,
            "owner": False,
            "lease": False,
            "operationalReady": bool(route),
        },
    }


def test_provider_idle_route_requires_explicit_selection():
    from wsprrypi_qualification.lifecycle_pi import verify_provider_idle

    verify_provider_idle(idle_provider())
    verify_provider_idle(idle_provider(), "gpio4")
    verify_provider_idle(idle_provider("gpio4"), "gpio4")
    for route in (None, "gpio20"):
        with pytest.raises(ValueError, match="provider route"):
            verify_provider_idle(idle_provider("gpio4"), route)


@pytest.mark.parametrize(
    "field,value",
    [
        ("outputEnabled", True),
        ("outputEnabled", 0),
        ("routeSelected", 1),
        ("safety.owner", True),
        ("safety.lease", True),
        ("safety.endpointOpen", True),
        ("safety.clock", "active"),
        ("safety.dma", "active"),
        ("safety.gpio", "active"),
        ("safety.operationalReady", False),
        ("safety.owner", None),
        ("routes.active", "gpio20"),
        ("state", "unknown"),
    ],
)
def test_configured_idle_policy_never_accepts_unsafe_or_unbound_state(field, value):
    from wsprrypi_qualification.lifecycle_pi import verify_provider_idle

    observed = idle_provider("gpio4")
    target = observed
    keys = field.split(".")
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    with pytest.raises(ValueError, match="provider"):
        verify_provider_idle(observed, "gpio4")


def test_retained_operator_paths_require_exact_bytes_and_empty_directory(tmp_path):
    import hashlib
    import stat

    asset = Path(recipes.__file__).parent / "lifecycle_assets/pi_absence.py.txt"
    tree = ast.parse(asset.read_text())
    functions = ast.Module(
        body=[n for n in tree.body if isinstance(n, ast.FunctionDef)], type_ignores=[]
    )
    scope = {"P": lambda p: tmp_path / p.lstrip("/"), "hashlib": hashlib, "stat": stat}
    exec(compile(functions, str(asset), "exec"), scope)
    name = "/usr/local/etc/wsprrypi.ini.pre_migration.bak"
    path = scope["P"](name)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"operator fixture")
    info = path.stat()
    record = {
        name: {
            "kind": "file",
            "uid": info.st_uid,
            "gid": info.st_gid,
            "mode": stat.S_IMODE(info.st_mode),
            "reason": "operator backup",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    }
    assert scope["verify_retained"](record) == {name}
    path.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="bytes changed"):
        scope["verify_retained"](record)
    name = "/root/.wsprrypi-wtp"
    directory = scope["P"](name)
    directory.mkdir(parents=True)
    info = directory.stat()
    record = {
        name: {
            "kind": "empty_directory",
            "uid": info.st_uid,
            "gid": info.st_gid,
            "mode": stat.S_IMODE(info.st_mode),
            "reason": "retained endpoint namespace",
        }
    }
    scope["verify_retained"](record)
    (directory / "unexplained").write_text("must block")
    with pytest.raises(RuntimeError, match="not empty"):
        scope["verify_retained"](record)


@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_selected_streamed_branch_is_explicit_and_hash_bound(tmp_path, monkeypatch, operation):
    from wsprrypi_qualification import lifecycle_pi

    config = tmp_path / "bench.json"
    config.write_text(
        json.dumps(
            {
                "host": "fixture",
                "known_hosts": "/known",
                "home": "/home/operator",
                "remote_lock": "/state/lock",
                "expected_hostname": "fixture",
                "evidence_root": "/state",
                "baseline_local": "/baseline",
                "baseline_remote": "/remote-baseline",
                "baseline_sha256": "a" * 64,
                "candidate_sha256": "b" * 64,
                "provider_source": "c" * 40,
                "provider_tooling_sha256": "d" * 64,
                "eligible_kernels": ["fixture"],
                "owned_paths": [],
                "target_identity": target_fixture(),
                "candidate_release_tag": "v4.0.0-beta.5",
            }
        )
    )
    calls = []
    monkeypatch.setattr(lifecycle_pi, "remote", lambda *args, **kw: calls.append(args[2]) or "log")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "lifecycle_pi",
            operation,
            str(config),
            "--installer-source",
            "a" * 40,
            "--installer-sha256",
            "b" * 64,
            "--installer-branch",
            "codex/lifecycle-release",
            "--output",
            str(tmp_path / "result.json"),
        ],
    )
    assert lifecycle_pi.main() == 0
    command = calls[0]
    assert "refs/heads/codex/lifecycle-release" in command
    assert "REPO_BRANCH=codex/lifecycle-release" in command
    assert "public installer bytes changed" in command
    assert ("--binary-source release --release-tag v4.0.0-beta.5" in command) == (
        operation == "install"
    )
    with pytest.raises(ValueError, match="branch reference"):
        lifecycle_pi.installer_url("bad..ref")
    if operation == "install":
        observed = {"host": "fixture", "binary_sha256": "wrong", "runtime": idle_provider()}
        monkeypatch.setattr(lifecycle_pi, "probe", lambda *args: observed)
        failed = tmp_path / "failed-observation.json"
        monkeypatch.setattr(
            sys, "argv", ["lifecycle_pi", "verify", str(config), "--output", str(failed)]
        )
        assert lifecycle_pi.main() == 1
        retained = json.loads(failed.read_text())
        assert retained["observed"] == observed
        assert "executable bytes differ" in retained["verification_error"]
