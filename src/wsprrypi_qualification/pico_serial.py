"""Explicit POSIX Pico USB capability; portable planning never imports OS IO.

Physical-parent/CDC-role discovery and the per-board lock use the selected
Pico's maintained installer. Firmware installation is never invoked here.
"""

from __future__ import annotations

import importlib.util
import platform
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.pico_wire import PicoWireError


class PicoCleanupError(PicoWireError):
    def __init__(self, primary: BaseException, cleanup: BaseException) -> None:
        super().__init__(f"{primary}; USB cleanup also failed: {cleanup}")


def require_supported_platform() -> None:
    if platform.system() not in {"Darwin", "Linux"} or "microsoft" in platform.release().lower():
        raise PicoWireError(
            "Live Pico USB requires native macOS or Linux; portable planning is supported"
        )


class SerialStream:
    """Exclusive nonblocking CDC stream with absolute deadlines and cancellation."""

    def __init__(self, path: str, cancellation: threading.Event) -> None:
        require_supported_platform()
        import fcntl
        import os
        import struct
        import termios

        self.fd: int | None = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.cancellation = cancellation
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            attributes = termios.tcgetattr(self.fd)
            attributes[:4] = [0, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0]
            attributes[4:6] = [termios.B115200, termios.B115200]
            attributes[6][termios.VMIN] = attributes[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attributes)
            fcntl.ioctl(self.fd, termios.TIOCMBIC, struct.pack("I", termios.TIOCM_DTR))
            time.sleep(0.1)
            termios.tcflush(self.fd, termios.TCIOFLUSH)
            fcntl.ioctl(self.fd, termios.TIOCMBIS, struct.pack("I", termios.TIOCM_DTR))
        except BaseException as primary:
            try:
                self.close()
            except BaseException as cleanup:
                raise PicoCleanupError(primary, cleanup) from cleanup
            raise

    def _remaining(self, deadline: float) -> float:
        if self.cancellation.is_set():
            raise InterruptedError("Pico controller cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Pico serial deadline; mutation outcome may be uncertain")
        return min(remaining, 0.05)

    def read(self, maximum: int, deadline: float) -> bytes:
        import os
        import select

        if self.fd is None:
            raise ConnectionError("Pico serial closed")
        while True:
            readers, _, _ = select.select([self.fd], [], [], self._remaining(deadline))
            if readers:
                return os.read(self.fd, maximum)

    def write(self, data: bytes, deadline: float) -> None:
        import os
        import select

        if self.fd is None:
            raise ConnectionError("Pico serial closed")
        while data:
            _, writers, _ = select.select([], [self.fd], [], self._remaining(deadline))
            if writers:
                count = os.write(self.fd, data)
                if count <= 0:
                    raise ConnectionError("Pico serial write disconnected")
                data = data[count:]

    def close(self) -> None:
        import fcntl
        import os
        import termios

        if self.fd is not None:
            fd, self.fd = self.fd, None
            try:
                fcntl.ioctl(fd, termios.TIOCNXCL)
            finally:
                os.close(fd)


class PicoUsb:
    """Own both separate CDC roles under the shared installer/runtime lock."""

    def __init__(self, plan: dict[str, Any], cancellation: threading.Event) -> None:
        require_supported_platform()
        self.stack = ExitStack()
        expected = plan["installer"]
        path = Path(expected["path"])
        if artifact(path) != expected:
            raise PicoWireError("Pico installer/discovery source changed")
        spec = importlib.util.spec_from_file_location("wspq_selected_pico_installer", path)
        if spec is None or spec.loader is None:
            raise PicoWireError("Selected Pico discovery is unavailable")
        installer = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = installer
        spec.loader.exec_module(installer)
        if artifact(path) != expected:
            raise PicoWireError("Pico discovery source changed during import")
        target = plan["configuration"]["pico"]
        self.installer = installer
        self.target = target
        self.wtp: SerialStream | None = None
        try:
            self.stack.enter_context(installer.device_lock(target["serial"]))
            self.identity = installer.selected(target["serial"], "application")
            if (
                self.identity["ports"].get("2") != target["wtp_port"]
                or self.identity["ports"].get("4") != target["management_port"]
            ):
                raise PicoWireError("Physical Pico CDC role/port binding changed")
            self.management = SerialStream(target["management_port"], cancellation)
            self.stack.callback(self.management.close)
            if (
                installer.selected(target["serial"], "application", self.identity["parent"])
                != self.identity
            ):
                raise PicoWireError("Physical Pico topology changed while opening")
        except BaseException as primary:
            try:
                self.stack.close()
            except BaseException as cleanup:
                raise PicoCleanupError(primary, cleanup) from cleanup
            raise

    def close(self) -> None:
        self.stack.close()

    def open_wtp(self, cancellation: threading.Event) -> SerialStream:
        if self.wtp is not None:
            raise PicoWireError("Pico WTP role already opened")
        current = self.installer.selected(
            self.target["serial"], "application", self.identity["parent"]
        )
        if current != self.identity:
            raise PicoWireError("Physical Pico topology changed after management admission")
        self.wtp = SerialStream(self.target["wtp_port"], cancellation)
        self.stack.callback(self.wtp.close)
        return self.wtp

    def begin_cleanup(self) -> None:
        # User cancellation stops work, not the supported ABORT/readback path.
        self.management.cancellation = threading.Event()
        if self.wtp is not None:
            self.wtp.cancellation = threading.Event()

    def begin_operations(self, cancellation: threading.Event) -> None:
        self.management.cancellation = cancellation
        if self.wtp is not None:
            self.wtp.cancellation = cancellation
