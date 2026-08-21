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
2. `analyze-carrier` compares distinct RF-off and RF-on captures. Add
   `--plot OUTPUT.png` or `--plot OUTPUT.svg` for an authenticated relative-spectrum
   rendering using Matplotlib Agg.
3. `make-slot-wav` creates a canonical WAV for each bounded UTC slot.
4. `decode-wspr` runs the pinned decoder independently for each WAV.
5. `summarize-decodes` verifies consecutive expected-identity results.

Offline analysis does not prove transmitter lifecycle, cleanup, calibrated
power, or spectral compliance.

Carrier plots are normalized to the strongest positive RF-on-minus-RF-off
residual and therefore show relative dB only. Their metadata binds the plot
bytes, media type, pixel dimensions, renderer version, normalization contract,
and canonical source analysis. A plot is neither calibrated power nor frequency
evidence.

Guide: [Split-host WSPR lifecycle](development/live-three-frame.md). Use each command's
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

The [live keyed contracts](development/live-keyed-contracts.md) define and
validate resolved QRSS, FSKCW, and DFCW plans, exact runtime-authorization
bindings, three independent transaction records, aggregate sessions, derived
results, and artifact indexes. The public Python API also provides a sealed,
deterministic hardware-free coordinator rehearsal with boundary-specific failure
and cancellation injection. It does not start a process, open a receiver,
contact a host, operate a service, or enable RF.

`run-cw-live-keyed` is the separate production command. It accepts only QRSS,
FSKCW, or DFCW plans with exactly three requested transactions and requires
`--enable-live-keyed`, `--enable-rf`, `--operator`, and an exact
`--confirm-plan-sha256`. It stops after the first unsuccessful transaction and
still performs cleanup, service restoration, quiescence verification, provider
shutdown, and partial-bundle publication.
Its helper configurations are immutable plan inputs, not authorization
receipts: their artifact hashes are placed in the resolved plan before its
digest is computed. The exact digest is passed separately at helper startup and
is correlated on every request and response.

## External archive intake

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
- `run-cw-live-keyed` coordinates three independent QRSS, FSKCW, or DFCW
  process/capture/analyze transactions.

Do not run any production live command without current authorization for the exact resolved
plan, host and device identities, RF path, level budget, stopping procedure, and
operator window. A prior run or authorization does not carry forward.

Guides: [Split-host WSPR lifecycle](development/live-three-frame.md) and
[bounded tone loopback mediator](development/bounded-tone-loopback-mediator.md).

## Result review

Validate output bundles with the command specific to their document type and
apply the checklist in [AGENT_OPERATIONS.md](AGENT_OPERATIONS.md). Qualification
claims must remain exact to the recorded backend, band, hardware, source,
receiver path, settings, time, and cleanup outcome.

The harness does not keep these bundles in Git. Use a temporary or external
output directory, then move records selected for preservation to the target
project or another approved evidence store.
