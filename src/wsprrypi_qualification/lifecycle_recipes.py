"""Maintained scenario and release-order selection, using explicit production inputs."""

from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

from wsprrypi_qualification.lifecycle import file_sha, source_identity, validate

CHECKS = {
    "pi-public-eight": [
        "fresh-ready",
        "first-absence",
        "baseline-ready",
        "same-settings-modules",
        "upgrade-settings-modules",
        "final-absence",
        "shared-preserved",
    ],
    "pi-isolated": [
        "application-ready",
        "settings-preserved",
        "companion-unchanged",
        "final-state",
    ],
    "pico-fresh": [
        "identity-safe",
        "full-erase-blank",
        "fresh-defaults",
        "distinctive-settings",
        "restart-readback",
        "final-state",
    ],
    "pico-preserve": [
        "baseline-verified",
        "distinctive-settings",
        "same-image-preserved",
        "upgrade-preserved",
        "recovery",
        "final-state",
    ],
    "pico-migration": [
        "baseline-verified",
        "upgrade-preserved",
        "new-defaults",
        "new-settings-roundtrip",
        "recovery",
        "final-state",
    ],
    "rp1-native": [
        "real-preflight",
        "same-reinstall",
        "same-version-replacement",
        "version-increase",
        "outgoing-unavailable",
        "partial-removal-recovery",
        "foreign-refusal",
        "terminal-absence",
    ],
    "rp1-live": [
        "real-preflight",
        "neutral-ready",
        "same-reinstall",
        "replacement",
        "recovery",
        "terminal-absence",
    ],
    "system": [
        "pi-acceptance",
        "rp1-acceptance",
        "pico-acceptance",
        "usb-handoff",
        "fixed-compatibility",
        "final-state",
    ],
}


CHECKS["pi-streamed-eight"] = list(CHECKS["pi-public-eight"])


def required_checks(profile: str) -> list[str]:
    return list(CHECKS[profile])


def release_order(scope: str, changed: list[str]) -> list[str]:
    """Dependencies are released only when selected and changed; Pi consumes last."""
    selected = {"pi", "pico", "rp1"} if scope == "system" else {scope}
    if not set(changed).issubset(selected):
        raise ValueError("independent release cannot publish an unselected companion")
    return [component for component in ("pico", "rp1", "pi") if component in changed]


def pi_public_request(
    config_path: Path, state_root: Path, pi: Path, rp1: Path, *, installer_branch: str = "devel"
) -> dict[str, Any]:
    """Produce the public eight-step matrix; no host access or mutation."""
    import json

    from wsprrypi_qualification.lifecycle_pi import installer_url, settings_fixture

    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate(config, "lifecycle-pi-bench")
    installer_url(installer_branch)
    if installer_branch == "devel" and config.get("candidate_release_tag"):
        raise ValueError("default public devel acceptance cannot pin a candidate release")
    if installer_branch != "devel" and not config.get("candidate_release_tag"):
        raise ValueError("selected branch requires an explicit candidate_release_tag")
    executable = str(Path(sys.executable).resolve())
    root = Path(str(files("wsprrypi_qualification")))
    immutable = [
        Path(executable),
        config_path.resolve(),
        Path(config["known_hosts"]),
        root / "lifecycle_pi.py",
        root / "lifecycle_assets/pi_snapshot.py.txt",
        root / "lifecycle_assets/pi_absence.py.txt",
        Path(config["baseline_local"]),
        pi / "scripts/install.sh",
    ]
    inputs = [{"path": str(p), "sha256": file_sha(p)} for p in immutable]
    output_root = state_root / "observations"
    steps = []

    def add(
        name: str,
        operation: str,
        phase: str,
        check: str,
        *,
        baseline: bool = False,
        compare: str | None = None,
        component: str = "pi",
    ) -> None:
        argv = [
            executable,
            "-m",
            "wsprrypi_qualification.lifecycle_pi",
            operation,
            str(config_path.resolve()),
            "--installer-source",
            source_identity("pi", pi)["revision"],
            "--installer-sha256",
            file_sha(pi / "scripts/install.sh"),
            "--installer-branch",
            installer_branch,
            "--output",
            str(output_root / (name + ".json")),
        ]
        if baseline:
            argv += ["--baseline"]
        if compare:
            argv += ["--compare", str(output_root / (compare + ".json"))]
        steps.append(
            {
                "id": name,
                "component": component,
                "phase": phase,
                "check": check,
                "command": {
                    "argv": argv,
                    "cwd": str(state_root),
                    "timeout_s": 2000,
                    "inputs": inputs,
                    "environment": {"PYTHONPATH": str(root.parent)},
                },
                "expect_exit": 0,
                "expect_json": {"verified": True, "operation": operation},
                "mutates": ["pi", "rp1"] if phase in {"effect", "cleanup"} else [],
                "replay_safe": phase in {"inspect", "verify"},
            }
        )

    add("00-before", "snapshot", "inspect", "baseline")
    add("00-absent", "audit", "verify", "initial-absence")
    add("01-install", "install", "effect", "install")
    add("01-seed", "seed", "effect", "distinctive-settings")
    add("01-ready", "verify", "verify", "fresh-ready")
    add("02-uninstall", "uninstall", "effect", "uninstall")
    add("02-marker", "retire-marker", "cleanup", "marker")
    add("03-absence", "audit", "verify", "first-absence")
    add("04-stage", "stage-baseline", "effect", "baseline-stage")
    add("04-install", "install", "effect", "baseline-install", baseline=True)
    add("04-seed", "seed", "effect", "distinctive-settings")
    add("04-restart", "restart", "effect", "restart")
    add("04-ready", "verify", "verify", "baseline-ready", baseline=True)
    add("05-reinstall", "install", "effect", "same-reinstall", baseline=True)
    add("05-ready", "verify", "verify", "same-settings-modules", baseline=True, compare="04-ready")
    add("06-upgrade", "install", "effect", "upgrade")
    add("06-ready", "verify", "verify", "upgrade-settings-modules", compare="05-ready")
    add("07-uninstall", "uninstall", "effect", "uninstall")
    add("07-marker", "retire-marker", "cleanup", "marker")
    add("08-absence", "audit", "verify", "final-absence")
    add("08-shared", "snapshot", "verify", "shared-preserved", compare="00-before")
    # Provider state is independently audited, even though the public installer
    # integrates both owners. This closes RP1's separate verification duty.
    add("08-provider", "audit", "verify", "provider-absence", component="rp1")
    return {
        "schema_version": 1,
        "operation": "test",
        "scope": "pi",
        "profile": "pi-public-eight" if installer_branch == "devel" else "pi-streamed-eight",
        "rf_enabled": False,
        "sources": [source_identity("pi", pi), source_identity("rp1", rp1)],
        "artifacts": inputs,
        "inputs": {
            "bench": config,
            "settings_fixture": settings_fixture(config),
            "companion_mutation": ["rp1"],
            "installer_branch": installer_branch,
            "default_public_acceptance": installer_branch == "devel",
        },
        "resources": ["host:" + config["target_identity"]["machine_id_sha256"]],
        "state_root": str(state_root),
        "protected_paths": [str(pi), str(rp1)],
        "final_states": {"pi": "absent", "rp1": "absent"},
        "required_checks": required_checks("pi-public-eight"),
        "steps": steps,
        "ci": [],
    }


def pico_request(config_path: Path, state_root: Path, source: Path, track: str) -> dict[str, Any]:
    """Select explicit same-board fresh or preservation steps; stage is separate."""
    import json
    import shlex
    import shutil

    from wsprrypi_qualification.lifecycle_pi import target_identity_script

    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate(config, "lifecycle-pico-bench")
    if track not in ("fresh", "preserve"):
        raise ValueError("unknown Pico track")
    transaction_root = PurePosixPath(config["transaction_state"])
    for key in (
        "remote_installer",
        "remote_schema",
        "remote_adapter",
        "evidence_root",
        "baseline_bundle",
        "candidate_bundle",
    ):
        if PurePosixPath(config[key]).is_relative_to(transaction_root):
            raise ValueError(
                "harness staging must remain outside the production transaction inventory: " + key
            )

    exe = Path(shutil.which("ssh") or "")
    if exe.is_absolute():
        exe = exe.resolve()
    if not exe.is_absolute():
        raise ValueError("absolute OpenSSH executable required")
    package = Path(str(files("wsprrypi_qualification")))
    adapter = package / "lifecycle_assets/pico_cycle.py.txt"
    installer = Path(config["installer_local"])
    schema = Path(config["schema_local"])
    inputs = [
        {"path": str(p.resolve()), "sha256": file_sha(p)}
        for p in (exe, config_path, installer, schema, adapter, Path(config["known_hosts"]))
    ]
    common = [
        "python3",
        config["remote_adapter"],
        "--installer",
        config["remote_installer"],
        "--installer-sha256",
        file_sha(installer),
        "--schema",
        config["remote_schema"],
        "--schema-sha256",
        file_sha(schema),
        "--device",
        config["serial"],
        "--parent",
        config["parent"],
        "--state-dir",
        config["transaction_state"],
        "--picotool",
        config["picotool"],
    ]
    steps = []

    def add(
        name: str,
        operation: str,
        phase: str,
        check: str,
        *,
        compare: str | None = None,
        bundle: str = "candidate_bundle",
        baseline: bool = False,
        fresh: bool = False,
        transaction_from: str | None = None,
    ) -> None:
        argv = [
            *common,
            operation,
            "--bundle",
            config[bundle],
            "--output",
            config["evidence_root"] + "/" + name + ".json",
        ]
        if fresh:
            argv += ["--fresh-defaults"]
        if transaction_from:
            argv += [
                "--transaction-from",
                config["evidence_root"] + "/" + transaction_from + ".json",
            ]
        if baseline:
            argv += ["--baseline-bundle", config["baseline_bundle"]]
        if compare:
            argv += ["--compare", config["evidence_root"] + "/" + compare + ".json"]
        # Strict transport; no raw user shell fragments. The target-side timeout
        # survives controller disconnect. Production core retains device locking.
        launcher = target_identity_script(config["target_identity"]) + (
            "import hashlib,os,sys;from pathlib import Path;"
            + "p=Path("
            + repr(config["remote_adapter"])
            + ");"
            + "h=hashlib.sha256(p.read_bytes()).hexdigest();"
            + "h=="
            + repr(file_sha(adapter))
            + " or sys.exit('Pico adapter changed');"
            + "os.execv(sys.executable,[sys.executable,"
            + ",".join(repr(v) for v in argv[1:])
            + "])"
        )
        encoded = shlex.join(["timeout", "--kill-after=30", "900", "python3", "-c", launcher])
        command = [
            str(exe),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "UserKnownHostsFile=" + config["known_hosts"],
            config["host"],
            encoded,
        ]
        steps.append(
            {
                "id": name,
                "component": "pico",
                "phase": phase,
                "check": check,
                "command": {
                    "argv": command,
                    "cwd": str(state_root),
                    "timeout_s": 950,
                    "inputs": inputs,
                    "environment": {},
                },
                "expect_exit": 0,
                "expect_json": {
                    "verified": True,
                    "operation": operation,
                    "serial": config["serial"],
                },
                "mutates": ["pico"] if phase == "effect" else [],
                "replay_safe": phase in {"inspect", "verify"},
            }
        )

    add("00-identity", "inspect", "inspect", "identity-safe", bundle="baseline_bundle")
    if track == "fresh":
        add("01-erase", "erase", "effect", "erase")
        add("01-blank", "verify-blank", "verify", "full-erase-blank")
        # Independent installed/fresh-default checks are distinct from the core's
        # complete blank verification retained in the erase transaction.
        add("02-install", "install", "effect", "install")
        add("03-defaults", "verify", "verify", "fresh-defaults", fresh=True)
    else:
        add("01-baseline", "verify", "verify", "baseline-verified", bundle="baseline_bundle")
    add(
        "04-seed",
        "seed",
        "effect",
        "seed",
        bundle="candidate_bundle" if track == "fresh" else "baseline_bundle",
    )
    add(
        "05-settings",
        "verify",
        "verify",
        "distinctive-settings",
        bundle="candidate_bundle" if track == "fresh" else "baseline_bundle",
    )
    add(
        "06-reinstall",
        "reinstall",
        "effect",
        "same-reinstall",
        bundle="candidate_bundle" if track == "fresh" else "baseline_bundle",
        baseline=track == "preserve",
    )
    add(
        "07-preserved",
        "verify",
        "verify",
        "same-image-preserved",
        compare="05-settings",
        bundle="candidate_bundle" if track == "fresh" else "baseline_bundle",
    )
    if track == "fresh":
        add("07-restart", "verify", "verify", "restart-readback", compare="05-settings")
    if track == "preserve":
        add("08-upgrade", "upgrade", "effect", "upgrade", baseline=True)
        add("09-preserved", "verify", "verify", "upgrade-preserved", compare="05-settings")
    if track == "preserve":
        add("09-recovery", "recover", "effect", "recovery-retry", transaction_from="08-upgrade")
        add("09-recovered", "verify", "verify", "recovery", compare="05-settings")
    add("10-final", "verify", "verify", "final-state", compare="05-settings")
    # The generic controller reports omitted required checks as blocked. These
    # tracks deliberately do not invent a new-default, reset or recovery proof.
    required = required_checks("pico-" + track)
    return {
        "schema_version": 1,
        "operation": "test",
        "scope": "pico",
        "profile": "pico-" + track,
        "rf_enabled": False,
        "sources": [source_identity("pico", source)],
        "artifacts": inputs,
        "inputs": {"bench": config, "track": track},
        "resources": [
            "host:" + config["target_identity"]["machine_id_sha256"],
            "usb:" + config["serial"],
        ],
        "state_root": str(state_root),
        "protected_paths": [str(source)],
        "final_states": {"pico": "verified-installed-with-synthetic-settings"},
        "required_checks": required,
        "steps": steps,
        "ci": [],
    }


def system_request(requests: list[dict[str, Any]], state_root: Path) -> dict[str, Any]:
    """Coordinate explicit component and handoff recipes; never synthesize evidence."""
    from copy import deepcopy

    if not requests:
        raise ValueError("select component requests and an explicit handoff/compatibility recipe")
    sources: dict[str, Any] = {}
    artifacts: dict[str, Any] = {}
    steps = []
    finals = {}
    resources = set()
    protected = set()
    for index, request in enumerate(requests):
        if request["operation"] != "test" or request["rf_enabled"] is not False:
            raise ValueError("system test accepts only RF-disabled test requests")
        for source in request["sources"]:
            prior = sources.setdefault(source["component"], source)
            if prior != source:
                raise ValueError("component recipes select different source identities")
        for item in request["artifacts"]:
            prior = artifacts.setdefault(item["path"], item)
            if prior != item:
                raise ValueError("component artifact identities conflict")
        resources.update(request["resources"])
        protected.update(request["protected_paths"])
        finals.update(request["final_states"])
        copied = deepcopy(request["steps"])
        for step in copied:
            step["id"] = f"part{index}-" + step["id"]
        # Preserve explicit system handoff/compatibility/final checks. A
        # component's independent verification can supply its acceptance, but
        # no command is cloned (its output destination may be exclusive).
        for component in ("pi", "rp1", "pico"):
            checks = [
                step
                for step in copied
                if step["component"] == component
                and step["phase"] in {"verify", "public_acceptance"}
                and step["check"] not in required_checks("system")
            ]
            if checks:
                checks[-1]["check"] = component + "-acceptance"
        steps.extend(copied)
    return {
        "schema_version": 1,
        "operation": "test",
        "scope": "system",
        "profile": "system",
        "rf_enabled": False,
        "sources": list(sources.values()),
        "artifacts": list(artifacts.values()),
        "inputs": {"component_requests": requests},
        "resources": sorted(resources),
        "state_root": str(state_root),
        "protected_paths": sorted(protected),
        "final_states": finals,
        "required_checks": required_checks("system"),
        "steps": steps,
        "ci": [],
    }


def system_public_request(
    pi_bench: Path,
    pico_bench: Path,
    state_root: Path,
    pi: Path,
    rp1: Path,
    pico: Path,
    *,
    installer_branch: str = "devel",
) -> dict[str, Any]:
    """Public Pi matrix with real production Pi/standalone USB lease handoff."""
    import copy
    import json
    import shlex
    import shutil

    from wsprrypi_qualification.lifecycle_pi import target_identity_script

    request = pi_public_request(pi_bench, state_root, pi, rp1, installer_branch=installer_branch)
    config = json.loads(pico_bench.read_text(encoding="utf-8"))
    validate(config, "lifecycle-pico-bench")
    if config["target_identity"] != request["inputs"]["bench"]["target_identity"]:
        raise ValueError("system public profile requires one identified Linux bench")
    package = Path(str(files("wsprrypi_qualification")))
    adapter = package / "lifecycle_assets/pico_cycle.py.txt"
    handoff = package / "lifecycle_assets/pi_usb_handoff.py.txt"
    remote_handoff = str(PurePosixPath(config["remote_adapter"]).with_name("pi_usb_handoff.py"))
    paths = [
        pico_bench,
        Path(config["installer_local"]),
        Path(config["schema_local"]),
        adapter,
        handoff,
    ]
    bound = [{"path": str(p.resolve()), "sha256": file_sha(p)} for p in paths]
    request["artifacts"] += bound
    request["sources"].append(source_identity("pico", pico))
    request["protected_paths"].append(str(pico))
    request["resources"].append("usb:" + config["serial"])
    request["inputs"]["pico_bench"] = config
    request["final_states"]["pico"] = "verified-installed-synthetic-settings"
    request.update(scope="system", profile="system")
    request["required_checks"] += required_checks("system")
    root = config["evidence_root"]

    def remote_step(
        name: str, argv: list[str], script: Path, check: str, component: str = "pico"
    ) -> dict[str, Any]:
        ssh = Path(shutil.which("ssh") or "").resolve()
        if not ssh.is_file():
            raise ValueError("OpenSSH required")
        remote_script = argv[1]
        launcher = target_identity_script(config["target_identity"]) + (
            "import hashlib,os,sys;from pathlib import Path;"
            + "h=hashlib.sha256(Path("
            + repr(remote_script)
            + ").read_bytes()).hexdigest();"
            + "h=="
            + repr(file_sha(script))
            + " or sys.exit('system adapter changed');"
            + "os.execv(sys.executable,[sys.executable,"
            + ",".join(repr(v) for v in argv[1:])
            + "])"
        )
        command = [
            str(ssh),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "UserKnownHostsFile=" + config["known_hosts"],
            config["host"],
            shlex.join(["timeout", "--kill-after=30", "180", "python3", "-c", launcher]),
        ]
        inputs = [
            *bound,
            {"path": str(ssh), "sha256": file_sha(ssh)},
            {"path": config["known_hosts"], "sha256": file_sha(Path(config["known_hosts"]))},
        ]
        return {
            "id": name,
            "component": component,
            "phase": "verify",
            "check": check,
            "command": {
                "argv": command,
                "cwd": str(state_root),
                "timeout_s": 220,
                "inputs": inputs,
                "environment": {},
            },
            "expect_exit": 0,
            "expect_json": {"verified": True},
            "mutates": [],
            "replay_safe": True,
        }

    def observe(name: str, check: str) -> dict[str, Any]:
        argv = [
            "python3",
            config["remote_adapter"],
            "verify",
            "--installer",
            config["remote_installer"],
            "--installer-sha256",
            file_sha(Path(config["installer_local"])),
            "--schema",
            config["remote_schema"],
            "--schema-sha256",
            file_sha(Path(config["schema_local"])),
            "--device",
            config["serial"],
            "--parent",
            config["parent"],
            "--state-dir",
            config["transaction_state"],
            "--bundle",
            config["candidate_bundle"],
            "--picotool",
            config["picotool"],
            "--output",
            root + "/" + name + ".json",
        ]
        return remote_step(name, argv, adapter, check)

    before = observe("system-pico-before", "pico-before")
    probe = remote_step(
        "system-pi-probe",
        [
            "python3",
            remote_handoff,
            "probe",
            "--before",
            root + "/system-pico-before.json",
            "--output",
            root + "/system-pi-probe.json",
        ],
        handoff,
        "pi-pico-probe",
        "pi",
    )
    after = observe("system-pico-after", "pico-acceptance")

    def proof(name: str, check: str) -> dict[str, Any]:
        return remote_step(
            name,
            [
                "python3",
                remote_handoff,
                "verify",
                "--before",
                root + "/system-pico-before.json",
                "--after",
                root + "/system-pico-after.json",
                "--probe",
                root + "/system-pi-probe.json",
                "--output",
                root + "/" + name + ".json",
            ],
            handoff,
            check,
        )

    position = next(i for i, s in enumerate(request["steps"]) if s["id"] == "02-uninstall")
    request["steps"][position:position] = [
        before,
        probe,
        after,
        proof("system-handoff", "usb-handoff"),
        proof("system-compatibility", "fixed-compatibility"),
    ]
    request["steps"][-1]["check"] = "rp1-acceptance"
    final = copy.deepcopy(request["steps"][-1])
    final.update(id="system-pi-final", component="pi", check="pi-acceptance")
    args = final["command"]["argv"]
    args[args.index("--output") + 1] = str(state_root / "observations/system-pi-final.json")
    request["steps"].append(final)
    request["steps"].append(observe("system-pico-final", "final-state"))
    return request
