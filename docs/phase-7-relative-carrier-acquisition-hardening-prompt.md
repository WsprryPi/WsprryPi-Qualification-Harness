# Phase 7 relative carrier-acquisition hardening prompt

## Objective

Adapt the offline Phase 7 carrier gate for a receiver that is not frequency
calibrated and may drift thermally. Find the transmitter-added signal relative
to the paired RF-off capture and accept it when it is strong and plausibly near
the commanded frequency, without claiming calibrated frequency, power, or
spectral compliance.

## Verified context

- The sealed drive-0 run remains immutable and aborted only because its original
  analyzer could not consume unequal exact capture lengths.
- Corrected offline replay found a transmitter-added feature near
  14,097,269.18 Hz, +169.18 Hz from the 14,097,100 Hz request, with about
  26.99 dB RF-on/RF-off contrast.
- The SDR has no established frequency calibration and bench temperature may
  move during capture.
- No new RF, GPIO, SDR, service, or host operation is authorized by this slice.

## Requirements

1. Use RF-on minus RF-off evidence to acquire the strongest resolved
   transmitter-added feature.
2. Define a fail-closed, reviewable meaning for "not totally out of line": no
   more than 500 Hz from the commanded frequency and at least 10 dB contrast at
   the acquired feature.
3. Retain the requested frequency, signed offset, legacy 100-Hz nominal-offset
   result, best-20-Hz share, and legacy 0.50 nominal-share result as diagnostics.
4. Do not use nominal offset or concentration alone to reject an otherwise
   strong bounded acquisition from an uncalibrated receiver.
5. Record the policy and limitations in schema-valid immutable evidence.
6. Recompute retained drive-0 evidence only in a separate output; never mutate
   or relabel the sealed run.
7. Add adversarial tests for plausible drift, implausible displacement, absent
   carrier, clipping, and deterministic replay.
8. Preserve macOS, Linux/Raspberry Pi OS, and native Windows portability.

## Non-goals and safety boundary

- No live retry, transmitter operation, receiver reconfiguration, service
  manipulation, GPIO access, or RF authorization.
- No calibrated-frequency, power, harmonic, spurious-emission, or antenna-ready
  claim.
- Do not qualify WSPR decoding or any hardware combination from carrier replay.

## Validation and exit

Run focused and complete tests, formatting, lint, type checks, package build,
native CMake/CTest, schema-copy comparison, provenance verification, diff
review, and an independent adversarial reassessment. Re-run the authenticated
offline replay outside the sealed evidence directory. Commit and push only the
attributable clean slice, then require green macOS, Ubuntu, and native Windows
CI. Exit when the relative-acquisition policy is explicit, reproducible,
portable, and truthfully applied to the retained evidence.
