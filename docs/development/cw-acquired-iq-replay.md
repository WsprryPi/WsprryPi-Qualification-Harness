# Phase 4 acquired-IQ replay

Phase 4 turns an already retained CW-family CF32LE capture into a portable
offline replay bundle. It performs no acquisition, hardware discovery, remote
access, service operation, GPIO/I2C activity, or RF transmission. A passing
measurement gate is not a hardware qualification because replay cannot prove
runtime authorization, the live session, cleanup, or quiescence.

The input metadata uses the strict `cw_acquired_capture` schema. It binds the
resolved plan, Phase 2 timeline, and IQ bytes and must agree with the plan's
format, exact sample count and rate, center frequency, zero-overflow limit,
fixed gain, disabled AGC and bias tee, discarded first read, and exact receiver
identity. `synthetic` is required to be false. This is an acquired-artifact
contract, not independent lifecycle proof.

Explicit local copies of the plan and expected-event documents are accepted
when their retained size and SHA-256 match exactly. Their recorded origin path
is provenance, not a requirement that replay recreate a Pi filesystem path.
The acquisition UTC records when capture actually began and is intentionally
distinct from the plan's earlier `resolved_utc`; neither timestamp is rewritten.

```text
wsprrypi-qualification compose-cw-acquired-replay \
  PLAN.json EXPECTED.json CAPTURE-METADATA.json REPLAY-DIRECTORY \
  --source-revision GIT_SHA

wsprrypi-qualification validate-cw-acquired-replay REPLAY-DIRECTORY
```

Composition refuses an existing destination, authenticates all source inputs,
and builds in a temporary sibling directory before one final rename. The
bundle contains canonical `plan.json`, `expected-events.json`,
`capture-metadata.json`, `capture.cf32`, `observations.json`, `mode-gate.json`,
`evidence-index.json`, `result.json`, and `SHA256SUMS`. All internal artifact
paths are portable relative filenames. The evidence index authenticates the
six upstream artifacts; the canonical manifest covers every retained file
except itself.

Validation rejects missing, extra, nested, symlinked, tampered, or incorrectly
indexed artifacts, reauthenticates every reference, checks the result against
the mode gate, verifies the canonical manifest, and recomputes the acquired-IQ
observations. Every replay result is `inconclusive` with
`qualification_claim: false` and explicitly absent lifecycle evidence. Phase 5
mock bounded lifecycle work is the next separately reviewed phase.

## Retained wspr5 intake lesson

The 2026-08-16 retained `wspr5` keyed-mode files were evaluated as possible
Phase 4 inputs. They contain one repetition per independently acquired file,
whereas the current resolved-plan contract requires at least three repetitions
described by one regenerated timeline and bound to one capture. The QRSS file
also lacks structured acquisition UTC metadata. The composer correctly cannot
represent those facts by pretending that separate files form one coherent
capture or by inventing metadata.

See [wspr5-retained-evidence-intake.md](wspr5-retained-evidence-intake.md) for
the authenticated inventory, exact blockers, promoted drift-aware analysis,
and deferred WSPR regression input.
