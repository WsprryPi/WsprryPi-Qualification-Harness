# Application shims and protocol plans

Status: maintained hardware-free architecture. This is preparation for, not
execution of, bounded transmitter validation.

The harness owns safety preflight, receiver coordination, deadlines, cleanup,
classification, and immutable evidence. An application shim has the smaller job
of translating a protocol plan into the exact structured argument vector and
application identity required by one transmitter application. The transmitter
does not orchestrate or judge its own qualification.

`WsprryPiShim` is the sole maintained implementation. It records the executable,
parent and transmitter-submodule revisions, selected backend, protocol, resolved
arguments, stopping contract, and cleanup contract. Its output always records
`execution_authorized: false` and `supervisor_required: true`; the shim has no
method that starts a process. A self-terminating request is not described as a
hard-bounded process.

## Protocol boundary

- WSPR carries explicit callsign, grid, power, requested emitted RF frequency,
  audio offset, derived USB dial frequency, and frame count. Plans force
  `--no-offset` and use self-terminating `--terminate` requests. The default
  application-supported 1500 Hz audio offset is recorded and subtracted exactly
  once to form the positional dial-frequency argument. Canonical uppercase
  identity and standard encoded WSPR power are required so application-side
  normalization cannot silently change the evidence contract.
  The harness's canonical synthetic scenario is documented in
  `bounded-simulator.md`, but every resolved test or live-session plan must
  continue to carry its identity explicitly. No synthetic default is injected
  across the live authorization boundary.
- QRSS carries message, carrier frequency, and dot duration.
- FSKCW carries message, mark and space frequencies, and dot duration.
- DFCW carries message, dot and dash frequencies, and dot duration.
- Hellschreiber is a named future mode and is explicitly unsupported.

QRSS, FSKCW, and DFCW use WsprryPi's transient startup interfaces. They are not
silently treated as WSPR, and one mode's evidence cannot qualify another mode.
FSKCW requires mark above space; DFCW requires two distinct tones.
Application support likewise does not establish backend, band, RF-path, power,
filtering, or spectral qualification.

## Future live integration gate

A separately authorized transmitter workflow may pass a validated plan to
the existing transport and supervisor. Before that can happen it must also bind
the per-run RF path and operator confirmation, install cleanup, verify idle
hardware, enforce receiver/transmitter deadlines, and retain application stdout,
stderr, return code, identity, arguments, and quiescence evidence. No universal
authorization imports an RF path or authorizes transmission.

The command spellings are derived from the maintained WsprryPi argument parser:
WSPR positional identity/frequency with `--terminate`, and the mode-specific
`--qrss-*`, `--fskcw-*`, and `--dfcw-*` transient options. This document does not
modify or constrain WsprryPi itself.
