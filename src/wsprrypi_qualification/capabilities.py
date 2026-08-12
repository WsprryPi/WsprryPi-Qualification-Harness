"""Read-only platform and dependency discovery."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Any

from wsprrypi_qualification.models import CapabilityResult, CapabilityState
from wsprrypi_qualification.tool_discovery import discover_executable

EXTERNAL_TOOLS = ("wsprd", "ffmpeg", "SoapySDRUtil", "cmake", "ssh")


def _tool_capability(name: str) -> CapabilityResult:
    found = discover_executable(name)
    if found is None:
        return CapabilityResult(
            name,
            CapabilityState.UNAVAILABLE,
            "executable not found on PATH or a supported platform bundle location",
        )
    return CapabilityResult(
        name,
        CapabilityState.AVAILABLE,
        "absolute executable path discovered without execution",
        found,
    )


def capability_report() -> dict[str, Any]:
    tools = [_tool_capability(name).to_dict() for name in EXTERNAL_TOOLS]
    mock_only = {
        "ssh_command",
        "local_soapy_capture",
        "service_inspection",
        "gpio_quiescence",
        "si5351_quiescence",
    }
    adapters = [
        CapabilityResult(
            name,
            CapabilityState.AVAILABLE
            if name == "local_command"
            else CapabilityState.UNSUPPORTED
            if name in mock_only
            else CapabilityState.NOT_IMPLEMENTED,
            "bounded local child execution is implemented"
            if name == "local_command"
            else (
                "native capture helper is implemented and wspr5-validated; "
                "portable live orchestration remains unsupported"
            )
            if name == "local_soapy_capture"
            else "mock/fake contract is testable; real operation is disabled in Slice 4"
            if name in mock_only
            else "adapter is outside Slice 4",
        ).to_dict()
        for name in (
            "local_command",
            "ssh_command",
            "local_soapy_capture",
            "remote_capture",
            "service_inspection",
            "gpio_quiescence",
            "si5351_quiescence",
            "rp1_gpclk",
        )
    ]
    return {
        "schema_version": 1,
        "read_only": True,
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": str(Path(sys.executable).resolve()),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "os_name": os.name,
        },
        "external_tools": tools,
        "adapters": adapters,
    }
