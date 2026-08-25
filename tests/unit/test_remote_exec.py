import base64
import json

from wsprrypi_qualification.remote_exec import main


def test_identity_is_machine_readable_and_stable(capsys) -> None:
    assert main(["--identity"]) == 0
    identity = json.loads(capsys.readouterr().out)
    assert identity["module"]["path"].endswith("remote_exec.py")
    assert identity["launcher"]["size_bytes"] > 0
    assert len(identity["module"]["sha256"]) == 64


def test_self_bounded_command_can_wait_for_completion() -> None:
    encoded = base64.urlsafe_b64encode(json.dumps(["/usr/bin/true"]).encode()).decode()
    assert main(["--argv-base64", encoded, "--wait-for-completion"]) == 0
