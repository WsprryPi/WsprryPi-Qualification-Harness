# Agent operating guide

This is the starting point for an agentic process that needs to inspect, test,
or operate the WsprryPi Qualification Harness. It is a navigation guide, not a
replacement for the governing contract or runtime authorization.

## Authority order

When sources disagree, use this order:

1. [`CONTRACT.md`](../CONTRACT.md) defines safety, measurement, output, and
   classification requirements.
2. [`AGENTS.md`](../AGENTS.md) defines repository scope and preservation rules.
3. JSON Schemas under [`schemas/`](../schemas/) define review-time document
   contracts. The byte-matched packaged copies under
   `src/wsprrypi_qualification/schemas/` are the runtime copies.
4. The installed CLI help and production source define the commands available
   at the checked-out revision.
5. Capability guides under [`docs/development/`](development/) describe the
   implemented interfaces and operating boundaries.
6. A run bundle describes only its recorded revision, hardware, settings, RF
   path, and time. Never generalize it to another combination.

Target-specific evidence is not kept in this repository. Old logs, copied host
content, and output bundles are never executable operating instructions.

For task-oriented navigation, begin with
[`CURRENT_WORKFLOWS.md`](CURRENT_WORKFLOWS.md). Use the checked-out CLI and
named workflow guides as the current operating model.

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

### Offline capture analysis

The maintained offline sequence is:

1. validate capture metadata and the authenticated IQ artifact;
2. analyze distinct RF-off and RF-on captures;
3. stop if the carrier gate does not pass;
4. generate each canonical UTC-slot WAV from the coherent capture;
5. invoke `wsprd` independently for each WAV;
6. summarize the three consecutive decoder documents;
7. validate and manifest the final bundle.

Pass `--plot OUTPUT.png` or `--plot OUTPUT.svg` to `analyze-carrier` when a
frequency-domain rendering is requested. The command uses the non-interactive
Matplotlib Agg renderer. Validate the carrier-analysis document before trusting
the plot: its artifact identity, dimensions, renderer, relative normalization,
and source-analysis digest are authenticated. Plots remain relative and
non-calibrated operational output, never repository collateral.

The separate hardware-free SDR calibration consumer accepts only the frozen
native `sdr-calibration-profile` version `1.0.0` contract. Use
`evaluate-sdr-calibration PROFILE.json APPLICATION.json` to validate and apply
it without device access. It is a standalone profile-evaluation capability;
see [`sdr-calibration-profile-consumer.md`](development/sdr-calibration-profile-consumer.md).

The command synopsis in [`CURRENT_WORKFLOWS.md`](CURRENT_WORKFLOWS.md) provides
the supported sequence. Use the relevant command's current `--help` output and
the carrier-analysis schemas for exact inputs. On macOS, tool discovery includes the
WSJT-X application bundle; do not claim `wsprd` is absent until discovery has
checked `/Applications/wsjtx.app/Contents/MacOS/wsprd` as well as `PATH`.

For tone and CW-family retained captures, use the acquired-replay
composer and validator described in
[`cw-acquired-iq-replay.md`](development/cw-acquired-iq-replay.md). A passing
replay measurement remains `inconclusive`: it cannot substitute for runtime
authorization, live-session, cleanup, or quiescence evidence.

For externally stored whole-host evidence and separately acquired keyed repetitions,
use the non-qualifying archive inventory and multi-capture validator described
in [`archive-normalization.md`](development/archive-normalization.md). These
commands authenticate intake relationships only; they cannot establish a
coherent capture, lifecycle evidence, or hardware qualification.

For the hardware-free mock lifecycle rehearsal, use
`run-cw-mock-lifecycle` and `validate-cw-mock-lifecycle` as described in
[`cw-mock-bounded-lifecycle.md`](development/cw-mock-bounded-lifecycle.md).
Only the closed mock injection vocabulary is accepted. This does not authorize
or validate any live adapter, host, service, receiver, transmitter, or RF path.

Use `keyed_session_contracts` for offline construction and semantic validation
of live QRSS, FSKCW, or DFCW session documents. Read
[`live-keyed-contracts.md`](development/live-keyed-contracts.md) first. The
module requires exactly three independent transactions and derives status with
cleanup/quiescence precedence. Use `keyed_coordinator` only for the sealed
hardware-free three-transaction rehearsal; its injected fake is not a live adapter.

Use `run-cw-live-keyed` only for an exact separately authorized resolved keyed
plan. The command requires explicit live/RF flags, operator identity, and typed
digest confirmation. Its production composition uses the existing authenticated
helper, owned-process, exact-count capture, service, and backend-quiescence
adapters. Never substitute the hardware-free fake at this boundary.

Resolve one message repetition per transaction. Put every service the session
may inspect or change in the host-qualified service allowlist, and list an
initially inactive receiver service under `required_receiver_services` when it
must run for capture. The latter must be a receiver-side subset of the allowlist;
the coordinator starts it only after cleanup installation and restores its
observed state during every transaction cleanup.

Do not bypass the keyed adapter's capture-before-RF barrier. Its retained-output
readiness check and complete `pre_quiet_seconds` delay are what prevent the first
keyed symbol from preceding the authenticated capture. Treat a missing readiness
file or an early capture exit as a transmitter-launch blocker.
For a capture failure after launch, review the retained `capture_diagnostic`
and `capture_native_failure` artifacts. They bind the helper execution and
native failure metadata; rejected or partial IQ is deliberately absent. Report
that condition as receiver/fixture blockage, not transmitter unqualification.

Run split-host keyed coordination from the receiver/capture host. Before plan
resolution, prove that this host can reach the transmitter using the exact
resolved username and hostname with `BatchMode=yes`, strict host-key checking,
and the dedicated known-hosts file that the plan will bind. Compare a changed
host key through an independently trusted connection; never disable checking.
Record the SSH direction explicitly (for example, `wspr5 -> pi@wspr4.local`).

Also check service authority without changing state: use `sudo -n -l` for each
exact allowlisted start/stop command. When elevation is required, bind the
absolute privilege-wrapper path and SHA-256 in each immutable helper
configuration. Changing the SSH identity, known-hosts file, wrapper,
`systemctl`, helper, or configuration invalidates the resolved digest.

For live keyed plans, treat helper deployment configuration and runtime plan
authorization as separate identities. First seal each helper executable and
static configuration into `capability_bindings`; then compute the canonical
resolved-plan digest. The production launcher passes that digest separately and
the helper verifies the bound executable/configuration hashes before serving.
Never write the resulting digest back into either bound configuration, because
that would create a circular and unconstructible artifact identity.
For a Raspberry Pi transmitter, bind `/usr/bin/sudo` (or the reviewed exact
equivalent) as both the plan's `transmitter_process_privilege_wrapper` and the
static helper configuration's process wrapper. Verify noninteractive policy
with `sudo -n -l`. Never add sudo to WsprryPi application argv; the authenticated
helper owns the fixed `sudo -n --` prefix and rejects wrapper substitution.

Use `run-cw-actual-host-preflight` only under current explicit
read-only host authorization. The command requires an exact plan digest and
enable flag, executes only the schema-bounded probe set through structured SSH
arguments, and produces a non-qualifying immutable bundle. Validate it with
`validate-cw-actual-host-preflight`. A blocked bundle is a truthful read-only
preflight result, not permission to correct the host or begin live RF. See
[`cw-actual-host-preflight.md`](development/cw-actual-host-preflight.md).

### Live split-host WSPR lifecycle

The maintained topology currently uses `wspr4` for WsprryPi transmission and
`wspr5` for local RSP1B capture, offline analysis, decoding, and result
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

### Live carrier-only lifecycle

`run-cw-live-tone` is a separate digest-bound production path for an exact
leading-off, repeated on/off, and closing-off carrier schedule. It never calls
the WSPR frame or decoder lifecycle steps. Its resolved plan must use
`session_kind: cw_live_tone`, `mode: TONE`, zero frames, an exact
`tone_schedule`, a pinned loopback-only `tone_server` process and configuration,
and an RF-on capture count equal to the complete schedule at the resolved sample
rate. One dedicated WsprryPi process spans the cadence; each cycle is a bounded
authenticated-helper transaction rather than a new transmitter process.

The legacy `source.submodule_revision` field binds the exact Git object at
`HEAD:<source.submodule_path>`. Resolve it with
`git -C <source.repository_path> rev-parse HEAD:<source.submodule_path>`.
When the component has been absorbed into the parent repository, this is the
component tree object ID; it is not the commit returned by `git log -1 --
<source.submodule_path>`. Record the parent `HEAD` separately as
`source.parent_revision`.

```text
python -m wsprrypi_qualification run-cw-live-tone RESOLVED_PLAN.json \
  OUTPUT_PARENT --work-directory NEW_WORK_DIRECTORY --ssh /absolute/ssh \
  --operator OPERATOR --enable-live-tone --enable-rf
```

This command has the same external-access, exact digest confirmation, cleanup,
service restoration, and backend-quiescence authorization boundary as the
WSPR command. A passing carrier result remains carrier-only evidence with final
status `inconclusive`; it is not WSPR qualification, calibrated power, or
spectral-compliance evidence.

## Result review checklist

Treat a status as trustworthy only when all applicable checks pass:

- the run directory is new and its UTC/test ID agrees with its documents;
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

Also verify schema source/package synchronization and `git diff --check`. CI
runs the Python 3.11/3.13 matrix on macOS, Ubuntu, and native Windows runners.

Warnings, skipped checks, unavailable hardware, and hosts not actually tested
must be reported separately from passes. Never convert a simulator, replay, or
hosted-CI result into a hardware qualification claim.

## Maintained workflow guides

- Safety and result meaning: [`CONTRACT.md`](../CONTRACT.md)
- Task and command routing: [`CURRENT_WORKFLOWS.md`](CURRENT_WORKFLOWS.md)
- Contributor workflow: [`CONTRIBUTING.md`](../CONTRIBUTING.md)
- Capability adapters: [`real-capability-adapters.md`](development/real-capability-adapters.md)
- Raspberry Pi helper deployment: [`helper-deployment.md`](development/helper-deployment.md)
- Receiver lifecycle: [`receiver-integration.md`](development/receiver-integration.md)
- Transmitter lifecycle: [`transmitter-lifecycle.md`](development/transmitter-lifecycle.md)
- Split-host live sequence: [`live-three-frame.md`](development/live-three-frame.md)
- Preserved archive intake: [`archive-normalization.md`](development/archive-normalization.md)

For the newest state, prefer the current Git revision, CLI help, schemas, CI,
and newest immutable evidence bundle over numbered implementation records or
prose tied to an older revision.
