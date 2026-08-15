# Phase 3 execution prompt: synthetic raw-IQ analyzer

## Objective

Implement only Phase 3 of `docs/cw-mode-gap-closure-contract.md`. Generate
deterministic CF32LE fixtures for `tone`, `cw`, `qrss`, `fskcw`, and `dfcw`,
then derive carrier state, frequency, contrast, continuity, transition timing,
tone spacing, event timing, symbols, repetitions, and reconstructed messages
from the IQ samples. Publish hash-bound generated-observation and mode-gate
documents. Every Phase 3 result is synthetic and non-qualifying.

## Governing constraints

Read and obey `CONTRACT.md`, `AGENTS.md`, `docs/AGENT_OPERATIONS.md`, the
gap-closure contract, and the Phase 1 and Phase 2 execution prompts. Preserve
historical sources and sibling repositories. Use Python 3.11-compatible,
portable code and structured paths; do not use shell, POSIX-only facilities,
hardware, SDR enumeration, services, remote hosts, GPIO, I2C, or RF.

The Phase 2 expected-event timeline is the planned answer used for comparison,
not an observation source. Measurements must come from authenticated CF32LE
bytes. Fixture mutation controls may create negative test inputs, but the
analyzer must never read fixture-generation metadata or accept caller-authored
observations as authoritative.

## Required implementation

1. Add a pure deterministic fixture generator whose output depends only on a
   validated resolved plan, its exact Phase 2 timeline, and an explicit seed.
   Generate interleaved little-endian float32 IQ with declared primary and
   secondary carriers, deterministic noise, quiet intervals, and bounded
   amplitude. Refuse overwrite and bind plan and expected-event artifacts.
2. Add an offline analyzer that authenticates the plan, expected events,
   capture metadata, sample count, byte length, SHA-256, synthetic marker, and
   zero-overflow contract before analysis.
3. Derive measurements from IQ using documented window/estimator parameters.
   Record time and frequency resolution plus uncertainty. Reject a plan when a
   frequency, spacing, transition, or timing threshold is tighter than the
   analyzer can resolve.
4. Measure every expected event, including RF-off intervals. Detect carrier
   presence, false silence, expected-frequency error, contrast, continuity,
   clipping, unexpected states, and transition/timing error. Reconstruct keyed
   symbols, repetitions, and messages independently from measured states and
   durations; do not copy the plan message into the result.
5. Derive `analysis_outcome`, `carrier_gate`, and `mode_gate` deterministically.
   Keep failure, fixture blockage, and inconclusive evidence distinct. Tone
   keeps `mode_gate: not_applicable`. Synthetic output always records
   `qualification_claim: false` and cannot emit an overall hardware result.
6. Write generated observations and the mode gate atomically as new files,
   refuse overwrite, and bind all upstream artifacts by resolved path, size,
   and SHA-256. Strengthen chain validation to reject forged analyzer identity,
   incomplete event coverage, contradictory gates, or synthetic positive
   qualification claims.
7. Provide hardware-free CLI commands for deterministic fixture generation and
   IQ analysis. CLI help must state the synthetic/non-qualifying boundary.
8. Add a development guide and update the roadmap/README status without
   claiming Phase 4 replay, lifecycle work, cross-platform CI completion, or
   hardware qualification.

## Required adversarial cases

Golden fixtures for all five modes must pass the measurement gates while
remaining non-qualifying. Focused tests must correctly reject or classify:

- wrong primary or secondary frequency and wrong tone spacing;
- swapped shifted-CW tones and conjugate/image-side signals;
- timing drift, displaced transitions, missing symbols, and truncation;
- interrupted carrier where continuity is required;
- RF present during an expected silent interval and an unexpected third state;
- clipping above the resolved threshold;
- short reads, non-CF32 byte length, non-finite samples, and overflow;
- capture, plan, or expected-event artifact tampering;
- thresholds tighter than recorded time/frequency resolution;
- output overwrite, stale bindings, manual observations, and any attempted
  synthetic qualification claim.

Tests must demonstrate that negative fixtures are detected from IQ alone and
that changing untrusted fixture labels cannot change analyzer conclusions.

## Verification

Run formatting, lint, strict typing, the complete pytest suite, package build,
hardware-disabled CMake build/CTest, schema source/package synchronization,
historical provenance verification, CLI smoke tests, and `git diff --check`.
Run no hardware-dependent test. Pushed macOS, Ubuntu, and native Windows CI is
the cross-platform exit confirmation; local success must be reported as local.

## Exit gate

Phase 3 is complete only when deterministic IQ bytes produce the complete
generated observations and reconstructed message for every mode; every listed
negative case fails closed or receives the contractually correct
failed/blocked/inconclusive classification; all evidence remains synthetic and
non-qualifying; every safe local gate passes; pushed cross-platform CI passes;
adversarial review has no unresolved material finding; and the working tree
contains only intended Phase 3 changes.

## Adversarial findings injected during execution

This section is append-only during execution. Each assessment records findings,
closures, and verification. Repeat assessment after every material repair until
no unresolved material finding remains.

### Assessment 1

1. The initial event output repeated expected event boundaries and symbols too
   directly, leaving the reconstruction claim insufficiently distinguished
   from timeline-shape validation. Closure: the analyzer now derives keyed
   symbols from measured active-state duration (and DFCW state), independently
   decodes the measured Morse groups into per-repetition messages, records them
   in a dedicated measurement summary, and keeps the summary explicitly
   non-qualifying.
2. Whole-event average contrast could hide an interrupted carrier. Closure:
   continuity is now evaluated across eight event subwindows; a low-power
   subwindow fails the required carrier-continuity observation, with a focused
   interruption test.
3. Image-side frequency behavior required a direct IQ-only adversarial check.
   Closure: a conjugated FSKCW capture with refreshed untrusted metadata is
   rejected for measured wrong frequency, demonstrating that fixture labels do
   not control the conclusion.

### Assessment 2

The repaired implementation was re-reviewed against the Phase 3 prompt,
artifact chain, synthetic/non-qualification boundary, estimator resolution,
overwrite behavior, clipping precedence, and IQ-only negative mutations. No
unresolved material finding remained in the authorized synthetic scope.
