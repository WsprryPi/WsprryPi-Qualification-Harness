# Noise robustness and measurement limits

CW IQ analyzer **10** and carrier-analysis schema **3** introduce independent
carrier-presence, timing, and interference checks. They do not relax a plan's
timing, frequency, spacing, drift, transition, contrast, decoding, clipping,
overflow, or lifecycle requirements. Campaign timing tolerance remains 150 ms.
All results remain specific to their capture and authenticated configuration.

## Mode routing

| Mode | Measurement path | Noise treatment |
|---|---|---|
| TONE | Carrier FFT gate plus CW cadence analysis | Short-window local spectral contrast in authenticated ON interiors; separate cadence and quiet checks can prevent TONE qualification without rewriting FFT frequency metrics |
| WSPR | Carrier FFT gate, existing audio conversion, independent `wsprd` invocations | Continuous-carrier temporal guard before decoding; modulation, WAV normalization, decoder arguments, identity and consecutive-decode requirements remain unchanged |
| QRSS/CW | CW IQ analyzer | Carrier-channel envelope, confirmed edges, independent raw-IQ quiet assessment |
| FSKCW | CW IQ analyzer and shifted frequency model | Common channel preserves both states; existing phase-based state classification and independent spacing/drift model remain |
| DFCW | CW IQ analyzer and shifted frequency model | Carrier-channel keyed envelope and independent frequency-coded symbol/spacing checks |

No decoder-success, power-calibration, spectral-compliance, or hardware claim
follows from synthetic, mock, replay, or hosted-CI results.

## CW detector

`noise.py` contains a versioned specification whose canonical SHA-256 and full
settings are retained in generated observations. Production analyzer source is
bound by the existing resolved-plan/deployment identities. There is no per-run
parameter fitting or user-facing tolerance override for this detector.

Acquisition sums Hann-windowed 0.5-second spectra inside the configured search
window around each commanded state. Widely separated shifted states use the
union of those windows, not an unrelated window between the tones.
Comparable separated candidates make acquisition inconclusive. A peak
alone cannot pass: temporal presence and the independent frequency and message
checks still apply. Frequency is not snapped to the requested value or spacing.

The acquired carrier is translated to baseband and passed through three centered
odd boxcars, each approximately 2 ms long. This compact FIR uses linear-time
cumulative sums, with no new dependency. Its first null is approximately 500 Hz
at normal capture rates; this is a timing channel, not a high-resolution spectrum
or a claim of ideal rejection of nearby interferers. The boxcar is shortened
when necessary to preserve larger commanded frequency separations. Very low
sample rates use the recorded discrete filter width, including width one.

The candidate reference is the plan's RF-off preamble, guarded at each end by at
least 20 ms. It requires at least 50 ms and four samples. A median power divided
by ln(2) supplies a robust, Gaussian-mean-equivalent reference; under non-Gaussian
noise it is only a documented scale estimator, not a calibrated noise power.
Consequently version-8 broadband contrast has a different reference estimator
from version 7's uncorrected median. The numeric contrast requirement is not
reduced. Channel-envelope thresholds and retained broadband contrast have
separate meanings.

Coherent peaks in the effective reference channel, excessive variation between
reference quarters, zero power, and insufficient reference data prevent a pass.
Reference peak frequency, peak/median ratio, search width, selected intervals,
and channel/raw noise estimates are retained. A feature elsewhere in the full
SDR span is not automatically a contaminated carrier-channel reference.

ON uses the plan's contrast ratio against channel noise; OFF uses half that
power threshold. Both transitions require 10 ms or four samples, whichever is
longer. Evidence records the onset and confirmation separately. Confirmation
backdates to the first threshold crossing after the last sustained old state,
not the last quiet subrun. The observed interval between leaving the last
sustained old threshold state and entering the confirmed new state is added
to the uncertainty budget. This includes both hysteresis-band transit and
excess confirmation wait. Centered filtering has no additional causal
timestamp delay to subtract, but its complete support remains an uncertainty.

RF-edge support plus one sample starts at approximately 3.004 ms at 250 ksps,
before adding the observed threshold-transition interval. The
reported analyzer resolution is never less than four samples. For shifted
modes it additionally includes the support of the existing phase-product
average: at the usual 100 ms classification window the conservative combined
budget starts at approximately 53 ms, also before that interval.
It is not valid to claim four-sample accuracy
for these filtered frequency transitions. A boundary within that budget of
the existing timing limit is inconclusive, not a relaxed pass. Known unsupported
filter/reference geometry is rejected during live preflight; excessive
capture-dependent confirmation uncertainty produces inconclusive evidence.
These are empirical engineering budgets for the validated signal/noise range,
not distribution-independent statistical confidence bounds.

Complete observed runs are found before event association; the search window
cannot truncate an erroneous edge back toward tolerance. Bounded common
latency remains separate from residual event timing. Quiet boundaries belong
to adjacent message events rather than the longest uninterrupted quiet run.

## Independent contamination evidence

Raw IQ is retained. A separate short-window detector inspects every quiet
interval, including the additional capture tail after the reference timeline.
Local raw quiet boundaries prevent the channel filter's support from blanking
short bursts next to a confirmed edge. This refinement affects transient
inspection, not the carrier timing observation.

Each credible burst retains start/end, duration, peak relative power,
integrated relative power, phase-derived frequency where resolvable, coherence,
and classification. No detected event is removed by the significance policy.
Frequency and coherence estimated from very short events remain diagnostics,
not sufficient evidence of an operational silence violation.

Analyzer 9 adds `slow_cw_quiet_significance` version 1 for keyed modes. An
individual event is material at one percent of the commanded dot duration,
capped at 10 ms and bounded below by four samples. At a 0.7-second dot and
250 ksps, this is 7 ms. Shorter events retain `diagnostic_only` qualification
effect. Material carrier-like events fail silence; material unresolved events
make it inconclusive. Analyzer 10 applies the same significance rules to TONE OFF intervals, using
`tone_quiet_significance` version 1 and the commanded `tone_on_seconds`
in place of dot duration. For two-second ON intervals, individual bursts become
material at 10 ms; sliding occupancy uses two-second windows (or the complete
quiet interval if shorter). Every event remains recorded. TONE ON interiors,
cadence timing, and WSPR continuous-carrier checks remain unchanged.

The policy also checks accumulated retained-event occupancy in every sliding
window of one dot duration, shortening the window only when the entire quiet
interval is shorter. Occupancy of one percent or more is inconclusive unless
there is already a material carrier-like silence violation. The sliding check
prevents repeated short bursts from escaping at fixed-bin boundaries or being
diluted by a long capture tail. Individually uncertain frequency estimates do
not become proof of carrier identity merely through accumulation.

Every significance-assessed quiet record binds the policy parameters and SHA-256, per-event
qualification effect, peak rolling occupancy and its interval, and count of
material events. Semantic validation regenerates the assessment from retained
events; full replay also regenerates events from IQ. These are explicit
engineering significance thresholds, not calibrated interference tolerance,
CFAR probabilities, or proof of which source emitted the activity. Changes
require a new policy identity/analyzer version and fresh qualification evidence.


## Carrier gate and TONE cadence

The original Hann FFT, RF-on-minus-RF-off spectra, frequency tolerance, and
10 dB RF-on/off requirement remain. A separate projection guard evaluates
short windows (up to 20 ms) against symmetric local reference channels. A noise
impulse cannot qualify solely by dominating the averaged FFT. Comparable
separated in-window features or insufficient local temporal contrast make the
guard inconclusive. Stronger remote features remain diagnostic.

Strict continuous-carrier analysis covers the complete retained capture,
including its FFT tail. Live WSPR carrier acquisition starts the receiver before
the transmitter, so its analyzer explicitly supplies
`--startup-acquisition-max-s 1.1`. Generic analysis defaults to zero (strict).
The version-2 temporal guard requires at least 100 ms of consecutive passing
windows to acquire within that 1.10-second completion bound: one second for
carrier onset plus 100 ms for confirmation. The bound is not an onset deadline.
Historical 1.00-second bounds retain their original completion-deadline semantics.
The guard chooses the earliest such run, requires at least one second of
retained steady evidence, and checks every
window from that onset through the capture tail. It never reacquires after a
dropout or selects a later convenient segment. Missing or late acquisition,
insufficient evidence, and subsequent contrast failures remain inconclusive.
The bound does not establish transmitter startup performance or RF identity.

Evidence retains the startup policy, bound, confirmation duration, excluded
window counts (including below-contrast counts), and steady-window metrics.
Replay recomputes these fields from the authenticated IQ. Existing strict
results retain their original behavior; historical evidence is never rewritten.
This prefix acquisition does not apply to TONE cadence or keyed modes.
 TONE cadence uses `analyze-carrier --cw-mode-plan PLAN
--cw-expected-events EVENTS`; both inputs are required together, authenticated,
and checked against capture count, rate, center, and requested frequency. The
guard checks every expected ON interior after the cadence detector's bounded
common-latency alignment, excluding only the plan's existing timing tolerance
at its boundaries. Individual pulses are never independently realigned.
Unsupported alignment, contaminated references, or excessive edge uncertainty
stop analysis. Aligned intervals and `bounded_common_latency_v1` are retained;
older unaligned TONE evidence requires its original analyzer. The separate full cadence analyzer checks
edges, gaps, silence, and capture tail. A cadence failure prevents TONE qualification but does not prevent subsequent
modes from running after verified cleanup. Each mode has an independent result.
FFT evidence is retained unchanged and `mode_gate` remains `not_applicable`.

The extra RF-on projection and alignment reads are included explicitly in the TONE analysis
workload bound. No timeout reserve or RF-duration allowance is introduced.

## Evidence compatibility

Source and packaged schemas must match byte-for-byte. Version-8, version-9, and version-10 CW observations
require the detector specification and quiet-window records. Semantic validation
checks specification identity and timing budgets; replay recomputation compares
the complete detector evidence, not only pass/fail fields. Carrier schema 3
requires temporal guard evidence and its policy, including authenticated TONE
references when applicable. The live stage retains separate temporal/cadence
decisions so a passing FFT result cannot hide an unsuccessful check.

Older observation schemas remain recognizable. Recomputing a historical CW
replay or acquired carrier document with a different analyzer is rejected with
an instruction to use the original analyzer or compose new evidence. Original
observations and manifests are never rewritten. A new replay is always
non-qualifying and does not supersede live authorization or cleanup evidence.

## Validation and remaining limits

`tests/unit/test_noise.py` uses independent oscillator/envelope construction,
multiple sample rates and seeds, held-out noise realizations, weak/fading and
drifting carriers, contaminated references, impulses, extra short pulses,
capture-tail pulses, boundary-adjacent bursts, missing/stuck output, wrong
spacing, cadence routing, and tampered timing budgets. Existing CW, carrier,
audio, decoder, replay, lifecycle, calibration, and result tests remain required.
Native Windows and macOS CI include the new noise suite.

Acceptance targets are zero invented confirmed edges in the enumerated
noise-only cases, no false passes in the enumerated defect cases, unchanged
qualification tolerances, and edge errors within the declared budget for the
tested clean/weak/fading signals. These are finite empirical tests, not a CFAR
probability guarantee. Filtering correlates samples, and impulsive SDR noise
does not justify a Gaussian false-alarm formula without further validation.

Retained captures must be tested separately from parameter-development cases.
Available captures do not establish performance for other devices, bands,
gain settings, noise distributions, or RF paths. In particular, synthetic audio
and fake decoder tests do not establish real WSPR decoding sensitivity. A
licensed/redistributable independently encoded noisy WSPR corpus and broader
receiver captures remain necessary for that claim. Frozen settings require a
new analyzer/specification version if changed after validation; fresh live
qualification requires separate exact authorization.

Background references: [SciPy FIR design](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.firwin.html),
[CFAR assumptions](https://www.mathworks.com/help/radar/ug/constant-false-alarm-rate-cfar-detection.html),
and [GNU Radio hysteresis](https://wiki.gnuradio.org/index.php/Threshold).
These explain mechanisms, not validated settings for this harness.

Authenticated inconclusive carrier evidence can publish a bounded inconclusive
session only with retained RF-off/RF-on evidence and verified cleanup and
quiescence. It never advances to frames; cleanup failure retains precedence.
