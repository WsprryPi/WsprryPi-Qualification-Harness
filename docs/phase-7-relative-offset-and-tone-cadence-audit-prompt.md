# Phase 7 relative-offset and tone-cadence audit prompt

## Objective

Determine whether the consumed drive-0 run failed because of frequency offset,
timing, continuity, or harness composition; correct proven offline defects and
preserve every distinct measurement claim.

## Evidence and requirements

- Treat +186.35 Hz as passing the authorized bounded relative-acquisition gate
  of +/-500 Hz; do not reinterpret it as calibrated error or a failure.
- Independently replay the retained IQ and inspect expected versus detected
  event boundaries, transmitter execution logs, capture timestamps, contrast,
  continuity, clipping, overflow, and cleanup.
- Keep the outer relative carrier acquisition, detailed tone carrier gate, and
  keyed mode gate as separate concepts. Tone keeps `mode_gate: not_applicable`.
- When outer acquisition passes, propagate the detailed analyzer's
  `carrier_gate`; never substitute the tone mode-gate value into a carrier gate.
- Add a regression in which relative frequency acquisition passes but detailed
  timing fails, requiring a valid failed carrier outcome without schema abort.
- Identify remote process-start latency separately from actual RF-on duration.
  Do not loosen timing tolerances or expand RF duration without new evidence and
  a separately digested candidate.

## Safety and exit

This slice is offline only. Preserve the consumed run, do not contact hardware
for a retry, and do not reuse its digest. Run focused and complete portable
validation, review adversarially, commit and push attributable changes on the
current branch, and require green macOS, Ubuntu, and native Windows CI. Report
the truthful measurement interpretation and the remaining cadence blocker.
