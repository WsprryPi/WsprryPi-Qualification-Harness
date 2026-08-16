# Phase 6 execution prompt: prerequisite closure for Phase 7

## Objective

Re-evaluate the actual-host prerequisites for Phase 7 against the exact current
harness revision and current `wspr4` transmitter and `wspr5` receiver state.
Produce a new immutable Phase 6 read-only preflight bundle that either proves
one exact candidate ready or identifies every remaining fail-closed blocker.
Do not transmit, open the SDR, operate transmitter hardware, or correct a host.

## Starting context

- Harness branch: `codex/issue-401-cw-qualification`.
- Starting revision: `9961be0123f8fbe68507871f87188d45f2411bdc`.
- CI run `31959032959` is green on macOS, Ubuntu, and native Windows with
  Python 3.11 and 3.13 at that revision.
- The retained 2026-08-15 Phase 6 bundle was correctly blocked by incomplete
  Gate D, undeclared current RF-path facts, then-unconfirmed CI, and an active
  `SoapySDRServer` conflict.
- CI is now closed. Gate D, physical RF path, source revisions, ownership,
  service state, and host identity must be re-established or retained as
  blockers; copied or historical values cannot satisfy a current gate.

## Scope

1. Inspect the clean harness checkout and authenticate its pushed revision and
   CI result.
2. Use only the maintained Phase 6 closed read-only SSH probe vocabulary to
   discover current hostname, model/revision, kernel/OS/clock, identity/groups,
   process names, loaded modules, WsprryPi repository revision/cleanliness,
   required binaries, and selected service states.
3. Construct a new plan in ignored or temporary storage. Bind the exact harness
   revision, discovered exact WsprryPi revisions, strict host-key aliases,
   current Gate D status, known blockers, and only contemporaneously confirmed
   RF-path facts.
4. Compute the canonical plan SHA-256, execute
   `run-cw-actual-host-preflight` with the explicit read-only enable flag, and
   validate the resulting bundle independently.
5. Recompute check outcomes, blockers, readiness, command ordering, timing,
   plan binding, file identities, and `SHA256SUMS` from retained evidence.
6. Update maintained documentation only with facts established by this run.

## Safety and preservation constraints

- No RF opt-in, transmission, tone, GPIO, I2C, DMA, PWM, GPCLK, `/dev` access,
  physical SDR discovery/open, package/kernel/DKMS operation, reboot, or sudo.
- No service start/stop/restart/reload/enable/disable and no process signal.
- No repository checkout, pull, reset, clean, stash, install, or source edit on
  either host.
- Do not infer physical antenna, termination, attenuation, filtering, routing,
  or safe-input facts from examples, historical runs, or copied archives.
- Do not relabel Gate D complete without current authenticated
  `executionReady: true` evidence for the exact candidate.
- Preserve every existing local, ignored, historical, and sibling-repository
  artifact. Use a new output directory and never overwrite a prior bundle.
- A blocked result is successful execution of this prompt when its blockers are
  truthful; it is not Phase 7 authorization.

## Non-goals

- No remediation of any discovered conflict or mismatch.
- No Phase 7 implementation or live tone and no Phase 8 keyed-mode work.
- No qualification claim, hardware-support generalization, or claim that a
  read-only probe validates receiver operation.
- No raw IQ, copied archive, generated evidence bundle, or machine-local plan
  in Git.
- No branch change, history rewrite, force-push, pull request, release, or issue
  mutation.

## Adversarial review

Attempt to prove that a stale plan can pass; expected identity can replace an
observation; an empty successful probe can count as evidence; a host-key alias
can be bypassed; shell syntax or mutation can enter the probe vector; process
arguments can leak; an optional failure can become readiness; current CI can
mask stale host state; an undeclared RF path or incomplete Gate D can disappear
from blockers; a dirty/mismatched repository can pass; files can be substituted
after execution; or a blocked bundle can authorize Phase 7. Repair every
actionable finding and repeat until the software/evidence review is clean.

## Validation and publication

- Validate the fresh bundle with `validate-cw-actual-host-preflight`.
- Run focused Phase 6 schema, CLI, command-safety, semantic-recomputation,
  tamper, timeout, and cross-platform path tests if code changes.
- For any code change, run the full local gate set and require green macOS,
  Ubuntu, and native Windows CI at the pushed revision.
- Stage only narrowly attributable maintained files after diff, generated-file,
  and large-file review. Commit and push only if a maintained change is needed.

## Exit criteria

The slice exits with a new schema-valid and fully authenticated Phase 6 bundle,
an independently recomputed ready-or-blocked result, no host mutation, and a
clean tracked worktree. Phase 7 remains blocked unless the same bundle proves
current Gate D complete, current RF-path facts declared, exact source and host
identity, conflict-free ownership, synchronized time, required tools/groups,
clean repositories, and no other blocker.

## Execution record - 2026-08-16

The first new bundle failed closed because sandbox policy denied every
Python-owned SSH subprocess with return code 255. It was validated and
preserved under its unique run ID; it was not reused or deleted. The identical
schema-valid plan was assigned a new run ID and executed outside the sandbox.

The accepted bundle is
`/private/tmp/20260816T165032Z-phase6-prerequisite-cw-actual-host-preflight`.
Its plan SHA-256 is
`54d602008b2b1472b9e5f769d445e66e3d764ce5ec7352e917607539d591fc73`.
Independent validation and canonical manifest verification passed.

Current passing observations include strict host identity, board model and
revision, synchronized clocks, required groups and tools, and clean exact
WsprryPi revisions `0bb96003950da61a1ccd19c6060f96e8aed454ae` on
`wspr4` and `c83c19b593ce915397e7e71a96688031e77c9fa2` on
`wspr5`. The result remains correctly `blocked`: Gate D is incomplete, current
RF-path facts are undeclared, `wsprrypi` is active on `wspr4`, and
`SoapySDRServer` is active on `wspr5`. `next_phase_authorized` and
`qualification_claim` are false.

No host state was corrected. No service, process, repository, SDR, GPIO, clock,
transmitter, or RF operation was performed.
