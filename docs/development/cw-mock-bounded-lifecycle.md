# Mock bounded lifecycle

This workflow binds an authenticated tone/CW-family measurement chain to the
reviewed supervisor. It rehearses receiver acquisition/start, transmitter mode
start, the bounded capture interval, cancellation, stopping, release, narrow
service restoration, owned-process leak verification, and mock backend
quiescence. It uses no hardware, remote host, physical service, or RF path.

Create and validate evidence with files in one immutable evidence directory:

```text
python -m wsprrypi_qualification run-cw-mock-lifecycle \
  plan.json expected-events.json observations.json mode-gate.json lifecycle.json
python -m wsprrypi_qualification validate-cw-mock-lifecycle lifecycle.json
```

`--injection` accepts only the closed choices shown by `--help`. No executable,
argv, callback, adapter, or shell input is accepted. The validator authenticates
the upstream hash chain and deterministically replays the declared injection,
then compares the safety-significant supervisor trace while excluding only
wall-clock timestamps.

A clean rehearsal is `inconclusive`; it is not a live-session pass. Setup or
capture failure is `fixture_blocked`, cancellation is `aborted`, and stop,
release, restoration, leak, or quiescence failure is `cleanup_failed`. Cleanup
failure retains precedence even when the carrier and mode measurements passed.
Every document has `mock_only: true` and `qualification_claim: false`.

Read-only actual-host preflight and every live workflow require separate,
current authorization. Preflight performs no transmission and makes no
qualification claim.
