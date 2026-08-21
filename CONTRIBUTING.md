# Contributing

This project is a maintainer engineering and qualification tool. Read
`CONTRACT.md` and `AGENTS.md` before planning or changing implementation.

## Scope and review

- Work within the currently authorized task boundary.
- Keep implementation, future plans, non-goals, and unvalidated qualification
  claims distinct.
- Keep this repository independent from WsprryPi and its sibling projects.
- Treat staging, commits, pushes, remotes, and pull requests as separately
  authorized actions.

## Portability and validation

Portable orchestration and offline analysis must support Python 3.11 or newer
on macOS, Linux/Raspberry Pi OS, and native Windows 11. Avoid shell, Unix-path,
POSIX-signal, `/proc`, systemd, and GNU-utility assumptions in the portable
core. Use explicit capability adapters for platform- or hardware-specific
behavior.

Run the complete safe validation applicable to a change. Report commands and
results accurately, including warnings, skipped checks, missing tools, and
platforms that were not actually tested.

## Safety and evidence

Normal development must remain hardware-free. Live RF, transmitter hardware,
physical SDR access, service changes, Raspberry Pi installation, and mutating
privileged operations require separate, precisely bounded authorization.

Qualification claims must be specific to the recorded backend, band,
hardware, sources, settings, receiver, and RF path. Decoder success is not
evidence of calibrated power or spectral compliance. Preserve complete logs,
failure causes, cleanup results, and immutable evidence records.

## Local and generated material

Do not commit credentials, machine-local overrides, private bench details,
large raw IQ or audio captures, generated run directories, compiled binaries,
or dependency environments. Small reviewed fixtures and evidence summaries may
be committed when their provenance, redistribution rights, and purpose are
clear.
