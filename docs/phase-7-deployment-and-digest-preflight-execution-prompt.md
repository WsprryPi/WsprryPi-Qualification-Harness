# Phase 7 deployment and digest-preflight execution prompt

## Objective

Stage the reviewed carrier-only controller and its exact machine-local inputs
without transmitting, preserve all unrelated work on `wspr4` and `wspr5`, and
produce one byte-exact resolved Phase 7 plan and SHA-256 for separate operator
authorization. This slice ends before the live command is invoked.

## Verified starting context

- Harness revision `d8dc53ec03e7879d0f5781a74d6a23cd33bf9dd8` is pushed and
  green on macOS, Ubuntu, and native Windows with Python 3.11 and 3.13.
- `wspr4` is the Pi 4 legacy-GPIO transmitter host. Its production
  `wsprrypi.service` is active and must not be replaced. A dedicated executable
  copy already exists at
  `/home/pi/wspq-phase7-20260816/bin/wsprrypi-0bb9600-f0910e5f`.
- `wspr5` is the local RSP1B receiver/controller host. Its production
  `wsprrypi.service` and SoapySDR server are active and must remain unchanged in
  this slice.
- The conducted path facts supplied by the operator are: antenna disconnected;
  GPIO4 connected directly to the SDR through two 10 dB attenuators; 20 dB
  total attenuation; no inline filter. The confirmed safe-input wording is
  `source and attenuation are operator confirmed`.
- The exact tone request is legacy Raspberry Pi GPIO clock on GPIO4 at
  14,097,100 Hz, 200 mW, with three cycles of two seconds off and two seconds
  on, at most six seconds aggregate RF-on and a 60-second overall deadline.
  Gate D is not applicable to this Pi 4 legacy-GPIO candidate.
- The earlier digest `07b9c442f519b4fe7a216bf44ff7e0286e64f3b6449eea6e2c069942b747a948`
  is stale and must not authorize the new controller or plan.

## Authorized work

1. Inspect both hosts read-only for identity, current processes, service state,
   repository revisions, binary identities, and ongoing work.
2. Build the exact pushed harness revision and stage it only in a new,
   phase-specific directory on `wspr5`.
3. Reuse or copy the dedicated `wspr4` executable without changing the
   production executable, installation, repository, configuration, or service.
4. Create new phase-specific profiles, CW plan/events, and helper
   configurations. Do not overwrite prior plans, evidence, or shared helper
   configuration.
5. Pin absolute paths, hashes, versions, host keys, source revisions, backend,
   output, receiver identity/settings, RF path, services, deadlines, cleanup,
   quiescence, and analyzer revision in the resolved plan.
6. Validate schemas and semantics locally on `wspr5`; verify every staged file
   and hash from the host on which it will execute.
7. Report the canonical resolved plan and its SHA-256, then stop for a new,
   exact digest-bound authorization.

## Prohibited work

- Do not invoke `run-cw-live-tone`, supply either live-enable flag, or enter an
  operator digest confirmation.
- Do not stop, start, restart, reload, enable, or disable a service.
- Do not open or reconfigure the SDR, start an IQ capture, execute the
  transmitter, access GPIO registers, generate a clock, or emit RF.
- Do not alter either WsprryPi checkout, the installed binaries or INI files,
  the copied executable's bytes, prior evidence, or another maintainer's work.
- Do not claim calibrated power, spectral compliance, qualification, or live
  readiness from a software/deployment preflight.

## Fail-closed review

Reject the candidate if host identity, source revision, executable/configuration
hash, helper protocol, receiver identity, RF path, service ownership, expected
event sequence, sample count, deadline, cleanup reserve, host key, or schema
semantics is unresolved or contradictory. Treat active unrelated work as a
non-interference blocker. Independently verify that the preflight made no
service, SDR, GPIO, transmitter, or RF change and that the stale digest cannot
match the new plan.

## Exit criteria

The exact controller, dedicated transmitter executable, helpers, profiles,
mode contract, receiver settings, source revisions, safety facts, and cleanup
contract are staged and hash-pinned in one validated resolved plan. Both hosts
retain their initial services and processes, no RF is emitted, and the new
SHA-256 is reported for separate authorization. No live execution follows in
this slice.
