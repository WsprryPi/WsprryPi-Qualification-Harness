# Phase 7 required receiver-service lifecycle hardening prompt

## Objective

Harden the production real-session adapter so a local SoapySDR capture can
declare a receiver service that must be running during capture, start it only
after cleanup is registered, verify its requested state, and restore its exact
initial state on every exit path.

## Verified context

- The RSP1B is present on wspr5 and SoapySDR identifies it as serial
  `2404058C60` when `sdrplay.service` is active.
- Two digest-authorized Phase 7 attempts failed closed before RF because the
  SDRplay API service was inactive when the RF-off capture began.
- The current `services.receiver` contract models services as receiver owners
  to stop after cleanup registration. It cannot express a service required by
  the local capture path.
- Both attempts retained `wrong_device` capture failures, verified cleanup,
  and verified GPIO4 quiescence; neither enabled the transmitter.
- This slice authorizes repository implementation and hardware-free tests only.

## Scope and requirements

1. Extend the resolved real-session plan with an explicit, reviewable required
   receiver-service policy while preserving existing conflict-service behavior.
2. Require every required receiver service to be part of the receiver service
   allowlist and reject contradictory or duplicate policy before external work.
3. After cleanup registration and before the first receiver capture, start each
   initially inactive required service and verify it is running.
4. Leave an initially running required service running, but retain its initial
   state for cleanup evidence.
5. For receiver services not marked required, preserve the existing behavior:
   stop an initially running conflict service only after cleanup registration.
6. Record every service changed by the adapter before mutation so cleanup can
   restore it after success, capture failure, timeout, cancellation, or later
   transmitter failure.
7. Treat start, verification, or restoration failure as a failed run; cleanup
   failure must continue to override an otherwise successful measurement.
8. Keep helper allowlists, plan digests, packaged schemas, and source schemas
   synchronized. Do not hard-code `sdrplay.service` in portable core logic.
9. Add focused adversarial tests for inactive-required start/restore,
   initially-active preservation, start verification failure, conflict-service
   stopping, cleanup after downstream failure, and invalid subset policy.
10. Document that local SoapySDR still uses the SDRplay Soapy module and its
    vendor API service; SoapyRemote is a separate, unnecessary network layer
    for this topology.

## Safety boundary and non-goals

- Do not access wspr4/wspr5, manipulate services, open an SDR, inspect GPIO, or
  transmit while implementing or testing this slice.
- Do not retry Phase 7 or reuse either consumed single-run authorization.
- Do not generalize service names, receiver identities, power, calibration, or
  spectral claims beyond the recorded plan.
- Do not refactor unrelated lifecycle, analyzer, decoder, or deployment code.

## Validation and evidence

Run focused unit/schema tests followed by formatting, lint, type checking, the
complete test suite, package build, native CMake/CTest, source/package schema
byte comparison, provenance verification, and `git diff --check`. Independently
review ordering, restoration, authorization boundaries, backward compatibility,
Windows portability, and evidence truthfulness; resolve every actionable
finding and reassess until clean.

## Exit criteria

The portable harness can represent and validate a required local receiver
service, activate it only inside the cleanup-protected interval, verify it
before capture, and restore its exact initial state on every tested exit path.
The worktree contains only reviewed attributable changes, all validation is
green, and the current branch is committed and pushed without any live or RF
operation.
