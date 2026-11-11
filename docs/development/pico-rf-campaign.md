# Pico RF campaigns

`complete-test` supports a distinct `pico` target over the production USB WTP
and USB management interfaces. Complete finite jobs execute on RP2350. The
Harness uses the selected Pico source's encoders, its existing JobService,
the exact-count SoapySDR capture helper, independent Morse references, carrier
analysis, and WSJT-X `wsprd`. It does not run Pi GPIO or I²C helpers for Pico.

## Configuration matrix

Run each combination as a separate campaign and retain its own result:

| Engine | GPS/PPS use | UTC selection | Automatic correction |
| --- | --- | --- | --- |
| `pio-dma-gp2` | Disabled | Explicit `sntp` or `controller` | Off |
| `pio-dma-gp2` | Enabled | `gps` | Explicit off and on subcases |
| `si5351` | Disabled | Explicit `sntp` or `controller` | Off |
| `si5351` | Enabled | `gps` | Explicit off and on subcases |

Each configuration composes TONE, WSPR, QRSS, FSKCW, and DFCW. Each selected
mode requires three independent WTP/capture transactions. A Tone transaction
has three on/off cycles; those cycles do not count as independent observations.
Each WSPR observation first runs a separate finite carrier gate, then captures
one complete ordinary Type 1 frame at an even UTC two-minute boundary. Live
paired Type 2/3 station plans are refused before USB access: the maintained
decode identity classifier currently covers Type 1. The offline production
compiler can compose paired plans without making a live qualification claim.

The supported direct output is GP2 with the ordinary 138 MHz clock profile.
Si5351 requires the product's existing supported, physically qualified profile
and verified register-control/output-disable state. Experimental automatic
Si5351 correction requires the existing CLK2/input-only GP7 feedback. No new
crystal-board support is implied.

## Host and prerequisites

Portable composition, rehearsal, result validation, and signal analysis support
native Windows, macOS, and Linux. Live USB supports native macOS and Linux
only. It fails before device access on Windows and WSL. Execute on the host
that physically owns both Pico USB and the SDR; this route does not delegate
USB/SDR access over SSH. A controller can invoke the installed command on that
host through its independently authorized management connection.

Use an installed Python 3.11+ Harness environment, a real Soapy-enabled capture
helper, and an installed `wsprd` for WSPR. Build the offline compiler against
the intended Pico checkout, without downloading an SDK or building firmware:

```text
cmake -S HARNESS_SOURCE -B COMPILER_BUILD -DWSPQ_BUILD_SOAPY=OFF -DWSPQ_PICO_SOURCE=PICO_SOURCE
cmake --build COMPILER_BUILD --config Release --target wspq-pico-compile
```

The compiler embeds the Pico Git revision and hashes of its actual encoder and
WTP source inputs. Composition checks those hashes against the selected
checkout and refuses a stale compiler. The plan binds the compiler binary,
encoder inputs, selected installer/discovery script, and a separate content
identity for Harness controller/analyzer sources. The target's observed
firmware version and boot identity are retained separately. A compiler source
revision alone does not establish which bytes are installed on a board.

Install or upgrade firmware separately through Pico's shared USB installer and
retain the exact developer/release bundle and same-board reference. Campaigns
never flash, restart, provision, change an engine, or rewrite saved settings.
The selected configuration must already be active and usable.

### Continuous UTC observations

Leave existing NTP services and Pico SNTP/GPS updates running. A recorded
snapshot is an observation, not a request to freeze the clock. GPS-enabled
rows continue using GPS; an already operational SNTP source may be selected
for GPS-disabled rows without a controller-time dependency.

An explicitly selected `controller` route binds refresh policy version 1 into
the composed plan. Firmware controller observations expire after 90 seconds;
the harness refreshes them every 30 seconds through its single owned management
client. It services the existing main-thread preparation/polling path during
WSPR slot waits, capture readiness, and armed/running jobs, and reacquires time
before admission or the next job after lengthy offline analysis. It creates no
competing USB owner or unsynchronized background reader. A competing job owner
blocks a time submission. Cleanup does not depend on another clock update.

The current host-time capability discovers `chronyc`, pins its executable
identity, and observes bounded tracking CSV without changing the service.
Missing host capability fails before WTP/RF; portable planning is unaffected,
and SNTP/GPS routes do not require this adapter. The submitted bound includes
chrony's conditional offset/dispersion/delay error, collection time, the full
USB sample midpoint radius, wall/monotonic disagreement across response and
ACK, and the maintained processing margin. ACK delay never recenters the
sample. Every exchange has a one-second outer bound and no mutation retry.

`controller-time.jsonl` retains raw host observations, exact sample/submission
identities, outcomes, policy, and tool identity. The bundle validator recomputes
the arithmetic, correlates it with the sealed USB byte audit, and checks time
coverage across owned ARM-to-RELEASE intervals. Only the fixed supported
`POST /api/usb/v1/time/submit` is permitted alongside management reads; settings
mutations remain rejected. Late/unqualified observations, clock steps, lost
ACKs, changed identity, or expired owned-job coverage cannot qualify. Healthy
SNTP/GPS arbitration retains priority; controller input does not force a switch.

### GPS acquisition and usable clock holdover

New GPS-selected plans bind `gps_clock_admission` version 1. GPS acquisition
health, usable UTC, and RF correction have separate lifetimes. A transient
capture rejection or pairing reacquisition remains visible but does not by
itself invalidate a retained synchronized/holdover clock. No recovery sleep,
three-second pairing gate, or polling for a green acquisition flag participates
in this policy. Plans without this field retain their historical rules for
reviewing old evidence.

Before a job, saved/active GPS policy, boot identity, selected source, clock
state, advertised holdover age, and required retained calibration are checked.
A cold unsynchronized clock, raw-clock-domain fault, source disagreement,
expired calibration, or unsupported state still refuses. Acquisition flags and
error strings retain their original values. The snapshot takes each resource
once; it does not force learning generations to remain equal. A pre-LOAD
calibration observation may precede a newer selection generation. LOAD remains
authoritative, and the actual job-frozen input must independently corroborate
its selection, arithmetic, age, uncertainty, and LOAD brackets before ARM.
Subsequent learning or temporary acquisition loss cannot replace that input.

After the prepared resource snapshot, the owned WTP client obtains GET_CLOCK,
then the same management client reads the selected source again. This records
an observed source bracket, not an atomic multi-resource snapshot. Both source
observations must report GPS without disagreement on the same boot; normal
forward model generations are permitted. `clock-admission.json` records the
clock, source observations and request IDs, actual capabilities and ARM limit,
job/target identity, and collection interval. Collection remains within the
existing capture/ARM preparation interval and the existing transport timeouts.
The actual ARM response and the firmware's pre-launch checks remain authoritative.

Clock state, source age, and uncertainty are checked against advertised WTP
holdover rules and the actual ARM request. A UINT64_MAX uncertainty limit is
retained as advertised and is not described as a finite precision guarantee.
This policy adds no arbitrary precision or freshness threshold. Mode analysis
requirements remain unchanged. NTP/GPS updates continue normally.

The result validator rechecks the clock decision and actual ARM clock, and
correlates every passing job's clock receipt with GET_CLOCK, CAPS, management
GET/ACK, LOAD, and ARM in the sealed USB audit. Duplicate samples, altered clock
values/limits/source bodies, wrong job/boot, unrecorded jobs, or reordered
collection cannot qualify. The terminal and web progress consumers receive the
same clock/acquisition transitions through the maintained progress stream;
repeated unchanged health states do not produce repeated notices.

Historical results keep their original outcome. Source inspection or a
hardware-free holdover replay does not qualify an untransmitted observation.

### Receiver cancellation receipts

The native capture helper observes a cooperative stop file at its metadata
path plus `.cancel`. The local capture launcher exclusively creates that
plan-derived file on cancellation or its outer timeout, then gives the owned
helper at most five seconds to finish cleanup and publish native failure
metadata. A cancellation already requested before launch is published before
the helper can configure a receiver. The native capture loop checks the stop
request around configuration and bounded reads, closes/releases its actual
resources, removes incomplete IQ, and writes the existing failure receipt.

An unresponsive helper is still terminated within the bounded stop deadline.
Process exit alone never proves native SDR cleanup: a missing, malformed, or
failed native receipt continues to make cleanup unsuccessful. Preserve both
the process-execution diagnostic and the native receipt, including their
distinct timeout/cancellation causes. This protocol requires the corrected
native helper; stage and bind its newly built executable rather than reusing
an older helper that ignores cooperative cancellation. No firmware, clock
service, or receiver-service change is involved.

## Prepare a configuration

Copy [the illustrative configuration](../../examples/pico-campaign-configuration.json)
to a private location outside Git. Replace every machine, device, port, path,
station, and RF-path field with current facts. The example is hardware-free
input, not an authorization receipt or a description of a connected bench.

`pico.serial` is the exact uppercase 16-character USB serial. `device_id` is
the first 32 lowercase hex characters of SHA-256 over the bytes
`b"WsprryPico/1" + bytes.fromhex(serial)`. Discovery must independently match
both CDC roles and their physical USB parent. Select management role 4 and WTP
role 2 explicitly. `control_carrier` is `usb`, and `board` is `pico2_w`.

`configuration_revision` may be null to bind the initial observed revision
automatically. A supplied revision must match exactly. Initial configuration
is read twice before admission. Saved and boot-active hardware, GPS, Si5351,
clock/correction, schedule, ownership, and output state are observed through
strict current-control schemas. Unknown fields, schema drift, malformed
identities, occupied boards, active schedules, maintenance holds, reboot
requirements, or unsupported state refuse admission. Management is inspected
before the WTP interface is opened; the Harness does not take over another
owner's job.

A GPS-disabled row observes firmware disablement; cable removal is unnecessary.
The selected Pico source also supplies the closed network-diagnostics schema used inside `ntp-observations`. Its platform network, μChrony, memory, and packet observations are validated with exact integer types and decimal bounds across schema references. Missing fields, unknown fields and malformed nested diagnostics refuse before a campaign starts.

GPS-enabled rows require qualified GPS-derived UTC and the selected correction policy. The holdover policy above governs new plans; historical plans require fresh paired serial/PPS/time observations. UTC source, GPS use, manual correction in PPB, and automatic RF correction are independent inputs. Correction-on requires fresh matching product observations and retained generation provenance, including the correction frozen at preparation. Missing or conflicting qualification refuses the job without manual fallback. For historical plans only, snapshot acquisition allows at most two seconds of read-only GPS readiness sampling for a fresh qualified observation; an uncleared pairing error, stale observation or missing PPS/time still refuses. The deadline is shared across the snapshot, including generation-coherence rereads. Non-WSPR jobs request a whole UTC second. GPS-enabled jobs select that start once after LOAD and the final resource snapshot, seven seconds ahead; GPS-disabled jobs retain the five-second pre-preparation lead. GPS-enabled non-WSPR capture retains ten additional seconds of RF-off preparation allowance. Before selecting a start, the controller conservatively checks that the finite capture still contains the complete job and at least two seconds of post-job quiet. Exhausted capture allowance refuses before ARM. Rounding adds less than one second, remains inside the existing nine-second controller ARM bound after preparation, and preserves the full requested UTC-second launch window. Fractional starts near the end of a second can leave only milliseconds before the product’s exclusive deadline. The deadline itself is unchanged, and WSPR stays at its original UTC slot plus two seconds. GPS-enabled WSPR begins its fixed 140-second capture twenty seconds before the slot to allow the complete LOAD and GPS reads; after retaining the immediate post-LOAD correction input, it waits until nine seconds before the requested start for the final resource snapshot. This wait checks receiver readiness and cancellation and has a separate 45-second monotonic deadline; missed preparation still refuses before ARM. GPS inputs are retained immediately before and after LOAD, in addition to the general snapshots. The Pico retains the complete automatic calibration input atomically with the accepted job: job ID, manual residual, oscillator error, base uncertainty, observation time, span, generation, and selection time. Live admission and offline result validation require the closed current diagnostic shape, exact job/device/boot binding, correction arithmetic, freshness and uncertainty at selection, and generation/timestamp bounds from both LOAD bracket observations. An exact matching live generation, when observed, must corroborate that input. The GPS reads may advance past an intervening frozen generation; they do not replace the retained input. Missing, stale, conflicting, or unbound evidence refuses before ARM without replaying LOAD. The prepared snapshot is retained before admission validation so failures preserve their inputs.
Receiver calibration is a separate frozen profile/request binding and affects
receiver interpretation only. It never adjusts transmitted frequency.

Use current evidence for the conducted output, termination, attenuation,
combiner/filter routing, antenna disconnection, receiver identity, and safe
input basis. Do not reuse an example or old wiring log as physical evidence.
Both RF engines can remain connected when each selected output has a verified
conducted path; campaign admission still checks the selected Pico and engine.

## Rehearse and execute

Derive options from the installed `complete-test --help`. The Pico form is:

```text
wsprrypi-qualification complete-test pico localhost --sdr driver=sdrplay,serial=RECEIVER_SERIAL --pico-configuration PRIVATE_CONFIG.json --pico-compiler COMPILER_BUILD/wspq-pico-compile --rehearse
```

The receiver host and exact SDR selector must agree with the configuration.
The physical Pico serial can replace the `pico` positional selector. On a
live-capable host, replace `--rehearse` with `--enable-rf` only when the current
task authorizes the resolved bench and RF path. Pi backend/deployment overrides
cannot be combined with Pico options. Repeat `--mode TONE`, `--mode WSPR`,
`--mode QRSS`, `--mode FSKCW`, or `--mode DFCW` to select modes; omission runs
all five in canonical order. Duplicate modes are refused.

`--pico-output-parent` selects a private directory outside all checkouts and
symlinked directory trees. The command creates a new exclusive run directory.
It prints the durable progress JSONL path and exact viewer command before
long-running work. Rehearsal compiles and validates plans, constructs no live
adapter, contacts no device, creates no captures, and cannot claim qualification.

Live execution observes idle state, acquires only its own lease, submits one
complete bounded job, checks receiver capture readiness and RF-off preamble,
prepares correction provenance, and arms the local job. Lease renewal, ARM
lead limits, slot preparation, exact-count capture, analysis, cancellation,
and cleanup have finite deadlines. A lost mutation response is never replayed. Event polling retains partial USB frame headers and payloads across polling deadlines. Each poll keeps its original absolute deadline, and full framing, length, CRC, schema and session validation precede delivery.
Cleanup reconciles actual status, aborts only its matching owned job, releases
its ownership, and checks boot-bound output inactivity. Si5351 additionally
requires register-disable readback. Device reset, disconnect, changed ownership,
or unverified receiver/transmitter cleanup prevents qualification.

Settings are read-only throughout this route. Final readback must match the
initial settings and active domains, with no owned job/output remaining. The
Harness does not resume an existing schedule. Retain primary and cleanup
errors together; cleanup failure takes precedence over successful measurements.

## Evidence and interpretation

Validate a retained bundle without device access:

```text
wsprrypi-qualification validate-complete-test ABSOLUTE_RUN_DIRECTORY
```

The private result includes requested and frozen plans, full resource snapshots,
actual USB topology, bounded WTP transactions and terminal journals, a sealed
USB byte audit including management delivery ACKs, exact-count IQ/metadata,
RF-off/preamble evidence, complete decoder stdout/stderr, mode references,
analysis, correction/clock provenance, final state, and an artifact inventory.
The validator checks identities, hashes, reference confinement, selected mode
order, exactly three independent observations, actual job/capture uniqueness,
measurement outcomes, cleanup, and settings preservation. It rejects a
rehearsal or partial run presented as qualification.

Acquired carrier, WAV conversion, and decoder records carry a native Pico context with the configuration identity and digest of the complete frozen campaign plan. These records do not substitute a Pi backend or bench/test profile. The Pico result validator checks this context, each independent carrier capture and native metadata reference, and the retained timing reference. Use `validate-complete-test` for Pico bundles; the general bench/test profile readers refuse Pico evidence. Live capture metadata uses canonical UTC timestamps ending in `Z` for acquired-IQ replay.

Outcomes distinguish `passed`, `failed`, `blocked`, `inconclusive`, `aborted`,
`cleanup_failed`, `unattempted`, and `rehearsed`. CLI exit codes are 0 for pass
or rehearsal, 4 for measurement failure, 130 for cancellation, 5 for cleanup
failure, and 3 for other unsuccessful outcomes. A result applies only to the
recorded board, installed bytes, engine, band, mode, clock/correction state,
receiver, and conducted path. Decode success is not calibrated power or
spectral compliance.

Automatic Si5351 correction is experimental. Successful selection and operation
do not demonstrate improved accuracy. The prior resolution-limited investigation
is finally closed as inconclusive; improved performance is not a campaign gate,
and these commands do not reopen that investigation or expand acquisition windows.

## Software acceptance

CI selects an immutable Pico source, builds its actual encoder plus production
WTP/USB-management fixtures, and exercises the new portable and POSIX adapters
without hardware. macOS and Linux run the production endpoint fixtures;
Windows builds the portable compiler and exercises planning/refusal/analysis.
The full Linux suite, typing, lint/format, distributions, wheel smoke test,
and native mock capture CTests remain required. Pi/Pico network and USB paired
integration remain separate required companion checks when product interfaces
or their fixtures change. None of these software checks establishes target
RF, timing, physical USB, installed-image, or release-artifact qualification.

For a short connected harness-control check, use the [Pico software governance gate](pico-governance-gate.md). It executes one keyed job and one WSPR job with simulated scheduling and retained validation, plus focused failure checks. Its results never qualify hardware or reduce live observation requirements.
