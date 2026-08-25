import json

from wsprrypi_qualification.remote_exec import main


def test_identity_is_machine_readable_and_stable(capsys) -> None:
    assert main(["--identity"]) == 0
    identity = json.loads(capsys.readouterr().out)
    assert identity["module"]["path"].endswith("remote_exec.py")
    assert identity["launcher"]["size_bytes"] > 0
    assert len(identity["module"]["sha256"]) == 64
