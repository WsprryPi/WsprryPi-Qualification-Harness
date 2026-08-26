# Operator security and host trust

Live campaigns use SSH for staging and bounded role-to-role coordination. SSH
reachability alone is not authorization to transmit, access an SDR, change a
service, or use privilege. Establish and verify the trust paths below before a
campaign; the harness still performs its normal plan, RF-path, ownership,
cleanup, and quiescence checks.

## Required trust paths

For the maintained split-host `complete-test` workflow:

1. The execution host must authenticate directly to the transmitter host.
2. The execution host must authenticate directly to the receiver host.
3. The receiver host must authenticate directly to the transmitter host because
   campaign execution is delegated to the receiver, which coordinates the
   transmitter during each mode.

The third path is required even when the execution host can reach both systems.
Same-host operation and other initiation topologies remain future roadmap work.

## Key and host setup

- Create a distinct operator or automation key on each initiating host that
  needs an outbound trust path. Copy only its public key to the destination
  account's `authorized_keys` file.
- Never copy a private key between hosts. Do not depend on SSH agent forwarding.
- Resolve and pin each destination's host key from the machine that will make
  that connection. An alias is local SSH configuration; do not assume the same
  alias or host-key entry exists on another host.
- Restrict key use and destination accounts to the minimum commands and hosts
  required by the deployment. Remove obsolete keys promptly.
- Confirm non-interactive authentication for every required direction before a
  live window. A password, passphrase, host-key, or privilege prompt during a
  campaign is a preflight failure, not a reason to weaken checking.

Typical read-only connection checks are:

```text
# Run on the execution host
ssh TRANSMITTER_HOST true
ssh RECEIVER_HOST true

# Run on the receiver host
ssh TRANSMITTER_HOST true
```

Use the exact configured host names that will appear in the campaign. Review
unexpected host-key changes out of band; never suppress host-key verification
to make a campaign proceed.

## Privilege boundary

The harness does not grant privilege. On Raspberry Pi endpoints, deployment
must provide narrowly scoped, non-interactive privilege for only the reviewed
helper, process, service, and quiescence operations required by the resolved
plan. The helper authenticates the configured privilege wrapper and managed
executables by absolute path and SHA-256 and invokes privilege non-interactively.

Do not grant unrestricted passwordless shell access, make the helper account a
general administrator, or replace the reviewed wrapper with an interactive
workflow. Keep transmitter and receiver permissions role-specific. Validate
configuration before live use with the maintained deployment validation and
read-only host-preflight commands described in
[`CURRENT_WORKFLOWS.md`](CURRENT_WORKFLOWS.md).

## Secret and evidence handling

Private keys, agent sockets, passwords, tokens, machine-local SSH configuration,
and authorization receipts must not be committed to this repository or copied
into result bundles. Result bundles may contain host identities and tool hashes;
store them according to the target project's evidence policy and access controls.
