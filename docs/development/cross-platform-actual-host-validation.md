# Cross-platform actual-host validation

This record covers the bounded 2026-08-13 validation of the portable harness
on the available hosts. It does not qualify every supported operating system or
any transmitter configuration.

## Hosts and outcomes

- macOS (local Apple Silicon): the full Python suite, Ruff, strict mypy,
  bounded simulator, hardware-free CMake/CTest, and distribution build passed.
- Ubuntu Mule (Ubuntu arm64 in VirtualBox): the same portable validation gates
  passed from an isolated virtual environment.
- `wspr5` (Raspberry Pi OS arm64): the portable gates and native CTest passed.
  The maintained live coordinator also authenticated and opened the installed
  RSP1B for exact-count RF-off and RF-on captures.
- GitHub Actions: the macOS, Ubuntu, and native Windows matrix was green for
  commit `20d01d7b9635a2bc0fac9d355613c1e6c4796a21`. Native Windows 11 actual-host
  testing was explicitly deferred; hosted Windows CI is not a substitute.

## Bounded split-host carrier run

The corrected run `20260813T192417Z-wspr4-wspr5-live` used `wspr4` as the
transmitter host and `wspr5` as the receiver/controller. Before the run, both
hosts had no tmux session or competing qualification process; WsprryPi had
`Transmit = false`; GPIO4 was input; and the normal WsprryPi and SoapyRemote
services were active. The operator-confirmed plan digest included the exact
helper configuration hashes. A separate nonrecursive helper digest bound the
two persistent-helper configurations.

Both RSP1B captures retained exactly 2,500,000 CF32 samples (20,000,000 bytes)
at 250,000 samples/s with zero overflow, timeout, and clipping counts. The
carrier gate failed: the strongest resolved transmitter-added feature was
39,208.79364013672 Hz from the requested frequency and the best 20 Hz channel
held 0.010264300770189208 of resolved added power. Frames were therefore not
started. Final classification was `unqualified_carrier`; cleanup was verified,
both services were restored, no helper/capture process remained, and GPIO4 was
input.

The immutable bundle is retained on `wspr5` at
`/home/pi/wspq-live-runs/20260813T192417Z-wspr4-wspr5-live`. Its manifest,
artifact index, session semantics, and independently replayed carrier metrics
were verified. Retained IQ relocation is accepted only through that bundle's
authenticated source-to-retained mapping; content identity alone is not a path
provenance claim.

## Scope

This establishes actual-host behavior for the named hosts and bounded carrier
workflow. It does not qualify the transmitter, because the carrier gate failed.
It does not cover native Windows 11 actual-host behavior, calibrated RF power,
harmonics, spurious emissions, filter adequacy, another backend, or another RF
path.
