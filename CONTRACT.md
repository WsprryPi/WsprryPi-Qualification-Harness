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
7. RF-silence and continuous-carrier analysis with optional authenticated
   relative-spectrum PNG or SVG plots;
8. WSPR-slot planning and bounded three-frame orchestration;
9. CF32 translation, per-slot WAV generation, and `wsprd` execution;
10. tone, QRSS, FSKCW, and DFCW reference generation, symbol-spacing, drift,
    timing, transition, replay, and mock lifecycle analysis;
11. offline resolved-plan, authorization, three-transaction, aggregate, result,
    and artifact-index contracts for live QRSS, FSKCW, and DFCW coordination;
12. sealed hardware-free QRSS, FSKCW, and DFCW coordinator rehearsal through a
    deterministic fake adapter with lifecycle failure and cancellation injection;
13. digest-authorized live QRSS, FSKCW, and DFCW coordination through the
    authenticated helper, exact-count capture, service, and quiescence adapters;
14. cleanup supervisor and backend-specific quiescence verification;
15. immutable-per-run result bundle and summary generation;
16. frozen SDR Calibration Profile 1.0.0 bindings and receiver-only calibrated
    frequency interpretation for recorded IQ and every maintained live mode.
17. thin typed turnkey route planning, exact-digest confirmation, deterministic
    hardware-free rehearsal, and dispatch to the existing production coordinator
    for every maintained mode.
18. one first-class `complete-test` campaign that accepts transmitter host,
    receiver host, and an exact SDR selector, resolves canonical defaults and
    explicit overrides, prepares all five bounded mode executions,
    and routes TONE, WSPR, QRSS, FSKCW, and DFCW in order through those same
    coordinators with one invocation authorization and one authenticated aggregate.
19. a durable JSON Lines `complete-test` progress stream, flushed after each
    campaign, mode, capture/lifecycle, WSPR-frame, keyed-observation, cleanup,
    and terminal transition and forwarded to the invoking controller.
20. a sealed hardware-free RP1 `complete-test` rehearsal that requires an
    explicit GPIO4 or GPIO20 administrative route, composes five independently
    authenticated mode plans for distinct same-host logical roles, and rejects
    live execution before production adapters are constructed.
21. schema-backed RP1 preflight and operation-lifecycle evidence validators
    that bind route-specific identity, process/lease/generation state, bounded
    drain, endpoint closure, cleanup, GPIO/clock/DMA quiescence, terminal
    silence, and fail-closed result precedence without contacting a host.

The keyed schema/validator layer is validation-only: it exposes no process,
transport, receiver, transmitter, service, or RF operation. Its three
transactions must have independent process, capture, acquisition, analysis, and
artifact identities. An early-stop failure aggregate contains only the
contiguous transactions actually attempted. Qualification requires exactly
three passing transactions; cleanup or quiescence failure has precedence over
measurement success.

The separate production live-keyed coordinator uses that contract and binds the
application-shim argv, executable identity, parent and component target
revisions, receiver identity and settings,
RF path, analyzer revision, external capability artifacts, and named services.
The public command requires both live/RF enable flags, a non-empty operator, and
an exact typed digest confirmation before production adapters are constructed.
Helper deployment configuration is an immutable input to that plan: the plan
binds the helper executable and configuration artifact identities, while the
canonical plan digest is supplied only when the authenticated helper process is
started. The helper rechecks both artifact hashes and rejects any plan digest
embedded in a runtime-bound configuration. A helper configuration used this
way must therefore not embed the digest of the plan that binds it.

Each live-keyed transaction represents exactly one keyed-message transmission;
the three required observations come from three independently owned process and
capture transactions, not repeated messages inside one transaction. Required
receiver services must be an explicit subset of the receiver-side service
allowlist. They may be started only after cleanup is installed and must be
restored to their observed initial state during transaction cleanup. Other
allowlisted services are stopped for the transaction and likewise restored.
When service management requires elevation, the immutable helper configuration
must bind both the service manager and a non-interactive privilege wrapper by
absolute path and SHA-256. The helper rechecks both identities before every
allowlisted operation; an interactive prompt, missing authorization, or changed
wrapper fails before transmitter launch.
Raspberry Pi transmitter processes are never launched directly as the helper
account. The resolved keyed plan and immutable helper configuration must bind a
non-interactive process privilege wrapper independently from WsprryPi. The
helper authenticates the wrapper and the requested WsprryPi executable, checks
that the request names the plan-bound wrapper digest, and constructs only
`WRAPPER -n -- EXACT_WSPRRYPI_ARGV`. Missing privilege, an interactive prompt,
or wrapper/executable/argument substitution fails before RF.
The receiver capture must establish its retained output and complete the
resolved RF-off preamble before WsprryPi is launched. Capture setup failure or
premature capture termination must therefore prevent transmitter launch.
Every QRSS, FSKCW, and DFCW capture must contain the final generated timeline
plus a maintained one-second guard, with the required sample count rounded
upward. Runtime scheduling may redistribute pre- and post-quiet time but must
preserve that guarded capture bound; an under-sized plan fails preflight.
After transmitter launch, a capture-helper or receiver-evidence failure is a
receiver/fixture blockage, not transmitter unqualification. The live keyed
bundle must retain the bounded helper execution diagnostic and any native
failure metadata, including stdout, stderr, return code, timeout/cancellation,
and cleanup state. Partial or rejected IQ is removed and must never be indexed
as a valid capture artifact.

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

Receiver calibration policy is explicit: `required`, `optional`, or `disabled`.
A supplied profile, application request, and application result are authenticated
inputs to the resolved plan and therefore to runtime authorization. Required
calibration must fail before receiver or RF access when absent, expired,
mismatched, outside its validity domain, or not qualification usable. Receiver
calibration preserves indicated measurements and adds estimated-true frequency
and uncertainty; it never changes requested RF, WsprryPi arguments, or
transmitter PPM.

Operator confirmation is runtime evidence and must never be satisfied by a
committed `confirmed: true` or similar profile value.

For `complete-test` only, deliberate invocation with `--enable-rf`, two exact
host names, and one exact SDR selector is the positive confirmation for the
bounded five-mode campaign. The command resolves installed deployment facts
internally and does not require an operator identity. Internal evidence binding
is automatic and is not part of the user interface. Advanced explicit-plan
commands retain their existing confirmation interfaces.

Generated complete-test mode plans, expected events, and resolved profiles are
campaign inputs, not temporary deployment files and not result evidence. They
must be created in a campaign-owned store outside source repositories and
runtime deployment roots before authorization. The resolved campaign binds
that store and retains it while the campaign result or any subordinate result
depends on it. Deployment cleanup must not remove it; disposal is a separate
manual evidence-retention action.

The complete-test progress JSON Lines stream belongs to the invoking control
host, regardless of whether execution is local, receiver-delegated, or launched
from a third system. Its default is a new exclusive file in durable user-state
storage, never an operating-system temporary directory or remote deployment
stage. It remains available for review until explicitly removed. An operator
may select another durable location with `--progress-log`.

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

Target source repositories and linked worktrees are immutable inputs. A file
that a managed child may normalize, migrate, persist, cache, or otherwise write
must be copied with exclusive creation into an authorization-bound runtime
directory outside every discovered or declared Git root before execution. The
child receives only that staged path and runs from an external runtime working
directory. Immediately before launch, the helper rechecks the source and staged
identities, exact arguments, writable paths, working directory, Git executable,
and protected roots. It snapshots repository state without requiring a clean
checkout and compares that exact dirty baseline after every exit path.

Repository mutation is an integrity and cleanup failure. It prevents a
qualification result and is reported without reset, checkout, clean, deletion,
or automatic restoration; operator work may have changed concurrently and must
not be overwritten or concealed. Service restoration is not permitted to erase
or supersede that integrity outcome.

The carrier gate must pass before WSPR frames may run. The harness must never
automatically classify receiver coverage, overload, ownership, or RF-fixture
failure as transmitter unqualification.

## 6. Measurement contract

The baseline capture contract is CF32, 250,000 samples/second, 200 kHz
bandwidth, fixed gain, AGC disabled, bias tee disabled, first read discarded,
exact requested sample count, and explicit overflow reporting. These values are
profile defaults for the preserved bench, not universal requirements.

The carrier gate compares fixed-gain RF-on and RF-off intervals in linear
power, using a Hann window and a documented FFT/averaging contract. Zero-IF
capture must place the complete requested-carrier search window outside the DC
exclusion and inside the usable receiver span. The maintained complete-test
policy tunes the receiver 25 kHz below requested RF. Invalid tuning geometry is
a preflight/configuration failure, not transmitter unqualification.

Carrier qualification selects the strongest resolved feature only inside the
500-Hz target window around requested RF, requires at least 10 dB RF-on/off
contrast, and requires its absolute requested-frequency offset to be at or
below the configured carrier tolerance (100 Hz by default). Record its
requested-frequency offset, contrast, and target-window best-20-Hz share.
Stronger features elsewhere in the captured span remain diagnostic and cannot
redefine the requested carrier. The 50-percent best-20-Hz threshold remains a
nominal diagnostic; neither the carrier gate nor global feature reporting
establishes calibrated power or spectral compliance.

This target-window behavior is carrier-analysis schema version 2. Version-1
evidence records the historical span-wide policy and is not silently
reinterpreted or accepted as version-2 evidence.

When a frozen receiver calibration is applied, retain both the indicated and
estimated-true frequency, expanded uncertainty, selected model segment,
reliability quotient, profile identity, and exact application binding. Derived
FSKCW and DFCW separations must retain their indicated value so a common
calibration correction cannot conceal spacing error.

Only a passing carrier advances to WSPR decoding. A qualifying run contains
one coherent 370-second capture spanning three consecutive bounded frames,
with exactly 92,500,000 samples at 250 ksps and zero overflows. Each frame is
translated to 1500 Hz audio, cut into its correct UTC slot, and independently
decoded with `wsprd`. Qualification requires three consecutive correct,
complete decodes of the expected identity. The resolved outer deadline must
contain the actual wait to the first capture launch, the coherent capture,
three separately bounded frame-analysis intervals, summary validation, result
publication, cleanup, and final quiescence. Receiver setup is derived from the
bound read interval; offline-analysis, summary, and publication deadlines are
derived from exact retained byte counts, required validation/copy passes,
decoder subprocess bounds, and the maintained minimum sequential-I/O
capability. No unexplained fixed allowance or scheduling reserve may
participate in qualification timing. Runtime revalidates the complete bound
against its actual start before installing the deadline.

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
- RF-off/on carrier results and any requested authenticated plot;
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
