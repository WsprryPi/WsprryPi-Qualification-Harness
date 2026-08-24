# Contract capability matrix

Use this matrix to find the production code, document contracts, and tests for
each capability governed by `CONTRACT.md`. It is a navigation index, not test
evidence. Confirm command syntax with the checked-out CLI
`--help` before use.

| Contract clause | Capability entry points | Principal schemas | Verification breadcrumbs |
|---|---|---|---|
| 1. Purpose and capabilities | `cli.py`, `capabilities.py`, application, capture, analysis, decode, coordinator, and result modules | capability-specific schemas listed below | `test_cli.py`, `test_capabilities.py`, capability-specific suites |
| 2. Supported control hosts | portable Python core; `transports.py`; CMake native helper | transport, SSH, process, and capture capability schemas | macOS/Ubuntu/native-Windows CI matrix; native CTest; `test_paths.py` |
| 3. Harness capabilities | `application_shims.py`, `carrier.py`, `carrier_plot.py`, `audio.py`, `decoder.py`, `cw_*`, `keyed_*`, `live_keyed.py`, `turnkey_campaign.py`, `complete_test.py`, `progress.py` | application-plan, carrier-analysis, audio, decoder, CW, keyed, turnkey, complete-test configuration/plan/result, and progress contracts | application, carrier, decoder, CW, keyed contract/coordinator/live, turnkey, complete-test routing/failure, and tail-ready progress tests |
| 4. Configuration | `profiles.py`, `application_shims.py`, `deployment.py`, `real_session.py`, `complete_test.py`, `sdr_calibration.py`, `receiver_calibration.py` | bench, test, receiver-run, helper deployment/runtime, application-plan, resolved-session, fixed-manual GPIO PPM containment pending Track E, frozen calibration profile/request and receiver-calibration binding | profile, application-shim, deployment translation, real-session, complete-test Tone/WSPR/keyed PPM containment, calibration binding, digest, and mismatch tests |
| 5. Safety invariants | `supervisor.py`, `real_session.py`, `receiver_integration.py`, `transmitter_lifecycle.py`, `capability_helper.py`, `real_capabilities.py`, `live_adapters.py`, `live_keyed.py` | runtime authorization, lifecycle/session, helper configuration/request/response, service allowlist/required-receiver subset, independently authenticated non-interactive service and transmitter-process privilege wrappers, capture-before-RF barrier, scheduled process start, WSPR slot-derived deadline and per-frame analysis budgets, process, capture failure diagnostics, cleanup, quiescence | supervisor, real-session, receiver, transmitter, helper static-config/runtime-digest/service-wrapper/process-wrapper substitution, scheduled-start arming/deadline/cancellation, WSPR boundary/deadline/analysis-budget regression, live-adapter capture readiness/pre-quiet/failure preservation/service restoration, and live-keyed failure-injection tests |
| 6. Measurement contract | `capture_metadata.py`, native capture helper, `carrier.py`, `audio.py`, `decoder.py`, `cw_reference.py`, `qualification_session.py` | capture metadata, guarded keyed exact-count capture, carrier analysis, audio conversion, decoder evidence, decode summary, qualification session | native CTest; capture, keyed capture-margin and scheduled-rebase, carrier, acquired-offline, audio, decoder, and qualification tests |
| 7. Result states | `classification.py`, `results.py`, `real_session.py`, `keyed_session_contracts.py`, `live_keyed.py` | result, real qualification session, keyed aggregate/result | classification, results, real-session, keyed precedence, and receiver-blockage tests |
| 8. Result bundle | `manifests.py`, `results.py`, replay/coordinator publishers | artifact-index, replay, receiver, keyed, and session schemas | manifest, result, replay, coordinator, tampering, and immutable-destination tests |
| 9. External tools | `tool_discovery.py`, `transports.py`, `remote_exec.py`, `remote_staging.py`, `real_capabilities.py`, `live_adapters.py` | SSH/transport/process/Soapy/helper capability schemas | tool-discovery, remote-staging, real-capability, deployment, and live-adapter tests |
| 10. Test strategy | hardware-free fixtures, sealed fakes, failure injection, native mock helper | all review-facing schemas | complete pytest suite, package build, native build/CTest, CI workflow |
| 11. Repository boundaries | `.gitignore`, packaging configuration, archive intake | archive inventory and non-qualifying multi-capture schemas | archive, schema-parity, installed-wheel smoke, and repository-boundary tests |
| 12. Change acceptance | checked-out docs and CLI; CI workflow | every `schemas/*.schema.json` byte-matched to its packaged copy | Ruff, Mypy, full Ubuntu product tests/build, focused macOS/Windows portability tests, and CMake/CTest |

Generated IQ, WAV, logs, manifests, and result directories are operational
outputs. Keep selected target records with the target project or another
approved evidence store; do not commit them to this harness.
