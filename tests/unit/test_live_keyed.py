import json
from pathlib import Path

import pytest

import wsprrypi_qualification.live_adapters as live_adapters_module
import wsprrypi_qualification.live_keyed as live_keyed_module
from tests.unit.test_keyed_session_contracts import plan
from wsprrypi_qualification.cli import main
from wsprrypi_qualification.keyed_session_contracts import (
    compose_keyed_runtime_authorization,
    resolved_keyed_plan_sha256,
)
from wsprrypi_qualification.live_keyed import (
    ProductionKeyedAdapter,
    run_live_keyed_session,
)
from wsprrypi_qualification.offline import artifact


class SealedFakeLiveProviders:
    __slots__ = ("calls", "close_ok", "evidence_root", "failure")

    def __init__(
        self,
        failure: str | None = None,
        *,
        close_ok: bool = True,
        evidence_root: Path | None = None,
    ) -> None:
        if type(self) is not SealedFakeLiveProviders:
            raise TypeError("live keyed fake provider is sealed")
        self.failure, self.close_ok = failure, close_ok
        self.evidence_root = evidence_root
        self.calls: list[tuple[str, int]] = []

    def _perform(self, name: str, number: int) -> bool:
        self.calls.append((name, number))
        return self.failure != name

    def preflight(self, resolved, number):
        del resolved
        return self._perform("preflight", number)

    def install_cleanup(self, resolved, number):
        del resolved
        return self._perform("cleanup_installed", number)

    def start_process(self, arguments, number):
        assert arguments[0] == "inputs/wsprrypi.json"
        return f"fake-process-{number}" if self._perform("process_started", number) else None

    def capture(self, resolved, number):
        del resolved
        return (
            (f"fake-capture-{number}", f"fake-acquisition-{number}")
            if self._perform("capture_completed", number)
            else None
        )

    def analyze(self, resolved, number):
        del resolved
        return (
            ("passed", f"fake-analysis-{number}")
            if self._perform("analysis_completed", number)
            else ("failed", f"failed-analysis-{number}")
        )

    def cleanup(self, resolved, number):
        del resolved
        return self._perform("cleanup_completed", number)

    def verify_quiescence(self, resolved, number):
        del resolved
        return self._perform("quiescence_verified", number)

    def evidence_paths(self, number):
        if self.evidence_root is None:
            return {}
        directory = self.evidence_root / str(number)
        directory.mkdir(parents=True)
        result = {}
        for role in ("process", "process_launch", "capture", "acquisition", "analysis"):
            path = directory / f"{role}.json"
            path.write_text(f"{role}-{number}\n", encoding="utf-8")
            result[role] = path
        return result

    def close(self):
        return self.close_ok


def _run(tmp_path: Path, providers: SealedFakeLiveProviders, mode: str = "QRSS"):
    resolved = plan(mode)
    authorization = compose_keyed_runtime_authorization(
        resolved, operator="operator", authorized_utc="2026-08-21T12:00:00Z"
    )
    return run_live_keyed_session(
        resolved,
        authorization,
        tmp_path,
        ProductionKeyedAdapter(providers),
    )


@pytest.mark.parametrize("mode", ("QRSS", "FSKCW", "DFCW"))
def test_production_adapter_success_uses_three_independent_transactions(
    tmp_path: Path, mode: str
) -> None:
    providers = SealedFakeLiveProviders()
    outcome = _run(tmp_path, providers, mode)
    assert outcome["result"]["final_status"] == "qualified"
    assert len(outcome["aggregate"]["transactions"]) == 3
    assert {number for _, number in providers.calls} == {1, 2, 3}


@pytest.mark.parametrize(
    "failure,expected",
    (
        ("preflight", "preflight_failed"),
        ("cleanup_installed", "aborted"),
        ("process_started", "aborted"),
        ("capture_completed", "unqualified_keyed"),
        ("analysis_completed", "unqualified_keyed"),
        ("cleanup_completed", "cleanup_failed"),
        ("quiescence_verified", "cleanup_failed"),
    ),
)
def test_every_production_boundary_fails_closed_and_preserves_partial_output(
    tmp_path: Path, failure: str, expected: str
) -> None:
    providers = SealedFakeLiveProviders(failure)
    outcome = _run(tmp_path, providers)
    assert outcome["result"]["final_status"] == expected
    assert len(outcome["aggregate"]["transactions"]) == 1
    assert Path(outcome["bundle"]).is_dir()
    assert (Path(outcome["bundle"]) / "transaction-1.json").is_file()
    assert ("cleanup_completed", 1) in providers.calls
    assert ("quiescence_verified", 1) in providers.calls


def test_incomplete_provider_close_overrides_measurement_success(tmp_path: Path) -> None:
    outcome = _run(tmp_path, SealedFakeLiveProviders(close_ok=False))
    assert outcome["result"]["final_status"] == "cleanup_failed"
    assert len(outcome["aggregate"]["transactions"]) == 3


def test_production_evidence_bytes_are_retained_and_authenticated(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    outcome = _run(tmp_path / "output", SealedFakeLiveProviders(evidence_root=source_root))
    bundle = Path(outcome["bundle"])
    for transaction in outcome["aggregate"]["transactions"]:
        for record in transaction["artifacts"]:
            retained = bundle / record["path"]
            assert retained.read_text(encoding="utf-8") == (
                f"{record['role']}-{transaction['transaction_number']}\n"
            )


def test_cli_requires_every_gate_before_provider_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    resolved = plan()
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(resolved), encoding="utf-8")
    called = False

    def fail_builder(*args, **kwargs):
        nonlocal called
        del args, kwargs
        called = True
        raise AssertionError("provider builder must not run after digest rejection")

    monkeypatch.setattr(live_keyed_module, "build_production_keyed_adapter", fail_builder)
    result = main(
        [
            "run-cw-live-keyed",
            str(plan_path),
            str(tmp_path / "out"),
            "--work-directory",
            str(tmp_path / "work"),
            "--ssh",
            str(tmp_path / "ssh"),
            "--operator",
            "operator",
            "--confirm-plan-sha256",
            "0" * 64,
            "--enable-live-keyed",
            "--enable-rf",
        ]
    )
    assert result == 2
    assert not called
    assert "digest confirmation" in capsys.readouterr().err


def test_cli_success_path_uses_sealed_production_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    resolved = plan()
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(resolved), encoding="utf-8")
    monkeypatch.setattr(
        live_keyed_module,
        "build_production_keyed_adapter",
        lambda *args, **kwargs: ProductionKeyedAdapter(SealedFakeLiveProviders()),
    )
    result = main(
        [
            "run-cw-live-keyed",
            str(plan_path),
            str(tmp_path / "out"),
            "--work-directory",
            str(tmp_path / "work"),
            "--ssh",
            str(tmp_path / "ssh"),
            "--operator",
            "operator",
            "--confirm-plan-sha256",
            resolved_keyed_plan_sha256(resolved),
            "--enable-live-keyed",
            "--enable-rf",
        ]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["result"]["final_status"] == "qualified"


def test_cli_parser_requires_live_keyed_and_rf_flags() -> None:
    with pytest.raises(SystemExit):
        main(["run-cw-live-keyed"])


def test_production_provider_builder_uses_exact_pinned_ssh_and_helper_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = plan()
    bindings = resolved["capability_bindings"]
    local_paths = {}
    for name in (
        "ssh",
        "known_hosts",
        "receiver_helper",
        "receiver_helper_config",
        "capture_helper",
    ):
        path = (tmp_path / name).resolve()
        path.write_text(name, encoding="utf-8")
        local_paths[name] = path
        bindings[name] = artifact(path)
    bindings["transmitter_helper"]["path"] = "/opt/wspq/helper"
    bindings["transmitter_helper_config"]["path"] = "/etc/wspq/helper.json"
    commands = []

    class FakePersistentTransport:
        def __init__(self, command, cleanup_timeout_s):
            commands.append((command, cleanup_timeout_s))

        def exchange(self, encoded_request, timeout_s):
            raise AssertionError((encoded_request, timeout_s))

        def close(self):
            return None

    monkeypatch.setattr(live_adapters_module, "PersistentHelperTransport", FakePersistentTransport)
    providers = live_adapters_module.build_keyed_capability_providers(
        resolved,
        ssh_executable=local_paths["ssh"],
        work_directory=(tmp_path / "work").resolve(),
    )
    ssh_command = commands[0][0]
    assert ssh_command == (
        str(local_paths["ssh"]),
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={local_paths['known_hosts']}",
        "--",
        resolved["transmitter"]["host"],
        "/opt/wspq/helper --serve --config /etc/wspq/helper.json",
    )
    assert commands[1][0] == (
        str(local_paths["receiver_helper"]),
        "--serve",
        "--config",
        str(local_paths["receiver_helper_config"]),
    )
    assert providers.close()
