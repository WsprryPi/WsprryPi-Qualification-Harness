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
| Supervisor lifecycle | authenticated `OperationDeadlines` fields | plan-bound safety ceiling |
| Local and SSH commands | `CommandPlan.timeout_s` | plan-bound safety ceiling |
| Helper verification | resolved helper bound times exact operation count | work-derived |
| Scheduled helper process | accepted UTC schedule converted once to a monotonic launch instant; watchdog and waiter share the same hard deadline and outcome | protocol/plan-bound |
| Soapy capture | exact sample count/rate plus receiver read interval | protocol/work-derived and external API |
| Tone | exact off/on cadence and bounded transaction count | protocol/work-derived |
| WSPR capture launch | first UTC slot minus retained margin minus the enforced helper-readiness bound covering configuration, activation, discarded first read, and retained-output establishment | protocol and external API |
| WSPR frame analysis | coherent CF32 bytes times two validation/render passes at the supported I/O floor, plus decoder subprocess bound | work-derived |
| WSPR summary | coherent CF32 bytes times two semantic-validation passes per frame at the supported I/O floor | work-derived |
| WSPR publication | coherent CF32 bytes times source-authentication, copy, retained-authentication, and post-publication-validation passes at the supported I/O floor | work-derived |
| WSPR overall | exact slot wait + capture + frame analyses + summary + publication + cleanup + quiescence | work-derived |
| Keyed capture | generated event timeline plus maintained one-second measurement guard | protocol/work-derived |
| Keyed transaction/campaign | exact capture duration and three authenticated transactions plus cleanup | work-derived |
| Automatic deployment | explicit build, stage, discovery, and delegated campaign ceilings | external tool or plan-bound safety ceiling; never qualification evidence |
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
