# Phase 4 execution prompt: acquired-IQ replay and evidence composition

## Objective

Implement only Phase 4 of `docs/cw-mode-gap-closure-contract.md`. Replay an
already retained, acquired CF32LE capture for `tone`, `cw`, `qrss`, `fskcw`, or
`dfcw` without opening hardware or creating RF activity. Authenticate the
capture and its declared acquisition contract, derive observations from IQ,
and compose one portable, deterministic, schema-valid, fully manifested
evidence bundle. A replay is measurement evidence only and must remain
`inconclusive` without authenticated lifecycle evidence.

## Governing constraints

Read and obey `CONTRACT.md`, `AGENTS.md`, `docs/AGENT_OPERATIONS.md`, the
gap-closure contract, and the Phase 1 through Phase 3 execution prompts. Keep
the portable core Python 3.11-compatible. Do not use shell, POSIX-only APIs,
remote hosts, services, GPIO, I2C, physical SDRs, transmitters, or RF. Preserve
the source capture and refuse every overwrite or reused bundle directory.

The acquired marker and capture settings are assertions to authenticate, not
proof of a live lifecycle. The Phase 2 timeline is the comparison reference,
not an observation source. Measurements and reconstructed messages must still
come from the authenticated IQ bytes through the reviewed Phase 3 analyzer.

## Required implementation

1. Define a strict acquired-capture metadata contract that binds the resolved
   plan, expected events, and IQ artifact and records exact format, sample
   count/rate, center frequency, acquired count, overflow count, fixed gain,
   AGC, bias tee, first-read discard, receiver identity, acquisition UTC, and
   `synthetic: false`.
2. Authenticate every metadata fact against the resolved plan and artifact
   before creating a replay bundle. Reject a short read, hash/size mismatch,
   non-CF32 bytes, overflow, non-finite IQ, receiver mismatch, or acquisition
   setting contradiction.
3. Create a new bundle transactionally. Copy the authenticated plan, expected
   events, capture metadata, and acquired IQ into canonical names with relative
   paths. Never alter or relabel the source artifacts. Clean up an incomplete
   bundle on failure without touching pre-existing paths.
4. Reuse the reviewed IQ measurement implementation for acquired bytes while
   emitting `synthetic: false`. Bind observations to the retained capture and
   record analyzer identity, resolution, complete per-event observations,
   reconstructed repetitions, and failure causes. Do not trust metadata labels
   for measured signal conclusions.
5. Compose the mode gate, an evidence index, `result.json`, and `SHA256SUMS`.
   The index must enumerate the exact required bundle files with size and
   SHA-256. The result must bind all upstream evidence, explicitly record that
   runtime authorization, live session, cleanup, and quiescence evidence are
   absent, emit `final_status: inconclusive`, and keep
   `qualification_claim: false` even when measurement gates pass.
6. Add semantic bundle validation that authenticates every reference and the
   canonical manifest, rejects missing or extra retained artifacts, rejects
   absolute or escaping bundle references, recomputes acquired observations
   from IQ, and rejects contradictory gates, results, indexes, or claims.
7. Ensure determinism: identical authenticated inputs and analyzer revision
   must produce byte-identical JSON and IQ artifacts plus identical manifests
   in differently named parent directories. Generated evidence must not embed
   output-root-specific absolute paths or wall-clock composition timestamps.
8. Provide hardware-free CLI commands to compose and validate a replay bundle.
   Help and output must state the acquired-replay/non-qualifying boundary.
9. Add a development guide and update the gap-closure roadmap, README, and
   agent operations guide without claiming Phase 5 lifecycle work,
   cross-platform CI completion, live validation, or hardware qualification.

## Required adversarial cases

Golden acquired replays for all five modes must pass their applicable
measurement gates while remaining inconclusive and non-qualifying. Tests must
also reject or correctly classify:

- metadata with `synthetic: true`, wrong evidence type, or forged acquired
  status;
- capture, plan, timeline, metadata, observation, gate, index, result, or
  manifest tampering;
- byte-length, sample-count/rate, center-frequency, overflow, gain, AGC, bias
  tee, first-read-discard, receiver-identity, or acquisition-UTC conflict;
- non-finite samples, clipping, truncation, false silence, wrong frequency,
  shifted-tone swap/image, timing or message failure, and incomplete events;
- source/output aliasing, existing output, path traversal, absolute bundle
  references, symlinks, missing files, unexpected files, and interrupted
  composition;
- a replay result changed to `qualified` or any lifecycle fact represented as
  verified without authenticated evidence; and
- non-deterministic bytes caused by parent path, directory name, file ordering,
  locale, platform separators, or wall-clock time.

Tests must demonstrate that changing untrusted metadata labels cannot change
IQ-derived conclusions and that a passing acquired measurement does not imply
a passing lifecycle or a hardware qualification.

## Verification

Run formatting, lint, strict typing, the complete pytest suite, package build,
hardware-disabled CMake build/CTest, schema source/package synchronization,
historical provenance verification, CLI composition/validation smoke tests,
and `git diff --check`. Run no hardware-dependent test. Pushed macOS, Ubuntu,
and native Windows CI remains the cross-platform exit confirmation; local
success must be described as local.

## Exit gate

Phase 4 is complete only when acquired CF32LE bytes deterministically produce a
portable immutable replay bundle; every retained artifact and reference is
authenticated; the bundle recomputes and schema-validates; the manifest is
complete and canonical; all replay results remain inconclusive and
non-qualifying without lifecycle evidence; all safe local gates and pushed CI
pass; an independent adversarial review has no unresolved material finding;
and the working tree contains only intended Phase 4 changes.

## Adversarial findings injected during execution

This section is append-only. Each independent assessment records findings,
closures, and verification. Repeat assessment after every material repair until
no unresolved material finding remains.

### Assessment 1

1. Acquisition UTC was only syntax-checked and could contradict the resolved
   plan. Closure required: bind the acquired timestamp exactly to the plan's
   resolved UTC and add a different-valid-UTC rejection test.
2. `result.failure_causes` could be rewritten and re-manifested without
   rejection. Closure required: require exact derivation from observation
   causes plus the absent-lifecycle cause and test semantic tampering.
3. The destination directory was published before final recomputing
   validation. Closure required: validate the temporary bundle before its one
   final rename and prove a final-validation failure leaves no destination.
4. The inherited analyzer copied expected event boundaries into measured
   timestamps and derived symbols from expected-window duration. Closure
   required: detect carrier/frequency state transitions from IQ, derive event
   boundaries and symbol durations from those transitions, enforce timing
   tolerance, and add shifted/stretched boundary tests.

These findings are release blockers. Repeat the independent assessment after
all closures are implemented and verified.

### Assessment 2

1. The repaired transition detector still allowed a dropout longer than
   `maximum_transition_s` when the broader timing tolerance permitted the
   shifted boundaries; eight averaged continuity windows could mask the local
   interruption. Closure: the analyzer now measures the longest detected
   RF-off run inside every active event and the measured gap between adjacent
   continuity-required active states, fails either over-limit condition as
   `carrier_interruption`, and marks the affected observations non-continuous.
   A focused FSKCW test keeps `timing_tolerance_s` looser than
   `maximum_transition_s` and inserts a 0.30-second boundary dropout.

Repeat independent assessment after this repair; Phase 4 is not complete while
any material finding remains.

### Assessment 3

1. The new adjacent-transition pass could overwrite clipping-blocked event
   outcomes with `failed`, violating fixture-blockage precedence. Closure: the
   transition-failure pass now stops when clipping has blocked the capture, and
   a combined clipped FSKCW plus over-limit dropout test requires the analysis
   and carrier gate to remain `blocked` with only the clipping cause.

Repeat independent assessment after this precedence repair.

### Assessment 4 - Final

The independent reviewer reproduced the repaired clipping-plus-dropout case as
`blocked` with only the clipping cause, reproduced the non-clipped over-limit
dropout as `carrier_interruption`, and rechecked UTC/result binding,
pre-publication validation, IQ-derived timing, canonical relative references,
determinism, and replay non-qualification. No unresolved material finding
remained in the Phase 4 scope.
