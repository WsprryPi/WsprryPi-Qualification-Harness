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


def identity(executable: str = "/opt/Wsprry Pi/wsprrypi") -> ApplicationIdentity:
    return ApplicationIdentity("wsprrypi", executable, "parent-sha", "submodule-sha")


def test_wspr_plan_is_bounded_and_not_authorized() -> None:
    plan = WsprryPiShim(identity(), backend="si5351").resolve_plan(
        "three-frames", WsprProtocol("AA0NT", "EM18", 20, 144_490_500, 3, 1500)
    )
    assert plan.arguments == (
        "/opt/Wsprry Pi/wsprrypi",
        "--backend",
        "si5351",
        "--no-offset",
        "--terminate",
        "3",
        "AA0NT",
        "EM18",
        "20",
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
        "resolved", WsprProtocol("AA0NT", "EM18", 20, 10_140_200, 3, 1500)
    )
    assert all(item in plan.arguments for item in expected)
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
    second = None if mode is ProtocolMode.QRSS else 10_140_098.5
    plan = WsprryPiShim(identity(), backend="gpio").resolve_plan(
        "cw-plan", CwProtocol(mode, "TEST DE AA0NT", 3.0, 10_140_100.0, second)
    )
    assert all(flag in plan.arguments for flag in flags)
    assert "--repeat" not in plan.arguments
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
        shim.resolve_plan("case", WsprProtocol("aa0nt", "EM18", 20, 10_140_200, 3, 1500))
    with pytest.raises(ApplicationPlanError, match="standard"):
        shim.resolve_plan("power", WsprProtocol("AA0NT", "EM18", 21, 10_140_200, 3, 1500))
    with pytest.raises(ApplicationPlanError, match="1500"):
        shim.resolve_plan("offset", WsprProtocol("AA0NT", "EM18", 20, 10_140_200, 3, 1200))


def test_unimplemented_backend_is_rejected() -> None:
    with pytest.raises(ApplicationPlanError, match=r"gpio.*si5351"):
        WsprryPiShim(identity(), backend="rp1_gpclk")


def test_windows_path_with_spaces_is_preserved_as_one_argument() -> None:
    executable = r"C:\Program Files\WsprryPi\wsprrypi.exe"
    plan = WsprryPiShim(identity(executable), backend="si5351").resolve_plan(
        "windows", WsprProtocol("AA0NT", "EM18", 20, 10_140_200, 3, 1500)
    )
    document = plan.to_document()
    assert document["identity"]["executable"] == executable
    assert plan.arguments[0] == executable
    assert len([item for item in plan.arguments if "Program Files" in item]) == 1
    validate_application_plan(document)


def test_posix_path_with_spaces_round_trips_without_host_normalization() -> None:
    executable = "/opt/Wsprry Pi/wsprrypi"
    plan = WsprryPiShim(identity(executable), backend="si5351").resolve_plan(
        "posix", WsprProtocol("AA0NT", "EM18", 20, 10_140_200, 3, 1500)
    )
    document = plan.to_document()
    assert document["identity"]["executable"] == executable
    assert document["arguments"][0] == executable
    validate_application_plan(document)


def test_schema_rejects_execution_authorization() -> None:
    document = (
        WsprryPiShim(identity(), backend="gpio")
        .resolve_plan("safe", WsprProtocol("AA0NT", "EM18", 20, 10_140_200, 3, 1500))
        .to_document()
    )
    document["execution_authorized"] = True
    with pytest.raises(ApplicationPlanError):
        validate_application_plan(document)


def test_schema_rejects_protocol_argument_mismatch() -> None:
    document = (
        WsprryPiShim(identity(), backend="gpio")
        .resolve_plan("safe", WsprProtocol("AA0NT", "EM18", 20, 10_140_200, 3, 1500))
        .to_document()
    )
    document["protocol"] = "qrss"
    with pytest.raises(ApplicationPlanError):
        validate_application_plan(document)


def test_schema_rejects_wspr_frequency_semantic_mismatch() -> None:
    document = (
        WsprryPiShim(identity(), backend="gpio")
        .resolve_plan("safe", WsprProtocol("AA0NT", "EM18", 20, 10_140_200, 3, 1500))
        .to_document()
    )
    contract = document["protocol_contract"]
    assert isinstance(contract, dict)
    contract["requested_rf_frequency_hz"] = 10_140_201
    with pytest.raises(ApplicationPlanError, match="frequency contract"):
        validate_application_plan(document)


@pytest.mark.parametrize(
    ("field", "value"),
    (("frame_count", 3.9), ("frame_count", 3.0), ("power_dbm", 20.9), ("power_dbm", 20.0)),
)
def test_validator_rejects_lossy_numeric_contract(field: str, value: float) -> None:
    document = (
        WsprryPiShim(identity(), backend="gpio")
        .resolve_plan("safe", WsprProtocol("AA0NT", "EM18", 20, 10_140_200, 3, 1500))
        .to_document()
    )
    contract = document["protocol_contract"]
    assert isinstance(contract, dict)
    contract[field] = value
    with pytest.raises(ApplicationPlanError):
        validate_application_plan(document)
