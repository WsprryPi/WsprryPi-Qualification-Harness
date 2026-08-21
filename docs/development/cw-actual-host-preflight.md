# Read-only actual-host preflight

This workflow identifies whether one exact tone/CW-family candidate combination is
ready for later live validation, or retains a specific fail-closed reason. It
never transmits, opens an SDR, changes GPIO or clocks, changes a service,
installs software, or makes a qualification claim.

The plan records the transmitter backend and RP1-GPCLK-DKMS Gate D as
`complete`, `incomplete`, or `not_applicable`. The schema accepts
`not_applicable` only with an explicit `legacy_gpio` or `si5351` backend, such
as a frozen Raspberry Pi 4 legacy-GPIO candidate. It rejects that exemption
for `rp1_gpclk`; an incomplete RP1 candidate remains blocked.

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

## Invocation

```text
wsprrypi-qualification run-cw-actual-host-preflight PLAN.json OUTPUT_PARENT \
  --ssh /absolute/path/to/ssh \
  --confirm-plan-sha256 EXACT_PLAN_SHA256 \
  --enable-read-only-host-preflight

wsprrypi-qualification validate-cw-actual-host-preflight BUNDLE
```

A live workflow needs separate explicit RF authorization and a passing,
unchanged preflight. Preflight never grants that authorization by itself.
