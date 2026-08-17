# Phase 7 retained CW-contract rebinding prompt

## Objective

Close the production-path gap that allowed a complete bounded tone lifecycle to
abort during offline mode analysis. Preserve the exact externally sealed CW
contracts, derive an explicitly path-rebound analysis copy, prove the complete
retained-copy analyzer chain offline, and construct a fresh candidate only
after portable review and CI are green.

## Verified context

- The single-use plan
  `ab9b9b42b79f4683015f06923c7ad57cf87706145e271cf90faceb0e0c84138d`
  is consumed and must never be rerun.
- Its live preflight, three bounded GPIO4 tones, exact 3,500,000-sample RF-on
  capture, receiver cleanup, service restoration, and final GPIO quiescence
  all completed successfully.
- Relative carrier acquisition passed with about 76.33 dB contrast and a
  strongest-feature offset of about +159.65 Hz.
- The subsequent tone-mode analyzer aborted because the byte-identical copied
  expected-events document still bound its original plan path while the
  analyzer was supplied a retained private plan copy.
- Existing tests mocked the strict analyzer at this composition boundary and
  therefore failed to exercise the path identity check that rejected the live
  inputs.

## Scope and requirements

1. Authenticate and retain byte-identical copies of both externally sealed CW
   contracts. Never mutate or relabel those source copies.
2. Create a clearly separate derived expected-events document for analysis,
   changing only its plan artifact reference to the authenticated retained plan
   copy and validating the derived document through the maintained schema.
3. Bind acquired metadata and all analyzer outputs to the retained plan and the
   derived expected-events document, while retaining the original sealed
   expected-events document as provenance.
4. Register each created source or derived artifact for failure evidence before
   invoking the next fallible analyzer stage.
5. Add a regression that uses real CW document validation and the real IQ
   analyzer, not a mocked analyzer, from external sealed inputs through copied,
   rebound inputs to completed observations and a mode gate.
6. Preserve the relative carrier gate, exact-count capture contract, runtime
   authorization boundary, deadlines, service lifecycle, cleanup, and backend
   quiescence behavior.

## Validation and adversarial review

- Challenge source-versus-derived labeling, byte preservation, path forms with
  spaces, overwrite refusal, source mutation, partial failure evidence,
  relocation semantics, and complete analyzer-chain validation.
- Run focused and complete tests, format, lint, type checking, native CTest,
  distributions, installed-package checks, historical provenance, and relevant
  schema-copy checks.
- Resolve every actionable finding and require green GitHub Actions on macOS,
  Ubuntu, and native Windows at the resulting revision.

## Safety boundary and non-goals

- Do not contact wspr4 or wspr5, mutate services, open an SDR, inspect or
  configure GPIO, execute WsprryPi, generate a hardware tone, or emit RF during
  implementation and review.
- Preserve all consumed evidence unchanged. Do not reuse an old run ID, work
  directory, candidate, authorization, or digest.
- Do not claim calibrated frequency, power, spectral compliance, or hardware
  qualification.
- Do not modify WsprryPi, sibling repositories, or historical files.

## Publication and exit criteria

Commit and push only attributable changes on the current branch after complete
staged-diff review. Do not switch branches, rewrite history, force-push, open a
pull request, or publish candidate artifacts to Git.

Exit only when the exact production retained-copy/analyzer relationship passes
offline without mocks, all portable gates and CI are green, and a fresh
exact-revision candidate passes hardware-free validation. Report its canonical
SHA-256 and stop for separate authorization; do not perform a live retry.
