# Phase 7 external CW-contract retention fix prompt

## Objective

Correct the hardware-free evidence-publication defect exposed by the authorized
drive-0 Phase 7 run: live tone analysis must authenticate and retain CW plan and
expected-event inputs that originate outside the fresh work directory. Prove
the correction offline, independently review it, and publish only the focused
repository change.

## Verified failure context

- Authorized plan SHA-256
  `20556d8db35047bed7de2edb0c2f8013cbda7f9f0333f08848e098f556b418f6`
  completed all three bounded tone cycles and exact-count SDR captures.
- Cleanup and final GPIO4 quiescence were verified, and both services returned
  to their original states.
- Relative carrier analysis passed, but the session was correctly classified
  `aborted` because `tone-plan.json` was outside the work-directory subtree
  used to construct portable analyzer references.
- The consumed authorization does not permit a retry or any additional host,
  service, SDR, GPIO, or RF operation.

## Scope and requirements

1. Reproduce the path-containment failure entirely with temporary local files.
2. Identify the narrow production boundary that prepares live CW analyzer
   inputs and evidence references.
3. Before analysis, authenticate external plan and expected-event bytes against
   their sealed size and SHA-256 bindings, then copy them into distinct,
   deterministic paths inside the fresh work directory.
4. Construct acquired-capture references and analyzer inputs from those
   retained copies so generated references are portable and work-root-relative.
5. Fail closed if a source is absent, changes before staging, contradicts its
   binding, or a retained destination already exists.
6. Ensure publication retains the staged contract artifacts and validates all
   JSON dependencies without consulting the original external paths.
7. Add regression tests covering an external source directory, paths containing
   spaces, successful staging, analyzer argument/reference selection, and
   binding tamper rejection.
8. Preserve existing schemas and the canonical resolved-plan digest contract;
   this correction must not broaden live authorization or change measurement
   thresholds, classification, timing, service policy, or cleanup semantics.

## Safety boundary and non-goals

- Perform no SSH, service, SDR, GPIO, transmitter, or RF operation.
- Do not alter or republish the retained aborted evidence.
- Do not construct or authorize another live candidate.
- Do not modify sibling repositories, historical files, or unrelated code.
- Do not infer hardware qualification from offline tests or the aborted run.

## Validation and adversarial review

- Run focused regression tests first, then formatting, lint, type checking, the
  complete unit suite, historical provenance verification, and the bounded
  hardware-free simulator.
- Inspect failure injection and publication validation for regressions.
- Reassess path traversal, symlinks, source mutation, duplicate names,
  Windows/path-with-spaces portability, overwrite behavior, and whether bundle
  validation remains independent of the original source files.
- Resolve every actionable review finding and rerun affected checks.

## Exit criteria

Exit only when the failure is reproducible offline, the focused correction and
regression tests are clean, no hardware boundary was crossed, the staged diff
contains only attributable files, the current branch is committed and pushed,
and macOS, Ubuntu, and native Windows CI status is reported without overstating
any pending job.
