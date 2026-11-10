# Pico receiver-loss governance tool

`scripts/pico_negative_gate.py` is a maintained Linux-only development adapter around the unchanged production `pico_smoke` one-job runner. It targets one uniquely identified receiver child after matching running-job evidence, consumes one SIGKILL allowance, and independently assesses the retained result. It does not qualify firmware, RF, spectra, calibration, or another backend. Physical use requires separate, current authorization; software acceptance does not authorize an attempt.

The tool requires the installed harness Python package, Linux pidfds and signal masking, and a prepared one-job QRSS plan accepted by `pico_smoke.check_scope`. For host preparation it uses an explicitly selected installed `chronyc`; it never installs or configures a time service. The job remains at most ten seconds and within the production capture budget. Unsupported live platforms refuse before operational process creation. The script is included in the source distribution; it is not a new installed campaign command.

## Prepare inputs without device access

Use the existing `pico_campaign.compose_pico_campaign(configuration, compiler, modes=("qrss",))` API with a current configuration and the installed, identified compiler. `pico_smoke.check_scope(plan)` validates the one-job scope. Keep configuration and generated plan outside source control. The existing [one-job guide](pico-governance-gate.md#one-job-runner-and-the-physical-gate) governs plan selection and physical admission. Do not substitute an old result bundle or a fixture configuration.

On the intended Linux execution host, after other separately authorized preflight work and immediately before the later attempt, run:

```text
python -I /absolute/path/pico_negative_gate.py --help
python -I /absolute/path/pico_negative_gate.py prepare /private/new-input-directory --plan /private/current-plan.json --chronyc /usr/bin/chronyc --enable-read-only-host-observation
```

The output parent must exist and the selected directory must not. Preparation writes `plan.json`, `resolved-gate.json`, and `host-preflight.json` exclusively. It validates the one-job plan, hashes the selected capture helper and tool, reads host boot identity, checks pidfd capability, and retains the bounded read-only clock observation. A failure leaves incomplete inputs that cannot launch; retain the failure and use a new directory only under the applicable authorization. No device is opened, no SDR is enumerated, and no physical attempt is consumed by preparation.

`passed` in `host-preflight.json` is explicitly scoped to `local_process_and_clock_only`. Device admission remains `pending_production_runner`; preparation never asserts bench readiness or grants RF authority. Runtime preflight checks exact plan, candidate, helper, boot, observation provenance, freshness, and wall/monotonic consistency. Physical preparation rejects the fixture receiver. Raw observations and private input paths stay outside Git.

## Timing decision and evidence

The private draft's fixed 100 ms host uncertainty ceiling was an implementation choice, not a user requirement. It has been removed. The current implementation needs host/device clock correspondence because WTP `JOB_STATE` carries job/state/output activity but no event-generation timestamp, while ARM scheduling is expressed in device UTC. Receiving an event recently does not by itself prove that the scheduled job still has time remaining.

The tool retains that existing UTC comparison and subtracts measured host uncertainty from the usable job and lease intervals. Larger uncertainty is permitted only when the actual conservative interval still fits. Unknown uncertainty is refused. The existing 250 ms receipt-freshness limit, 250 ms device/clock safety margin, two-second remaining-job margin, device clock checks, and 20 ms wall/monotonic step detection remain; these are documented local injection policy, not general harness qualification requirements. Intent publication precedes a fresh guard, and signal timestamps must remain inside the reconstructed interval. A missed interval is inconclusive and never causes a retry.

The collector invokes `chronyc -n -c tracking` with a two-second process bound, preceded by a one-second optional `chronyc -v` provenance query. Version-query failure records `unavailable` and does not block timing eligibility. It retains the executable hash/version, raw CSV and host timing bracket. The installed chronyc documentation defines the clock error estimate as absolute system-time offset plus root dispersion plus half root delay, conditional on a correct reference clock. The tool adds the observation duration and a full sixty-second projection using absolute reported frequency, absolute residual frequency, and skew. It rejects non-finite or negative uncertainty terms, an absent/local unsynchronized reference, invalid stratum/leap state, and reference timestamps inconsistent with their reported error. It does not impose an additional reference-update-age threshold; reported dispersion already accounts for reference age. This is conditional operational clock evidence, not independent UTC calibration or a guarantee against arbitrary clock/OS failures.

The retained assessor recomputes the host estimate from raw observations for a physical assessment. Both the preflight and clock observation expire after sixty seconds; preparation should therefore be the last input step. A stale sample must refuse, not become an invented current observation. A wall/monotonic offset is used to detect steps, not as evidence of UTC accuracy.

## Separately authorized physical invocation

The following is an interface description, not permission to execute it:

```text
python -I /absolute/path/pico_negative_gate.py run /private/new-input-directory --operator OPERATOR --enable-physical-negative-gate
python -I /absolute/path/pico_negative_gate.py assess /private/new-input-directory
```

Invoke `run` only under the exact current authorization, with the approved plan, current physical-path facts and required preflight. It consumes a launch intent before the production runner is spawned and has no retry path. The production runner performs its own device admission and remains the sole WTP controller. The wrapper does not issue ABORT or RELEASE, reset devices, operate services, or alter firmware/settings. Capture selection binds parent, UID, lifetime, executable hash and exact production argv including the full capture ID. Pidfds bind the signal to that process lifetime. No process-name killing is used.

Ctrl-C or SIGTERM to the wrapper latches disarm and forwards cancellation once to the owned production runner. Do not kill unrelated processes or bypass production cleanup. Supervision has a 300-second outer limit, a separate bounded 45-second runner cleanup wait, and bounded watcher shutdown. If cleanup remains unverified, report it; do not reset hardware or restart services. A software deadline is not a guarantee against an unresponsive OS.

`run` completion means assessment is pending. Run `assess` in a fresh process with an external thirty-second bound and retain its JSON and exit status. It reconstructs identity, operation ordering, timing, injection, ABORT/RELEASE, result classification, and receiver diagnostic relationships. It requires a failed receiver process without competing timeout/cancellation, preserved partial evidence, unchanged settings, and released inactive transmitter state. It does not accept the injector's `injected` or `window_proven` flags as sufficient proof.

A passing negative assessment is distinct from production success. The production result remains `cleanup_failed` when native receiver cleanup proof is missing. Transmitter release, process exit, and receiver availability are separate facts. Receiver hardware availability remains unassessed unless a separately authorized check establishes it. Synthetic evidence cannot establish a physical pass; `assess --offline-fixture` explicitly reports a nonphysical assessment.

## Reproduce software acceptance

Use the existing inhibited native build described in the [governance guide](pico-governance-gate.md). Set `WSPQ_PICO_SOURCE`, `WSPQ_PICO_COMPILER`, and `WSPQ_SMOKE_WSPRD` to explicit local artifacts, then run:

```text
python -m pytest tests/unit/test_pico_negative_gate.py tests/unit/test_pico_negative_preparation.py tests/unit/test_pico_smoke.py
```

The required negative-tool tests generate keyed and receiver-loss cases through the maintained production endpoint/capture fixtures. They require no historical temporary directories or captured device responses. Running-state substitutions and injected-result receipts are explicitly synthetic. Missing explicit native inputs skip the connected fixture suite and cannot count as acceptance. The preparation unit tests do not require those native tools.

The original private trace's exact total frame count was replaced with lifecycle assertions and fragmented replay checks because transport polling can change frame counts. Production argv comparison, the valid retained positive case, rehashed semantic corruptions, signal consumption, lifecycle conflicts, cancellation races, fast exits, and receipt integrity remain tested. Scheduling and timing mutations use simulated time; real process checks retain their declared bounds.

For benign Linux validation, first generate a new fixture locally with the inhibited build:

```text
python -m tests.support.pico_negative_fixture --source PICO_SOURCE --native NATIVE_BUILD --wsprd ABSOLUTE_WSPRD --output NEW_FIXTURE_DIRECTORY
```

Under separate benign-host authority, stage the exact tool, `tests/support/pico_negative_linux_check.py`, `tests/support/pico_negative_linux_watch.py`, generated `watch-fixture.json`, and `tests/support/negative_gate_process/` into a new private Linux directory. Name the tool `pico_negative_gate.py`; place the two Python checks and fixture JSON beside it. Build the standalone process fixture with CMake, selecting that directory as `CMAKE_RUNTIME_OUTPUT_DIRECTORY` so its executable is named `benign`. Use the intended harness Python environment and verify source/package identities before execution.

```text
cmake -S STAGE/negative_gate_process -B STAGE/build -DCMAKE_RUNTIME_OUTPUT_DIRECTORY=ABSOLUTE_STAGE
cmake --build STAGE/build --config Release
python STAGE/pico_negative_linux_check.py ABSOLUTE_STAGE
python STAGE/pico_negative_linux_watch.py ABSOLUTE_STAGE
```

These scripts never launch the physical runner. The first uses owned inert target/decoy processes; the second exercises the actual watcher using generated synthetic WTP and those inert processes. The watcher check also exercises the real read-only chrony collector. Each has an eight-second watchdog, bounded cleanup, and a ten-second acceptance limit. All owned children must be reaped, including on failure, and the decoy must survive the intended injection. The aggregate allowance is sixty seconds, not a delay to consume. Receipts bind the candidate hash and state `offline_benign` with hardware unassessed. Use a fresh directory for each reproduction because intent and output publication are exclusive.

Retain deployment artifacts and evidence outside Git, bind their hashes to the delivered commit, and validate the actual staged entrypoint before a later gate. There is no automatic transition from software acceptance to physical execution.
