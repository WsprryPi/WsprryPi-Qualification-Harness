"""Explicit product-owned build, publication and consumer adapters.

Linux paths occur only inside Docker capability invocations. The coordinator and
GitHub API transport use portable Python paths and structured argument vectors.
"""

from __future__ import annotations

import ast
import base64
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

from wsprrypi_qualification import lifecycle as lc
from wsprrypi_qualification.prepared_release import (
    REPOSITORIES,
    inventory,
    regular,
    require,
    source,
)
from wsprrypi_qualification.progress import ProgressReporter

TARGETS = ("armv6-bookworm", "armv6-trixie", "aarch64-bookworm", "aarch64-trixie")
WORKFLOWS = {"pi": "debian-non-hardware.yml", "pico": "software-build.yml", "rp1": "offline.yml"}


class CanonicalReleaseAdapter:
    def __init__(self, tool_manifest: dict[str, Any]) -> None:
        self.tools = tool_manifest
        self.preparing = False
        self.progress: ProgressReporter | None = None
        self.outcome: str | None = None

    def command(self, name: str) -> list[str]:
        item = self.tools[name]
        require(item["available"], "required capability is unavailable: " + name)
        require(
            str(Path(item["argv"][0]).resolve()) == item["resolved_executable"],
            "tool executable link changed",
        )
        for path, expected in item["inputs"].items():
            require(lc.file_sha(regular(Path(path))) == expected, "external tool changed: " + path)
        return list(item["argv"])

    def run(
        self,
        argv: list[str],
        root: Path,
        *,
        timeout: int = 180,
        cwd: Path | None = None,
        allow_failure: bool = False,
        stdin: str | None = None,
    ) -> dict[str, Any]:
        # LocalCommandTransport supervises whole process trees on each platform.
        require(stdin is None, "command input must use a retained file")
        directory = root / ("evidence" if self.preparing else "continuations")
        directory.mkdir(mode=0o700, exist_ok=True)
        inputs = [
            {"path": str(Path(a).resolve()), "sha256": lc.file_sha(Path(a))}
            for a in argv
            if Path(a).is_absolute() and Path(a).is_file()
        ]
        environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        # Credentials stay in memory and the supported GH credential store;
        # execute_command records names only, never environment values.
        for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "SSH_AUTH_SOCK", "SSL_CERT_FILE"):
            if name in os.environ:
                environment[name] = os.environ[name]
        step = {
            "command": {
                "argv": argv,
                "cwd": str(cwd or root),
                "timeout_s": timeout,
                "inputs": inputs,
                "environment": environment,
            },
            "expect_exit": 0,
            "expect_json": {},
        }
        if self.progress is not None:
            self.progress.emit("canonical-command", "started", " ".join(argv[:3]))
        record = lc.execute_command(step)
        if self.progress is not None:
            self.progress.emit(
                "canonical-command",
                "completed" if record["assertions_passed"] else "failed",
                " ".join(argv[:3]),
            )
        lc.save(directory / ("command-" + secrets.token_hex(8) + ".json"), record, new=True)
        require(
            not record["timed_out"] and not record["cancelled"] and record["cleanup_verified"],
            "canonical command supervision or cleanup failed; inspect retained command record",
        )
        if not allow_failure:
            require(
                record["assertions_passed"],
                "canonical command failed; inspect retained full log: "
                + " ".join(argv[:3])
                + "\n"
                + (record.get("stdout", "") + "\n" + record.get("stderr", ""))[-2400:],
            )
        return record

    def api(
        self,
        repository: str,
        endpoint: str,
        root: Path,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        missing: bool = False,
    ) -> Any:
        require(repository in REPOSITORIES.values(), "unknown repository authority")
        require(not self.preparing or method == "GET", "preparation cannot mutate public state")
        argv = [
            *self.command("gh"),
            "api",
            "--method",
            method,
            "repos/" + repository + "/" + endpoint,
        ]
        if body is not None:
            directory = root / "continuations"
            directory.mkdir(mode=0o700, exist_ok=True)
            path = directory / ("api-input-" + secrets.token_hex(8) + ".json")
            lc.save(path, body, new=True)
            argv += ["--input", str(path)]
        record = self.run(argv, root, allow_failure=missing)
        if record["return_code"] != 0:
            require(missing and "(HTTP 404)" in record["stderr"], "GitHub state inspection failed")
            return None
        return json.loads(record["stdout"]) if record["stdout"].strip() else None

    def ci(self, name: str, spec: dict[str, Any], root: Path) -> dict[str, Any]:
        repository = REPOSITORIES[name]
        run = self.api(repository, f"actions/runs/{spec['ci_run']}", root)
        expected = source(Path(spec["source"]), name)["commit"]
        require(
            run["head_sha"] == expected
            and run["status"] == "completed"
            and run["conclusion"] == "success"
            and run["head_repository"]["full_name"] == repository
            and run["path"] == ".github/workflows/" + WORKFLOWS[name],
            "canonical CI source/workflow/result differs: " + name,
        )
        attempt = run["run_attempt"]
        jobs = self.api(
            repository, f"actions/runs/{spec['ci_run']}/attempts/{attempt}/jobs?per_page=100", root
        )
        items = jobs["jobs"]
        require(
            0 < jobs["total_count"] == len(items) <= 100
            and len({j["name"] for j in items}) == len(items)
            and all(j["status"] == "completed" and j["conclusion"] == "success" for j in items),
            "failed, skipped, incomplete or ambiguous required canonical jobs: " + name,
        )
        names = {j["name"] for j in items}
        essential = (
            {"offline", "lifecycle"}
            if name == "rp1"
            else {
                "wtp-network-loopback",
                "wtp-usb-management-pair",
                "non-hardware-validation",
                "strict-i2c-profile",
                "ubuntu-gcc13-release",
            }
        )
        require(
            essential <= names and (name != "pi" or len(items) >= 9),
            "required canonical jobs missing: " + name,
        )
        if name == "pi":
            record = self.run(
                [
                    *self.command("gh"),
                    "run",
                    "view",
                    str(spec["ci_run"]),
                    "--repo",
                    repository,
                    "--attempt",
                    str(attempt),
                    "--log",
                ],
                root,
            )
            for job in ("wtp-network-loopback", "wtp-usb-management-pair"):
                selected = re.findall(
                    r"(?m)^"
                    + re.escape(job)
                    + r"\tVerify immutable Pico source\t[^\n]*"
                    + r"Verified Pico commit: ([0-9a-f]{40})\s*$",
                    record["stdout"],
                )
                require(
                    selected == [spec["pico_source"]],
                    "canonical paired Pico identity missing or incompatible: " + job,
                )
        return {"run": run, "jobs": jobs, "status": "PASS"}

    def docker(
        self,
        image: str,
        mounts: dict[Path, str],
        arguments: list[str],
        root: Path,
        *,
        tmp_build: bool = False,
        timeout: int = 1800,
    ) -> dict[str, Any]:
        require(
            re.fullmatch("sha256:[0-9a-f]{64}", image), "immutable Docker image identity required"
        )
        owner = secrets.token_hex(16)
        container = "wspq-prepared-" + owner
        argv = [
            *self.command("docker"),
            "run",
            "--name",
            container,
            "--label",
            "wsprrypi.qualification.owner=" + owner,
            "--rm",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:rw,exec,mode=1777",
            "-w",
            "/",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "WSPRRYPI_DISABLE_HARDWARE_ACCESS=1",
            "-e",
            "GIT_CONFIG_COUNT=2",
            "-e",
            "GIT_CONFIG_KEY_0=safe.directory",
            "-e",
            "GIT_CONFIG_VALUE_0=/source",
            "-e",
            "GIT_CONFIG_KEY_1=safe.directory",
            "-e",
            "GIT_CONFIG_VALUE_1=/consumer",
        ]
        if tmp_build:
            for host, target in mounts.items():
                if target == "/source:ro":
                    # The owning validator uses an ignored build scratch root.
                    # Create the empty mountpoint before mounting source read-only.
                    (host / "build").mkdir(exist_ok=True)
            argv += ["--tmpfs", "/source/build:rw,exec,mode=1777"]
        for host, target in mounts.items():
            argv += ["-v", str(host) + ":" + target]
        try:
            return self.run(
                [*argv, "--entrypoint", arguments[0], image, *arguments[1:]], root, timeout=timeout
            )
        finally:
            # Docker daemon children are outside the local CLI process tree.
            # Inspect our unique identity even after cancellation; never remove
            # a container merely because its name matches an expected prefix.
            inspected = self.run(
                [*self.command("docker"), "inspect", "--type", "container", container],
                root,
                timeout=30,
                allow_failure=True,
            )
            if inspected["return_code"] == 0:
                observed = json.loads(inspected["stdout"])
                require(
                    len(observed) == 1
                    and observed[0]["Config"]["Labels"].get("wsprrypi.qualification.owner") == owner
                    and observed[0]["Image"] == image,
                    "container cleanup refused foreign ownership; inspect retained command log",
                )
                identifier = observed[0]["Id"]
                self.run(
                    [*self.command("docker"), "rm", "--force", identifier],
                    root,
                    timeout=30,
                    allow_failure=True,
                )
                inspected = self.run(
                    [*self.command("docker"), "inspect", "--type", "container", identifier],
                    root,
                    timeout=30,
                    allow_failure=True,
                )
            require(
                inspected["return_code"] != 0
                and re.search(r"no such (?:object|container)", inspected["stderr"], re.I),
                "container cleanup could not verify absence; "
                "retain state and inspect owned container",
            )

    def prepare(self, name: str, spec: dict[str, Any], root: Path) -> dict[str, Any]:
        self.preparing = True
        try:
            output = root / name
            output.mkdir(mode=0o700)
            if name == "pi":
                result = self.pi(spec, root, output)
            elif name == "rp1":
                result = self.rp1(spec, root, output)
            else:
                result = self.pico(spec, root, output)
            result.update(
                component=name,
                repository=REPOSITORIES[name],
                source=source(Path(spec["source"]), name)["commit"],
                version=spec["version"],
                tag=spec["tag"],
            )
            return result
        finally:
            self.preparing = False

    def pi(self, spec: dict[str, Any], root: Path, output: Path) -> dict[str, Any]:
        require(spec["mode"] in {"build", "exports", "packet"}, "unknown Pi preparation mode")
        src = Path(spec["source"])
        revision = source(src, "pi")["commit"]
        builds = output / "builds"
        builds.mkdir()
        if spec["mode"] == "build":
            self.run(
                [*self.command("git"), "submodule", "update", "--init", "--recursive"],
                root,
                cwd=src,
                timeout=600,
            )
            for target in TARGETS:
                self.run(
                    [
                        *self.command("python"),
                        "-B",
                        str(src / "scripts/container_build.py"),
                        target,
                        str(builds / target),
                        "--release-version",
                        spec["version"],
                    ],
                    root,
                    timeout=7200,
                )
        else:
            require(
                set(spec.get("exports", {})) == set(TARGETS),
                "all four original Pi exports required",
            )
            for target, path in spec["exports"].items():
                shutil.copytree(path, builds / target)
        records: list[dict[str, Any]] = []
        images = {}
        embedded = set()
        build_branches = set()
        for target in TARGETS:
            directory = builds / target
            binary = regular(directory / "wsprrypi")
            metadata = regular(directory / "build-metadata.hpp").read_text(encoding="utf-8")
            fields = dict(re.findall(r'^#define (MAKE_[A-Z_]+) "([^"\n]*)"$', metadata, re.M))
            require(
                fields.get("MAKE_COMMIT") == revision
                and fields.get("MAKE_DIRTY") == "false"
                and fields.get("MAKE_TAG", "").startswith(spec["version"] + "+"),
                "Pi executable build source/version differs: " + target,
            )
            embedded.add(fields["MAKE_TAG"])
            build_branches.add(fields["MAKE_RAW_BRH"])
            sums = (directory / "SHA256SUMS").read_text(encoding="utf-8")
            require(lc.file_sha(binary) in sums, "Pi export checksum differs: " + target)
            image = (directory / "build-image-id.txt").read_text(encoding="utf-8").strip()
            images[target] = image
            self.docker(
                image,
                {src: "/source:ro", directory: "/artifact:ro"},
                [
                    "python3",
                    "-B",
                    "/source/scripts/precompiled_binary.py",
                    "check",
                    "--binary",
                    "/artifact/wsprrypi",
                    "--full",
                ],
                root,
            )
            version_output = self.docker(
                image, {directory: "/artifact:ro"}, ["/artifact/wsprrypi", "--version"], root
            )
            require(
                fields["MAKE_TAG"] in version_output["stdout"],
                "actual Pi executable version differs from retained metadata",
            )
            records.append(
                {
                    "name": "wsprrypi-" + target,
                    "bytes": binary.stat().st_size,
                    "sha256": lc.file_sha(binary),
                    "target": target,
                }
            )
        require(
            len(embedded) == len(build_branches) == 1,
            "Pi targets embed different versions or build branches",
        )
        ci = self.ci("pi", spec, root)
        assets = output / "artifacts"
        if spec["mode"] == "packet":
            require("packet" in spec, "existing Pi packet required")
            shutil.copytree(spec["packet"], assets)
            manifest = lc.load(regular(assets / "publication-manifest.json"))
            require(
                manifest["release_tag"] == spec["tag"]
                and manifest["executable_source"]["commit"] == revision
                and manifest["executable_source"]["branch"] == spec["public_branch"]
                and manifest["assets"] == records,
                "Pi publication packet differs from exact exports",
            )
        else:
            assets.mkdir()
            for record in records:
                shutil.copy2(builds / record["target"] / "wsprrypi", assets / record["name"])
            (assets / "SHA256SUMS").write_text(
                "".join(r["sha256"] + "  " + r["name"] + "\n" for r in records),
                encoding="ascii",
                newline="\n",
            )
            manifest = {
                "schema_version": 1,
                "release_tag": spec["tag"],
                "version": spec["version"],
                "embedded_version": next(iter(embedded)),
                "classification": "prepared release",
                "latest": False,
                "executable_source": {
                    "repository": "https://github.com/" + REPOSITORIES["pi"],
                    "commit": revision,
                    "tree": source(src, "pi")["tree"],
                    "branch": spec["public_branch"],
                    "build_branch": fields.get("MAKE_RAW_BRH", ""),
                    "dirty": False,
                },
                "intended_tag_source": revision,
                "assets": records,
                "publication_state": (
                    "unpublished preparation; observed tag identity is a separate receipt"
                ),
            }
            lc.save(assets / "publication-manifest.json", manifest, new=True)
        expected_names = {r["name"] for r in records} | {"publication-manifest.json", "SHA256SUMS"}
        require(set(inventory(assets)) == expected_names, "Pi public asset inventory differs")
        for record in records:
            require(
                lc.file_sha(assets / record["name"]) == record["sha256"], "Pi packet bytes changed"
            )
        tree = ast.parse((src / "scripts/rp1_gpclk_dkms_install.py").read_text(encoding="utf-8"))
        default = next(
            (
                n.value.value
                for n in tree.body
                if isinstance(n, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "DEFAULT_RELEASE_TAG" for t in n.targets
                )
                and isinstance(n.value, ast.Constant)
            ),
            None,
        )
        return {
            "directory": str(assets),
            "validation": {"status": "PASS", "canonical_ci": ci},
            "images": images,
            "helper_source": str(src),
            "public_branch": spec["public_branch"],
            "ci_pico_source": spec["pico_source"],
            "default_rp1_tag": default,
            "provenance_directory": str(builds),
            "build_provenance": inventory(builds),
        }

    def rp1(self, spec: dict[str, Any], root: Path, output: Path) -> dict[str, Any]:
        require(spec["mode"] in {"build", "candidate"}, "unknown RP1 preparation mode")
        src, consumer = Path(spec["source"]), Path(spec["consumer_source"])
        revision = source(src, "rp1")["commit"]
        work = output / "work"
        work.mkdir(mode=0o777)
        work.chmod(0o777)
        mounts = {src: "/source:ro", consumer: "/consumer:ro", work: "/output"}
        if spec["mode"] == "build":
            self.docker(
                spec["image"],
                mounts,
                [
                    "/source/scripts/build-release-candidate",
                    "--version",
                    spec["version"],
                    "--source-commit",
                    revision,
                    "--output",
                    "/output/candidate",
                    "--release",
                ],
                root,
                tmp_build=True,
            )
        else:
            require("candidate" in spec, "verified RP1 candidate directory required")
            shutil.copytree(spec["candidate"], work / "candidate")
        candidate = work / "candidate"
        manifest = lc.load(regular(candidate / "rp1-gpclk-dkms-installation-manifest-v1.json"))
        require(
            manifest["sourceCommit"] == revision
            and manifest["productVersion"] == spec["version"]
            and manifest["releaseTag"] == spec["tag"],
            "RP1 candidate source/version/tag differs",
        )
        self.docker(
            spec["image"],
            mounts,
            [
                "/source/scripts/validate-release-candidate",
                "--candidate",
                "/output/candidate",
                "--source-commit",
                revision,
                "--release",
            ],
            root,
            tmp_build=True,
        )
        self.docker(
            spec["image"],
            mounts,
            [
                "python3",
                "-B",
                "/consumer/scripts/maintainer/accept_rp1_release.py",
                "--provider-source",
                "/source",
                "--candidate",
                "/output/candidate",
                "--release",
                "--output",
                "/output/consumer-acceptance",
            ],
            root,
            tmp_build=True,
        )
        accepted = lc.load(work / "consumer-acceptance/acceptance.json")
        require(
            accepted["status"] == "PASS"
            and accepted["sourceCommit"] == revision
            and accepted["installationPerformed"] is False,
            "RP1 non-installing consumer check failed",
        )
        assets = output / "artifacts"
        shutil.copytree(candidate, assets)
        require(len(inventory(assets)) == 7, "seven RP1 release assets required")
        ci = self.ci("rp1", spec, root)
        return {
            "directory": str(assets),
            "validation": {"status": "PASS", "canonical_ci": ci, "consumer": accepted},
            "image": spec["image"],
            "helper_source": str(src),
            "consumer_source": str(consumer),
            "consumer_source_commit": source(consumer, "pi")["commit"],
            "provenance_directory": str(work),
            "build_provenance": inventory(work),
        }

    def pico(self, spec: dict[str, Any], root: Path, output: Path) -> dict[str, Any]:
        require(spec["mode"] == "ci", "Pico preparation requires an exact canonical run")
        tool = Path(spec["tooling_source"])
        request = [
            *self.command("python"),
            "-B",
            str(tool / "scripts/ci/prepared.py"),
            "prepare",
            "--branch",
            spec["branch"],
            "--run",
            str(spec["ci_run"]),
            "--attempt",
            str(spec["attempt"]),
            "--source",
            source(Path(spec["source"]), "pico")["commit"],
            "--version",
            spec["version"],
            "--destination",
            str(output / "owner-preparation"),
        ]
        self.run(request, root, timeout=1200)
        owner = output / "owner-preparation"
        owner_plan = lc.load(owner / "prepared-publication.json")
        assets = output / "artifacts"
        assets.mkdir()
        for name in owner_plan["files"]:
            path = owner / "deployment" / name
            shutil.copy2(path if path.exists() else owner / name, assets / name)
        require(inventory(assets) == owner_plan["files"], "Pico canonical packet differs")
        return {
            "directory": str(assets),
            "validation": {"status": "PASS", "canonical_preparation": owner_plan},
            "owner_plan": owner_plan,
            "helper_source": str(tool),
            "branch": spec["branch"],
            "provenance_directory": str(owner),
            "build_provenance": inventory(owner),
            "publisher_files": {
                name: lc.file_sha(tool / name)
                for name in (
                    ".github/workflows/publish-prepared.yml",
                    "scripts/ci/prepared.py",
                    "scripts/ci/publish.py",
                    "scripts/ci/download.py",
                    "scripts/ci/bundle.py",
                    "scripts/usb_installer.py",
                    "scripts/check_control_policy.py",
                    "scripts/ci/release-manifest-2.schema.json",
                    "scripts/ci/requirements.txt",
                    "scripts/ci/execution.py",
                    "scripts/ci/inputs.json",
                    "scripts/ci/legacy-test-skip-reasons.json",
                    "scripts/check_application_image.py",
                    "scripts/check_standalone_image.py",
                    "scripts/firmware_payload.py",
                    "engineering/protocol/control-1.schema.json",
                    "engineering/protocol/control-policy.json",
                )
            },
        }

    def tag(self, packet: dict[str, Any], root: Path) -> str | None:
        repository, tag = packet["repository"], packet["tag"]
        ref = self.api(repository, "git/ref/tags/" + tag, root, missing=True)
        if ref is None:
            return None
        obj = ref["object"]
        for _ in range(4):
            if obj["type"] == "commit":
                require(
                    obj["sha"] == packet["source"], "existing release tag points to another source"
                )
                return str(ref["object"]["sha"])
            require(obj["type"] == "tag", "unsupported tag object")
            obj = self.api(repository, "git/tags/" + obj["sha"], root)["object"]
        raise lc.LifecycleError("excessively nested release tag")

    def inspect_public(self, packet: dict[str, Any], root: Path) -> dict[str, Any]:
        oid = self.tag(packet, root)
        release = self.api(
            packet["repository"], "releases/tags/" + packet["tag"], root, missing=True
        )
        if release is None:
            # REST tag lookup hides drafts; GraphQL resolves exactly one tag.
            owner, repository = packet["repository"].split("/")
            body = {
                "query": (
                    "query($owner:String!,$name:String!,$tag:String!)"
                    "{repository(owner:$owner,name:$name)"
                    "{release(tagName:$tag){databaseId isDraft}}}"
                ),
                "variables": {"owner": owner, "name": repository, "tag": packet["tag"]},
            }
            directory = root / "continuations"
            directory.mkdir(mode=0o700, exist_ok=True)
            path = directory / ("graphql-" + secrets.token_hex(8) + ".json")
            lc.save(path, body, new=True)
            record = self.run([*self.command("gh"), "api", "graphql", "--input", str(path)], root)
            data = json.loads(record["stdout"])
            require(not data.get("errors"), "draft discovery failed")
            draft = data["data"]["repository"]["release"]
            if draft is None:
                return {
                    "state": "absent",
                    "tag_object": oid,
                    "packet_sha256": packet["packet_sha256"],
                }
            release = self.api(packet["repository"], "releases/" + str(draft["databaseId"]), root)
        require(
            oid is not None and release["tag_name"] == packet["tag"], "release tag identity differs"
        )
        require(
            release["prerelease"] is ("-" in packet["version"].split("+", 1)[0]),
            "release classification differs from the prepared version",
        )
        assets = release["assets"]
        by_name = {a["name"]: a for a in assets}
        require(
            len(by_name) == len(assets) and set(by_name) <= set(packet["files"]),
            "unexpected public assets",
        )
        for name, asset in by_name.items():
            expected = packet["files"][name]
            require(
                asset["state"] == "uploaded"
                and asset["size"] == expected["bytes"]
                and asset.get("digest") == "sha256:" + expected["sha256"],
                "public artifact differs: " + name,
            )
        require(
            release["draft"] or set(by_name) == set(packet["files"]),
            "published packet is incomplete",
        )
        publication_state = "draft" if release["draft"] else "published"
        if (
            packet["component"] == "pico"
            and not release["draft"]
            and "wsprrypico-prepared-publication/1" in release.get("body", "")
        ):
            helper = Path(packet["helper_source"]) / "scripts/usb_installer.py"
            request = root / "continuations" / ("public-proof-" + secrets.token_hex(8) + ".json")
            request.parent.mkdir(parents=True, exist_ok=True)
            lc.save(
                request,
                {
                    "release": release,
                    "manifest": lc.load(
                        Path(packet["provenance_directory"]) / "deployment/release-manifest.json"
                    ),
                },
                new=True,
            )
            code = (
                "import importlib.util,json,sys;from pathlib import Path;"
                "s=importlib.util.spec_from_file_location('c'," + repr(str(helper)) + ");"
                "c=importlib.util.module_from_spec(s);s.loader.exec_module(c);"
                "v=json.loads(Path(" + repr(str(request)) + ").read_text(encoding='utf-8'));"
                "\ntry:c.verify_prepared_publication(c.Https(),v['release'],v['manifest'])"
                "\nexcept c.PublicationPending:sys.exit(3)"
            )
            result = self.run(
                [*self.command("python"), "-B", "-c", code], root, timeout=300, allow_failure=True
            )
            require(result["return_code"] in (0, 3), "invalid delayed publication receipt")
            if result["return_code"] == 3:
                publication_state = "publication_pending"
        return {
            "state": publication_state,
            "id": release["id"],
            "tag_object": oid,
            "tag": packet["tag"],
            "source": packet["source"],
            "packet_sha256": packet["packet_sha256"],
            "assets": sorted(by_name),
            "url": release["html_url"],
            "prerelease": release["prerelease"],
        }

    def preflight(self, packet: dict[str, Any], root: Path, observed: dict[str, Any]) -> None:
        if packet["component"] == "pico" and observed["state"] != "published":
            self.pico_publisher(packet, root)
            destination = root / "continuations" / ("pico-verify-" + secrets.token_hex(8))
            plan_path = root / "continuations" / ("pico-plan-" + secrets.token_hex(8) + ".json")
            lc.save(plan_path, packet["owner_plan"], new=True)
            self.run(
                [
                    *self.command("python"),
                    "-B",
                    str(Path(packet["helper_source"]) / "scripts/ci/prepared.py"),
                    "verify",
                    str(plan_path),
                    "--destination",
                    str(destination),
                ],
                root,
                timeout=1200,
            )
        if packet["component"] == "pi":
            current = self.api(packet["repository"], "commits/" + packet["public_branch"], root)
            require(
                current["commit"]["tree"]["sha"]
                == source(Path(packet["helper_source"]), "pi")["tree"],
                "Pi public support branch changed or source is unmerged; return to preparation",
            )

    def publish(self, packet: dict[str, Any], root: Path) -> dict[str, Any]:
        require(not self.preparing, "preparation cannot publish")
        if packet["component"] == "pico":
            return self.publish_pico(packet, root)
        repository, tag = packet["repository"], packet["tag"]
        if self.tag(packet, root) is None:
            obj = self.api(
                repository,
                "git/tags",
                root,
                method="POST",
                body={
                    "tag": tag,
                    "message": "Prepared release " + packet["version"],
                    "object": packet["source"],
                    "type": "commit",
                },
            )
            self.api(
                repository,
                "git/refs",
                root,
                method="POST",
                body={"ref": "refs/tags/" + tag, "sha": obj["sha"]},
            )
        observed = self.inspect_public(packet, root)
        if observed["state"] == "published":
            return observed
        prerelease = "-" in packet["version"].split("+", 1)[0]
        if observed["state"] == "absent":
            release = self.api(
                repository,
                "releases",
                root,
                method="POST",
                body={
                    "tag_name": tag,
                    "target_commitish": packet["source"],
                    "name": tag,
                    "body": "Prepared and validated exact bytes from `"
                    + packet["source"]
                    + "`.\n\n"
                    "Software and lifecycle evidence do not establish RF qualification.",
                    "draft": True,
                    "prerelease": prerelease,
                    "make_latest": "false",
                },
            )
            observed = self.inspect_public(packet, root)
            require(observed["id"] == release["id"], "created draft identity differs")
        require(
            observed["prerelease"] is prerelease, "existing draft release classification differs"
        )
        for name in sorted(set(packet["files"]) - set(observed["assets"])):
            self.run(
                [
                    *self.command("gh"),
                    "release",
                    "upload",
                    tag,
                    str(Path(packet["directory"]) / name),
                    "--repo",
                    repository,
                ],
                root,
                timeout=300,
            )
        observed = self.inspect_public(packet, root)
        require(set(observed["assets"]) == set(packet["files"]), "draft upload incomplete")
        self.api(
            repository,
            "releases/" + str(observed["id"]),
            root,
            method="PATCH",
            body={"draft": False, "prerelease": prerelease, "make_latest": "false"},
        )
        return self.inspect_public(packet, root)

    def pico_publisher(self, packet: dict[str, Any], root: Path) -> str:
        repository = packet["repository"]
        publisher = self.api(repository, "commits/devel", root)["sha"]
        for name, expected in packet["publisher_files"].items():
            item = self.api(repository, "contents/" + name + "?ref=" + publisher, root)
            require(item.get("encoding") == "base64", "publisher source is not inspectable")
            import hashlib

            require(
                hashlib.sha256(base64.b64decode(item["content"])).hexdigest() == expected,
                "prepared Pico publisher is unmerged or changed; return to preparation",
            )
        return str(publisher)

    def publish_pico(self, packet: dict[str, Any], root: Path) -> dict[str, Any]:
        repository = packet["repository"]
        publisher = self.pico_publisher(packet, root)
        checkpoint = root / "pico-publisher.json"
        title = "Prepared firmware " + packet["owner_plan"]["packet_sha256"] + " (publish)"
        endpoint = (
            "actions/workflows/publish-prepared.yml/runs?event=workflow_dispatch&per_page=100"
        )
        if checkpoint.exists():
            current = lc.load(regular(checkpoint))
            require(
                current["packet_sha256"] == packet["packet_sha256"], "publisher checkpoint differs"
            )
            require(
                current["publisher"] == publisher, "publisher source changed during continuation"
            )
        else:
            before = self.api(repository, endpoint, root)["workflow_runs"]
            current = {
                "packet_sha256": packet["packet_sha256"],
                "publisher": publisher,
                "before": [v["id"] for v in before],
                "run_id": None,
            }
            lc.save(checkpoint, current, new=True)
            # Persist intent first. An interrupted dispatch is reconciled by title,
            # source and previously observed IDs; it is never blindly repeated.
            self.api(
                repository,
                "actions/workflows/publish-prepared.yml/dispatches",
                root,
                method="POST",
                body={
                    "ref": "devel",
                    "inputs": {
                        "prepared_plan": json.dumps(packet["owner_plan"], sort_keys=True),
                        "packet_sha256": packet["owner_plan"]["packet_sha256"],
                        "publisher_source": publisher,
                        "publish": "true",
                    },
                },
            )
        if current["run_id"] is None:
            for _ in range(12):
                runs = self.api(repository, endpoint, root)["workflow_runs"]
                matches = [
                    v
                    for v in runs
                    if v["id"] not in current["before"]
                    and v["head_sha"] == publisher
                    and v["head_branch"] == "devel"
                    and v["display_title"] == title
                ]
                require(len(matches) <= 1, "ambiguous publisher dispatch; inspect checkpoint")
                if matches:
                    current["run_id"] = matches[0]["id"]
                    lc.save(checkpoint, current)
                    break
                time.sleep(5)
            require(
                current["run_id"] is not None,
                "publisher dispatch not visible; retain checkpoint and resume, never redispatch",
            )
        previous_run = self.api(repository, "actions/runs/" + str(current["run_id"]), root)
        if previous_run["status"] == "completed" and previous_run["conclusion"] != "success":
            attempt = previous_run["run_attempt"]
            require(
                current.get("rerun_intent") != attempt,
                "rerun intent is unresolved; inspect canonical run before another attempt",
            )
            current["rerun_intent"] = attempt
            lc.save(checkpoint, current)
            self.run(
                [*self.command("gh"), "run", "rerun", str(current["run_id"]), "--repo", repository],
                root,
            )
        self.run(
            [
                *self.command("gh"),
                "run",
                "watch",
                str(current["run_id"]),
                "--repo",
                repository,
                "--exit-status",
            ],
            root,
            timeout=1500,
        )
        observed = self.inspect_public(packet, root)
        require(observed["state"] == "published", "Pico canonical publication incomplete")
        return observed

    def accept(self, packet: dict[str, Any], root: Path) -> dict[str, Any]:
        name = packet["component"]
        destination = root / "continuations" / ("download-" + secrets.token_hex(8))
        destination.mkdir(mode=0o700, parents=True)
        self.run(
            [
                *self.command("gh"),
                "release",
                "download",
                packet["tag"],
                "--repo",
                packet["repository"],
                "--dir",
                str(destination),
            ],
            root,
            timeout=600,
        )
        require(
            inventory(destination) == packet["files"],
            "downloaded public bytes differ from prepared packet",
        )
        if name == "pi":
            for target in TARGETS:
                target_dir = destination / target
                target_dir.mkdir(mode=0o777)
                target_dir.chmod(0o777)
                mounts = {Path(packet["helper_source"]): "/source:ro", target_dir: "/output"}
                helper = ["python3", "-B", "/source/scripts/precompiled_binary.py"]
                resolved = self.docker(
                    packet["images"][target],
                    mounts,
                    [
                        *helper,
                        "resolve",
                        "--repo",
                        packet["repository"],
                        "--branch",
                        packet["public_branch"],
                    ],
                    root,
                )
                expected = packet["files"]["wsprrypi-" + target]["sha256"]
                require(
                    resolved["stdout"].strip() == "release " + packet["tag"] + " " + expected,
                    "default public Pi selector differs from prepared release",
                )
                self.docker(
                    packet["images"][target],
                    mounts,
                    [
                        *helper,
                        "fetch",
                        "--repo",
                        packet["repository"],
                        "--tag",
                        packet["tag"],
                        "--directory",
                        "/output",
                        "--sha256",
                        expected,
                    ],
                    root,
                )
                self.docker(
                    packet["images"][target],
                    mounts,
                    [*helper, "check", "--binary", "/output/wsprrypi", "--full"],
                    root,
                )
        elif name == "rp1":
            work = destination / "acceptance"
            work.mkdir(mode=0o777)
            work.chmod(0o777)
            self.docker(
                packet["image"],
                {
                    Path(packet["helper_source"]): "/source:ro",
                    Path(packet["consumer_source"]): "/consumer:ro",
                    Path(packet["directory"]): "/candidate:ro",
                    work: "/output",
                },
                [
                    "python3",
                    "-B",
                    "/consumer/scripts/maintainer/accept_rp1_release.py",
                    "--provider-source",
                    "/source",
                    "--candidate",
                    "/candidate",
                    "--release",
                    "--verify-publication",
                    "--release-tag",
                    packet["tag"],
                    "--output",
                    "/output/public",
                ],
                root,
                tmp_build=True,
            )
            result = lc.load(work / "public/acceptance.json")
            require(
                result["status"] == "PASS"
                and result["publicationVerified"]
                and result["installationPerformed"] is False,
                "RP1 public consumer acceptance failed",
            )
        else:
            tool = Path(packet["helper_source"])
            code = (
                "import importlib.util,json;from pathlib import Path;"
                "p=Path(" + repr(str(tool / "scripts/usb_installer.py")) + ");"
                "s=importlib.util.spec_from_file_location('core',p);c=importlib.util.module_from_spec(s);"
                "s.loader.exec_module(c);c.SCHEMA_BYTES=(p.parent/'ci/release-manifest-2.schema.json').read_bytes();"
                "f,m,i=c.resolve(c.Https(),branch="
                + repr(packet["branch"] if packet["branch"] != "release" else "main")
                + ",version="
                + repr(packet["version"])
                + ");"
                "assert m['source']['revision']==" + repr(packet["source"]) + ";"
                "assert m['firmware_version']=="
                + repr(packet["version"])
                + ";print(json.dumps({'verified':True}))"
            )
            self.run([*self.command("python"), "-B", "-c", code], root, timeout=600)
        return {
            "status": "PASS",
            "packet_sha256": packet["packet_sha256"],
            "source": packet["source"],
            "version": packet["version"],
            "checks": "public exact bytes and owning consumer resolution",
            "hardware": False,
        }
