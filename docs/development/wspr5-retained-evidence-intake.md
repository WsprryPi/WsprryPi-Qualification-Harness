# wspr5 retained-evidence intake

This record inventories the retained 2026-08-16 `wspr5` artifacts inspected
during the offline evidence-intake and analyzer-hardening slice. It is an
engineering input record, not an executable profile, lifecycle record, or
qualification result. No retained host file was modified and no hardware,
service, SDR, transmitter, or RF operation was performed during intake.

## Source boundary

- Host: `wspr5`
- Session root:
  `/home/pi/issue401-si5351-2200m-current-20260816T125941Z`
- Recorded WsprryPi parent revision:
  `80237f6f53e66b78784862e371b0fd5de45ccfea`
- Recorded transmitter revision:
  `c416e0f4de608164a10d7f0fe2f5adf6f5b911ce`
- `provenance.txt`: 232 bytes,
  `0667e5e297d7f0f614fd7aecd31de7ce77f7f8bdf90dabf1e796914e2ba09d45`

The provenance file leaves `binary_sha256` empty. The source revisions are
therefore retained facts, but exact executed-binary identity is unavailable
from that file.

## Representative acquired captures

All paths below are relative to the session root.

| Role | Path | Bytes | SHA-256 | Structured metadata |
| --- | --- | ---: | --- | --- |
| QRSS repetition 1 | `qrss/rep1/qrss.cf32` | 80,000,000 | `a56a143efd3d00376130f859241d1fe82ba10070541c41851ac8680d14e673d7` | unavailable; `capture.log` only |
| FSKCW usable repetition 1 | `fskcw/rep1-retry2/fskcw.cf32` | 80,000,000 | `231c563ee72dc52d7bb88c6251e82c37cebf4f49aa66d851df6c11731c4439e8` | `capture.json`, 2,525 bytes, `00122e44f09d53f5023ba60285373c860f744ea4012ff37bb4a57b03530b9a2b` |
| DFCW repetition 1 | `dfcw/rep1/dfcw.cf32` | 80,000,000 | `1b60bceae757fe2121b90c90a3b0fd6c27f656e3bce9c1d5b542aaa356663a6f` | `capture.json`, 2,508 bytes, `a841a53a86f0b858ae6042529ab4f3164ad469ed187d8b810edb556c71d5577a` |

The FSKCW and DFCW metadata directly record CF32, 250,000 samples/s,
162,500 Hz center frequency, 10 dB fixed gain, disabled AGC and bias tee,
discarded first read, exact 10,000,000 retained samples, zero overflow, zero
clipping, receiver serial `2404058C60`, bounded cleanup, and acquisition UTC.
The QRSS capture log records the sample count and receiver settings but only
monotonic capture times. Its log is 448 bytes with SHA-256
`2e533d9fedf06de7a12fe241e5cf9083ce0bebde61dd1423d58d021de09cc6d3`.

## Replay-contract result

None of these representative files can truthfully enter the current Phase 4
composer without changing its contracts:

1. Each file contains one keyed repetition, while `cw-mode-plan.schema.json`
   requires at least three repetitions in the plan and the regenerated event
   timeline must fit and describe the single authenticated capture.
2. The QRSS file additionally lacks the structured acquired-capture metadata
   and UTC binding required by `cw-acquired-capture.schema.json`.
3. The three separate repetitions cannot be concatenated or represented as one
   coherent acquisition without creating a new derived artifact and an
   explicit multi-capture provenance contract.

No large IQ file was copied into the disposable analysis area during this
intake slice after these blockers were established. A later, separately made
pre-DKMS preservation snapshot now exists under the repository's ignored
`local/wspr5-pre-dkms-20260816/` tree. That whole-host archive is preservation
input, not a portable harness fixture or a qualification bundle, and remains
outside Git.
The schemas were not weakened and no invented RF-path, acquisition-time,
binary, drive, clock, or lifecycle fact was supplied. A future live supervisor
should either capture the required repetitions coherently or introduce a
reviewed multi-capture session composition layer above independently valid
single-capture measurement bundles.

## Analyzer prototypes inspected

These source-host scripts were read as disposable prototypes and were not
copied into the repository:

| Script | Bytes | SHA-256 | Useful behavior |
| --- | ---: | --- | --- |
| `issue401-si5351-2200m-qualification-20260815/tone_analysis.py` | 3,577 | `3a790dfda8267571634142d1cbf3067945337ca8398a0b9181762cb3cee63e3a` | averaged on/off spectrum and best-20-Hz carrier concentration |
| `issue401-si5351-2200m-qualification-20260815/analyze_qrss.py` | 3,948 | `82374a1be71595f9795baa1d5c280f1bf33fd9ff32a0f85376109d56379ca3e0` | onset search and envelope/template comparison |
| `issue401-wspr4-6m-keyed-qualification-20260815/analyze_issue401_keyed.py` | 8,572 | `975a2a1d67535731caca5c96d4a906d5679d94500d85a5a5ae9bdacda4d3c1d0` | common-drift plus commanded-state regression and transition-local signed shifts |

The harness already had the carrier, expected-event, onset, continuity,
message-reconstruction, replay, and manifest foundations. This slice promoted
only the generic shifted-frequency model: it fits common linear drift and tone
state together, records measured spacing, drift, residual, and transition
counts, and fails closed on wrong spacing, reversed assignment, excessive
drift, excessive residual, missing state coverage, or an unstable fit.

## Deferred WSPR regression input

`wspr/three-frames.cf32` is 740,000,000 bytes with SHA-256
`5c91446a5c4b325d2008918fc7e46c33e34aa261b385a23211cff7acc7cb4b29`.
Its capture log is 451 bytes with SHA-256
`a878cd26f9eae40b04b12331c1c767fd058ff2d54d5514d0196c6e75fe94e722`.
The first retained `wsprd.log` is 281 bytes with SHA-256
`0272edf98041b14f44fc035c8d73df95d9441428f4cb0a35abb81a4a840a9b70`.

The improvised audio conversion produced repeated symmetric copies of the
same decoded identity. That capture is a useful future intended-side,
conjugate-image, mixing, filtering, and resampling regression input, but WSPR
conversion repair is outside this slice.

## Remaining boundary

This intake did not authenticate current RF-path facts, exact executable hash,
runtime authorization, service ownership, transmitter cleanup, or complete
backend quiescence as one bound session. The promoted analyzer remains offline
measurement code and cannot establish those lifecycle facts.
