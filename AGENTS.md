# Repository Instructions

These instructions apply to the entire WsprryPi Qualification Harness.

Before operating or changing the harness, read in this order:

1. `CONTRACT.md` for safety and result semantics;
2. `docs/AGENT_OPERATIONS.md` for agent navigation and validation;
3. `docs/CURRENT_WORKFLOWS.md` for capability routing;
4. `docs/CAPABILITY_MATRIX.md` for source/schema/test breadcrumbs;
5. the relevant guide under `docs/development/`, JSON Schema, CLI `--help`,
   production module, and tests.

Treat the checked-out source and CLI help as current. Do not reconstruct an
operation from old logs or target evidence.

## Scope and preservation

- Read `CONTRACT.md` before planning or changing implementation.
- Inspect branch, working tree, dependencies, and relevant contracts first.
- Preserve user changes. Do not reset, clean, stash, commit, push, or create a
  pull request unless explicitly requested.
- Keep the harness separate from WsprryPi and its submodules.
- Do not add target-specific run bundles, copied host archives, evidence
  anchors, authorization receipts, or retrospective corrections to this repo.
  Generated `runs/` content is ignored scratch output. Transfer records that
  must be kept to the applicable target project or approved evidence store.

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

## Output and claims

- Produce new run directories and schema-validated result documents outside
  source control.
- Preserve complete decoder logs, not only matching lines.
- Distinguish transmitter failure, receiver/fixture blockage, abort, preflight
  failure, cleanup failure, and inconclusive evidence.
- Qualification is backend-, band-, hardware-, source-, and path-specific.
- Decode success does not establish spectral compliance or calibrated power.
- Pin external tool paths and versions in each result bundle.

## Agent validation

Before proposing a change, run the safe checks applicable to it:

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

Use temporary directories for test output. Never treat offline, mock, replay,
or source inspection as hardware qualification. Do not cross into host access,
device access, service changes, installation, or RF without the exact authority
required by the contract and current user request.
