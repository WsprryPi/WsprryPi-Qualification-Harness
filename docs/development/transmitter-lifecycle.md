# Hardware-free transmitter lifecycle preparation

`TransmitterLifecycleSession` is a sealed no-qualification transaction. It
accepts only `hardware_free_validation`, a fake adapter that cannot launch a
process or access a network or device, and an application plan produced by the
reviewed WsprryPi shim. It has no live CLI path and always records
`rf_emitted: false` and `qualification_claim: false`.

The lifecycle validates capabilities, the intended host and helper, ownership,
and backend-specific initial idle evidence. Cleanup becomes required before
the cleanup-registration stage is invoked, and that stage must precede the
owned-process event. The fake process records its handle before waiting and
retains arguments, deadline, output, return code, timeout, cancellation, and
disconnect facts. Cleanup independently records process and helper absence,
service restoration limited to the resolved allowlist, and GPIO or Si5351
quiescence. Any cleanup or quiescence uncertainty has `cleanup_failed`
precedence.

Failure injection covers preflight blockage, partial cleanup registration,
launch failure, nonzero exit, timeout, cancellation, disconnect, process leak,
service restoration failure, and quiescence failure. Evidence is published to
a new immutable directory with a deterministic SHA-256 manifest. A successful
exercise is only `inconclusive`; this workflow cannot emit qualification or
carrier/decode classifications.

An authorized read-only Stage A inspection on `wspr4` found an active WsprryPi
service, an installation tmux session, and a dirty source worktree. It stopped
as `fixture_blocked` before helper installation, service mutation, GPIO/I2C
inspection, WsprryPi launch, or RF output. A fresh non-interference inspection
and separate authorization are required before any later boundary.
