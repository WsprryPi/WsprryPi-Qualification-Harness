# TODO: Pico harness governance gate — 90-minute cap

**Status: complete for the bounded software gate; no hardware qualification claimed.**

The bounded implementation and its coverage limits are documented in [Pico software governance gate](pico-governance-gate.md). The execution prompt below is retained for scope and milestone history.

## Execution prompt

You are working in:

/Users/lbussy/GitHub/WsprryPi-Qualification-Harness

This prompt is for a separately invoked implementation task. Printing or saving it does not execute it.

## Objective

Establish a small, repeatable, hardware-free gate proving that the harness can govern a test:

1. Submit one complete bounded job.
2. Establish receiver readiness before ARM.
3. Collect the corresponding output.
4. Evaluate it using production analysis.
5. Verify cleanup.
6. Publish retained evidence that can be validated independently.

Firmware transmission correctness belongs to firmware testing. This task evaluates harness coordination and result handling.

## Scope

Use one representative Pico configuration.

Exercise:

- One short keyed job through the production keyed measurement path.
- One WSPR job through the distinct audio-conversion and decoder path, retaining its existing carrier prerequisite.
- Three focused failure scenarios:
  1. Receiver unavailable: ARM must not occur.
  2. Cancellation: owned work must terminate within a bound, with accurate cleanup evidence.
  3. Incomplete or contradictory evidence: the gate must not pass.

Do not run three observations per protocol, all five modes, or the six-configuration matrix.

Exercise the production transaction path below the campaign repetition policy. Do not weaken the existing live campaign’s observation counts or qualification requirements to accommodate this software gate. A passing software gate is not a live qualification result.

## Authorization and boundaries

When explicitly invoked, this prompt authorizes scoped harness implementation, offline builds and tests, one adversarial review with bounded corrections, and safe commit/push delivery.

It does not authorize:

- Bench-host access.
- Physical USB or SDR access.
- RF or transmitter operation.
- Service changes, installation, or firmware replacement.
- Target-repository changes.
- Release publication, merging, or tag movement.
- CI dispatch or paid CI execution.

Treat WsprryPico and other target repositories as read-only source inputs.

Follow the repository instructions, starting with CONTRACT.md, docs/AGENT_OPERATIONS.md, docs/CURRENT_WORKFLOWS.md, and docs/CAPABILITY_MATRIX.md. Read the relevant production modules, schemas, tests, and CLI help.

Preserve unrelated work. Use an isolated codex/ branch or worktree when appropriate.

## Time budget

The hard cap is 90 minutes of elapsed wall time, including orientation, implementation, builds, tests, review, delivery, and reporting. Start the clock immediately upon invocation.

This is a maximum, not a target. Finish earlier when the objective is met.

Milestone deadlines below are cumulative. Do not silently borrow time from later milestones.

If a milestone fails its exit criteria:

- Stop further implementation.
- Report the missed criterion, concrete obstacle, work completed, and remaining work promptly.
- Do not consume the remaining allowance trying alternative designs.
- Use at most five additional minutes for safe cleanup and reporting, without exceeding the overall 90-minute cap.

A smaller scope, different architecture, extra cycle, or extension requires a new user decision. Elapsed time and silence do not constitute approval.

## Communication

Provide concise progress updates at least every five minutes and at every milestone.

Each milestone report must state:

- Elapsed time.
- The exit criterion and whether it was met.
- Concrete evidence obtained.
- Remaining uncertainty.
- The next bounded action.

Code written, tests collected, a successful build, or a mocked success document do not satisfy an end-to-end milestone.

## Milestone 1 — Feasible route established by minute 10

Inspect current source and identify the shortest route through the existing production components.

Confirm:

- The actual coordinator/observation entrypoint to exercise.
- The production WTP client and endpoint/JobService source pair.
- Available source, compiler, decoder, and capture-fixture prerequisites.
- The physical boundaries that will be substituted.
- How simulated time reaches scheduling and timeout decisions.
- How retained observation evidence will be validated without requiring a full campaign.
- How software fixture output remains nonqualifying.

Reuse existing fixtures and production mechanisms. Do not start by designing a new framework.

Exit criterion:

A concrete component map and a viable implementation route using available local prerequisites. Identify the small set of expected edits.

Stop at this milestone if a prerequisite is unavailable or success requires target-project changes, a broad redesign, or authority outside this prompt.

## Milestone 2 — Connected keyed transaction demonstrated by minute 30

Run one short keyed transaction through the real production observation/coordinator logic.

Keep real:

- Job composition and production encoding.
- WTP client, framing, and transaction control.
- Production endpoint and JobService.
- Measurement processing and evidence writers.
- Cleanup handling.
- Applicable retained-evidence semantic validation.

Substitute only the necessary physical transport/device/receiver boundaries and time. Declare each substitution.

Use a recording or inhibited RF fixture whose signal output follows accepted job execution. A missing ARM or cancelled job must not still receive an ideal passing signal merely because the plan requested one.

Do not fabricate passing analysis, replace the coordinator, stub the validator, or substitute a cooperative fake WTP peer for the required production endpoint.

Exit criterion:

One bounded keyed job has completed through the connected production path, produced actual analysis and cleanup evidence, and passed applicable retained-evidence validation. Its software result makes no hardware qualification claim.

Report the result and evidence location immediately.

If this is not demonstrated by minute 30, stop. Do not spend the remaining hour building more infrastructure.

## Milestone 3 — WSPR processing path demonstrated by minute 45

Extend the same gate to one WSPR job, retaining the existing carrier prerequisite.

Use the actual audio-conversion and decoder path. Preserve the represented WSPR timing and complete waveform data, but do not wait through a physical UTC slot or transmission window.

Use an independent known identity/vector or other maintained oracle to check the result. Do not derive both the expected answer and simulated measurement from an unchecked shared assumption.

Exit criterion:

One WSPR observation has traversed the connected control, collection, conversion, decoding, cleanup, and retained-evidence validation path. The expected identity is correctly evaluated.

Report this separately from the keyed result.

Stop if the WSPR path cannot meet this criterion by minute 45.

## Milestone 4 — Governance failures demonstrated; code frozen by minute 60

Exercise the three required failure scenarios through the same connected path:

1. Receiver unavailable
   - Demonstrate that no ARM request is sent.
   - Retain the receiver/preparation cause.
   - Release any ownership already acquired.

2. Cancellation
   - Cancel owned work at a meaningful lifecycle boundary.
   - Demonstrate bounded termination.
   - Distinguish transmitter release, receiver process exit, and required native cleanup evidence.
   - Missing cleanup proof must remain unsuccessful.
   - Test-owned workers must be released even when an assertion fails.

3. Invalid evidence
   - Remove or contradict required retained evidence.
   - Demonstrate rejection by the applicable public or maintained semantic validation path.
   - Include a contradiction with updated hashes so the check proves more than file-integrity verification.

Correct only harness defects necessary to make these successful and unsuccessful paths trustworthy.

Do not expand into unrelated validator hardening, Pi policy changes, a configuration matrix, caching, broad performance optimization, or firmware qualification.

Exit criterion:

Both successful paths and all three failure scenarios produce the required outcomes. The gate has a reproducible command and explicit coverage limits.

Freeze feature work at minute 60. Unfinished scenarios mean the task is incomplete.

## Timing requirements

Scheduling, readiness transitions, lease progression, waveform duration, and timeout scenarios must use simulated time.

Advance clocks and completion events rather than sleeping through production-length windows.

Keep real-time waiting only for short tests of actual process supervision. Bound each new supervision test to ten seconds and their aggregate to sixty seconds. These are upper limits, not required waits.

Simulated time must not replace verification that a real child process can be cancelled and reaped.

Keep signal duration distinct from execution duration: a fixture can represent a complete waveform without waiting for that waveform to elapse.

## Milestone 5 — Validation and adversarial review complete by minute 80

Run directly affected checks during development. After the minute-60 freeze, complete the repository-required checks appropriate to the actual change.

If shared behavior is changed, include focused Pi regressions. Do not change Pi policy merely because the assessment identified a separate inconsistency.

For cross-cutting changes or unavailable CI, complete the repository’s required local acceptance set. Do not omit required checks to fit the clock or repeatedly run broad checks without a concrete reason.

Perform one adversarial review:

- Did any required production component get bypassed?
- Does the signal fixture follow actual accepted execution?
- Can the gate pass without ARM, complete collection, or required cleanup?
- Can producer and validator share the same wrong assumption?
- Can updated hashes conceal contradictory evidence?
- Can fixture artifacts claim live qualification?
- Can inherited configuration or a child process reach physical transports?
- Can failed assertions leave workers running?
- Are scheduling tests actually using simulated time?

Allow one correction pass within this milestone. Rerun checks affected by corrections. Do not begin another repair cycle.

Close findings only with evidence. Unresolved blockers or incomplete required checks mean incomplete acceptance.

Exit criterion:

Required checks pass, both successful paths and all three failure scenarios remain demonstrated, and the adversarial review finds no unresolved blocker within this narrow scope.

Report actual runtimes. A proposed runtime is not a measured result.

## Isolation and evidence requirements

Establish fixture isolation before test collection or subprocess creation.

Control inherited configuration, hardware flags, plugins, executable selection, working directories, and child environments. Use non-hardware native fixtures and deny physical transport construction.

Describe the actual isolation boundary honestly. Do not claim application-level controls protect against arbitrary malicious native code.

Use temporary output outside source control. Preserve original campaign evidence.

Software fixtures must remain explicitly nonqualifying at the final acceptance boundary, not merely through a comment in the test report.

Validate retained output in a fresh process using only retained artifacts. Use the maintained semantic checks; do not duplicate a weaker validator inside the test.

When publication fails, produce valid unsuccessful evidence where possible. If evidence cannot be published, report that explicitly rather than claiming a valid bundle exists.

## Milestone 6 — Safe delivery and final report by minute 90

Review the final diff for scope, correctness, unrelated changes, private data, and generated output.

Commit and push scoped harness changes on the isolated codex/ branch, with no CI. Inspect current push triggers and use the repository-supported skip mechanism where needed. Do not disable checks or alter workflow triggers to avoid CI.

Do not push a known production-default regression. Incomplete work may be delivered only when isolated from default behavior and minimum delivery checks pass. Otherwise preserve it locally and report the blocker.

Verify the pushed commit identity. Do not merge.

## Final report

Lead with one of:

- Gate demonstrated.
- Incomplete — followed by the specific unmet criterion.

Report:

- What the keyed and WSPR paths actually exercised.
- Every substituted boundary.
- Outcomes of the three failure scenarios.
- Harness defects corrected.
- Adversarial findings and closure evidence.
- Tests run, failures, skips, and unavailable checks.
- Measured gate and supervision-test runtimes.
- Milestone completion times and total elapsed time.
- What the gate proves about harness governance.
- What remains outside scope, including firmware and physical qualification.
- Branch, commit, and push verification, or exact delivery blocker.
- Any uncommitted work.
- Boundary-use accounting for host/device/RF access, Git network operations, and CI.

Do not describe this gate as comprehensive firmware, RF, platform, or configuration qualification.

The intended result is modest and concrete: evidence that the harness can govern a bounded test and report its outcome correctly, without another broad campaign or an open-ended hardening exercise.
