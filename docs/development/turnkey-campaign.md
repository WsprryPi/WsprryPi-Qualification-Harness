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
