# WsprryPi Qualification Harness

Cross-platform, evidence-producing WsprryPi RF qualification harness maintained
as an independent project.

The project currently provides the reviewed implementation from Slices 1
through 6, including portable profiles and schemas, mock-tested exact-count
capture, offline carrier and WSPR decoding, lifecycle supervision, production
capability adapters, a fail-closed split-host live coordinator, and immutable
evidence packaging. Actual-host receiver, transmitter-lifecycle, and bounded
carrier exercises have been retained for the recorded `wspr4`/`wspr5`
configuration. No complete three-frame transmitter qualification has yet been
established. Preserved Issue 379 sources remain provenance, not production
code.

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

Slices 1 through 4 provide a portable, hardware-free foundation: schema-validated
profiles, typed models, read-only capability reporting, deterministic UTC run
IDs, exact WSPR-slot/sample calculations, result classification, SHA-256
manifests, a CMake-built exact-count CF32 capture engine exercised only by a
deterministic mock source, RF-off-subtracted carrier analysis, timestamped
CF32-to-WAV conversion, bounded independent `wsprd` decoding, structured local
child execution, mock transport and capability adapters, and lifecycle cleanup
supervision with injected failure tests. Slice 5 adds bounded receiver-only
exact-count validation on `wspr5`. Slice 6 adds reviewed production adapters
and the fail-closed split-host lifecycle. Bounded actual-host work has
exercised `wspr4` transmission and `wspr5` RSP1B capture, but the retained
carrier runs do not establish complete three-frame WSPR qualification.
Qualification remains specific to an immutable passing run and its exact
recorded configuration.

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
- `schemas/`: initial machine-readable profile and result contracts.
- `examples/`: non-executable example bench and test profiles.
- `historical/`: original helpers copied from `wspr5`; review before reuse.
- `provenance/`: source locations, hashes, and related WsprryPi research.

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes and
[SECURITY.md](SECURITY.md) for reporting safety or security defects.
Agentic processes should begin with the
[agent operating guide](docs/AGENT_OPERATIONS.md), which points to the current
CLI, schemas, safety gates, evidence validators, and actual-host records.

## Install and validate

Python 3.11 or newer is required.

```text
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"
python -m ruff format --check .
python -m ruff check .
python -m mypy
python -m pytest
python -m build
cmake -S . -B build-native -DWSPQ_BUILD_SOAPY=OFF
cmake --build build-native --config Release
ctest --test-dir build-native -C Release --output-on-failure
```

Safe Slice 1 and 2 Python commands are:

```text
wsprrypi-qualification version
wsprrypi-qualification capabilities
wsprrypi-qualification validate-profile bench examples/bench-wspr5-rsp1b.json
wsprrypi-qualification validate-profile test examples/test-si5351-160m.json
wsprrypi-qualification validate-profile receiver-run RUNTIME_RECEIVER_RUN.json
wsprrypi-qualification validate-capture-metadata CAPTURE_METADATA.json
wsprrypi-qualification validate-application-plan examples/application-plan-wsprrypi-wspr.json
wsprrypi-qualification validate-cw-qualification CW_ANALYSIS.json
wsprrypi-qualification analyze-carrier RF_OFF.cf32 RF_ON.cf32 carrier.json --rf-off-metadata RF_OFF.json --rf-on-metadata RF_ON.json --bench-profile BENCH.json --test-profile TEST.json
wsprrypi-qualification make-slot-wav CAPTURE.cf32 CAPTURE.json WAV_DIRECTORY audio.json --slot 2026-08-09T21:00:00Z --bench-profile BENCH.json --test-profile TEST.json
wsprrypi-qualification decode-wspr SLOT.wav audio.json decoder.json
wsprrypi-qualification summarize-decodes decode-summary.json slot-2100.json slot-2102.json slot-2104.json
```

The capability report locates future dependencies without executing them. Live
RF remains unavailable by default, and committed profiles cannot satisfy
runtime operator confirmation. See
[Slice 1](docs/development/slice-1.md) and
[Slice 2](docs/development/slice-2.md) and
[Slice 3](docs/development/slice-3.md) and
[Slice 4](docs/development/slice-4.md) and
[Slice 5](docs/development/slice-5.md) development guides for their complete
behavior and validation contracts.

Hardware-free application-shim and WSPR/QRSS-family protocol planning is
documented in [application shims](docs/development/application-shims.md). It
constructs reviewable WsprryPi argument vectors but cannot execute them or
authorize RF.

Hardware-free Slice 6 preparation composes WsprryPi plans, runtime
confirmation, the mock lifecycle supervisor, WSPR gate sequencing, result
classification, and immutable evidence packaging. See
[Slice 6 preparation](docs/development/slice-6-preparation.md). Its mock path
cannot produce hardware qualification.

Fail-closed production capability contracts now exist for OpenSSH, the native
SoapySDR capture helper, WsprryPi process ownership, narrow service restoration,
and GPIO/Si5351 quiescence. They are documented in
[real capability adapters](docs/development/real-capability-adapters.md). No
provider is enabled by the CLI, and their implementation is not live hardware
validation.
The packaged remote helper is also disabled without explicit provider and
allowlist configuration; installing it does not inspect or mutate a host.

A separate real-session coordinator now composes these adapter boundaries in
the required qualification order. `real-session PLAN.json --plan-only` remains
the zero-external-call review surface. The deliberately unavailable-by-default
`run-live-session` path accepts only the sealed production composition, a
split wspr4/wspr5 plan, both enable flags, and an exact ephemeral plan-digest
confirmation. See [split-host live qualification](docs/development/live-three-frame.md).

Hardware-free Raspberry Pi OS deployment preparation provides a strict helper
deployment configuration, pinned systemd/GPIO/Si5351 command-provider evidence,
an optional uninstalled service-unit template, and fake-only cross-platform
tests. See [helper deployment](docs/development/helper-deployment.md). The
`validate-helper-deployment` command reads local configuration and hashes local
fake or staged files only; it performs no SSH, service, GPIO, I2C, SDR, or RF
operation. Actual Raspberry Pi OS behavior remains an actual-host gate.

A bounded real-time, hardware-free qualification simulator exercises local
child deadlines, synthetic carrier analysis, compact CF32-to-WAV conversion,
three fake-decoder invocations, cleanup precedence, and immutable packaging.
It always records `qualification_claim: false` and can never return
`qualified`. See [bounded simulator](docs/development/bounded-simulator.md).

A distinct hardware-free receiver integration coordinator now prepares the
future `wspr5` physical-capture lifecycle without reusing or weakening the
transmitter coordinator. It uses only sealed fakes, requires ephemeral
receiver-only authorization, registers cleanup before simulated acquisition,
authenticates exact-count RF-off evidence, and can never qualify a transmitter.
See [receiver integration](docs/development/receiver-integration.md). SSH and
physical SDR operations remain unavailable until separately authorized.

A sealed hardware-free transmitter lifecycle now prepares owned-process,
service-restoration, and GPIO/Si5351 quiescence evidence without launching
WsprryPi or touching a host. It always records no RF and no qualification
claim. See [transmitter lifecycle](docs/development/transmitter-lifecycle.md).

Slice 3 evidence records canonical absolute artifact paths so validation is
independent of the caller's current working directory. Acquired audio and
decoder evidence is authenticated against the retained profiles, capture
metadata, exact sample timing, tool identity, and decoder-created artifacts.
Carrier evidence similarly retains and revalidates hashed profile, capture,
and IQ artifacts. Audio verification deterministically regenerates the full
PCM payload, and decoder data directories are rolled back if evidence cannot
be published.
RF-off and RF-on must be distinct capture, metadata, and IQ artifacts, and each
capture metadata output path must resolve to its authenticated IQ file.
Decoder evidence separately records expected-identity presence and intended
positive-target signal presence.

Slice 4 adds bounded structured local child execution and ownership-aware mock
lifecycle supervision. SSH behavior uses a deterministic in-process fake that
cannot launch OpenSSH or connect; service and backend inspectors
remain mocks. Successful orchestration is `inconclusive` and cannot qualify
hardware.

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
