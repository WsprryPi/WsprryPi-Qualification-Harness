# Bounded qualification simulator

`simulate-qualification` is a hardware-free integration exercise. It launches
owned local child processes, creates deterministic compact CF32 fixtures, runs
the maintained carrier and audio paths, invokes a fake decoder three separate
times, verifies cleanup, and publishes an immutable manifest-covered bundle.

The quick CI mode represents a logical 370-second, 92,500,000-sample WSPR
capture with an explicitly distinct 243,000-sample compact fixture. It records
logical duration, physical fixture count, time scale, actual elapsed time, and
the outer deadline. It never represents compact bytes as a physical capture.

Run it with a new portable UTC run ID:

```text
wsprrypi-qualification simulate-qualification ./simulator-runs \
  --run-id 20260812T120000Z-local-smoke
```

The parent canonicalizes the output directory once before collision checks or
worker launch. The parent request, worker plans, returned path, and bundle
location therefore use one portable path identity, including relative paths,
paths with spaces, and platform aliases.

Deterministic `carrier_fail` and `cleanup_fail` injections exercise gate
suppression and cleanup precedence. Stage-specific `*_timeout` and `*_nonzero`
injections stop advancement and retain an `aborted` or `cleanup_failed` bundle.
Existing output directories are never
reused. The ordinary run is bounded to 15 seconds; each child is bounded to one
second. Overall deadlines below the measured and tested two-second portability
floor are rejected before creating an output directory; the remaining monotonic
budget is checked at every stage and bounds every child. The entire simulation
runs in a separate worker process supervised by the portable parent. The parent
enforces the approved overall deadline plus a fixed 0.5-second worker-reaping
margin, so a blocked carrier, WAV, decoder, or publication stage cannot silently
run past the bound or promote a completed bundle. Deterministic stage-hang
injections exercise that outer boundary. The optional
full-duration mode remains future work and is not run by CI.

The decode summary authenticates exactly three consecutive even-UTC slots and
their WAV and decoder-document hashes. Before atomic promotion, the bundle
validator rechecks the resolved-plan digest, lifecycle, children, carrier gate,
decode summary, fully derived final result, artifact index, and complete
`SHA256SUMS` file. It also verifies exact CF32 byte/sample counts and the WAV
sample rate, channel count, sample width, and frame count against the resolved
compact-fixture contract. Rebuilding hashes after changing those bytes cannot
make contradictory evidence valid.
WAV inspection parses the complete RIFF container: its declared size must equal
the physical file, chunk boundaries and padding must be valid, exactly one
canonical PCM `fmt ` chunk and one exact-length `data` chunk must exist, and no
trailing bytes are permitted. Decoder-library properties remain a secondary
check rather than the container trust boundary.
Capabilities, simulated confirmation, and read-only quiescence documents have
strict schemas and are semantically bound to the resolved plan and cleanup
outcome. Each lifecycle has an exact required artifact set: missing, extra, or
post-gate files are rejected even if both hash inventories are regenerated.
Failed publication remains in a uniquely named `.incomplete-*` directory for
diagnosis and can never be mistaken for a completed run.

The simulator proves portable composition, real subprocess deadlines, offline
analysis, decoder invocation, evidence publication, and fail-closed result
semantics. It does not prove SSH, Raspberry Pi OS service behavior, physical
SDR capture, GPIO/I2C behavior, RF safety, transmitter behavior, or hardware
qualification. A successful simulation is always `inconclusive` with
`qualification_claim: false`; it never authorizes RF.

The next gates remain separately authorized actual-host checks on `wspr4` and
`wspr5`, beginning with verification that ongoing work will not be interrupted.
