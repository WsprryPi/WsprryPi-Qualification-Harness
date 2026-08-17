# Phase 7 relative tone-replay execution prompt

## Objective

Complete a non-qualifying offline tone-mode replay of the sealed Phase 7
drive-0 RF-on capture while applying the same bounded relative-frequency basis
as the carrier prerequisite. Preserve strict timing, silence, contrast,
continuity, and lifecycle boundaries.

## Verified context

- The paired carrier replay acquired a transmitter-added feature at
  14,097,269.18 Hz, +169.18 Hz from the command, with 26.99 dB contrast.
- The SDR is not frequency calibrated and may move thermally.
- The sealed run retained exact 3,500,000-sample RF-on IQ with zero overflow,
  clipping, or timeout, but aborted before tone observations were produced.
- Local tone plan and expected-event copies match the SHA-256 values bound into
  the resolved live plan.
- The first transmitter execution reported a materially shortened interval;
  relative frequency handling must not conceal timing or continuity failure.

## Requirements

1. For unshifted Tone, CW, and QRSS events, fit one common receiver-frequency
   offset from acquired active observations.
2. Accept the common offset only within the maintained ±500 Hz acquisition
   bound; enforce the plan's frequency tolerance against residual variation
   after centering.
3. Retain commanded frequency, measured center, signed common offset, maximum
   residual, observation count, and acquisition bound in schema-valid evidence.
4. Do not weaken shifted-tone spacing, state assignment, drift, timing,
   contrast, silence, continuity, transition, clipping, or cleanup rules.
5. Authenticate the local plan/events and IQ/metadata before composing the
   replay into a new `/private/tmp` directory. Never mutate the sealed bundle.
6. Report the tone gate truthfully. A replay pass remains inconclusive and
   non-qualifying; a timing or continuity failure remains a failure.

## Safety and non-goals

- No RF, GPIO, SDR access, service operation, SSH, Pi mutation, or live retry.
- No calibrated-frequency, power, spectral-compliance, or qualification claim.
- Do not regenerate or reuse any consumed live authorization digest.

## Validation and exit

Add synthetic and adversarial coverage for plausible common offset, excessive
offset, unstable residual frequency, and unchanged timing/continuity failures.
Run the complete local validation matrix and authenticated drive-0 replay;
independently review all gate consumers and evidence schemas. Commit and push
only a clean attributable slice, then require green macOS, Ubuntu, and native
Windows CI.
