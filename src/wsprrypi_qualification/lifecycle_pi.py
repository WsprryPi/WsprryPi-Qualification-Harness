"""Explicit Linux/SSH Pi lifecycle capability, independent of the portable controller.

No RF commands are available. Only production streamed installation/removal and
passive evidence probes are exposed. The controller retains full command logs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shlex
import subprocess
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

RAW = "https://raw.githubusercontent.com/WsprryPi/WsprryPi/refs/heads/devel/scripts/install.sh"


def installer_url(branch: str) -> str:
    try:
        subprocess.run(
            ["git", "check-ref-format", "refs/heads/" + branch],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except subprocess.SubprocessError as error:
        raise ValueError("invalid or unavailable installer branch reference") from error
    return RAW.replace("/devel/", "/" + quote(branch, safe="/") + "/")


DEFAULT_SETTINGS_FIXTURE = {
    "WSPR": {"Call Sign": "AA0NT", "Grid Square": "EM18", "TX Power": 10},
    "CW": {"Message": "CQ CQ DE AA0NT EM18 QRSS TEST 73"},
}


def settings_fixture(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("settings_fixture", DEFAULT_SETTINGS_FIXTURE)
    permitted = {"WSPR": {"Call Sign", "Grid Square", "TX Power"}, "CW": {"Message"}}
    if not value or any(
        section not in permitted
        or not isinstance(fields, dict)
        or not set(fields).issubset(permitted[section])
        for section, fields in value.items()
    ):
        raise ValueError("settings fixture may only change RF-disabled station/message fields")
    return cast(dict[str, Any], value)


def target_identity_script(expected: dict[str, str]) -> str:
    return """import hashlib,json,platform,socket,sys
from pathlib import Path
release={}
for line in Path('/etc/os-release').read_text().splitlines():
    if '=' in line:
        key,value=line.split('=',1)
        release[key]=value.strip('"')
observed={'machine_id_sha256':hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest(),
          'hostname':socket.gethostname(),'os_id':release['ID'],
          'os_version':release['VERSION_ID'],'kernel':platform.release(),
          'board_model':Path('/proc/device-tree/model').read_text().rstrip(chr(0))}
if observed!=json.loads(EXPECTED):
    sys.exit('physical target OS/kernel/board identity changed')
""".replace("EXPECTED", repr(json.dumps(expected)))


def configuration_script(operation: str, fixture: dict[str, Any]) -> str:
    return """import json,urllib.request,urllib.error,subprocess,sys,time
url='http://127.0.0.1:31415/config'
fixture=json.loads(FIXTURE)
def read():
    with urllib.request.urlopen(url,timeout=10) as response:
        return json.load(response),response.headers['ETag']
before,revision=read()
policy=before['Operation']
if policy['Transmit'] is not False or policy['Enable on Boot']!='Never':
    sys.exit('unsafe startup policy')
if OPERATION=='seed':
    body={**fixture,'Operation':{'Transmit':False,'Enable on Boot':'Never'}}
    request=urllib.request.Request(url,data=json.dumps(body).encode(),method='PATCH',
        headers={'Content-Type':'application/json','If-Match':revision})
    with urllib.request.urlopen(request,timeout=10) as response:
        response.read()
else:
    subprocess.run(['sudo','-n','systemctl','restart','wsprrypi.service'],
                   check=True,timeout=60)
deadline=time.monotonic()+30
while True:
    try:
        after,_=read()
        if after['Operation']['Transmit'] is not False:
            sys.exit('output enabled')
        if all(after[s][k]==v for s,fields in fixture.items() for k,v in fields.items()):
            break
    except (OSError,ValueError,KeyError):
        pass
    if time.monotonic()>=deadline:
        sys.exit('fixture/API readback deadline')
    time.sleep(.25)
""".replace("FIXTURE", repr(json.dumps(fixture))).replace("OPERATION", repr(operation))


def remote(host: str, known_hosts: Path, command: str, *, timeout: float = 120) -> str:
    if host.startswith("-") or any(c.isspace() for c in host):
        raise ValueError("invalid SSH host")
    record = subprocess.run(
        [
            "/usr/bin/ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "UserKnownHostsFile=" + str(known_hosts),
            host,
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if record.returncode:
        raise RuntimeError(
            f"SSH command failed ({record.returncode}):\n{record.stdout}\n{record.stderr}"
        )
    return record.stdout


def probe(config: dict[str, Any], kind: str) -> dict[str, Any]:
    code = (
        files("wsprrypi_qualification")
        .joinpath("lifecycle_assets/pi_" + kind + ".py.txt")
        .read_bytes()
    )
    encoded = base64.b64encode(code).decode()
    env = {
        "LIFECYCLE_INVOKING_HOME": config["home"],
        "LIFECYCLE_EVIDENCE_ROOT": config["evidence_root"],
        "LIFECYCLE_OWNED_PATHS": json.dumps(config["owned_paths"]),
        "LIFECYCLE_SETTINGS_FIXTURE": json.dumps(settings_fixture(config)),
        "LIFECYCLE_RETAINED_PATHS": json.dumps(config.get("retained_paths", {})),
    }
    command = shlex.join(
        [
            "sudo",
            "-n",
            "env",
            *[k + "=" + v for k, v in env.items()],
            "python3",
            "-c",
            target_identity_script(config["target_identity"])
            + "import base64;exec(base64.b64decode("
            + repr(encoded)
            + "))",
        ]
    )
    return cast(
        dict[str, Any],
        json.loads(remote(config["host"], Path(config["known_hosts"]), command, timeout=240)),
    )


def verify_provider_idle(runtime: dict[str, Any], expected_route: str | None = None) -> None:
    """Require neutral state or an explicitly selected, fully quiescent idle route."""
    safety = runtime.get("safety", {})
    routes = runtime.get("routes", {})
    if (
        runtime.get("outputEnabled") is not False
        or not isinstance(safety, dict)
        or not isinstance(routes, dict)
        or "active" not in routes
        or any(safety.get(k) != "quiescent" for k in ("clock", "dma", "gpio"))
        or any(safety.get(k) is not False for k in ("endpointOpen", "lease", "owner"))
    ):
        raise ValueError("provider output, endpoint, ownership or hardware is not quiescent")
    if (
        runtime.get("state") == "neutral_ready"
        and runtime.get("routeSelected") is False
        and routes["active"] is None
        and safety.get("operationalReady") is False
    ):
        return
    if (
        expected_route in {"gpio4", "gpio20"}
        and runtime.get("state") == "exact_ready"
        and runtime.get("routeSelected") is True
        and routes["active"] == expected_route
        and safety.get("operationalReady") is True
    ):
        return
    raise ValueError("provider route differs from the explicitly selected idle policy")


def verify_installed(
    snapshot: dict[str, Any], config: dict[str, Any], *, baseline: bool = False
) -> None:
    expected = config["baseline_sha256"] if baseline else config["candidate_sha256"]
    if snapshot.get("binary_sha256") != expected:
        raise ValueError("installed executable bytes differ from verified bundle")
    service = snapshot["service"]["stdout"]
    if "ActiveState=active" not in service or "UnitFileState=enabled" not in service:
        raise ValueError("application service not active/enabled")
    if not all(snapshot["http"][k].get("status") == 200 for k in ("ui", "api", "config")):
        raise ValueError("UI/API/configuration not ready")
    safety = snapshot["safety"]
    if safety.get("transmit") != "false" or safety.get("enable on boot") != "Never":
        raise ValueError("RF-disabled persistent policy not established")
    verify_provider_idle(snapshot["runtime"], config.get("provider_idle_route"))
    if snapshot["provider"]["sourceCommit"] != config["provider_source"]:
        raise ValueError("provider package source mismatch")
    if snapshot["provider_tooling_sha256"] != config["provider_tooling_sha256"]:
        raise ValueError("provider tooling mismatch")
    if snapshot.get("api_fixture") != settings_fixture(config):
        raise ValueError("runtime/API readback differs from distinctive fixture")
    if not snapshot["marker"]:
        raise ValueError("successful install marker missing")
    if not snapshot["module_artifacts"]:
        raise ValueError("installed modules missing")
    # Exact supported kernel inventory is a plan input derived from package policy,
    # never a fixed historical module-file count.
    kernels = {Path(item["path"]).parts[3] for item in snapshot["module_artifacts"]}
    if kernels != set(config["eligible_kernels"]):
        raise ValueError("module inventory disagrees with selected package/kernel policy")
    for kernel in kernels:
        stems = {
            Path(item["path"]).name.split(".ko")[0]
            for item in snapshot["module_artifacts"]
            if Path(item["path"]).parts[3] == kernel
        }
        if stems != {"rp1_gpclk_dkms", "rp1_route_controller"}:
            raise ValueError("missing/duplicate module inventory")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "snapshot",
            "audit",
            "install",
            "uninstall",
            "verify",
            "stage-baseline",
            "retire-marker",
            "seed",
            "restart",
        ),
    )
    parser.add_argument("config", type=Path)
    parser.add_argument("--installer-source")
    parser.add_argument("--installer-sha256")
    parser.add_argument("--installer-branch", default="devel")
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument(
        "--compare", type=Path, help="before snapshot for shared/settings/module preservation"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    # The parent validates and hash-binds the bench schema. This child uses
    # the pinned base interpreter and deliberately needs only the standard library.
    value: dict[str, Any] | None = None
    try:
        if args.operation == "stage-baseline":
            data = Path(config["baseline_local"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != config["baseline_sha256"]:
                raise ValueError("baseline input bytes changed")
            code = (
                "from pathlib import Path;import base64,hashlib;"
                + "p=Path("
                + repr(config["baseline_remote"])
                + ");"
                + "p.parent.mkdir(mode=0o700,parents=True,exist_ok=True);"
                + "data=base64.b64decode("
                + repr(base64.b64encode(data).decode())
                + ");"
                + "hashlib.sha256(data).hexdigest()=="
                + repr(config["baseline_sha256"])
                + " or sys.exit('baseline bytes changed');"
                + "f=p.open('xb');f.write(data);f.close();p.chmod(0o700)"
            )
            # Stream code over stdin: executable images may exceed SSH argument limits.
            command = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ForwardAgent=no",
                "-o",
                "UserKnownHostsFile=" + config["known_hosts"],
                config["host"],
                "python3 -",
            ]
            code = target_identity_script(config["target_identity"]) + code
            subprocess.run(command, input=code, text=True, check=True, timeout=120)
            value = {"staged_sha256": config["baseline_sha256"]}
        elif args.operation == "retire-marker":
            code = (
                "from pathlib import Path;p=Path("
                + repr(config["home"])
                + ")/'finished';p.unlink(missing_ok=True)"
            )
            code = target_identity_script(config["target_identity"]) + code
            remote(config["host"], Path(config["known_hosts"]), shlex.join(["python3", "-c", code]))
            value = {"marker_retired": True}
        elif args.operation in {"seed", "restart"}:
            fixture = settings_fixture(config)
            code = target_identity_script(config["target_identity"]) + configuration_script(
                args.operation, fixture
            )
            output = remote(
                config["host"],
                Path(config["known_hosts"]),
                shlex.join(["python3", "-c", code]),
                timeout=120,
            )
            value = {"settings_fixture": fixture, "operation": args.operation, "output": output}
        elif args.operation in ("snapshot", "audit", "verify"):
            value = probe(config, "absence" if args.operation == "audit" else "snapshot")
            if (
                args.operation in {"snapshot", "verify"}
                and value["host"] != config["expected_hostname"]
            ):
                raise ValueError("unexpected target hostname")
            if args.operation == "verify":
                verify_installed(value, config, baseline=args.baseline)
            if args.compare:
                before = json.loads(args.compare.read_text(encoding="utf-8"))
                keys = ["shared_hashes"]
                if args.operation == "verify":
                    keys += ["settings_sha256", "module_artifacts"]
                if any(value[key] != before[key] for key in keys):
                    raise ValueError(
                        "shared files, canonical settings or module bytes/timestamps changed"
                    )
            if value.get("dpkg_audit", {}).get("stdout", "").strip():
                raise ValueError("package manager audit failed")
            if value.get("apache_config", {}).get("returncode", 0) != 0:
                raise ValueError("Apache configuration invalid")
        else:
            expected_url = installer_url(args.installer_branch)
            url = config.get("installer_url", expected_url)
            if url != expected_url:
                raise ValueError(
                    "installer URL differs from the explicitly selected streamed branch"
                )
            if not args.installer_source or not args.installer_sha256:
                raise ValueError(
                    "public installation requires exact tooling source and script SHA256"
                )
            channel_check = (
                "import subprocess,sys;"
                "value=subprocess.check_output(['git','ls-remote',"
                "'https://github.com/WsprryPi/WsprryPi.git',"
                + repr("refs/heads/" + args.installer_branch)
                + "],"
                "text=True,timeout=30).split();"
                + "value and value[0]=="
                + repr(args.installer_source)
                + " or sys.exit('public devel source changed')"
            )
            script_check = (
                "import sys,hashlib;data=sys.stdin.buffer.read();"
                + "hashlib.sha256(data).hexdigest()=="
                + repr(args.installer_sha256)
                + " or sys.exit('public installer bytes changed');sys.stdout.buffer.write(data)"
            )
            home = shlex.quote(config["home"])
            environment = [
                "sudo",
                "-n",
                "env",
                "REPO_BRANCH=" + args.installer_branch,
                "TERSE=true",
                "TERM=xterm",
            ]
            if args.operation == "uninstall":
                environment.append("ACTION=uninstall")
            pipeline = (
                "set -o pipefail; rm -f "
                + home
                + "/finished; curl -fsSL "
                + shlex.quote(url)
                + " | "
            )
            pipeline += shlex.join(["python3", "-c", script_check]) + " | "
            pipeline += shlex.join(environment) + " bash -s --"
            if args.baseline:
                pipeline += " --binary-source local --binary-path " + shlex.quote(
                    config["baseline_remote"]
                )
            elif args.operation == "install" and config.get("candidate_release_tag"):
                pipeline += " --binary-source release --release-tag " + shlex.quote(
                    config["candidate_release_tag"]
                )
            if args.operation == "install":
                pipeline += " && touch " + home + "/finished"
            check = shlex.join(["python3", "-c", channel_check])
            pipeline = check + " && ( " + pipeline + " ) && " + check
            # Linux adapter owns a host-wide flock and target-side timeout. An SSH
            # disconnect cannot remove the bound; the controller must inspect before retry.
            wrapper = [
                "flock",
                "-n",
                config["remote_lock"],
                "timeout",
                "--signal=TERM",
                "--kill-after=30",
                "1800",
                "bash",
                "-c",
                pipeline,
            ]
            guard = shlex.join(["python3", "-c", target_identity_script(config["target_identity"])])
            output = remote(
                config["host"],
                Path(config["known_hosts"]),
                guard + " && " + shlex.join(wrapper),
                timeout=1900,
            )
            value = {"output": output, "completed": args.operation}
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        print(
            json.dumps(
                {
                    "verified": True,
                    "operation": args.operation,
                    "evidence_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                }
            )
        )
        return 0
    except (
        ValueError,
        OSError,
        RuntimeError,
        KeyError,
        TypeError,
        subprocess.TimeoutExpired,
    ) as error:
        if value is not None and not args.output.exists():
            try:
                with args.output.open("x", encoding="utf-8") as stream:
                    json.dump(
                        {"verification_error": str(error), "observed": value}, stream, indent=2
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as save_error:
                print("Failed to retain observation: " + str(save_error), file=sys.stderr)
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
