# Phase 7 publication hardening and fresh connected-path candidate prompt

## Objective

Close the two production defects exposed by the path-invalid drive-0 session,
prove the complete retained analyzer and publication path offline, review the
slice adversarially until clean, and then construct a fresh hardware-free
candidate for the operator-confirmed connected path.

## Verified context

- The previous single-use candidate is consumed. Its cable was unplugged, so
  its failed relative acquisition is path-invalid rather than a transmitter
  verdict, and its quarantined evidence must remain unchanged.
- A failed tone carrier produced a maintained mode-gate document with
  `mode_gate: not_applicable`, but the coordinator rejected that schema-valid
  tone value instead of recording the carrier failure.
- The byte-exact sealed expected-events provenance copy retains its original
  plan binding. Publication incorrectly treated that provenance payload as the
  active rebound JSON contract and rejected its external dependency.

## Required implementation and evidence

1. Accept the maintained `not_applicable` mode gate for tone while deriving the
   carrier result from authenticated relative-acquisition metrics. A failed
   carrier must classify as `unqualified_carrier` and must never advance.
2. Preserve the exact sealed expected-events bytes as authenticated provenance,
   clearly distinct from the schema-validated, path-rebound analysis contract.
3. Exercise the real IQ analyzer from external sealed plan and expected events
   through retained copies, derived contracts, acquired metadata, observations,
   mode gate, artifact index, publication, manifest-compatible content, and
   relocation validation after the external inputs are unavailable.
4. Cover passing and failing carrier outcomes and ensure all published JSON
   dependencies resolve through authenticated retained artifacts.
5. Review source/derived labeling, mutation and overwrite refusal, paths with
   spaces and native Windows forms, partial failure evidence, classification,
   relocation, manifest coverage, and archive hygiene. Correct every actionable
   finding and repeat validation.

## Safety, scope, and non-goals

- Implementation and review are offline: do not contact wspr4 or wspr5, touch
  services, open an SDR, inspect GPIO, execute WsprryPi, generate a tone, or
  emit RF.
- Do not change WsprryPi, sibling repositories, historical evidence, consumed
  candidates, or qualification status. Do not claim calibrated frequency,
  power, spectral compliance, or transmitter qualification.
- Preserve authorization, deadlines, exact-count capture, cleanup, restoration,
  and backend-quiescence contracts.

## Validation, publication, and next candidate

Run focused and complete tests, formatting, lint, typing, native CTest,
distribution/install checks, provenance checks, archive hygiene, and green
GitHub Actions on macOS, Ubuntu, and native Windows. Commit and push only
attributable changes on the current branch after staged-diff review; do not
rewrite history, force-push, open a pull request, or commit generated evidence.

Only after the revision is green, construct a new hardware-free drive-0 plan:
wspr4 legacy GPIO clock on GPIO4 at 14,097,100 Hz; three cycles of two seconds
off and two seconds on; at most six RF-on seconds and sixty seconds overall;
wspr5 local SoapySDR with SDRplay serial 2404058C60, CF32 at 250 ksps, 200 kHz
bandwidth, gain 10, AGC and bias tee off; antenna disconnected; direct cable
through two 10 dB attenuators; safe-input basis exactly `source, connected
routing, and attenuation are operator confirmed`; relative acquisition only;
Gate D not applicable. Report the canonical SHA-256 and stop for separate
digest authorization. Do not transmit.
