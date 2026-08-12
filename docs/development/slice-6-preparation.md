# Slice 6 preparation: hardware-free qualification composition

This phase composes the maintained profile, WsprryPi application-plan, Slice 4
supervisor, WSPR timing, result, evidence, and manifest contracts. It does not
add a live command or authorize transmitter operation.

## State and safety model

`QualificationSession` is single-use and accepts only `mock_only` plans whose
application plan records `execution_authorized: false`. It validates the bench,
per-run receiver/RF path, transmitter profile, application source identity,
backend, emitted RF frequency, three-frame WSPR contract, exact 370-second
sample count, and external deadlines before crossing a simulated enable
boundary.

Runtime confirmation is an ephemeral record containing the complete resolved
plan and its SHA-256. It is not loaded from a committed profile. Receiver-only
authorization remains receiver-only and cannot satisfy transmitter
confirmation.

The recorded phase order is requested, validated, runtime confirmed, evidence
authentication in preflight, cleanup installed, and RF idle, followed by a
bounded carrier mock lifecycle.
That lifecycle records its cleanup and quiescence before the retained carrier
analysis is authenticated. Only a passing carrier document starts a fresh
bounded frame mock lifecycle; its cleanup and quiescence are likewise recorded
before retained audio, decoder, and summary authentication. The existing
supervisor supplies mock ownership, cancellation, child lifecycle, service
restoration, leak checking, cleanup ordering, and backend quiescence evidence.
No parallel supervisor or cleanup model was introduced, and the event stream
does not pretend that offline analysis occurred before lifecycle cleanup.

The carrier gate is the sole path to frame evidence. Failed or blocked carrier
evidence leaves `frames_started` false. The prepared WSPR path requires three
ordered consecutive slots and exactly 92,500,000 samples at 250 ksps. A
successful mock carrier and decode sequence remains `inconclusive` by retaining
the explicit incomplete-hardware-evidence cause. It can never produce
`qualified`. Cleanup failure retains precedence.

## Failure injection

Declarative injections are limited to orchestration faults: profile,
confirmation, and source errors; unavailable capabilities and dependencies;
unsafe RF paths; receiver and ownership conflicts; RF-idle failure; launch
failure; bounded child timeout; cancellation; service restoration;
cleanup; and backend-quiescence failure. Carrier, capture, slot, and decode
outcomes are never injected. Tests create retained outputs with the maintained
Slice 3 components and corrupt those artifacts when exercising evidence
failure paths. Callbacks and arbitrary executables are not accepted.

## Evidence

Each simulation creates a new run-ID directory and refuses reuse. It retains:

- `resolved-session-plan.json`;
- `runtime-confirmation.json`;
- `session.json`, including the complete Slice 4 supervisor documents;
- stage-applicable authenticated copies of carrier, audio, decoder, and summary JSON;
- retained profile, capture-metadata, WAV, and decoder-data dependencies;
- `offline-evidence-index.json`, preserving source identity and bundled/external disposition;
- schema-valid `result.json`; and
- deterministic `SHA256SUMS`.

The resolved plan carries requested/resolved profiles, application identity and
arguments, protocol contract, UTC slots, exact capture plan, receiver and
transmitter deadlines, backend/output/calibration facts, and current RF path.
Paths are handled with `pathlib`; executable paths containing spaces and native
Windows forms remain one structured argument.

Existing carrier, WAV, decoder, and decode-summary modules remain the
authoritative offline measurement implementations. The composition layer loads
their retained files, revalidates hashes and semantic bindings, recomputes the
decode summary, binds the resolved profiles to the session plan, and derives
gate outcomes only from those authenticated documents. Synthetic test artifacts
remain hardware-free evidence and cannot qualify a transmitter.

Raw CF32 IQ is deliberately external: its durable record preserves its absolute
location, byte size, and SHA-256. Derivatives and metadata are bundled and remain
reviewable if the fixture workspace is removed. A failed carrier gate suppresses
all frame-stage documents and dependencies from the run bundle.

## Development validation

Run:

```text
python -m ruff format --check .
python -m ruff check .
python -m mypy src
python -m pytest
python -m build --no-isolation
cmake -S . -B BUILD -DWSPQ_BUILD_SOAPY=OFF -DWSPQ_BUILD_TESTS=ON
cmake --build BUILD --config Release
ctest --test-dir BUILD -C Release --output-on-failure
```

No CLI capable of executing a qualification plan exists. `--enable-rf`, live
commands, physical SDR access, and real SSH remain refused by the portable CLI.
QRSS, FSKCW, and DFCW retain application-plan support but have no qualification
workflow. Hellschreiber and RP1 remain unsupported.

## Next gate

The mock coordinator remains unchanged in meaning. A distinct
`RealQualificationSession` now composes the reviewed production adapter
boundaries using an explicit resolved plan and ephemeral external-access and RF
authorizations. Its only public command is `real-session --plan-only`, which
validates and prints a plan digest and performs zero adapter calls. The portable
CLI still refuses `--enable-rf`, `transmit`, and other live commands.

This integration was implemented and tested only with sealed fake adapters. A
hardware-free success is forced to `inconclusive`; it cannot qualify a
transmitter. The next unfinished step is a separately authorized read-only
real-capability preflight. It must pause before the first SSH connection, SDR
enumeration/open, service inspection, GPIO inspection, or I2C transaction.
Transmitter launch and RF remain a later, separately authorized gate. Actual
macOS, Ubuntu, Windows, and Raspberry Pi OS behavior remains host validation.

The real-session plan uses a path-safe UTC run ID and, for this prepared WSPR
workflow, fixes the preserved coherent-capture contract at 250,000 CF32
samples/second for 370 seconds (exactly 92,500,000 samples). Requested and
resolved profile artifacts, executable and provider hashes, receiver identity,
current RF path, backend/output, calibration, drive, deadlines, and stopping
procedure are explicit. Adapter records carry the complete plan digest and are
schema- and semantics-checked; a bare claimed gate outcome is not accepted.
Cleanup is attempted after any cleanup-registration attempt, including partial
failure, and evidence publication rolls back its harness-created incomplete
directory on failure.

`execution_mode` is fixed to `hardware_free_validation` in the current schema.
There is no supported way to relabel mock evidence as live evidence, and this
coordinator cannot return `qualified`. Stage records include elapsed and hard
deadline values, and carrier pass/fail is recomputed from the historical
100-Hz offset and 50-percent best-20-Hz thresholds instead of trusting an
adapter's claimed gate. Retained physical IQ, WAV, and decoder-log packaging is
therefore still a live-phase acceptance gate, not a claim made by this phase.
