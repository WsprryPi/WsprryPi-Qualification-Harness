# Phase 2 execution prompt: independent tone and CW-family reference encoders

## Objective

Implement only Phase 2 of `docs/cw-mode-gap-closure-contract.md`. Add pure,
portable, deterministic reference encoders that transform a validated,
normalized `tone`, `cw`, `qrss`, `fskcw`, or `dfcw` resolved plan into the exact
hash-bound expected-event document established by Phase 1. Do not implement IQ
fixture generation, signal analysis, replay composition, lifecycle execution,
hardware access, or RF activity.

## Governing constraints

Read and obey `CONTRACT.md`, `AGENTS.md`, `docs/AGENT_OPERATIONS.md`, the
gap-closure contract, and the Phase 1 execution prompt. Preserve historical
evidence and sibling repositories. Keep `tone` distinct from keyed CW. Treat the
transmitter execution trace only as a reviewed protocol source: it must never
supply expected answers at run time. Use only Python 3.11 standard-library APIs
in the encoder and retain native macOS, Linux, and Windows behavior.

## Reviewed protocol definitions

Support only explicit versioned definitions; fail closed on every other name or
version:

- `wspq-tone@v1`: the plan's declared leading/off, on, alternating off/on
  cycles, and trailing/off intervals;
- `wspq-cw@v1` and `wspq-qrss@v1`: International Morse characters encoded as
  dot = 1 unit, dash = 3 units, with plan-declared positive intra-element,
  inter-character, and inter-word gap units;
- `wspq-fskcw@v1`: the same Morse timing, using primary as mark for elements and
  secondary as space for gaps, with RF continuous across every internal event;
- `wsprrypi-dfcw@v1`: the reviewed WsprryPi definition at source revision
  `854b39d37433c5b98d4ed43784f0b9819cf6143e`: dot and dash each last one
  declared dot duration at their respective primary/secondary frequencies;
  internal gaps are RF-off; and gap multipliers are exactly `0.333333`, `1`,
  and `3` dot units.

The supported character repertoire must exactly match the reviewed WsprryPi
Morse table: ASCII letters A-Z (case-insensitive), digits 0-9, and `/ ? . , - +
=`. Whitespace separates words; leading/trailing or repeated whitespace is
normalized without producing empty words. Reject non-ASCII lookalikes,
unsupported punctuation, and messages that contain no encodable character.

## Required implementation

1. Extend the normalized plan contract with explicit pre/post quiet durations
   and keyed-mode gap multipliers. Tone must use null gap fields; keyed modes
   must use finite positive values. Bind DFCW v1 to its exact reviewed values.
2. Extend expected events with repetition number and source-message character
   position so every generated event is traceable to the normalized plan.
3. Implement one pure encoder entry point accepting an already validated plan
   object and returning ordered event objects without reading files, clocks,
   environment variables, Git state, devices, or the network.
4. Emit leading and trailing quiet events. Generate every declared repetition,
   preserve exact state/frequency/timing semantics, and add an inter-word gap
   between repetitions so they cannot merge into one message.
5. Use decimal plan values for deterministic time accumulation and emit finite
   JSON numbers. Produce contiguous zero-based indexes, positive durations,
   non-overlap, and a timeline that fits the capture contract.
6. Add a hardware-free CLI command that validates a plan and writes a new
   expected-event document atomically. It must refuse overwrite, bind the exact
   plan path/size/SHA-256, and record explicit generator version/source revision.
7. Strengthen semantic chain validation so caller-authored timelines cannot
   pass merely because their shape is plausible: independently regenerate the
   expected events from the bound plan and require exact equality.
8. Add focused golden and adversarial tests for all five modes, punctuation,
   case and whitespace normalization, repetitions, frequency/state continuity,
   DFCW compressed timing, unknown versions, unsupported input, mutation,
   capture bounds, output conflicts, and CLI artifact binding.
9. Update the roadmap status, README, and development documentation without
   claiming Phase 3 analysis or any hardware qualification.

## Required fail-closed behavior

Reject invalid/non-finite timing, zero or negative gaps, wrong mode/definition
pairing, DFCW parameter drift, reversed/equal FSKCW tones, unsupported or empty
messages, unknown protocol versions, an event timeline exceeding capture time,
wrong event indexes or message positions, wrong roles/states/frequencies,
altered continuity flags, rounded/recomputed timing changes, stale plan hashes,
and overwrite attempts. Generated software evidence remains non-qualifying.

## Verification

Run formatting, lint, strict typing, the complete pytest suite, package build,
hardware-disabled CMake build/CTest, schema byte synchronization, preserved
historical provenance verification, CLI smoke tests, and `git diff --check`.
Run no hardware-dependent test. The cross-platform exit claim is based on the
existing macOS/Ubuntu/native-Windows CI matrix; local validation alone must be
reported as local until pushed CI completes.

## Exit gate

Phase 2 is complete only when golden and adversarial tests prove exact symbols,
states, frequencies, timing, repetitions, and bindings; semantic validation
regenerates rather than trusts the supplied timeline; every safe local gate
passes; pushed macOS, Ubuntu, and native Windows CI passes; adversarial review
has no unresolved material finding; and the working tree contains only intended
Phase 2 changes.

## Adversarial findings injected during execution

This section is append-only during execution. Each assessment must state its
finding, closure, and verification. Repeat assessment after every material
repair until no unresolved material finding remains.

### Assessment 1

1. The standalone generator relied on later chain validation for FSKCW tone
   ordering, so a schema-valid plan could publish primary/secondary frequencies
   that contradicted `wspq-fskcw@v1`. Closure: the pure encoder now rejects mark
   at or below space and rejects equal DFCW tones; focused adversarial tests call
   the encoder directly.
2. Python's Unicode `isspace()` accepted separators that the reviewed C++ ASCII
   repertoire does not. Closure: the encoder rejects every non-ASCII code point
   before recognizing only the six ASCII whitespace characters; non-breaking
   space and non-ASCII letter tests fail closed.
3. The inherited Phase 1 role vocabulary called the between-element interval
   `intra_character_gap`, which could cause a later analyzer to compare the
   wrong timing class. Closure: Phase 2 corrects the schema, packaged copy,
   semantic validator, generator, and tests to `intra_element_gap` before any
   production expected-event artifact exists.
4. Inter-word gap events initially pointed at the preceding character rather
   than the first separator position used by the reviewed WsprryPi compiler.
   Closure: the reference encoder now records the first separator position and
   retains null position only for the synthetic gap between repetitions.

All Assessment 1 repairs must pass focused golden/adversarial tests and the
complete safe validation suite before the next assessment.

### Assessment 2

The repaired implementation was re-reviewed against every prompt requirement,
the Phase 1 hash-chain contract, the reviewed WsprryPi Morse/QRSS/FSKCW/DFCW
compiler behavior, cross-platform path/process constraints, schema/package
synchronization, non-qualification boundaries, and overwrite/capture-bound
failure behavior. No unresolved material finding remained.

Verification after the repairs passed formatting, lint, strict typing, 716
pytest tests, source/package schema equality, preserved historical provenance,
package sdist/wheel build, hardware-disabled native build and both CTest tests,
CLI discovery, and diff hygiene. The isolated package build could not download
its already-installed build backend inside the network-restricted sandbox, so
the local package build used `--no-isolation`; pushed CI retains the isolated
install/build gate on macOS, Ubuntu, and native Windows.
