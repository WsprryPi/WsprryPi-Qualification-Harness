# Synthetic IQ analyzer

This hardware-free workflow proves that the tone and CW-family evidence chain can
be populated from CF32LE samples rather than caller-authored observations. It
does not open an SDR, contact another host, execute WsprryPi, or qualify any
transmitter.

The deterministic fixture command authenticates a plan/timeline pair,
uses an explicit 32-bit seed, and writes a new CF32LE capture plus
`cw_synthetic_capture` metadata. It refuses overwrite. The capture contains
only deterministic noise and the primary/secondary carriers declared by the
expected events.

The analyzer reauthenticates the complete input chain and the capture size and
SHA-256 before reading samples. It rejects short/non-CF32 captures, non-finite
samples, overflow, tampering, and thresholds tighter than its recorded time or
frequency resolution. For every event it measures power/contrast, carrier
continuity in eight subwindows, and frequency from complex phase progression.
RF-off intervals are measured too, so false silence cannot pass. Clipping is a
fixture blockage rather than transmitter failure.

Frequency resolution uses the full shortest active interval consumed by the
phase-progress estimator; it is not truncated to an artificial 256-sample FFT
limit. Tone, CW, and QRSS use a bounded common receiver offset of at most
500 Hz and enforce the plan tolerance against residual event-to-event error.
Shifted modes retain their separate spacing, state, transition, and drift model.
Spacing resolution is therefore applicable only to FSKCW and DFCW.

For acquired shifted-CW evidence, carrier presence and frequency-transition
resolution are independent facts. An active expected interval with continuous
RF but no uniquely resolved primary/secondary run is reported as
`unresolved_frequency_transition`; `missing_carrier` is reserved for an active
interval whose RF-presence test fails. The analyzer does not reinterpret a
backend's variable startup latency as part of the requested dot duration.

```text
wsprrypi-qualification generate-cw-synthetic-iq \
  PLAN.json EXPECTED.json CAPTURE.cf32 CAPTURE.json --seed 1

wsprrypi-qualification analyze-cw-synthetic-iq \
  PLAN.json EXPECTED.json CAPTURE.json OBSERVATIONS.json GATE.json \
  --source-revision GIT_SHA
```

The analyzer writes new generated-observation and mode-gate documents. A
passing synthetic measurement gate means only that the portable analyzer
recognized the generated fixture. `qualification_claim` remains false. Acquired
IQ replay, lifecycle rehearsal, and live operation remain separate workflows;
live operation requires current explicit authorization.

Historical analyzer version 7 excludes isolated above-threshold activity shorter than its
reported four-sample timing resolution before selecting event boundaries. Such
an excursion cannot define a resolved carrier edge: treating it as one can split
a quiet interval and produce a much larger false timing error. Resolvable
activity remains subject to the unchanged contrast, timing, and transition
gates. Original observations remain immutable; a new replay is non-qualifying
and never replaces the live lifecycle evidence.

Analyzer version 8 uses carrier-channel filtering, independently confirmed
edges, guarded reference checks, and separate raw-IQ quiet contamination
evidence. It reports the full filter/state-classification timing budget rather
than claiming four-sample accuracy for filtered transitions. See
[Noise robustness](noise-robustness.md) for semantics, compatibility,
validation scope, and remaining limitations.
