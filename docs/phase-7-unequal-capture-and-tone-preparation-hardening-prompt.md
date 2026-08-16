# Phase 7 unequal-capture and tone-preparation hardening prompt

## Objective

Resolve the two harness defects demonstrated by the sealed drive-0 Phase 7 run
`20260816T215145Z-wspr4-wspr5-phase7-drive0`, using only offline development and
replay. Do not transmit or mutate the sealed evidence.

First, allow carrier analysis to compare independently exact RF-off and RF-on
captures of different lengths. Average every complete FFT block in each file
independently in linear power, then subtract the averaged RF-off spectrum from
the averaged RF-on spectrum. Never truncate the longer capture, repeat the
shorter capture, or imply equal statistical weight.

Second, remove service-stop latency from the scheduled tone cadence. After
cleanup registration and before starting the RF-on capture epoch, stop only the
authorized transmitter service if it was initially active and verify it is
stopped. Scheduled tone transitions may then launch only the already-prepared
dedicated transmitter. Preserve absolute deadlines and fail closed if a launch
cannot meet the cadence. Do not claim that process launch is proof of RF
activation; retained transmitter logs and IQ remain the measurement evidence.

## Verified context

- The sealed run captured exactly 500,000 RF-off samples and 3,500,000 RF-on
  samples with zero clipping, overflow, or timeout.
- Carrier analysis aborted solely because it required those independently
  planned counts to be equal.
- All three owned transmitter stops and final cleanup/quiescence passed.
- The first tone log reported only 0.210510 seconds, while later cycles reported
  1.907589 and 1.868919 seconds.
- Production code stopped and re-inspected `wsprrypi.service` inside the first
  on-transition, consuming most of that first two-second absolute window.

## Requirements

1. Retain strict per-capture validation of requested count, retained count,
   byte size, format, settings, receiver identity, clipping, overflow, timeout,
   and artifact hash.
2. Permit the validated RF-off and RF-on counts to differ.
3. Record each count and each FFT-block count in evidence. Require at least one
   complete block per capture.
4. Preserve distinct-capture checks and deterministic recomputation.
5. Prepare transmitter service ownership once, after cleanup registration and
   before RF-on capture/tone cadence begins.
6. Forbid `_begin_transmitter` from silently performing an unprepared service
   transition during a scheduled on-window.
7. Restore only services actually changed by the harness during final cleanup.
8. Add regression tests for unequal acquired counts and for nonzero service
   preparation latency that does not shorten the scheduled first tone.
9. Update maintained documentation that incorrectly requires equal counts.
10. Reanalyze the sealed drive-0 captures offline after the fix. Treat this as
    replay evidence only; do not rewrite the sealed run or upgrade its recorded
    `aborted` status.

## Constraints and non-goals

- No RF, GPIO output, physical SDR access, service operation, or live retry.
- No modification or replacement of WsprryPi or its dedicated executable.
- No alteration of the sealed evidence bundle or `SHA256SUMS`.
- No calibrated-power, spectral-compliance, exact RF-activation, or hardware-
  qualification claim.
- No automatic GPIO drive increase from level `0`.
- Do not weaken cleanup, quiescence, ownership, digest, or deadline gates.

## Validation and adversarial review

Run focused carrier/live-adapter tests, formatting, lint, typing, the complete
test suite, package builds, hardware-free native CMake/CTest, provenance and
schema synchronization checks, and `git diff --check`. Replay the sealed IQ
through the corrected analyzer without writing inside its immutable directory.

Review the diff for hidden truncation, unequal-weight arithmetic, stale path or
metadata acceptance, service mutation before cleanup registration, service
restoration of an unchanged unit, cadence drift, cross-platform assumptions,
or any new RF path. Correct every actionable finding and repeat relevant checks.

## Exit criteria

- Independently exact unequal captures produce deterministic carrier evidence.
- First-cycle service preparation occurs outside the scheduled tone epoch.
- Scheduled launches cannot silently stop services or absorb preparation time.
- The sealed drive-0 evidence can be analyzed offline without mutation.
- All offline and portable checks pass.
- Commit and push only the reviewed slice on the current branch; do not create
  a branch, force-push, open a PR, or perform another transmission.
