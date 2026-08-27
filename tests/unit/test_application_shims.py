import pytest

from wsprrypi_qualification.application_shims import (
    ApplicationIdentity,
    ApplicationPlanError,
    CwProtocol,
    ProtocolMode,
    ToneProtocol,
    WsprProtocol,
    WsprryPiBackendConfig,
    WsprryPiShim,
    validate_application_plan,
)
from wsprrypi_qualification.cw_defaults import (
    CANONICAL_KEYED_TEST_MESSAGE,
    hardware_free_keyed_protocol,
)


def identity(executable: str = "/opt/Wsprry Pi/wsprrypi") -> ApplicationIdentity:
    return ApplicationIdentity("wsprrypi", executable, "parent-sha", "submodule-sha")


def test_wspr_plan_is_bounded_and_not_authorized() -> None:
    plan = WsprryPiShim(identity(), backend="si5351").resolve_plan(
        "three-frames", WsprProtocol("Q0QQQ", "JJ00", 0, 144_490_500, 3, 1500)
    )
    assert plan.arguments == (
        "/opt/Wsprry Pi/wsprrypi",
        "--backend",
        "si5351",
        "--no-offset",
        "--terminate",
        "3",
        "Q0QQQ",
        "JJ00",
        "0",
        "144489000",
    )
    assert not plan.execution_authorized
    assert plan.supervisor_required
    assert plan.protocol_contract["requested_rf_frequency_hz"] == 144_490_500
    assert plan.protocol_contract["dial_frequency_hz"] == 144_489_000
    validate_application_plan(plan.to_document())


def test_carrier_tone_plan_requires_external_supervisor() -> None:
    plan = WsprryPiShim(identity(), backend="si5351").resolve_plan(
        "carrier", ToneProtocol(1_838_100)
    )
    assert plan.arguments[-2:] == ("--test-tone", "1838100")
    assert not plan.self_terminating_request
    assert plan.supervisor_required
    assert not plan.execution_authorized
    validate_application_plan(plan.to_document())


@pytest.mark.parametrize(
    ("backend", "config", "expected"),
    (
        (
            "si5351",
            WsprryPiBackendConfig("CLK0", 2.5, 1, "0x60", 27_000_000, 1),
            ("--si5351-tx-output", "CLK0", "--si5351-power-level", "1"),
        ),
        (
            "gpio",
            WsprryPiBackendConfig("GPIO4", -1.25, drive_or_power_level=0, gpio_pin=4),
            ("--transmit-gpio", "4", "--gpio-power-level", "0"),
        ),
    ),
)
def test_resolved_backend_contract_authenticates_complete_arguments(
    backend: str, config: WsprryPiBackendConfig, expected: tuple[str, ...]
) -> None:
    plan = WsprryPiShim(identity(), backend=backend, backend_config=config).resolve_plan(
        "resolved", WsprProtocol("Q0QQQ", "JJ00", 0, 10_140_200, 3, 1500)
    )
    assert all(item in plan.arguments for item in expected)
    if backend == "gpio":
        assert plan.arguments.count("--no-system-clock-frequency-estimate") == 1
        assert plan.arguments.count("--gpio-manual-ppm") == 1
    else:
        assert "--no-system-clock-frequency-estimate" not in plan.arguments
    validate_application_plan(plan.to_document())
    changed = plan.to_document()
    arguments = list(changed["arguments"])
    arguments[arguments.index(expected[-1])] = "99"
    changed["arguments"] = arguments
    with pytest.raises(ApplicationPlanError, match="backend contract"):
        validate_application_plan(changed)


@pytest.mark.parametrize("level", (-1, 8))
def test_gpio_power_level_is_bounded_by_application_contract(level: int) -> None:
    with pytest.raises(ApplicationPlanError, match="0 through 7"):
        WsprryPiShim(
            identity(),
            backend="gpio",
            backend_config=WsprryPiBackendConfig(
                "GPIO4", 0, drive_or_power_level=level, gpio_pin=4
            ),
        ).resolve_plan("gpio-level", ToneProtocol(14_097_100))


@pytest.mark.parametrize("level", (0, 5))
def test_si5351_power_level_is_bounded_by_application_contract(level: int) -> None:
    with pytest.raises(ApplicationPlanError, match="1 through 4"):
        WsprryPiShim(
            identity(),
            backend="si5351",
            backend_config=WsprryPiBackendConfig("CLK0", 0, 1, "0x60", 27_000_000, level),
        ).resolve_plan("si5351-level", ToneProtocol(14_097_100))


@pytest.mark.parametrize(
    ("mode", "flags"),
    (
        (ProtocolMode.QRSS, ("--qrss-frequency",)),
        (ProtocolMode.FSKCW, ("--fskcw-mark-frequency", "--fskcw-space-frequency")),
        (ProtocolMode.DFCW, ("--dfcw-dot-frequency", "--dfcw-dash-frequency")),
    ),
)
def test_qrss_family_uses_mode_specific_transient_interface(
    mode: ProtocolMode, flags: tuple[str, ...]
) -> None:
    protocol = hardware_free_keyed_protocol(
        mode.value,
        primary_frequency_hz=10_140_100.0,
        pre_quiet_seconds=1.0,
        post_quiet_seconds=1.0,
    )
    plan = WsprryPiShim(identity(), backend="gpio").resolve_plan(
        "cw-plan",
        CwProtocol(
            mode,
            str(protocol["message"]),
            float(protocol["dot_seconds"]),
            float(protocol["primary_frequency_hz"]),
            None
            if protocol["secondary_frequency_hz"] is None
            else float(protocol["secondary_frequency_hz"]),
        ),
    )
    assert all(flag in plan.arguments for flag in flags)
    assert "--repeat" not in plan.arguments
    assert CANONICAL_KEYED_TEST_MESSAGE in plan.arguments and "0.7" in plan.arguments
    validate_application_plan(plan.to_document())


def test_hellschreiber_is_explicitly_unsupported() -> None:
    shim = WsprryPiShim(identity(), backend="gpio")
    assert ProtocolMode.HELLSCHREIBER not in shim.supported_protocols()
    with pytest.raises(ApplicationPlanError, match="unsupported"):
        shim.resolve_plan("hell", CwProtocol(ProtocolMode.HELLSCHREIBER, "TEST", 1, 10_140_100))


def test_missing_secondary_tone_is_rejected() -> None:
    with pytest.raises(ApplicationPlanError, match="positive"):
        WsprryPiShim(identity(), backend="gpio").resolve_plan(
            "fsk", CwProtocol(ProtocolMode.FSKCW, "TEST", 3, 10_140_100)
        )


def test_fskcw_mark_must_be_above_space() -> None:
    with pytest.raises(ApplicationPlanError, match="mark frequency"):
        WsprryPiShim(identity(), backend="gpio").resolve_plan(
            "fsk",
            CwProtocol(ProtocolMode.FSKCW, "TEST", 3, 10_140_100, 10_140_101),
        )


def test_dfcw_tones_must_differ() -> None:
    with pytest.raises(ApplicationPlanError, match="must differ"):
        WsprryPiShim(identity(), backend="gpio").resolve_plan(
            "dfcw",
            CwProtocol(ProtocolMode.DFCW, "TEST", 3, 10_140_100, 10_140_100),
        )


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_non_finite_values_are_rejected(value: float) -> None:
    with pytest.raises(ApplicationPlanError):
        WsprryPiShim(identity(), backend="gpio").resolve_plan(
            "qrss", CwProtocol(ProtocolMode.QRSS, "TEST", value, 10_140_100)
        )


def test_wspr_requires_application_supported_identity_power_and_offset() -> None:
    shim = WsprryPiShim(identity(), backend="gpio")
    with pytest.raises(ApplicationPlanError, match="uppercase"):
        shim.resolve_plan("case", WsprProtocol("q0qqq", "JJ00", 0, 10_140_200, 3, 1500))
    with pytest.raises(ApplicationPlanError, match="standard"):
        shim.resolve_plan("power", WsprProtocol("Q0QQQ", "JJ00", 1, 10_140_200, 3, 1500))
    with pytest.raises(ApplicationPlanError, match="1500"):
        shim.resolve_plan("offset", WsprProtocol("Q0QQQ", "JJ00", 0, 10_140_200, 3, 1200))


@pytest.mark.parametrize(
    ("route", "pin", "output", "compatibility_id"),
    (
        ("gpio4", 4, "GPIO4", "v1.1.2-pi5-gpio4-6.18.34-development-candidate-r4"),
        ("gpio20", 20, "GPIO20", "v1.1.2-pi5-gpio20-6.18.34-development-candidate-r4"),
    ),
)
def test_rp1_backend_is_route_bound_and_applies_ppm_once(
    route: str, pin: int, output: str, compatibility_id: str
) -> None:
    config = WsprryPiBackendConfig(
        output,
        -3.56,
        drive_or_power_level=0,
        gpio_pin=pin,
        rp1_route=route,
        endpoint="/dev/rp1-gpclk",
        compatibility_id=compatibility_id,
        abi_version=4,
        finite_tone_required=True,
        development_enrollment="Experimental",
        live_output_required=True,
        operation_live_gate_required=True,
        rp1_drive_ma=2,
    )
    plan = WsprryPiShim(identity(), backend="rp1_gpclk", backend_config=config).resolve_plan(
        f"rp1-{route}", WsprProtocol("Q0QQQ", "JJ00", 0, 10_140_200, 3, 1500)
    )
    assert plan.arguments[:13] == (
        "/opt/Wsprry Pi/wsprrypi",
        "--backend",
        "rp1-gpclk",
        "--transmit-gpio",
        str(pin),
        "--gpio-power-level",
        "0",
        "--rp1-gpio-drive-ma",
        "2",
        "--no-system-clock-frequency-estimate",
        "--gpio-manual-ppm",
        "-3.56",
        "--no-offset",
    )
    assert plan.arguments.count("--gpio-manual-ppm") == 1
    assert plan.arguments.count("--no-system-clock-frequency-estimate") == 1
    validate_application_plan(plan.to_document())


def test_rp1_backend_rejects_missing_or_cross_route_identity() -> None:
    with pytest.raises(ApplicationPlanError, match="explicit route"):
        WsprryPiShim(
            identity(), backend="rp1_gpclk", backend_config=WsprryPiBackendConfig("GPIO4", 0)
        ).resolve_plan("missing-route", ToneProtocol(14_097_100))
    with pytest.raises(ApplicationPlanError, match="mismatched"):
        WsprryPiShim(
            identity(),
            backend="rp1_gpclk",
            backend_config=WsprryPiBackendConfig(
                "GPIO4",
                0,
                drive_or_power_level=0,
                gpio_pin=4,
                rp1_route="gpio4",
                endpoint="/dev/rp1-gpclk",
                compatibility_id="v1.1.2-pi5-gpio20-6.18.34-development-candidate-r4",
                abi_version=4,
                finite_tone_required=True,
                development_enrollment="Experimental",
                live_output_required=True,
                operation_live_gate_required=True,
                rp1_drive_ma=2,
            ),
        ).resolve_plan("wrong-route", ToneProtocol(14_097_100))


def test_windows_path_with_spaces_is_preserved_as_one_argument() -> None:
    executable = r"C:\Program Files\WsprryPi\wsprrypi.exe"
    plan = WsprryPiShim(identity(executable), backend="si5351").resolve_plan(
        "windows", WsprProtocol("Q0QQQ", "JJ00", 0, 10_140_200, 3, 1500)
    )
    document = plan.to_document()
    assert document["identity"]["executable"] == executable
    assert plan.arguments[0] == executable
    assert len([item for item in plan.arguments if "Program Files" in item]) == 1
    validate_application_plan(document)


def test_posix_path_with_spaces_round_trips_without_host_normalization() -> None:
    executable = "/opt/Wsprry Pi/wsprrypi"
    plan = WsprryPiShim(identity(executable), backend="si5351").resolve_plan(
        "posix", WsprProtocol("Q0QQQ", "JJ00", 0, 10_140_200, 3, 1500)
    )
    document = plan.to_document()
    assert document["identity"]["executable"] == executable
    assert document["arguments"][0] == executable
    validate_application_plan(document)


def test_schema_rejects_execution_authorization() -> None:
    document = (
        WsprryPiShim(identity(), backend="gpio")
        .resolve_plan("safe", WsprProtocol("Q0QQQ", "JJ00", 0, 10_140_200, 3, 1500))
        .to_document()
    )
    document["execution_authorized"] = True
    with pytest.raises(ApplicationPlanError):
        validate_application_plan(document)


def test_schema_rejects_protocol_argument_mismatch() -> None:
    document = (
        WsprryPiShim(identity(), backend="gpio")
        .resolve_plan("safe", WsprProtocol("Q0QQQ", "JJ00", 0, 10_140_200, 3, 1500))
        .to_document()
    )
    document["protocol"] = "qrss"
    with pytest.raises(ApplicationPlanError):
        validate_application_plan(document)


def test_schema_rejects_wspr_frequency_semantic_mismatch() -> None:
    document = (
        WsprryPiShim(identity(), backend="gpio")
        .resolve_plan("safe", WsprProtocol("Q0QQQ", "JJ00", 0, 10_140_200, 3, 1500))
        .to_document()
    )
    contract = document["protocol_contract"]
    assert isinstance(contract, dict)
    contract["requested_rf_frequency_hz"] = 10_140_201
    with pytest.raises(ApplicationPlanError, match="frequency contract"):
        validate_application_plan(document)


@pytest.mark.parametrize(
    ("field", "value"),
    (("frame_count", 3.9), ("frame_count", 3.0), ("power_dbm", 0.9), ("power_dbm", 0.0)),
)
def test_validator_rejects_lossy_numeric_contract(field: str, value: float) -> None:
    document = (
        WsprryPiShim(identity(), backend="gpio")
        .resolve_plan("safe", WsprProtocol("Q0QQQ", "JJ00", 0, 10_140_200, 3, 1500))
        .to_document()
    )
    contract = document["protocol_contract"]
    assert isinstance(contract, dict)
    contract[field] = value
    with pytest.raises(ApplicationPlanError):
        validate_application_plan(document)
