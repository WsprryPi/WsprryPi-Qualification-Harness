# Future roadmap

This roadmap contains only work that remains. Completed implementation history
belongs in Git and merged pull requests, not in the active roadmap.

## Phase 1 — Universal execution topology

**Status: on hold**

Allow a transmitter host, receiver host, or third system to initiate a live
campaign through role-specific, independently authenticated connections.
Support same-host and split-host layouts without relying on copied private
keys, implicit agent forwarding, or the initiating host's aliases being valid
on another machine. Keep privileged operations narrowly scoped, bounded, and
fail closed across transport loss.

## Phase 2 — Multi-frequency campaigns

**Status: planned**

Run a selected campaign across multiple requested frequencies while resolving
shared parameters once, binding every frequency-specific plan independently,
placing generated collateral outside source repositories, and cleaning up only
runtime material owned by the campaign.

## Phase 3 — Operator experience

**Status: planned**

Improve preflight guidance, confirmation, progress presentation, failure
explanations, recovery guidance, and result discovery without weakening the
existing authorization, evidence, or cleanup boundaries.

## Phase 4 — Qualification campaigns

**Status: planned**

Define and execute separately authorized qualification campaigns. Keep claims
specific to the exact backend, band, hardware, source, receiver path, settings,
and cleanup result; never infer qualification from rehearsal or from another
campaign configuration.

## Current rig capability

A working conducted test rig can run the default five-mode campaign against
the Si5351 backend and the GPIO backend used by Broadcom/DMA WsprryPi versions.
This repository retains no campaign qualification data. Results remain
configuration-specific and belong with the target project or an approved
evidence store.
