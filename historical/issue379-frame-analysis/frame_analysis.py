#!/usr/bin/env python3
"""Recover and validate the conducted Issue 379 Si5351 WSPR frame."""

from pathlib import Path
import json
import numpy as np

FS = 250_000.0
BLOCK = 250
RATE = FS / BLOCK
SYMBOL_S = 8192.0 / 12000.0
CENTER_HZ = 144_515_500.0
NOMINAL_HZ = 144_490_500.0
ROOT = Path("/home/pi/issue379-si5351-frame-valid")


def main():
    iq = np.memmap(ROOT / "si5351-2m-frame-valid.cf32", dtype=np.complex64, mode="r")
    count = len(iq) // BLOCK
    starts = np.arange(count, dtype=np.float64) * BLOCK
    offset = NOMINAL_HZ - CENTER_HZ
    intra = np.exp(-2j * np.pi * offset * np.arange(BLOCK) / FS)
    z = np.empty(count, dtype=np.complex128)
    for first in range(0, count, 5000):
        last = min(count, first + 5000)
        blocks = np.asarray(iq[first*BLOCK:last*BLOCK]).reshape(-1, BLOCK)
        phase0 = np.exp(-2j * np.pi * offset * starts[first:last] / FS)
        z[first:last] = (blocks @ intra) / BLOCK * phase0

    amp = np.abs(z)
    smooth = np.convolve(amp, np.ones(20)/20, mode="same")
    floor = np.median(smooth[200:1000])
    signal = np.median(smooth[3000:100000])
    threshold = np.sqrt(floor * signal)
    candidates = np.where((np.arange(count) > 500) & (np.arange(count) < 5000) & (smooth > threshold))[0]
    onset_idx = None
    for k in candidates:
        if np.count_nonzero(smooth[k:k+50] > threshold) >= 45:
            onset_idx = k
            break
    if onset_idx is None:
        raise RuntimeError("No sustained frame onset found")
    amplitude_onset = onset_idx / RATE
    expected=json.loads((ROOT/'reference-symbols.json').read_text())['symbols']
    expected_values=np.array([int(x) for x in expected],dtype=float)

    # Synchronize to the measured symbol grid.  A cumulative one-sample phase
    # correlation makes each candidate inexpensive and avoids phase-unwrapping
    # ambiguity across the intentional retunes.
    corr=np.conj(z[:-1])*z[1:]
    prefix=np.concatenate(([0j],np.cumsum(corr)))
    best=None
    symbol_times=np.arange(162,dtype=float)
    centered=(symbol_times-symbol_times.mean())/np.ptp(symbol_times)
    for onset in np.arange(amplitude_onset-0.45,amplitude_onset+0.451,0.001):
        f=[]
        for symbol in range(162):
            lo=int((onset+(symbol+0.20)*SYMBOL_S)*RATE)
            hi=int((onset+(symbol+0.80)*SYMBOL_S)*RATE)
            c=prefix[hi-1]-prefix[lo]
            f.append(np.angle(c)*RATE/(2*np.pi))
        f=np.asarray(f)
        design=np.column_stack((np.ones(162),centered,centered**2,centered**3,expected_values))
        coeff=np.linalg.lstsq(design,f,rcond=None)[0]
        rms=float(np.sqrt(np.mean((f-design@coeff)**2)))
        if best is None or rms<best[0]: best=(rms,onset,f)
    _,onset,frequencies=best

    fit_rms=[]
    for symbol in range(162):
        begin=onset+(symbol+0.12)*SYMBOL_S
        end=onset+(symbol+0.88)*SYMBOL_S
        lo,hi=int(begin*RATE),int(end*RATE)
        t=np.arange(lo,hi)/RATE
        phase=np.unwrap(np.angle(z[lo:hi]))
        slope,intercept=np.polyfit(t-t.mean(),phase,1)
        residual=phase-(slope*(t-t.mean())+intercept)
        fit_rms.append(np.sqrt(np.mean(residual**2)))

    design=np.column_stack((np.ones(162),centered,centered**2,centered**3,expected_values))
    coeff=np.linalg.lstsq(design,frequencies,rcond=None)[0]
    slow=design[:,:4]@coeff[:4]
    spacing=coeff[4]
    tone_component=frequencies-slow
    recovered=np.clip(np.rint(tone_component/spacing),0,3).astype(int)
    centers=np.arange(4)*spacing
    symbols=''.join(str(int(x)) for x in recovered)
    mismatches=[i for i,(a,b) in enumerate(zip(symbols,expected)) if a!=b]

    summary={
      "amplitude_threshold_onset_after_capture_s":float(amplitude_onset),
      "synchronized_frame_onset_after_capture_s":float(onset),
      "symbol_duration_s":SYMBOL_S,
      "symbol_count":len(symbols),
      "recovered_symbols":symbols,
      "expected_symbols":expected,
      "symbol_mismatch_count":len(mismatches),
      "symbol_mismatch_indices":mismatches,
      "tone_residual_centers_hz":[float(x) for x in centers],
      "tone_spacing_hz":[float(x) for x in np.diff(centers)],
      "mean_tone_spacing_hz":float(np.mean(np.diff(centers))),
      "ideal_tone_spacing_hz":12000.0/8192.0,
      "tone_spacing_max_error_hz":float(np.max(abs(np.diff(centers)-12000.0/8192.0))),
      "slow_drift_start_to_end_hz":float(slow[-1]-slow[0]),
      "symbol_frequency_model_rms_hz":float(np.sqrt(np.mean((frequencies-design@coeff)**2))),
      "median_symbol_phase_fit_rms_rad":float(np.median(fit_rms)),
      "amplitude_floor_db":float(20*np.log10(floor)),
      "amplitude_signal_db":float(20*np.log10(signal)),
    }
    (ROOT/'frame-analysis.json').write_text(json.dumps(summary,indent=2)+'\n')
    (ROOT/'recovered-symbols.txt').write_text(symbols+'\n')
    print(json.dumps(summary,indent=2))

if __name__ == '__main__': main()
