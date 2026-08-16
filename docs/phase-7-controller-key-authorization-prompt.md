# Phase 7 Controller-Key Authorization Prompt

## Objective

Authorize the existing dedicated `wspr5` controller key for the `pi` account on
`wspr4`, without replacing existing maintainer access. Then prove the complete
strict SSH/helper path, perform read-only candidate preflight, and finalize the
digest that may be presented for a separate live-tone authorization.

## Verified starting state

- `wspr5` controller-key fingerprint:
  `SHA256:uRtQsOJUJbhRNQZkKXTsTgOlbKze4SIsdYmgXkvr8NI`
- Controller-key comment: `wspq-wspr5-to-wspr4-20260813`
- Existing `wspr4` maintainer-key fingerprint:
  `SHA256:hSKZJLmch6WAj9zS/E0OlV9cy/o9QwBqwgJsEkQon9Y`
- Existing `wspr4` authorized-keys SHA-256:
  `299efbe0eebc4a56bcd32c76ffee1810f06be4060a29f952e8f3e62137fcb107`
- Corrected dedicated host-key fingerprint:
  `SHA256:QTU3GpAhxAO6eMiFd1rIP0oHngJZBFHQXg+4faPOhXk`
- Revised candidate canonical digest before transport proof:
  `0bb13cd0a7eb03d6c9b013e30b79adceb89b02bd852823caca5830a6456fccb4`

## Scope and safety boundary

This slice may modify only `/home/pi/.ssh/authorized_keys` on `wspr4`, preserve
its current contents as a timestamped backup, and add exactly the authenticated
`wspr5` public key. It may subsequently exercise strict SSH and read-only helper
operations required by preflight.

Do not remove, reorder, or alter the existing maintainer key. Do not change SSH
daemon configuration, passwords, users, groups, sudoers, services, GPIO, SDR
state, transmitter state, or general trust files. Do not enter an RF digest
confirmation or transmit.

## Required key material

The appended line must exactly match `/home/pi/.ssh/id_ed25519.pub` read through
the authenticated `wspr5` maintainer session:

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPveETzEFs+iAW9nwD/diGUyzeZnduJpXHfRUSQq0JI1 wspq-wspr5-to-wspr4-20260813
```

Before installation, independently recompute its fingerprint and require the
stated `SHA256:uRtQ...` value. Construct a candidate file containing the
existing line followed by this line exactly once. Reject duplicates or any
other difference.

## Installation

1. Transfer the candidate to a temporary path on `wspr4`.
2. Verify its content, fingerprints, ownership intent, mode, and line count.
3. Require that the original file still has the stated starting hash.
4. Copy the original to
   `/home/pi/.ssh/authorized_keys.before-phase7-controller-20260816`.
5. Atomically replace the live file and enforce owner `pi:pi`, mode `0600`.
6. Verify both authorized fingerprints and the preserved backup.

On any mismatch, leave the original active file unchanged.

## Read-only transport and candidate validation

From `wspr5`, with strict host-key checking and the dedicated known-hosts file:

1. authenticate non-interactively as `pi@wspr4.local`;
2. start the pinned capability helper and close stdin without an operation,
   requiring a clean exit;
3. use the helper protocol only for read-only service inspection, source
   identity, ownership, and GPIO idle/quiescence checks where supported;
4. confirm the dedicated transmitter executable and all staged configuration
   hashes;
5. confirm the two scoped services remain active/enabled;
6. confirm GPIO4 remains input and no staged process remains; and
7. validate the exact revised resolved plan using the production loader.

Do not invoke `run-cw-live-tone`: even reaching its digest prompt is outside
this slice. Do not stop services or open the physical SDR.

## Repository and evidence review

Keep the key, backups, machine-local plan, helper configurations, and preflight
outputs outside Git. Confirm ignored local deployment artifacts remain ignored
and that no generated archive or evidence content is staged. Commit and push
only this durable prompt or a separately justified source/test correction.

## Exit criteria

Exit when existing access is preserved, the dedicated controller key works
through the strict pinned transport, read-only preflight is clean, all services
and hardware remain undisturbed, and the exact candidate canonical digest is
confirmed. Any live-tone attempt still requires a separate authorization that
names that final digest.
