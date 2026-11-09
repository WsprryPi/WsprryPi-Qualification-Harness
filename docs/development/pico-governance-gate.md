# Pico software governance gate

The original Step 1 development gate exercises one short QRSS job and one WSPR job with its carrier prerequisite through `PicoLiveAdapter.observe`, the real framed WTP client, the selected Pico source's production Endpoint and JobService, production measurement writers, cleanup, and retained observation validation. It does not execute a three-observation campaign or change live qualification policy.

The intended question is whether the harness can govern a bounded test and report its outcome. Firmware transmission correctness, physical USB, real SDR acquisition, RF timing, calibration, spectra, and the configuration matrix remain outside this gate.

The separate `--connected-capture` Step 2 below replaces the virtual receiver worker with the production process/capture stack. It selects exactly three cases and does not run WSPR. The following baseline sections describe Step 1 unless explicitly stated otherwise.

## Run

Use the harness development environment, a local Pico source checkout whose contracts match the harness, CMake and a C++20 compiler, and a real WSJT-X `wsprd`. No SDK, firmware build, bench host, or device is needed. Source trees are read-only inputs. Choose build and output directories outside both source trees; the output directory must not already exist. The script and its test support are development tools, not an installed live command.

```text
cmake -S HARNESS_SOURCE -B BUILD -DWSPQ_BUILD_SOAPY=OFF -DWSPQ_BUILD_TESTS=ON -DWSPQ_PICO_SOURCE=PICO_SOURCE
cmake --build BUILD --config Release --parallel 2
python scripts/pico_governance_gate.py --source PICO_SOURCE --native BUILD --wsprd ABSOLUTE_WSPRD --output NEW_OUTPUT
python scripts/pico_governance_gate.py --validate --output NEW_OUTPUT
```

The command prints a nonqualifying summary and exits unsuccessfully if a required scenario or retained validation fails. `--scenario keyed|wspr|receiver|cancel` runs one diagnostic slice; each slice is independently validated before a successful exit. A slice is not the complete gate. The default `all` includes the evidence corruption checks. A fresh process validates each scenario from retained files, using the maintained `_validate_frame`, transaction, capture-metadata, analysis, audio, and decoder semantics rather than a replacement measurement validator.

The gate requires one representative `pio-dma-gp2` configuration with GPS and automatic correction disabled. It compiles `ET` with a 0.7-second dot for QRSS and checks WSPR identity `K1ABC FN42 37` with the independent WSJT-X decoder. The existing carrier prerequisite retains its three short on/off cycles; these are not three independent jobs. Job IDs remain independent and live campaign observation counts remain three.

## Substitutions and evidence

- USB bytes travel over bounded subprocess pipes to the production Endpoint and JobService. Framing, request correlation, admission, leases, LOAD, ARM, ABORT, and RELEASE remain real production handling.
- The RF engine is an inhibited recorder linked without physical hardware adapters. It records only execution accepted and started by JobService. Receiver samples come from those executed intervals, clipped at cancellation, rather than from the requested plan. Missing execution yields only fixture noise.
- Management resources use maintained snapshot fixtures, combined with current status and capabilities from the production endpoint. This does not exercise the UMP management transport, physical discovery, or firmware management implementation.
- The receiver and acquisition worker are virtual. Their schema-backed success/failure metadata describe the fixture boundary. Actual Soapy acquisition and its native cleanup are not established. A separate short test uses production local process supervision to start, cancel, and reap a real child; a deliberate assertion tests native fixture worker cleanup.
- UTC, monotonic time, readiness, lease progression, capture duration, and scheduling waits advance virtually. Full WSPR waveform data and slot geometry remain intact. Only bounded subprocess I/O and process supervision use real-time waits.

The compiler and native fixture must report the same Pico source revision and source-input hashes. Plans retain compiler and analyzer identity. Each case retains framed WTP audit data, native fixture exchanges, execution records, requests, management snapshots, capture metadata, raw IQ when complete, analysis, full decoder output when applicable, cleanup, and a file inventory. Intermediate acquired-analysis records are enclosed in `pico_software_governance` evidence whose final validator requires `qualification_claim: false`. No live campaign `result.json` is published.

The runner clears inherited Python/plugin configuration, RF flags, and SSH-agent state before importing test support. It pins explicit executable paths, uses a separate output working directory, and rejects physical adapter construction along the exercised production path. This is an application-level fixture boundary around trusted local code and explicitly selected tools, not an OS sandbox against malicious native code.

## Required outcomes

| Scenario | Required evidence |
| --- | --- |
| Keyed | One connected compiled job, passing actual analysis, released ownership, complete retained validation |
| WSPR | Passing carrier prerequisite, one full connected WSPR job, real audio conversion and correct independent decode, cleanup, retained validation |
| Receiver unavailable | Fixture blockage before CLAIM/ARM, no executed signal, inactive unowned endpoint, retained receiver failure and cleanup |
| Cancellation | Interrupted running job, one ABORT and RELEASE, unsuccessful observation, transmitter release distinct from virtual receiver cleanup, actual child cancellation/reaping |
| Invalid evidence | Missing transaction, rehashed contradictory completion, missing receiver cleanup proof, and hardware-qualification promotion all rejected in fresh validation processes |

Corruption tests operate only on newly generated scratch evidence and restore the original bytes in `finally`. They rebuild inventory hashes before validation so the contradictory-completion case exercises semantic checks. Failed or partial capture data cannot become a passing observation.

## Timing and acceptance limits

Complete signal duration is not wall-clock waiting. Scheduling tests must use simulated time. Each real process-supervision test is bounded below ten seconds, with an aggregate allowance of sixty seconds; the command has an outer 180-second diagnostic limit. These are failure bounds, not delays to consume. The summary reports measured wall time and simulated duration separately. Native compilation and full repository acceptance are separate costs from repeating this gate.

On the development Mac, the connected gate, fresh validators, and corruption checks completed in approximately eleven seconds. This is a measured local result, not a cross-platform performance guarantee. Native Windows and Linux need their own execution evidence; portable code and CMake support alone do not establish those passes.

The gate exposed a shared capture-validation defect: phase timestamps were checked in JSON member order. The validator now uses the explicit lifecycle order, accepting sorted JSON while still rejecting reversed chronology. The regression check covers both outcomes. Pico observation clock functions are explicitly passed into the already injectable transaction clock, preserving production defaults while allowing simulated scheduling throughout the connected gate.

The bounded adversarial review checked production-path bypasses, signal independence from requested plans, missing ARM/collection/cleanup, shared producer/validator assumptions, rehashed contradictions, nonqualifying publication, inherited physical-access configuration, assertion cleanup, and simulated time. Its correction pass bound fixture/compiler source identity and made individual diagnostic slices fail on retained-validation errors. The gate does not establish correctness of controller-loss recovery, every deadline race, or every Pi/Pico lifecycle distinction.

Local acceptance covered all 1,974 Python tests across the full run and a targeted rerun, lint/format, typing, distribution build, two native capture CTests, and three selected Pico endpoint/management/frequency-policy tests. The first full run used a temporary root containing spaces: 1,946 passed and 28 existing Pi adapter-composition tests refused their helper/configuration paths. Those 28 passed under their supported temporary-path setup. The existing Pi helper-path restriction was not changed; the Pico governance gate independently passed with spaces in its output path. Compilation emitted conversion warnings from the read-only Pico source. These results describe the development Mac only.


## Step 2: connected receiver process

Run the same build above with physical Soapy support disabled, then select the connected gate explicitly:

```text
python scripts/pico_governance_gate.py --source PICO_SOURCE --native BUILD --wsprd ABSOLUTE_WSPRD --output NEW_OUTPUT --connected-capture
python scripts/pico_governance_gate.py --validate --output NEW_OUTPUT
```

The compiler and plan composer still bind a `wsprd` artifact, but this gate never executes the decoder or a WSPR job. `--scenario keyed|receiver|cancel` selects an independently validated diagnostic case; `--connected-capture --scenario wspr` is refused. Without `--connected-capture`, the original Step 1 behavior remains available.

The connected path retains `PicoLiveAdapter.observe`, `_capture`, `close`, the framed WTP client, production Endpoint/JobService, `SoapyCaptureCapability`, `LocalTransportLauncher`, `LocalCommandTransport`, a real `ThreadPoolExecutor` and futures, the native `capture_exact` writer, production QRSS analysis, and maintained retained frame/transaction/capture/capability validators. Only USB transport, the inhibited RF engine, management observations, scheduling clocks, and receiver samples are fixtures. This does not exercise the physical Soapy plugin, USB/UMP transport, controller-loss recovery, or complete campaign publication.

The test-only `wspq-capture-connected` executable is built under `WSPQ_BUILD_TESTS`, links the capture writer without SoapySDR, and accepts only the `fixture` driver. Its argument layout intentionally matches the production helper, including the literal `--enable-physical-sdr` argument emitted by the capture capability; that token cannot add a hardware implementation to this fixture. The gate pins this helper explicitly and never falls back to an installed physical capture executable.

The controller supplies little-endian sample chunks rendered from the native JobService execution recorder. The child consumes them through a `SampleSource` and writes the `.incomplete` readiness prefix, final IQ, success/failure metadata, and native cleanup receipt through `capture_exact`. The controller never manufactures those output artifacts. A native acknowledgment is created only after the preceding read has returned through the writer. The next scheduling advance waits for that acknowledgment. ARM records the child-produced prefix size and requires the real capture future to remain pending.

Controller scheduling and capture timestamps use simulated time. Once the production endpoint has processed RELEASE, the capture sample clock advances through the remaining quiet tail without expiring the controller's pending RELEASE acknowledgment. Native reads have five-second wall watchdogs and the fixture source has an eight-second total wall deadline; controller handshakes have five-second limits. An independent eight-second cancellation watchdog surrounds each production capture call, so a stalled child is terminated and reaped by the real launcher; each call must finish within ten seconds and retain its wall-time receipt. Wall-clock process supervision is not patched. The gate rejects an aggregate of more than sixty seconds of handshake waits. These bounds are failure limits, not sleeps to consume; production-length capture and scheduling windows are never waited out.

| Case | Required connected behavior | Retained observation outcome |
| --- | --- | --- |
| `keyed` | One short QRSS job; child-produced preamble before ARM; complete execution-derived capture; native cleanup; production analysis and fresh-process validation | `passed` |
| `receiver` | RF-off acquisition succeeds; asynchronous RF-on source fails before preamble readiness; no CLAIM/ARM or execution; native failure diagnostics retained; partial IQ absent | `blocked` |
| `cancel` | Capture child exits abruptly only after production JobService reports running with a positive executed interval; native cleanup receipt absent; real launcher reaps child; controller ABORTs and RELEASEs exactly once; partial IQ absent; adapter close refuses success | `cleanup_failed` |

The `cancel` label in this variant names receiver-process loss, not the original Step 1 user-cancellation case. The transaction error remains fixture blockage; the missing receiver cleanup proof takes precedence in the gate's observation summary. A passing negative control means the expected refusal was demonstrated, never that its observation passed or hardware qualified.

Every case retains the production capture capability or failure diagnostic, native metadata where it exists, exact helper identity, child-produced source acknowledgments, WTP requests, execution intervals, transmitter cleanup, worker shutdown, and artifact inventory. The validator binds failure diagnostics to their plan and native receipt, and success capabilities to retained IQ/metadata. New scratch evidence is also tested with missing native metadata and a rehashed forged cleanup-success claim; both must fail in fresh validator processes. Original bytes and inventory are restored in `finally`, followed by final independent validation. The summary reports gate time before its final validation subprocess and separately reports real handshake wait time.

The bounded review checked that the production worker, future, launcher and writer were connected; readiness preceded ARM; failure followed confirmed running work; missing native cleanup proof could not be replaced by a reaped-child receipt; rejected IQ was removed; and the source remained execution-driven. Its correction pass made observation failure precedence explicit and strengthened retained diagnostic/plan/native-file bindings. No production lifecycle change was needed for these three cases. Passing here supports this bounded software-governance path only, not every race, backend, mode, platform, or physical receiver.

## One-job runner and the physical gate

`python -m wsprrypi_qualification.pico_smoke` is the bounded one-job governance route. It accepts an authenticated Pico campaign plan containing exactly one QRSS job, executes one observation, and publishes `smoke-result.json` under a new private output directory. The job must be at most ten seconds; the two captures together must fit a conservative 128 MiB budget. The full `complete-test` qualification route still requires three independent observations. This runner always records `qualification_claim: false`.

The question answered by a passing result is whether the harness issued one accepted job, collected complete receiver evidence, evaluated it conclusively, released its resources, preserved settings, and validated the retained evidence. A conclusive measurement failure can pass this governance question; its measurement outcome remains visible in `frame.outcome`. An inconclusive measurement, missing capture, ambiguous write, unverified cleanup, or invalid retained evidence cannot pass.

### Contextual diagnostic validation

The one-job runner selects the `governance` policy in production code. Ordinary campaigns keep the default `qualification` policy; neither a firmware version string nor result metadata selects a weaker policy. `pico_context.py` declares the diagnostic fields consumed by governance. Control settings, WTP messages, device/boot identity, active backend, current GPS/UTC validity, output inactivity, ownership, job identity, correction bounds, capture completeness, and cleanup remain enforced by their schemas and lifecycle checks.

| Evidence | Governance requirement | Qualification requirement |
| --- | --- | --- |
| RF diagnostic selection | Current source/generation/correction and prepared summary; typed and bounded | Complete current diagnostic contract |
| Atomic `prepared_input` | Optional capability for older documents; if present, validate it and reject contradictions; automatic input cannot be null after LOAD | Required atomic input and full job-bound correction proof for automatic calibration |
| Legacy automatic prepared summary | Exact accepted job and claimed owner, same boot, inactive loaded state, valid prepared source/correction, generation inside the retained LOAD bracket, and agreement with any matching-generation bracket observation | Insufficient to establish atomic calibration provenance |
| UTC diagnostic | Required source, disagreement, nominal sample rate, generation, rate correction, age, and target | Complete current diagnostic contract |
| Retired calibration candidate fields, network diagnostics, and new read-only extensions | Preserve unchanged as unassessed telemetry; never infer authority or a capability from them | Exact current diagnostic contract |
| Message schedule | Keep all existing schedule fields and semantics strict; retain additional read-only telemetry | Exact current diagnostic contract |

A document with missing optional telemetry is different from a document with a malformed required control field. Unknown protocol/resource identifiers, missing required evidence, wrong types, invalid bounds, mismatched resource responses, contradictory lifecycle state, and capability changes during a run are refusals. Errors identify the purpose, lifecycle stage, and trusted schema rule without exposing peer values. No validator inserts a missing field, replaces it with null, or modifies the raw response.

`validation-context.json` records the policy, lifecycle stages, whether the atomic-input capability exists, and that calibration qualification was not assessed. Fresh retained validation reconstructs that assessment from unchanged management evidence and checks capability continuity. A legacy summary can establish this bounded governance claim without proving the exact frozen calibration input. If that atomic provenance is the requested claim, firmware providing it is required; flashing is not a prerequisite solely because an optional diagnostic field is absent at idle. Offline compatibility evidence does not establish installed-firmware or RF correctness.

### Offline development check

Build the inhibited native helpers using the Step 2 commands above, then run `tests/unit/test_pico_smoke.py` with explicit `WSPQ_PICO_SOURCE`, `WSPQ_PICO_COMPILER`, and `WSPQ_SMOKE_WSPRD` environment variables. The compiler directory must also contain `wspq-pico-governance` and `wspq-capture-connected`. These are local artifact selections, not host or device access. Use a temporary test directory outside the checkouts. The connected checks skip when these explicit inputs are absent; a skipped check is not readiness evidence.

The fixture executes the runner entrypoint, plan authentication, initial admission, production UMP management client and delivery ACKs, production WTP client, native production Endpoint/JobService, capture worker, process launcher, native writer, deferred production QRSS analyzer, and fresh-process result validation. The substituted boundaries are USB discovery/transport, management resource contents, inhibited RF execution, receiver sample source, and scheduling clock. Management contents come from maintained fixtures combined with native JobService status; this does not establish firmware UMP behavior or physical discovery. The receiver samples remain derived from executed JobService intervals. Physical constructors are forbidden in this check.

Required connected cases are successful acquisition/evaluation with current diagnostics and with legacy manual/automatic correction summaries, receiver failure before ARM, receiver loss during confirmed running work, and occupied-device admission refusal before WTP access. Receiver loss with no native cleanup proof must produce `cleanup_failed`, even when the child has been reaped and the transmitter released. Repeated LOAD/ARM calls, including after an ambiguous write, are refused. Fresh validation checks both successful and partial unsuccessful evidence; successful validation of a failure record does not make its observation pass.

Run `tests/unit/test_pico_context.py` with `WSPQ_CAPTURED_PICO_DIAGNOSTIC` and `WSPQ_CAPTURED_PICO_AUDIT` pointing to private captured JSON and management JSONL files to exercise the original response and byte-for-byte UMP request/ACK replay offline. These two checks skip without explicit inputs; private captures stay outside source control. Generic regressions cover omitted and extended telemetry, malformed required fields, contradictory state, claim separation, and capability loss.

Scheduling and management-deadline tests use simulated time. Real waits are limited to the bounded process supervision already described for Step 2. The deadline tests exercise multiple management requests against one operation deadline and confirm that repeated production USB cleanup resets cannot renew the cleanup allowance.

### Precisely authorized physical invocation

Prepare and review one current QRSS plan with the maintained Pico plan composer, using the current installed compiler/source artifacts, actual selected backend and settings, exact USB roles and SDR identity, and the operator-confirmed RF path. Do not substitute a fixture plan or an old run bundle. Obtain its digest with `pico_campaign.plan_digest`; the runner confirms that exact digest before constructing a physical adapter. Run on the selected USB/SDR host only, after the independently authorized host/device preflight.

```text
python -m wsprrypi_qualification.pico_smoke run --help
python -m wsprrypi_qualification.pico_smoke run --plan PLAN.json --output-parent PRIVATE_OUTPUT --operator OPERATOR --expected-plan-sha256 REVIEWED_DIGEST --enable-rf
python -m wsprrypi_qualification.pico_smoke validate PRIVATE_RUN_DIRECTORY
```

The physical command requires a separate explicit authorization; the offline checks do not grant it. An attempt receipt is written before physical adapter construction. An invocation consumes its attempt even if opening or admission fails, and contains no retry loop. Do not invoke it again under the same one-attempt authorization. It never flashes firmware, changes backend/settings, operates services, or performs a carrier prerequisite or another protocol job. The output directory and exact progress-viewer command are printed before device access.

### Deadline and ownership composition

The operation cancellation token has one 120-second absolute deadline shared by construction, management reads, WTP, readiness, and capture supervision. It is not a fresh allowance for every request. Cleanup uses a separate 45-second absolute deadline, created once and rebound to both USB streams after every production cleanup reset. The existing capture unwind has a fifteen-second drain bound. Device closure precedes analysis; unchanged production analysis runs in a fresh isolated subprocess with a sixty-second timeout. Fresh retained validation has a thirty-second timeout. Both subprocess allowances are additionally clamped to the remaining 300-second software budget. The nominal allowances total 270 seconds, leaving thirty seconds for local publication and overhead; these are failure bounds, not waits to consume. This is cooperative software supervision with bounded transport/process operations, not a guarantee against a stalled OS or filesystem.

The schema-backed result is distinct from campaign qualification evidence. Passing publication requires a fresh validator to accept the inventory, original authorized plan and permitted initial-revision binding, transaction completion and release, complete capture/analysis, final inactive unowned state, unchanged settings, and sealed USB audit. All four admission/settings snapshots are compared with their retained management GET response bodies and ACK sequence. A provisional passing result is kept separate until validation returns; validation failure publishes an unsuccessful result. Partial failure bundles preserve primary and cleanup errors separately and check retained transaction and receiver-failure evidence without inventing missing proof.

Passing these offline checks establishes software readiness to attempt the single physical gate. Physical USB/UMP behavior, the installed image, actual backend/receiver admission, real capture, and physical cleanup remain the purpose of that gate. No broader RF campaign or firmware transmission qualification follows automatically.
