# Agent operating guide

This is the starting point for an agentic process that needs to inspect, test,
or operate the WsprryPi Qualification Harness. It is a navigation guide, not a
replacement for the governing contract or runtime authorization.

## Authority order

When sources disagree, use this order:

1. [`CONTRACT.md`](../CONTRACT.md) defines safety, measurement, evidence, and
   classification requirements.
2. [`AGENTS.md`](../AGENTS.md) defines repository scope and preservation rules.
3. JSON Schemas under [`schemas/`](../schemas/) define review-time document
   contracts. The byte-matched packaged copies under
   `src/wsprrypi_qualification/schemas/` are the runtime copies.
4. The installed CLI help and production source define the commands available
   at the checked-out revision.
5. Development guides under [`docs/development/`](development/) describe the
   implemented boundaries and retained actual-host work.
6. A run bundle describes only its recorded revision, hardware, settings, RF
   path, and time. Never generalize it to another combination.

Historical files are evidence, not executable operating instructions.

## Establish current truth first

From the repository root, perform these read-only checks before planning:

```text
git status --short --branch
git rev-parse HEAD
git remote -v
python --version
python -m wsprrypi_qualification version
python -m wsprrypi_qualification --help
python -m wsprrypi_qualification capabilities
```

Then read the contract, repository instructions, relevant schema, and relevant
development guide. Do not assume a command shown in an old run log still has
the same interface; use `COMMAND --help` at the current revision.

Before accessing another host, inspect it read-only for active processes,
sessions, services, dirty repositories, device owners, and other maintainer
work. Existing work is a non-interference blocker unless the operator confirms
that it is stale and authorizes its bounded removal. Never infer authority from
an earlier run or a committed profile.

## Install a development environment

Python 3.11 or newer is required. Run Python tooling from a virtual
environment:

```text
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"
```

On Windows, use the Python launcher when needed, for example
`py -3.13 -m venv .venv`, and then invoke `.venv\Scripts\python.exe` directly.
Use structured argument lists in automation; do not depend on Bash quoting.

## Choose the correct workflow

### Read-only orientation

Use `capabilities`, schema inspection, `--help`, source inspection, and Git
status. Capability discovery does not authorize or perform a live operation.

### Profile and plan validation

Start from [`examples/`](../examples/) only as non-executable examples. Create
machine-local requested profiles outside Git, resolve every host/device/RF-path
fact for the current run, and validate the requested documents:

```text
python -m wsprrypi_qualification validate-profile bench BENCH.json
python -m wsprrypi_qualification validate-profile test TEST.json
python -m wsprrypi_qualification validate-profile receiver-run RECEIVER_RUN.json
python -m wsprrypi_qualification validate-application-plan APPLICATION_PLAN.json
python -m wsprrypi_qualification real-session RESOLVED_PLAN.json --plan-only
```

Schema validity is necessary but not sufficient. Semantic validators bind
digests, artifact identities, lifecycle ordering, result precedence, and
cross-document facts. Use the maintained loader or validator for the evidence
type rather than calling `jsonschema` alone.

### Hardware-free qualification simulation

The bounded simulator is the normal end-to-end rehearsal. It launches only
local synthetic children and can never qualify hardware:

```text
python -m wsprrypi_qualification simulate-qualification RUN_PARENT \
  --run-id 20260813T230000Z-example \
  --child-timeout 1 \
  --overall-timeout 15
```

See [`bounded-simulator.md`](development/bounded-simulator.md) for injection
cases and bundle validation.

### Offline evidence analysis

The maintained offline sequence is:

1. validate capture metadata and the authenticated IQ artifact;
2. analyze distinct RF-off and RF-on captures;
3. stop if the carrier gate does not pass;
4. generate each canonical UTC-slot WAV from the coherent capture;
5. invoke `wsprd` independently for each WAV;
6. summarize the three consecutive decoder documents;
7. validate and manifest the final bundle.

The separate hardware-free SDR calibration consumer accepts only the frozen
native `sdr-calibration-profile` version `1.0.0` contract. Use
`evaluate-sdr-calibration PROFILE.json APPLICATION.json` to validate and apply
it without device access. This command is not yet connected to recorded or live
qualification; see [`sdr-calibration-profile-consumer.md`](development/sdr-calibration-profile-consumer.md).

The command synopsis in [`README.md`](../README.md) provides examples. The
details and acquired-evidence checks are in
[`slice-3.md`](development/slice-3.md). On macOS, tool discovery includes the
WSJT-X application bundle; do not claim `wsprd` is absent until discovery has
checked `/Applications/wsjtx.app/Contents/MacOS/wsprd` as well as `PATH`.

For tone and CW-family retained captures, use the Phase 4 acquired replay
composer and validator described in
[`cw-acquired-iq-replay.md`](development/cw-acquired-iq-replay.md). A passing
replay measurement remains `inconclusive`: it cannot substitute for runtime
authorization, live-session, cleanup, or quiescence evidence.

For preserved whole-host evidence and separately acquired keyed repetitions,
use the non-qualifying archive inventory and multi-capture validator described
in [`archive-normalization.md`](development/archive-normalization.md). These
commands authenticate intake relationships only; they cannot establish a
coherent capture, lifecycle evidence, or hardware qualification.

For the hardware-free Phase 5 lifecycle rehearsal, use
`run-cw-mock-lifecycle` and `validate-cw-mock-lifecycle` as described in
[`cw-mock-bounded-lifecycle.md`](development/cw-mock-bounded-lifecycle.md).
Only the closed mock injection vocabulary is accepted. This does not authorize
or validate any live adapter, host, service, receiver, transmitter, or RF path.

For Phase 6, use `run-cw-actual-host-preflight` only under current explicit
read-only host authorization. The command requires an exact plan digest and
enable flag, executes only the schema-bounded probe set through structured SSH
arguments, and produces a non-qualifying immutable bundle. Validate it with
`validate-cw-actual-host-preflight`. A blocked bundle is a truthful Phase 6
result, not permission to correct the host or advance to Phase 7. See
[`cw-actual-host-preflight.md`](development/cw-actual-host-preflight.md).

### Live split-host WSPR lifecycle

The maintained topology currently uses `wspr4` for WsprryPi transmission and
`wspr5` for local RSP1B capture, offline analysis, decoding, and evidence
publication. Read [`live-three-frame.md`](development/live-three-frame.md) and
the strict [`resolved-real-session-plan.schema.json`](../schemas/resolved-real-session-plan.schema.json)
before constructing a plan.

The live command is deliberately unavailable without all runtime gates:

```text
python -m wsprrypi_qualification run-live-session RESOLVED_PLAN.json \
  --enable-live-session \
  --enable-rf \
  --operator-id OPERATOR
```

This is not permission to run it. A live run additionally requires current,
explicit operator authorization, exact digest confirmation, current RF-path
facts, successful non-interference and ownership preflight, cleanup registered
before output, hard deadlines, and backend-specific quiescence. Pause before a
new external or hardware boundary unless the operator has authorized that
precise boundary in the current task.

The carrier gate must pass before frame transmission. At 250 ksps the WSPR run
must retain one coherent 370-second capture containing exactly 92,500,000 CF32
samples with zero overflow and three consecutive even-UTC slots. Qualification
requires three independent complete decodes of the configured identity.

### Live carrier-only Phase 7 lifecycle

`run-cw-live-tone` is a separate digest-bound production path for an exact
leading-off, repeated on/off, and closing-off carrier schedule. It never calls
the WSPR frame or decoder phases. Its resolved plan must use
`session_kind: cw_live_tone`, `mode: TONE`, zero frames, an exact
`tone_schedule`, a pinned loopback-only `tone_server` process and configuration,
and an RF-on capture count equal to the complete schedule at the resolved sample
rate. One dedicated WsprryPi process spans the cadence; each cycle is a bounded
authenticated-helper transaction rather than a new transmitter process.

```text
python -m wsprrypi_qualification run-cw-live-tone RESOLVED_PLAN.json \
  OUTPUT_PARENT --work-directory NEW_WORK_DIRECTORY --ssh /absolute/ssh \
  --operator OPERATOR --enable-live-tone --enable-rf
```

This command has the same external-access, exact digest confirmation, cleanup,
service restoration, and backend-quiescence authorization boundary as the
WSPR command. A passing carrier result remains Phase 7 evidence with final
status `inconclusive`; it is not WSPR qualification, calibrated power, or
spectral-compliance evidence.

## Evidence review checklist

Treat a status as trustworthy only when all applicable checks pass:

- the run directory is new and its UTC/test ID agrees with retained documents;
- requested and resolved plans, runtime authorization, tool identities, source
  revisions, host identities, and RF-path facts are retained;
- `SHA256SUMS` is canonical and covers the exact required artifact set;
- artifact indexes bind original and retained paths, sizes, and SHA-256 values;
- raw IQ sizes equal `sample_count * 8` for interleaved CF32;
- metadata settings and device identity agree with the resolved receiver;
- captures have exact counts, first-read discard, zero overflow, and acceptable
  clipping/timeout/cancellation outcomes;
- carrier metrics are recomputed from retained RF-off/RF-on IQ and enforce the
  bounded relative offset and contrast gate while retaining nominal offset and
  best-20-Hz-share diagnostics;
- frame evidence exists only after a passing carrier gate;
- WAV names, UTC slots, PCM structure, hashes, and decoder arguments agree;
- complete `wsprd` stdout/stderr and decoder-created artifacts are retained;
- service restoration affects only services changed by the harness;
- owned processes are absent and GPIO/Si5351 quiescence is verified;
- cleanup failure overrides every otherwise successful measurement; and
- `result.json` agrees with gates, causes, cleanup, session evidence, and the
  exact classification enum in `CONTRACT.md`.

If raw IQ has been moved, accept it only through the maintained authenticated
relocation/index mechanism. A matching hash alone is not path provenance.

## Validation before proposing a commit

Run all safe applicable gates:

```text
python -m ruff format --check .
python -m ruff check .
python -m mypy src
python -m pytest
python -m build
cmake -S . -B build-native -DWSPQ_BUILD_SOAPY=OFF -DWSPQ_BUILD_TESTS=ON
cmake --build build-native --config Release
ctest --test-dir build-native -C Release --output-on-failure
```

Also verify `provenance/SHA256SUMS`, schema source/package synchronization, and
`git diff --check`. CI runs the Python 3.11/3.13 matrix on macOS, Ubuntu, and
native Windows runners. Actual-host records are summarized in
[`cross-platform-actual-host-validation.md`](development/cross-platform-actual-host-validation.md).

Warnings, skipped checks, unavailable hardware, and hosts not actually tested
must be reported separately from passes. Never convert a simulator, replay, or
hosted-CI result into a hardware qualification claim.

## Where to look next

- Safety and result meaning: [`CONTRACT.md`](../CONTRACT.md)
- Contributor workflow: [`CONTRIBUTING.md`](../CONTRIBUTING.md)
- Capture helper: [`slice-2.md`](development/slice-2.md)
- Carrier/audio/decoder pipeline: [`slice-3.md`](development/slice-3.md)
- Capability adapters: [`real-capability-adapters.md`](development/real-capability-adapters.md)
- Raspberry Pi helper deployment: [`helper-deployment.md`](development/helper-deployment.md)
- Receiver lifecycle: [`receiver-integration.md`](development/receiver-integration.md)
- Transmitter lifecycle: [`transmitter-lifecycle.md`](development/transmitter-lifecycle.md)
- Bounded carrier evidence: [`bounded-carrier-evidence.md`](development/bounded-carrier-evidence.md)
- Split-host live sequence: [`live-three-frame.md`](development/live-three-frame.md)
- Current actual-host record: [`cross-platform-actual-host-validation.md`](development/cross-platform-actual-host-validation.md)
- Preserved archive intake: [`archive-normalization.md`](development/archive-normalization.md)

For the newest state, prefer the current Git revision, CLI help, schemas, CI,
and newest immutable evidence bundle over prose that describes an older slice.
