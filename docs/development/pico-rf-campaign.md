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
never flash, restart, provision, set the clock, change an engine, or rewrite
settings. The selected configuration must already be active and usable.

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

GPS-enabled rows require fresh paired serial/PPS/time observations. UTC source, GPS use, manual correction in PPB, and automatic RF correction are independent inputs. Correction-on requires fresh matching product observations and retained generation provenance, including the correction frozen at preparation. Missing or conflicting qualification refuses the job without manual fallback. GPS inputs are retained immediately before and after LOAD, in addition to the general snapshots. Frozen correction generation and composed PPB must match an exact same-boot observation in both live admission and offline result validation; a newer GPS estimate cannot substitute for the frozen input. An unavailable generation refuses before ARM without replaying LOAD.
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
