# Phase 7 GPIO drive and early-exit hardening execution prompt

## Objective

Correct the two offline harness defects demonstrated by the sealed Phase 7 run
`20260816T203938Z-wspr4-wspr5-phase7-tone`, without performing another live,
receiver, service, GPIO, or RF operation.

Keep nominal transmitter power metadata distinct from the legacy Raspberry Pi
GPIO clock hardware drive setting. Accept the WsprryPi legacy GPIO contract of
integer levels `0` through `7`; use level `0` for the next candidate. Level `1`
is a later operator choice only if retained carrier evidence shows that more
drive is needed. Preserve Si5351's distinct integer range of `1` through `4`.

Also correct lifecycle accounting when the transmitter exits before the
intentional stop: if the helper positively verifies that its owned handle is
gone, remove that handle from cleanup ownership while still failing the tone
cycle because it did not remain running until the planned stop. Do not turn an
early exit into a passing transmission.

## Verified context

- The failed application plan passed nominal `23 dBm / 200 mW` metadata as
  `--gpio-power-level 23`.
- WsprryPi rejected that argument before generating a tone because legacy GPIO
  drive accepts only `0` through `7`.
- The stop evidence recorded `running_before_stop: false` and
  `cleanup_verified: true`; final GPIO quiescence and service restoration were
  independently verified, but stale ownership accounting produced
  `cleanup_failed`.
- The sealed run is historical evidence and must not be changed or committed.

## Scope and requirements

1. Update source and packaged schema copies so GPIO drive accepts only `0..7`
   and Si5351 drive accepts only `1..4`.
2. Add application-shim semantic checks with backend-specific error messages,
   so invalid values fail before any process can start even apart from schema
   validation.
3. Prove GPIO level `0` produces `--gpio-power-level 0` and remains distinct
   from `identity.power_dbm` and other nominal-power metadata.
4. When a stop request reports verified helper cleanup, release its owned
   handle. Continue requiring `running_before_stop: true`, cancellation,
   no timeout/disconnect, and verified cleanup for a successful intentional
   carrier stop.
5. Preserve genuinely unverified handles for cleanup retry and failure
   reporting.
6. Keep canonical and packaged schemas byte-identical.
7. Document that the next candidate uses GPIO drive `0`; do not generate or
   authorize that candidate in this slice.

## Constraints and non-goals

- No live run, RF output, GPIO access, SDR access, SSH, service changes, host
  installation, or evidence mutation.
- No automatic escalation from GPIO level `0` to `1` or higher.
- Do not claim calibrated output power, carrier success, or qualification.
- Do not alter WsprryPi or any sibling repository.
- Do not weaken cleanup, quiescence, digest, deadline, or authorization gates.
- Do not retry the consumed Phase 7 authorization.

## Validation and evidence

Run focused tests for application plans, live adapters, resolved plans, and
schema synchronization. Then run formatting, lint, typing, the complete test
suite, package build, native CMake/CTest with Soapy disabled, provenance
verification, schema-copy comparison, and `git diff --check`.

Independently inspect the resulting diff for unit conflation, backend range
leakage, false successful-stop classification, premature owned-handle release,
cross-platform regressions, or any path that could enable RF. Correct every
actionable finding and repeat the relevant checks until clean.

## Exit criteria

- GPIO `0..7` and Si5351 `1..4` are enforced consistently.
- Nominal dBm metadata cannot become a backend drive argument implicitly.
- A verified early exit remains a failed tone cycle but no longer creates a
  false cleanup failure solely through stale ownership.
- An unverified cleanup remains fail-closed.
- All applicable offline checks pass and the worktree contains only the
  reviewed hardening slice.
- Commit and push only the attributable slice on the current branch; do not
  create a branch, rewrite history, force-push, open a PR, or run hardware.
