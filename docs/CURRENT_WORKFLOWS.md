# Current workflows

This page maps supported tasks to the checked-out production CLI and maintained
guides. Confirm command arguments with `COMMAND --help` at the current revision.

## Orientation and plan review

Use `version`, `capabilities`, `validate-profile`,
`validate-application-plan`, and `real-session --plan-only` to establish the
current local capabilities and validate inputs without opening devices or
contacting another host.

Profiles are schema-valid only after both JSON Schema and maintained semantic
validation pass. Runtime authorization and current RF-path facts cannot be
supplied by a committed example.

Receiver plans must keep the complete requested-carrier acquisition window
outside the zero-IF DC exclusion and inside the usable sample-rate/bandwidth
span. The complete-test composer supplies the maintained 25-kHz offset for all
five modes. Expert-authored plans with contradictory tuning geometry are
rejected before production adapters can operate.

## Turnkey campaign orchestration

Use `turnkey-campaign plan`, `validate`, and `rehearse` for one typed workflow
covering Tone, WSPR, QRSS, FSKCW, and DFCW. Planning and rehearsal are
hardware-free and construct no production adapters. `execute` requires exact
digest confirmation and routes only through the existing real-session or
live-keyed production coordinator. That coordinator remains authoritative for
runtime safety, cleanup, evidence, and status.

Guide: [Turnkey campaign orchestration](development/turnkey-campaign.md).

### Simple complete selected-mode campaign

The normal live form is:

```text
wsprrypi-qualification complete-test TRANSMITTER_HOST RECEIVER_HOST \
  --sdr driver=sdrplay,serial=2404058C60 --enable-rf
wsprrypi-qualification complete-test TRANSMITTER_HOST RECEIVER_HOST \
  --sdr driver=sdrplay,serial=2404058C60 --mode TONE --enable-rf
```

Omitting `--mode` runs all five modes. Repeat `--mode` to select one or more;
execution is normalized to TONE, WSPR, QRSS, FSKCW, DFCW order and only selected
plans are materialized. Duplicate modes fail before host access.

Automatic composition uses the GPIO backend by default. Select the maintained
Si5351 production path explicitly with `--transmitter-backend si5351`; this
binds bus 1, address `0x60`, reference frequency 27 MHz, output CLK0, drive
strength 1, and the deployed read-only Si5351 quiescence inspector into every
subordinate plan. No backend fallback is permitted.

RP1 development campaigns use `--transmitter-backend rp1_gpclk` with the
required public route selector `--transmit-gpio 4|20` (the older
`--rp1-route gpio4|gpio20` spelling remains accepted). `--gpio-manual-ppm PPM`
binds the measured fixed host source independently from the residual Harness
`--transmitter-ppm-offset`. Hardware-free `--rehearse --configuration CONFIG`
composes five route-bound plans with distinct same-host transmitter and receiver
roles, ABI-v4 finite TONE, exact PPM provenance, and receiver/RF-path identity.
It constructs no production adapter, makes no external call, and is not
deployment, target, GPIO, SDR, or qualification evidence.

The maintained live RP1 form accepts either automatic exact-source deployment or
the installed WsprryPi binary/configuration development path and requires an
identical transmitter/receiver host. The installed path copies and authenticates
the installed runtime bytes and builds only the passive RP1 administrative probe
from a disposable clone of `/home/pi/WsprryPi`; it does not rebuild WsprryPi.
That path is rapid-development evidence and does not prove that the installed
binary was produced by the observed checkout. The receiver delegation enters
that host once through the configured controller SSH alias. Inside the host, distinct,
digest-bound local transmitter and receiver helper channels are used; self-SSH
and agent forwarding are not part of the topology. The transmitter helper
retains a schema-validated passive ABI-v4-capable snapshot before RF and after cleanup.
The maintained `rp1_contracts` validators additionally define the strict input
boundary for future passive preflight and operation-scoped lifecycle evidence.
They validate review-facing schemas, exact route identity, authenticated
process/lease/generation state, terminal timing, cancellation, operation-live-gate
capability, endpoint closure, cleanup and GPIO/clock/DMA quiescence. A complete cleanup failure is
valid unsuccessful evidence only when classified `cleanup_failed`; receiver
blockage after authenticated launch remains `fixture_blocked`. The passive
collector never acquires the endpoint or authorizes RF.

The versioned helper protocol now reserves one fixed `rp1-inspect` request with
an allowlisted route, `read_only=true`, and `acquire_endpoint=false`. The
same-host collector binds that response to the exact plan, transmitter helper,
helper binary, role configuration, and a separate receiver channel. It forbids
RF authorization, agent forwarding, and self-SSH. Live RP1 `complete-test`
extends this passive prerequisite with the selected deployment path and a separately
authenticated, operation-scoped authorization for each bounded transmission.

Every invocation creates an exclusive JSON Lines progress log and prints its
absolute path to stderr before long-running work begins. By default it is kept
in the invoking host's durable user-state directory, not temporary or remote
deployment storage, so stage cleanup cannot remove it before review. Use
`--progress-log PATH` to select another durable location explicitly. Records are
flushed individually and carry the timestamp, campaign, mode, stage, status,
and optional frame or observation number. The viewer places a normalized
second-resolution UTC timestamp immediately after every status glyph. Each WSPR
frame changes from started to completed at its own RF-window boundary, and each
per-frame WAV generation and decode operation reports both started and
completed states; stdout
remains reserved for the final structured result. Delegated receiver execution
forwards protocol records into the invoking controller's local log, so the same
tailing interface works from either endpoint or a third system.

The default path packages the current harness, copies the transmitter's
`/usr/local/bin/wsprrypi` and `/usr/local/etc/wsprrypi.ini` into an independently
owned durable per-campaign deployment, stages only the required runtime on both
hosts, and removes both temporary stages. It does not compile WsprryPi. Missing
installed inputs fail preflight and never cause an implicit source build. Use
`--wsprrypi-binary REMOTE_PATH` and `--wsprrypi-config REMOTE_PATH` for a
nonstandard installation. Use `--wsprrypi-source LOCAL_CHECKOUT` to opt in to
packaging that exact checkout and compiling its `rpi-gpio` backend on the
transmitter. Generated mode
plans, expected events, resolved profiles, and dispatch wrapper inputs are placed separately under
`OUTPUT_PARENT/complete-test-inputs/CAMPAIGN_ID`; they are neither deployment
scratch nor result-bundle contents. The resolved campaign retains that store
while its aggregate or subordinate results exist, and only an explicit manual
retention action may remove it. `--enable-rf` authorizes the bounded RF run; it
does not manufacture a physical-path assertion. Physical path, equipment,
software, firmware, module, overlay, clock, route, revision, and hash facts are
recorded when observable and may remain unknown. They are descriptive evidence,
not provenance whitelists. Runtime preflight still rejects incompatible ABI or
capability and unsafe, busy, unbounded, or unclean state. The receiver uniquely resolves the
selected SDR through SoapySDR, validates every selected subordinate plan, then
routes selected modes in canonical order. Both named hosts may be
remote to the controller; execution is delegated to the receiver host.
`--rehearse` is deterministic and hardware-free and conflicts with
`--enable-rf`. RP1 same-host production uses distinct authenticated local role channels.
Generic same-host local transport remains unsupported and fails before
production adapter construction.

The composed WSPR outer deadline follows the final three-slot schedule. It
contains receiver setup derived from the configured read bound, the exact wait
to coherent capture, capture duration, per-frame analysis, summary validation,
publication, cleanup, and final quiescence. Offline bounds scale with exact CF32
bytes and required validation/copy passes under the maintained minimum
sequential-I/O capability; there is no fixed summary allowance or generic
reserve. Production rechecks the same timing envelope from its actual start.

The complete timer classification and formulas are maintained in
[`development/timing-contracts.md`](development/timing-contracts.md).

Target checkouts are immutable provenance inputs. The automatic source-build
path copies the tracked WsprryPi INI byte-for-byte into the dedicated deployment
runtime before composing the final plan; only that external staged copy may be
passed through `-i`. The plan separately binds the source and runtime files,
the external child working directory, and every protected Git root. The helper
rechecks those facts immediately before spawn. It does not snapshot or compare
the working tree after stop; unrelated checkout changes do not make cleanup
fail. The Harness never resets or restores a checkout.

`complete-test` resolves transmitter PPM once before child-plan composition. A
fresh `tracked_host_ppm` absolute value supersedes `manual_host_ppm`, which in
turn supersedes `backend_native_ppm`; two sources at the winning precedence are
ambiguous and fail. `--transmitter-ppm-offset PPM` is then added once as a
harness residual delta (default `0`). Values use the selected WsprryPi backend's
sign convention and must be finite and keep the effective result within
plus/minus 200 ppm. Tracked values require an acquisition time and maximum age;
stale or host/backend-mismatched sources fail before host or RF access. The
resolved plan, generated profiles, backend argument plans, and aggregate result
bind the same provenance and effective value. Receiver calibration remains a
separate receiver-frequency interpretation and never contributes to this sum.

`--carrier-offset-max-hz HZ` controls the carrier gate (default `100`; finite,
non-negative; zero is valid). The gate compares the absolute requested-frequency
offset of the strongest acquired transmitter-added frequency with this inclusive
threshold. For example, `--carrier-offset-max-hz 250` permanently selects the
previously demonstrated 250 Hz policy without editing source. The selected value
is emitted as `carrier_offset_max_hz` in generated test profiles and reaches
`CarrierAnalysisParameters.offset_gate_hz` through the resolved real-session
plan. It is analysis tolerance, not transmitter or receiver calibration.

`--requested-transmit-frequency-offset-hz HZ` intentionally shifts the nominal
`--frequency-hz` exactly once for TONE, WSPR, QRSS, FSKCW, and DFCW. The
aggregate retains nominal, offset, and effective frequencies; every child plan,
receiver center, protocol request, and derived keyed frequency uses the
effective value. This setting is not transmitter PPM, receiver calibration,
measured residual error, or analysis tolerance.

`--frequency-acquisition-half-width-hz HZ` selects the positive symmetric
receiver/analyzer search half-width for the complete campaign (default `1000`).
It is authenticated in each keyed session and reference plan, used by live and
replay analysis, retained in shifted and unshifted frequency-model evidence,
and used for receiver tuning geometry. It is distinct from
`--carrier-offset-max-hz`, which remains the carrier pass/fail criterion.

## Offline WSPR analysis

Carrier policy version 3 adds independent temporal contrast and candidate
ambiguity checks. CW analyzer version 8 separately measures carrier edges and
raw-IQ quiet contamination. See [Noise robustness](development/noise-robustness.md)
for mode routing, historical evidence compatibility, and validation limits.

The maintained sequence is:

1. `validate-capture-metadata` authenticates exact-count capture metadata and
   its IQ artifact.
2. `analyze-carrier` compares distinct RF-off and RF-on captures and selects
   the carrier inside the requested target window; stronger span-wide features
   remain diagnostic. Add
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

Generic QRSS, FSKCW, and DFCW fixtures use the documented `ET` / `0.7`-second
canonical hardware-free scenario, with a `5.0` Hz separation only for FSKCW and
DFCW. Resolved inputs remain explicit; this convention supplies no live-plan
frequency, hardware identity, RF path, authorization, deadline, cleanup, or
quiescence fact.

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
For Raspberry Pi transmission, the plan and transmitter helper configuration
also bind the process privilege wrapper independently. The helper authenticates
it on every start and supplies the fixed noninteractive `sudo -n --` prefix;
the application plan continues to contain only the exact WsprryPi executable
and arguments.

Each of the three transactions sends one keyed message. Plans bind receiver
services that must run for capture separately from the complete service
allowlist; they start only after cleanup installation, and all listed services
return to their observed initial state during transaction cleanup.
The production adapter first authenticates and owns a prepared WsprryPi process
without scheduling or launching it, establishes the exact-count capture, and
only then sends a separate arm event for a future UTC start. The transmitter helper converts the
accepted wall-clock interval to a local monotonic deadline, so SSH latency does
not select the RF start instant. The coordinator monitors capture through the
resolved RF-off preamble and cancels the armed process before RF if capture
fails. Each transaction retains the accepted schedule, actual launch, schedule
error, receiver capture start, and derived capture-relative start. Capture setup
failure therefore blocks RF rather than producing a knowingly truncated
observation.
The resolved exact-count capture is calculated from the final generated keyed
timeline plus a one-second guard and rounded upward to a whole sample. Scheduled
quiet-time rebasing preserves that bound, and production preflight rejects an
older or hand-authored plan that omits the guard.
If capture fails after launch, the transaction is classified as receiver or
fixture blockage. Its partial bundle retains authenticated native failure
metadata and the bounded helper execution diagnostic, while incomplete or
semantically rejected IQ is removed and is not advertised as capture evidence.
The receiver host must have strict, plan-bound SSH access to the transmitter.
Service elevation, when required, uses a static-configuration-bound executable
such as `/usr/bin/sudo` in non-interactive mode; both it and `systemctl` are
hash-checked before each allowlisted operation.

Before operating a split-host campaign, establish the execution-host-to-both-
endpoints and receiver-to-transmitter trust paths described in
[`OPERATOR_SECURITY.md`](OPERATOR_SECURITY.md). Private keys must not be copied
between hosts or supplied through implicit agent forwarding.

## External archive intake

Use `inventory-archive` and `validate-cw-multi-capture` to authenticate
preserved artifacts and their declared relationships. These commands do not
turn independently acquired files into a coherent live capture or a hardware
qualification.

Guide: [Archive normalization](development/archive-normalization.md).

## Receiver calibration

Use `evaluate-sdr-calibration` to inspect the frozen native
`sdr-calibration-profile` version `1.0.0` contract, then
`compose-receiver-calibration` to create a first-class `required` or `optional`
binding. Recorded carrier and CW replay accept that binding, and resolved Tone,
WSPR, QRSS, FSKCW, and DFCW plans require an explicit binding (including the
explicit `disabled` form). The plan digest covers the complete binding.

`generate-synthetic-sdr-calibration` creates a deterministic unsigned fixture
for hardware-free exercises. It is not a real calibration and cannot qualify a
receiver. Receiver calibration adds estimated-true frequency and uncertainty
while retaining indicated measurements; it never changes transmitter PPM.

Guides: [SDR calibration profile consumer](development/sdr-calibration-profile-consumer.md)
and [receiver calibration operator workflow](development/receiver-calibration-operator.md).

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

For live keyed blockage, inspect the transaction's `capture_diagnostic` and
`capture_native_failure` artifacts before deciding whether to change receiver
settings, fixture routing, or capture bounds. A successful transmitter process
does not turn missing receiver evidence into transmitter unqualification.

The harness does not keep these bundles in Git. Use a temporary or external
output directory, then move records selected for preservation to the target
project or another approved evidence store.

Legacy GPIO campaigns accept `--transmitter-backend gpio --transmit-gpio 4|20`.
Omitting the pin preserves the GPIO4 default. The selection binds every mode's
output, GPIO backend contract, transmitter arguments, and quiescence inspection.
TONE explicitly overrides the installed INI backend, pin, and drive in its
campaign-owned process; the installed INI is not edited. RP1 still requires an
explicit pin, and `--rp1-route` remains RP1-only. Si5351 rejects GPIO selectors.
When a configuration is supplied, its mode plans must agree with an explicit
pin selector; the harness does not silently rewrite an authenticated plan.
