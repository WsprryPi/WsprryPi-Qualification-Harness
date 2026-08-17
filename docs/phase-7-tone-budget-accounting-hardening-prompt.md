# Phase 7 tone-budget accounting hardening prompt

## Objective

Correct the carrier-only Phase 7 tone scheduler so controller scheduling jitter
cannot falsely classify an otherwise bounded tone pattern as exceeding its
cumulative RF-on authorization. Preserve the exact per-cycle and aggregate RF
safety bounds, review the correction adversarially, publish the reviewed
revision, and construct a fresh hardware-free candidate for separate digest
authorization.

## Verified context

- The single-use plan
  `df7adbcb45d552988ae53d2d10a72cd236175a187a80ede302c8a5d6a88194ca`
  ran once and is consumed.
- Its immutable session passed preflight, emitted three bounded tones, restored
  services, and verified GPIO4 quiescence, but aborted before analysis with
  `RealSessionError: tone pattern exceeded its cumulative RF-on bound`.
- Retained transmitter logs recorded approximately 1.020082, 1.913350, and
  1.872182 seconds of transmission, about 4.806 seconds total and below the
  authorized six-second maximum.
- The implementation accumulated controller-side monotonic time from before a
  remote process-start request and rejected any total over six seconds with a
  one-nanosecond tolerance. That measurement includes transport and scheduler
  latency and is not the remote child's RF-on interval.
- Each tone child is independently owned by the remote helper and has a hard
  timeout equal to the resolved per-cycle `on_seconds` value.

## Scope and requirements

1. Replace the jitter-sensitive elapsed-time comparison with deterministic RF
   budget reservation based on the exact resolved tone schedule.
2. Before each transmitter launch, fail closed if reserving that cycle's
   `on_seconds` would exceed `maximum_rf_on_seconds`.
3. Preserve absolute enable/disable cadence, the remote per-cycle hard timeout,
   intentional owned-stop verification, capture cancellation, cleanup, service
   restoration, overall deadline, and final backend quiescence.
4. Add regression coverage proving that scheduler overshoot cannot cause a
   false cumulative-budget abort and that an over-budget schedule is rejected
   before the offending transmitter launch.
5. Keep the portable core free of new platform assumptions or dependencies.
6. Preserve the consumed run and all historical evidence unchanged.

## Validation and adversarial review

- Run focused tests, formatting, lint, type checking, the complete unit suite,
  hardware-free native CTest, distribution builds, provenance checks, and
  source/package schema equality checks where applicable.
- Independently challenge whether the change could lengthen a child lifetime,
  permit another cycle, weaken cancellation, omit cleanup, or confuse planned
  time with measured RF evidence. Correct every actionable finding and repeat
  validation until clean.
- Require green GitHub Actions on macOS, Ubuntu, and native Windows at the
  resulting revision.

## Safety boundary and non-goals

- Do not start or stop services, open an SDR, inspect or configure GPIO, run
  WsprryPi, generate a tone, or emit RF while implementing and reviewing this
  correction.
- Do not reuse the consumed run ID, work directory, candidate bytes, or digest.
- Do not infer calibrated power, frequency accuracy, spectral compliance, or
  hardware qualification from the prior run.
- Do not modify WsprryPi, its submodules, sibling repositories, or historical
  evidence.

## Publication and exit criteria

Commit and push only attributable repository changes on the current branch
after staged-diff review. Do not rewrite history, force-push, switch branches,
open a pull request, or publish candidate artifacts to Git.

Exit when the correction is clean and portable, CI is green, and a fresh
exact-revision candidate has passed only hardware-free validation in new
isolated wspr4/wspr5 roots. Report its exact canonical SHA-256 and stop for
separate authorization. No live retry belongs to this slice.
