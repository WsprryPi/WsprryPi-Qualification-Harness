# Provenance

## Historical source

The files under `historical/issue379-long-stability/` were copied from:

`pi@wspr5.local:/home/pi/issue379-long-stability/`

The files under `historical/issue379-frame-analysis/` were copied from:

`pi@wspr5.local:/home/pi/issue379-si5351-frame-valid/`

Copy date: 2026-08-11. Compiled binaries and large CF32/WAV evidence were not
copied. File hashes are recorded in `SHA256SUMS`.

## Authoritative research inputs

At seed creation, the procedure and retained evidence lived in the sibling
WsprryPi repository:

- `docs/research/issue-379-conducted-qualification-procedure.md`
- `docs/research/issue-379-si5351-stability.md`
- `docs/research/issue-390-transmitter-qualification.md`
- `docs/research/issue-390-transmitter-qualification/`

The historical helpers were explicitly described there as evidence rather
than supported project tools. The new project must preserve that distinction.

## Known historical bench facts

- RSP1B serial: `2404058C60`.
- Capture: CF32, 250 ksps, 200 kHz bandwidth, AGC off, fixed gain.
- Three-frame capture: 370 seconds, 92,500,000 samples, zero overflows required.
- Conducted path: attenuation, shielded 50-ohm load/sample point, no antenna.
- Qualification identity: `AA0NT EM18 20` unless explicitly changed.
- Decoder used in retained evidence: WSJT-X 2.7.0 `wsprd`.

These are provenance, not universal defaults or current-state assertions.
