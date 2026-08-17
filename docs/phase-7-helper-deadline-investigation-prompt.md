# Phase 7 helper-deadline investigation and fresh-candidate prompt

## Objective

Independently determine why the digest-authorized Phase 7 attempt stopped in
`verify_helper` with `RealSessionError: helper exceeded its hard deadline`.
Reproduce the behavior without RF or hardware access, correct the narrow
lifecycle defect if confirmed, validate it across supported platforms, and
seal a fresh drive-0 candidate under a new digest. Stop for separate digest
authorization.

## Verified context

- Authorized candidate digest
  `f8416e7ec6d81f1fc44816daeafb31f13034a300b1665df00f461d3dfdfd5185`
  was consumed by exactly one attempt.
- That attempt passed plan validation, runtime confirmation, and capability
  discovery, then failed during helper verification before ownership, GPIO
  idle inspection, service mutation, SDR access, capture, transmitter launch,
  or RF.
- Retained status is `preflight_failed`; carrier and decode gates are
  `not_run`; harness cleanup is `not_required`.
- Manual post-attempt checks found GPIO4 input and unowned, wspr4
  `wsprrypi.service` active, both relevant wspr5 services inactive, and no
  candidate-owned process. The evidence manifest verifies completely.
- The failed run and its work directory are immutable and must not be reused.

## Investigation scope

1. Inspect the exact helper-verification implementation and deadline model at
   revision `c7faf22dfdb7d32e512a75ce55d2a002d629c736`.
2. Time each non-mutating operation that contributes to helper verification:
   transmitter helper startup/service inspection, receiver helper
   startup/service inspection, and both pinned source-revision queries.
3. Reproduce enough times to distinguish an individual operation timeout from
   an aggregate-stage deadline overrun. Record timing and closure results.
4. Determine whether the defect is implementation logic, insufficient
   evidence granularity, candidate configuration, host contention, or an
   inconclusive transient. Do not merely enlarge a timeout without showing
   which contract it represents.
5. If code changes are needed, keep them limited to the helper-verification
   deadline/evidence boundary. Preserve fail-closed behavior, the cumulative
   overall deadline, and the cleanup reserve.

## Required hardening

- Every external operation remains individually bounded.
- Aggregate helper verification must have an explicit, truthful bound that can
  encompass its documented sequential operations, or those operations must be
  represented as independently bounded stages.
- Timeout evidence must identify the operation or substage responsible rather
  than collapsing all helper failures into an ambiguous message.
- Partial helper startup must always close both helper sessions within the
  cleanup bound, including failures before cleanup registration.
- Add deterministic tests for an aggregate overrun caused by multiple
  individually successful operations, an individual operation timeout,
  overall-deadline precedence, closure after partial startup, and normal
  success.
- Keep portable core behavior valid on macOS, Linux/Raspberry Pi OS, and native
  Windows; do not introduce POSIX-only assumptions into portable code.

## Safety boundary

- Do not reuse the consumed digest or execute the failed candidate again.
- Do not start or stop services, inspect or configure GPIO, open the SDR,
  launch WsprryPi, capture samples, generate a tone, or emit RF.
- Helper sessions may perform only integrity checks, service-state reads, and
  pinned source-revision reads. Close them after every diagnostic trial.
- Preserve all prior evidence and staging roots. Do not alter sibling
  repositories or shared executables.

## Validation and independent review

Run formatting, lint, strict typing, focused and full unit tests, distribution
builds, hardware-disabled native tests, provenance checks, schema/package
synchronization, and staged-diff review. Independently challenge timeout
semantics, failure attribution, cleanup, overall-deadline interaction, Windows
portability, and compatibility with existing plans. Resolve every actionable
finding before publication.

Commit and push only attributable reviewed repository changes on the current
branch. Confirm green macOS, Ubuntu, and native Windows CI at the resulting
revision.

## Fresh-candidate exit criteria

After the resulting revision is green, deploy its exact wheel into new
isolated wspr4/wspr5 roots. Construct a new candidate with the same operator
RF facts and bounded drive-0 tone contract, but with a new run ID, work/run
paths, helper identities and hashes, analyzer revision, plan-file hash,
helper-configuration digest, and canonical plan digest. Run only schema,
plan-only, integrity, service-state, ownership, and helper-closure checks.

Exit when the failure is explained and resolved or truthfully classified, the
repository is clean and synchronized, the fresh candidate passes all
non-hardware checks, and its exact canonical SHA-256 is reported. Require this
new, separate authorization wording before any live execution:

`I authorize only the Phase 7 bounded live-tone plan with SHA-256 <digest>.`
