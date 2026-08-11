# WsprryPi Qualification Harness

Foundation for a future cross-platform, evidence-producing WsprryPi RF
qualification harness maintained as an independent project.

The project currently contains its governing contract, initial data schemas,
non-executable example profiles, and preserved Issue 379 capture and analysis
sources. The production harness has not yet been implemented. Development is
organized into reviewed slices, beginning with an offline-only portable
foundation.

## Intended outcome

The finished harness should run from macOS, Linux, Raspberry Pi OS, or Windows
and coordinate a bounded WsprryPi transmitter test with a SoapySDR receiver.
It must produce reviewable evidence rather than a simple pass/fail indication.

The portable control and analysis layer should use Python 3.11 or newer. The
exact-sample-count SoapySDR capture helper should be built with CMake on every
supported host. Platform-specific behavior belongs behind capability adapters:

- local subprocess and filesystem operations;
- SSH control of a Raspberry Pi transmitter;
- local SoapySDR receiver access;
- optional remote capture transport;
- service and hardware-quiescence inspection on the transmitter.

Shell scripts under `historical/` are preserved evidence only and are not the
future cross-platform interface.

## Current status

Repository foundation only. Slice 1 is not yet implemented, and no live
hardware path is qualified by this repository.

The reviewed roadmap is:

1. portable Python package, profiles, schemas, read-only capabilities, result
   model, manifests, tests, packaging, and cross-platform CI;
2. CMake-based exact-count capture helper with mocked capture tests;
3. offline carrier, IQ, WAV, decoder, and optional frame analysis;
4. transports, adapters, supervision, and failure-injected cleanup tests;
5. separately authorized receiver-only validation; and
6. separately authorized bounded transmitter qualification.

## Preserved material

- `CONTRACT.md`: product, safety, evidence, portability, and acceptance contract.
- `AGENTS.md`: repository instructions for future Codex work.
- `START_NEW_CODEX_PROJECT.md`: directly usable implementation prompt.
- `schemas/`: initial machine-readable profile and result contracts.
- `examples/`: non-executable example bench and test profiles.
- `historical/`: original helpers copied from `wspr5`; review before reuse.
- `provenance/`: source locations, hashes, and related WsprryPi research.

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes and
[SECURITY.md](SECURITY.md) for reporting safety or security defects.

## Important boundary

Nothing in this seed authorizes RF transmission, GPIO activity, I2C output,
service changes, software installation, or SDR ownership. Offline development
and replay tests must come first. Live operation requires an explicit operator
action, complete preflight, bounded execution, and verified cleanup.

Successful decoding does not establish calibrated RF power, filtering,
harmonic suppression, spurious-emission compliance, antenna readiness, or
qualification of another backend, band, board, receiver, or RF path.

## License

This project is licensed under the [MIT License](LICENSE). Preserved historical
sources retain their provenance and licensing notice in
[historical/NOTICE.md](historical/NOTICE.md).
