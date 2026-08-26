# WsprryPi Qualification Harness

Cross-platform tooling for bounded WsprryPi transmitter qualification,
exact-count SDR capture, offline signal analysis, independent WSPR decoding,
and lifecycle verification.

The harness is an engineering qualification tool. It is not an operator
transmitter interface, a spectrum-compliance instrument, or authorization to
radiate. Read [CONTRACT.md](CONTRACT.md) before operating it.

## What is available

The maintained package provides:

- schema-validated bench, test, receiver-run, application, and resolved-session
  plans;
- read-only dependency and capability discovery;
- a portable C++ exact-count CF32 capture helper with mock-source tests;
- offline carrier analysis with optional authenticated PNG/SVG relative-spectrum
  plots, UTC-slot WAV generation, `wsprd` execution, and consecutive-decode validation;
- deterministic tone and CW-family reference generation, synthetic-IQ analysis,
  acquired-IQ replay, and mock lifecycle rehearsal;
- first-class frozen SDR Calibration Profile 1.0.0 bindings for receiver-only
  frequency interpretation in recorded and live workflows;
- offline live-keyed plan, authorization, transaction, aggregate, result, and
  artifact-index contracts plus a sealed deterministic hardware-free coordinator
  rehearsal for QRSS, FSKCW, and DFCW;
- fail-closed `run-cw-live-keyed` production coordination for three independent
  QRSS, FSKCW, or DFCW process/capture transactions;
- authenticated archive and multi-capture evidence intake;
- digest-bound, read-only actual-host preflight;
- fail-closed split-host WSPR and carrier-only live coordinators;
- thin typed `turnkey-campaign` planning, deterministic rehearsal, exact-digest
  confirmation, and routing to the existing production coordinators;
- simple `complete-test TRANSMITTER_HOST RECEIVER_HOST --sdr SELECTOR --enable-rf`
  orchestration for the ordered TONE, WSPR, QRSS, FSKCW, and DFCW campaign; and
- schema-validated result bundles with explicit cleanup and qualification states.

Hardware-free results, replays, mock lifecycles, and host preflights cannot
qualify hardware. A positive qualification claim requires an explicitly
authorized live run whose output satisfies the exact backend, band, hardware,
source, receiver-path, and cleanup contracts.

This repository does not retain target qualification evidence. Commands write
new result directories for operator review and transfer; keep target-specific
records with the target project or another approved evidence store. `runs/` is
ignored temporary output, not a repository archive.

A working conducted test rig can run the default five-mode campaign against
Si5351 and against the GPIO backend used by Broadcom/DMA WsprryPi versions.
That statement records rig capability, not portable qualification evidence.

## Start here

- [Current workflows](docs/CURRENT_WORKFLOWS.md) — supported commands and the
  correct guide for each task.
- [Operator security](docs/OPERATOR_SECURITY.md) — required SSH trust paths,
  key handling, host verification, and privilege boundaries.
- [Future roadmap](docs/ROADMAP.md) — the five remaining phases only.
- [Turnkey campaign guide](docs/development/turnkey-campaign.md) — one thin
  multi-mode routing workflow.
- [Agent operating guide](docs/AGENT_OPERATIONS.md) — required orientation,
  authority order, validation, and evidence review.
- [Contract capability matrix](docs/CAPABILITY_MATRIX.md) — source, schema, and
  test breadcrumbs for each governed capability.
- [Contract](CONTRACT.md) — governing safety, measurement, evidence, and result
  semantics.
- [Contributing](CONTRIBUTING.md) — development workflow.
- [Security](SECURITY.md) — safety and security reporting.

## Agentic testing control

An agent begins with [AGENTS.md](AGENTS.md), then follows the
[agent operating guide](docs/AGENT_OPERATIONS.md),
[operator security guide](docs/OPERATOR_SECURITY.md), and
[current workflow router](docs/CURRENT_WORKFLOWS.md). The
[capability matrix](docs/CAPABILITY_MATRIX.md) maps each route to its production
module, schemas, and tests. Exact invocation syntax comes from the checked-out
CLI `--help`, never from retained logs.

The normal bounded campaign entrypoint is `complete-test`, which composes and
runs TONE, WSPR, QRSS, FSKCW, and DFCW. It prints the durable progress JSONL
path and an exact log-viewer command before long-running work begins. An agent
may perform read-only orientation and hardware-free validation within the
current task, but it must have explicit authority before host access, SDR or
transmitter access, privilege use, service changes, or RF transmission.

## Install

Python 3.11 or newer is required.

```text
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On Windows, use the Python launcher if needed and invoke the environment's
interpreter directly, for example `.venv\Scripts\python.exe`.

## Safe orientation

These commands are read-only or local validation operations:

```text
wsprrypi-qualification version
wsprrypi-qualification --help
wsprrypi-qualification capabilities
wsprrypi-qualification validate-profile bench BENCH.json
wsprrypi-qualification validate-profile test TEST.json
wsprrypi-qualification validate-profile receiver-run RECEIVER_RUN.json
wsprrypi-qualification validate-application-plan APPLICATION_PLAN.json
wsprrypi-qualification real-session RESOLVED_PLAN.json --plan-only
wsprrypi-qualification generate-synthetic-sdr-calibration NEW_DIRECTORY
wsprrypi-qualification compose-receiver-calibration PROFILE REQUEST BINDING
```

Example profiles are non-executable starting points. Device-specific gain,
frequency correction, attenuation, safe-input limits, identity, and RF-path
facts must be resolved for the actual run and recorded in its output bundle.

## Development validation

Use targeted tests while developing. Before merging a cross-cutting change or
release, run the complete local acceptance set (CI runs the same product gates):

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

CI covers supported Python versions on macOS, Ubuntu, and native Windows.
Actual Raspberry Pi, SDR, transmitter, and RF-path validation remains a
separate, explicitly authorized evidence gate.

## Repository layout

- `src/wsprrypi_qualification/` — portable package and packaged schemas.
- `native/` — CMake-based exact-count capture helper.
- `schemas/` — review-time JSON Schema contracts.
- `deployment/` — reviewed deployment assets; presence does not authorize
  installation.
- `examples/` — non-executable example inputs.
- `tests/` — hardware-free and failure-injected validation.
- `docs/development/` — capability-specific operating and implementation guides.
- `runs/` — ignored scratch location for generated output; never a source archive.

The review-time schemas under `schemas/` and their runtime copies under
`src/wsprrypi_qualification/schemas/` must remain synchronized when a packaged
schema changes.

## Safety boundary

Nothing in the repository, an example profile, or a prior evidence bundle
authorizes RF transmission, GPIO/I2C activity, SDR access, service changes, or
software installation. Live commands require their explicit enable flags,
current operator confirmation bound to the exact plan, successful ownership and
idle-state preflight, bounded execution, cleanup registered before RF enable,
and backend-specific quiescence verification.

Successful decoding does not establish calibrated power, filtering, harmonic
suppression, spurious-emission compliance, antenna readiness, or qualification
of another configuration.
Receiver calibration never supplies or alters WsprryPi transmitter PPM.

## License

This project is licensed under the [MIT License](LICENSE).
