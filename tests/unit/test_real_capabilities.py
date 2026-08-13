import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.unit.test_capture_metadata import capture_document
from wsprrypi_qualification.application_shims import (
    ApplicationIdentity,
    WsprProtocol,
    WsprryPiBackendConfig,
    WsprryPiShim,
)
from wsprrypi_qualification.real_capabilities import (
    CapabilityError,
    CaptureCapabilityPlan,
    GpioObservation,
    GpioQuiescenceCapability,
    LaunchResult,
    NarrowServiceCapability,
    OpenSshCapability,
    ResolvedCapabilityPlan,
    RuntimeAuthorization,
    SealedFakeGpioProvider,
    SealedFakeLauncher,
    SealedFakeOwnedLauncher,
    SealedFakeOwnedProcess,
    SealedFakeServiceProvider,
    SealedFakeSi5351Provider,
    Si5351Observation,
    Si5351QuiescenceCapability,
    SoapyCaptureCapability,
    SshCapabilityPlan,
    WsprryPiProcessCapability,
    capability_plan_sha256,
    compose_capability_session,
    validate_capability_semantics,
    validate_capability_session_document,
)
from wsprrypi_qualification.remote_exec import decode_arguments


def executable(tmp_path: Path, name: str = "fake ssh with spaces") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    path.write_bytes(b"sealed fake executable")
    return path.resolve()


def authorization(plan: object, *, rf: bool = False) -> RuntimeAuthorization:
    return RuntimeAuthorization(
        capability_plan_sha256(plan), "operator", datetime.now(UTC), True, rf
    )


def test_ssh_records_reversible_remote_boundary(tmp_path: Path) -> None:
    plan = SshCapabilityPlan(
        executable(tmp_path),
        "pi@fixture",
        "/usr/local/bin/wspq-remote-exec",
        ("wsprrypi", "argument with spaces"),
        2,
        3,
        4,
    )
    document = OpenSshCapability(SealedFakeLauncher()).execute(plan, authorization(plan.document()))
    assert document["remote_command"].endswith(document["encoded_remote_command"])
    assert document["intended_remote_arguments"] == list(plan.remote_arguments)
    assert decode_arguments(document["encoded_remote_command"]) == plan.remote_arguments


@pytest.mark.parametrize(
    ("result", "outcome"),
    (
        (LaunchResult(1, stderr="auth failed"), "nonzero_exit"),
        (LaunchResult(None, timed_out=True), "timed_out"),
        (LaunchResult(None, disconnected=True), "disconnected"),
        (LaunchResult(None, cleanup_verified=False), "cleanup_failed"),
    ),
)
def test_ssh_typed_outcomes(tmp_path: Path, result: LaunchResult, outcome: str) -> None:
    plan = SshCapabilityPlan(executable(tmp_path), "host", "/opt/wspq-helper", ("true",), 1, 1, 1)
    document = OpenSshCapability(SealedFakeLauncher(result)).execute(
        plan, authorization(plan.document())
    )
    assert document["outcome"] == outcome


def test_ssh_requires_ephemeral_authorization(tmp_path: Path) -> None:
    plan = SshCapabilityPlan(executable(tmp_path), "host", "/opt/wspq-helper", ("true",), 1, 1, 1)
    with pytest.raises(CapabilityError, match="authorization"):
        OpenSshCapability(SealedFakeLauncher()).execute(plan, None)


def test_ssh_rejects_shell_metacharacters_and_option_like_host(tmp_path: Path) -> None:
    bad_helper = SshCapabilityPlan(
        executable(tmp_path), "host", "/safe/helper;touch_BAD", ("true",), 1, 1, 1
    )
    with pytest.raises(CapabilityError, match="unsafe"):
        OpenSshCapability(SealedFakeLauncher()).execute(
            bad_helper, authorization(bad_helper.document())
        )
    bad_host = SshCapabilityPlan(
        executable(tmp_path), "-oProxyCommand=bad", "/safe/helper", ("true",), 1, 1, 1
    )
    with pytest.raises(CapabilityError, match="destination"):
        OpenSshCapability(SealedFakeLauncher()).execute(
            bad_host, authorization(bad_host.document())
        )


def application_plan(tmp_path: Path):
    return WsprryPiShim(
        ApplicationIdentity("wsprrypi", executable(tmp_path, "fake wsprrypi"), "parent", "sub"),
        backend="si5351",
        backend_config=WsprryPiBackendConfig("CLK0", 2.5, 1, "0x60", 27_000_000, 1),
    ).resolve_plan("plan", WsprProtocol("AA0NT", "EM18", 20, 10_140_200, 3, 1500))


def transmitter_session_plan(plan) -> ResolvedCapabilityPlan:
    bindings = (
        f"wsprrypi_process_capability:{capability_plan_sha256(plan.to_document())}",
        "si5351_quiescence_capability:" + "0" * 64,
    )
    return ResolvedCapabilityPlan(
        "run", "local", False, True, (), "si5351", 10, bindings, True, True
    )


def test_wsprrypi_process_tracks_cleanup_and_typed_failure(tmp_path: Path) -> None:
    plan = application_plan(tmp_path)
    capability = WsprryPiProcessCapability(
        SealedFakeOwnedLauncher(
            SealedFakeOwnedProcess(LaunchResult(None, timed_out=True, cleanup_verified=True))
        )
    )
    session = transmitter_session_plan(plan)
    document = capability.execute(plan, session, authorization(session.document(), rf=True), 10)
    assert document["outcome"] == "timed_out"
    assert document["cleanup_verified"] is True


def test_wsprrypi_rejects_tampered_or_missing_authorization(tmp_path: Path) -> None:
    plan = application_plan(tmp_path)
    with pytest.raises(CapabilityError, match="authorization"):
        WsprryPiProcessCapability(SealedFakeOwnedLauncher(SealedFakeOwnedProcess())).execute(
            plan, transmitter_session_plan(plan), None, 10
        )


def test_wsprrypi_requires_separate_rf_authorization_before_begin(tmp_path: Path) -> None:
    plan = application_plan(tmp_path)
    process = SealedFakeOwnedProcess()
    session = transmitter_session_plan(plan)
    with pytest.raises(CapabilityError, match="RF authorization"):
        WsprryPiProcessCapability(SealedFakeOwnedLauncher(process)).execute(
            plan, session, authorization(session.document()), 10
        )
    assert process.stopped is False


def test_service_changes_only_named_service_and_restores() -> None:
    provider = SealedFakeServiceProvider({"wsprrypi": True})
    capability = NarrowServiceCapability(provider, frozenset({"wsprrypi"}))
    plan = {"name": "wsprrypi", "requested_running": False}
    document = capability.apply_and_restore("wsprrypi", False, authorization(plan))
    assert document["changed_by_harness"] is True
    assert document["restoration_verified"] is True
    assert provider.states["wsprrypi"] is True
    with pytest.raises(CapabilityError, match=r"authorization|named"):
        capability.apply_and_restore("other", False, authorization(plan))


class FailingRestoreProvider(SealedFakeServiceProvider):
    def set_running(self, name: str, running: bool) -> None:
        if running:
            raise RuntimeError("restore failed")
        super().set_running(name, running)


def test_service_cleanup_failure_is_retained_as_evidence() -> None:
    provider = FailingRestoreProvider({"wsprrypi": True})
    plan = {"name": "wsprrypi", "requested_running": False}
    document = NarrowServiceCapability(provider, frozenset({"wsprrypi"})).apply_and_restore(
        "wsprrypi", False, authorization(plan)
    )
    assert document["outcome"] == "cleanup_failed"
    assert "restore failed" in document["failure_cause"]


def test_gpio_quiescence_is_read_only_and_backend_specific() -> None:
    auth = authorization({"pin": 4, "expected_direction": "input", "read_only": True})
    passed = GpioQuiescenceCapability(SealedFakeGpioProvider(GpioObservation(4, "input"))).inspect(
        4, auth
    )
    failed = GpioQuiescenceCapability(
        SealedFakeGpioProvider(GpioObservation(4, "output", "foreign"))
    ).inspect(4, auth)
    assert passed["verified"] is True
    assert failed["verified"] is False


def test_si5351_requires_matching_identity_and_disabled_outputs() -> None:
    plan = {"bus": 1, "address": "0x60", "required_outputs": ["CLK0"], "read_only": True}
    auth = authorization(plan)
    passed = Si5351QuiescenceCapability(
        SealedFakeSi5351Provider(Si5351Observation(1, "0x60", ()))
    ).inspect(1, "0x60", ("CLK0",), auth)
    enabled = Si5351QuiescenceCapability(
        SealedFakeSi5351Provider(Si5351Observation(1, "0x60", ("CLK0",)))
    ).inspect(1, "0x60", ("CLK0",), auth)
    wrong = Si5351QuiescenceCapability(
        SealedFakeSi5351Provider(Si5351Observation(2, "0x61", ()))
    ).inspect(1, "0x60", ("CLK0",), auth)
    assert passed["verified"] is True
    assert enabled["verified"] is False
    assert wrong["verified"] is False


def test_cancellation_never_calls_an_external_provider(tmp_path: Path) -> None:
    plan = SshCapabilityPlan(executable(tmp_path), "host", "/opt/wspq-helper", ("true",), 1, 1, 1)
    cancellation = threading.Event()
    cancellation.set()
    document = OpenSshCapability(SealedFakeLauncher()).execute(
        plan, authorization(plan.document()), cancellation
    )
    assert document["outcome"] == "cancelled"


class FakeCaptureLauncher:
    def __init__(
        self, plan: CaptureCapabilityPlan, *, overflow: int = 0, clipping_threshold: float = 0.999
    ) -> None:
        self.plan, self.overflow, self.clipping_threshold = plan, overflow, clipping_threshold

    def launch(self, arguments, timeout_s, cancellation):
        self.arguments = arguments
        self.plan.output_path.write_bytes(bytes(self.plan.sample_count * 8))
        metadata = capture_document()
        settings = {
            "format": "CF32",
            "sample_rate_hz": self.plan.sample_rate_hz,
            "bandwidth_hz": self.plan.bandwidth_hz,
            "center_frequency_hz": self.plan.center_frequency_hz,
            "gain_db": self.plan.gain_db,
            "channel": self.plan.channel,
            "agc": False,
            "bias_tee": False,
        }
        device = {"driver": self.plan.driver, "serial": self.plan.serial}
        metadata.update(
            requested_device=device,
            resolved_device=device,
            requested_settings=settings,
            actual_settings=settings,
            requested_sample_count=self.plan.sample_count,
            retained_sample_count=self.plan.sample_count,
            overflow_count=self.overflow,
        )
        metadata["clipping"]["threshold"] = self.clipping_threshold
        import hashlib

        metadata["output"].update(
            path=str(self.plan.output_path),
            size_bytes=self.plan.output_path.stat().st_size,
            sha256=hashlib.sha256(self.plan.output_path.read_bytes()).hexdigest(),
        )
        self.plan.metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        return LaunchResult(0)


def capture_plan(tmp_path: Path) -> CaptureCapabilityPlan:
    return CaptureCapabilityPlan(
        executable(tmp_path, "fake capture helper"),
        tmp_path / "capture.json",
        tmp_path / "capture.cf32",
        "sdrplay",
        "SERIAL",
        0,
        250_000,
        200_000,
        1_838_100,
        10,
        10,
        2_000_000,
        5,
        0.999,
    )


def test_soapy_adapter_validates_exact_capture_without_opening_sdr(tmp_path: Path) -> None:
    plan = capture_plan(tmp_path)
    launcher = FakeCaptureLauncher(plan)
    document = SoapyCaptureCapability(launcher).execute(plan, authorization(plan.document()))
    assert document["output"]["size_bytes"] == 80
    assert launcher.arguments == (
        str(plan.helper),
        "--enable-physical-sdr",
        "sdrplay",
        "SERIAL",
        "1.8381e+06",
        "10",
        "10",
        "250000",
        "200000",
        "0",
        "false",
        "false",
        "2000000",
        "5",
        str(plan.output_path),
        str(plan.metadata_path),
        plan.output_path.stem,
    )


def test_soapy_adapter_accepts_only_float32_rounding_of_clipping_threshold(
    tmp_path: Path,
) -> None:
    plan = capture_plan(tmp_path)
    SoapyCaptureCapability(
        FakeCaptureLauncher(plan, clipping_threshold=0.99900001287460327)
    ).execute(plan, authorization(plan.document()))
    different = capture_plan(tmp_path / "different")
    with pytest.raises(CapabilityError, match="identity or settings"):
        SoapyCaptureCapability(FakeCaptureLauncher(different, clipping_threshold=0.998)).execute(
            different, authorization(different.document())
        )


def test_soapy_adapter_rejects_overflow_and_output_collision(tmp_path: Path) -> None:
    plan = capture_plan(tmp_path)
    with pytest.raises(CapabilityError, match="exact-count"):
        SoapyCaptureCapability(FakeCaptureLauncher(plan, overflow=1)).execute(
            plan, authorization(plan.document())
        )
    assert not plan.output_path.exists()
    assert not plan.metadata_path.exists()
    collision = capture_plan(tmp_path / "other")
    collision.output_path.write_bytes(b"preserve")
    with pytest.raises(CapabilityError, match="new"):
        SoapyCaptureCapability(SealedFakeLauncher()).execute(
            collision, authorization(collision.document())
        )


def test_semantic_validation_rejects_tampered_ssh_encoding(tmp_path: Path) -> None:
    plan = SshCapabilityPlan(executable(tmp_path), "host", "/opt/wspq-helper", ("true",), 1, 1, 1)
    document = OpenSshCapability(SealedFakeLauncher()).execute(plan, authorization(plan.document()))
    document["encoded_remote_command"] = '["false"]'
    with pytest.raises(CapabilityError, match="encoding"):
        validate_capability_semantics(document)


def test_resolved_plan_fails_closed_without_explicit_enablement() -> None:
    plan = ResolvedCapabilityPlan("run", "local", True, False, (), "none", 10)
    with pytest.raises(CapabilityError, match="enablement"):
        plan.document()


def test_session_composition_is_evidence_only_and_never_qualifies() -> None:
    plan = ResolvedCapabilityPlan(
        "run", "local", False, False, (), "none", 10, external_access_enabled=False
    )
    document = compose_capability_session(plan, ())
    assert document["qualification_status"] == "inconclusive"
    assert document["capabilities"] == []
    document["plan_sha256"] = "0" * 64
    with pytest.raises(CapabilityError, match="digest"):
        validate_capability_session_document(document)
