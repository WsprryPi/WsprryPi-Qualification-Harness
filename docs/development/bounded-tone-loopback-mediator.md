# Bounded Tone loopback mediator

This component provides the portable, hardware-free RFC 6455 client needed to mediate
WsprryPi's product-owned `bounded_tone` transaction. The authenticated
capability helper exposes it as `bounded-tone`, and the production carrier-only
cadence retains each complete helper response instead of launching a WsprryPi
process per cycle.

The mediator accepts only literal `127.0.0.1` or `::1` endpoints. Hostnames,
LAN addresses, redirects, TLS substitution, and proxy discovery are rejected.
It validates the HTTP upgrade and `Sec-WebSocket-Accept`, masks every client
frame, accepts only complete unmasked server text frames, bounds headers and
payloads, and decodes only JSON objects.

Each transaction requires a restricted request ID, positive RF frequency, a
product duration from 1 through 60000 milliseconds, and a monotonic outer
deadline longer than that duration. Success requires both the correlated start
acknowledgement and the correlated terminal event showing `stopped: true` and
`scheduler_restored: true`. Once the bounded-tone request has been sent, any
malformed, mis-correlated, rejected, disconnected, or expired response path
makes a best-effort `tone_end` cleanup request before failing. The transaction
reserves part of the hard outer deadline for that cleanup attempt; cleanup does
not extend the authorized deadline.

In production, the resolved `helper_s` deadline is the inner transaction
deadline. It covers the requested RF-on duration, terminal response processing,
and the helper's reserved cleanup attempt. The separately resolved
`transmitter_s` deadline bounds transport, helper serialization, and return of
the authenticated response while the overall-session cleanup reserve remains
untouched. Tone on/off cadence controls absolute RF transition scheduling and
must never be reused as either request deadline.

Returned evidence is explicitly non-qualifying. It records the two product
responses and the requested bounds but cannot establish RF timing, GPIO state,
frequency, power, receiver behavior, or cleanup on a physical backend.

For `cw_live_tone`, the resolved transmitter-helper subplan and deployed helper
configuration must bind the same literal loopback endpoint and exact WsprryPi
source revision. Those fields participate in the helper and operator digests;
changing either requires newly generated configuration, plan, authorization,
and evidence. Deployment and live RF remain later authorization gates.

The live adapter owns one dedicated WsprryPi process for the complete carrier
cadence. Its exact argument vector must select the plan-bound INI file, socket
port, and `--socket-loopback-only`; the helper re-hashes both the executable and
the INI argument immediately before spawning it. A tracked INI is source
provenance only: deployment copies it into a unique external runtime directory
before final plan authorization, and the child receives only that staged path.
The final plan binds the protected Git roots, source and staged identities, and
external working directory. Legacy plans that pass a checkout path through
`-i` are rejected. The service instance is
stopped first when required, the dedicated server is started only after cleanup
ownership is installed, and every tone cycle uses the same server transaction
endpoint. The server is then stopped and its complete owned-process result is
retained before service restoration and GPIO quiescence verification. A
missing server, changed INI file, premature exit, or unverified owned stop makes
the run unsuccessful. No repository snapshot or post-stop working-tree comparison
is performed. Unrelated checkout changes do not affect cleanup qualification;
operator work is never automatically repaired or restored.
