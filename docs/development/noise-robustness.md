# Noise robustness and measurement limits

CW IQ analyzer **8** and carrier-analysis schema **3** introduce independent
carrier-presence, timing, and interference checks. They do not relax a plan's
timing, frequency, spacing, drift, transition, contrast, decoding, clipping,
overflow, or lifecycle requirements. Campaign timing tolerance remains 150 ms.
All results remain specific to their capture and authenticated configuration.

## Mode routing

| Mode | Measurement path | Noise treatment |
|---|---|---|
| TONE | Carrier FFT gate plus CW cadence analysis | Short-window local spectral contrast in authenticated ON interiors; separate cadence and quiet checks can prevent progression without rewriting FFT frequency metrics |
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
and classification. Each quiet interval retains occupancy. Four-sample
carrier-like bursts can fail silence even when shorter than the 10 ms state
confirmation. Strong unresolved impulses and ambiguous broadband contamination
remain inconclusive. Near-noise subresolution excursions cannot define an edge.

Coherent in-channel energy establishes a silence violation, not independent
proof of which physical source emitted it. Co-channel interference and very
short events can remain unidentifiable. Sustained or repeated extra carrier
activity must never disappear into an adaptive background estimate.

## Carrier gate and TONE cadence

The original Hann FFT, RF-on-minus-RF-off spectra, frequency tolerance, and
10 dB RF-on/off requirement remain. A separate projection guard evaluates
short windows (up to 20 ms) against symmetric local reference channels. A noise
impulse cannot qualify solely by dominating the averaged FFT. Comparable
separated in-window features or insufficient local temporal contrast make the
guard inconclusive. Stronger remote features remain diagnostic.

Continuous-carrier input covers the complete retained capture, including its
FFT tail. TONE cadence uses `analyze-carrier --cw-mode-plan PLAN
--cw-expected-events EVENTS`; both inputs are required together, authenticated,
and checked against capture count, rate, center, and requested frequency. The
guard checks every expected ON interior, excluding only the plan's existing
timing tolerance at its boundaries. The separate full cadence analyzer checks
edges, gaps, silence, and capture tail. A cadence failure now prevents campaign
progression; this strengthens the historical diagnostic-only TONE behavior.
FFT evidence is retained unchanged and `mode_gate` remains `not_applicable`.

The extra RF-on projection read is included explicitly in the TONE analysis
workload bound. No timeout reserve or RF-duration allowance is introduced.

## Evidence compatibility

Source and packaged schemas must match byte-for-byte. Version-8 CW observations
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
