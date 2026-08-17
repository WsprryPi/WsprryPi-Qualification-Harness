# Phase 7 required-service candidate deployment prompt

## Objective

Deploy the reviewed required receiver-service lifecycle revision into new,
isolated wspr4/wspr5 staging roots and seal a fresh drive-0 Phase 7 live-tone
candidate that requires the local SDRplay API service during SoapySDR capture.
Report the exact canonical plan SHA-256 and stop for separate authorization.

## Verified context

- Harness revision `5ebc970845a174bebff0a2cc6a448fa672a3f0ca` is pushed and green on
  macOS, Ubuntu, and native Windows with Python 3.11 and 3.13.
- SoapySDR identifies the connected RSP1B as serial `2404058C60` when
  `sdrplay.service` is active.
- The capture helper runs locally on wspr5 through the SoapySDR `sdrplay`
  module. SoapyRemote is not part of this topology.
- The preserved RF path is direct conducted GPIO4-to-RSP1B through two 10 dB
  attenuators, with no antenna and no inline filter.
- Prior run directories and authorizations are consumed and immutable.

## Candidate contract

- Transmitter: wspr4 legacy Raspberry Pi GPIO clock, dedicated copied
  executable, GPIO4, 14,097,100 Hz, drive 0.
- Tone: three cycles, 2 seconds off and 2 seconds on, no more than 6 seconds
  total RF-on and 60 seconds overall.
- Receiver: wspr5 local SoapySDR capture, RSP1B serial `2404058C60`, CF32 at
  250 ksps, 200 kHz bandwidth, fixed 10 dB gain, AGC and bias tee disabled.
- RF path: antenna disconnected, direct conducted connection, two 10 dB
  attenuators, 20 dB total, no filter, safe-input basis `source and
  attenuation are operator confirmed`.
- Receiver service policy: `sdrplay.service` must appear in both
  `services.receiver` and `services.receiver_required`; helper allowlists must
  bind the same service. `soapyremote-server.service` must not be substituted.
- Analyzer source revision: the exact deployed harness revision.

## Execution requirements

1. Inspect current branch/worktree and read both hosts for active services,
   processes, existing staging roots, and non-interference blockers.
2. Build the exact committed wheel and create new unique staging roots without
   altering or reusing prior roots, evidence, virtual environments, or runs.
3. Use a separate helper executable path on each host and retain the dedicated
   copied WsprryPi executable on wspr4.
4. Compute helper hashes from the deployed bytes, then regenerate helper
   configuration hashes, helper-plan digest, plan-file hash, and canonical plan
   digest. Never authorize one digest while deploying another.
5. Validate every profile, CW tone plan/events contract, schema, and plan-only
   resolution with zero external calls.
6. Run only the non-hardware helper/hash/ownership preflight. Leave RF-idle GPIO
   inspection, service mutation, SDR access, and live execution for the later
   digest-authorized command.
7. Verify helpers close, no owned process remains, `wsprrypi.service` retains
   its initial state, and `sdrplay.service` retains its initial state.
8. Independently review exact deployed paths, hashes, service semantics,
   revision bindings, RF facts, deadlines, and cleanup wording; resolve every
   actionable finding before reporting the digest.

## Safety boundary and non-goals

- Do not start or stop any service, open the SDR, inspect or configure GPIO,
  launch WsprryPi, or emit RF.
- Do not reuse a prior candidate digest or authorization.
- Do not claim calibrated frequency, power, spectral compliance, WSPR decode,
  or hardware qualification.
- Do not modify sibling repositories or prior immutable evidence.

## Validation and exit

Run applicable repository checks for any committed prompt change, inspect the
complete staged diff, commit and push only attributable changes, and confirm a
clean synchronized branch. Exit only when exact candidate bytes are deployed,
all non-hardware checks are clean, services and processes remain unchanged,
and the canonical SHA-256 is reported with an explicit requirement for a new
separate digest authorization.
