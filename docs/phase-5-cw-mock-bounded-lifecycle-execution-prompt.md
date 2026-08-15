# Phase 5 execution prompt: mock bounded lifecycle

## Objective

Implement only Phase 5 of `docs/cw-mode-gap-closure-contract.md`. Bind one
authenticated `tone`, `cw`, `qrss`, `fskcw`, or `dfcw` plan and measurement
gate to the reviewed Slice 4 supervisor, and exercise start, capture/monitor,
stop, cancellation, timeout, cleanup, service restoration, leak verification,
and quiescence using only sealed mock or reviewed local-process operations.
Every result is a hardware-free lifecycle rehearsal and is non-qualifying.

## Governing constraints

Read and obey `CONTRACT.md`, `AGENTS.md`, `docs/AGENT_OPERATIONS.md`, the
gap-closure contract, the Phase 1 through Phase 4 prompts, and the reviewed
Slice 4 supervisor contract. Keep the portable core Python 3.11-compatible.
Do not access remote hosts, services, GPIO, I2C, DMA, PWM, GPCLK, an SDR, a
transmitter, or RF. Do not add a production adapter or relax the supervisor's
sealed-operation checks. Preserve historical files.

## Required implementation

1. Add a strict mock-lifecycle evidence contract binding the exact resolved
   plan, expected events, generated observations, and mode gate by canonical
   relative path, size, and SHA-256.
2. Run a fresh single-use Slice 4 supervisor for each rehearsal. Model receiver
   acquisition/start as capture preparation/start, transmitter start as the
   mode start, the bounded monitor as the complete capture interval, and
   cleanup as transmitter stop, receiver stop, releases, conditional service
   restoration, owned-handle leak verification, and backend quiescence.
3. Accept only a closed declarative injection vocabulary covering every setup,
   monitor, cancellation, cleanup, restoration, leak, and quiescence boundary.
   Do not accept executable paths, argv, callbacks, shell text, or adapter
   objects from the CLI.
4. Record mode, run identity, injection, upstream artifacts, the complete
   supervisor document, measurement gates, lifecycle gate, final status,
   failure causes, and `qualification_claim: false` in a new output file.
5. Derive status semantically. A successful mock lifecycle remains
   `inconclusive`; cancellation is `aborted`; setup/monitor failure is
   `fixture_blocked`; and any cleanup, leak, restoration, or quiescence failure
   is `cleanup_failed` even when measurement passed.
6. Validate by re-authenticating every upstream artifact, revalidating the
   Phase 1 chain and supervisor document, requiring exact derived fields, and
   rejecting path traversal, absolute paths, symlinks, aliases, tampering,
   mode/run mismatch, unsupported adapters, and positive qualification claims.
7. Provide hardware-free CLI commands to create and validate the evidence.
   Help and output must clearly say mock-only and non-qualifying.
8. Update the roadmap status, README, agent operations guide, and add a Phase 5
   development guide without claiming pushed CI, actual-host preflight, live
   lifecycle validation, or hardware qualification.

## Required adversarial cases

Exercise all five modes and inject receiver/transmitter acquire and start
failure/timeout, monitor failure/timeout, cancellation during every operational
phase, transmitter/receiver stop and release failure/timeout, service restore
failure/timeout, leak-check failure/timeout, and quiescence failure/timeout.
Prove partial ownership never cleans unowned resources, cleanup continues after
an earlier cleanup failure, no owned handle remains after ordinary failures,
and cleanup failure overrides passing carrier/mode measurements and aborts.

Reject modified upstream bytes or references, reordered or fabricated cleanup
evidence, inconsistent mode/run identity, changed derived causes/status,
absolute or escaping references, symlinks, output overwrite, unknown injection,
and any `qualified` or `qualification_claim: true` result.

## Verification

Run formatting, lint, strict typing, the complete pytest suite, package build,
hardware-disabled CMake build/CTest, schema source/package synchronization,
historical provenance verification, CLI create/validate smoke tests, and
`git diff --check`. Run no hardware-dependent test. Pushed macOS, Ubuntu, and
native Windows CI remains the cross-platform exit confirmation.

## Exit gate

Phase 5 is complete only when every lifecycle boundary has deterministic
failure injection, every owned operation is bounded, cleanup ordering and
precedence are semantically revalidated, passing measurement cannot override a
cleanup failure, all safe local gates pass, an adversarial review has no open
material finding, and the tree contains only intended Phase 5 changes.

## Adversarial findings injected during execution

This section is append-only. Repeat assessment after every material repair
until no unresolved material finding remains.

### Assessment 1

1. The lifecycle validator authenticated the four input files but did not
   revalidate their internal plan/event/observation/gate hash chain. Closure:
   authenticate every upstream binding before accepting lifecycle evidence and
   test upstream-chain tampering.
2. The retained supervisor document was structurally and semantically valid,
   but its `injection` label could be changed to another allowed value without
   proving the trace came from that injection. Closure: deterministically
   replay the sealed declarative injection during validation and require exact
   safety-semantic agreement after excluding wall-clock timestamps.

Both findings are release blockers. Repeat the independent assessment after
the closures are implemented and verified.

### Assessment 2 - Final

The repeated adversarial assessment rechecked upstream hash-chain tampering,
injection relabeling, supervisor-trace fabrication, cleanup ordering and
precedence, all declared failure/cancellation/timeout boundaries, path and
symlink rejection, output overwrite refusal, all five modes, and the
non-qualification boundary. The complete safe local validation suite passed.
No unresolved material finding remained in the Phase 5 scope.
