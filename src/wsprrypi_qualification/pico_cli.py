"""Pico target routing inside the maintained complete-test command."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
from typing import Any

from wsprrypi_qualification.complete_test import _parse_sdr_selector
from wsprrypi_qualification.pico_campaign import (
    MODE_ORDER,
    compose_pico_campaign,
    validate_configuration,
)
from wsprrypi_qualification.pico_live import run_pico_campaign
from wsprrypi_qualification.pico_wire import PicoWireError, loads
from wsprrypi_qualification.progress import ProgressReporter


def add_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pico-configuration",
        type=Path,
        help="select a preconfigured Pico USB target and receiver profile",
    )
    parser.add_argument(
        "--pico-compiler",
        type=Path,
        help="offline job compiler built against the intended Pico source",
    )
    parser.add_argument(
        "--pico-output-parent",
        type=Path,
        default=Path.home() / ".local/state/wsprrypi-qualification/runs",
    )


def execute(args: argparse.Namespace, reporter: ProgressReporter) -> dict[str, Any]:
    config = loads(args.pico_configuration.read_bytes())
    validate_configuration(config)
    allowed = {
        "--pico-configuration",
        "--pico-compiler",
        "--pico-output-parent",
        "--sdr",
        "--mode",
        "--enable-rf",
        "--rehearse",
        "--progress-log",
        "--progress-stream",
    }
    if set(getattr(args, "_explicit_options", ())) - allowed:
        raise PicoWireError("Pico settings belong in its configuration; unsupported CLI overrides")
    if args.pico_compiler is None:
        raise PicoWireError("Pico complete-test requires --pico-compiler")
    if any(
        (
            args.configuration,
            args.rf_path,
            args.transmit_gpio,
            args.rp1_route,
            args.wsprrypi_source,
        )
    ):
        raise PicoWireError("Pico and Pi deployment/backend options cannot be mixed")
    if args.receiver_host != config["receiver"]["host"] or args.transmitter_host not in {
        "pico",
        config["pico"]["serial"],
    }:
        raise PicoWireError("CLI target/receiver differs from the Pico configuration")
    selector = _parse_sdr_selector(args.sdr)
    if selector != {"driver": config["receiver"]["driver"], "serial": config["receiver"]["serial"]}:
        raise PicoWireError("CLI SDR selector differs from the Pico receiver binding")
    modes = tuple(mode.lower() for mode in args.mode) if args.mode else MODE_ORDER
    plan = compose_pico_campaign(config, args.pico_compiler, modes)
    return run_pico_campaign(
        plan,
        args.pico_output_parent,
        reporter,
        enable_rf=args.enable_rf,
        operator=getpass.getuser(),
        rehearse=args.rehearse,
    )
