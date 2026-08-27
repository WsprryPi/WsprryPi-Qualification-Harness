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

## Agentic testing control

For an agent asked to plan, rehearse, execute, monitor, or review a campaign:

1. follow `docs/AGENT_OPERATIONS.md` to establish repository and tool truth;
2. follow `docs/OPERATOR_SECURITY.md` to verify the required SSH trust paths
   and privilege boundary without copying private keys or forwarding agents;
3. select the maintained route in `docs/CURRENT_WORKFLOWS.md`;
4. use `docs/CAPABILITY_MATRIX.md` to locate its schemas, production modules,
   and tests; and
5. derive exact commands from the checked-out CLI `--help` and the relevant
   `docs/development/` guide.

The normal agentic five-mode entrypoint is `complete-test`; its progress JSONL
path and exact viewer command are printed before long-running work begins.
Planning, rehearsal, validation, and log review do not authorize host access,
physical SDR access, privilege, transmitter operation, or RF. Cross those
boundaries only when the current user request grants the exact authority that
`CONTRACT.md` requires.

## Scope and preservation

- Read `CONTRACT.md` before planning or changing implementation.
- Inspect branch, working tree, dependencies, and relevant contracts first.
- Preserve user changes. Do not reset, clean, stash, commit, push, or create a
  pull request unless explicitly requested.
- Keep the harness separate from WsprryPi and its submodules.
- Treat a target repository as read-only unless the user separately authorizes
  changes there. Do not require a target project to adopt harness internals.
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

During development, run the static checks and tests directly affected by the
change. Run the complete acceptance set below for cross-cutting changes,
releases, or when CI is unavailable; do not repeatedly run it after every edit.

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
