# Live keyed session contracts

`keyed_session_contracts` is the hardware-free contract boundary used by live
QRSS, FSKCW, and DFCW coordination. It constructs and validates documents;
it cannot start a process, contact a host, open an SDR, change a service, touch
GPIO, or enable RF.

The resolved plan fixes the mode, transmitter and receiver identities, RF path,
reference artifacts, deadlines, stopping procedure, and transaction count. Only
canonical uppercase `QRSS`, `FSKCW`, and `DFCW` are accepted. The maintained
canonical digest is SHA-256 over finite JSON encoded with sorted keys, compact
separators, and ASCII escaping. Runtime authorization binds that exact digest,
session, mode, operator, UTC time, and exactly three transactions.

The plan fixes `message_repetitions_per_transaction` to one. Each transaction
therefore launches one keyed message and acquires one independent capture; the
required three observations are never modeled as repetitions within one process.

Each transaction records the ordered lifecycle:

1. preflight;
2. cleanup installation;
3. process start;
4. capture completion;
5. analysis completion;
6. cleanup completion; and
7. quiescence verification.

The aggregate records the contiguous transactions actually attempted, beginning
with transaction 1 and stopping after the first unsuccessful transaction. A
qualifying aggregate requires transactions numbered 1, 2, and 3. Transaction,
process, capture, acquisition, analysis, artifact-path, and artifact-hash
identities must be independent across all three. A transaction cannot make a
qualification claim by itself.

Final status is derived, not trusted. Precedence is cleanup or quiescence
failure, abort, preflight failure, fixture blockage, keyed measurement failure,
inconclusive measurement, then qualification. `qualified` requires all three
transactions to pass with verified cleanup and quiescence. The result binds the
canonical aggregate digest, and the artifact index requires each core contract
document exactly once with safe relative paths and unique identities.

`keyed_coordinator.run_hardware_free_keyed_session` rehearses this lifecycle
against `SealedFakeKeyedAdapter`. The adapter is deterministic and sealed against
subclassing; it cannot launch a process, contact a host, operate a service, open
a receiver, or touch a transmitter. Failure and cancellation can be injected at
every lifecycle boundary. Caller cancellation stops primary work but cannot
suppress cleanup or quiescence verification. Each output path is single-use and
is published only after its result documents and canonical manifest are complete.

The hardware-free coordinator's `qualified` result means only that all three
fake transactions passed their modeled contract. It is not runtime
authorization, hardware evidence, or live qualification. Production execution
is exposed only by the separately gated command below.

## Production command

`run-cw-live-keyed` connects the same coordinator semantics to the authenticated
production capabilities. The resolved digest includes the WsprryPi application
plan and argv, executable identity, parent and component revisions, complete
receiver identity/settings, RF path, analyzer revision, SSH/helper/capture
identities, named host services, and backend quiescence mechanism.

Helper executable and deployment-configuration identities are immutable plan
inputs. The keyed helper configuration omits `plan_sha256`; after those inputs
are sealed, the launcher computes and supplies the resolved-plan digest with
the expected helper and configuration SHA-256 values. The helper authenticates
those files before accepting requests and correlates the exact digest on every
request and response. A runtime-bound configuration containing any plan digest
is rejected rather than overridden. This order makes the plan constructible and
prevents configuration or digest substitution without weakening authorization.

The plan separately binds `transmitter_process_privilege_wrapper`. The static
transmitter helper configuration must bind the same executable as
`process_privilege_wrapper_path` and `process_privilege_wrapper_sha256`. Every
process-start request carries that resolved wrapper digest; the helper rejects
an absent or different binding and constructs `WRAPPER -n --` ahead of the
already authenticated WsprryPi argv. On Raspberry Pi OS the wrapper is normally
`/usr/bin/sudo`; direct launch as the unprivileged `pi` account is unsupported.

The command requires `--enable-live-keyed`, `--enable-rf`, a non-empty
`--operator`, and `--confirm-plan-sha256` equal to the canonical resolved-plan
digest. It accepts only QRSS, FSKCW, or DFCW and exactly three requested
transactions. Each transaction uses a new owned process, capture, acquisition,
analysis, and artifact identity. The command stops after the first unsuccessful
transaction, but cleanup, service restoration, quiescence verification, helper
shutdown, and partial immutable output remain mandatory.

Run it only after separately resolving and authorizing the exact plan:

```text
wsprrypi-qualification run-cw-live-keyed RESOLVED_PLAN.json OUTPUT_PARENT \
  --work-directory NEW_ABSOLUTE_WORK_DIRECTORY \
  --ssh /absolute/path/to/ssh \
  --operator OPERATOR \
  --confirm-plan-sha256 EXACT_CANONICAL_PLAN_SHA256 \
  --enable-live-keyed --enable-rf
```

The work directory and session destination under the output parent must be new.
This synopsis documents the interface; it supplies no host identity, RF-path
fact, authorization, or permission to execute a live run.

`capability_bindings.services` is the complete host-qualified service allowlist.
`required_receiver_services` is an explicit receiver-only subset. After cleanup
has been installed, required receiver services are made active and other listed
services are made inactive for that transaction. Cleanup restores every listed
service to the state observed by that transaction. Failure to establish or
restore any requested state makes the transaction unsuccessful.

If the helper account cannot operate an allowlisted service directly, its
static configuration binds `service_privilege_wrapper_path` and
`service_privilege_wrapper_sha256` alongside the pinned `systemctl` identity.
The maintained backend invokes the wrapper only in non-interactive mode and
rechecks both executables before every inspect or state-change request. On
Raspberry Pi OS this is normally `/usr/bin/sudo`; verify the exact allowlisted
`systemctl start/stop SERVICE` permissions with `sudo -n -l` before resolving a
plan. Never depend on an interactive password prompt or an unbound shell.
Provision and verify narrow passwordless permission for the exact pinned
WsprryPi executable and resolved argument forms as well. The wrapper is fixed by
helper configuration; it is not accepted from application argv or operator
input.

The coordinator must also have strict SSH access to the transmitter from the
host where `run-cw-live-keyed` executes. Resolve the actual hostname and user,
verify the transmitter's current public host-key fingerprint through an
independent trusted path, and bind a dedicated immutable known-hosts file into
the plan. A hostname, username, host key, or known-hosts change requires a new
resolved plan and digest; do not repair trust with `StrictHostKeyChecking=no`.

Within the process-start boundary, the production adapter starts the exact-count
capture worker first, waits for its retained `.incomplete` output to prove that
capture is established, and then observes the mode plan's complete RF-off
preamble before launching WsprryPi. If capture setup fails or the worker exits
before launch, RF remains disabled. Capture cancellation and bounded join are
part of unconditional transaction cleanup.

If the worker fails after process launch, the transaction records fixture
blockage rather than keyed transmitter failure. The provider preserves a
hash-bound `capture_diagnostic` containing the exact helper argv identity,
return code, stdout/stderr, timeout/cancellation flags, and transport cleanup
state. It also retains valid native `capture_failure` metadata as
`capture_native_failure`. Incomplete IQ and IQ rejected by exact-count or
identity semantics are removed and never appear as a capture artifact. These
diagnostics are copied into the partial immutable bundle and covered by its
transaction record and manifest.

Conversely, a successfully authenticated capture is not relabeled as fixture
blockage merely because WsprryPi exits unsuccessfully. A nonzero transmitter
exit is a keyed measurement failure; a transmitter timeout, cancellation,
disconnect, or unverified process cleanup aborts the transaction. Valid capture
and acquisition evidence remain independently retained in those cases.
