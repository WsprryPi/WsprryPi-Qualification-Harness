# Prepare a release now and publish it later

The operator goal is to build and test an exact Pi, Pico, RP1 or coordinated WsprryPi release, exit, and publish those tested bytes in a later invocation. `lifecycle release-session-plan`, `release-prepare`, `release-status` and `release-publish` implement that handoff. Preparation has no publication flag and makes no public tags, releases, assets or branch bookmarks. Publication never builds replacement artifacts. RP1’s owning acceptance validator may rebuild a disposable comparison package to verify reproducibility; it never substitutes that package for retained publication bytes. Current publication authority is still required; possession of a retained plan is not authorization.

## Prepare the inputs

Use clean owning Git worktrees with canonical `origin` URLs. Existing work stays in its original checkout. Resolve the intended versions and exact canonical CI runs first. Pi CI must have tested the selected immutable Pico source in both network and USB management jobs. RP1 CI must include the real native lifecycle job. Missing, failed or skipped required jobs block preparation. The Pico build's optional publisher may be skipped; its verification job must pass.

Pi preparation calls `scripts/container_build.py TARGET OUTPUT --release-version VERSION` for all four supported targets, or revalidates existing exports with `mode: exports`. Each export must retain its executable, build metadata, checksums and immutable `build-image-id.txt`. `mode: packet` additionally retains a pre-existing six-file publication packet and checks it against all four original exports. The owning full ABI checker and actual executable version run in each original build image. The adapter records source/build branch separately from the intended public selector branch.

RP1 preparation calls `scripts/build-release-candidate --version VERSION --source-commit SHA --output DIRECTORY --release` and `scripts/validate-release-candidate`, then the owning Pi `scripts/maintainer/accept_rp1_release.py` non-installing consumer check. `--release` means release-shaped local files, not public publication. `mode: candidate` revalidates an existing seven-file candidate. Supply an available Linux Docker image by its immutable `sha256:...` ID, containing the owning build/validation dependencies. The Pi consumer source is an explicit input; in a coordinated plan it must equal the selected Pi source.

Pico uses its canonical `software-build.yml` with publication disabled. For example, after inspecting that workflow's current inputs, start a build with `gh workflow run software-build.yml --repo WsprryPi/WsprryPico --ref devel -f publish=false`. Identify the resulting exact run ID from authenticated Actions records and watch it with `gh run watch RUN_ID --repo WsprryPi/WsprryPico --exit-status`. Do not select whichever concurrent run happens to finish last. Preparation retrieves the exact successful run/attempt/source/version through owning `scripts/ci/prepared.py prepare`; it validates the immutable deployment/audit archives and retains nine public files. No firmware rebuild occurs during later publication. The tooling worktree must contain the prepared-publication capability and the selected firmware commit in its Git history.

Use `python scripts/ci/prepared.py --help`, `prepare --help` and `verify --help` in that tooling worktree for the standalone owner interface. Local `verify PLAN --destination NEW_DIRECTORY` re-downloads and checks the original immutable archives without public effects. Its environment needs the hash-pinned `scripts/ci/requirements.txt` dependencies and authenticated `gh`. Original Actions archive expiry or unavailable validation proof blocks continuation; retained local files alone do not bypass that gate.

## Create requests for four independent operator goals

The following Python example creates four request files outside Git. Replace the local paths, versions, immutable image ID and canonical run IDs with the combination actually selected and tested. `publish: false` selects a fixed, already published companion. A coordinated request may also reuse existing components this way; it publishes only components marked true. The examples use existing exports/candidates to avoid redundant builds. Choose `build` and omit `exports`/`candidate` to build during preparation instead.

```python
import copy, json, subprocess
from pathlib import Path

out = Path("/absolute/private/release-requests")
out.mkdir(parents=True, exist_ok=True)
pi = "/absolute/Pi-clean-worktree"
pico = "/absolute/Pico-firmware-clean-worktree"
tooling = "/absolute/Pico-publisher-clean-worktree"
rp1 = "/absolute/RP1-clean-worktree"


def head(path):
    return subprocess.check_output(["git", "-C", path, "rev-parse", "HEAD"], text=True).strip()


components = {
    "pi": {
        "source": pi,
        "version": "4.0.0-beta.7",
        "tag": "v4.0.0-beta.7",
        "publish": True,
        "ci_run": 12345,
        "mode": "exports",
        "public_branch": "devel",
        "pico_source": head(pico),
        "exports": {
            target: "/absolute/private/pi-exports/" + target
            for target in ("armv6-bookworm", "armv6-trixie", "aarch64-bookworm", "aarch64-trixie")
        },
    },
    "pico": {
        "source": pico,
        "tooling_source": tooling,
        "version": "0.1.0-ci.12346.1",
        "tag": "v0.1.0-ci.12346.1",
        "publish": True,
        "ci_run": 12346,
        "attempt": 1,
        "mode": "ci",
        "branch": "devel",
    },
    "rp1": {
        "source": rp1,
        "consumer_source": pi,
        "version": "1.0.0-rc.3",
        "tag": "v1.0.0-rc.3",
        "publish": True,
        "ci_run": 12347,
        "mode": "candidate",
        "candidate": "/absolute/private/rp1-candidate",
        "image": "sha256:" + "a" * 64,
    },
}
for scope in ("pi", "pico", "rp1", "system"):
    selected = copy.deepcopy(
        components if scope in ("pi", "system") else {scope: components[scope]}
    )
    for name, spec in selected.items():
        spec["publish"] = scope == "system" or scope == name
    request = {"schema_version": 1, "scope": scope, "components": selected}
    if "pi" in selected:
        request["pi_dependencies"] = {
            name: {
                "source": head(components[name]["source"]),
                "version": components[name]["version"],
            }
            for name in ("pico", "rp1")
        }
    (out / (scope + ".json")).write_text(json.dumps(request, indent=2) + "\n")
```

For independent Pi publication, change the Pico/RP1 entries to suitable existing releases and their exact source/CI/artifact inputs before planning. Pi's owning `DEFAULT_RELEASE_TAG` must select the prepared RP1 version. There is no automatic dependency edit or substitution. A new combination requires preparation and compatible canonical tests. The request schema is [`prepared-release-request`](../../schemas/prepared-release-request.schema.json). Optional `tools` supplies explicit argument arrays for `git`, `python`, `gh` and `docker`; select a Python environment with the owning validation dependencies installed. Tool executable identities, controller code and source copies are retained. The portable source resolver’s PATH-selected Git is separately pinned from an optional owning-builder Git command. Source freezing uses the maintained process supervisor and retains complete command records.

## Worked prepare → exit → publish-later commands

These commands come from the maintained CLI help. `PLAN_SHA` is `plan_sha256` from the plan JSON; `PREPARED_SHA` is `prepared_sha256` printed by preparation/status. Substitute the actual digest rather than typing these labels. Use a different new state root for every preparation. An existing root is never overwritten.

Pi, retaining fixed public Pico/RP1 companions:

```text
wsprrypi-qualification lifecycle release-session-plan /absolute/private/release-requests/pi.json --state-root /absolute/private/releases/pi-001 --output /absolute/private/pi-plan.json
wsprrypi-qualification lifecycle release-prepare /absolute/private/pi-plan.json --confirm-plan-sha256 PLAN_SHA
wsprrypi-qualification lifecycle release-status /absolute/private/releases/pi-001
```

Exit the preparation process or close the shell. In a later invocation, inspect first, validate current gates without publication, then publish only with current authority:

```text
wsprrypi-qualification lifecycle release-status /absolute/private/releases/pi-001
wsprrypi-qualification lifecycle release-publish /absolute/private/releases/pi-001 --confirm-prepared-sha256 PREPARED_SHA --validate-only
wsprrypi-qualification lifecycle release-publish /absolute/private/releases/pi-001 --confirm-prepared-sha256 PREPARED_SHA --enable-publication
```

Pico alone:

```text
wsprrypi-qualification lifecycle release-session-plan /absolute/private/release-requests/pico.json --state-root /absolute/private/releases/pico-001 --output /absolute/private/pico-plan.json
wsprrypi-qualification lifecycle release-prepare /absolute/private/pico-plan.json --confirm-plan-sha256 PLAN_SHA
wsprrypi-qualification lifecycle release-status /absolute/private/releases/pico-001
```

Exit. Later:

```text
wsprrypi-qualification lifecycle release-publish /absolute/private/releases/pico-001 --confirm-prepared-sha256 PREPARED_SHA --validate-only
wsprrypi-qualification lifecycle release-publish /absolute/private/releases/pico-001 --confirm-prepared-sha256 PREPARED_SHA --enable-publication
```

RP1 alone:

```text
wsprrypi-qualification lifecycle release-session-plan /absolute/private/release-requests/rp1.json --state-root /absolute/private/releases/rp1-001 --output /absolute/private/rp1-plan.json
wsprrypi-qualification lifecycle release-prepare /absolute/private/rp1-plan.json --confirm-plan-sha256 PLAN_SHA
wsprrypi-qualification lifecycle release-status /absolute/private/releases/rp1-001
```

Exit. Later:

```text
wsprrypi-qualification lifecycle release-publish /absolute/private/releases/rp1-001 --confirm-prepared-sha256 PREPARED_SHA --validate-only
wsprrypi-qualification lifecycle release-publish /absolute/private/releases/rp1-001 --confirm-prepared-sha256 PREPARED_SHA --enable-publication
```

Coordinated WsprryPi superproject, preserving the selected component combination:

```text
wsprrypi-qualification lifecycle release-session-plan /absolute/private/release-requests/system.json --state-root /absolute/private/releases/system-001 --output /absolute/private/system-plan.json
wsprrypi-qualification lifecycle release-prepare /absolute/private/system-plan.json --confirm-plan-sha256 PLAN_SHA
wsprrypi-qualification lifecycle release-status /absolute/private/releases/system-001
```

Exit. Later:

```text
wsprrypi-qualification lifecycle release-publish /absolute/private/releases/system-001 --confirm-prepared-sha256 PREPARED_SHA --validate-only
wsprrypi-qualification lifecycle release-publish /absolute/private/releases/system-001 --confirm-prepared-sha256 PREPARED_SHA --enable-publication
```

The system order is Pico, RP1, then Pi. All selected and fixed packets are checked before the first public write. Fixed companions must remain publicly available with identical assets. The engine neither follows a newer dependency nor rebuilds to make a selection work.

## Retained state, readiness and verification

The state root is private durable operator storage outside Git and product-managed directories. `preparation-plan.json` retains the initial resolution; `sources/` contains independent frozen Git copies; component directories retain exact public files and full build/validation provenance; `evidence/` retains complete preparation command records; `prepared.json` seals their inventories and hashes. `state.json` records mutable publication/acceptance checkpoints. `continuations/` retains later inspections, commands and downloaded consumer packets. `pico-publisher.json` binds dispatch intent, exact publisher source and canonical run ID. Each preparation/continuation prints a durable progress JSONL path beside the state root and its exact viewer command before long-running work. Preserve that log with the root. Preserve the whole root, original Docker images and required tool environment until delivery/recovery is complete. Do not commit it.

`release-status` performs offline integrity verification. `retained_artifacts_ready` concerns local bytes only. `ready_for_publication` remains false until online prerequisites have actually been assessed; use `release-publish --validate-only` for that assessment. A validated result lists actual public states, checks already published packets through their real consumers, and lists remaining publications. It does not claim that future network permissions, canonical jobs or hardware will remain available. The currently implemented status does not cache online readiness as authority.

Every later invocation checks schemas, sealed plan/controller/tool/source identities, complete file inventories, sizes and SHA256 values, build/test evidence and exact dependency selections. Changed, extra or missing files refuse before publication. A changed controller, selected tool, publisher implementation, source, canonical archive or incompatible companion selection requires a new preparation; the refusal explains the invalidation. Never edit a sealed plan or checkpoint to acknowledge a change. Original development worktrees may advance because retained source copies are independent.

## Actual tag and publication mechanics

| Owner | Non-publishing preparation | Tag creation and public effects |
| --- | --- | --- |
| Pi | Version override builds four exports without creating a tag; full local validation precedes publication. | The harness canonical release adapter creates a new annotated tag only during enabled publication, or verifies an exact existing tag. Pi branch/PR CI validates source; there is no tag-driven binary publisher. Draft creation, missing-asset upload and final publication are explicit API operations. |
| RP1 | Release-shaped candidate builder and validator do not require a tag or publish. | Enabled publication creates a new annotated tag or verifies an exact existing one, then publishes the seven unchanged assets. Tag creation alone is not the package publisher. |
| Pico CI stream | Canonical manual `software-build.yml` with `publish=false`, followed by exact run retrieval. | The trusted later `publish-prepared.yml` job creates a missing immutable version tag, preserves nine tested files, then updates the source-ordered branch bookmark. It never moves an existing version or bookmark tag. Bookmark release metadata may advance according to the existing ancestry policy. |
| Pico semantic tagged release | The exact tag must already exist and canonical verification must succeed with publication disabled. Tag creation is a separately selected operation before this preparation. | Pushing a `v*` tag triggers canonical verification and therefore is not side-effect-free. Publication still requires explicit manual selection. A CI version must not be manually tagged as a semantic-release preparation shortcut. |
| Coordinated system | Resolve all components and tested dependencies together, reusing suitable existing releases. | Invoke only selected owners in dependency order; a partially delivered system retains each successful receipt. |

Pico's later run has a different identity from the original build. Its release-body receipt records the packet digest and publisher attempt history, separately from the nine frozen artifacts. Consumers require authenticated canonical repository/workflow/devel source/run/job provenance and a successful later publisher when the original publisher was skipped. The publisher's full required implementation closure must match the prepared code on public devel; an unmerged companion branch is an explicit gate, never a reason to merge automatically. Retry metadata may append an attempt; asset bytes are never replaced. A run interrupted after publishing bytes can remain pending until a successful canonical continuation completes.

## Recovery, public acceptance and limitations

After a partial publication, retain all files and inspect status, actual releases and the canonical run. Use the same prepared digest and `--resume`:

```text
wsprrypi-qualification lifecycle release-publish /absolute/private/releases/system-001 --confirm-prepared-sha256 PREPARED_SHA --validate-only
wsprrypi-qualification lifecycle release-publish /absolute/private/releases/system-001 --confirm-prepared-sha256 PREPARED_SHA --enable-publication --resume
```

The controller checkpoints intent before effects and records successful publications before acceptance. A later component failure cannot erase them. Resume re-inspects all public packets and skips only unchanged, already accepted components. Partial drafts may receive only missing identical assets; conflicting assets or tags refuse, with no clobber, rollback or force move. Docker adapters name and label their own containers, verify daemon-side absence after success or interruption, and refuse to remove foreign ownership. Unverified cleanup prevents success. A surviving controller lock is never stolen. Inspect a proved-dead controller before explicitly retiring its account-wide lock. If a Pico dispatch intent has no unambiguous run ID, inspect/reconcile it rather than dispatching another run blindly. A failed identified publisher can be rerun canonically; its attempt history remains visible. If reconciliation cannot prove a safe continuation, report the exact unresolved gate instead of editing proof.

After each publication, mandatory software consumer acceptance downloads and verifies all exact public bytes. Pi exercises the actual default resolver/fetch/full ABI checks for all four targets. RP1 runs the owning non-installing publication consumer validator. Pico resolves its explicit prepared version through the owning verified USB bundle resolver without opening USB. These checks do not install, transmit or qualify hardware. Physical public lifecycle acceptance remains separately selected through the maintained [lifecycle guide](lifecycle-release.md): use its generated Pi/Pico/system public plans, RF disabled, and exact current host/device authority. A release needing physical acceptance remains gated until that separately recorded run passes; software completion must not be described as hardware qualification.

The portable core and fixture handoff tests support macOS, Linux and native Windows. The concrete builds require Docker with supported Linux userspaces; the current Pico consumer adapter uses its owning macOS/Linux installer and therefore is unavailable on native Windows. Do not substitute WSL USB for native Windows qualification. Canonical Actions access, original archive retention and available immutable images remain external requirements. No new product release is created merely to validate this implementation. Deterministic publication transport tests establish handoff/refusal/checkpoint behavior; real read-only accepted-release rehearsals and new-publication gates must be reported separately.
