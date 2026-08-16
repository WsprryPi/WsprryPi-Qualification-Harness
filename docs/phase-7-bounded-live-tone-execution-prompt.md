# Phase 7 execution prompt: bounded live tone validation

## Objective

Validate one exact WsprryPi transmitter, backend, band, receiver, and RF path
with a strictly bounded live tone run. Exercise the production start, capture,
analysis, stop, cleanup, and backend-specific quiescence path and retain an
immutable evidence bundle. This phase validates only the live tone control; it
does not qualify CW, QRSS, FSKCW, DFCW, WSPR, another backend, another band, or
spectral or regulatory compliance.

## Verified starting context

- Repository: `WsprryPi-Qualification-Harness`.
- Branch: `codex/issue-401-cw-qualification`.
- Prompt baseline revision: `556c1dbc09f73e6a7a1f9a6d409bb0683aa325d7`.
- CI run `31958233926` is green at that revision on macOS, Ubuntu, and native
  Windows with Python 3.11 and 3.13.
- The retained Phase 6 actual-host result is `blocked`, not ready. It records
  Gate D `executionReady: false`, undeclared current RF-path facts, and an
  active `SoapySDRServer` conflict on `wspr5`.
- The copied `wspr5-pre-dkms-20260816` archive authenticates historical state
  only. Its service snapshot also records `SoapySDRServer` active; it cannot
  establish current ownership or readiness.
- Archive normalization and retained replay results are non-qualifying and do
  not substitute for a current Phase 6 bundle.

## Mandatory entry gate

Perform no transmission, tone generation, GPIO/I2C/DMA/PWM/GPCLK access, SDR
open, service mutation, or live implementation until every condition below is
simultaneously true:

1. The repository and all exact source/submodule revisions are clean, pushed,
   and bound into one candidate plan.
2. Required cross-platform CI is green at the exact harness revision.
3. Gate D is current, authenticated, and explicitly `executionReady: true` for
   the exact transmitter candidate.
4. A newly executed, immutable Phase 6 actual-host preflight passes for the
   unchanged candidate and hosts.
5. Current RF-path facts explicitly identify antenna state, termination,
   attenuation, filter, safe receiver-input basis, receiver identity, and
   physical routing. Examples and historical values are not confirmation.
6. Receiver and transmitter ownership are conflict-free. Any existing
   `SoapySDRServer`, WsprryPi, helper, or competing process is a blocker unless
   the plan explicitly owns and safely restores it.
7. The exact Phase 7 plan fixes backend, output, frequency, band, drive,
   maximum tone duration, receiver settings, process deadlines, stop procedure,
   emergency stop, service-restoration policy, and quiescence checks.
8. The operator provides separate current RF authorization that names the
   exact plan SHA-256 and authorizes only this bounded live tone run.
9. Runtime confirmation matches the exact plan digest and explicit RF opt-in.

If any item is false, stale, absent, contradictory, or unverifiable, stop with
a specific blocked/preflight result. Do not infer permission, fill missing
facts from history, correct a host, stop a service, or weaken a contract.

## Authorized execution scope after the gate passes

1. Create a new never-reused UTC-and-test-ID evidence directory.
2. Authenticate the exact plan, revisions, binaries, tools, hosts, receiver,
   backend, and RF authorization before enabling RF.
3. Install bounded ownership and cleanup handlers before transmitter enable.
4. Capture a fixed-gain RF-off control, then one short production-path tone,
   then a closing RF-off control. Bound every process and record complete logs.
5. Analyze the requested-frequency offset, on/off contrast, concentration,
   clipping, short reads, overflow, and unexpected competing features using the
   maintained analyzer. Do not hand-author observations.
6. Stop all owned children, disable the transmitter, restore only state the
   harness deliberately changed, release the receiver, and verify
   backend-specific quiescence.
7. Make cleanup failure override an otherwise passing measurement. Separate
   receiver/RF-path blockage from transmitter failure.
8. Seal requested/resolved plans, authorization, preflight, lifecycle,
   captures, analysis, cleanup, quiescence, result, and a canonical SHA-256
   manifest. Preserve raw IQ according to the resolved retention policy.

## Non-goals

- No keyed-mode or WSPR transmission and no Phase 8 work.
- No automatic host repair, package/kernel/DKMS installation, reboot, or
  persistent service reconfiguration.
- No sibling-repository edits, historical evidence edits, schema weakening,
  synthetic qualification, or relabeling copied evidence as current.
- No claim of calibrated power, harmonics, spurious-emission compliance,
  antenna readiness, or general hardware support.
- No branch change, history rewrite, force-push, pull request, release, or issue
  mutation.

## Validation and adversarial review

Before live execution, prove with hardware-free tests that missing or stale
preflight, plan/authorization digest mismatch, unsafe RF path, ownership
conflict, partial cleanup registration, timeout, cancellation, capture failure,
analysis failure, service-restoration failure, and quiescence failure all stop
or fail closed without a positive claim.

After any authorized run, independently authenticate every retained artifact
and recompute the entry gate, lifecycle ordering, analysis, cleanup precedence,
quiescence, final classification, and manifest. Attempt to prove that RF could
start before cleanup registration, an unowned process could be stopped, stale
facts could satisfy authorization, a passing tone could conceal cleanup
failure, or missing evidence could become success. Treat every actionable
finding as a blocker, repair it, and repeat until clean.

## Publication gate

Commit and push only narrowly attributable code, tests, schemas, and maintained
documentation after staged-diff and large/generated-file review. Never commit
raw IQ, copied archives, generated run bundles, credentials, or machine-local
configuration. Require a new green macOS, Ubuntu, and native Windows matrix at
the resulting revision.

## Exit criteria

Phase 7 exits only when the exact candidate has a passing current Phase 6
bundle and digest-bound RF authorization; the bounded tone run succeeds; the
opening/closing controls, lifecycle, cleanup, and quiescence all pass; the
sealed bundle survives independent recomputation; the reviewed revision is
pushed with green cross-platform CI; and the tracked worktree is clean.

## Execution record - 2026-08-16

The mandatory entry gate was evaluated read-only at revision `556c1db`.
Cross-platform CI is now satisfied, closing one former blocker. Live execution
remains blocked because there is no current passing Phase 6 bundle for this
revision, no current Gate D `executionReady: true` evidence, no declared current
RF path, no conflict-free current ownership observation, and no explicit RF
authorization bound to an exact Phase 7 plan digest. The copied archive records
historical `SoapySDRServer` activity but cannot establish current state.

No SSH session, service operation, SDR access, hardware access, tone, or RF
activity was performed. No production code or safety contract requires a
change to report this truthful gate result. Phase 7 remains unauthorized.
