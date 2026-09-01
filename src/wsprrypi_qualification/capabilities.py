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
    descriptions = {
        "local_command": "bounded local child execution",
        "ssh_command": "structured bounded OpenSSH transport",
        "local_soapy_capture": "exact-count local SoapySDR capture through the native helper",
        "service_inspection": (
            "allowlisted service inspection, restoration, and transaction recording"
        ),
        "gpio_quiescence": "backend-specific GPIO idle-state verification",
        "si5351_quiescence": "backend-specific Si5351 output-disable verification",
        "carrier_analysis": (
            "RF-off-subtracted continuous-carrier analysis with authenticated "
            "Matplotlib Agg PNG/SVG plotting"
        ),
        "wspr_decode": "UTC-slot WAV generation, independent wsprd execution, and decode summary",
        "cw_analysis": "tone, QRSS, FSKCW, and DFCW reference, IQ, replay, and mode analysis",
        "receiver_calibration": (
            "frozen SDR Calibration Profile 1.0.0 bindings and receiver-only frequency "
            "interpretation for recorded, Tone, WSPR, QRSS, FSKCW, and DFCW evidence"
        ),
        "live_keyed_contracts": (
            "offline-only QRSS, FSKCW, and DFCW three-transaction plan, authorization, "
            "aggregate, result, and artifact-index validation"
        ),
        "hardware_free_keyed_coordination": (
            "sealed deterministic QRSS, FSKCW, and DFCW three-transaction lifecycle "
            "rehearsal with failure and cancellation injection"
        ),
        "live_keyed_coordination": (
            "digest-authorized three-transaction QRSS, FSKCW, and DFCW coordination "
            "through authenticated helper, capture, service, and quiescence adapters"
        ),
        "live_wspr_coordination": (
            "digest-authorized split-host carrier gate and three-frame WSPR lifecycle"
        ),
        "live_tone_coordination": "digest-authorized bounded live TONE lifecycle",
        "turnkey_campaign_orchestration": (
            "typed route planning, exact-digest confirmation, deterministic hardware-free "
            "rehearsal, and dispatch to the maintained real-session or live-keyed coordinator"
        ),
        "complete_test_campaign": (
            "two-host, exact-SDR selectable TONE, WSPR, QRSS, FSKCW, and DFCW "
            "composition with an all-mode default, hardware-free rehearsal, and "
            "invocation-authorized dispatch"
        ),
    }
    adapters = [
        CapabilityResult(
            name,
            CapabilityState.AVAILABLE,
            description,
        ).to_dict()
        for name, description in descriptions.items()
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
