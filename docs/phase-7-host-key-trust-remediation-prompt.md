# Phase 7 Host-Key Trust Remediation Prompt

## Objective

Repair the dedicated `wspr5` to `wspr4` Phase 7 SSH trust binding after the
authorized live-tone candidate failed closed on a strict host-key mismatch.
Authenticate the current key through two independent paths, preserve the stale
trust artifact, update only the ignored deployment copy and its resolved plan,
produce a new canonical plan digest, and stop before any RF operation.

## Verified failure

- Failed run: `20260816T182534Z-wspr4-wspr5-phase7-tone`
- Authorized plan digest:
  `655396710b51b37837f8d34f710a2b5164b43a5fbb7ed08303309233ced608a3`
- Failure classification: `preflight_failed`
- Carrier gate: `not_run`
- Stale pinned fingerprint:
  `SHA256:gScv3g9z5WS+AbQLErqzGGaTdAVraJT9+BOhG0v4pbI`
- Current `wspr4` ED25519 fingerprint:
  `SHA256:QTU3GpAhxAO6eMiFd1rIP0oHngJZBFHQXg+4faPOhXk`

The original authorization is consumed. It does not authorize a retry.

## Scope and safety boundary

This is a hardware-free trust and plan-preparation slice. It may:

1. read the installed ED25519 host public key through the already authenticated
   maintainer path to `wspr4`;
2. observe the ED25519 key independently from `wspr5` with `ssh-keyscan`;
3. require exact public-key and fingerprint agreement;
4. preserve the existing dedicated known-hosts file as a timestamped backup;
5. atomically install the authenticated one-line replacement;
6. update the ignored resolved-plan transport hashes and fingerprint;
7. copy the revised ignored plan to the staged controller directory; and
8. perform read-only strict-host-key helper and actual-host preflight checks.

Do not stop or start services, open or configure the SDR, touch GPIO, invoke the
dedicated transmitter, enter an RF confirmation, or transmit. Do not alter the
user's general SSH trust database. Modify only
`/home/pi/.ssh/wspq_known_hosts` and the dedicated ignored Phase 7 plan copies.

## Required authenticated replacement

The key read from `/etc/ssh/ssh_host_ed25519_key.pub` on authenticated `wspr4`
must byte-match the key returned for `wspr4.local` by `ssh-keyscan` on `wspr5`:

```text
wspr4.local ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEpbLjM9ID+8yAusbVF4aNkO45+eRg3cITonJ0wRjgDR
```

Expected replacement-file SHA-256:
`49b71d48c63659e3cc49d85d924f817dec4c953106a07e40e9788da039e9fc38`.

Fail closed if either observation differs, if the backup cannot be preserved,
or if post-install strict host-key verification does not resolve to the stated
fingerprint.

## Plan regeneration

Update only these transport facts in the ignored resolved plan:

- `transport_identity.known_hosts_sha256` to the replacement-file SHA-256;
- `transport_identity.transmitter_host_key_sha256` to the authenticated current
  fingerprint.

Then validate the complete resolved plan using the maintained production plan
loader and report both its byte SHA-256 and newly computed canonical live-plan
SHA-256. Copy the exact validated bytes to
`wspr5:/home/pi/wspq-phase7-ed96a6d/config/resolved-plan.json` and verify the
local and remote byte hashes agree.

The RF topology, transmitter executable, frequency, drive, tone schedule,
receiver, gain, sample count, deadlines, service scope, source revisions, and
all other plan facts must remain unchanged.

## Read-only validation

After replacement, verify:

1. `ssh-keygen -F wspr4.local` finds exactly the authenticated key in the
   dedicated file.
2. A strict, pinned `wspr5` to `wspr4` helper session starts and exits cleanly
   with no operation request.
3. The resolved plan validates and produces the reported new canonical digest.
4. Both authorized services retain their initial active/enabled states.
5. GPIO4 remains input and no staged helper, capture, controller, or dedicated
   transmitter process remains.
6. The repository remains free of local deployment artifacts and generated or
   large evidence content.

Review all changed tracked files and ignored deployment differences
independently. Commit and push only the durable prompt or genuine source/test
fixes; never commit the machine-local trust file, resolved plan, credentials,
or run evidence.

## Exit criteria

Exit when the trust mismatch is reproducibly corrected, the exact revised plan
is staged and validated, a new canonical digest is reported, services and hosts
are undisturbed, and no RF has occurred. Any later live run requires a separate
operator authorization naming that new digest.
