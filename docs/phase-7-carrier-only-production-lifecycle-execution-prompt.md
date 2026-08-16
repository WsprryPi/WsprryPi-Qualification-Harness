# Phase 7 carrier-only production lifecycle execution prompt

## Objective

Implement the missing production `tone` lifecycle needed to execute one exact
digest-bound Phase 7 plan without advancing into WSPR or keyed modes. The
command must coordinate a fixed RF-off/on pattern, exact-count receiver
capture, maintained analysis, cleanup, service restoration, quiescence, and an
immutable evidence bundle. This software slice performs no live run.

## Verified starting context

- Harness revision `274646d0f7d6fcac96612f0775031a031e0e72ce` is clean,
  pushed, and green on macOS, Ubuntu, and native Windows with Python 3.11 and
  3.13.
- `run-live-session` implements a WSPR session whose passing carrier gate
  automatically advances to three WSPR frames. It cannot execute a
  carrier-only Phase 7 plan.
- Phase 5 supplies only mock tone/CW lifecycle evidence. Phase 6 supplies only
  read-only actual-host preflight evidence.
- The authorized digest `07b9c442...b747a948` was rejected before any service,
  SDR, GPIO, or RF action because no reviewed production carrier-only path
  existed. It must not be reused after this implementation changes the
  controller revision.

## Required implementation

1. Extend the closed, versioned resolved real-session schema with a live-tone
   variant that binds the exact
   controller, mode plan, expected events, hosts, binaries, backend/output,
   receiver, RF path, service policy, deadlines, stop procedure, and runtime
   authorization requirements.
2. Add a separate `run-cw-live-tone` command. Do not overload or weaken the
   WSPR-only `run-live-session` contract.
3. Require a byte-exact plan SHA-256, explicit live and RF enable flags, and an
   ephemeral operator confirmation matching the same digest before external
   access.
4. Isolate SSH, service, receiver, and transmitter behavior behind an explicit
   production adapter. The portable coordinator must contain no shell,
   systemd, POSIX-signal, `/proc`, or Unix-path assumptions.
5. Install cleanup ownership before receiver or transmitter enable. Preserve
   initial service state and restore only services deliberately changed.
6. Execute exactly the resolved leading quiet, three bounded carrier periods,
   intervening quiet periods, and closing quiet period. Enforce both cumulative
   RF-on and overall deadlines.
7. Retain exact-count CF32 capture metadata, complete process logs, maintained
   observations and gate documents, cleanup and quiescence evidence, final
   classification, and a canonical SHA-256 manifest.
8. Never advance into WSPR, CW, QRSS, FSKCW, or DFCW. A passing tone result is
   Phase 7 evidence only and makes no calibrated-power or spectral-compliance
   claim.

## Fail-closed and adversarial requirements

The production command repeats its own current host, ownership, capability,
RF-idle, and receiver checks; Phase 6 remains planning evidence and is not
substituted for this fresh preflight. Reject schema or digest mismatch, changed
binaries, host/device mismatch, unsafe RF-path facts, unowned conflicts,
receiver readiness failure, cleanup registration failure, early RF enable,
wrong event count/order/duration/frequency, capture short read or overflow,
clipping, timeout, cancellation, service-restoration failure, leaked process,
or backend quiescence failure. Cleanup failure overrides a passing
measurement. RP1 Gate D remains outside this legacy-GPIO candidate; the plan's
GPIO backend/output and GPIO-specific quiescence capability binding must make
that scope explicit rather than accepting a generic bypass field.

## Validation and publication

Run formatting, lint, typing, all unit tests, package build, native CMake tests,
schema source/package synchronization, provenance verification, generated or
large-file review, and `git diff --check`. Independently attempt to make the
coordinator transmit before cleanup, accept the wrong digest, stop an unowned
service, advance to WSPR, or report success after cleanup failure. Resolve all
findings, commit only attributable source/tests/schemas/docs, push the current
branch, and require green macOS, Ubuntu, and native Windows CI.

## Exit criteria

The carrier-only command and contracts are deterministic, portable,
fail-closed, independently reviewed, pushed, and green. No live operation has
occurred. Construct a new exact plan at the resulting revision and require a
new digest-bound operator authorization before stopping services or accessing
the receiver, GPIO, or RF path.
