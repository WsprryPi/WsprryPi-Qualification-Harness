# Offline archive inventory and multi-capture validation

These commands provide portable, non-qualifying intake for evidence created
outside the current immutable live-session workflow. They authenticate files
and declared relationships without converting them into a qualification claim
or authorizing hardware access.

## Archive inventory

`inventory-archive` authenticates every entry in a canonical SHA-256 manifest.
It rejects unsafe or duplicate paths, symlinks, non-regular files, root escape,
and size or hash mismatch. It writes a new schema-valid inventory outside the
source archive.

Paths in the output are portable, manifest-relative POSIX paths.
Classification is limited to artifact provenance; it does not assert lifecycle
completeness or qualification.

```text
wsprrypi-qualification inventory-archive ARCHIVE_ROOT MANIFEST OUTPUT \
  --archive-id ARCHIVE_ID
```

## Multi-capture relationship validation

`validate-cw-multi-capture` validates a session relationship above at least
three separately authenticated keyed-mode repetitions. Repetition numbering
must be ordered and contiguous. Acquisition IDs, capture paths, capture
content, metadata, and observations must be distinct and authenticated.
Metadata and observations must bind the same normalized plan, mode, and
per-repetition capture.

The validator does not concatenate IQ, describe separate files as one coherent
capture, or issue a hardware judgment. Without a live lifecycle that binds
runtime authorization, execution, stopping, cleanup, and quiescence, the only
accepted final status is `inconclusive` and `qualification_claim` is false.

```text
wsprrypi-qualification validate-cw-multi-capture SESSION.json
```

## Boundary

Archive inventory and relationship validation do not establish acquisition
timing, executable identity, RF-path facts, service ownership, transmitter
cleanup, or backend quiescence unless those facts are independently present in
the authenticated input contract. They cannot substitute for a controlled live
capture or live qualification.
