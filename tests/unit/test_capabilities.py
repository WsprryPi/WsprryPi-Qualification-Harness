import tempfile
from pathlib import Path

from wsprrypi_qualification.capabilities import capability_report


def test_capability_report_is_read_only_and_truthful() -> None:
    report = capability_report()
    assert report["read_only"] is True
    assert report["schema_version"] == 1
    states = {adapter["name"]: adapter["state"] for adapter in report["adapters"]}
    assert states["local_command"] == "available"
    assert states["ssh_command"] == "unsupported"
    assert states["service_inspection"] == "unsupported"
    assert states["gpio_quiescence"] == "unsupported"
    assert states["local_soapy_capture"] == "unsupported"
    local_soapy = next(
        adapter for adapter in report["adapters"] if adapter["name"] == "local_soapy_capture"
    )
    assert "wspr5-validated" in local_soapy["reason"]
    assert "orchestration remains unsupported" in local_soapy["reason"]
    assert all(tool["state"] in {"available", "unavailable"} for tool in report["external_tools"])
    for tool in report["external_tools"]:
        if "path" in tool:
            assert Path(tool["path"]).is_absolute()
    wsprd = next(tool for tool in report["external_tools"] if tool["name"] == "wsprd")
    if Path("/Applications/wsjtx.app/Contents/MacOS/wsprd").is_file():
        assert wsprd["state"] == "available"
        assert wsprd["path"] == "/Applications/wsjtx.app/Contents/MacOS/wsprd"


def test_capability_report_does_not_probe_or_mutate_filesystem(monkeypatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("capability reporting attempted a filesystem mutation")

    monkeypatch.setattr(tempfile, "gettempdir", fail)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", fail)
    monkeypatch.setattr(Path, "mkdir", fail)
    monkeypatch.setattr(Path, "touch", fail)
    monkeypatch.setattr(Path, "write_bytes", fail)
    monkeypatch.setattr(Path, "write_text", fail)
    monkeypatch.setattr(Path, "unlink", fail)

    report = capability_report()

    assert report["read_only"] is True
    assert "temporary_directory" not in report
