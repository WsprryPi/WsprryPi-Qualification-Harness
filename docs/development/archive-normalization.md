# Offline archive normalization and multi-capture composition

This slice adds portable, non-qualifying intake for preserved evidence that
predates the harness's immutable live-session contracts. It does not convert
historical evidence into a qualification claim and does not authorize Phase 7.

## Archive inventory

`inventory-archive` authenticates every entry in a canonical SHA-256 manifest,
rejects unsafe or duplicate paths, symlinks, non-regular files, root escape,
and size/hash mismatch, and writes a new schema-valid inventory outside the
source archive. Paths retained in the document are portable manifest-relative
POSIX paths. Classification is deterministic and deliberately limited to
artifact provenance; it never asserts lifecycle completeness or qualification.

The ignored `wspr5-pre-dkms-20260816` archive was exercised on 2026-08-16. Its
2,055,908-byte manifest has SHA-256
`3b5065490ad6f57ce7fa130840913f626bb17ae7fd65b8a7c3fa05abf2c64ce4`.
All 12,194 manifested files authenticated, totaling 16,656,431,611 bytes:

- 10,951 repository-snapshot files;
- 1,153 historical/ad hoc evidence or baseline files;
- 70 generated derivatives;
- 19 explicitly incomplete/failed artifacts; and
- one archive control document.

The generated inventory remains outside Git. These categories are intake
routing facts, not pass/fail results and not qualification states.

## Multi-capture relationship

`validate-cw-multi-capture` validates a session relationship above at least
three separately authenticated keyed-mode repetitions. Repetition numbering is
ordered and contiguous; acquisition IDs, capture paths, capture content,
metadata, and observations must be distinct and authenticated. Metadata and
observations bind the same normalized plan, mode, and per-repetition capture.

The validator does not concatenate IQ, call separate files coherent, or issue a
positive or negative hardware judgment. Until a reviewed lifecycle composer
binds runtime authorization, live execution, stopping, cleanup, and quiescence,
the only accepted final status is `inconclusive` and
`qualification_claim` is always false.

## Retained WSPR conversion regression

The retained 92,500,000-sample CF32 capture was translated with the maintained
complex mixer and windowed-sinc resampler for the 13:04, 13:06, and 13:08 UTC
slots. The source log records only monotonic capture timestamps; the regression
therefore used the session's recorded first-slot boundary and an inferred
five-second pre-slot margin. That alignment is sufficient for a diagnostic
replay but is not authoritative acquisition UTC evidence.

Each maintained-path WAV decoded the intended `AA0NT EM18 20` identity at
1500 Hz with the same additional symmetric detections near 1408, 1438, 1469,
1562, and 1594 Hz seen in the improvised conversion. Reducing the WAV amplitude
by 10x and 100x did not remove the multiplicity. A synthetic complex-carrier
test confirms that the maintained mixer itself produces only the intended
positive-frequency component and suppresses out-of-band aliases.

The truthful conclusion is that the duplicate detections are not repaired by
correct complex translation or amplitude reduction. They remain a retained
source/path-or-decoder regression for later diagnosis. No favorable image was
selected, no decode was discarded, and this replay makes no qualification
claim.

The ignored local regression WAV SHA-256 values were:

- 13:04: `d3b1fffd59898ce5c9d436e6af7ff529fe8542bd1fdccb3989005d594c49ca54`;
- 13:06: `a49101ce826037f160f90ba32dd729d37a920ffbfe443698d2338806129ae638`;
- 13:08: `3baf0704136546a687bd800b8ed857ecc67230ea89c146fd40d33c68b2e74a65`.

## Commands

```text
wsprrypi-qualification inventory-archive ARCHIVE_ROOT MANIFEST OUTPUT \
  --archive-id ARCHIVE_ID

wsprrypi-qualification validate-cw-multi-capture SESSION.json
```

## Remaining boundary

This slice provides deterministic intake and relationship validation only. A
future offline slice may diagnose the retained WSPR multiplicity further, but
it must not weaken the three-consecutive-frame identity contract. Phase 7 still
requires separate authorization and a harness-controlled bounded live Tone
session with current RF-path, ownership, cleanup, and quiescence evidence.
