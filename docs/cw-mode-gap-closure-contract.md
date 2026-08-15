# Tone and CW-family qualification gap-closure contract

## 1. Status and purpose

This document is the implementation contract for closing the qualification gaps
for `tone`, `cw`, `qrss`, `fskcw`, and `dfcw`. Its numbered phases are stable
references for planning and review. Completing an earlier phase does not
authorize or imply advancement into a later phase.

This document records planned work. It does not claim that raw-IQ mode analysis,
live mode orchestration, or new hardware qualification is implemented. The
repository contract and live-RF authorization requirements remain governing.

## 2. First-class modes

The harness shall retain five distinct first-class capabilities:

- `tone`: a bounded, unkeyed continuous carrier. This is the simplest test and
  is used for carrier placement, contrast, stability, startup, stopping, and
  quiescence. It is not CW and does not imply keyed-mode correctness.
- `cw`: ordinary on-off-keyed Morse at a declared dot duration.
- `qrss`: on-off-keyed slow Morse at a declared QRSS dot duration.
- `fskcw`: continuous RF shifted between declared mark and space frequencies.
- `dfcw`: continuous RF shifted between declared dot and dash frequencies using
  WsprryPi's versioned DFCW timing semantics.

Evidence for one mode shall never qualify another. In particular, a passing
`tone` result is only a prerequisite for the keyed modes; it cannot qualify
`cw`, `qrss`, `fskcw`, or `dfcw`.

Hellschreiber and RP1 remain outside this contract. Initial production backends
are GPIO and Si5351.

## 3. Qualification unit

Every qualification claim is specific to the recorded combination of:

- mode and all mode parameters;
- WsprryPi parent and transmitter-component revisions;
- backend, transmitter hardware profile, output, drive, and clock/reference;
- band and requested frequency or frequencies;
- message, dot duration, and versioned protocol semantics;
- receiver, capture helper, sample format/rate/bandwidth, gain, and calibration;
- RF path and its attenuation, filtering, termination, and safe-input basis;
- analyzer revision, configuration, resolution, and thresholds; and
- live-session authorization, run time, cleanup, and quiescence result.

A result shall not be generalized to another mode, backend, band, board,
frequency plan, source revision, receiver, RF path, or production setting.

## 4. Measurement and evidence invariants

Qualification-grade captures shall record interleaved CF32 format, actual sample
rate and center frequency, exact requested and acquired sample counts, overflow
count, first-read-discard behavior, fixed gain, disabled AGC and bias tee,
receiver identity, tool versions, calibration, clipping metrics, RF-path facts,
and the capture SHA-256.

The mode capture shall include a pre-transmission quiet interval, the complete
bounded test sequence, a post-transmission quiet interval, and adequate margins
for startup and stopping analysis. RF-off and RF-on evidence shall use unchanged
receiver settings.

Thresholds shall be part of the resolved plan before transmission. They shall
not be selected after examining the capture and shall not be tighter than the
documented time or frequency resolution of the analyzer. Analyzer evidence
shall record its window, transform and estimator parameters, time and frequency
resolution, and measurement uncertainty.

If receiver coverage, overload, clipping, clock loss, capture failure, or the RF
path prevents a trustworthy judgment, the result shall be `fixture_blocked` or
`inconclusive`, not transmitter unqualification.

Qualification is functional only. It does not establish calibrated output
power, harmonic suppression, spectral-mask compliance, antenna readiness, or
legal authority to transmit.

## 5. Expected-event contract

Before transmission, the harness shall generate an immutable expected-event
timeline from the resolved mode plan. Each event shall record:

- sequence number and message position;
- symbol and semantic role;
- expected start and end relative to the transmission epoch;
- expected RF state and frequency;
- permitted frequency, duration, and boundary error; and
- whether RF continuity is required across the transition.

A small independently tested reference encoder shall generate this timeline
from the versioned protocol rules. A transmitter execution trace may be retained
for diagnosis but shall not supply the expected answer.

## 6. Mode gates

### 6.1 Tone

The qualification sequence shall contain at least three bounded off-on-off
cycles. Every cycle shall demonstrate:

- acquisition at the requested carrier frequency;
- the required RF-on versus RF-off contrast;
- continuous carrier during the commanded on interval;
- onset and cessation within tolerance;
- no persistent output during the commanded off intervals; and
- verified final backend-specific quiescence.

Tone has no message-decode gate. It remains independently useful even when no
keyed-mode test is requested.

### 6.2 CW

The fixed test message shall exercise dots, dashes, intra-character gaps,
inter-character gaps, and preferably an inter-word gap. Qualification requires:

- correct carrier placement during every key-down interval;
- adequate key-down versus RF-off contrast;
- silence during every key-up interval;
- correct dot, dash, and gap timing;
- correctly ordered transitions;
- an independently reconstructed Morse sequence matching the expected message;
  and
- at least three consecutive complete correct repetitions.

### 6.3 QRSS

QRSS shall meet the same observable on-off-keying requirements as CW, but shall
use its separately declared slow dot duration, capture bounds, analysis
resolution, and tolerances. A CW result shall not be relabeled as QRSS, and a
QRSS result at one dot duration shall not qualify another dot duration.

Qualification requires at least three consecutive complete correct message
repetitions reconstructed independently from IQ.

### 6.4 FSKCW

The fixed test message shall exercise both keyed states and repeated transitions
in both directions. Qualification requires:

- mark and space frequencies within tolerance;
- correct mark-above-space ordering;
- correct tone spacing;
- continuous RF across every transition for which continuity is required;
- no unintended third state or prolonged loss of carrier;
- correct symbol and gap timing;
- an independently reconstructed message matching the expected message; and
- at least three consecutive complete correct repetitions.

An interruption during a required continuous transition fails the mode gate
even if both tones were observed elsewhere.

### 6.5 DFCW

The fixed test message shall contain dots and dashes, transitions in both
directions, character boundaries, and a sequence that distinguishes DFCW timing
from FSKCW. Qualification requires:

- dot and dash frequencies within tolerance;
- distinct and correctly assigned tones;
- correct tone spacing;
- continuous RF where the versioned WsprryPi DFCW definition requires it;
- correct compressed-symbol and character-gap timing;
- an independently reconstructed DFCW message matching the expected message;
  and
- at least three consecutive complete correct repetitions.

The analyzer shall use a versioned WsprryPi protocol contract rather than infer
DFCW semantics from historical captures.

## 7. Gate and result semantics

Each session shall keep at least these separate gates:

- `carrier_gate`: whether the transmitter and receiver path produced a usable
  carrier or required carriers;
- `mode_gate`: whether the complete bounded mode sequence, transitions, timing,
  and reconstructed message passed; `not_applicable` for tone; and
- `cleanup_gate`: whether owned processes stopped and backend-specific
  quiescence was verified.

The final status shall use the governing repository vocabulary:

- `unqualified_carrier`: transmitter-attributable carrier absence, placement,
  contrast, or continuity failure at the carrier prerequisite;
- `unqualified_decode`: keyed-mode sequence, state, message, spacing, timing, or
  transition failure after a passing carrier prerequisite;
- `fixture_blocked`: receiver, coverage, overload, clock, capture, or RF-path
  blockage prevents judgment;
- `preflight_failed`: a required capability, identity, dependency, ownership, or
  safety preflight fails;
- `aborted`: operator or external cancellation;
- `cleanup_failed`: cleanup or quiescence cannot be verified;
- `inconclusive`: evidence is incomplete, synthetic, contradictory, or not
  qualification-grade; or
- `qualified`: every applicable measurement and lifecycle gate passes.

For tone, failure of the repeated bounded on/off behavior after carrier
acquisition maps to `unqualified_carrier`; it shall not be described as a decode
failure. Cleanup failure overrides every otherwise passing outcome.

## 8. Qualification authority

The standalone offline analyzer shall emit a measurement gate of `passed`,
`failed`, `blocked`, or `inconclusive`, detailed generated observations, and
`qualification_claim: false`. It cannot establish live authorization, process
ownership, or cleanup and therefore shall not issue an overall hardware
qualification.

Only the final live-session composer may emit `qualified` with
`qualification_claim: true`, and only when:

- the capture is acquired rather than synthetic;
- provenance, metadata, and artifact authentication pass;
- every applicable carrier and mode gate passes;
- all required consecutive repetitions pass;
- the live lifecycle is bound to the same plan and capture;
- no required evidence is missing or contradictory; and
- cleanup and backend-specific quiescence pass.

## 9. Required evidence bundle

Every run directory shall be new and immutable and contain, as applicable:

- requested and resolved plans;
- resolved-plan digest and runtime authorization;
- the expected-event timeline;
- source, tool, OS, receiver, and transmitter identities;
- RF-path, capture, and calibration facts;
- RF-off and mode-capture IQ, or authenticated relocation records;
- complete capture and transmitter logs;
- analyzer-generated observations and uncertainty;
- event-by-event comparisons and reconstructed symbols/messages;
- time-frequency, state, and transition plots;
- carrier, mode, cleanup, and quiescence gate documents;
- transmitter stopping evidence;
- final `result.json`; and
- a canonical SHA-256 manifest covering retained artifacts.

Generated observations shall identify the analyzer revision and bind every input
artifact by path, size, and SHA-256. Manually supplied observations may be
retained only as non-authoritative supplemental material.

## 10. Numbered implementation phases

### Phase 1 - Contract and schema alignment

**Status:** Implemented and locally validated on 2026-08-15. This status covers
only the non-qualifying contract layer; Phase 2 and all live/hardware work remain
unfinished and separately gated.

- Add `cw` without demoting or aliasing away `tone`.
- Define mode-specific plans and expected-event documents.
- Replace manually authoritative observations with generated-observation,
  mode-gate, and final-session contracts.
- Align CW-family final statuses with `CONTRACT.md`.
- Preserve current evidence as historical or version-1 input; do not silently
  reinterpret it as qualification-grade evidence.

**Exit gate:** schemas and semantic validation reject mode confusion,
contradictory claims, post-selected thresholds, and unsupported positive claims.

### Phase 2 - Independent reference encoders

**Status:** Implemented and locally validated on 2026-08-15. This status covers
only portable expected-event generation. Pushed cross-platform CI remains the
exit confirmation; Phase 3 IQ fixture/analyzer work and all hardware activity
remain unfinished and separately gated.

- Implement pure, portable encoders for CW, QRSS, FSKCW, and DFCW.
- Generate exact expected events from normalized plans.
- Keep tone as the simpler off-on-off state-plan generator.
- Bind DFCW behavior to a reviewed, versioned WsprryPi protocol definition.

**Exit gate:** golden and adversarial tests prove exact symbol, state, frequency,
and timing plans on macOS, Ubuntu, and native Windows CI.

### Phase 3 - Synthetic raw-IQ analyzer

- Generate deterministic tone and mode fixtures.
- Derive carrier states, frequencies, contrast, transitions, timing, tone
  spacing, and reconstructed messages directly from IQ.
- Record resolution and uncertainty and reject unresolvable threshold plans.
- Keep every synthetic result non-qualifying.

**Exit gate:** the analyzer passes known-good fixtures and correctly classifies
wrong frequency, swapped tones, timing drift, missing symbols, interrupted
carrier, false silence, unexpected states, clipping, conjugate images, short
reads, overflow, truncation, and artifact tampering.

### Phase 4 - Acquired-IQ replay and evidence composition

- Analyze retained acquired captures without creating new RF activity.
- Bind generated observations to authenticated input artifacts.
- Compose carrier, mode, evidence-index, result, and manifest documents.
- Classify captures lacking required events or lifecycle evidence as
  `inconclusive`, not qualified.

**Exit gate:** independently reviewed replay bundles are deterministic,
schema-valid, fully manifested, and contain no unsupported hardware claim.

### Phase 5 - Mock bounded lifecycle

- Add per-mode start, capture, stop, cancellation, timeout, and cleanup flows
  using only reviewed mock/local-process adapters.
- Exercise process ownership, deadline, service-restoration, leak, and
  quiescence evidence.
- Prove that a passing mode analysis cannot override cleanup failure.

**Exit gate:** failure injection covers every lifecycle boundary with no leaked
owned process and with cleanup precedence preserved.

### Phase 6 - Read-only actual-host preflight

- Under separate explicit authorization, validate exact tools, revisions, host
  identity, receiver capability, ownership, clock/reference facts, backend
  inspection, and RF-path inputs.
- Perform no transmission and make no qualification claim.

**Exit gate:** an immutable preflight bundle identifies one exact candidate
combination as ready or gives a specific fail-closed reason.

### Phase 7 - Bounded live tone validation

- Under separate explicit RF authorization, run tone first as the known control.
- Exercise the production transmitter, receiver, capture, analysis, stopping,
  cleanup, and quiescence path with strict time bounds.
- Do not advance if tone or cleanup fails; classify path blockage separately
  from transmitter failure.

**Exit gate:** an independently reviewed tone bundle validates the complete live
path for the exact backend, hardware, band, receiver, and RF path. This does not
qualify a keyed mode.

### Phase 8 - Bounded live keyed-mode validation

- Qualify CW, QRSS, FSKCW, and DFCW independently, one exact combination at a
  time, only after a passing unchanged tone control.
- Require all mode-specific repetitions, analysis, cleanup, and evidence gates.
- Repeat the tone control before a candidate whenever the RF path, receiver,
  backend, hardware, settings, or session context changes.

**Exit gate:** each claimed mode has its own independently reviewed immutable
bundle; untested combinations remain unqualified.

## 11. Overall completion standard

The gap is closed only when no qualification observation is manually
authoritative; raw IQ deterministically produces the mode observations and
reconstructed message; synthetic and replay runs cannot claim hardware
qualification; failure injection proves bounded ownership and cleanup; the
final composer binds the exact plan, lifecycle, capture, analysis, and cleanup;
portable offline behavior is recorded on macOS, Linux/Raspberry Pi OS, and
native Windows; and an explicitly authorized live run has passed for every
exact mode and hardware combination actually claimed.

Until Phase 8 passes for a particular combination, that keyed-mode combination
remains `inconclusive` or untested even if every offline software gate passes.
