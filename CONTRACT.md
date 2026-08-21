# WsprryPi Qualification Harness Contract

## 1. Purpose and capabilities

This repository provides a reusable, cross-platform maintainer harness that coordinates WsprryPi
transmitter qualification, exact-count complex-IQ capture, carrier analysis,
independent WSJT-X `wsprd` decoding, lifecycle verification, and portable
result packaging.

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

## 3. Harness capabilities

Use a Python 3.11+ package for CLI orchestration, schemas, analysis, process
supervision, SSH coordination, manifests, and reporting. Use CMake for the
small C++ SoapySDR capture helper so it can be built on all target platforms.

The harness provides:

1. profile loader and JSON Schema validation;
2. capability and dependency discovery;
3. local and SSH command transports;
4. application-plan adapters that bind the target executable, source identity,
   backend, output, drive, mode, and structured arguments without embedding
   target implementation details;
5. local SoapySDR capture adapter;
6. exact-sample-count CF32 capture helper;
7. RF-silence and continuous-carrier analysis;
8. WSPR-slot planning and bounded three-frame orchestration;
9. CF32 translation, per-slot WAV generation, and `wsprd` execution;
10. tone, QRSS, FSKCW, and DFCW reference generation, symbol-spacing, drift,
    timing, transition, replay, and mock lifecycle analysis;
11. cleanup supervisor and backend-specific quiescence verification;
12. immutable-per-run result bundle and summary generation.

Capability reporting describes only operations supplied by this harness. A
target backend name in a plan identifies what is being tested; it does not imply
that the harness implements the target's synthesizer, GPIO controller, kernel
driver, or application internals.

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

Receiver authorization and RF-path resolution are separate. An operator may
record either single-run or universal authorization for receiver-only access,
but every live run must still record the current antenna state, termination,
attenuation, filter state, and safe-input basis. Universal authorization never
imports stale RF-path facts and never authorizes transmitter operation.

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
contrast, and best-20-Hz share. When the receiver is not frequency-calibrated,
the gate uses bounded relative acquisition: the strongest transmitter-added
feature must be within 500 Hz of the requested frequency and at least 10 dB
above its RF-off power. The historical 100-Hz offset and 50-percent best-20-Hz
thresholds remain recorded as nominal diagnostics; they are not calibrated
frequency or thermal-stability claims.

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

## 8. Result bundle

Each run creates a new, never-reused UTC-and-test-ID directory containing:

- requested and fully resolved profiles;
- tool, OS, package, decoder, SoapySDR, source, and submodule identities;
- preflight, session, transmitter, capture, decoder, analysis, and cleanup logs;
- raw IQ or its durable location, byte size, format, sample count, and SHA-256;
- RF-off/on carrier results;
- per-slot WAV files and complete `wsprd` output;
- frame/tone/transition results when run;
- `result.json`; and
- a SHA-256 manifest covering retained artifacts.

Run directories are operational outputs, not repository content. This harness
does not retain target qualification evidence in Git. After review, move any
records that must be preserved to the target project or another approved
evidence store. Raw-IQ retention is controlled by that store's policy; do not
commit raw IQ, copied host archives, or target-specific evidence anchors here.

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

Hardware-free validation precedes live work. The harness test suite covers:

- schema and profile tests;
- UTC slot and sample-boundary tests;
- synthetic CF32 carrier, comb, silence, clipping, and wrong-frequency cases;
- known WSPR decode success/failure fixtures where redistribution permits;
- conjugate-image and wrong-identity cases;
- short-read and overflow simulation;
- cancellation, timeout, child-process, and cleanup-failure injection;
- golden result and manifest tests; and
- replay validation against temporary or operator-supplied captures outside
  the source repository.

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

## 12. Change acceptance

Accept changes only when applicable portable workflows and CI pass, the capture
helper builds on each supported OS, failure injection covers cleanup behavior,
documentation matches the checked-out capabilities, and every hardware claim is
supported by a separately authorized live test of that exact combination.
