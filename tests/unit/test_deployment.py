# ruff: noqa: E501

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from wsprrypi_qualification.deployment import (
    DeploymentError,
    PinnedCommand,
    SystemdRestoration,
    inspect_gpio,
    inspect_si5351,
    inspect_systemd,
    load_deployment_config,
    runtime_helper_config,
    validate_provider_evidence,
    validate_systemd_transaction,
)

PLAN = "a" * 64


def fake_provider(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "fake provider with spaces.py"
    path.write_text(
        """import json, sys, time
from pathlib import Path
op = sys.argv[1]; state=Path(__file__).with_suffix('.state')
behavior=Path(__file__).with_suffix('.behavior').read_text() if Path(__file__).with_suffix('.behavior').exists() else ''
if op == 'sleep': time.sleep(5)
if op == 'systemd-inspect':
 print(json.dumps({'service':sys.argv[2],'active_state':state.read_text() if state.exists() else 'active','unit_file_state':'enabled','load_state':'loaded'}))
elif op == 'systemd-set':
 if behavior == 'fail-before': sys.exit(7)
 state.write_text(sys.argv[3])
 if behavior == 'timeout-after': time.sleep(5)
 if behavior == 'nonzero-after': sys.exit(7)
 if behavior == 'malformed-after': print('not-json'); sys.exit(0)
 print(json.dumps({'service':sys.argv[2],'active_state':sys.argv[3],'unit_file_state':'enabled','load_state':'loaded'}))
elif op == 'gpio-inspect':
 c=json.loads(sys.argv[2]); time.sleep(5) if c.get('delay') else None; print(json.dumps({'chip':c['chip'],'line':c['line'],'direction':'input','function':'input','owner':None,'value':0}))
elif op == 'si5351-inspect':
 c=json.loads(sys.argv[2]); print(json.dumps({'bus':c['bus'],'address':c['address'],'device':'Si5351','enabled_outputs':[],'owner':None}))
""",
        encoding="utf-8",
    )
    return path.resolve()


def command(tmp_path: Path, provider_type: str, timeout: float = 1) -> PinnedCommand:
    script = fake_provider(tmp_path)
    python = Path(sys.executable).resolve()
    digest = hashlib.sha256(python.read_bytes()).hexdigest()
    return PinnedCommand(
        provider_type,
        python,
        digest,
        timeout,
        PLAN,
        "configured-host",
        (str(script),),
        ((script, hashlib.sha256(script.read_bytes()).hexdigest()),),
    )


def test_fake_systemd_gpio_and_si5351_are_structured_and_read_only(tmp_path: Path):
    service = inspect_systemd(
        command(tmp_path / "s", "systemd"), "wsprrypi.service", frozenset({"wsprrypi.service"})
    )
    gpio = inspect_gpio(
        command(tmp_path / "g", "gpio"),
        {"chip": "gpiochip0", "line": 4, "direction": "input", "function": "input"},
    )
    si = inspect_si5351(
        command(tmp_path / "i", "si5351"),
        {"bus": 1, "address": "0x60", "device": "Si5351", "required_outputs": ["CLK0"]},
    )
    assert service["parsed_result"]["active_state"] == "active"
    assert gpio["parsed_result"]["direction"] == "input"
    assert si["parsed_result"]["enabled_outputs"] == []
    assert gpio["mutation_performed"] is si["mutation_performed"] is False
    assert any(item.endswith("fake provider with spaces.py") for item in gpio["arguments"])


def test_systemd_restores_only_its_recorded_change_and_detects_drift(tmp_path: Path):
    transaction = SystemdRestoration(
        command(tmp_path / "restore", "systemd"),
        "wsprrypi.service",
        frozenset({"wsprrypi.service"}),
    )
    changed = transaction.set_active(False)
    assert changed["mutation_performed"] is True
    restored = transaction.restore()
    assert restored["mutation_performed"] is True
    fresh = SystemdRestoration(
        command(tmp_path / "fresh", "systemd"),
        "wsprrypi.service",
        frozenset({"wsprrypi.service"}),
    )
    with pytest.raises(DeploymentError, match="not changed"):
        fresh.restore()


@pytest.mark.parametrize(
    ("behavior", "cause", "timeout"),
    [
        ("timeout-after", "timeout", 2.0),
        ("nonzero-after", "nonzero_exit", 2.0),
        ("malformed-after", "malformed_response", 2.0),
    ],
)
def test_uncertain_systemd_mutation_remains_restorable(
    tmp_path: Path, behavior: str, cause: str, timeout: float
):
    provider = command(tmp_path / behavior, "systemd", timeout=timeout)
    script = Path(provider.prefix_arguments[0])
    script.with_suffix(".behavior").write_text(behavior, encoding="utf-8")
    transaction = SystemdRestoration(provider, "wsprrypi.service", frozenset({"wsprrypi.service"}))
    result = transaction.set_active(False)
    assert result["outcome"] == "blocked" and result["cause"] == cause
    assert result["mutation_performed"] is None
    assert transaction.change_attempted is True and transaction.changed_to == "inactive"
    assert script.with_suffix(".state").read_text(encoding="utf-8") == "inactive"
    script.with_suffix(".behavior").unlink()
    restored = transaction.restore()
    assert restored["outcome"] == "completed"
    assert script.with_suffix(".state").read_text(encoding="utf-8") == "active"
    assert transaction.restoration_complete is True
    with pytest.raises(DeploymentError, match="already complete"):
        transaction.restore()


def test_failed_mutation_confirmed_unchanged_needs_no_second_set(tmp_path: Path):
    provider = command(tmp_path / "unchanged", "systemd")
    script = Path(provider.prefix_arguments[0])
    script.with_suffix(".behavior").write_text("fail-before", encoding="utf-8")
    transaction = SystemdRestoration(provider, "wsprrypi.service", frozenset({"wsprrypi.service"}))
    result = transaction.set_active(False)
    assert result["cause"] == "nonzero_exit"
    script.with_suffix(".behavior").unlink()
    evidence_count = len(transaction.evidence)
    verified = transaction.restore()
    assert verified["parsed_result"]["active_state"] == "active"
    assert len(transaction.evidence) == evidence_count + 1
    assert transaction.restoration_complete is True


def test_transaction_evidence_is_snapshot_and_tamper_validated(tmp_path: Path):
    transaction = SystemdRestoration(
        command(tmp_path / "transaction", "systemd"),
        "wsprrypi.service",
        frozenset({"wsprrypi.service"}),
    )
    returned = transaction.set_active(False)
    returned["contract"]["service"] = "tampered.service"
    exposed = transaction.evidence
    exposed[0]["parsed_result"]["active_state"] = "failed"
    document = transaction.transaction_document()
    assert document["service"] == "wsprrypi.service"
    assert document["steps"][0]["evidence"]["parsed_result"]["active_state"] == "active"
    assert document["steps"][1]["evidence"]["contract"]["service"] == "wsprrypi.service"
    transaction.restore()
    complete = transaction.transaction_document()
    assert complete["cleanup_outcome"] == "verified"

    for mutate in (
        lambda item: item["steps"].pop(1),
        lambda item: item["steps"].reverse(),
        lambda item: item.update(initial_state="failed"),
        lambda item: item.update(service="other.service"),
        lambda item: item.update(mutation_effect="confirmed_unchanged"),
        lambda item: item.update(cleanup_outcome="required", completed=False),
        lambda item: item.update(allowed_services=["other.service"]),
    ):
        altered = json.loads(json.dumps(complete))
        mutate(altered)
        with pytest.raises((DeploymentError, ValueError)):
            validate_systemd_transaction(altered)

    backwards = json.loads(json.dumps(complete))
    backwards["steps"][0]["evidence"]["completed_utc"] = "2026-01-01T00:00:01Z"
    backwards["steps"][1]["evidence"]["started_utc"] = "2026-01-01T01:00:00+02:00"
    with pytest.raises(DeploymentError, match="timestamps"):
        validate_systemd_transaction(backwards)


def test_uncertain_restoration_failure_stays_retryable(tmp_path: Path):
    provider = command(tmp_path / "restore-failure", "systemd", timeout=0.2)
    script = Path(provider.prefix_arguments[0])
    transaction = SystemdRestoration(provider, "wsprrypi.service", frozenset({"wsprrypi.service"}))
    transaction.set_active(False)
    script.with_suffix(".behavior").write_text("timeout-after", encoding="utf-8")
    failed = transaction.restore()
    assert failed["outcome"] == "blocked"
    assert failed["cleanup_verified"] is False
    assert transaction.restoration_complete is False
    script.with_suffix(".behavior").unlink()
    verified = transaction.restore()
    assert verified["parsed_result"]["active_state"] == "active"
    assert transaction.restoration_complete is True


def test_post_attempt_inspection_failure_keeps_cleanup_required(tmp_path: Path):
    provider = command(tmp_path / "verify-failure", "systemd")
    script = Path(provider.prefix_arguments[0])
    transaction = SystemdRestoration(provider, "wsprrypi.service", frozenset({"wsprrypi.service"}))
    original_inspect = transaction.inspect
    inspections = 0

    def fail_second_inspection():
        nonlocal inspections
        inspections += 1
        if inspections == 2:
            raise DeploymentError("injected verification failure")
        return original_inspect()

    transaction.inspect = fail_second_inspection  # type: ignore[method-assign]
    with pytest.raises(DeploymentError, match="verification failure"):
        transaction.set_active(False)
    assert transaction.change_attempted is True
    assert transaction.restoration_complete is False
    uncertain = transaction.transaction_document()
    assert uncertain["mutation_effect"] == "uncertain"
    altered = json.loads(json.dumps(uncertain))
    altered["mutation_effect"] = "confirmed_unchanged"
    with pytest.raises(DeploymentError, match="mutation effect"):
        validate_systemd_transaction(altered)
    transaction.inspect = original_inspect  # type: ignore[method-assign]
    transaction.restore()
    assert script.with_suffix(".state").read_text(encoding="utf-8") == "active"


def test_systemd_transaction_rejects_reuse_and_state_drift(tmp_path: Path):
    provider = command(tmp_path / "drift", "systemd")
    script = Path(provider.prefix_arguments[0])
    transaction = SystemdRestoration(provider, "wsprrypi.service", frozenset({"wsprrypi.service"}))
    transaction.set_active(False)
    with pytest.raises(DeploymentError, match="already changed"):
        transaction.set_active(True)
    script.with_suffix(".state").write_text("failed", encoding="utf-8")
    with pytest.raises(DeploymentError, match="drift"):
        transaction.restore()


def test_failed_initial_service_state_is_rejected_before_mutation(tmp_path: Path):
    provider = command(tmp_path / "initial-failed", "systemd")
    script = Path(provider.prefix_arguments[0])
    state = script.with_suffix(".state")
    state.write_text("failed", encoding="utf-8")
    transaction = SystemdRestoration(provider, "wsprrypi.service", frozenset({"wsprrypi.service"}))
    with pytest.raises(DeploymentError, match="not safely restorable"):
        transaction.set_active(False)
    assert state.read_text(encoding="utf-8") == "failed"
    assert transaction.change_attempted is False
    assert len(transaction.evidence) == 1


def test_allowlist_hash_replacement_timeout_and_read_only_semantics(tmp_path: Path):
    with pytest.raises(DeploymentError, match="allowlist"):
        inspect_systemd(
            command(tmp_path / "a", "systemd"), "other", frozenset({"wsprrypi.service"})
        )
    pinned = tmp_path / "replace" / "provider"
    pinned.parent.mkdir(parents=True)
    pinned.write_bytes(b"v1")
    cmd = PinnedCommand(
        "gpio", pinned.resolve(), hashlib.sha256(b"v1").hexdigest(), 1, PLAN, "host"
    )
    pinned.write_bytes(b"replacement")
    with pytest.raises(DeploymentError, match="identity changed"):
        inspect_gpio(cmd, {"chip": "gpiochip0", "line": 4})
    document = {
        "schema_version": 1,
        "evidence_type": "deployment_provider_execution",
        "provider_type": "gpio",
        "host": "host",
        "plan_sha256": PLAN,
        "executable": {"path": str(Path(sys.executable).resolve()), "sha256": "b" * 64},
        "executed_artifacts": [],
        "arguments": [
            str(Path(sys.executable).resolve()),
            "gpio-inspect",
            json.dumps({}, sort_keys=True),
        ],
        "prefix_argument_count": 0,
        "started_utc": "2026-08-12T00:00:00Z",
        "completed_utc": "2026-08-12T00:00:01Z",
        "elapsed_s": 1,
        "deadline_s": 2,
        "return_code": 0,
        "timed_out": False,
        "stdout": "{}",
        "stderr": "",
        "contract": {},
        "parsed_result": {},
        "outcome": "completed",
        "cause": None,
        "mutation_performed": True,
        "cleanup_verified": True,
    }
    with pytest.raises(DeploymentError, match="read-only"):
        validate_provider_evidence(document)


def test_strict_response_rejects_wrong_identity_and_extra_fields(tmp_path: Path):
    document = inspect_gpio(
        command(tmp_path, "gpio"),
        {"chip": "gpiochip0", "line": 4, "direction": "input", "function": "input"},
    )
    document["parsed_result"]["extra"] = True
    with pytest.raises(DeploymentError, match="contradicts"):
        validate_provider_evidence(document)


def test_prefix_code_path_must_be_the_exact_pinned_artifact(tmp_path: Path):
    actual = fake_provider(tmp_path / "actual")
    innocent = fake_provider(tmp_path / "innocent")
    python = Path(sys.executable).resolve()
    with pytest.raises(DeploymentError, match="exact pin"):
        PinnedCommand(
            "gpio",
            python,
            hashlib.sha256(python.read_bytes()).hexdigest(),
            1,
            PLAN,
            "host",
            (str(actual),),
            ((innocent, hashlib.sha256(innocent.read_bytes()).hexdigest()),),
        )
    late = (tmp_path / "late.py").resolve()
    with pytest.raises(DeploymentError, match="exact pin"):
        PinnedCommand(
            "gpio",
            python,
            hashlib.sha256(python.read_bytes()).hexdigest(),
            1,
            PLAN,
            "host",
            (str(late),),
        )


def test_retained_evidence_cannot_omit_executed_script_pin(tmp_path: Path):
    document = inspect_gpio(
        command(tmp_path, "gpio"),
        {"chip": "gpiochip0", "line": 4, "direction": "input", "function": "input"},
    )
    document["executed_artifacts"] = []
    document["prefix_argument_count"] = 0
    with pytest.raises(DeploymentError, match="executed artifacts"):
        validate_provider_evidence(document)


def test_timeout_and_active_gpio_are_typed_and_blocked(tmp_path: Path):
    slow = command(tmp_path / "slow", "gpio", timeout=0.01)
    timed = slow.run(
        (
            "gpio-inspect",
            json.dumps({"chip": "gpiochip0", "delay": True, "line": 4}, sort_keys=True),
        ),
        {"chip": "gpiochip0", "delay": True, "line": 4},
    )
    assert timed["outcome"] == "blocked" and timed["cause"] == "timeout"
    active = command(tmp_path / "active", "gpio")
    script = Path(active.prefix_arguments[0])
    script.write_text(
        script.read_text(encoding="utf-8").replace(
            "'direction':'input','function':'input'",
            "'direction':'output','function':'gpclk'",
        ),
        encoding="utf-8",
    )
    active = PinnedCommand(
        active.provider_type,
        active.executable,
        active.sha256,
        active.timeout_s,
        active.plan_sha256,
        active.host,
        active.prefix_arguments,
        ((script, hashlib.sha256(script.read_bytes()).hexdigest()),),
    )
    result = inspect_gpio(
        active,
        {"chip": "gpiochip0", "line": 4, "direction": "input", "function": "input"},
    )
    assert result["outcome"] == "blocked" and result["cause"] == "active_output"


def test_retained_evidence_binds_arguments_contract_and_result(tmp_path: Path):
    gpio = inspect_gpio(
        command(tmp_path / "gpio", "gpio"),
        {"chip": "gpiochip0", "line": 4, "direction": "input", "function": "input"},
    )
    gpio["arguments"][-1] = json.dumps(
        {"chip": "gpiochip9", "line": 99, "direction": "input", "function": "input"},
        sort_keys=True,
    )
    with pytest.raises(DeploymentError, match="payload contradicts"):
        validate_provider_evidence(gpio)

    systemd = inspect_systemd(
        command(tmp_path / "systemd", "systemd"),
        "wsprrypi.service",
        frozenset({"wsprrypi.service"}),
    )
    systemd["contract"]["service"] = "other.service"
    systemd["parsed_result"]["service"] = "other.service"
    with pytest.raises(DeploymentError, match="arguments contradict"):
        validate_provider_evidence(systemd)

    transaction = SystemdRestoration(
        command(tmp_path / "mutation", "systemd"),
        "wsprrypi.service",
        frozenset({"wsprrypi.service"}),
    )
    mutation = transaction.set_active(False)
    mutation["parsed_result"]["active_state"] = "active"
    with pytest.raises(DeploymentError, match="response contradicts"):
        validate_provider_evidence(mutation)


def test_deployment_config_validates_absolute_pinned_files(tmp_path: Path):
    executable = tmp_path / "safe fixture executable with spaces"
    executable.write_bytes(b"reviewed deployment fixture\n")
    if os.name != "nt":
        executable.chmod(0o700)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    entry = {"path": str(executable), "sha256": digest}
    config = {
        "schema_version": 1,
        "protocol_version": 1,
        "helper_identity": "fixture-helper",
        "plan_sha256": PLAN,
        "target_host": "fixture-host",
        "venv_path": str(tmp_path / "venv"),
        "config_path": str(tmp_path / "config.json"),
        "state_directory": str(tmp_path / "state"),
        "allowed_services": ["wsprrypi.service"],
        "bounded_tone_endpoint": {
            "host": "127.0.0.1",
            "port": 31416,
            "path": "/",
            "maximum_frame_bytes": 16384,
        },
        "wsprrypi_revision": "1" * 40,
        "executables": {
            name: entry for name in ("python", "helper", "systemctl", "gpio", "si5351")
        },
        "gpio_contract": {
            "chip": "gpiochip0",
            "line": 4,
            "direction": "input",
            "function": "input",
        },
        "si5351_contract": {
            "bus": 1,
            "address": "0x60",
            "device": "Si5351",
            "required_outputs": ["CLK0"],
        },
    }
    path = tmp_path / "deployment config with spaces.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    assert load_deployment_config(path)["helper_identity"] == "fixture-helper"
    runtime_path = tmp_path / "runtime helper.json"
    runtime_path.write_text(
        json.dumps(runtime_helper_config(load_deployment_config(path))), encoding="utf-8"
    )
    from wsprrypi_qualification.capability_helper import load_server_config

    loaded_server = load_server_config(runtime_path)
    assert loaded_server.identity == "fixture-helper"
    assert loaded_server.bounded_tone.wsprrypi_revision == "1" * 40
    original_digest = entry["sha256"]
    entry["sha256"] = "0" * 64
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(DeploymentError, match="SHA-256 mismatch"):
        load_deployment_config(path)
    entry["sha256"] = original_digest
    executable.write_bytes(b"replaced fixture bytes\n")
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(DeploymentError, match="SHA-256 mismatch"):
        load_deployment_config(path)
    executable.write_bytes(b"reviewed deployment fixture\n")
    if os.name != "nt":
        executable.chmod(0o702)
        with pytest.raises(DeploymentError, match="world-writable"):
            load_deployment_config(path)
        executable.chmod(0o700)
    config["allowed_services"] = []
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError):
        load_deployment_config(path)
