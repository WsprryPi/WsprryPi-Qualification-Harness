"""Build explicitly selected production Pico software fixtures without an SDK or hardware."""

from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
from pathlib import Path


def run(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True, timeout=600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pico_source", type=Path)
    parser.add_argument("--native-build", type=Path, default=Path("build-native"))
    parser.add_argument("--pico-build", type=Path, default=Path("../pico-host-build"))
    args = parser.parse_args()
    source = args.pico_source.resolve(strict=True)
    native = args.native_build.resolve()
    fixture = args.pico_build.resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=10
    ).strip()
    requested = os.environ.get("WSPQ_PICO_REQUESTED_REF", revision)
    if not re.fullmatch(r"[0-9a-f]{40}", requested) or requested != revision:
        raise ValueError("Pico integration requires the exact intended 40-character source commit")
    run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            str(native),
            "-DWSPQ_BUILD_SOAPY=OFF",
            "-DWSPQ_BUILD_TESTS=ON",
            f"-DWSPQ_PICO_SOURCE={source}",
        ]
    )
    run(["cmake", "--build", str(native), "--config", "Release", "--parallel", "2"])
    run(["ctest", "--test-dir", str(native), "-C", "Release", "--output-on-failure"])
    candidates = [
        native / "wspq-pico-compile",
        native / "Release/wspq-pico-compile",
        native / "Release/wspq-pico-compile.exe",
        native / "wspq-pico-compile.exe",
    ]
    compiler = next(path for path in candidates if path.is_file())
    values = {
        "WSPQ_PICO_SOURCE": str(source),
        "WSPQ_PICO_COMPILER": str(compiler),
        "WSPQ_PICO_EXPECTED_REVISION": revision,
    }
    if platform.system() != "Windows":
        run(
            [
                "cmake",
                "-S",
                str(source),
                "-B",
                str(fixture),
                "-DWSPRRY_PICO_BUILD_FIRMWARE=OFF",
                "-DWSPRRY_PICO_BUILD_TESTS=ON",
                f"-DPython3_EXECUTABLE={sys.executable}",
            ]
        )
        run(
            [
                "cmake",
                "--build",
                str(fixture),
                "--target",
                "endpoint_driver",
                "usb_management_driver",
                "frequency_policy_tests",
                "--parallel",
                "2",
            ]
        )
        values["WSPQ_PICO_TEST_BUILD"] = str(fixture)
    print("Selected Pico source:", revision)
    output = os.environ.get("GITHUB_ENV")
    if output is not None:
        with Path(output).open("a", encoding="utf-8") as handle:
            for name, value in values.items():
                if "\n" in value or "\r" in value:
                    raise ValueError("Invalid CI environment path")
                handle.write(f"{name}={value}\n")


if __name__ == "__main__":
    main()
