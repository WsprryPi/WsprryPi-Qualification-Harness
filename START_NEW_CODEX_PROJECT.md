# Prompt for a new Codex project

Work in `/Users/lbussy/GitHub/WsprryPi-Qualification-Harness`.

This directory is a seed for a new, independent Git repository. First inspect
all files, especially `CONTRACT.md`, `AGENTS.md`, `provenance/README.md`, the
schemas, examples, and historical sources. Then inspect the directory and Git
state. Do not initialize Git, commit, push, create a remote, or modify any
sibling repository unless I explicitly request it.

Goal: implement a maintained, evidence-producing WsprryPi qualification harness
whose portable control and offline-analysis workflows run on macOS, Linux,
Raspberry Pi OS, and native Windows. Use Python 3.11+ for the portable CLI and
analysis layer and CMake for the cross-platform SoapySDR capture helper. Keep
OS-, SSH-, service-, SDR-, and transmitter-specific behavior behind capability
adapters. Historical scripts are evidence, not production code; review and
port their proven algorithms rather than polishing them in place.

Start with Slice 1 only:

1. Reconcile and tighten the supplied contract and JSON schemas without
   changing their safety or evidence boundaries.
2. Create the minimal Python package, CLI skeleton, typed domain models, profile
   loader, schema validation, capability-report command, deterministic run-ID
   generation, result-state model, and manifest writer.
3. Add unit tests for profiles, cross-platform paths, UTC slot calculations,
   exact sample-count calculations, result classification, and deterministic
   manifests.
4. Configure formatting, linting/type checking, tests, packaging, and CI for
   current macOS, Ubuntu, and Windows runners.
5. Document installation and the offline-only workflow delivered in this slice.

Do not implement live transmitter control, open an SDR, access GPIO or I2C,
change services, install on a Raspberry Pi, or transmit RF in Slice 1. Do not
claim that any live hardware path is qualified. Preserve the later slices as
an explicit roadmap:

- Slice 2: CMake capture helper and mocked exact-count capture contract.
- Slice 3: offline carrier/IQ/WSPR decoding using synthetic and approved replay
  fixtures.
- Slice 4: transports, adapters, supervision, and failure-injected cleanup.
- Slice 5: separately authorized live receiver-only validation.
- Slice 6: separately authorized bounded transmitter qualification.

Before editing, report the proposed file tree, key dependency choices, and any
contract ambiguity that would materially affect portability, safety, evidence,
or compatibility. Implement only after resolving material ambiguity. Run the
complete safe Slice 1 validation and report exact commands, results, remaining
gates, documentation impact, working-tree state, and whether anything was
committed or pushed.
