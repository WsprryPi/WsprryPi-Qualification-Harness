#!/usr/bin/env python3
"""Read an Elecraft W1 once per second using macOS/POSIX serial support.

No third-party packages required. Only read-only W1 commands are sent.
"""

import argparse
import copy
import os
import re
import select
import sys
import termios
import time
from datetime import datetime


def query(fd, command):
    os.write(fd, command.encode("ascii"))
    reply = bytearray()
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], max(0, deadline - time.monotonic()))
        if ready:
            chunk = os.read(fd, 1)
            if not chunk:
                raise OSError("Serial connection closed")
            reply.extend(chunk)
            if chunk == b";":
                value = bytes(reply).decode("ascii")
                if not re.fullmatch(re.escape(command) + r" *\d+(?:\.\d+)? *;", value):
                    raise ValueError(f"Unexpected {command} response: {value!r}")
                return float(value[1:-1])
    raise TimeoutError(f"No complete reply to {command}: {bytes(reply)!r}")


def readout(port, count):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    original = None
    try:
        original = termios.tcgetattr(fd)
        settings = copy.deepcopy(original)
        settings[0] = settings[1] = settings[3] = 0
        settings[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        settings[4] = settings[5] = termios.B9600
        settings[6][termios.VMIN] = settings[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, settings)
        time.sleep(0.5)
        termios.tcflush(fd, termios.TCIFLUSH)
        version = query(fd, "V")
        print(f"Elecraft W1 firmware {version:.2f} | {port} | Ctrl+C to stop", flush=True)
        print("Time       Forward W  Reflected W    SWR", flush=True)
        deadline = time.monotonic()
        samples = 0
        while count == 0 or samples < count:
            forward = query(fd, "F")
            reflected = query(fd, "R")
            swr = query(fd, "S")
            # The idle SWR reported by the W1 is not a load measurement.
            swr_text = f"{swr:.1f}" if forward > 0 else "--"
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"{timestamp}  {forward:10.2f}  {reflected:11.2f}  {swr_text:>5}", flush=True)
            samples += 1
            if count and samples >= count:
                break
            deadline += 1.0
            now = time.monotonic()
            if deadline < now:
                deadline = now + 1.0
            time.sleep(max(0, deadline - now))
    finally:
        try:
            if original is not None:
                termios.tcsetattr(fd, termios.TCSANOW, original)
        finally:
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/cu.usbserial-A95QWDJT")
    parser.add_argument(
        "--count", type=int, default=0, help="Stop after N readings; default: run until Ctrl+C"
    )
    args = parser.parse_args()
    if args.count < 0:
        parser.error("--count must be zero or positive")
    try:
        readout(args.port, args.count)
    except KeyboardInterrupt:
        print("\nStopped.")
    except (OSError, ValueError, TimeoutError, termios.error) as error:
        print(f"W1 readout stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
