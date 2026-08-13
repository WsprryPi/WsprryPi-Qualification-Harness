# Actual-host preflight evidence corrections

Requested intent and runtime authorization are separate evidence layers. A
requested plan may remain unauthorized until the operator authorizes an exact
command contract. If that authorization is recorded retrospectively, the
correction must stay labeled retrospective; it cannot become contemporaneous
run evidence.

A remote `hostname` response is only a hostname observation. Exact SSH host
identity requires the negotiated server-key fingerprint retained during the
run. Current `known_hosts` entries are useful context but cannot retroactively
prove which key was negotiated.

Correction bundles never rewrite earlier evidence. The composite validator
authenticates the original manifest and every original artifact, the preceding
correction, the exact ordered SSH command records, the separate and embedded
command contracts, artifact hashes, host mappings, and the final result. It
also requires the complete deterministic artifact set for the superseding
correction.

Controller OpenSSH evidence is current-local retrospective context. Its exact
path, bytes, version invocation, output convention, and chronology are
validated, but it does not prove which binary negotiated the original SSH
sessions. For correction schema version 4, the observation must start no
earlier than the correction request, finish no later than ten minutes after
that request, and take no more than thirty seconds. Both bounds are inclusive.
Correction chronology is likewise a strict machine-readable
contract: it cannot be replaced by prose claiming a host connection, RF, a
hardware action, cleanup failure, or a prior-evidence rewrite.

The recorded preflight remains `fixture_blocked`: ongoing work and active
WsprryPi services were observed, the helper was not installed, and exact host
identity was unresolved. Persistent-helper, service-provider, GPIO, Si5351,
physical-SDR, and RF boundaries were not run. A future attempt must restart at
Boundary 1 after ongoing work ends and retain authorization and the negotiated
server-key identity contemporaneously.
