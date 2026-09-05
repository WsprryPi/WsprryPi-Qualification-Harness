# Simultaneous frequency-reference analysis

A continuous reference can share a capture with a keyed transmitter when their
channels are sufficiently separated. CW analyzer 13 measures strength and
continuity in the acquired transmitter channel. Clipping still uses raw IQ.
Quiet policy 2 uses a channel-referred noise floor and local spectral guards;
a concentrated feature in those guards makes silence evidence inconclusive rather
than hiding an unwanted transmitter signal. Historical analyzer versions keep
their original validation semantics.

This change applies to TONE, QRSS, FSKCW and DFCW analysis, including maintained
live paths and offline replay. It does not change WSPR decoding, transmitter
arguments, acquisition limits, the 10 dB contrast requirement, or hardware
ownership and cleanup. A nearby interfering carrier can still make acquisition
or quiet evidence unusable. A continuous reference is not permission to ignore
unexpected signals.

## Analyze a retained reference

The offline command accepts the native exact-count capture metadata, an explicit
IQ copy, and a request. The IQ size and SHA-256 must match the metadata even if
the copy has moved. The command does not open an SDR or control a GPSDO.

```text
python -m wsprrypi_qualification analyze-simultaneous-reference \
  capture-metadata.json capture.cf32 reference-request.json reference-report.json
python -m wsprrypi_qualification validate-simultaneous-reference reference-report.json
```

Example request (frequencies and uncertainty inputs are illustrative, not bench
facts or instrument specifications):

```json
{
  "signal_frequency_hz": 14097100,
  "reference_frequency_hz": 14107100,
  "channel_half_width_hz": 100,
  "window_seconds": 1,
  "minimum_contrast_db": 10,
  "maximum_reference_excursion_hz": 2,
  "reference_uncertainty_hz": 0.1,
  "transfer_uncertainty_hz": 0.2
}
```

Supply the known reference frequency and justified reference and transfer error
budgets. The latter covers the difference between the receiver error at the two
frequencies. A single reference cannot separately establish receiver tuning
error and sample-clock scale error. These inputs are not independently certified
by the Harness; do not use the example budgets as calibration evidence.

Channels and their noise guards must avoid DC and receiver edges, and their
centers must be more than eight channel half-widths apart. The half-width must
contain at least eight FFT bins. Windows are 0.1–10 seconds and at most 4,194,304
samples. Acquisition is confined to each requested channel; comparable peaks
separated by more than four FFT bins are ambiguous. Closer features are not
independently resolved by this method.

Each Hann-windowed FFT reports indicated frequency and integrated channel power
relative to unit complex CF32 amplitude (`power_dbfs`). This is not calibrated
dBm. The local signal/background ratio uses guard-band power. Both channels
use the same samples. The capture tail is included with an overlapping last
window if necessary; overlapping windows are not independent observations.
The reference also receives a 20 ms coherent-amplitude check (at least 16
samples), rejecting a drop below half the median power within a window. This is
an empirical dropout check at finite time resolution, not a guarantee that all
shorter disturbances are detected.

Every window must have a usable reference, and its observed frequency excursion
must meet the request limit. Otherwise all corrected estimates are withheld.
A missing transmitter in an otherwise valid window produces no corrected
transmitter estimate; the reference cannot substitute for it. This diagnostic
command does not establish the expected transmission schedule: use the CW
analyzer for missing, extra or mistimed transmitter activity.

For usable windows the local additive model is:

```text
receiver_error = indicated_reference - known_reference
corrected_signal = indicated_signal - receiver_error
frequency_error_budget = reference_uncertainty + transfer_uncertainty + 2 FFT bins
```

The budget is a conservative allocation under the stated model, not a calibrated
confidence interval or proof of absolute accuracy. Narrowband interference,
unmodeled reference error and receiver nonlinearity can violate its assumptions.
The original indicated frequencies remain in the report. Frozen receiver
calibration profiles are not applied or combined here, and no transmitter PPM
or qualification result is modified.

Outputs explicitly set `qualification_claim: false`. `usable_diagnostic` exits
0; inconclusive measurements exit 4; invalid inputs or output conflicts exit 2.
The validator authenticates all three inputs and recomputes the complete report.
Retain those inputs at the report's recorded paths. Do not hand-edit an old
report to point at relocated evidence; generate a new report from explicit
copies instead. Output files are never overwritten.

## Replaying copied CW evidence

```text
python -m wsprrypi_qualification compose-cw-acquired-replay \
  tone-plan.json tone-expected-events.json tone-acquired-capture.json NEW_BUNDLE \
  --capture-copy copied-rf-on.cf32 --source-revision ANALYZER_SOURCE_COMMIT
```

`--capture-copy` explicitly selects an IQ copy authenticated against the retained
metadata's size and hash. The composer creates fresh relative bindings inside
the new replay bundle. Original results remain untouched, and successful replay
still cannot qualify physical hardware or current runtime operation.
