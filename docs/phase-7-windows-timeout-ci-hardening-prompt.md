# Phase 7 Windows Timeout CI Hardening Prompt

## Objective

Restore green native-Windows CI for the Phase 7 candidate without weakening
the uncertain-service-mutation safety contract. Reproduce and classify the
single Windows Python 3.13 failure, apply the smallest deterministic correction,
adversarially exercise the affected behavior, and require all macOS, Ubuntu,
and native Windows matrix jobs to pass at the resulting revision.

## Verified failure

- GitHub Actions run: `31967161571`
- Revision: `8bfa17f11d138b32729c63b2d9f1d09e236eb8bb`
- Failing job: `validate (windows-latest, 3.13)`
- Failing test:
  `test_uncertain_systemd_mutation_remains_restorable[timeout-after-timeout-0.2]`
- Observed mismatch: the fixture state file contained an empty string instead
  of `inactive` after the provider deadline expired.
- Other results: 829 passed and 9 skipped in that job; the other five matrix
  jobs passed.

## Root-cause requirement

Distinguish an implementation restoration defect from a fixture scheduling
race. The `timeout-after` fixture writes the requested state and then sleeps
for five seconds. Its 0.2-second provider deadline can expire during native
Windows Python startup, before the fixture reaches the state write. That does
not exercise the intended uncertain-mutation case.

Do not change production restoration logic unless independent evidence shows
it is defective. Do not weaken assertions that require:

- a timed-out mutation to remain classified as uncertain;
- `change_attempted` and the desired state to remain recorded;
- cleanup to restore the initial state;
- restoration evidence to be complete; and
- a second restoration attempt to be rejected.

## Focused correction

For the `timeout-after` parameter only, replace the 0.2-second provider budget
with the existing two-second test budget used by the neighboring provider
failure cases. Keep the fixture's five-second post-mutation sleep unchanged.
This preserves a real timeout while allowing sufficient native-Windows process
startup time for the mutation marker to be written first.

Do not add platform skips, retries that conceal failures, unconditional sleeps
after the test, broad timeout inflation, or Windows-specific production paths.

## Validation

Run, in order:

1. the exact affected parametrized test repeatedly;
2. the complete deployment unit-test module;
3. the full portable unit suite;
4. formatting, lint, and type checks;
5. staged-diff and repository-hygiene review; and
6. pushed GitHub Actions CI across macOS, Ubuntu, and native Windows for Python
   3.11 and 3.13.

Adversarially confirm that the timeout case still reports `cause: timeout`,
`mutation_performed: null`, and requires successful restoration. Confirm the
`fail-before`, nonzero-after-mutation, and malformed-after-mutation cases retain
their distinct behavior.

## Scope and safety

This is hardware-free CI work. Do not access either Pi, services, GPIO, SDR, or
RF. Do not alter the staged Phase 7 plan or its digest. No live authorization is
implied.

## Exit criteria

Exit only when the race is deterministically corrected, safety assertions are
unchanged, local checks pass, the minimal change is committed and pushed, and
all six GitHub Actions matrix jobs are green at the resulting revision. Until
then, the Phase 7 candidate must not advance to live RF authorization.
