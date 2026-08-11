# Slice 1: portable offline foundation

Slice 1 supplies the maintained Python package, schemas, safe command-line
surface, typed profile models, read-only capability reporting, UTC and sample
calculations, result classification, deterministic manifests, and
hardware-free tests.

It does not implement transmitter control, SDR capture, carrier analysis,
audio conversion, `wsprd`, SSH transport, service control, cleanup supervision,
or hardware quiescence checks. No backend, band, board, receiver, or RF path is
qualified by Slice 1.

## Development setup

Use Python 3.11 or newer:

```text
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The only runtime dependency is `jsonschema`, used for JSON Schema Draft
2020-12 validation. Development uses pytest, Ruff, mypy, and build.

## Validation

```text
python -m ruff format --check .
python -m ruff check .
python -m mypy
python -m pytest
python -m build
```

CI runs these checks plus wheel installation and a CLI smoke test on current
Ubuntu, macOS, and native Windows runners with Python 3.11 and 3.13.

## Safe CLI

```text
wsprrypi-qualification version
wsprrypi-qualification capabilities
wsprrypi-qualification validate-profile bench examples/bench-wspr5-rsp1b.json
wsprrypi-qualification validate-profile test examples/test-si5351-160m.json
```

Capability discovery records the host platform and searches `PATH` for future
external tools without executing them. Future platform, transport, SDR,
service, and hardware adapters are reported as `not_implemented`. Tool presence
does not establish hardware availability or qualification.

`--enable-rf` always fails closed in Slice 1. Runtime operator confirmation is
not a profile property and cannot be committed as `confirmed`,
`operator_verified`, `approved`, or `enable_rf`.

## Profiles and schemas

Review-facing schemas live under `schemas/`; identical packaged copies are
loaded from `wsprrypi_qualification.schemas`. Bench profiles describe stable
receiver and RF-path facts. Test profiles describe the requested transmitter,
frequency, identity, timing, gates, and stopping procedure. The stopping
procedure records requested termination, abort, cleanup, and emergency
expectations; it is not runtime confirmation or evidence that cleanup occurred.
Requested profiles remain distinct from future fully resolved runtime profiles.

Machine-local credentials do not belong in profiles. Future transports must
obtain them from platform-appropriate environment or credential facilities.

## Time, samples, and run IDs

An instant exactly on an even UTC two-minute boundary maps to that boundary;
otherwise the next boundary is returned. Naive datetimes are rejected.

Sample counts require positive integral rates and durations. At 250,000
samples per second, 370 seconds is exactly 92,500,000 samples.

Run IDs use `YYYYMMDDTHHMMSSZ-test_id`. Equivalent instants normalize to UTC,
and bench/test identifiers are restricted to portable directory-safe
characters. They reject traversal, trailing dots, control characters, and
Windows device names. The UTC prefix means a complete run ID cannot itself be
a Windows reserved device name. Future run directory creation must reject an
existing ID rather than reuse it.

## Results

Final status is exactly one of `qualified`, `unqualified_carrier`,
`unqualified_decode`, `fixture_blocked`, `preflight_failed`, `aborted`,
`cleanup_failed`, or `inconclusive`. Preflight, carrier and decode gates,
failure causes, cleanup, and final classification remain separate. Cleanup
failure has highest precedence. Receiver, dependency, ownership, or RF-path
limitations are fixture blockage rather than transmitter unqualification.
Typed results derive status from the shared classifier. Loaded result documents
must pass both JSON Schema validation and semantic status/evidence validation.

## Manifests

Evidence manifests contain SHA-256, two spaces, and a POSIX-style relative
path, sorted lexically. Files are hashed in binary mode. Absolute paths,
traversal, symlinks, non-regular files, changing files, the manifest itself,
and `.incomplete-` artifacts fail closed or are explicitly excluded as
documented by the implementation. Writing uses an fsynced temporary file and
atomic replacement. A manifest name is one portable filename and cannot escape
the evidence root. Raw-IQ retention or deletion is not implemented.

## Next unfinished step

Slice 2 is the CMake-based exact-sample-count SoapySDR capture helper and mock
capture contract. It remains separately gated and must not open a physical SDR.
