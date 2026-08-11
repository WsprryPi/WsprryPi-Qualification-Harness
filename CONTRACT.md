# WsprryPi Qualification Harness Contract

## 1. Purpose

Build a reusable, cross-platform maintainer tool that coordinates WsprryPi
transmitter qualification, exact-count complex-IQ capture, carrier analysis,
independent WSJT-X `wsprd` decoding, lifecycle verification, and durable
evidence packaging.

The harness is an engineering qualification tool, not an operator transmitter
UI, production service manager, spectrum-compliance instrument, or automatic
authorization to radiate.

## 2. Supported control hosts

The orchestration and offline-analysis commands must work on:

- macOS;
- Linux distributions, including Raspberry Pi OS;
- Windows 11 with native Python and OpenSSH; and
- a Raspberry Pi used directly as the control and/or capture host.

WSL may be supported as an additional environment but must not substitute for
native Windows testing.

The implementation must not require Bash, GNU-only utilities, POSIX signals,
systemd, `/tmp`, Unix path syntax, or fork semantics in its portable core.
Platform-specific behavior must be capability-detected and isolated behind
adapters. Unsupported live capabilities must fail before RF is enabled.

## 3. Architecture

Use a Python 3.11+ package for CLI orchestration, schemas, analysis, process
supervision, SSH coordination, manifests, and reporting. Use CMake for the
small C++ SoapySDR capture helper so it can be built on all target platforms.

Required logical components:

1. profile loader and JSON Schema validation;
2. capability and dependency discovery;
3. local and SSH command transports;
4. transmitter adapters for GPIO and Si5351, with future RP1 extensibility;
5. local SoapySDR capture adapter;
6. exact-sample-count CF32 capture helper;
7. RF-silence and continuous-carrier analysis;
8. WSPR-slot planning and bounded three-frame orchestration;
9. CF32 translation, per-slot WAV generation, and `wsprd` execution;
10. optional symbol-spacing, drift, and transition analysis;
11. cleanup supervisor and backend-specific quiescence verification;
12. immutable evidence bundle and summary generation.

## 4. Configuration

Separate stable bench facts from an individual test request.

The bench profile records receiver identity, transport, safe RF-path facts,
sample format/rate/bandwidth, and platform capabilities. The test profile
records transmitter source, backend, output, frequency, gain, calibration,
identity, timing, and gates.

Profiles must be validated before any external process is started. Machine-
local paths and credentials belong in ignored local overrides or environment
configuration, never committed profiles. Avoid implicit project defaults for
device-specific PPM, attenuation, gain, or safe input level.

Operator confirmation is runtime evidence and must never be satisfied by a
committed `confirmed: true` or similar profile value.

## 5. Safety invariants

Live RF is disabled by default. It requires all of the following:

- an explicit command-line opt-in such as `--enable-rf`;
- a validated profile that declares the exact host, backend, output, frequency,
  duration/frame count, receiver, attenuation, termination, antenna state, and
  stopping procedure;
- positive operator confirmation of the resolved plan;
- successful source, dependency, clock, device-identity, ownership, and idle-
  state preflight;
- cleanup handlers installed before transmitter enable;
- hard receiver and transmitter time bounds; and
- a backend-specific post-run quiescence check.

On success, failure, timeout, cancellation, or interruption, cleanup must stop
children, disable output, restore the GPIO/input or Si5351 state, restore only
services the harness intentionally changed, verify no helper remains, and
record the outcome. Cleanup failure overrides an otherwise successful result.

The carrier gate must pass before WSPR frames may run. The harness must never
automatically classify receiver coverage, overload, ownership, or RF-fixture
failure as transmitter unqualification.

## 6. Measurement contract

The baseline capture contract is CF32, 250,000 samples/second, 200 kHz
bandwidth, fixed gain, AGC disabled, bias tee disabled, first read discarded,
exact requested sample count, and explicit overflow reporting. These values are
profile defaults for the preserved bench, not universal requirements.

The carrier gate compares fixed-gain RF-on and RF-off intervals in linear
power, using a Hann window and a documented FFT/averaging contract. Record the
strongest transmitter-added feature, requested-frequency offset, on/off
contrast, and best-20-Hz share. The historical gate required the strongest
feature within 100 Hz and at least 50 percent of resolved transmitter-added
power in the best 20 Hz.

Only a passing carrier advances to WSPR decoding. A qualifying run contains
one coherent 370-second capture spanning three consecutive bounded frames,
with exactly 92,500,000 samples at 250 ksps and zero overflows. Each frame is
translated to 1500 Hz audio, cut into its correct UTC slot, and independently
decoded with `wsprd`. Qualification requires three consecutive correct,
complete decodes of the expected identity.

Successful decode qualifies only the recorded backend, band, transmitter
hardware/profile, source revisions, receiver path, and production settings. It
does not establish antenna-ready spectral compliance, calibrated output power,
harmonic suppression, or another backend/platform.

## 7. Result states

The machine-readable final status must be exactly one of:

- `qualified`;
- `unqualified_carrier`;
- `unqualified_decode`;
- `fixture_blocked`;
- `preflight_failed`;
- `aborted`;
- `cleanup_failed`; or
- `inconclusive`.

Gate outcomes and failure reasons must remain separate fields. Do not flatten
all unsuccessful runs into `failed`.

## 8. Evidence bundle

Each run creates a new, never-reused UTC-and-test-ID directory containing:

- requested and fully resolved profiles;
- tool, OS, package, decoder, SoapySDR, source, and submodule identities;
- preflight, session, transmitter, capture, decoder, analysis, and cleanup logs;
- raw IQ or its durable location, byte size, format, sample count, and SHA-256;
- RF-off/on carrier results and plots;
- per-slot WAV files and complete `wsprd` output;
- frame/tone/transition results when run;
- `result.json`; and
- a SHA-256 manifest covering retained artifacts.

Raw IQ retention is policy-controlled. It may be removed only after required
derivatives are generated and reviewed; its metadata and hash remain.

## 9. External tools

Discover and record, but do not vendor by default:

- WSJT-X `wsprd`;
- FFmpeg, if retained in the conversion path;
- SoapySDR and the selected hardware module;
- CMake and a C++ compiler for capture-helper builds;
- OpenSSH for remote transmitter control.

Record absolute executable paths, versions, hashes where practical, command
lines, and return codes. A missing or incompatible tool produces an actionable
preflight result, never an implicit fallback that changes measurement meaning.

## 10. Test strategy

Hardware-free validation precedes live work. Provide:

- schema and profile tests;
- UTC slot and sample-boundary tests;
- synthetic CF32 carrier, comb, silence, clipping, and wrong-frequency cases;
- known WSPR decode success/failure fixtures where redistribution permits;
- conjugate-image and wrong-identity cases;
- short-read and overflow simulation;
- cancellation, timeout, child-process, and cleanup-failure injection;
- golden result and manifest tests; and
- replay tests using retained historical captures supplied outside Git when
  they are too large to commit.

CI must cover current macOS, Ubuntu, and Windows runners. Raspberry Pi OS and
real SDR/transmitter validation remain separately recorded hardware gates.

## 11. Repository boundaries

This project remains independent of WsprryPi, WsprryPi-UI, WSPR-Transmitter,
Wsprry_Pi_Docs, and SDR-Calibration. It may consume their public interfaces but
must not silently modify, install, start, stop, or update them.

Keep qualification evidence about WsprryPi behavior in WsprryPi research
records when appropriate. Keep operator-facing support changes in the separate
Wsprry_Pi_Docs repository. Treat changes across repositories as separate review,
commit, and push boundaries.

## 12. Completion standard

Do not call the project complete until portable offline workflows and CI pass,
the capture helper builds on each supported OS, failure injection proves safe
cleanup behavior, documentation is reviewed, and explicitly authorized live
tests validate each claimed host/receiver/transmitter combination.
