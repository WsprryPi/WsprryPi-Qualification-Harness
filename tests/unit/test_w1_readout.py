"""Hardware-free checks for the standalone POSIX W1 utility."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("termios", reason="The standalone W1 utility requires POSIX serial APIs")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "w1_readout.py"
spec = importlib.util.spec_from_file_location("w1_readout", SCRIPT)
assert spec is not None and spec.loader is not None
w1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w1)


@pytest.mark.parametrize(
    ("command", "response", "expected"),
    [
        ("V", b"V1.05;", 1.05),
        ("F", b"F0.00;", 0.0),
        ("F", b"F9.99;", 9.99),
        ("F", b"F99.9;", 99.9),
        ("F", b"F100 ;", 100.0),
        ("R", b"R149 ;", 149.0),
        ("S", b"S01.0;", 1.0),
    ],
)
def test_documented_numeric_replies(monkeypatch, command, response, expected):
    sent = []
    received = iter(bytes([value]) for value in response)
    monkeypatch.setattr(w1.os, "write", lambda fd, data: sent.append(data) or len(data))
    monkeypatch.setattr(w1.os, "read", lambda fd, size: next(received))
    monkeypatch.setattr(w1.select, "select", lambda *args: ([7], [], []))
    assert w1.query(7, command) == expected
    assert sent == [command.encode("ascii")]


@pytest.mark.parametrize("response", [b"R1.00;", b"Fnan;", b"F-1.0;", b"F1x00;"])
def test_rejects_wrong_or_malformed_reply(monkeypatch, response):
    received = iter(bytes([value]) for value in response)
    monkeypatch.setattr(w1.os, "write", lambda fd, data: len(data))
    monkeypatch.setattr(w1.os, "read", lambda fd, size: next(received))
    monkeypatch.setattr(w1.select, "select", lambda *args: ([7], [], []))
    with pytest.raises(ValueError, match="Unexpected F response"):
        w1.query(7, "F")
