# Turnkey campaign routing

`turnkey-campaign` is a thin operator surface over the maintained production
coordinators. It does not introduce another qualification protocol. TONE and
WSPR route to `RealQualificationSession`; QRSS, FSKCW, and DFCW route to the
live-keyed coordinator.

The subordinate resolved plan remains authoritative for hosts, deployments,
capabilities, deadlines, cleanup, evidence, and final qualification status.
The wrapper binds that complete plan and the small campaign request by path,
size, and SHA-256. It adds only the selected route and a campaign digest.

## Hardware-free workflow

Create a request with `campaign_id`, `mode`, and `execution_policy`. Then use:

```text
wsprrypi-qualification turnkey-campaign plan REQUEST.json MODE-PLAN.json RESOLVED.json
wsprrypi-qualification turnkey-campaign validate RESOLVED.json
wsprrypi-qualification turnkey-campaign rehearse RESOLVED.json RUNS
```

Planning and validation make no external calls and construct no production
adapter. Rehearsal checks the route and writes an authenticated immutable
bundle. Its result is always non-qualifying and contains no subordinate
lifecycle outcomes: rehearsal does not run preflight, install cleanup, or
simulate qualification.

## Live dispatch boundary

Live dispatch requires a request whose `execution_policy` is `live`, a complete
subordinate live plan, a non-empty operator identity, and an exact confirmation
of the resolved campaign digest:

```text
wsprrypi-qualification turnkey-campaign execute RESOLVED.json RUNS \
  --operator OPERATOR --work-directory WORK --ssh /absolute/path/to/ssh \
  --confirm-plan-sha256 SHA256 --enable-turnkey-live --enable-rf
```

No production adapter is imported or constructed until validation and exact
confirmation pass. After that gate, the selected existing coordinator owns all
runtime safety, cleanup, result precedence, and evidence. The command returns
the underlying authoritative bundle; it does not copy or reinterpret it.

Do not infer permission to contact a host, open a device, change a service, or
emit RF from a plan, rehearsal, saved digest, or this guide. Those operations
still require the precise current authority required by the subordinate
coordinator and repository contract.

## Simple five-mode `complete-test`

`complete-test` is the normal fixed campaign surface; the explicit-plan
`turnkey-campaign` commands above remain the advanced interface. The normal
command accepts two hosts and an exact SDR selector, packages the current harness,
copies the installed WsprryPi executable and configuration, stages the required
runtime, discovers the selected SDR immediately, and derives all five mode plans
rather than accepting five operator-authored mode plans. Each host receives an
independently owned durable per-campaign executable; temporary stages are removed,
and the retained aggregate is revalidated afterward. `--enable-rf` also confirms
the documented conducted
default: antenna disconnected and a direct 50-ohm SDR input through 20 dB
attenuation. Both endpoints
may be remote to the controller; the controller delegates execution to the
receiver. `--configuration PATH` remains an advanced development override:

```text
wsprrypi-qualification complete-test wspr4 wspr5 \
  --sdr driver=sdrplay,serial=2404058C60 --enable-rf \
  --progress-log /absolute/path/complete-test-progress.jsonl
wsprrypi-qualification complete-test wspr2 wspr5 \
  --sdr driver=sdrplay,serial=2404058C60 --transmitter-backend si5351 \
  --enable-rf
wsprrypi-qualification complete-test wspr4 wspr5 \
  --sdr driver=sdrplay,serial=2404058C60 --rehearse --configuration PATH
wsprrypi-qualification validate-complete-test CAMPAIGN-BUNDLE
```

The normal path copies `/usr/local/bin/wsprrypi` and
`/usr/local/etc/wsprrypi.ini`; it does not compile WsprryPi. Use
`--wsprrypi-binary REMOTE_PATH` and `--wsprrypi-config REMOTE_PATH` for an
explicit nonstandard installation. Missing installed inputs fail rather than
falling back to a checkout. Building current WsprryPi work is deliberately
opt-in:

The automatic composer defaults to `--transmitter-backend gpio`. An explicit
`--transmitter-backend si5351` selects the existing Si5351 production
coordinators for all five modes and binds the canonical transient settings:
I2C bus 1, address `0x60`, 27 MHz reference, CLK0, and drive strength 1. The
deployment includes a hash-bound read-only register-3 inspector and requires
all selected outputs to be disabled at preflight and cleanup. The backend never
falls back to GPIO or to the installed INI backend default.

An explicit `--transmitter-backend rp1_gpclk --rp1-route gpio4|gpio20
--rehearse --configuration CONFIG` selects the sealed RP1 hardware-free
composer. It binds two distinct logical roles on the named host, the canonical
endpoint/module, ABI v2, finite TONE, route-specific r2 compatibility identity,
`Experimental` enrollment, `live_output=1`, exact WsprryPi/component revisions,
receiver/RF-path identity, and one provenance-bound PPM source. The WsprryPi
argv uses its reviewed Pi-5 `--backend gpio --transmit-gpio 4|20` interface,
while the Harness retains `rp1_gpclk` as the authenticated backend identity.
The complete route contract prevents legacy fallback. Any RP1 invocation
without `--rehearse` fails before configuration loading or adapter construction.

`rp1_contracts.validate_preflight` and
`rp1_contracts.validate_operation_lifecycle` are the maintained semantic
boundaries for the future RP1 collector. Both require their packaged schemas
and reject extra or incomplete fields. The lifecycle validator additionally
requires the expected plan digest and prior generation from the caller. It
accepts cleanup failure only as `cleanup_failed`, never as a passing
measurement, and preserves authenticated post-launch receiver blockage as
`fixture_blocked`. These functions validate supplied documents only; they do
not open the endpoint, run diagnostics, construct SSH commands, or authorize
live execution.

```text
wsprrypi-qualification complete-test wspr4 wspr5 \
  --sdr driver=sdrplay,serial=2404058C60 \
  --wsprrypi-source /absolute/path/to/WsprryPi \
  --enable-rf
```

When the complete-test progress producer opens its JSONL file, it prints the
exact tracking command for another terminal. The command binds the running
Python interpreter and resolved viewer source, so it does not depend on a
separately installed console script or shell `PATH`:

```text
/path/to/python /path/to/progress_viewer.py /path/to/complete-test-progress.jsonl
```

The viewer keeps each logical campaign, mode, capture, frame, observation, and
cleanup step on one terminal row. Every row begins with its status glyph followed
immediately by a normalized `YYYY-MM-DDTHH:MM:SSZ` timestamp. Later queued,
started, and terminal records replace that row, and visible output is limited to
79 columns. WSPR frames complete independently at their 110.592-second RF-window
boundaries. Per-frame WAV generation and WSPR decoding each expose distinct
started and completed rows, so offline processing never creates an unexplained
multi-minute gap. The viewer exits after receiver delegation completes; direct receiver runs
exit at the campaign terminal record. Use `--replay` to render an existing log
without waiting for another record. This display is operational convenience;
the authenticated campaign bundle remains authoritative evidence.
The progress file uses `complete-test-progress.schema.json`. Each JSON Lines
record is flushed before execution continues. The CLI announces the absolute
path on stderr; stdout remains final-result JSON only. Delegated processes
mirror schema-identified records over stderr, and the controller resequences
them into its local log. Other stderr remains diagnostic text.

The default log is a new exclusive file in durable user-state storage on the
invoking host: macOS Application Support, Windows local application data, or
the Linux XDG state directory (with the documented home-state fallback).
`WSPQ_PROGRESS_DIR` provides a deployment-level override, while
`--progress-log` selects an exact file. Automatic stage cleanup never owns or
deletes this log; review and removal are explicit operator retention actions.
Defaults are 20 m, 14,097,100 Hz, `Q0QQQ`, `JJ00`, 0 dBm, keyed message `ET`,
0.7-second QRSS/FSKCW/DFCW dots, and 5.0 Hz FSKCW/DFCW separation. All are
named CLI overrides. WSPR retains the maintained three-frame contract; keyed
modes retain three independent transactions. The application shim derives the
WSPR dial frequency from its maintained 1500 Hz audio offset and derives the
FSKCW space and DFCW dash frequency below the primary according to the existing
protocol ordering.

The WSPR child deadline is calculated from its final slot schedule rather than
a fixed campaign allowance. It includes receiver setup derived from the
configured maximum read interval, the actual first-slot wait, the 370-second
coherent capture, byte-work-derived frame analysis, summary validation and
publication, cleanup, and final quiescence. A boundary-adjacent composition can
therefore select a later first slot without consuming time required after the
capture. Production checks the same containment again using the real session
start before installing the hard deadline.

Each keyed capture is sized from its final generated timeline, not a nominal
duration reconstructed elsewhere. The composer adds a one-second guard and
rounds upward to a whole sample, then expands the transaction and overall
deadlines when necessary. Production preflight and scheduled-plan analysis
revalidate the same bound after runtime quiet-time rebasing.

For every mode, the normal composer tunes the receiver 25 kHz below requested
RF while leaving transmitter and protocol frequencies unchanged. This places
the target at positive complex baseband, outside the maintained zero-IF DC
exclusion and inside the 200-kHz usable receiver span. The carrier gate selects
only within the requested target window; globally stronger features remain
diagnostic. Any expert-authored receiver center whose target window overlaps DC
or leaves the usable span is rejected during plan validation, before capture or
RF. This gate is relative acquisition, not calibrated-power or spectral-
compliance evidence.

The command validates the complete bounded execution before constructing a
production adapter. One deliberate invocation is the campaign authorization. Modes
run as TONE, WSPR, QRSS, FSKCW, DFCW. Cleanup, abort, preflight, fixture,
quiescence, or inconclusive outcomes stop all later modes. A failed TONE carrier
also stops the campaign. WSPR decode unqualification and keyed-mode
unqualification remain authoritative but do not prevent later independent modes,
so useful authenticated evidence is preserved. Modes stopped by the matrix are
`not_attempted`; cleanup and quiescence failures retain precedence and cannot be
hidden by aggregation.

Live exit codes are stable: 0 qualified, 2 invalid input, 3 technical blockage
or inconclusive evidence, 4 transmitter unqualification, 5 abort, and 6 cleanup
failure. Aggregate validation recomputes ordering, campaign and subordinate plan
bindings, linked result identities, cleanup precedence, final status, and
qualification scope even if a manifest has been rebuilt after tampering.

Rehearsal routes and authenticates five generated plans but contacts no host,
opens no receiver, inspects no service, touches no GPIO/I2C/GPCLK, and emits no
RF. It is non-qualifying. Same-host local production transport is rejected as
`unsupported_topology`; Track D owns that capability. Track E transmitter-PPM
provenance remains separate. Until that track closes, GPIO complete-test plans
pin each transmitter process to the resolved fixed manual PPM value and
explicitly disable the system-clock frequency estimate. The Tone server is not
an exception. This prevents configuration defaults or a changing Chrony
observation from altering RF during a campaign, but it does not establish the
manual value's provenance. Later prompt/qualification campaigns are also
outside this work.

The simple complete-test composer gives generated mode plans, expected-event
documents, resolved profiles, and production-dispatch wrapper inputs their own
campaign ownership. It writes them
under `OUTPUT_PARENT/complete-test-inputs/CAMPAIGN_ID`, outside source
repositories, runtime staging directories, and the published result bundle.
The resolved campaign records the exact directory and the policy
`retain_while_campaign_or_subordinate_result_exists` with `manual_only`
cleanup. Runtime staging cleanup therefore cannot invalidate a retained plan,
and post-cleanup validation must reopen and authenticate every generated input
from that store. Missing, changed, symlinked, or path-escaping inputs fail
closed. Normal WsprryPi installation and `/usr/local/etc/wsprrypi.ini` behavior
remain unchanged.

For production composition, a target checkout is immutable source provenance,
never a runtime directory. Any child-writable configuration is copied into a
new deployment-owned directory outside all protected Git roots before the final
plan is authorized. That plan binds the source file, staged file, protected
roots, and external process working directory. The helper verifies the boundary
at spawn and compares the post-process repository state with its exact original
baseline, including pre-existing dirty work. Mutation fails cleanup and is
reported without automatic repair.
The current production application/quiescence contracts support GPIO and
Si5351, not RP1. RP1 is available only for the sealed hardware-free rehearsal
above and returns `missing_capability` for live execution before adapter
construction. No RP1 value can leak into GPIO or Si5351 plans.
