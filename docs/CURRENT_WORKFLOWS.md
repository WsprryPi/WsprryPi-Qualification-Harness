# Current workflows

This page maps supported tasks to the checked-out production CLI and maintained
guides. Confirm command arguments with `COMMAND --help` at the current revision;
old run logs and implementation prompts may describe earlier interfaces.

## Orientation and plan review

Use `version`, `capabilities`, `validate-profile`,
`validate-application-plan`, and `real-session --plan-only` to establish the
current local capability and validate inputs without opening devices or
contacting another host.

Profiles are schema-valid only after both JSON Schema and maintained semantic
validation pass. Runtime authorization and current RF-path facts cannot be
supplied by a committed example.

## Hardware-free qualification rehearsal

Use `simulate-qualification` for a bounded end-to-end rehearsal with local
synthetic children. It exercises deadlines, carrier analysis, decoder
invocation, cleanup precedence, and immutable bundle creation. Its result is
always non-qualifying.

Guide: [Bounded simulator](development/bounded-simulator.md).

## Offline WSPR analysis

The maintained sequence is:

1. `validate-capture-metadata` authenticates exact-count capture metadata and
   its IQ artifact.
2. `analyze-carrier` compares distinct RF-off and RF-on captures.
3. `make-slot-wav` creates a canonical WAV for each bounded UTC slot.
4. `decode-wspr` runs the pinned decoder independently for each WAV.
5. `summarize-decodes` verifies consecutive expected-identity results.

Offline analysis does not prove transmitter lifecycle, cleanup, calibrated
power, or spectral compliance.

Guides: [Bounded carrier evidence](development/bounded-carrier-evidence.md) and
[split-host WSPR lifecycle](development/live-three-frame.md). Use each command's
current `--help` output for its exact offline arguments.

## Tone and CW-family evidence

The maintained hardware-free commands form an authenticated progression:

- `generate-cw-expected-events` creates the reference event timeline;
- `generate-cw-synthetic-iq` and `analyze-cw-synthetic-iq` exercise deterministic
  synthetic IQ;
- `compose-cw-acquired-replay` and `validate-cw-acquired-replay` authenticate
  and analyze already-acquired IQ;
- `run-cw-mock-lifecycle` and `validate-cw-mock-lifecycle` exercise bounded
  lifecycle and cleanup behavior without hardware; and
- `validate-cw-contract-chain` verifies the hash-bound plan, event,
  observation, gate, and session relationship.

Guides: [Reference encoders](development/cw-reference-encoders.md),
[synthetic-IQ analyzer](development/cw-synthetic-iq-analyzer.md),
[acquired-IQ replay](development/cw-acquired-iq-replay.md), and
[mock bounded lifecycle](development/cw-mock-bounded-lifecycle.md).

## Preserved archive intake

Use `inventory-archive` and `validate-cw-multi-capture` to authenticate
preserved artifacts and their declared relationships. These commands do not
turn independently acquired files into a coherent live capture or a hardware
qualification.

Guide: [Archive normalization](development/archive-normalization.md).

## SDR calibration profile evaluation

Use `evaluate-sdr-calibration` to validate and apply the frozen native
`sdr-calibration-profile` version `1.0.0` contract without opening a receiver.
The result is not automatically attached to recorded or live qualification.

Guide: [SDR calibration profile consumer](development/sdr-calibration-profile-consumer.md).

## Deployment and actual-host preflight

`validate-helper-deployment` validates local deployment configuration and
pinned artifacts without installing them. `run-cw-actual-host-preflight` is a
separately enabled, digest-confirmed, read-only SSH workflow with a sealed probe
set. A blocked or passing preflight remains non-qualifying and does not authorize
corrective action or RF.

Guides: [Helper deployment](development/helper-deployment.md) and
[actual-host preflight](development/cw-actual-host-preflight.md).

## Live qualification

The production live commands are deliberately fail-closed:

- `run-live-session` coordinates the bounded split-host carrier gate and
  three-frame WSPR lifecycle.
- `run-cw-live-tone` coordinates carrier-only tone cadence and stops before WSPR
  decoding.

Do not run either command without current authorization for the exact resolved
plan, host and device identities, RF path, level budget, stopping procedure, and
operator window. A prior run or authorization does not carry forward.

Guides: [Split-host WSPR lifecycle](development/live-three-frame.md) and
[bounded tone loopback mediator](development/bounded-tone-loopback-mediator.md).

## Evidence review

Validate retained bundles with the command specific to their evidence type and
apply the checklist in [AGENT_OPERATIONS.md](AGENT_OPERATIONS.md). Qualification
claims must remain exact to the recorded backend, band, hardware, source,
receiver path, settings, time, and cleanup outcome.
