# Execution prompt: wspr5 archive normalization and WSPR regression

## Objective

Turn the ignored 2026-08-16 `wspr5` preservation snapshot into authenticated,
portable intake metadata without importing its 16 GB of evidence into Git.
Define a fail-closed contract for composing independently acquired CW-family
repetitions, classify retained sessions without making qualification claims,
and use the retained three-frame WSPR capture to verify the maintained
intended-side IQ-to-WAV conversion path.

This is an offline evidence-normalization slice. It does not implement Phase 7,
operate a transmitter or receiver, change WsprryPi, or reinterpret historical
evidence as a harness-native live qualification bundle.

## Verified starting context

- Branch `codex/issue-401-cw-qualification` is clean at
  `f1b72836c5a3d11aa58871191bc5fac458a16f9b`, tracking the same origin branch.
- `local/wspr5-pre-dkms-20260816/` is ignored and contains a whole-archive
  `ARCHIVE-SHA256SUMS`, baseline verification, 1,343 evidence entries, and clean
  repository snapshots totaling approximately 16.2 GB.
- The retained Si5351 2200 m session contains three separately acquired QRSS,
  FSKCW, and DFCW repetitions. They cannot be represented as one coherent
  Phase 4 capture without an explicit composition layer.
- `wspr/three-frames.cf32` contains exactly 92,500,000 CF32 samples at 250 ksps
  with zero overflow. The planned first slot is `2026-08-16T13:04:00Z`.
- The retained improvised converter interpreted complex data through a real
  path and produced repeated symmetric copies. The maintained converter in
  `audio.py` performs explicit complex mixing and windowed-sinc resampling.

## Governing constraints

Read and obey `CONTRACT.md`, `AGENTS.md`, `docs/AGENT_OPERATIONS.md`,
`docs/cw-mode-gap-closure-contract.md`, and the existing Phase 4/intake records.

- Perform no SSH or other host access.
- Do not open or discover an SDR, transmit, generate a hardware tone, touch
  GPIO/I2C/DMA/PWM/GPCLK/Si5351, or change a service.
- Treat the copied archive as immutable read-only input.
- Keep raw IQ, WAVs, decoder products, inventories generated from machine-local
  paths, and replay bundles under ignored or temporary directories.
- Commit only portable schemas, source, tests, prompts, and documentation.
- Never infer missing runtime authorization, RF-path, binary, cleanup,
  quiescence, UTC, or source facts.
- Every normalized or composed result remains non-qualifying and uses
  `inconclusive` when lifecycle evidence is absent.

## Required work

1. Add a portable archive-inventory schema and implementation that consumes a
   canonical SHA-256 manifest using relative POSIX artifact names. Reject
   absolute paths, traversal, duplicates, malformed digests, symlinks,
   non-regular files, size/hash mismatch, incomplete artifacts, and output
   placement inside the source archive.
2. Emit an immutable inventory containing archive identity, manifest identity,
   entry path/size/hash, deterministic classification, reasons, and summary
   counts. Keep classification separate from qualification status.
3. Use a closed classification vocabulary that distinguishes complete regular
   artifacts, incomplete artifacts, generated/derived products, historical
   ad hoc evidence, repository snapshots, and unsupported entries. Do not
   attempt to certify lifecycle completeness from filenames.
4. Add a multi-capture CW-family session schema and semantic validator. It must
   bind at least three distinct repetition records, require a single mode and
   normalized plan identity, authenticate each capture/metadata/observation
   artifact, preserve per-repetition acquisition identity, reject duplicate or
   reordered repetition numbers, and force `qualification_claim: false` with
   final status `inconclusive` until a later lifecycle composer exists.
5. Do not concatenate captures or present separate acquisitions as one coherent
   capture. The composition layer records a session relationship above the
   individually authenticated inputs.
6. Add CLI commands to inventory an archive and validate a multi-capture
   session. CLI operation must be portable and transactional.
7. Exercise the retained WSPR capture through the maintained complex converter
   in an ignored/temporary location using the recorded slot boundary. Run the
   discovered `wsprd` only on generated WAVs, retain the local regression
   record outside Git, and compare intended-frequency decode multiplicity with
   the historical improvised output.
8. Add small synthetic regression tests proving intended-side translation,
   conjugate rejection/alias suppression, deterministic output, archive
   tamper rejection, path safety, and multi-capture binding semantics.
9. Update the development guide with verified outcomes, unavailable facts,
   remaining limitations, and the exact boundary before Phase 7.

## Non-goals

- No live Tone/CW supervisor or Phase 7/8 activity.
- No new RF capture, transmission, decoder claim, or hardware qualification.
- No wholesale Git import of archive paths, hashes, IQ, WAV, logs, repositories,
  or generated inventory documents.
- No weakening of the coherent WSPR three-frame contract.
- No claim that archive normalization establishes spectral compliance,
  calibrated power, cleanup, quiescence, or current host readiness.
- No branch creation/switching, history rewrite, force-push, pull request,
  release, issue mutation, or sibling-repository change.

## Validation and evidence

- Focused archive, multi-capture, audio, schema, CLI, and adversarial tests.
- Complete Python suite, Ruff format/lint, strict mypy, package build,
  hardware-disabled CMake/CTest, provenance verification, packaged-schema byte
  comparison, and `git diff --check`.
- Confirm `local/`, `runs/`, `dist/`, raw IQ, WAV, decoder products, and generated
  inventory output are absent from the staged candidate.
- Commit and push the reviewed slice on the current tracked branch, then require
  green macOS, Ubuntu, and native Windows CI for Python 3.11 and 3.13.

## Independent adversarial review

Attempt to prove that manifest text can escape the archive root, symlinks can
substitute content, a duplicate path can shadow an entry, classifications imply
qualification, separate captures can be relabeled coherent, one artifact can
satisfy multiple repetitions, reordered repetitions pass, missing lifecycle
facts become positive claims, expected metadata can replace measured evidence,
the WSPR regression selects a favorable image after decoding, or large/local
artifacts can enter Git. Treat every actionable finding as a blocker, repair it,
and repeat the assessment until clean. Append findings and closures here.

## Exit criteria

The slice is complete only when archive intake is deterministic and fail-closed;
multi-capture relationships are authenticated without coherence or qualification
overstatement; the retained WSPR capture has a reproducible maintained-path
regression outcome; all local and cross-platform gates pass; the independent
review has no unresolved material finding; the current branch is pushed; and
the tracked worktree is clean.

## Findings and completion evidence

Append-only during execution.

### Assessment 1

1. Resolving the archive root and manifest before checking their file types
   erased evidence that either input was itself a symlink. Closure: the original
   caller-supplied paths are rejected when symlinks before resolution, and every
   manifested entry is checked with `lstat`, root containment, and regular-file
   tests before hashing.
2. The first multi-capture validator draft prevented one capture from satisfying
   multiple repetitions but allowed one file to satisfy different roles.
   Closure: a global role-path set now rejects cross-role reuse, while per-role
   sets reject reuse across repetitions.
3. Artifact hashes alone did not prove that metadata retained the repetition's
   acquisition identity or that observations bound its capture. Closure: mode,
   normalized-plan digest, acquisition ID, metadata capture digest, and
   observation capture digest are all recomputed and cross-checked.
4. A successful whole-archive CLI invocation printed every inventory entry,
   producing excessive terminal output. Closure: the durable output retains all
   entries while stdout reports only archive ID, output path, non-qualifying
   state, and summary counts.

### Assessment 2 - retained WSPR regression

All three retained slots were translated through the maintained complex mixer
and windowed-sinc resampler. Each decoded the intended identity at 1500 Hz, but
each also retained the same symmetric detections near 1408, 1438, 1469, 1562,
and 1594 Hz. Tenfold and hundredfold amplitude reductions on the first slot did
not remove them. Closure: no converter change was made because the evidence
contradicts the proposed conversion-only cause. The multiplicity is retained as
a source/path-or-decoder regression blocker, every decode remains visible, and
no qualification claim is made.

### Final reassessment

Traversal, absolute and Windows-form paths, duplicate entries, empty manifests,
symlinks, content tampering, reordered repetitions, repeated acquisition IDs,
same-role and cross-role artifact reuse, and broken semantic bindings fail
closed. Classification cannot express qualification, multi-capture status is
fixed to `inconclusive`, and generated/archive material remains ignored or
temporary. No unresolved material finding remains before full validation.

### Local completion evidence

- The copied archive manifest authenticated all 12,194 entries totaling
  16,656,431,611 bytes. The non-qualifying inventory classified 10,951
  repository-snapshot entries, 1,153 historical ad-hoc evidence entries, 70
  generated derivatives, 19 incomplete artifacts, and one complete regular
  artifact. The generated inventory remained in `/private/tmp`.
- Focused archive, multi-capture, audio, schema, and CLI validation passed 33
  tests. The complete suite passed 823 tests in 235.42 seconds.
- Ruff formatting and lint, strict mypy over 44 source files, package build,
  hardware-disabled CMake build, both CTest cases, historical provenance
  verification, packaged-schema byte comparison, and diff checks all passed.
- Candidate-file inspection found no generated inventory, raw capture, local
  archive, run directory, distribution artifact, or file larger than 10 MiB in
  the tracked change set. Cross-platform CI remains the post-push gate.
