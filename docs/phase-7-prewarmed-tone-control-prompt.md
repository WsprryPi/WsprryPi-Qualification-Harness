# Phase 7 prewarmed tone-control prompt

## Objective

Replace process-start-timed Tone cadence with an explicitly owned, prewarmed
WsprryPi control lifecycle so each requested two-second interval measures RF
time rather than executable startup time.

## Verified context

- The consumed drive-0 run passed bounded relative acquisition at +186.35 Hz
  with 110.63 dB contrast; frequency offset was not the failure.
- Detected starts were roughly 0.28 to 0.31 seconds late. The first owned
  process reported only 1.910380 seconds of transmission because initialization
  occurred inside its absolute two-second window.
- WsprryPi already provides serialized WebSocket `tone_start` and `tone_end`
  commands, affirmative replies, `get_tx_state`, and scheduler restoration.
- `tone_start` rejects active or enabled normal transmission. A prewarmed
  candidate therefore requires an isolated configuration with transmission
  disabled and unique control ports.

## Required design and implementation

1. Add a portable, standard-library bounded WebSocket control adapter with
   explicit connect/read/write deadlines, response validation, masking,
   maximum-frame size, and deterministic cleanup. Do not add a third-party
   runtime dependency without justification.
2. Bind the exact transmitter host, dedicated executable, isolated config,
   socket endpoint, source revision, and hashes into the resolved plan. Never
   expose the unauthenticated WebSocket directly on the LAN: carry control
   through the authenticated helper/SSH boundary, or require a separately
   reviewed WsprryPi loopback-binding capability.
3. Start the dedicated process before the cadence epoch with normal
   transmission disabled. Verify process ownership, `get_tx_state`, backend,
   output, frequency context, and RF-idle state before any `tone_start`.
4. For each cycle, issue `tone_start`, require its affirmative response and a
   transmitting state, begin the two-second RF budget only after that evidence,
   then issue `tone_end` at the absolute deadline and require stopped plus
   scheduler-restored evidence.
5. Retain command/reply timestamps and state transitions. The independent SDR
   analyzer remains authoritative for actual RF timing and continuity.
6. Keep the cumulative RF-on maximum at six seconds. Network delay, missing or
   malformed replies, late transitions, disconnect, process exit, or state
   contradiction must fail closed and enter cleanup; never compensate by
   extending an RF interval.
7. Cleanup must attempt `tone_end`, stop only the owned dedicated process,
   restore only services changed by the harness, verify GPIO4 input, and retain
   failures with cleanup precedence.

## Offline validation and adversarial review

- Build a fake RFC 6455 server and inject fragmentation, oversized frames,
  wrong opcodes, stale replies, delayed replies, disconnects, rejected starts,
  failed ends, process death, state contradictions, and cleanup failures.
- Prove process startup is complete before the cadence epoch and absent from
  RF-on accounting.
- Prove an unauthenticated network peer cannot invoke Tone control.
- Prove paths with spaces and native Windows socket behavior.
- Run full formatting, lint, typing, tests, package/install, native CTest,
  provenance, archive hygiene, and macOS/Ubuntu/native-Windows CI.

## Safety and exit

Implementation and review are hardware-free. Do not start services, execute a
transmitter, open an SDR, touch GPIO, or emit RF. Do not modify WsprryPi unless
separately authorized in its repository. Commit and push only attributable
harness changes on the current branch. Exit with a clean portable control
contract and no candidate digest; a fresh candidate and live validation remain
separately gated.
