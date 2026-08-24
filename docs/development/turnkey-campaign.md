# Turnkey campaign routing

`turnkey-campaign` is a thin operator surface over the maintained production
coordinators. It does not introduce another qualification protocol. TONE and
WSPR route to `RealQualificationSession`; QRSS, FSKCW, and DFCW route to the
live-keyed coordinator.

The subordinate resolved plan remains authoritative for hosts, deployments,
capabilities, deadlines, cleanup, evidence, and final qualification status.
The wrapper binds that complete plan and the small campaign request by path,
size, and SHA-256. It adds only the selected route and a campaign digest.

## Hardware-free workflow

Create a request with `campaign_id`, `mode`, and `execution_policy`. Then use:

```text
wsprrypi-qualification turnkey-campaign plan REQUEST.json MODE-PLAN.json RESOLVED.json
wsprrypi-qualification turnkey-campaign validate RESOLVED.json
wsprrypi-qualification turnkey-campaign rehearse RESOLVED.json RUNS
```

Planning and validation make no external calls and construct no production
adapter. Rehearsal checks the route and writes an authenticated immutable
bundle. Its result is always non-qualifying and contains no subordinate
lifecycle outcomes: rehearsal does not run preflight, install cleanup, or
simulate qualification.

## Live dispatch boundary

Live dispatch requires a request whose `execution_policy` is `live`, a complete
subordinate live plan, a non-empty operator identity, and an exact confirmation
of the resolved campaign digest:

```text
wsprrypi-qualification turnkey-campaign execute RESOLVED.json RUNS \
  --operator OPERATOR --work-directory WORK --ssh /absolute/path/to/ssh \
  --confirm-plan-sha256 SHA256 --enable-turnkey-live --enable-rf
```

No production adapter is imported or constructed until validation and exact
confirmation pass. After that gate, the selected existing coordinator owns all
runtime safety, cleanup, result precedence, and evidence. The command returns
the underlying authoritative bundle; it does not copy or reinterpret it.

Do not infer permission to contact a host, open a device, change a service, or
emit RF from a plan, rehearsal, saved digest, or this guide. Those operations
still require the precise current authority required by the subordinate
coordinator and repository contract.

## Simple five-mode `complete-test`

`complete-test` is the normal fixed campaign surface; the explicit-plan
`turnkey-campaign` commands above remain the advanced interface. The normal
command accepts two hosts and an exact SDR selector, packages the current harness
and local WsprryPi source, stages the required runtime, discovers the selected
SDR immediately, and derives all five mode plans rather than accepting five
operator-authored mode plans. Each host receives an independently owned durable
per-campaign executable; temporary stages are removed, and the retained aggregate
is revalidated afterward. `--enable-rf` also confirms the documented conducted
default: antenna disconnected and a direct 50-ohm SDR input through 20 dB
attenuation. Both endpoints
may be remote to the controller; the controller delegates execution to the
receiver. `--configuration PATH` remains an advanced development override:

```text
wsprrypi-qualification complete-test wspr4 wspr5 \
  --sdr driver=sdrplay,serial=2404058C60 --enable-rf
wsprrypi-qualification complete-test wspr4 wspr5 \
  --sdr driver=sdrplay,serial=2404058C60 --rehearse --configuration PATH
wsprrypi-qualification validate-complete-test CAMPAIGN-BUNDLE
```

Defaults are 20 m, 14,097,100 Hz, `Q0QQQ`, `JJ00`, 0 dBm, keyed message `ET`,
0.7-second QRSS/FSKCW/DFCW dots, and 5.0 Hz FSKCW/DFCW separation. All are
named CLI overrides. WSPR retains the maintained three-frame contract; keyed
modes retain three independent transactions. The application shim derives the
WSPR dial frequency from its maintained 1500 Hz audio offset and derives the
FSKCW space and DFCW dash frequency below the primary according to the existing
protocol ordering.

The command validates the complete bounded execution before constructing a
production adapter. One deliberate invocation is the campaign authorization. Modes
run as TONE, WSPR, QRSS, FSKCW, DFCW. Cleanup, abort, preflight, fixture,
quiescence, or inconclusive outcomes stop all later modes. A failed TONE carrier
also stops the campaign. WSPR decode unqualification and keyed-mode
unqualification remain authoritative but do not prevent later independent modes,
so useful authenticated evidence is preserved. Modes stopped by the matrix are
`not_attempted`; cleanup and quiescence failures retain precedence and cannot be
hidden by aggregation.

Live exit codes are stable: 0 qualified, 2 invalid input, 3 technical blockage
or inconclusive evidence, 4 transmitter unqualification, 5 abort, and 6 cleanup
failure. Aggregate validation recomputes ordering, campaign and subordinate plan
bindings, linked result identities, cleanup precedence, final status, and
qualification scope even if a manifest has been rebuilt after tampering.

Rehearsal routes and authenticates five generated plans but contacts no host,
opens no receiver, inspects no service, touches no GPIO/I2C/GPCLK, and emits no
RF. It is non-qualifying. Same-host local production transport is rejected as
`unsupported_topology`; Track D owns that capability. Track E transmitter-PPM
provenance and later prompt/qualification campaigns are also outside this work.
The current `main` production application/quiescence contracts support GPIO and
Si5351, not `rp1_gpclk`; selecting RP1 therefore returns `missing_capability`
before adapter construction. GPIO4/2 mA RP1 defaults will apply only after that
backend is present in the maintained production contracts and cannot leak into
GPIO or Si5351 plans.
