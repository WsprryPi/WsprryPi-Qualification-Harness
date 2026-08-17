# Slice 3: hardware-free offline carrier and decoder pipeline

Slice 3 adds maintained offline CF32 inspection, RF-off-subtracted carrier
analysis, rational UTC slot mapping, 1500 Hz channel translation, timestamped
12 kHz mono PCM WAV generation, bounded command-line `wsprd` execution, exact
identity checking, deterministic fixtures, and versioned evidence schemas.

It does not enumerate or operate an SDR, run WsprryPi, transmit RF, control a
remote host, modify services, supervise a live lifecycle, or qualify any
backend, band, board, receiver, or RF path. Synthetic and replay success is an
offline gate result, never a live `qualified` result.

## Venv and dependencies

Python 3.11 or newer is required. Create and use a project venv for the
harness, tests, and fixture generator:

```text
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e ".[dev]"
```

On native Windows, use `.venv\Scripts\python.exe` in place of
`.venv/bin/python`. `requirements.txt` captures runtime ranges and must remain
synchronized with `pyproject.toml`. NumPy supplies portable complex arrays,
FFT, mixing, and resampling. `wsprd` is discovered externally and is not
vendored. FFmpeg is not used by the maintained conversion path.

Executable discovery checks `PATH` first. On macOS it also checks the standard
WSJT-X application-bundle locations, including
`/Applications/wsjtx.app/Contents/MacOS/wsprd`; native Windows discovery also
checks standard `Program Files` WSJT-X layouts using Windows path semantics.

Generate the small review fixture only from the venv:

```text
.venv/bin/python scripts/generate_slice3_fixtures.py OUTPUT_DIRECTORY
```

The generator refuses an existing output directory. Tests generate fixtures
in unique platform temporary directories and delete them through pytest. Large
IQ, WAV, evidence runs, binaries, and build trees remain outside Git. Any
third-party fixture requires documented source, license, and redistribution
permission before it may be committed.

## Carrier contract

`analyze-carrier` compares separate RF-off and RF-on CF32 files and requires
the bench profile, test profile, and both Slice 2 capture-metadata documents.
Requested frequency and gate thresholds come only from the test profile;
receiver identity and stable settings come from both profiles. It validates successful
exact-count captures, hashes, sizes, overflow/clipping/timeout counts, receiver
identity, complete actual settings, sample rate, center, fixed gain, bandwidth,
AGC, bias tee, channel, and format before comparison. Direct API calls may omit
metadata only for explicitly labeled synthetic fixtures; that evidence records
the limitation and is not acceptable as acquired qualification evidence.

RF-off and RF-on requested and retained sample counts are validated exactly for
each capture but may differ. The analyzer averages every complete FFT block in
each capture independently; it never truncates the longer capture, repeats the
shorter capture, or assigns fabricated equal statistical weight. Helper/wire
contracts, settings, and clipping thresholds must still match.

The default analysis uses non-overlapping 262,144-sample Hann-window blocks,
averages power in the linear domain, and subtracts averaged RF-off power from
averaged RF-on power. It inspects the full Nyquist span except the configured
center/DC exclusion. Resolved bins have positive residual power and RF-on
power at least 6 dB above the corresponding per-frequency-bin RF-off power.
This preserves the retained machine-readable Issue 379 definition; no scalar
median baseline is substituted. Negative residuals
are excluded rather than clipped into evidence. The best 20 Hz channel is the
largest contiguous bin sum divided by all resolved power.

For an uncalibrated receiver, the gate uses bounded relative acquisition: the
strongest transmitter-added feature must be within 500 Hz of the requested
frequency and at least 10 dB above its RF-off power. The historical 100-Hz
offset and 0.50 best-20-Hz share remain nominal diagnostics rather than
pass/fail criteria. This tolerates plausible receiver error and thermal drift
without making a calibrated-frequency claim.
No resolved transmitter-added power is `inconclusive`, not transmitter
unqualification. These relative captured-span metrics are neither calibrated
power nor spectral-compliance measurements.

## Frequency, WAV, and UTC contract

The CF32 sign convention is positive complex frequency above receiver center.
For selected RF frequency `selected`, center `center`, and target audio 1500
Hz, conversion records and applies:

```text
mix_hz = (selected - center) - 1500
audio[n] = real(iq[n] * exp(-j * 2 * pi * mix_hz * n / fs))
```

A deterministic Hann-windowed sinc interpolator converts to 12,000 samples/s.
The output is normalized and written as mono signed 16-bit little-endian PCM
WAV. Conversion records the filter, rates, shift, scaling, sample boundaries,
hashes, and real-audio conjugate-image policy.

The production path requires the validated capture document and uses its
`retained_capture_start_utc` as authoritative sample zero. Caller-supplied
capture timestamps cannot relabel acquired evidence. Clipping uses the native
helper's per-I-or-Q-component threshold, not complex magnitude.

UTC-to-sample mapping uses integer microseconds and rational arithmetic. Slots
must begin on even UTC two-minute boundaries and retain the configured leading
and trailing margins (five seconds by default). Thus a coherent 370-second
capture at 250 ksps contains exactly 92,500,000 samples and can cover three
consecutive 120-second slots with five-second outer margins. Slot filenames use
`YYYYMMDDTHHMMSSZ.wav`; the acquired path derives and enforces that name.
WAV data is written and validated under a same-filesystem `.incomplete-*`
name. If evidence validation/publication fails after promotion, the newly
created WAV is rolled back so no orphan final-looking artifact remains.

## Independent decoder contract

`decode-wspr` discovers `wsprd` or accepts an explicit executable, invokes it
with a structured argument list and `shell=False`, applies a portable timeout,
and records its absolute path, SHA-256, bounded version query, arguments,
return code, stdout, and stderr. Every output line is retained. Parsed Type 1 lines must exactly match
the profile callsign, grid, and power. A matching decode near positive 1500 Hz
is intended; other real-audio decodes are retained as companion or conjugate
images. Decoder UTC must match the evidence slot. A schema-validated decode
summary rejects duplicate, reordered, skipped, nonconsecutive, and
context-mismatched slots before applying the profile's required consecutive
decode count.

The acquired decoder does not accept a second test-profile argument. It
authenticates the profile already retained by the audio evidence and derives
the expected identity and gate from that artifact. Summary inputs are decoder
evidence paths, not caller-supplied JSON objects. Each document is schema
validated; retained profile, capture, IQ, WAV, tool, data-file, and evidence
hashes are checked; decoder parsing and gate outcomes are recomputed before a
summary can be published.

Published artifact records use canonical absolute paths. Caller-relative paths
are resolved when evidence is created, so later validation does not depend on
the reviewer's current working directory. This is the Slice 3 policy for
external artifacts; a future immutable bundle may additionally define and
validate bundle-relative paths during evidence packaging.

Acquired carrier evidence retains canonical size-and-SHA-256 records for both
IQ captures, both capture-metadata documents, and both profiles. Its loader
re-authenticates the receiver and exact-count contracts and deterministically
recomputes the full carrier analysis before accepting the retained metrics or
gate outcome. Synthetic carrier output is explicitly marked and is not
publishable acquired qualification evidence.
RF-off and RF-on must be distinct capture events: their capture IDs, metadata
artifacts, and IQ artifacts must all differ even though receiver settings and
exact sample counts must match. This prevents one observation from being used
as both sides of the subtraction.

A successful capture metadata `output.path` is part of the authenticated
contract. Absolute paths are canonicalized directly; relative paths are
resolved against the capture-metadata file's parent directory, never the
process working directory. The resolved location must be the authenticated IQ
file in addition to matching its size and SHA-256.

The audio loader recomputes the complete resolved profile context, capture UTC
and exact sample bounds, slot offset, maintained 1500 Hz translation contract,
WAV format, and all retained artifact hashes. Decode summaries require exactly
the authenticated test profile's planned frame count, as well as ordered,
unique, consecutive even UTC slots. The preserved qualification profile plans
three frames and requires three consecutive correct decodes.
It also regenerates the maintained normalized PCM payload from the retained IQ
and compares every WAV payload byte, the exact normalization scale, and the
fixed conjugate-image policy. Matching headers and an updated file hash alone
cannot authenticate a replacement WAV.

The acquired path assigns each decoder invocation a new, slot-specific data
directory with `wsprd -a`; it refuses reuse and records every decoder-created
file. Decoder state is therefore not written into the caller's current working
directory or silently shared between slots.
The evidence loader also requires the exact structured `-a` invocation and an
exact inventory of every regular file in that directory; omitted, foreign, or
changed files invalidate the evidence.
The new decoder data directory is transactional: launch, artifact-inspection,
or evidence-publication failure removes only the directory created by that
invocation. Successfully published timeout evidence and successful executions
retain their authenticated directories.
All deterministic validation—including UTC awareness and acquired even-slot
checks—occurs before that directory is created.

Decoder evidence distinguishes expected identity presence from intended-signal
presence. A conjugate-only copy sets `expected_identity_found` but not
`expected_intended_signal_found` and therefore cannot pass the decode gate.

Version-query evidence records arguments, return code, complete output,
timeout state, and launch failure. Publication and validation use the same
deterministic retained-output interpretation for `version` and
`unavailable_reason`; validation never reruns the query.

A missing decoder, timeout, or tool failure is dependency/fixture blockage,
not transmitter unqualification. No GUI automation is used. A real decoder
smoke test is skipped when `wsprd` is absent; fake executables cover process,
timeout, log, malformed-output, and identity behavior in unit tests.

## Evidence and deferred analyses

Review-facing and packaged schemas cover carrier, audio, per-slot decoder, and
decode-summary evidence.
Outputs are new-file-only, deterministic JSON, and compatible with the Slice 1
SHA-256 manifest. Raw IQ retention remains policy-controlled outside Git.

Historical symbol, tone-spacing, slow-drift, and transition scripts are
preserved but not promoted in Slice 3. They depend on capture-specific manual
onsets, expected symbol sequences, or estimator assumptions that are not yet a
general reviewed contract. Inventing a universal qualification gate from them
would change evidence meaning. They remain optional future offline work.

## Validation and next gate

Run formatting, linting, strict mypy, pytest, package build and wheel smoke
tests from the venv, plus the existing mock-only CMake/CTest path. CI covers
current Ubuntu, macOS, and native Windows. Actual hosted runners and Raspberry
Pi OS remain separate validation environments.

The next unfinished step is Slice 4: local/SSH transports, capability adapters,
external hard deadlines, supervision, cleanup state machines, and injected
failure tests. Slice 4 remains hardware-free and separately authorized.
