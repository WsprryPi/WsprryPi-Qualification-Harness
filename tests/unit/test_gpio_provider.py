from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

PROVIDER = Path(__file__).parents[2] / "deployment" / "raspberry-pi-os" / "wspq-gpio-inspect"


def invoke(monkeypatch: pytest.MonkeyPatch, arguments: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", [str(PROVIDER), *arguments])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(PROVIDER), run_name="__main__")
    return int(stopped.value.code)


@pytest.mark.parametrize(("token", "direction"), [("ip", "input"), ("op", "output"), ("a0", "a0")])
def test_provider_parses_read_only_pinctrl(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], token: str, direction: str
) -> None:
    monkeypatch.setattr(sys, "argv", [str(PROVIDER), "gpio-inspect", '{"pin":4}'])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, f"4: {token} pn | lo\n", ""),
    )
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(PROVIDER), run_name="__main__")
    assert stopped.value.code == 0
    assert json.loads(capsys.readouterr().out) == {"pin": 4, "direction": direction, "owner": None}


def test_provider_fails_closed_on_unknown_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", [str(PROVIDER), "gpio-inspect", '{"pin":4}'])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "unexpected\n", ""),
    )
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(PROVIDER), run_name="__main__")
    assert stopped.value.code == 4


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["gpio-inspect"],
        ["gpio-inspect", '{"pin":4}', "extra"],
        ["gpio-set", '{"pin":4}'],
        ["gpio-inspect", "not-json"],
        ["gpio-inspect", "[]"],
        ["gpio-inspect", '{"pin":true}'],
        ["gpio-inspect", '{"pin":-1}'],
        ["gpio-inspect", '{"pin":54}'],
        ["gpio-inspect", '{"pin":4,"mode":"op"}'],
    ],
)
def test_provider_rejects_invalid_or_mutating_requests(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: pytest.fail("pinctrl must not be reached")
    )
    assert invoke(monkeypatch, arguments) == 2


def test_provider_uses_exact_read_only_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["arguments"] = arguments
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(arguments, 0, "4: ip pu | hi\n", "diagnostic")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert invoke(monkeypatch, ["gpio-inspect", '{"pin":4}']) == 0
    assert observed["arguments"] == ["/usr/bin/pinctrl", "get", "4"]
    assert observed["kwargs"] == {
        "shell": False,
        "check": False,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "timeout": 3,
    }


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (subprocess.CompletedProcess([], 1, "", "failed"), 3),
        (subprocess.CompletedProcess([], 0, "", ""), 4),
        (subprocess.CompletedProcess([], 0, "4: unknown\n", ""), 4),
    ],
)
def test_provider_fails_closed_on_command_or_output_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure: subprocess.CompletedProcess[str],
    expected: int,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: failure)
    assert invoke(monkeypatch, ["gpio-inspect", '{"pin":4}']) == expected


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired(["pinctrl"], 3), OSError("unavailable")]
)
def test_provider_fails_closed_on_timeout_or_os_error(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    def fail(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise error

    monkeypatch.setattr(subprocess, "run", fail)
    assert invoke(monkeypatch, ["gpio-inspect", '{"pin":4}']) == 3
