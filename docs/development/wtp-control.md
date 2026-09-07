# Device-neutral WTP campaign control

The portable `wtp_control` module executes one bounded WTP/1 transaction through
an injected peer. It validates the complete rf-events/1 job before adapter
access, verifies HELLO device identity and negotiated protocol, claims ownership,
loads the whole job,
invokes the caller's time observation immediately before ARM, and requires a
matching inactive terminal job event and STATUS with unchanged boot identity.
LOAD must acknowledge the submitted job and loaded state; ARM must acknowledge
the same job, armed state and exact requested start. Source-specific GPIO or RF synthesis
logic is outside the Harness. All symbol timing remains on the WTP device.

The initial implementation bounds transactions to 162 events, 110.592 seconds
and a future start within 45 seconds. After time observation, ARM must be within
0.1 to 9 seconds of the declared start. Lost LOAD/ARM replies still trigger an
owned-job ABORT attempt. The caller owns transport close/disconnect fallback,
physical RF/source interlocks, capture readiness and final observed inactivity.
An unverified initial inactive state prevents LOAD and ARM. Cleanup failure
prevents a successful transaction even after an apparently complete job.

`run_transaction` is an adapter API, not a new implicit live authorization.
Call it only from an explicitly authorized, bounded live coordinator that owns
receiver capture, job provenance, source sequencing and cleanup. WsprryPico's
`pio_campaign.py` supplies the initial Mac USB/wspr5 SSH adapter and target-owned
band/mode plan. Existing WsprryPi `complete-test` routes remain as documented.
This adds experimental WTP control capability, not completed general WTP or RF
qualification. Native Windows serial operation is not supplied by the Pico adapter.

For retained transaction evidence, the hardware-free command is:

```text
python -m wsprrypi_qualification validate-wtp-transaction TRANSMITTER.json
```

The validator authenticates the job digest and checks matching terminal/status
identity, boot continuity, LOAD/ARM acceptance and completion/cleanup claims.
Malformed nested evidence and invalid schema-version types produce structured
validation failure. A valid record can describe failure.
Its `qualification_claim` is always false: a WTP completion report does not
replace independent capture, decoding, timing, spectra or physical quiescence.

Tests in `tests/test_wtp_control.py` inject success, lost mutating replies,
invalid jobs, stale terminal events, receiver failures and unsafe idle states.
No test opens USB, SSH, an SDR or a transmitter.

Short transactions explicitly release their own WTP lease after verified
completion or abort. USB close alone is not ownership release. A lost CLAIM
reply is reconciled against the exact generated owner ID; foreign owners are
never released. Captured completion status is retained before RELEASE clears
the current device job. Regression coverage includes consecutive short jobs,
lost CLAIM/LOAD/ARM replies and foreign ownership.

The existing `progress_viewer.py` accepts Pico campaign band/mode progress JSONL
as well as `complete_test_progress` records. Launch the script with the Harness
Python interpreter and the Pico campaign's `progress.jsonl` path. Band identities
remain separate, and RF failures stay failures even when transmission completed.
This display is read-only and carries no independent qualification authority.
