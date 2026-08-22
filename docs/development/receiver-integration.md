# Hardware-free receiver lifecycle validation

`ReceiverIntegrationSession` is a receiver-only lifecycle distinct from the
transmitter-oriented `RealQualificationSession`. It is currently fixed to
`hardware_free_validation`, accepts only a sealed deterministic fake adapter,
and exposes no live CLI command. It cannot connect over SSH, enumerate or open
an SDR, inspect or change services, touch GPIO or I2C, operate WsprryPi, or
transmit.

The resolved plan binds the controller, capture host, optional mandatory
coordination host, helper and capability identities, complete RSP1B-style
receiver identity and settings, current RF path, exact CF32 count and bytes,
deadlines, retention, stopping, release, and artifact contracts. Runtime
receiver authorization is an ephemeral, plan-digest-bound record. It is never
loaded from a profile and cannot authorize a transmitter.

The lifecycle validates capabilities and identities, verifies read-only
ownership and RF-path facts, registers cleanup before acquisition, creates a
compact exact-count RF-off fixture, authenticates its metadata and bytes, then
stops and independently verifies release. Cleanup failure has precedence.
Success remains `inconclusive`; the only other statuses are `fixture_blocked`,
`preflight_failed`, `aborted`, and `cleanup_failed`.

Evidence is published transactionally to a new path-safe run directory with
the resolved plan, authorization, complete session, result, compact fake CF32,
metadata, artifact index, and deterministic `SHA256SUMS`. Failure injection
covers capability, identity, ownership, RF path, exact count, overflow,
timeout, cancellation, disconnect, clipping, helper exit, partial cleanup
registration, stop/shutdown/coordination cleanup, and independent release.
Bundle validation requires the exact lifecycle appropriate to the failure
point; omitting a required helper, capture, cleanup, or release step is invalid. It
also derives cleanup truth from the component details, parses the strict
capture-metadata document, and independently scans the retained interleaved
little-endian CF32 bytes for exact sample count, finite values, and
component-wise clipping. Rebuilding hashes cannot make contradictory evidence
valid.

Capture timing uses parsed timezone-aware canonical UTC values plus finite
monotonic elapsed evidence. The elapsed value must remain within the resolved
capture deadline, and wall-clock duration must agree within 0.1 second to
allow only the documented read-boundary difference. Every hardware-free
preflight stage has a strict stage-specific schema and is reconstructed from
the resolved plan. Such evidence must state that no external process,
physical SDR, or other hardware access occurred.

Failure causes are stable identifiers reconstructed from evidence, never
operator prose. Preflight identifiers follow lifecycle order:
`missing_capability`, `wrong_capture_host`, `coordination_disconnect`,
`helper_mismatch`, `ownership_conflict`, `unsafe_rf_path`,
`cleanup_registration_partial`, and `receiver_absent`. Capture identifiers are
ordered `short_read`, `overflow`, `capture_timeout`, `capture_cancelled`,
`helper_nonzero`, `receiver_disconnect`, then `clipping`. Cleanup identifiers
then include `capture_cleanup_unverified` when applicable. Lifecycle cleanup
identifiers follow as `receiver_stop_failed`, `helper_shutdown_failed`,
`coordination_close_failed`, and `receiver_release_failed`. An otherwise
unrepresented internal preflight or abort exception uses `internal_error`.
Session and result cause arrays must exactly equal this derived ordering, so a
hardware-free bundle cannot claim RF, transmitter, service, GPIO, I2C, or
physical-device activity.

The bundle retains one authoritative `chronology.started_utc`, identical to
the UTC timestamp encoded in the run ID. Runtime authorization may be recorded
from exactly that instant through the inclusive `overall_s` interval before
it; evidence outside that interval is invalid. Durable validation compares parsed
aware UTC datetimes and the plan-bound freshness interval, not the reviewer's
current clock. The standalone and embedded authorization documents must remain
identical.

This capability remains hardware-free. The macOS, Ubuntu, and native Windows CI
matrix validates its portable contract. Contacting `wspr4` or `wspr5` requires
separate authorization for the exact read-only SSH contract; physical SDR
discovery, opening, configuration, and capture require their own authorization.
