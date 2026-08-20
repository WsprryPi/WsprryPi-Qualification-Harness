# Bounded Tone loopback mediator execution prompt

Add the hardware-free, portable mediation layer that the Qualification Harness
needs before it may consume WsprryPi's `bounded_tone` endpoint. Implement a
standard-library RFC 6455 client restricted to literal loopback addresses, with
strict HTTP upgrade validation, masked client frames, bounded frame sizes,
text-only JSON, request-ID correlation, exact start and terminal-response
contracts, monotonic outer deadlines, and best-effort `tone_end` cleanup on
every malformed, rejected, disconnected, or timed-out transaction.

Produce immutable-shaped transaction evidence without claiming RF success. Add
deterministic fake-server tests for success, delayed or missing replies,
rejection, wrong IDs, malformed frames, masked or oversized server data,
disconnects, and cleanup attempts. Document how this mediator will be exposed
through the existing authenticated SSH capability helper in the following
wiring slice.

Do not alter live-session composition, schemas, deployed helpers, hosts,
services, GPIO, SDR, or RF in this slice. Run the complete portable validation
suite, adversarially correct findings, commit and push the attributable changes,
and obtain green macOS, Ubuntu, and native Windows CI.
