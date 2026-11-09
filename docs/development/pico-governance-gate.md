# Pico software governance gate

This development gate exercises one short QRSS job and one WSPR job with its carrier prerequisite through `PicoLiveAdapter.observe`, the real framed WTP client, the selected Pico source's production Endpoint and JobService, production measurement writers, cleanup, and retained observation validation. It does not execute a three-observation campaign or change live qualification policy.

The intended question is whether the harness can govern a bounded test and report its outcome. Firmware transmission correctness, physical USB, real SDR acquisition, RF timing, calibration, spectra, and the configuration matrix remain outside this gate.

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
