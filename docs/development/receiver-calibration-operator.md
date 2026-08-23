# Receiver calibration operator workflow

This workflow binds the frozen `sdr-calibration-profile` 1.0.0 contract to
recorded or live receiver evidence. It does not open an SDR, change tuning,
contact a host, authorize RF, or modify transmitter PPM.

## Choose a policy

- `required`: missing, expired, mismatched, out-of-domain, or unusable
  calibration blocks before receiver or RF access.
- `optional`: a supplied calibration must pass every check; absence is retained
  as explicitly uncalibrated interpretation.
- `disabled`: calibration is deliberately not applied and supplied calibration
  artifacts are contradictory.

## Hardware-free preparation

Validate a real profile and run-specific application request, then bind them:

The request must bound `maximum_application_age_seconds` to at most 3600. Live
validation rejects stale observed temperature, warm-up, and configuration facts
before constructing receiver or RF capabilities.

```text
wsprrypi-qualification evaluate-sdr-calibration PROFILE.json REQUEST.json
wsprrypi-qualification compose-receiver-calibration \
  PROFILE.json REQUEST.json RECEIVER-CALIBRATION.json --policy required
```

Until a real profile is available, create the maintained deterministic fixture:

```text
wsprrypi-qualification generate-synthetic-sdr-calibration NEW-DIRECTORY
wsprrypi-qualification compose-receiver-calibration \
  NEW-DIRECTORY/synthetic-profile.json \
  NEW-DIRECTORY/synthetic-request.json \
  RECEIVER-CALIBRATION.json --policy required
```

The fixture uses synthetic identity and provenance, is unsigned, and exists
only to exercise the frozen contract. It cannot qualify hardware.

Applied unsigned profiles are likewise rejected at live execution entry until
a reviewed signature verifier and trust-store policy are available. They remain
valid for hardware-free composition and recorded/replay interpretation.

Recorded carrier accepts `--receiver-calibration-binding`. Acquired CW replay
accepts the same option. Resolved Tone, WSPR, QRSS, FSKCW, and DFCW plans carry
the complete binding as `receiver_calibration`; the resulting plan digest and
runtime authorization therefore change whenever any bound calibration fact
changes.

Results preserve indicated frequencies. When calibration is applied they add
estimated-true frequency, expanded uncertainty, profile and segment identity,
and reliability. FSKCW/DFCW retain indicated separation independently from
calibrated primary/secondary interpretations.

## Suggested agent prompts

### Hardware-free fixture rehearsal

```text
Read AGENTS.md, CONTRACT.md, docs/AGENT_OPERATIONS.md, and the receiver
calibration guide. In a new temporary directory, generate the maintained
synthetic SDR calibration fixture, validate it, compose a required receiver
calibration binding, and report the exact frozen contract identity, hashes,
application result, corrected frequency, and uncertainty. Do not contact a
host or device and do not modify the repository.
```

### Recorded evidence composition

```text
Read the governing contracts and inspect the supplied capture metadata,
receiver settings, frozen calibration profile, and application request
read-only. Verify exact identity/configuration compatibility and compose a new
receiver-calibration binding. Run only the requested recorded carrier or CW
replay workflow with that binding, validate the complete output, and report
indicated and estimated-true frequency separately. Do not infer transmitter
lifecycle, alter transmitter PPM, or access hardware.
```

### Live-plan review

```text
Review the proposed Tone, WSPR, QRSS, FSKCW, or DFCW resolved plan without
executing it. Confirm that receiver_calibration has an explicit policy, its
profile/request/result hashes and frozen contract are valid, its receiver
identity/settings exactly match the plan, and it is covered by the final plan
digest. Confirm that WsprryPi arguments and transmitter PPM are unchanged by
receiver calibration. Stop and report any mismatch; do not contact hosts,
operate services/devices, or enable RF.
```

### Authorization-bound live use

```text
First perform the documented read-only plan and host review. Do not execute
unless the operator separately supplies current authorization for the exact
plan digest, hosts, receiver, RF path, level, stopping procedure, and window.
If authorized, run only the named production command and retain both indicated
and calibrated receiver-frequency evidence. Treat calibration failure as
receiver/preflight blockage, never transmitter unqualification. Always require
cleanup, service restoration, and backend quiescence evidence.
```
