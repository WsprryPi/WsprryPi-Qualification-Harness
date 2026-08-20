# Bounded Tone loopback mediator

This slice adds the portable, hardware-free RFC 6455 client needed to mediate
WsprryPi's product-owned `bounded_tone` transaction. It does not yet expose the
client through the deployed capability helper or replace the production Phase
7 cadence.

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

The next reviewed slice must add a `bounded-tone` operation to the authenticated
capability helper, bind the literal loopback endpoint and WsprryPi revision in
the resolved live plan and helper configuration, retain helper-side transaction
evidence, and replace per-cycle process launches in `_run_tone_pattern`. That
slice must include failure injection at the helper transport boundary and must
remain hardware-free. Deployment and live RF remain later authorization gates.
