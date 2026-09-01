# Timing contracts

Every operational wait in the Harness must be bounded and explainable. A value
is acceptable only when it belongs to one of these classes:

- **Protocol time:** fixed by an external waveform or UTC schedule.
- **Work-derived time:** calculated from exact samples, bytes, frames,
  subprocesses, transport operations, or cleanup actions.
- **Plan-bound safety ceiling:** authenticated before execution. It limits
  work; it is not an estimate manufactured by an adapter.
- **External API interval:** imposed by a tool or device contract, such as a
  receiver read timeout or SSH connection timeout.
- **Polling cadence:** affects responsiveness only. Correctness is determined
  by a monotonic deadline or completion event, never by the number of polls.

Unclassified fixed allowances, generic reserves, and sleeps that determine
correctness are forbidden.

## Production inventory

| Area | Timing source | Classification |
|---|---|---|
| Supervisor lifecycle | exact sum of its authenticated sequential `OperationDeadlines` fields | plan-bound and work-derived |
| Local and SSH commands | `CommandPlan.timeout_s` | plan-bound safety ceiling |
| Helper verification | resolved helper bound times exact operation count; every sequential operation shares the remaining aggregate envelope | work-derived |
| Helper request transport | operation-specific server work plus any cleanup it may perform: process start includes repository inspection, process wait includes the requested wait and cleanup, and process stop includes cleanup | parent/work-derived |
| Repository boundary discovery | the repository guard carries its parent helper or keyed-transaction envelope into every Git subprocess | parent-derived |
| Remote child termination | the request carries a cleanup envelope; TERM and KILL escalation split that exact remainder into two stages | parent-derived |
| Forwarded remote command termination | the capability plan separates command work from cleanup; INT, TERM, and KILL split the exact cleanup remainder into three stages and the local SSH process owns their sum | parent-derived |
| Scheduled helper process | accepted UTC schedule converted once to a monotonic launch instant; watchdog and waiter share the same hard deadline and outcome | protocol/plan-bound |
| Soapy capture | exact sample count/rate plus receiver read interval | protocol/work-derived and external API |
| Tone | exact preflight phase envelopes, capture bounds, off/on cadence, repository-guarded server start, capture-byte analysis/publication work, cleanup, and quiescence | protocol/plan/work-derived |
| WSPR capture launch | first UTC slot minus retained margin minus the enforced helper-readiness bound covering configuration, activation, discarded first read, and retained-output establishment | protocol and external API |
| WSPR frame analysis | coherent CF32 bytes times two validation/render passes at the supported I/O floor, plus decoder subprocess bound | work-derived |
| WSPR summary | coherent CF32 bytes times two semantic-validation passes per frame at the supported I/O floor | work-derived |
| WSPR publication | coherent CF32 bytes times source-authentication, copy, retained-authentication, and post-publication-validation passes at the supported I/O floor | work-derived |
| WSPR overall | exact slot wait + capture + frame analyses + summary + publication + cleanup + quiescence | work-derived |
| Keyed capture | generated event timeline plus maintained one-second measurement guard | protocol/work-derived |
| Keyed capture readiness | remaining transaction envelope; readiness is an observed retained-output event and has no independent fixed cutoff | work-derived |
| Keyed scheduled start | one half of the protocol-defined pre-quiet interval is reserved for arming | protocol-derived |
| Keyed transaction | exact capture duration, four authenticated CF32 analysis passes at the supported I/O floor, and five named control phases: preflight, cleanup registration, scheduled start, cleanup, and quiescence | work-derived |
| Keyed campaign | exact transaction bound times the requested observation count plus final provider cleanup | work-derived |
| Automatic installed-runtime deployment | discovery, identity, transfer, and post-run validation wait for completion; the delegated coordinator is only an observer of child plans that retain their own live bounds | completion event; never qualification evidence |
| Opt-in source/native compilation | actual build-process completion before live execution; no elapsed-time estimate | completion event; never RF or qualification work |
| Complete selected-mode campaign evidence | exact sum of the selected resolved child envelopes; each child enforces its own bound | work-derived |
| Progress forwarding | child command bound; forwarding has no independent success timer | plan-bound |

The supported offline sequential-I/O capability floor is 25,000,000 bytes per
second. This is a fail-closed platform requirement used only to turn exact byte
work into a deadline. A receiver unable to sustain it is unsupported for the
default production campaign; the Harness does not silently wait forever or
substitute an unrelated fixed allowance.

The standard coherent WSPR capture is 92,500,000 CF32 samples, exactly
740,000,000 bytes. Its three-frame summary therefore receives a bound derived
from 4,440,000,000 bytes of semantic-validation work, rather than the former
fixed 30 seconds.

## Composition rules

Nested work never receives a fresh fixed timeout. It receives the smaller of
the remaining authenticated parent envelope and the remaining deterministic
phase envelope. In particular, the four helper-verification operations share
one aggregate monotonic deadline. A slow valid `git rev-parse` may consume more
than one nominal helper-operation share as long as all four operations complete
inside that aggregate. This prevents the former five-second
`parent_revision_inspect` failure while preserving a finite pre-RF bound.

The helper response channel must remain open for the server work authorized by
each request. It must not reuse the generic helper-operation timeout when the
request explicitly authorizes longer nested work. `process-start` therefore
uses the helper request envelope plus the repository-inspection envelope;
`process-wait` uses the requested child wait plus its cleanup envelope; and
`process-stop` uses the cleanup envelope. These are response-transport bounds,
not additional permission for the child process or RF operation to run.

The OpenSSH capability likewise does not give the remote command and its
cleanup the same deadline. Its authenticated overall envelope must exceed the
command envelope; the difference is transmitted as the cleanup envelope, and
the local SSH launcher is bounded by the complete overall envelope. The remote
executor divides cleanup equally among INT, TERM, and KILL escalation instead
of manufacturing 30-, 5-, and 5-second waits.

The complete campaign has no independent wall-clock cutoff. Its recorded child
budget is recomputed after all selected child plans are materialized and must
equal their sum. Each maintained child coordinator enforces its own authenticated
envelope. The parent does not pre-empt a child or reject the next child because
of orchestration overhead between bounded modes.

The TONE session likewise has no inherited fixed overall cutoff. Its outer
deadline is the exact sum of the named preflight and lifecycle envelopes plus
analysis and publication derived from the RF-off and RF-on CF32 byte counts.
The analysis subprocess and publication phase receive only their own derived
budgets; they do not receive the complete remaining session time.

For live TONE, repository verification and server startup complete before the
RF-on capture begins. The retained capture readiness event then establishes the
planned capture epoch. Independent command delivery can delay individual TONE
pulses; analyzer 12 records those offsets without gating them. It checks actual
ON duration and quiet/stop evidence instead of command scheduling. The
separate cadence analyzer retains its own evidence and does not rewrite the
FFT acquisition metrics. Under carrier policy version 3, an unsuccessful
cadence assessment prevents TONE qualification even when the FFT acquisition
passes; later independent modes can still run after verified cleanup.
The TONE analysis workload includes the additional RF-on temporal-projection and independent-pulse association
reads. See [Noise robustness](noise-robustness.md).

Polling values such as JSONL refresh, process-status checks, and readiness-file
checks are cadences only. They may affect how quickly completion is noticed,
but they do not subtract a fixed allowance, count a fixed number of attempts,
or determine success.

Compilation is a preparation exception because elapsed wall time cannot be
derived honestly from source size across supported machines. The explicitly
selected source build and the receiver-native helper build therefore wait for
their process completion, failure, transport exit, or operator interruption.
They occur before a live coordinator is entered. Their former 900-, 1,000-,
180-, 600-, and 850-second nested limits are forbidden. This does not relax any
receiver, transmitter, RF, cleanup, or quiescence deadline.

The same rule applies to controller-side discovery, staging, identity checks,
and post-run validation: machine and network throughput do not provide an
honest elapsed-time formula. The controller may therefore wait for completion,
failure, transport exit, or operator interruption. A delegated complete-test
coordinator also waits for its self-bounded campaign to finish instead of
imposing the former 7,500-second observer cutoff. This never removes the
resolved transmitter, receiver, RF, cleanup, quiescence, subordinate-session,
or campaign deadlines enforced on the execution host.

## Prohibited patterns

- a per-operation cutoff inside a separately bounded sequential phase;
- a generic reserve added to capture, decoding, publication, or campaign work;
- a fixed server-start sleep when a protocol-defined RF-off interval already
  supplies the readiness envelope;
- a fixed receiver-readiness cutoff independent of the transaction envelope;
- a campaign cap unrelated to its resolved child plans; and
- a controller, staging, discovery, build, or validation cutoff inferred from
  assumed machine or network speed; and
- treating polling cadence as an execution deadline.
