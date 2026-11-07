# Lifecycle tests and release orchestration

`wsprrypi-qualification lifecycle` coordinates explicitly selected Pi, Pico, RP1 and system work without RF. It has separate schemas, statuses and recovery duties from `complete-test`. Planning reads local source and artifacts; it does not contact hosts, open USB, dispatch CI, install software or publish. Execution requires the exact plan digest. The operator's current authority must cover every declared effect. Use current CLI help before an operation.

## Commands and storage

```text
wsprrypi-qualification lifecycle --help
wsprrypi-qualification lifecycle source pi /absolute/Pi-worktree
wsprrypi-qualification lifecycle plan REQUEST.json --output PLAN.json
wsprrypi-qualification lifecycle execute PLAN.json /durable/controller-state/run-001 --confirm-plan-sha256 SHA256
wsprrypi-qualification lifecycle status /durable/controller-state/run-001
wsprrypi-qualification lifecycle report /durable/controller-state/run-001
```

The state root is an absolute durable directory outside all source and protected product trees. Keep it outside installer-managed trees and operating-system temporary storage. The runner creates a new exclusive child directory, plan, atomic fsynced checkpoints, complete child stdout/stderr records, a flushed progress JSONL stream and a result. It prints the exact progress viewer command before starting work. Evidence is private operational output; never commit it here. Retain all dependent input files, baseline/candidate bundles and owning-component recovery records until their duties close. File hashes authenticate retained bytes; they do not authenticate an untrusted author.

Local controller resource locks use one account-wide namespace across output roots. Generated Linux profiles bind `target_identity` (machine-ID SHA256, hostname, OS ID/version, kernel and board model), recheck it before effects, and key host ownership on the physical machine digest rather than an SSH alias. USB ownership is additionally keyed by exact serial. Custom resource names must identify actual hosts and USB serials consistently. A surviving lock is never stolen. After an uncatchable controller loss, inspect its process and target state before an operator retires a proved-stale controller lock. Production installers retain their own target/device locks. A controller-local lock cannot exclude an unrelated account or an operator bypassing the production lock.

The portable controller uses Python standard-library paths and subprocess arguments plus the existing JSON Schema dependency. Linux shell, systemd, package/kernel inspection and USB are explicit capability adapters. The built-in Pi SSH adapter requires a Unix controller with `/usr/bin/ssh`; native Windows controllers use explicit Windows OpenSSH command recipes with the portable runner. The endpoint requires native Linux, Bash, GNU timeout, flock, noninteractive sudo, systemd, APT/DKMS and OpenSSH. Its SSH transport requires strict pinned host keys, BatchMode and no agent forwarding. Generated SSH Pico profiles require a native Linux bench; explicit local command recipes can use the owning installer on macOS. Pico USB requires the owning installer's supported picotool; physical Windows/WSL USB is unsupported. Native Windows remains a portable-controller validation target.

## Request and result contracts

[`lifecycle-request.schema.json`](../../schemas/lifecycle-request.schema.json), [`lifecycle-plan.schema.json`](../../schemas/lifecycle-plan.schema.json) and [`lifecycle-result.schema.json`](../../schemas/lifecycle-result.schema.json) have byte-identical packaged copies. The controller also validates [`lifecycle-checkpoint.schema.json`](../../schemas/lifecycle-checkpoint.schema.json) and verifies every claimed completed step against its retained execution record before a resume can contact an adapter. Pi and Pico bench inputs also have strict [`lifecycle-pi-bench`](../../schemas/lifecycle-pi-bench.schema.json) and [`lifecycle-pico-bench`](../../schemas/lifecycle-pico-bench.schema.json) schemas. A request includes operation, independent scope, named profile, `rf_enabled: false`, exact source identities, artifacts, dependency/toolchain/target inputs, resource ownership, protected paths, expected final states, required checks, ordered steps and CI evidence. Source identity includes HEAD, binary tracked diff, status and untracked file bytes. Newly resolved plans also bind controller modules, Python executable/version and the harness source/dirty identity. Ignored build inputs and external dependencies must be bound separately as artifacts/inputs.

Each step declares its owning component, phase, mutation scope, check, hash-bound executable/input files, structured argv, external working directory, deadline, expected exit and independent JSON assertions. Phases are `inspect`, `effect`, `verify`, `cleanup`, `recover`, `publish` and `public_acceptance`. Recipes are reviewed engineering inputs, not a command sandbox: declaring a command read-only does not make arbitrary code read-only. Use maintained production entrypoints and inspect their effects. Never supply secrets in argv or the request; use private supported credential stores.

The first step inspects actual state; every effect has a later independent component check; the final step verifies state. Named profile checks cannot be omitted. Missing required checks become plan blockers. Confirmed execution of a blocked plan retains a `blocked_prerequisite` result without calling an adapter or cleaning an unowned baseline. Verification needs explicit JSON assertions and distinguishes missing values, null, booleans and numbers. An expected nonzero refusal can pass its step only when its declared assertions also pass. Unattempted checks cannot pass. A successful child exit alone never establishes device readiness.

Lifecycle statuses are `pass`, `expected_refusal`, `unexpected_failure`, `blocked_prerequisite`, `abort`, `inconclusive` and `cleanup_failure`. Cleanup failure overrides scenario success. Records retain complete logs and hashes; report validation rechecks required checks and child outcomes. A pass applies only to the selected scenarios, exact inputs and observed final state. It establishes no RF, timing, spectral, calibrated-power or stable-release qualification.

## Public Pi/RP1 eight-step profile

```text
wsprrypi-qualification lifecycle pi-public-plan BENCH.json --state-root /durable/controller-state --pi-source /absolute/Pi-worktree --rp1-source /absolute/RP1-checkout --output PI-REQUEST.json
wsprrypi-qualification lifecycle plan PI-REQUEST.json --output PI-PLAN.json
wsprrypi-qualification lifecycle execute PI-PLAN.json /durable/controller-state/pi-001 --confirm-plan-sha256 SHA256
```

`BENCH.json` supplies `target_identity`, `host`, `expected_hostname`, invoking `home`, `known_hosts`, remote durable `evidence_root`, `remote_lock`, verified `baseline_local` and `baseline_remote`, `baseline_sha256`, `candidate_sha256`, `provider_source`, `provider_tooling_sha256`, `eligible_kernels` and exact `owned_paths`. Bind machine-specific facts only after current inspection. The baseline is copied into durable controller input storage before planning. Prepare the empty `observations` directory under the state root. The remote lock's parent must already exist outside product trees.

The maintained sequence installs through the actual public raw devel stdin/bootstrap, verifies installed readiness, uninstalls through the supported streamed path, independently audits absence, installs a verified earlier executable, repeats it identically, performs the default public upgrade, then uninstalls and repeats absence/shared-state checks. The generated profile explicitly includes RP1 mutation and separate RP1 final verification. It refuses to describe normal Pi 5 installation as Pi-isolated. No checked-out script substitutes for the public path. Before and after streamed execution the adapter verifies the selected public devel object; a byte-verification stage checks the actual downloaded script against the selected source before forwarding its unchanged bytes to the installer stdin. Public-source drift refuses or fails the operation rather than attributing it to the selected source. A separately reviewed Pi-isolated request must use an owning production operation that excludes companion mutation and verify fixed companions unchanged.

Before each install, the invoking home's stale `finished` marker is removed; it is created only after success. Verification checks executable bytes, version observation, service active/enabled, UI/API/config availability, RF-disabled INI policy, neutral provider state or an explicitly declared configured idle route, output disabled, package/tooling identities and selected-kernel module inventory. Reinstall/upgrade compare canonical settings and module hashes/mtimes. The maintained profile seeds distinctive synthetic callsign, locator, reported WSPR power and the canonical 32-character test message through revision-bound production configuration PATCH requests while enforcing Transmit false and Enable on Boot Never. It verifies actual API readback, restarts the service, and compares both canonical settings and runtime readback after reinstall and upgrade. A custom fixture is restricted to station/message fields and remains an explicit immutable bench input.

The independent Linux audit checks exact declared paths, systemd units/enabled inventory, dedicated group, package status, DKMS registrations, modules/device nodes, source/module artifacts, product-name sweeps of the root filesystem, runtime and boot locations, and package health. Exclusions are reported explicitly for virtual filesystems, credential history and the independent evidence store. It never deletes product residue. Shared boot configuration, stock Apache configuration and picotool hashes are compared before/after; installed observations retain HTTP, package and Apache diagnostics. Standalone Pico resources remain independently owned. A retained harness baseline under the evidence store is not a product residue exemption.

## Pico fresh and preserved tracks

```text
wsprrypi-qualification lifecycle pico-plan PICO-BENCH.json --state-root /durable/controller-state --pico-source /absolute/Pico-feature-worktree --track fresh --output PICO-FRESH.json
wsprrypi-qualification lifecycle pico-plan PICO-BENCH.json --state-root /durable/controller-state --pico-source /absolute/Pico-feature-worktree --track preserve --output PICO-PRESERVE.json
wsprrypi-qualification lifecycle plan PICO-FRESH.json --output PICO-PLAN.json
wsprrypi-qualification lifecycle execute PICO-PLAN.json /durable/controller-state/pico-001 --confirm-plan-sha256 SHA256
```

The bench supplies `target_identity`, `host`, `known_hosts`, exact `serial`, physical `parent`, `installer_local`, `schema_local`, staged `remote_installer`, `remote_schema`, `remote_adapter`, production `transaction_state`, `picotool`, private `evidence_root`, and independently verified `baseline_bundle` and `candidate_bundle` paths on the endpoint. Stage the packaged `lifecycle_assets/pico_cycle.py.txt` as a Python file, and stage the exact selected production installer with its `ci/release-manifest-2.schema.json` at the relative path expected by that installer. Plans bind local staged inputs; the adapter rechecks installer/schema hashes and verifies bundle bytes before reset. Keep the staged adapter, installer, schemas, bundles and observations outside the production transaction-state root; its inventory accepts transaction directories only. Planning rejects overlapping layouts. Do not overwrite staging during a run.

Both tracks verify serial/physical topology and idle, unowned, RF-inactive state. Fresh explicitly uses the owning installer's `erase --software-bootsel`, independently reads back all 4 MiB blank, installs a verified bundle, checks unconfigured defaults, seeds a distinctive synthetic hostname through Control/1, reinstalls and checks saved semantics after its verification reboot. Preserve verifies the actual baseline, seeds synthetic settings, performs identical-image reinstall and compatible upgrade using the independently identified original bundle, compares saved semantics, retries owning-component recovery and verifies final state. The adapter never edits product receipts, invents image references, directly loads firmware, resumes a schedule or stops a job.

The primary track ends installed with verified synthetic settings. A selected final cleanup request can explicitly erase and reinstall its verified input to return to fresh defaults or leave verified blank ROM BOOTSEL. Erase, provisioning reset and software maintenance are distinct operations. Normal supported guarded cycles need no BOOTSEL button. Unsupported/unresponsive firmware, changed physical topology or an unconfirmed reset can require physical recovery. A missing Pico blocks a required Pico/system request; Pi/RP1-only requests do not silently acquire USB.

A `pico-migration` request separately requires explicit new-field defaults and newly introduced settings round trips. The ordinary preservation track does not claim to test new settings that the selected firmware pair does not introduce. Physical power-cut, every journal fault and arbitrary downgrade coverage remain separate selected scenarios; reuse the owning deterministic transaction/recovery tests before destructive bench fault injection.

## Standalone RP1 and coordinated system requests

Standalone RP1 requests select `scope: rp1`, exact provider source/package/tooling, neutral final state and the native or live check set from `lifecycle_recipes.CHECKS`. Reuse the maintained [`provider lifecycle test`](https://github.com/WsprryPi/RP1-GPIO-CLK-DKMS/blob/devel/tests/check_provider_lifecycle.py), [`native removal test`](https://github.com/WsprryPi/RP1-GPIO-CLK-DKMS/blob/devel/tests/provider_package_removal_linux.py), and provider `tools/run-package-lifecycle.py --help`. The canonical native invocation is:

```text
python /absolute/RP1/tools/run-package-lifecycle.py --image VERIFIED_IMAGE --candidate /absolute/verified-candidate --predecessor /absolute/verified-version-fixture --output /new/external/native-result
python /absolute/RP1/tools/summarize-lifecycle.py /new/external/native-result/result.json
wsprrypi-qualification lifecycle plan RP1-REQUEST.json --output RP1-PLAN.json
wsprrypi-qualification lifecycle execute RP1-PLAN.json /durable/controller-state/rp1-001 --confirm-plan-sha256 SHA256
```

The native matrix executes genuine preflight/APT/dpkg/DKMS and controlled failures in disposable environments; its synthetic version increase is fixture evidence. A physical version increase requires two available genuine compatible packages. Keep missing package/download, foreign-file, changed ownership, concurrent-lock, partial-removal and recovery checks in the owning tests. Never manufacture a public tag to provide a baseline. Plan only the selected physical cases; native tests do not authorize a module load or RF.

The maintained public system profile adds a real production Pi USB identity lease between standalone Pico observations, then verifies unchanged physical identity, boot and saved semantics after standalone reacquisition. It does not enroll the Pico, select a transmit route, submit a job or program firmware through the Pi web UI. Stage the packaged `pi_usb_handoff.py.txt` next to the staged Pico cycle as `pi_usb_handoff.py`. The Pico bench's candidate bundle must independently identify the currently installed firmware. Use fresh output destinations for each request:

Both production callers must support opening another user's existing serial lock without `O_CREAT`; Linux protected-file policy can otherwise refuse cross-user handoff in the shared sticky directory. Retain any refusal and its response body. Never unlink the lock, change its ownership, relax the system policy, or substitute an unpublished binary while claiming public-system acceptance. When the necessary product fix is only on an unmerged feature branch, validate that exact binary through an explicitly selected local-binary recipe and report public-system acceptance as unresolved until a suitable published build is available.

```text
wsprrypi-qualification lifecycle system-public-plan PI-BENCH.json PICO-BENCH.json --state-root /durable/controller-state --pi-source /absolute/Pi-worktree --rp1-source /absolute/RP1-checkout --pico-source /absolute/Pico-feature-worktree --output SYSTEM-PUBLIC.json
wsprrypi-qualification lifecycle plan SYSTEM-PUBLIC.json --output SYSTEM-PUBLIC-PLAN.json
wsprrypi-qualification lifecycle execute SYSTEM-PUBLIC-PLAN.json /durable/controller-state/system-public-001 --confirm-plan-sha256 SHA256
```

A coordinated request uses `scope: system`, all exact component sources and independent final states, a shared host/device ownership set, and ordered component steps followed by real USB controller handoff and fixed-companion compatibility checks. Required checks are `pi-acceptance`, `rp1-acceptance`, `pico-acceptance`, `usb-handoff`, `fixed-compatibility` and `final-state`. Reuse the component production recipes; do not invoke `complete-test`. Independent successes alone cannot supply the handoff check. An absent handoff or unavailable exact paired source remains a blocker, even if native software checks pass.

```text
wsprrypi-qualification lifecycle system-plan PI-REQUEST.json PICO-REQUEST.json HANDOFF-REQUEST.json --state-root /durable/controller-state --output SYSTEM-REQUEST.json
wsprrypi-qualification lifecycle plan SYSTEM-REQUEST.json --output SYSTEM-PLAN.json
wsprrypi-qualification lifecycle execute SYSTEM-PLAN.json /durable/controller-state/system-001 --confirm-plan-sha256 SHA256
```

## Release planning, CI selection and publication

```text
wsprrypi-qualification lifecycle release-plan RELEASE-REQUEST.json --output RELEASE-GRAPH.json
```

The [complete release request example](../examples/lifecycle-release-request.json) is a runnable, read-only schema example with fictional sources and missing CI evidence. It deliberately requests fresh canonical validation and rebuilding; none of its hashes or publication assertions are production evidence. From the repository root, create independent and system examples in an existing external directory:

```python
import copy, json
from pathlib import Path

example = json.loads(Path("docs/examples/lifecycle-release-request.json").read_text())
output = Path("/absolute/external/example-plans")
output.mkdir(parents=True, exist_ok=True)
for scope in ("pi", "pico", "rp1", "system"):
    request = copy.deepcopy(example)
    request["scope"] = scope
    for name, component in request["components"].items():
        component["changed"] = scope == "system" or name == scope
        component["published"] = not component["changed"]
    with (output / (scope + ".json")).open("x") as stream:
        json.dump(request, stream, indent=2)
```

```text
wsprrypi-qualification lifecycle release-plan /absolute/external/example-plans/pi.json --output /absolute/external/example-plans/pi-plan.json
wsprrypi-qualification lifecycle release-plan /absolute/external/example-plans/pico.json --output /absolute/external/example-plans/pico-plan.json
wsprrypi-qualification lifecycle release-plan /absolute/external/example-plans/rp1.json --output /absolute/external/example-plans/rp1-plan.json
wsprrypi-qualification lifecycle release-plan /absolute/external/example-plans/system.json --output /absolute/external/example-plans/system-plan.json
```

The independent graphs select only their named component and retain fixed companions; the system graph orders Pico, RP1, then Pi. Before operational use, replace every fictional source, artifact, build dependency, workflow/job and public-state assertion with measured inputs and authenticated owning records, then recompute `build_inputs_sha256` with `lifecycle.digest` and each `ci_input_sha256` with `lifecycle_release.ci_input_key`. Set verified/successful keys only when exact retained evidence supports them. These examples have publication disabled and never execute a graph.

[`lifecycle-release-request.schema.json`](../../schemas/lifecycle-release-request.schema.json) requires exact source and dirty-state identities, tooling, test and embedded identities for all three components, selected version/tag, artifact bytes, board/profile/compiler/dependency/generator inputs, build-input digests, canonical workflow/jobs and CI input keys. `lifecycle_release.ci_input_key` binds the complete fixed source/artifact pair. Artifact reuse retains original build provenance; a changed build-input key requires rebuilding. Failed, missing, cancelled, skipped or incomplete canonical jobs do not count as reusable green evidence. Supplied CI evidence must come from authenticated canonical records; a local plan does not independently verify a URL's truth or trigger jobs.

For independent release, set scope to `pi`, `pico` or `rp1` and mark only that component changed. Fixed companions remain fully identified. System release orders changed Pico/RP1 dependencies before Pi, reuses unchanged suitable public artifacts, updates Pi's authoritative selections before its validation, and requires actual consumer/public acceptance. It never publishes all dependencies automatically. Source tag and embedded binary source remain separate fields so metadata-only tag changes cannot claim an old executable was rebuilt.

The graph is a reviewable selection artifact. Convert its phases to a lifecycle request using the owning canonical build/release tooling and explicit independent public assertions. A publishing request has `operation: release`, `publish` steps and explicit plan-time selection; execution requires both digest confirmation and `--enable-publication`. Non-publishing validation never passes either publishing flag:

```text
wsprrypi-qualification lifecycle plan SYSTEM-RELEASE-VALIDATION.json --output RELEASE-VALIDATION.json
wsprrypi-qualification lifecycle execute RELEASE-VALIDATION.json /durable/controller-state/release-validation-001 --confirm-plan-sha256 SHA256
```

The same forms support independent Pico/Pi/RP1 release validation requests. Exact supported canonical commands and asset promotion rules come from the selected owning source. No generic central job replaces Pico canonical verification, Pi's checked-in Debian recipes or RP1 native package acceptance. `tag_push_arguments` provides compare-and-swap tag movement only when movement and the exact prior remote object ID are explicitly selected. No command implicitly forces a tag. Publication is outside this implementation task's live scope.

## Interruption and recovery

```text
wsprrypi-qualification lifecycle resume PLAN.json /durable/controller-state/run-001 --inspect-step INSPECTION_ID --confirm-plan-sha256 SHA256
wsprrypi-qualification lifecycle recover RECOVERY-PLAN.json /durable/controller-state/recovery-001 --confirm-plan-sha256 RECOVERY_SHA256
wsprrypi-qualification lifecycle report /durable/controller-state/recovery-001
```

Every continuation performs a fresh named read-only inspection. An interrupted non-replayable effect is refused rather than repeated. Use a new explicit recovery plan invoking the owning product's supported transaction/recovery command and independently verifying final state. Retain the original unsuccessful run and its duties; a separate successful recovery does not rewrite its outcome. Cleanup phases are explicitly selected recovery-safe actions and run after an unsuccessful execution; do not put destructive baseline restoration in unconditional cleanup.

Published-component receipts survive later failures. Inspect actual public state and continue only unfinished owning operations after partial delivery. No automatic cross-repository rollback, tag movement, history deletion or republishing is promised. A final cleanup report must state observed devices/services/routes/packages, intentionally retained inputs/shared tools, unresolved duties and every unattempted required gate.

For example, after a system run selected Pi/RP1 removal and an installed idle Pico, run `lifecycle report /durable/controller-state/system-001` and inspect the final named observation records. A successful cleanup account states Pi services/listeners and RP1 package/DKMS/modules/routes absent; lists each protected backup and empty namespace with its verified identity; identifies the retained shared picotool and separate Pico transaction/bundle store; gives the Pico serial, firmware source, disabled output and idle state; and states zero missing checks and recovery duties. If any assertion or cleanup failed, retain that unsuccessful result and describe the separate supported recovery rather than rewriting the original report. A successful feature profile still lists default-public acceptance and publication as unexecuted when those gates were outside its selection.

## Explicit idle routes, retention and feature validation

The Pi bench may declare `provider_idle_route: gpio4` or `gpio20` when the owning startup contract reconciles that configured route after a controlled restart. Omission requires neutral provider state. The explicit policy also permits neutral state after installation; a configured route must match exactly, report disabled output and operational readiness, and prove closed endpoint, no owner/lease, and quiescent clock/DMA/GPIO. This does not select a route or authorize output. An active or unexpected route, missing evidence, or enabled output refuses.

`retained_paths` is a closed inventory of supported operator backup/fleet/log sidecars and the empty root WTP endpoint or pre-existing application-state namespace. Every file requires an exact SHA256, uid, gid, mode and reason established before execution. A retained directory requires exact ownership/mode and must be empty; adding any child blocks the audit. The scan traverses retained directories rather than excluding their contents. Provider state, package/module/code trees and arbitrary directories cannot be retained through this field. Both promised retention and scoped removal are verified; a pass never means every WsprryPi-named byte on the host disappeared. Configuring synthetic settings is still an edit under the production backup-protection policy; explain retained backups rather than manually deleting them.

For an unmerged installer correction, explicitly select its pushed source branch and select either `candidate_release_tag` or a verified `candidate_local`/`candidate_remote` pair in a separate bench file. The resulting `pi-streamed-eight` profile records `default_public_acceptance: false`; it runs the same mandatory lifecycle checks using that branch's actual streamed bytes. Default `devel` plans reject these artifact overrides so they continue to prove the actual default public selector. No automatic source fallback exists:

```text
wsprrypi-qualification lifecycle pi-public-plan FEATURE-BENCH.json --installer-branch codex/lifecycle-release --state-root /durable/controller-state --pi-source /absolute/Pi-feature-worktree --rp1-source /absolute/RP1-checkout --output FEATURE-REQUEST.json
wsprrypi-qualification lifecycle plan FEATURE-REQUEST.json --output FEATURE-PLAN.json
wsprrypi-qualification lifecycle execute FEATURE-PLAN.json /durable/controller-state/feature-001 --confirm-plan-sha256 SHA256
```

`system-public-plan` accepts the same explicit source-branch option and preserves the false default-public acceptance marker. For coordinated feature validation, pass the local-candidate bench to `system-public-plan FEATURE-BENCH.json PICO-BENCH.json --installer-branch codex/lifecycle-release` with the same source/output arguments shown above; stage both packaged USB adapters and bind the exact companion core, schema and firmware bundle in the Pico bench. A local binary keeps its original embedded source identity even when installer-only corrections advance the feature branch. The selected installed binary, installer tooling and shared Pico core remain separately identified. These tests can validate an unpublished correction without merging it, moving tags or publishing. They cannot close a required default-public acceptance gate until the necessary owning fixes are publicly selected; report that exact gate as unresolved.

`rolling_ini_backup: true` explicitly selects the owning installer's rolling `.pre_migration.bak` contract. The final audit requires that backup to match the verified baseline INI immediately before upgrade, using `--retained-from` and the producer's integrity-checked observation. Initial/first-removal checks still require the declared initial backup hash. A failed observation, wrong host/binary, altered payload or missing proof refuses; other retained files and directory contents remain unchanged expectations. The request must declare the backup before selecting this mode. Ordinary canonical settings comparisons ignore INI comments, including the generated version banner, while retaining every parsed setting.

Pi observations include a producer digest and operation identity; comparison and rolling-backup checks require a successful snapshot/verification producer. Full observations are also retained inline in command logs, including remote Pico/handoff observations. Used output names, redirected parents and missing/private evidence prerequisites refuse before host/device effects; a failed observation never becomes a successful comparison baseline. These hashes establish integrity within the trusted controller workflow, not author authentication.

An expected nonzero refusal with all required independent checks satisfied ends as `expected_refusal`. A scenario that additionally completes a successful owning `recover` phase and final checks may end as `pass`. Missing checks, ambiguous effects and cleanup failures retain their unsuccessful meanings in either case.
