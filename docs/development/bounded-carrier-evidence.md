# Bounded carrier evidence

The 2026-08-13 20 m carrier boundary produced two local, ignored evidence
bundles. The first attempt remains `cleanup_failed`: its RF-off capture
completed, but the controller response deadline closed the receiver helper
before in-run restoration could be verified. Later recovery does not change
that result. The retry remains `unqualified_carrier`: the strongest added
feature was 177.764892578125 Hz above the request, outside the 100 Hz gate,
although 0.9999765555030494 of resolved added power occupied the best 20 Hz
channel. No frame or decoder stage ran.

The raw IQ stays under ignored `runs/` directories and must not be committed.
Each original bundle has its own manifest. The committed
`evidence-anchors/bounded-carrier-original-anchors.json` independently pins the
exact original manifest and artifact identities without committing raw IQ.
This external anchor is necessary because an attacker can regenerate a bundle
manifest and its correction reference together. A separate `correction-2`
directory binds the maintained anchor; it does not rewrite or relabel the
original run.

## Authorization boundaries

RF-path approval and permission to operate hardware are separate facts.
Universal RF-path approval means only that the operator permits reuse of that
approval policy; each run still records the current path. It does not by itself
authorize receiver access or transmission. Live authorization must bind the
resolved plan, run ID, receiver access, transmitter operation, and transaction
scope. A single-run authorization cannot be reused.

Contemporaneous authorization requires an independently retained UTC time.
The correction keeps the verbatim operator statement separate from a harness
interpretation. The statement contains no invented run ID, digest, or
timestamp. The interpretation explains which runs and plan the harness
associated with the prompt, but is marked `operator_authenticated: false` and
is not qualification-grade runtime confirmation. Retrospective reconstruction
documents why the work proceeded without curing the standalone bundle's
missing contemporaneous confirmation evidence.

## RF-path values

A radiated path may record attenuation and termination as not applicable. It
must not turn `N/A` into zero attenuation or invent a filter description. The
operator facts for these runs are radiated, antenna connected, termination
N/A, attenuation N/A, filter `None`, and safe-input basis N/A. Conducted paths
remain stricter: they require a termination and numeric nonnegative
attenuation.

## Cleanup and recovery

In-run cleanup and later recovery are different evidence. A later observation
cannot repair a cleanup failure. Process-absence or service-state claims need
the retained command/provider identity, host and plan binding, structured
operation, UTC bounds, outcome, output, and artifact identity. The existing
summary-only post-run annotations are explicitly unsupported and are not used
to classify either result. The retry's in-run evidence supports its service
restoration and GPIO-input claims.

The anchor's exact allowlists and semantic checks independently establish that
the plans prohibited frames, session logs contain no frame/decode phase, decode
gates remained `not_run`, and no coherent-frame capture, slot WAV, `wsprd` log,
decoder evidence, or decode summary was retained.

This carrier evidence does not establish calibrated power, filtering,
harmonic or spurious-emission compliance, antenna readiness, WSPR decoding, or
transmitter qualification.
