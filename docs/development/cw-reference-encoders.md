# Independent CW-family reference encoders

The encoder converts one normalized, schema-valid CW-family plan into the expected
answer used by later analyzers. The encoder is deliberately independent of
transmitter logs, captured IQ, clocks, devices, and operating-system behavior.
Its output remains non-qualifying.

## Supported definitions

- `wspq-tone@v1`, `wspq-cw@v1`, `wspq-qrss@v1`, and `wspq-fskcw@v1` are the
  maintained harness definitions.
- `wsprrypi-dfcw@v1` binds the reviewed semantics in WsprryPi revision
  `854b39d37433c5b98d4ed43784f0b9819cf6143e`: equal-duration dot and dash
  elements on distinct frequencies, RF-off gaps, and gap multipliers
  `0.333333`, `1`, and `3`.

Unknown names or versions fail closed. DFCW v1 also rejects any drift from its
reviewed gap values. The supported Morse repertoire matches the reviewed
WsprryPi execution-plan compiler.

## Canonical hardware-free keyed scenario

Generic hardware-free QRSS, FSKCW, and DFCW tests use message `ET`, a
`0.7`-second dot, and one message per transaction. `E` is one Morse dot and `T`
is one Morse dash, so this is the shortest deterministic message that exercises
both element lengths. The short dot keeps synthetic, replay, and mock tests
bounded while remaining long enough for their maintained timing and frequency
analysis contracts. FSKCW and DFCW use a `5.0` Hz primary-to-secondary
separation; the separation does not apply to QRSS.

Under the existing mode definitions, the message portion of each transaction is:

- QRSS: `0.7` seconds keyed dot, `2.1` seconds RF-off inter-character gap,
  then `2.1` seconds keyed dash;
- FSKCW: `0.7` seconds mark, `2.1` seconds continuous space, then `2.1`
  seconds mark, preserving continuous carrier across all `4.9` seconds; and
- DFCW: `0.7` seconds at the dot frequency, `0.7` seconds RF-off
  inter-character separation, then `0.7` seconds at the dash frequency.

`hardware_free_keyed_protocol` centralizes these generic values but requires an
explicit primary frequency and explicit leading/trailing quiet intervals. It
returns only a protocol fragment and cannot resolve or authorize a live plan.
Callers may explicitly override the message, dot duration, and shifted-mode
separation for special-purpose hardware-free tests. Qualification-style
multi-capture contracts use exactly three independent acquisitions, each with
one message; they do not turn three messages in one process or capture into
three transactions.

This is a testing default, not an operator recommendation, authorization, or
regulatory claim. A passing test of this vector does not transfer qualification
to another hardware unit, output or pin, backend, band, drive level, frequency
shift, timing, build, receiver, RF path, or operating environment.

## Generate a timeline

```text
wsprrypi-qualification generate-cw-expected-events \
  PLAN.json EXPECTED.json --source-revision 40_HEX_DIGIT_HARNESS_REVISION
```

The command validates the plan, refuses to overwrite output, generates the
explicitly declared message count with leading/trailing quiet, and atomically writes an
expected-event document bound to the plan's canonical path, size, and SHA-256.
The source revision is explicit because an installed wheel need not have access
to a Git checkout.

Contract-chain validation regenerates this timeline and demands exact event
equality. A plausible caller-authored or mutated timeline therefore cannot pass
on schema shape alone.

## Boundary

This command generates no IQ, performs no signal analysis, invokes no external
program, and touches no hardware. Synthetic-IQ generation and analysis are
separate non-qualifying operations.
