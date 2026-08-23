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
