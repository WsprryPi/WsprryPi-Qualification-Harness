# Slice 5: bounded receiver-only validation

Slice 5 validates the maintained native exact-count capture helper against one
recorded physical receiver configuration. It does not operate or qualify a
transmitter and does not authorize another receiver, host, RF path, or setting.

## Validated configuration

The explicitly authorized run on 2026-08-12 used `wspr5` and its locally
attached SDRplay RSP1B, serial `2404058C60`, through SoapySDR 0.8.1 and the
`sdrplay` 0.5.2 module. The fully explicit receiver plan was:

- CF32, channel 0, 250,000 samples/second, and 200 kHz bandwidth;
- 1,863,100 Hz center frequency and fixed 10 dB gain;
- AGC disabled and bias tee disabled;
- a radiated path with an antenna connected, no termination, no inline
  attenuation, and no inline filter; the operator recorded the safe-input
  basis as `N/A`;
- 2,500,000 retained samples over 10 seconds;
- two-second read timeout, 15-second helper deadline, and 20-second external
  process deadline; and
- stop only `soapyremote-server.service` immediately before local ownership,
  then restore it during cleanup.

The final run retained exactly 2,500,000 samples and 20,000,000 bytes, with
zero timeouts, overflows, or clipped samples. Requested and actual identity and
settings matched. The first read was discarded. Stream deactivation, stream
closure, device release, service restoration, and helper-process absence were
verified. The helper and service returned successfully.

The RF-path declaration was supplied with `single_run` scope and applies only
to this retained receiver capture.

Raw IQ remains outside Git at:

```text
/home/pi/wspq-slice5-70bd307/evidence/receiver-only-10s-rerun.cf32
```

Its SHA-256 is
`afe0989bbc24d7c8b91f76b053b0314aaa0bb4f5fa7f07be64677095583f1d3e`.
The retained evidence bundle is:

```text
/home/pi/wspq-slice5-70bd307/evidence/20260812T135930Z-slice5-rsp1b-rerun/
```

It is also reproduced in the ignored local `runs/` tree.

## Identity correction discovered during validation

The first attempt selected the correct receiver but exposed an adapter defect:
the SDRplay device returned a capitalized driver key and did not include its
serial in hardware information. The helper therefore rejected the correct
device after configuration. The maintained adapter now authenticates the
unique result from exact driver/serial enumeration before creating the device.
Hardware-free native tests cover a unique match, no match, multiple matches,
wrong driver, wrong serial, and typed configuration failure. Identity failures
produce `wrong_device` evidence and exit code 6 rather than generic source-I/O
failure.

The physical helper now receives sample rate, bandwidth, channel, AGC, and bias
tee explicitly instead of relying on compiled defaults.

## Evidence meaning and remaining boundary

The final status is `inconclusive` because carrier and decode gates were not
run. This result validates exact-count capture and cleanup only for the recorded
`wspr5` RSP1B configuration. It does not establish RF silence, calibrated
power, spectral compliance, transmitter operation, or WsprryPi qualification.

Portable live orchestration is still unsupported: the capability report states
that the native helper is implemented and `wspr5`-validated while the portable
adapter remains unavailable. Slice 6, separately authorized bounded
transmitter qualification, is the next unfinished roadmap step.

## Per-run RF path and reusable authorization

The stable bench profile describes expected equipment, but it is not the live
RF-path declaration. Before every receiver run, the operator supplies a
schema-valid `receiver-run` profile containing the current antenna state,
termination, attenuation, filter description, safe-input basis, receiver
settings, exact sample count, timeouts, ownership, and cleanup plan.

Authorization has two scopes:

- `single_run` authorizes only the referenced receiver run; and
- `universal` records reusable permission for receiver-only access.

Both scopes still require a newly resolved RF path for every run. Neither can
authorize a transmitter, satisfy transmitter runtime confirmation, or turn a
committed profile into authorization. Runtime receiver-run documents belong in
the immutable evidence bundle rather than committed examples.
