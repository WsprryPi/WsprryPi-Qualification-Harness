# Phase 1 execution prompt: tone and CW-family contract alignment

## Objective

Implement only Phase 1 of `docs/cw-mode-gap-closure-contract.md`. Establish a
portable, fail-closed, versioned document chain for `tone`, `cw`, `qrss`,
`fskcw`, and `dfcw` without implementing reference encoders, IQ analysis, live
mode orchestration, hardware access, or RF activity.

## Governing constraints

Read and obey `CONTRACT.md`, `AGENTS.md`, `docs/AGENT_OPERATIONS.md`, and the
gap-closure contract. Preserve historical evidence and existing user work. Keep
tone first-class and distinct from keyed CW. Do not invent a WsprryPi production
CW CLI mapping; Phase 1 plans may name CW, but production execution must remain
unavailable until a reviewed versioned interface exists.

## Required implementation

1. Add review-facing and byte-identical packaged JSON Schemas for:
   - a resolved mode plan with predeclared thresholds;
   - an independently generated expected-event timeline;
   - analyzer-generated observations bound to the plan, timeline, and capture;
   - a carrier/mode gate document; and
   - a final session document binding measurement and lifecycle evidence.
2. Model all five modes. Apply mode-specific contracts:
   - tone has bounded off-on-off cycles and no message gate;
   - CW and QRSS use on-off keyed messages and distinct declared timing;
   - FSKCW and DFCW require two frequencies and versioned semantics.
3. Make thresholds authoritative only in the resolved plan. Downstream
   documents must bind the exact plan by SHA-256 and must not redefine
   thresholds.
4. Require generated observations to declare generator identity and prohibit
   caller-authored/manual observations from being authoritative.
5. Add semantic validation that authenticates every document and capture,
   verifies the hash chain, enforces mode consistency, rejects contradictory
   gates/statuses, and rejects unsupported positive qualification claims.
6. Preserve the existing version-1 `cw_qualification_analysis` input as legacy,
   explicitly non-qualifying evidence. Do not silently reinterpret it as the
   new chain.
7. Align new final statuses with `CONTRACT.md`; do not introduce generic
   `unqualified` in the new contracts.
8. Add a CLI validation command, documentation, schema synchronization checks,
   and focused positive/adversarial tests.

## Required fail-closed behavior

Reject missing or non-finite data, mode confusion, tone/CW conflation,
post-selected or downstream thresholds, wrong or stale hashes, capture size or
hash mismatch, manually sourced observations, invalid event/state combinations,
contradictory gate outcomes, cleanup precedence violations, synthetic positive
claims, and any Phase 1 positive hardware qualification claim.

## Compatibility

Keep version-1 schema and loader behavior available for historical documents,
but ensure its public result remains non-qualifying. New documents use distinct
evidence types and schema version 1 per document family; they must not masquerade
as upgraded version-1 analysis.

## Verification

Run formatting, lint, strict typing, complete pytest, package build, native mock
build/CTest, schema byte-synchronization, provenance verification where
applicable, and `git diff --check`. Run no hardware-dependent test.

## Exit gate

Phase 1 is complete only when schemas and semantic validation reject mode
confusion, contradictory claims, downstream/post-selected thresholds, broken
artifact bindings, and unsupported positive claims; all safe validation passes;
an adversarial review has no unresolved material finding; and the working tree
contains only the intended Phase 1 documentation and implementation.

## Adversarial findings injected during execution

### Assessment 1

1. Tone timelines must require actual quiet-carrier alternation with leading and
   trailing quiet intervals; merely counting three carrier events is inadequate.
2. Expected-event roles must be constrained per mode so CW, QRSS, FSKCW, and
   DFCW documents cannot reuse another mode's event vocabulary.
3. Synthetic or non-live evidence must remain `inconclusive`; it must not issue
   transmitter-attributable `unqualified_*` results without an authenticated
   acquired live lifecycle.
4. Expected-event generators must explicitly declare harness-generated origin,
   matching the generated-observation provenance boundary.
5. The public CLI path needs an end-to-end success/failure test in addition to
   direct semantic-validator tests.

These findings are mandatory corrections. Repeat the adversarial assessment
after implementation and append any further findings below.

### Assessment 2

1. Bare lifecycle booleans are self-asserted rather than evidence. Every true
   runtime-authorization, live-session, cleanup, or quiescence fact must bind an
   authenticated artifact; an unestablished fact must carry a null reference.
2. Until reviewed reference generators, analyzers, and live composition exist,
   declarative generator identity is not enough to attribute failure to
   hardware. Phase 1 semantic validation must therefore force every final
   result to `inconclusive`, while retaining the governing status vocabulary in
   the schema for later phases.

These corrections prevent Phase 1 fixtures or caller-authored documents from
qualifying or unqualifying hardware.

### Assessment 3

The resolved mode plan did not bind the complete qualification unit. Add exact
transmitter host/output/model/drive/clock facts, receiver host/driver/device
identity, and current RF-path attenuation/filter/termination/antenna/safe-input
facts. Because every downstream document hashes the plan, these additions make
cross-combination evidence substitution detectable.

### Assessment 4

1. Require an explicit version marker in every protocol-definition identifier.
2. Reject shifted tones that are not separated by more than the declared
   spacing tolerance.
3. Reject requested primary or secondary frequencies outside the planned
   receiver Nyquist span.
4. Reject expected timelines that extend beyond the exact planned capture.
5. Reject spacing and transition thresholds, as well as frequency and timing
   tolerances, when they are tighter than analyzer resolution.

### Assessment 5

Tone carrier events must explicitly require continuity; alternating RF state
alone is insufficient to express the continuous-carrier contract.

### Assessment 6 - Final

No additional material finding remained after reinjecting Assessments 1 through
5. The complete safe local validation suite passed. Phase 2 encoders, Phase 3
analysis, actual-host work, live orchestration, and hardware qualification were
not performed.
