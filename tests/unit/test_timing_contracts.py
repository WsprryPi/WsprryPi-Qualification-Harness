from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_complete_campaign_has_no_independent_two_hour_cutoff() -> None:
    source = _source("src/wsprrypi_qualification/complete_test.py")
    assert 'campaign_deadline_s"] > 7200' not in source
    assert '"campaign_deadline_s": config["campaign_deadline_s"]' not in source
    assert '"campaign_deadline_s": sum(' in source
    assert 'max(plan["deadlines"]["transmitter_s"], 380)' not in source
    assert 'max(plan["deadlines"]["receiver_s"], 390)' not in source


def test_supervisor_overall_is_the_exact_sequential_phase_sum() -> None:
    supervisor = _source("src/wsprrypi_qualification/supervisor.py")
    fixture = _source("src/wsprrypi_qualification/qualification_session.py")
    assert "overall_s: float = 12.0" in supervisor
    assert "overall_s=monitor_s + 11" in fixture
    assert "overall_s=monitor_s + 12" not in fixture


def test_live_timing_has_no_fixed_nested_readiness_or_arming_cutoff() -> None:
    source = _source("src/wsprrypi_qualification/live_adapters.py")
    assert 'min(5.0, plan["deadlines"]["transaction_s"])' not in source
    assert "min(0.25, pre_quiet / 2.0)" not in source
    assert 'epoch + plan["tone_server"]["startup_seconds"]' not in source


def test_tone_cleanup_uses_the_protocol_remainder() -> None:
    source = _source("src/wsprrypi_qualification/bounded_tone_control.py")
    assert "min(1.0, (outer_timeout_s - duration_ms / 1000) / 2)" not in source
    assert "cleanup_reserve_s = (outer_timeout_s - duration_ms / 1000) / 2" in source


def test_keyed_deadline_has_no_generic_five_second_reserve() -> None:
    source = _source("src/wsprrypi_qualification/complete_test.py")
    assert "math.ceil(capture_seconds) + 5" not in source
    assert "required_keyed_transaction_deadline(" in source
    capability = _source("src/wsprrypi_qualification/real_capabilities.py")
    assert "plan.maximum_elapsed_s + 5" not in capability


def test_opt_in_compilation_has_no_machine_speed_deadline() -> None:
    source = _source("src/wsprrypi_qualification/automatic_deployment.py")
    assert "timeout=900" not in source
    assert "timeout=600" not in source
    assert "timeout=180" not in source
    assert "run_python_to_completion" in source


def test_remote_preparation_and_delegation_have_no_speed_guess() -> None:
    deployment = _source("src/wsprrypi_qualification/automatic_deployment.py")
    staging = _source("src/wsprrypi_qualification/remote_staging.py")
    complete = _source("src/wsprrypi_qualification/complete_test.py")
    assert "timeout_s: float = 7500.0" not in deployment
    assert "timeout_s: float = 7500.0" not in complete
    assert "timeout_s: float = 60.0" not in staging
    assert "timeout_s: float = 30.0" not in staging
    assert "run_python_stream_to_completion" in deployment
    assert '"--wait-for-completion"' in complete


def test_repository_inspection_uses_its_bound_parent_envelope() -> None:
    source = _source("src/wsprrypi_qualification/repository_protection.py")
    assert "timeout=10" not in source
    helper = _source("src/wsprrypi_qualification/capability_helper.py")
    assert 'raw_guard.get("inspection_timeout_s")' in helper


def test_remote_child_cleanup_splits_its_bound_envelope_between_escalations() -> None:
    helper = _source("src/wsprrypi_qualification/capability_helper.py")
    assert "child.cleanup_timeout_s / 2" in helper
    assert "wait(timeout=0.25)" not in helper
    assert "join(timeout=0.2)" not in helper


def test_forwarded_command_cleanup_uses_the_capability_plan_remainder() -> None:
    remote = _source("src/wsprrypi_qualification/remote_exec.py")
    capability = _source("src/wsprrypi_qualification/real_capabilities.py")
    assert "options.cleanup_timeout / 3" in remote
    assert "process.wait(timeout=30)" not in remote
    assert "process.wait(timeout=5)" not in remote
    assert "cleanup_timeout_s = plan.overall_timeout_s - plan.command_timeout_s" in capability
    assert "self._launcher.launch(arguments, plan.overall_timeout_s, cancellation)" in capability
