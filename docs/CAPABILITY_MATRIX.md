# Contract capability matrix

Use this matrix to find the production code, document contracts, and tests for
each capability governed by `CONTRACT.md`. It is a navigation index, not test
evidence or a project roadmap. Confirm command syntax with the checked-out CLI
`--help` before use.

| Contract area | Capability entry points | Principal schemas | Verification breadcrumbs |
|---|---|---|---|
| Purpose and platform | `cli.py`, `capabilities.py`, `tool_discovery.py` | profile and capability schemas | `test_cli.py`, `test_capabilities.py`, CI workflows |
| Profiles and plans | `profiles.py`, `application_shims.py`, `real_session.py` | bench, test, receiver-run, application-plan, resolved-session | `test_profiles.py`, `test_application_shims.py`, `test_real_session.py` |
| Local and SSH execution | `transports.py`, `remote_exec.py`, `real_capabilities.py` | SSH and process capability schemas | `test_real_capabilities.py`, `test_live_adapters.py` |
| Helper boundary | `capability_helper.py`, `deployment.py` | helper request/response/configuration and deployment schemas | `test_capability_helper.py`, `test_deployment.py` |
| Exact-count capture | `native/src/capture.cpp`, `capture_metadata.py`, `live_adapters.py` | capture metadata and receiver lifecycle schemas | native CTest, `test_capture_metadata.py`, `test_receiver_integration.py` |
| Carrier analysis | `carrier.py`; `analyze-carrier` | carrier-analysis | `test_carrier.py`, `test_acquired_offline.py` |
| WSPR timing and decode | `timing.py`, `audio.py`, `decoder.py` | audio, decoder, and decode-summary schemas | `test_timing.py`, `test_audio.py`, `test_decoder.py` |
| Tone and keyed-mode analysis | `cw_reference.py`, `cw_iq.py`, `cw_replay.py`, `cw_qualification.py` | CW plan, expected-event, observation, gate, replay, and qualification schemas | `test_cw_reference.py`, `test_cw_contracts.py`, `test_cw_qualification.py` |
| Supervision and cleanup | `supervisor.py`, `real_session.py`, `receiver_integration.py`, `transmitter_lifecycle.py` | session, result, cleanup, quiescence, and lifecycle schemas | supervisor, real-session, receiver, and transmitter lifecycle tests |
| Live WSPR and TONE | `real_session.py`, `live_adapters.py`, `bounded_tone_control.py` | resolved real-session, runtime authorization, real-session result | `test_real_session.py`, `test_live_adapters.py`, `test_bounded_tone_control.py` |
| Result classification | `classification.py`, `results.py`, `manifests.py` | result and artifact-index schemas | `test_classification.py`, `test_results.py`, `test_manifests.py` |
| Repository boundary | `.gitignore`, packaging configuration, package-asset tests | packaged schemas mirror review schemas | `test_packaging_assets.py`, `test_schemas.py`, `test_requirements.py` |

Generated IQ, WAV, logs, manifests, and result directories are operational
outputs. Keep selected target records with the target project or another
approved evidence store; do not commit them to this harness.
