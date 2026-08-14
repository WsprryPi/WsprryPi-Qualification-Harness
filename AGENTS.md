# Repository Instructions

These instructions apply to the entire WsprryPi Qualification Harness.

For repository orientation and maintained command/evidence breadcrumbs, read
`docs/AGENT_OPERATIONS.md` after `CONTRACT.md` and before operating the harness.

## Scope and preservation

- Read `CONTRACT.md` before planning or changing implementation.
- Inspect branch, working tree, dependencies, and relevant contracts first.
- Preserve user changes. Do not reset, clean, stash, commit, push, or create a
  pull request unless explicitly requested.
- Keep the harness separate from WsprryPi and its submodules.
- Keep historical files unchanged unless the user explicitly requests archival
  correction. Promote behavior by writing reviewed production code and tests.

## Cross-platform requirement

- The portable core must run on macOS, Linux/Raspberry Pi OS, and native Windows.
- Prefer Python standard-library APIs and `pathlib`; justify dependencies.
- Do not place Bash, systemd, `/proc`, POSIX signal, GNU utility, or Unix path
  assumptions in the portable core.
- Isolate OS and transport behavior behind explicit capability adapters.
- Test paths containing spaces and Windows path forms.
- Use CMake for native helper builds and CI on macOS, Ubuntu, and Windows.

## Hardware and RF safety

Unless the user explicitly authorizes a precisely bounded live run, do not:

- transmit or generate a tone;
- touch GPIO, I2C, DMA, PWM, GPCLK, or an attached transmitter;
- open or reconfigure a physical SDR;
- stop/start services;
- install or replace software on a Raspberry Pi; or
- use mutating `sudo` operations.

Offline fixture generation, replay analysis, compilation, and non-hardware
tests are distinct from live qualification. Never claim hardware correctness
from source or replay tests.

Live mode must be opt-in, fail closed, install cleanup before RF enable, bound
every process, and verify backend-specific quiescence. A cleanup failure makes
the run unsuccessful.

## Evidence and claims

- Produce immutable run directories and schema-validated result documents.
- Preserve complete decoder logs, not only matching lines.
- Distinguish transmitter failure, receiver/fixture blockage, abort, preflight
  failure, cleanup failure, and inconclusive evidence.
- Qualification is backend-, band-, hardware-, source-, and path-specific.
- Decode success does not establish spectral compliance or calibrated power.
- Pin external tool paths and versions in each evidence bundle.

## Development sequence

Work in reviewed slices:

1. schemas, portable package skeleton, dependency discovery, and offline tests;
2. capture-helper build and mocked exact-count capture contract;
3. offline carrier and decoder pipelines using fixtures;
4. transport/adapters and failure-injected supervisor tests;
5. explicitly authorized live receiver validation; and
6. explicitly authorized bounded transmitter validation.

Do not silently advance into a later hardware slice.
