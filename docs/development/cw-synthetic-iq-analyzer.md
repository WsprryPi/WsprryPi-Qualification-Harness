# Phase 3 synthetic IQ analyzer

Phase 3 is a hardware-free proof that the tone and CW-family evidence chain can
be populated from CF32LE samples rather than caller-authored observations. It
does not open an SDR, contact another host, execute WsprryPi, or qualify any
transmitter.

The deterministic fixture command authenticates a Phase 2 plan/timeline pair,
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

```text
wsprrypi-qualification generate-cw-synthetic-iq \
  PLAN.json EXPECTED.json CAPTURE.cf32 CAPTURE.json --seed 1

wsprrypi-qualification analyze-cw-synthetic-iq \
  PLAN.json EXPECTED.json CAPTURE.json OBSERVATIONS.json GATE.json \
  --source-revision GIT_SHA
```

The analyzer writes new generated-observation and mode-gate documents. A
passing synthetic measurement gate means only that the portable analyzer
recognized the generated fixture. `qualification_claim` remains false. Phase 4
is required before retained acquired IQ may be composed into replay evidence;
later lifecycle and live phases remain separately authorized.
