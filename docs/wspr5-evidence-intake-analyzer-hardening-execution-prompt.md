# Execution prompt: wspr5 evidence intake and analyzer hardening

## Objective

Use retained, already-acquired `wspr5` evidence from the 2026-08-16 Si5351
2200 m session to improve the portable WsprryPi Qualification Harness without
performing any new live, hardware, service, or RF operation. Authenticate and
inventory the useful source artifacts, exercise representative QRSS, FSKCW,
and DFCW captures through the maintained acquired-IQ replay boundary, and
promote only generally useful analysis behavior into reviewed production code
and tests.

This slice improves offline evidence intake and analysis. It does not implement
a live Tone/CW supervisor, change WsprryPi, alter qualification policy, or make
a new hardware qualification claim.

## Verified starting context

- The local branch is `codex/issue-401-cw-qualification` at
  `79d8f9293a0bd73bec6d79af5bd0ef9ee6a74fc8`, initially clean and tracking the
  corresponding origin branch.
- The retained source session is
  `/home/pi/issue401-si5351-2200m-current-20260816T125941Z` on `wspr5`.
- Its recorded WsprryPi source identities are parent
  `80237f6f53e66b78784862e371b0fd5de45ccfea` and transmitter component
  `c416e0f4de608164a10d7f0fe2f5adf6f5b911ce`.
- Representative QRSS, FSKCW, and DFCW captures are exact-count CF32LE at
  250,000 samples/s, centered at 162,500 Hz, with fixed 10 dB gain, AGC and
  bias tee disabled, the first read discarded, zero overflow, zero clipping,
  and SDRplay serial `2404058C60`.
- The retained ad hoc keyed-mode analyzer demonstrated a useful technique for
  separating common slow frequency drift from the commanded two-state shift:
  fit frequency as intercept plus time drift plus tone state, and inspect
  signed frequency changes locally across commanded transitions.
- The harness already implements schema-bound plans, independent expected
  events, synthetic IQ analysis, acquired-IQ replay composition, manifests,
  and replay recomputation. Extend those contracts rather than introducing a
  parallel analyzer or permanent one-off script.

## Governing constraints

Read and obey `CONTRACT.md`, `AGENTS.md`, `docs/AGENT_OPERATIONS.md`,
`docs/cw-mode-gap-closure-contract.md`, and the Phase 1 through Phase 5
execution prompts. Preserve all user and historical work.

The slice is strictly offline:

- Do not transmit or generate a tone.
- Do not open, discover, or reconfigure an SDR.
- Do not touch GPIO, I2C, DMA, PWM, GPCLK, or Si5351 hardware.
- Do not start, stop, restart, enable, disable, or modify a service.
- Do not invoke WsprryPi operationally or run any live-session command.
- Do not modify `wspr5`, its repositories, evidence, configuration, or files.
- Remote access is read-only and limited to inventorying or copying retained
  regular files for offline analysis.
- Keep large IQ outside Git. Do not add captures or generated replay bundles.
  A source-inventory record may identify the observed remote root, but no
  executable profile, fixture, bundle, or production default may depend on an
  absolute machine-local path.
- Do not change WsprryPi or WSPR-Transmitter repositories, band policy, mode
  status, operator documentation, or GitHub state.

## Required work

1. Add a durable development record that identifies each retained artifact
   actually used by host, source path, size, SHA-256, capture facts, and its
   intended harness use. Clearly distinguish directly verified facts from
   inferred or unavailable facts. Do not present this inventory as a portable
   executable profile or qualification result.
2. Copy only the minimum representative IQ and metadata needed for execution
   to a disposable local path outside Git. Verify every copied byte against
   the recorded source size and SHA-256 before analysis.
3. Construct disposable harness-compatible resolved plans, expected-event
   documents, and acquired-capture metadata from retained facts. Do not fill
   unknown RF-path, drive, clock, or lifecycle facts with invented values. If
   the strict replay contract cannot truthfully represent an artifact, record
   the exact incompatibility instead of weakening the schema.
4. Run the maintained Phase 4 replay composer and recomputing validator on one
   QRSS, one FSKCW, and one DFCW capture where truthful inputs can be formed.
   Keep every result non-qualifying and outside Git.
5. Compare the retained ad hoc algorithms with `cw_iq.py`. Promote only generic
   missing behavior. Shifted-mode measurements must tolerate common linear
   carrier drift without tolerating wrong spacing, swapped state assignment,
   reversed commanded transitions, carrier interruption, timing errors, or
   non-finite/unresolvable evidence.
6. Keep the independent expected-event timeline authoritative for commanded
   state and timing. Measurements must still originate from IQ. Do not hardcode
   Issue 401 paths, 137,500 Hz, `TEST`, backend, band, sample rate, message, or
   shift in production analysis code.
7. Add focused synthetic tests for a correct shifted mode with common linear
   drift and adversarial cases for wrong spacing, swapped/reversed tone state,
   excessive drift or unresolvable estimation, interrupted continuity, and
   regression instability. Existing no-drift behavior must remain unchanged.
8. Update the relevant development guide to describe what the retained
   evidence taught the harness, what was promoted, what remained disposable,
   and which live-supervisor and WSPR-conversion work remains deferred.

## Non-goals

- No Phase 7 or Phase 8 live execution.
- No new capture, transmission, decode, or hardware observation.
- No reuse of a passing measurement as lifecycle or qualification evidence.
- No wholesale copy of remote scripts into production or `historical/`.
- No permanent dependency on NumPy code outside the existing dependency
  contract.
- No WSPR IQ-to-WAV repair in this slice; record the mirrored-decoder-output
  observation as a later regression target only.
- Commit and push the reviewed slice only under the operator's explicit
  authorization. Do not create or switch branches, force-push, rewrite history,
  open a pull request, publish a release, or perform any other publication.

## Required validation and evidence

- Authenticate source and disposable copied artifacts by size and SHA-256.
- Run focused reference, IQ, contract, replay, and schema tests.
- Run the complete safe local Python suite, formatting, lint, strict typing,
  package build, hardware-disabled CMake build and CTest, provenance check,
  schema source/package synchronization check, and `git diff --check`.
- Record skipped or unavailable checks separately from passes.
- Review the complete diff and working tree for unrelated or generated files.

## Adversarial review

After implementation, perform a separate evidence/safety audit. Treat every
material finding as a blocker. At minimum, attempt to show that:

- expected events or metadata, rather than IQ, can dictate a passing result;
- common drift can hide wrong tone spacing or state assignment;
- regression can pass singular, one-state, non-finite, or inadequately sampled
  evidence;
- new logic weakens clipping, continuity, timing, or failure precedence;
- the inventory overstates missing RF-path, binary, lifecycle, or provenance
  facts;
- absolute host paths or large/generated artifacts entered Git; or
- an offline replay can imply a live or qualification claim.

Repair actionable findings and repeat the audit until no unresolved material
finding remains. Append the findings and closures to this prompt.

## Exit criteria

This slice is complete only when the retained source artifacts used are
authenticated and durably inventoried; representative acquired captures have
either passed the maintained offline replay boundary or have a precise recorded
contract blocker; generic drift-aware shifted-mode analysis is implemented and
adversarially tested if the comparison proves it is missing; all applicable
local gates pass; no hardware or remote state changed; no unsupported claim is
made; the adversarial review is clean; and the working tree contains only the
intended prompt, documentation, production code, and tests.

## Adversarial findings during execution

This section is append-only. Record each assessment, its findings, repairs, and
reverification here.

### Assessment 1

1. The proposed representative Phase 4 replay could not be constructed
   truthfully. Each retained keyed file contains one repetition, while the
   strict plan requires at least three repetitions described by one regenerated
   timeline in one capture. QRSS also lacks structured acquisition UTC
   metadata. Closure: no large IQ was copied into the disposable intake
   analysis area, no schema was weakened, and the exact blockers and
   authenticated source artifacts are recorded in
   `docs/development/wspr5-retained-evidence-intake.md`.
2. Direct unit tests of the extracted regression helper would not prove that
   the production IQ path populated or enforced the model. Closure: a complete
   synthetic FSKCW capture is now modified with bounded common linear drift and
   passed through the public analyzer; it must pass with the measured drift and
   shifted-frequency model retained in generated observations.
3. A schema-valid passing observation could have its new shifted-frequency
   summary rewritten and then accepted by the Phase 1 semantic chain without
   checking plan consistency. Closure: semantic validation now requires a
   passing shifted mode to retain a model whose primary frequency, signed
   spacing, drift excursion, residual, and transition counts satisfy the
   resolved plan and thresholds. Unshifted modes reject a shifted model.
4. Reversed state assignment needed an explicit direction assertion rather
   than relying only on the spacing error. Closure: the adversarial reversed
   case now requires `transition_direction` as well as rejection of the signed
   spacing.

### Assessment 2 - Final

Reassessment confirmed that expected-event state remains the independent
command reference while all model inputs originate in measured IQ event
frequencies and boundaries; bounded common drift passes; wrong spacing,
reversed assignment, excessive drift, missing state coverage, unstable fits,
clipping, and carrier interruption fail closed; replay remains non-qualifying;
the inventory explicitly records missing binary, RF-path, lifecycle, and QRSS
UTC facts; and no remote file, hardware state, large IQ, generated replay, or
absolute-path-dependent executable artifact entered Git. No unresolved
material finding remains in this slice.

### Assessment 3 - Milestone completion review

1. A schema-valid shifted-frequency summary could change its secondary
   frequency, reference time, drift excursion, residual, or transition counts
   independently of the measured event rows while retaining a passing result.
   Closure: semantic validation now recomputes the reference, secondary
   frequency, drift excursion, and residual from measured events and rejects
   contradictory transition counts. Focused tamper tests cover every retained
   derived field, including transition counts recomputed from the measured
   state sequence.
2. The intake record's statement that no large IQ was copied locally became
   ambiguous after the later 16 GB pre-DKMS preservation snapshot was copied
   under ignored `local/`. Closure: the record now limits the statement to the
   disposable intake analysis area and identifies the later archive as ignored
   preservation input, not a fixture or qualification bundle.
3. The prompt's original no-commit/no-push boundary conflicted with the
   operator's later explicit milestone authorization. Closure: the prompt now
   permits only the reviewed current-slice commit and current-branch push while
   retaining the prohibitions on branch changes, history rewriting, pull
   requests, releases, and broader publication.

Reassessment found no path by which expected events can replace IQ-derived
measurements, common drift can hide incorrect signed spacing or transition
direction, generated summaries can be post-selected into a passing result, or
the ignored archive can enter the reviewed Git changes. No unresolved material
finding remains.

### Completion evidence

- Focused CW contract tests: 104 passed.
- Complete Python suite: 811 passed.
- Ruff format and lint, strict mypy, package sdist/wheel build, hardware-disabled
  CMake build, CTest 2/2, provenance verification, schema source/package byte
  comparison, and `git diff --check`: passed.
- The isolated package build could not query PyPI in the restricted execution
  environment; the installed, no-isolation build completed successfully. No
  dependency or package defect was observed.
- Git ignore inspection confirmed `local/`, `runs/`, and `dist/` remain ignored.
  No tracked or proposed source, schema, test, or documentation file exceeds
  10 MB; the 16 GB preservation archive is absent from the Git candidate set.
