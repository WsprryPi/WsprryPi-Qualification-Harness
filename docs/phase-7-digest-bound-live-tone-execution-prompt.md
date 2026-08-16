# Phase 7 Digest-Bound Live-Tone Execution Prompt

## Objective

Execute the already staged Phase 7 conducted live-tone candidate exactly once,
but only after the operator separately authorizes the exact canonical plan digest
shown below. Preserve fail-closed behavior, bounded RF time, complete cleanup,
service restoration, and immutable evidence.

## Authorized candidate

- Controller revision: `ed96a6de8b932e9df94a9370ee251c392102c64b`
- Canonical live-plan SHA-256:
  `655396710b51b37837f8d34f710a2b5164b43a5fbb7ed08303309233ced608a3`
- Local plan-file SHA-256:
  `c94e499e2635b77a18afb9a02d6bf0d3307d2de53f754a42f7ef0b1bf48f108a`
- Helper configuration digest:
  `41fae143b22193a9fbd1d561ead195025c045010833e364b8be0e175c12c4c4b`
- Receiver execution copy:
  `/home/pi/wspq-phase7-ed96a6d/config/resolved-plan.json` on `wspr5`
- Dedicated transmitter executable:
  `/home/pi/wspq-phase7-20260816/bin/wsprrypi-0bb9600-f0910e5f` on `wspr4`
- Dedicated transmitter executable SHA-256:
  `f0910e5fd14663f0b8219099fd5efb4cb2d45e58a22aa75e646aadcfa1536725`

Any digest, revision, executable, device, topology, or setting mismatch cancels
the run. Do not regenerate or reinterpret the plan under this authorization.

## Physical and RF contract

- Transmitter: `wspr4`, Raspberry Pi 4 legacy GPIO clock, GPIO4
- Frequency: 14,097,100 Hz
- Nominal configured output: 23 dBm (200 mW)
- Pattern: 3 cycles, each 2 seconds off followed by 2 seconds on
- Maximum aggregate RF-on duration: 6 seconds
- Expected complete pattern duration: 14 seconds
- Overall session deadline: 60 seconds
- Receiver: `wspr5`, SDRplay RSP1B serial `2404058C60`
- Receiver center frequency: 14,122,100 Hz
- Sample rate: 250,000 samples/second
- Gain: fixed 10 dB
- Capture: 3,500,000 CF32 samples
- Antenna: disconnected
- Routing: direct conducted connection
- Attenuation: two 10 dB attenuators, 20 dB total
- Inline filter: none
- Safe-input basis: `source and attenuation are operator confirmed`
- Gate D: not applicable to this Raspberry Pi 4 legacy-GPIO candidate
- Mode: carrier-only `TONE`; zero frames and zero decodes

This run can evaluate bounded carrier presence, drift, timing, cleanup, and
portability of the evidence path. It cannot establish calibrated transmitter
power, spectral compliance, antenna performance, WSPR decoding, or general
hardware qualification.

## Separate authorization gate

Do not stop services, open or configure the physical SDR, touch GPIO, start the
transmitter executable, or generate RF until the operator sends this exact
authorization for this exact digest:

> I authorize only the Phase 7 bounded live-tone plan with SHA-256
> 655396710b51b37837f8d34f710a2b5164b43a5fbb7ed08303309233ced608a3.

A general instruction to continue, execute, commit, or push is not a substitute
for this digest-specific authorization.

## Pre-run verification after authorization

Immediately before execution, independently verify and record:

1. The controller checkout is still the stated revision and its tracked working
   tree is clean.
2. The local and receiver plan copies have the stated byte SHA-256 and resolve
   to the stated canonical live-plan digest.
3. The dedicated transmitter executable has the stated SHA-256 and no shared
   WsprryPi executable will be replaced or modified.
4. The transmitter and receiver host identities, SDR USB identity, physical
   routing, attenuation, antenna-disconnected state, and lack of inline filter
   match this prompt.
5. GPIO4 is input/unowned and no conflicting helper, capture, or transmitter
   process is running.
6. Record the initial states of `wsprrypi.service` on `wspr4` and
   `soapyremote-server.service` on `wspr5` without changing either service yet.
7. Select new, unique receiver work and run directories; never reuse or
   overwrite an earlier evidence directory.

Fail closed and produce no RF if any check is false, ambiguous, or cannot be
recorded.

## Bounded execution

Run the controller from `wspr5` using the staged environment and a new unique
work-directory name:

```text
/home/pi/wspq-phase7-ed96a6d/venv/bin/python -m wsprrypi_qualification run-cw-live-tone /home/pi/wspq-phase7-ed96a6d/config/resolved-plan.json /home/pi/wspq-phase7-ed96a6d/runs --work-directory /home/pi/wspq-phase7-ed96a6d/work/<NEW-UNIQUE-NAME> --ssh /usr/bin/ssh --operator <OPERATOR> --enable-live-tone --enable-rf
```

When the controller requests confirmation, enter only the exact canonical
live-plan digest authorized above. Permit the controller to stop and later
restore only `wsprrypi.service` on `wspr4` and
`soapyremote-server.service` on `wspr5`, and only for this bounded session.

If the operator invokes the emergency procedure, if the deadline expires, or if
any unexpected condition occurs: stop RF immediately, perform cleanup, restore
the owned service states, verify backend-specific quiescence, and notify the
operator. A cleanup or restoration failure makes the run unsuccessful.

## Validation and evidence

Preserve the immutable run directory and report:

- plan, configuration, source, executable, host, SDR, and tool identities;
- full controller, transmitter, capture, and analyzer logs;
- exact sample-count result and capture metadata;
- carrier, drift, and timing measurements with their gate results;
- aggregate RF-on time and overall elapsed time;
- initial and final service states;
- GPIO/helper/transmitter quiescence after cleanup;
- final classification, including preflight, capture, analysis, abort, timeout,
  and cleanup distinctions; and
- hashes and paths for the complete evidence bundle.

Independently review the evidence for contradictions or missing safety facts.
Resolve actionable harness defects only through a separate reviewed change; do
not repeat RF under this authorization.

## Exit criteria

The step is complete only when the exact authorized candidate either:

1. completes once within all bounds, restores both hosts, proves quiescence, and
   yields reproducible immutable evidence; or
2. fails closed with no RF, or stops safely after RF enable, while preserving a
   truthful failure or inconclusive record and restoring both hosts.

No second transmission is authorized. The result remains a bounded Phase 7
carrier-path observation, not a calibrated-power, spectral, WSPR, or general
qualification claim.
