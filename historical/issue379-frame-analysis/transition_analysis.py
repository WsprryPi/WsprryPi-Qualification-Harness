#!/usr/bin/env python3
"""Measure RF-envelope continuity across Si5351 WSPR tone changes."""

from pathlib import Path
import json
import numpy as np

FS=250_000.0
BIN=25                     # 100 microseconds
ROOT=Path('/home/pi/issue379-si5351-frame-valid')
ONSET=4.608000000000124
SYMBOL=8192.0/12000.0

iq=np.memmap(ROOT/'si5351-2m-frame-valid.cf32',dtype=np.complex64,mode='r')
symbols=json.load(open(ROOT/'reference-symbols.json'))['symbols']
amp=np.abs(iq[:(len(iq)//BIN)*BIN]).reshape(-1,BIN).mean(axis=1)
rate=FS/BIN

interior=[]
for i in range(162):
    lo=int((ONSET+(i+0.25)*SYMBOL)*rate)
    hi=int((ONSET+(i+0.75)*SYMBOL)*rate)
    interior.extend(amp[lo:hi])
baseline=float(np.median(interior))

mins=[]
max_below_6db=0
for i in range(1,162):
    if symbols[i]==symbols[i-1]: continue
    center=int((ONSET+i*SYMBOL)*rate)
    segment=amp[center-50:center+51] # +/-5 ms
    mins.append(float(np.min(segment)))
    below=segment < baseline*10**(-6/20)
    run=0
    for value in below:
        run=run+1 if value else 0
        max_below_6db=max(max_below_6db,run)

summary={
  'changed_tone_boundaries':len(mins),
  'analysis_bin_us':100.0,
  'boundary_window_ms':10.0,
  'median_interior_amplitude_dbfs':float(20*np.log10(baseline)),
  'worst_boundary_bin_relative_db':float(20*np.log10(min(mins)/baseline)),
  'boundary_minimum_p05_relative_db':float(20*np.log10(np.percentile(mins,5)/baseline)),
  'longest_contiguous_below_minus_6db_ms':float(max_below_6db/rate*1000),
  'qualification_statement':'No transition produced a carrier-envelope interruption at or above the reported 100 us resolution.' if max_below_6db==0 else 'See measured below-threshold duration; decode result remains separate.'
}
(ROOT/'transition-analysis.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
