# Slice 2: hardware-free exact-count capture contract

Slice 2 adds a portable C++17 capture engine, deterministic mock source,
optional SoapySDR adapter target, versioned capture-evidence schema, typed
Python evidence loader, native tests, and cross-platform CI configuration.

It does not authorize or perform SDR discovery or capture. It does not qualify
an SDR, receiver, transmitter, backend, band, or RF path. Carrier analysis,
audio conversion, decoding, transports, external process supervision, and RF
operation remain outside this slice.

## Build and test

The normal development configuration cannot link or instantiate SoapySDR:

```text
cmake -S . -B build-native -DWSPQ_BUILD_SOAPY=OFF -DWSPQ_BUILD_TESTS=ON
cmake --build build-native --config Release
ctest --test-dir build-native -C Release --output-on-failure
```

`WSPQ_BUILD_SOAPY` defaults to `OFF`. Enabling it only compiles the physical
adapter when SoapySDR development files are already installed. Configuration
and compilation do not authorize executing that adapter. Before any execution
that could enumerate, open, configure, or stream from an SDR, stop and obtain
new approval for the exact device and bounded plan.

The deterministic mock executable accepts structured process arguments:

```text
wspq-capture-mock SAMPLE_COUNT OUTPUT SUCCESS_EVIDENCE CAPTURE_ID [success|short-read]
```

Its failure evidence path is `SUCCESS_EVIDENCE.failure.json`. Production code
never silently substitutes the mock for a SoapySDR source.

## Capture and wire-format behavior

The engine validates identifiers, UTF-8 text, settings, destinations, limits,
and size arithmetic before touching a source. Existing output, evidence,
failure-evidence, or incomplete paths cause refusal and are preserved.

After configuration, requested and actual receiver identities and settings
must agree. The first successful read is discarded and is excluded from
retained-sample, overflow, and clipping statistics. Legal partial reads
continue until the exact retained count is reached. Reads, read calls, and the
monotonic in-process deadline are bounded. Baseline operation permits zero
overflows.

The maintained CF32 wire format is exactly:

- interleaved real then imaginary components;
- IEEE-754 binary32 components;
- little-endian byte order; and
- eight bytes per complex sample.

The writer serializes each component explicitly. It does not write the object
representation of `std::complex<float>`. Compilation requires eight-bit bytes,
four-byte IEEE-754 `float`, and checked buffer/file-size arithmetic.

Data is written to `OUTPUT.incomplete`. A successful file must contain exactly
`sample_count * 8` bytes. It is then promoted within the same filesystem and
hashed. Failure removes files created by that attempt and never publishes
final-looking IQ or success evidence.

## Success and failure evidence

Success evidence is written to the requested metadata path. Failure evidence
uses the explicitly failure-identifying path and records the primary failure,
additional causes, counters, available requested/actual settings, cleanup
steps, final exit code, and the absence of complete IQ. When an incomplete IQ
file existed, its byte size and SHA-256 are recorded before removal.

Both forms record the wire format, limits, UTC phase timestamps, monotonic
elapsed duration, first-read policy, sample/read/timeout/overflow/clipping
counts, and output state. JSON Schema uses discriminated success and failure
branches. Python validation rejects contradictory outcomes.

Cleanup failure overrides the exit code. A prior capture cause remains the
primary cause, while `cleanup` is added separately. Cleanup attempts every
remaining step after a step fails. Repeated cleanup is idempotent.

If evidence writing fails, the helper preserves the prior cause internally,
adds `evidence_write_failed`, removes any apparently successful IQ, and emits
an actionable stderr message. It exits `11` unless cleanup already failed, in
which case cleanup retains precedence with exit `9`.

## Time and timeout semantics

The helper observes its own UTC timestamps. Callers do not supply evidence
timestamps. A separately injected monotonic clock makes deadline tests
deterministic. Deadline checks occur before and after configuration, the
discarded first read, retained reads, and cleanup.

The Soapy adapter also passes an explicit per-read timeout to `readStream`.
These in-process checks detect a third-party operation that returns after the
deadline, but cannot interrupt a configuration, read, or cleanup call that
blocks permanently. Slice 4 must provide the external hard process deadline
and supervisor needed for that case. Slice 2 does not claim otherwise.

## Exit codes

- `0`: exact-count success with verified cleanup and evidence
- `1`: capture, source, first-read, short-read, or I/O failure
- `2`: invalid arguments or refused physical-adapter invocation
- `3`: overflow exceeded policy
- `4`: timeout, read-call limit, or elapsed deadline exceeded
- `5`: cancellation
- `6`: receiver identity mismatch
- `7`: actual-setting, gain, or non-finite-setting mismatch
- `8`: clipping or non-finite sample detected
- `9`: cleanup failed; this overrides prior success/failure exit status
- `10`: output byte-size, hash, or promotion failure
- `11`: evidence could not be completed

## Fixtures and retention

Native tests generate small deterministic CF32 and evidence files in uniquely
named platform temporary directories and delete them after each test. No
recording is committed. Golden bytes cover signed fractional samples and are
independently SHA-256 checked. Committed synthetic fixtures should remain below
1 MiB per file and document their generator and purpose.

Large IQ, WAV, run directories, compiled binaries, build trees, and incomplete
artifacts are ignored. Future approved physical or replay captures remain
outside Git; their durable location, format, byte order, sample count, size,
and SHA-256 belong in evidence.

## Next unfinished step

Slice 3 adds offline carrier analysis, CF32 translation and WAV generation,
independent `wsprd` integration, and deterministic synthetic fixtures. See
`slice-3.md`.
