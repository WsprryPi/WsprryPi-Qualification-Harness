"""Read-only platform and dependency discovery."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from wsprrypi_qualification.models import CapabilityResult, CapabilityState

EXTERNAL_TOOLS = ("wsprd", "ffmpeg", "SoapySDRUtil", "cmake", "ssh")


def _tool_capability(name: str) -> CapabilityResult:
    found = shutil.which(name)
    if found is None:
        return CapabilityResult(name, CapabilityState.UNAVAILABLE, "executable not found on PATH")
    return CapabilityResult(
        name,
        CapabilityState.AVAILABLE,
        "absolute executable path discovered without execution",
        Path(found).resolve(),
    )


def capability_report() -> dict[str, Any]:
    tools = [_tool_capability(name).to_dict() for name in EXTERNAL_TOOLS]
    adapters = [
        CapabilityResult(
            name,
            CapabilityState.NOT_IMPLEMENTED,
            "adapter is outside Slice 1",
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
