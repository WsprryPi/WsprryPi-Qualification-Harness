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
- offline-only live-keyed plan, authorization, three-transaction aggregate,
  result, and artifact-index contracts for QRSS, FSKCW, and DFCW;
- authenticated archive and multi-capture evidence intake;
- digest-bound, read-only actual-host preflight;
- fail-closed split-host WSPR and carrier-only live coordinators; and
- schema-validated result bundles with explicit cleanup and qualification states.

Hardware-free results, replays, mock lifecycles, and host preflights cannot
qualify hardware. A positive qualification claim requires an explicitly
authorized live run whose output satisfies the exact backend, band, hardware,
source, receiver-path, and cleanup contracts.

This repository does not retain target qualification evidence. Commands write
new result directories for operator review and transfer; keep target-specific
records with the target project or another approved evidence store. `runs/` is
ignored temporary output, not a repository archive.

## Start here

- [Current workflows](docs/CURRENT_WORKFLOWS.md) — supported commands and the
  correct guide for each task.
- [Agent operating guide](docs/AGENT_OPERATIONS.md) — required orientation,
  authority order, validation, and evidence review.
- [Contract capability matrix](docs/CAPABILITY_MATRIX.md) — source, schema, and
  test breadcrumbs for each governed capability.
- [Contract](CONTRACT.md) — governing safety, measurement, evidence, and result
  semantics.
- [Contributing](CONTRIBUTING.md) — development workflow.
- [Security](SECURITY.md) — safety and security reporting.

## Install

Python 3.11 or newer is required.

```text
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
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
```

Example profiles are non-executable starting points. Device-specific gain,
frequency correction, attenuation, safe-input limits, identity, and RF-path
facts must be resolved for the actual run and recorded in its output bundle.

## Development validation

Run safe applicable gates from an activated development environment:

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

## License

This project is licensed under the [MIT License](LICENSE).
