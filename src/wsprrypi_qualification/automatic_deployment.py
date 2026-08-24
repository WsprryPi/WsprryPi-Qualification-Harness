"""Temporary two-host deployment for the first-class complete-test path."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import Any, cast

from wsprrypi_qualification.offline import artifact
from wsprrypi_qualification.remote_staging import (
    RemoteStage,
    StagedFile,
    build_runtime_archive,
    discover_remote_python,
    find_scp,
)


class AutomaticDeploymentError(RuntimeError):
    """A default complete-test deployment could not be prepared or cleaned."""


def _run(arguments: list[str], *, timeout_s: float = 120.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AutomaticDeploymentError("automatic deployment command failed") from error


def _require(result: subprocess.CompletedProcess[str], label: str) -> str:
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or str(result.returncode)
        raise AutomaticDeploymentError(f"{label} failed: {detail}")
    return result.stdout


def _source_repository() -> Path:
    configured = os.environ.get("WSPQ_WSPRRRYPI_SOURCE")
    candidates = (
        Path(configured) if configured else None,
        Path.cwd().resolve().parent / "WsprryPi",
        Path(__file__).resolve().parents[3] / "WsprryPi",
    )
    for candidate in candidates:
        if candidate is not None and (candidate / ".git").exists() and (candidate / "src").is_dir():
            return candidate.resolve()
    raise AutomaticDeploymentError("a local WsprryPi source checkout is unavailable")


def _zip_tree(source: Path, destination: Path, names: tuple[str, ...]) -> Path:
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            root = source / name
            paths = (
                [root]
                if root.is_file()
                else sorted(path for path in root.rglob("*") if path.is_file())
            )
            for path in paths:
                relative = path.relative_to(source).as_posix()
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
    return destination


def _remote_records(stage: RemoteStage, paths: dict[str, str]) -> dict[str, dict[str, Any]]:
    encoded = base64.urlsafe_b64encode(json.dumps(paths, separators=(",", ":")).encode()).decode()
    result = stage.run_python(
        "import base64,json;m=json.loads(base64.urlsafe_b64decode(sys.argv[2]));out={};"
        "[(lambda p,n:out.update({n:{'path':str(p),'size_bytes':p.stat().st_size,"
        "'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}}))(pathlib.Path(v),k)"
        " for k,v in m.items()];print(json.dumps(out,sort_keys=True))",
        encoded,
    )
    return cast(
        dict[str, dict[str, Any]], json.loads(_require(result, "remote identity discovery"))
    )


def _write_remote(stage: RemoteStage, path: str, content: str, *, executable: bool = False) -> None:
    encoded = base64.urlsafe_b64encode(content.encode()).decode()
    result = stage.run_python(
        "import base64;p=pathlib.Path(sys.argv[2]);"
        "p.write_bytes(base64.urlsafe_b64decode(sys.argv[3]));"
        "p.chmod(0o700 if sys.argv[4]=='1' else 0o600)",
        path,
        encoded,
        "1" if executable else "0",
    )
    _require(result, "remote file creation")


def _host_address(host: str, *, ssh: Path, known_hosts: Path) -> str:
    result = _run(
        [
            str(ssh),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "--",
            host,
            "hostname -I",
        ]
    )
    fields = _require(result, "transmitter address discovery").split()
    if not fields or re.fullmatch(r"[0-9a-fA-F:.]+", fields[0]) is None:
        raise AutomaticDeploymentError("transmitter address discovery was invalid")
    return fields[0]


def _remote_python_path(host: str, *, ssh: Path, known_hosts: Path) -> str:
    result = _run(
        [
            str(ssh),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "--",
            host,
            "/usr/bin/python3 -c 'import pathlib,sys;"
            "print(pathlib.Path(sys.executable).resolve())'",
        ]
    )
    value = _require(result, "remote Python path discovery").strip()
    if re.fullmatch(r"/[A-Za-z0-9._/+:-]+", value) is None:
        raise AutomaticDeploymentError("remote Python path is invalid")
    return value


def _selected_host_keys(known_hosts: Path, host: str, *, ssh_keygen: Path) -> tuple[str, str]:
    selected = _run([str(ssh_keygen), "-F", f"{host}.local", "-f", str(known_hosts)])
    lines = [
        line
        for line in _require(selected, "transmitter trust discovery").splitlines()
        if not line.startswith("#")
    ]
    if not lines:
        raise AutomaticDeploymentError("transmitter trust material is unavailable")
    rewritten = []
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            raise AutomaticDeploymentError("transmitter trust material is invalid")
        if fields[1] == "ssh-ed25519":
            rewritten.append(" ".join((host, *fields[1:])))
    if len(rewritten) != 1:
        raise AutomaticDeploymentError("one current transmitter ED25519 key is required")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as selected_file:
        selected_file.write("\n".join(rewritten) + "\n")
        selected_path = Path(selected_file.name)
    try:
        fingerprint = _run([str(ssh_keygen), "-lf", str(selected_path)])
        values = [
            line.split()[1]
            for line in _require(fingerprint, "transmitter fingerprint discovery").splitlines()
        ]
    finally:
        selected_path.unlink(missing_ok=True)
    if len(values) != 1:
        raise AutomaticDeploymentError("transmitter host fingerprint is ambiguous")
    return "\n".join(rewritten) + "\n", values[0]


def _prepare_transmitter(
    stage: RemoteStage, *, installed_binary: str | None = None
) -> dict[str, Any]:
    token = stage.root.rsplit("-", 1)[-1]
    deployment = f"/home/pi/wsprrypi-qualification-runs/complete-test-deployment-{token}"
    if installed_binary is not None:
        if not PurePosixPath(installed_binary).is_absolute():
            raise AutomaticDeploymentError("installed WsprryPi binary must be absolute")
        installed_configuration = os.environ.get(
            "WSPQ_WSPRRRYPI_INSTALLED_CONFIG", "/usr/local/etc/wsprrypi.ini"
        )
        if not PurePosixPath(installed_configuration).is_absolute():
            raise AutomaticDeploymentError("installed WsprryPi configuration must be absolute")
        program = (
            "import hashlib,os;deployment=pathlib.Path(sys.argv[2]);"
            "deployment.parent.mkdir(parents=True,exist_ok=True);"
            "assert deployment.parent.is_dir() and not deployment.parent.is_symlink();"
            "os.mkdir(deployment,0o700);"
            "fd=os.open(deployment/'.owner',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);"
            "os.write(fd,sys.argv[3].encode('ascii'));os.close(fd);"
            "source=pathlib.Path(sys.argv[4]);"
            "assert source.is_absolute() and source.is_file() and not source.is_symlink();"
            "data=source.read_bytes();binary=deployment/'wsprrypi';"
            "fd=os.open(binary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o700);"
            "os.write(fd,data);os.fsync(fd);os.close(fd);"
            "assert hashlib.sha256(binary.read_bytes()).hexdigest()=="
            "hashlib.sha256(data).hexdigest();"
            "config_source=pathlib.Path(sys.argv[5]);"
            "assert config_source.is_absolute() and config_source.is_file() "
            "and not config_source.is_symlink();"
            "lines=config_source.read_text().splitlines(keepends=True);"
            "unsupported=('22m =','22m Active High =');"
            "filtered=''.join(line for line in lines "
            "if not line.lstrip().startswith(unsupported));"
            "config=deployment/'wsprrypi.ini';"
            "fd=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);"
            "os.write(fd,filtered.encode());os.fsync(fd);os.close(fd);"
            "(pathlib.Path(sys.argv[1])/'gpio-inspect').chmod(0o700);print(str(binary))"
        )
        result = stage.run_python(
            program,
            deployment,
            stage.owner_token,
            installed_binary,
            installed_configuration,
            timeout_s=120,
        )
        source_path = "/home/pi/WsprryPi"
        tone_configuration = f"{deployment}/wsprrypi.ini"
    else:
        program = (
            "import hashlib,os,subprocess,zipfile;root=pathlib.Path(sys.argv[1]);"
            "deployment=pathlib.Path(sys.argv[2]);deployment.parent.mkdir(parents=True,exist_ok=True);"
            "assert deployment.parent.is_dir() and not deployment.parent.is_symlink();"
            "os.mkdir(deployment,0o700);"
            "fd=os.open(deployment/'.owner',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);"
            "os.write(fd,sys.argv[3].encode('ascii'));os.close(fd);"
            "src=root/'source';"
            "r=subprocess.run(['/usr/bin/git','clone',str(root/'wsprrypi.bundle'),str(src)],capture_output=True,text=True);"
            "assert r.returncode==0,r.stderr;"
            "r=subprocess.run(['/usr/bin/git','-C',str(src),'rev-parse','HEAD'],capture_output=True,text=True);"
            "assert r.returncode==0,r.stderr;"
            "r=subprocess.run(['/usr/bin/make','-C',str(src/'src'),'-j2',"
            "'BACKENDS=rpi-gpio'],capture_output=True,text=True,timeout=900)\n"
            "assert r.returncode==0,r.stdout+r.stderr\n"
            "source_binary=src/'src/build/bin/wsprrypi';data=source_binary.read_bytes();"
            "binary=deployment/'wsprrypi';"
            "fd=os.open(binary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o700);"
            "os.write(fd,data);os.fsync(fd);os.close(fd);"
            "assert binary.is_file() and not binary.is_symlink() and "
            "hashlib.sha256(binary.read_bytes()).hexdigest()==hashlib.sha256(data).hexdigest();"
            "(root/'gpio-inspect').chmod(0o700);"
            "print(str(binary))"
        )
        result = stage.run_python(program, deployment, stage.owner_token, timeout_s=1000)
        source_path = f"{stage.root}/source"
        tone_configuration = f"{source_path}/config/wsprrypi.ini"
    binary = _require(result, "temporary WsprryPi build").strip().splitlines()[-1]
    launcher = f"{stage.root}/capability-helper"
    _write_remote(
        stage,
        launcher,
        "#!/bin/sh\nexec /usr/bin/python3 -c 'import sys;"
        "runtime=sys.argv.pop(1);sys.argv[0]=sys.argv.pop(1);"
        "sys.path.insert(0,runtime);"
        "from wsprrypi_qualification.capability_helper import main;"
        "raise SystemExit(main())' "
        f'{stage.root}/runtime.zip {launcher} "$@"\n',
        executable=True,
    )
    return {
        "binary": binary,
        "launcher": launcher,
        "source": source_path,
        "tone_configuration": tone_configuration,
        "deployment_root": deployment,
    }


def _prepare_receiver(
    stage: RemoteStage,
    transmitter_host: str,
    address: str,
    keys: str,
    native_cache_key: str,
    runtime_cache_key: str,
) -> dict[str, Any]:
    if any(
        re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in (native_cache_key, runtime_cache_key)
    ):
        raise AutomaticDeploymentError("receiver native cache identity is invalid")
    token = stage.root.rsplit("-", 1)[-1]
    deployment_root = f"/home/pi/wsprrypi-qualification-runs/complete-test-deployment-{token}"
    create_deployment = (
        "import os;root=pathlib.Path(sys.argv[2]);os.mkdir(root,0o700);"
        "fd=os.open(root/'.owner',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);"
        "os.write(fd,sys.argv[3].encode('ascii'));os.close(fd)"
    )
    _require(
        stage.run_python(create_deployment, deployment_root, stage.owner_token),
        "durable receiver deployment creation",
    )
    cached_capture = f"{deployment_root}/wspq-capture-soapy"
    program = (
        "import hashlib,os,subprocess,zipfile;root=pathlib.Path(sys.argv[1]);"
        "cached=pathlib.Path(sys.argv[2]);src=root/'native-source';src.mkdir();"
        "zipfile.ZipFile(root/'native.zip').extractall(src);build=root/'native-build';"
        "r=subprocess.run(['/usr/bin/cmake','-S',str(src),'-B',str(build),"
        "'-DWSPQ_BUILD_SOAPY=ON','-DWSPQ_BUILD_TESTS=OFF'],"
        "capture_output=True,text=True,timeout=180);assert r.returncode==0,r.stdout+r.stderr;"
        "r=subprocess.run(['/usr/bin/cmake','--build',str(build),'--config','Release','-j2'],"
        "capture_output=True,text=True,timeout=600);assert r.returncode==0,r.stdout+r.stderr;"
        "data=(build/'wspq-capture-soapy').read_bytes();"
        "fd=os.open(cached,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o700);"
        "os.write(fd,data);os.fsync(fd);os.close(fd);"
        "assert cached.is_file() and not cached.is_symlink() and "
        "hashlib.sha256(cached.read_bytes()).hexdigest()==hashlib.sha256(data).hexdigest();"
        "(root/'gpio-inspect').chmod(0o700)"
    )
    _require(
        stage.run_python(program, cached_capture, timeout_s=850),
        "temporary receiver helper build",
    )
    helper = f"{deployment_root}/capability-helper"
    qualification = f"{deployment_root}/wsprrypi-qualification"
    cached_runtime = f"{deployment_root}/runtime.zip"
    ssh_wrapper = f"{deployment_root}/ssh-wspr4"
    transmitter_known_hosts = f"{deployment_root}/tx-known-hosts"
    cache_runtime = (
        "import hashlib,os;source=pathlib.Path(sys.argv[1])/'runtime.zip';"
        "target=pathlib.Path(sys.argv[2]);"
        "expected=sys.argv[3];data=source.read_bytes();"
        "assert hashlib.sha256(data).hexdigest()==expected;"
        "fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);"
        "os.write(fd,data);os.close(fd);"
        "assert target.is_file() and not target.is_symlink() and "
        "hashlib.sha256(target.read_bytes()).hexdigest()==expected"
    )
    _require(
        stage.run_python(cache_runtime, cached_runtime, runtime_cache_key),
        "receiver runtime cache preparation",
    )
    _write_remote(stage, transmitter_known_hosts, keys)
    _write_remote(
        stage,
        ssh_wrapper,
        "#!/bin/sh\nexec /usr/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "
        f"-o UserKnownHostsFile={transmitter_known_hosts} "
        f'-o HostName={address} -o HostKeyAlias={transmitter_host} "$@"\n',
        executable=True,
    )
    py = (
        "import sys;runtime=sys.argv.pop(1);sys.argv[0]=sys.argv.pop(1);sys.path.insert(0,runtime);"
    )
    _write_remote(
        stage,
        helper,
        "#!/bin/sh\nexec /usr/bin/python3 -c '"
        + py
        + "from wsprrypi_qualification.capability_helper import main;raise SystemExit(main())' "
        f'{cached_runtime} {helper} "$@"\n',
        executable=True,
    )
    _write_remote(
        stage,
        qualification,
        "#!/bin/sh\n"
        'if [ "$1" = runtime-identity ]; then\n'
        " exec /usr/bin/python3 -c 'import hashlib,json,pathlib,sys;"
        'f=lambda p:{"path":str(p),"size_bytes":p.stat().st_size,'
        '"sha256":hashlib.sha256(p.read_bytes()).hexdigest()};'
        'print(json.dumps({"launcher":f(pathlib.Path(sys.argv[1])),'
        '"module":f(pathlib.Path(sys.argv[2]))},sort_keys=True))\' '
        f"{qualification} {cached_runtime}\n"
        "fi\n"
        f"export PYTHONPATH={cached_runtime}\n"
        "exec /usr/bin/python3 -c '"
        + py
        + "from wsprrypi_qualification.cli import main;raise SystemExit(main())' "
        f'{cached_runtime} {qualification} "$@"\n',
        executable=True,
    )
    return {
        "helper": helper,
        "qualification": qualification,
        "ssh": ssh_wrapper,
        "known_hosts": transmitter_known_hosts,
        "capture": cached_capture,
        "deployment_root": deployment_root,
    }


def delegate_automatic_complete_test(
    transmitter_host: str,
    receiver_host: str,
    sdr_selector: str,
    forwarded_arguments: list[str],
    *,
    timeout_s: float = 7500.0,
) -> dict[str, Any]:
    """Stage current runtimes, execute on the receiver, validate, and clean both hosts."""
    controller_known_hosts = Path.home() / ".ssh" / "known_hosts"
    if not controller_known_hosts.is_file():
        raise AutomaticDeploymentError("controller SSH trust store is unavailable")
    with tempfile.TemporaryDirectory(prefix="wspq-controller-trust-") as trust_directory:
        known_hosts = Path(trust_directory).resolve() / "known_hosts"
        known_hosts.write_bytes(controller_known_hosts.read_bytes())
        return _delegate_automatic_complete_test(
            transmitter_host,
            receiver_host,
            sdr_selector,
            forwarded_arguments,
            known_hosts=known_hosts,
            timeout_s=timeout_s,
        )


def _delegate_automatic_complete_test(
    transmitter_host: str,
    receiver_host: str,
    sdr_selector: str,
    forwarded_arguments: list[str],
    *,
    known_hosts: Path,
    timeout_s: float,
) -> dict[str, Any]:
    installed_binary = os.environ.get("WSPQ_WSPRRRYPI_INSTALLED_BINARY")
    source = None if installed_binary else _source_repository()
    root = Path(__file__).resolve().parents[1]
    discovered_ssh = shutil.which("ssh")
    discovered_ssh_keygen = shutil.which("ssh-keygen")
    discovered_git = shutil.which("git")
    if discovered_ssh is None or discovered_ssh_keygen is None or discovered_git is None:
        raise AutomaticDeploymentError("OpenSSH and Git are required")
    ssh = Path(discovered_ssh).resolve()
    ssh_keygen = Path(discovered_ssh_keygen).resolve()
    git = Path(discovered_git).resolve()
    scp = find_scp(ssh)
    tx_python = discover_remote_python(
        transmitter_host,
        known_hosts=known_hosts,
        ssh=ssh,
        remote_python=_remote_python_path(transmitter_host, ssh=ssh, known_hosts=known_hosts),
    )
    rx_python = discover_remote_python(
        receiver_host,
        known_hosts=known_hosts,
        ssh=ssh,
        remote_python=_remote_python_path(receiver_host, ssh=ssh, known_hosts=known_hosts),
    )
    address = _host_address(transmitter_host, ssh=ssh, known_hosts=known_hosts)
    host_keys, host_fingerprint = _selected_host_keys(
        known_hosts, transmitter_host, ssh_keygen=ssh_keygen
    )
    with tempfile.TemporaryDirectory(prefix="wspq-automatic-") as temporary_name:
        temporary = Path(temporary_name).resolve()
        runtime = build_runtime_archive(root, temporary / "runtime.zip")
        runtime_cache_key = str(artifact(runtime)["sha256"])
        gpio = root.parent / "deployment" / "raspberry-pi-os" / "wspq-gpio-inspect"
        if not gpio.is_file():
            gpio = (
                Path(__file__).resolve().parents[2]
                / "deployment"
                / "raspberry-pi-os"
                / "wspq-gpio-inspect"
            )
        native = _zip_tree(
            Path(__file__).resolve().parents[2],
            temporary / "native.zip",
            ("CMakeLists.txt", "native"),
        )
        native_cache_key = str(artifact(native)["sha256"])
        bundle = temporary / "wsprrypi.bundle"
        if source is not None:
            _require(
                _run([str(git), "-C", str(source), "bundle", "create", str(bundle), "HEAD"]),
                "WsprryPi source packaging",
            )
        tx_files = [StagedFile(runtime, "runtime.zip"), StagedFile(gpio, "gpio-inspect")]
        if source is not None:
            tx_files.append(StagedFile(bundle, "wsprrypi.bundle"))
        tx_stage = RemoteStage(
            transmitter_host,
            tuple(tx_files),
            known_hosts=known_hosts,
            remote_python=tx_python[0],
            remote_python_sha256=tx_python[1],
            ssh=ssh,
            scp=scp,
            timeout_s=120,
        )
        rx_stage = RemoteStage(
            receiver_host,
            (
                StagedFile(runtime, "runtime.zip"),
                StagedFile(gpio, "gpio-inspect"),
                StagedFile(native, "native.zip"),
            ),
            known_hosts=known_hosts,
            remote_python=rx_python[0],
            remote_python_sha256=rx_python[1],
            ssh=ssh,
            scp=scp,
            timeout_s=120,
        )
        with ExitStack() as stack:
            tx = stack.enter_context(tx_stage)
            rx = stack.enter_context(rx_stage)
            tx_paths = _prepare_transmitter(tx, installed_binary=installed_binary)
            rx_paths = _prepare_receiver(
                rx,
                transmitter_host,
                address,
                host_keys,
                native_cache_key,
                runtime_cache_key,
            )
            tx_records = _remote_records(
                tx,
                {
                    "tx_helper": tx_paths["launcher"],
                    "tx_sudo": "/usr/bin/sudo",
                    "tx_systemctl": "/usr/bin/systemctl",
                    "tx_gpio": f"{tx.root}/gpio-inspect",
                    "tx_wsprrypi": tx_paths["binary"],
                    "tx_git": "/usr/bin/git",
                    "tone_ini": tx_paths["tone_configuration"],
                },
            )
            rx_records = _remote_records(
                rx,
                {
                    "ssh": rx_paths["ssh"],
                    "ssh_keygen": "/usr/bin/ssh-keygen",
                    "known_hosts": rx_paths["known_hosts"],
                    "rx_helper": rx_paths["helper"],
                    "rx_systemctl": "/usr/bin/systemctl",
                    "rx_gpio": f"{rx.root}/gpio-inspect",
                    "capture_helper": rx_paths["capture"],
                    "wsprd": "/usr/bin/wsprd",
                },
            )
            if source is None:
                revision_result = tx.run_python(
                    "import subprocess;r=subprocess.run(['/usr/bin/git','-C',sys.argv[2],"
                    "'rev-parse','HEAD'],capture_output=True,text=True);"
                    "assert r.returncode==0,r.stderr;print(r.stdout)",
                    tx_paths["source"],
                )
                component_result = tx.run_python(
                    "import subprocess;r=subprocess.run(['/usr/bin/git','-C',sys.argv[2],"
                    "'rev-parse','HEAD:src/WSPR-Transmitter'],capture_output=True,text=True);"
                    "assert r.returncode==0,r.stderr;print(r.stdout)",
                    tx_paths["source"],
                )
                revision = _require(revision_result, "source revision").strip()
                component = _require(component_result, "component revision").strip()
            else:
                revision = _require(
                    _run([str(git), "-C", str(source), "rev-parse", "HEAD"]),
                    "source revision",
                ).strip()
                component = _require(
                    _run([str(git), "-C", str(source), "rev-parse", "HEAD:src/WSPR-Transmitter"]),
                    "component revision",
                ).strip()
            tx_config = {
                "protocol_version": 1,
                "helper_identity": "complete-test-transmitter",
                "allowed_services": ["wsprrypi.service"],
                "systemctl_path": "/usr/bin/systemctl",
                "systemctl_sha256": tx_records["tx_systemctl"]["sha256"],
                "service_privilege_wrapper_path": "/usr/bin/sudo",
                "service_privilege_wrapper_sha256": tx_records["tx_sudo"]["sha256"],
                "gpio_helper_path": f"{tx.root}/gpio-inspect",
                "gpio_helper_sha256": tx_records["tx_gpio"]["sha256"],
                "bounded_tone_endpoint": {
                    "host": "::1",
                    "port": 31416,
                    "path": "/",
                    "maximum_frame_bytes": 16384,
                },
                "wsprrypi_revision": revision,
            }
            tx_keyed_config = {
                **tx_config,
                "process_privilege_wrapper_path": "/usr/bin/sudo",
                "process_privilege_wrapper_sha256": tx_records["tx_sudo"]["sha256"],
            }
            rx_config = {
                "protocol_version": 1,
                "helper_identity": "complete-test-receiver",
                "allowed_services": ["ssh.service"],
                "systemctl_path": "/usr/bin/systemctl",
                "systemctl_sha256": rx_records["rx_systemctl"]["sha256"],
                "gpio_helper_path": f"{rx.root}/gpio-inspect",
                "gpio_helper_sha256": rx_records["rx_gpio"]["sha256"],
            }
            _write_remote(tx, f"{tx.root}/helper.json", json.dumps(tx_config, sort_keys=True))
            _write_remote(
                tx,
                f"{tx.root}/keyed-helper.json",
                json.dumps(tx_keyed_config, sort_keys=True),
            )
            rx_helper_config = str(PurePosixPath(rx_paths["helper"]).with_name("helper.json"))
            _write_remote(rx, rx_helper_config, json.dumps(rx_config, sort_keys=True))
            tx_records.update(
                _remote_records(
                    tx,
                    {
                        "tx_helper_config": f"{tx.root}/helper.json",
                        "tx_keyed_helper_config": f"{tx.root}/keyed-helper.json",
                    },
                )
            )
            rx_records.update(_remote_records(rx, {"rx_helper_config": rx_helper_config}))
            output_parent = "/home/pi/wsprrypi-qualification-runs"
            work_directory = f"{rx.root}/work"
            launcher_identity = {
                "launcher": rx_records["rx_helper"],
                "module": rx_records["rx_helper_config"],
            }
            qualification_identity_result = rx.run_python(
                "import json,subprocess;"
                "r=subprocess.run([sys.argv[2],'runtime-identity'],capture_output=True,text=True);"
                "assert r.returncode==0,r.stderr;print(r.stdout)",
                rx_paths["qualification"],
            )
            qualification_identity = cast(
                dict[str, Any],
                json.loads(
                    _require(
                        qualification_identity_result,
                        "receiver qualification identity discovery",
                    )
                ),
            )
            delegation_receipt = {
                "receiver_host": receiver_host,
                "ssh": artifact(ssh),
                "known_hosts": artifact(known_hosts),
                "remote_exec": launcher_identity,
                "qualification": qualification_identity,
            }
            facts = {
                "transmitter_host": transmitter_host,
                "receiver_host": receiver_host,
                "receiver_hostname": receiver_host,
                "sdr": {
                    "driver": "sdrplay",
                    "serial": "2404058C60",
                    "label": "SDRplay Dev0 RSP1B 2404058C60",
                },
                "sdr_selector": sdr_selector,
                "artifacts": {**tx_records, **rx_records},
                "source": {"parent_revision": revision, "submodule_revision": component},
                "transmitter_host_key_sha256": host_fingerprint,
                "transmitter_source_path": tx_paths["source"],
                "work_directory": work_directory,
                "output_parent": output_parent,
                "rf_confirmation": {
                    "path_type": "conducted",
                    "antenna_connected": False,
                    "termination": "50 ohm direct SDR input through attenuator",
                    "attenuation_db": 20,
                    "filter": "none",
                    "safe_input_basis": (
                        "explicit --enable-rf confirmation of the documented conducted "
                        "20 dB default path"
                    ),
                    "authorization_scope": "single_run",
                },
                "receiver_delegation": {
                    "ssh": delegation_receipt["ssh"],
                    "known_hosts": delegation_receipt["known_hosts"],
                    "remote_exec": launcher_identity,
                    "qualification": qualification_identity,
                },
            }
            facts_path = temporary / "facts.json"
            facts_path.write_text(json.dumps(facts), encoding="utf-8")
            remote_facts = rx.add_file(StagedFile(facts_path, "facts.json"))
            generated = rx.run_python(
                "import sys;sys.path.insert(0,str(pathlib.Path(sys.argv[1])/'runtime.zip'));"
                "from wsprrypi_qualification.automatic_configuration import "
                "write_automatic_configuration;"
                "print(write_automatic_configuration(pathlib.Path(sys.argv[2]),"
                "pathlib.Path(sys.argv[3])))",
                remote_facts,
                f"{rx_paths['deployment_root']}/configuration",
            )
            remote_configuration = _require(generated, "remote automatic configuration").strip()
            command = [
                rx_paths["qualification"],
                "complete-test",
                transmitter_host,
                receiver_host,
                "--sdr",
                sdr_selector,
                "--receiver-local",
                "--delegated-output",
                "--delegation-receipt-base64",
                base64.urlsafe_b64encode(
                    json.dumps(delegation_receipt, separators=(",", ":")).encode()
                ).decode(),
                "--enable-rf",
                "--configuration",
                remote_configuration,
                *forwarded_arguments,
            ]
            encoded = base64.urlsafe_b64encode(
                json.dumps(command, separators=(",", ":")).encode()
            ).decode()
            result = rx.run_python(
                "import base64,json,subprocess;"
                "argv=json.loads(base64.urlsafe_b64decode(sys.argv[2]));"
                "r=subprocess.run(argv,capture_output=True,text=True,timeout=float(sys.argv[3]));"
                "print(json.dumps({'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}))",
                encoded,
                str(timeout_s),
                timeout_s=timeout_s + 30,
            )
            envelope = json.loads(_require(result, "receiver campaign execution"))
            if envelope["returncode"] not in {0, 3, 4, 5, 6}:
                raise AutomaticDeploymentError(
                    f"receiver campaign failed: {envelope['stderr'].strip()}"
                )
            try:
                campaign = cast(dict[str, Any], json.loads(envelope["stdout"]))
            except json.JSONDecodeError as error:
                raise AutomaticDeploymentError("receiver campaign returned invalid JSON") from error
        bundle_path = campaign.get("bundle")
        if not isinstance(bundle_path, str) or not bundle_path:
            raise AutomaticDeploymentError("receiver campaign omitted its retained bundle")
        validation = _run(
            [
                str(ssh),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                f"UserKnownHostsFile={known_hosts}",
                "--",
                receiver_host,
                rx_paths["qualification"],
                "validate-complete-test",
                bundle_path,
            ],
            timeout_s=180,
        )
        try:
            validated = json.loads(_require(validation, "post-cleanup campaign validation"))
        except json.JSONDecodeError as error:
            raise AutomaticDeploymentError(
                "post-cleanup campaign validation was invalid"
            ) from error
        if validated != campaign.get("result"):
            raise AutomaticDeploymentError("post-cleanup campaign result differs")
        return campaign
