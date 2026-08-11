#!/usr/bin/env python3
"""Translate the retained 2 m complex IQ frame to wsprd audio."""

from pathlib import Path
import numpy as np

root=Path('/home/pi/issue379-si5351-frame-valid')
source=root/'si5351-2m-frame-valid.cf32'
output=root/'frame-250k.f32'
fs=250_000.0
center=144_515_500.0
carrier=144_490_500.0
audio=1500.0
mix_hz=(carrier-center)-audio
iq=np.memmap(source,dtype=np.complex64,mode='r')
chunk=1_000_000
with output.open('wb') as h:
    for first in range(0,len(iq),chunk):
        last=min(len(iq),first+chunk)
        n=np.arange(first,last,dtype=np.float64)
        mixed=np.asarray(iq[first:last])*np.exp(-2j*np.pi*mix_hz*n/fs)
        np.asarray(mixed.real,dtype=np.float32).tofile(h)
print(f'samples={len(iq)} output={output}')
