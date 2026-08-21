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

## Generate a timeline

```text
wsprrypi-qualification generate-cw-expected-events \
  PLAN.json EXPECTED.json --source-revision 40_HEX_DIGIT_HARNESS_REVISION
```

The command validates the plan, refuses to overwrite output, generates all
declared repetitions with leading/trailing quiet, and atomically writes an
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
