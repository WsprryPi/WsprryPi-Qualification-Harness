# Phase 6 read-only actual-host preflight

Phase 6 identifies whether one exact tone/CW-family candidate combination is
ready for later live validation, or retains a specific fail-closed reason. It
never transmits, opens an SDR, changes GPIO or clocks, changes a service,
installs software, or makes a qualification claim.

## Safety boundary

The plan fixes each host, role, strict OpenSSH host-key alias, expected hardware
and repository identity, tools, groups, services to inspect, conflicts, RF-path
declaration, known blockers, and deadline. Execution additionally requires the
SHA-256 of the exact plan and `--enable-read-only-host-preflight`.

Commands are constructed internally; arbitrary remote commands are not
accepted. Every token passes a classifier that rejects privilege changes,
shells, metacharacters, chaining, mutation-capable commands, `/dev` access,
unsafe paths, mutating `systemctl` operations, and Git inspection that does not
disable optional locks. Local process execution uses an argument list,
`shell=False`, closed standard input, a hard timeout, and bounded output.

The probes retain hostname, kernel, OS, Raspberry Pi model/revision, effective
identity and groups, clock state, process names, loaded modules, repository
revision/cleanliness, required executable paths, and selected service state.
They do not invoke `sudo`, DKMS, module administration, GPIO/I2C/DMA/PWM/GPCLK,
Soapy device discovery, WsprryPi, or RF operations.

## Evidence and validation

Each new run contains the resolved plan, sealed command contract, bounded
command records, per-check result, and canonical `SHA256SUMS`. Validation
rejects symlinks, missing or extra files, manifest drift, command substitution
or reordering, invalid timing/outcomes, unsafe probes, false readiness, and any
result or blocker that does not match recomputation from retained records.

## Actual-host result on 2026-08-15

The accepted run contacted `wspr4` and `wspr5` using strict known-host checking
and LAN addresses after an earlier immutable mDNS attempt failed closed. It
confirmed the exact host/model/revision, repository, tool, group, kernel, OS,
module, process, and service observations. The outcome remained `blocked`:

- RP1-GPCLK-DKMS Gate D reported `executionReady: false`;
- pushed cross-platform CI for the preceding phases was not confirmed;
- current RF-path facts were not declared;
- `SoapySDRServer` was active on `wspr5`.

No state was corrected during preflight. Phase 7 remains unauthorized.

## Actual-host prerequisite refresh on 2026-08-16

A new digest-bound read-only bundle at harness revision `9961be0` validated the
current host identities, synchronized clocks, required groups and tools, clean
repositories, and exact WsprryPi revisions `0bb9600` on `wspr4` and `c83c19b`
on `wspr5`. The immutable bundle and machine-local plan remain in
`/private/tmp`; they are not portable fixtures and are not committed.

The result remains `blocked`. Current Gate D `executionReady: true` evidence is
not available, current physical RF-path facts are not declared, `wsprrypi` is
active on `wspr4`, and `SoapySDRServer` is active on `wspr5`. The bundle's
manifest and semantic validator pass, `next_phase_authorized` is false, and no
host state was changed. Phase 7 remains unauthorized.

## Invocation

```text
wsprrypi-qualification run-cw-actual-host-preflight PLAN.json OUTPUT_PARENT \
  --ssh /absolute/path/to/ssh \
  --confirm-plan-sha256 EXACT_PLAN_SHA256 \
  --enable-read-only-host-preflight

wsprrypi-qualification validate-cw-actual-host-preflight BUNDLE
```

The exact next roadmap phase is Phase 7, bounded live tone validation. It needs
separate explicit RF authorization and a passing unchanged preflight.
