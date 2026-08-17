# Split-host live WSPR qualification

The maintained live topology runs the coordinator on the receiver host. The
current qualified-by-design arrangement is:

- `wspr4`: transmitter host, WsprryPi process, transmitter service ownership,
  and GPIO or Si5351 quiescence provider;
- `wspr5`: local RSP1B capture helper, receiver-side helper, profiles, offline
  carrier/audio/decoder tools, and evidence publication.

Live execution is unavailable by default. `run-live-session` requires a
schema-valid plan with `execution_mode: live`, both `--enable-live-session` and
`--enable-rf`, an operator identity, and entry of the exact resolved-plan
SHA-256 at runtime. Committed profile fields never satisfy that confirmation.
The coordinator accepts live execution only from the sealed
`ProductionRealSessionAdapters` class; a caller-provided object cannot opt in
by claiming to be live.

The plan separately pins the two helper identities/configurations, OpenSSH,
the native Soapy capture helper, WsprryPi, `wsprd`, service providers, and the
backend quiescence provider. Transmitter and receiver service lists are
separate. `services.receiver` is the complete receiver-side service allowlist;
the optional `services.receiver_required` must be a subset and identifies
services that must run during local capture. Other receiver services remain
conflicting owners that are stopped for capture. The receiver-helper,
capture-helper, and decoder host must match the receiver; the WsprryPi and
transmitter-helper host must match the transmitter.

Local SDRplay capture still uses the SoapySDR API and the `sdrplay` Soapy
module. Its vendor API daemon may therefore be declared as a required receiver
service. SoapyRemote is a separate network-export layer and is not required
when the capture helper runs locally on the receiver host.

The lifecycle is intentionally ordered:

1. validate the complete plan and ephemeral authorizations;
2. authenticate local executable/profile bytes and both helper sessions;
3. inspect service ownership on both hosts and initial transmitter quiescence;
4. register cleanup, stop named conflicting receiver services, start named
   required receiver services, and verify every requested state;
5. capture RF-off only after required receiver services are running;
6. stop only named transmitter services, start an owned bounded tone, capture
   RF-on, and stop the owned tone;
7. recompute the acquired carrier gate from retained IQ and profiles;
8. only after a pass, begin the coherent receiver capture at the resolved UTC
   margin, launch the owned three-frame WsprryPi request, and require exactly
   92,500,000 CF32 samples;
9. produce three UTC-named WAV files, invoke `wsprd` independently three times,
   and summarize the three consecutive slots;
10. stop owned processes, restore only services changed by this transaction,
    inspect backend quiescence, close both helpers, and publish new-file-only
    evidence plus SHA256SUMS.

This software path does not itself qualify hardware. Before invoking it,
maintainers must perform the non-interference preflight on both Pis and prepare
a complete current plan whose UTC slots leave enough time for RF-off capture
and carrier review. A failed carrier gate suppresses all frame work. Any
cleanup or quiescence failure overrides a successful measurement.
