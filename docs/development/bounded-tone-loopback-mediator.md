# Bounded Tone loopback mediator

This slice adds the portable, hardware-free RFC 6455 client needed to mediate
WsprryPi's product-owned `bounded_tone` transaction. The authenticated
capability helper now exposes it as `bounded-tone`, and the production Phase 7
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

Returned evidence is explicitly non-qualifying. It records the two product
responses and the requested bounds but cannot establish RF timing, GPIO state,
frequency, power, receiver behavior, or cleanup on a physical backend.

For `cw_live_tone`, the resolved transmitter-helper subplan and deployed helper
configuration must bind the same literal loopback endpoint and exact WsprryPi
source revision. Those fields participate in the helper and operator digests;
changing either requires newly generated configuration, plan, authorization,
and evidence. Deployment and live RF remain later authorization gates.
