# Phase 7 exact-revision drive-0 candidate deployment prompt

## Objective

Deploy the fully reviewed and cross-platform-green harness revision
`c7faf22dfdb7d32e512a75ce55d2a002d629c736` into new isolated wspr4 and
wspr5 staging roots. Construct, validate, and seal a fresh drive-0 Phase 7
bounded live-tone candidate that contains the external CW contracts needed by
the analyzer. Report its exact canonical SHA-256 and stop for separate digest
authorization.

## Verified context

- Revision `c7faf22dfdb7d32e512a75ce55d2a002d629c736` is pushed and its GitHub
  Actions run is green on macOS, Ubuntu, and native Windows with Python 3.11
  and 3.13.
- This revision includes external CW-contract retention through the evidence
  analysis boundary and the GitHub Actions Node 24 maintenance update.
- The previous candidate and its authorization are consumed. Its evidence is
  immutable and must not be reused or amended.
- wspr4 normally runs `wsprrypi.service`. wspr5 normally has both
  `sdrplay.service` and `soapyremote-server.service` inactive.
- The RSP1B receiver serial is `2404058C60`; the intended receiver path is
  local SoapySDR using the `sdrplay` module, not SoapyRemote.

## Exact candidate contract

- Transmitter: wspr4 legacy Raspberry Pi GPIO clock through a dedicated copied
  WsprryPi executable, GPIO4, 14,097,100 Hz, drive 0.
- Tone: three cycles of 2 seconds off followed by 2 seconds on; no more than
  6 seconds total RF-on and 60 seconds overall.
- Receiver: wspr5 local SoapySDR capture, serial `2404058C60`, CF32 at
  250 ksps, 200 kHz bandwidth, fixed gain 10, AGC disabled, and bias tee
  disabled.
- Physical path: antenna disconnected; direct conducted connection through
  two 10 dB attenuators (20 dB total); no filter. Safe-input basis:
  `source and attenuation are operator confirmed`.
- Receiver service policy: `sdrplay.service` appears in both
  `services.receiver` and `services.receiver_required`, and the receiver
  helper allowlist binds that same service. SoapyRemote is excluded.
- Gate D is not applicable to this Raspberry Pi 4 legacy-GPIO candidate.
- Evaluation uses relative signal acquisition because the SDR is uncalibrated
  and may be temperature-drifting. It may establish only relative detection,
  not calibrated frequency, power, or spectral compliance.
- Analyzer source revision and every deployed harness copy are bound to
  `c7faf22dfdb7d32e512a75ce55d2a002d629c736`.

## Execution requirements

1. Inspect the branch, complete worktree, exact revision, and initial host
   service/process state. Resolve host addresses without changing either host.
2. Build the exact committed wheel. Create new unique staging roots on wspr4
   and wspr5 without replacing, modifying, or reusing earlier roots, runs,
   evidence, virtual environments, configurations, or candidate bytes.
3. Install the wheel in isolated virtual environments. Use a distinct helper
   executable path in each new root, copy the GPIO inspector into the wspr4
   root, and retain a dedicated immutable WsprryPi executable copy.
4. Hash the deployed executable bytes. Generate fresh profiles, CW tone plan,
   expected events, helper configurations, and resolved plan whose absolute
   paths name only the new root. Recompute every nested file hash, executable
   hash, helper-configuration digest, plan-file hash, and canonical plan
   digest from the final bytes.
5. Validate all schemas and profiles and run plan-only resolution. Require zero
   external calls. Confirm that the resolved plan retains both external CW
   contracts with their exact deployed paths, hashes, and sizes.
6. Run only non-hardware capability, helper-integrity, service-state, and
   ownership checks. Explicitly defer GPIO RF-idle inspection, service
   mutation, SDR access, WsprryPi execution, capture, and RF.
7. Close all helpers and verify that no helper-owned process remains and every
   relevant service has its initial state.
8. Independently and adversarially review the candidate bytes, absolute paths,
   hashes, identities, service semantics, revision bindings, deadlines,
   relative-analysis wording, and cleanup contract. Correct every actionable
   finding, regenerate affected hashes, and repeat validation until clean.

## Safety boundary and non-goals

- Do not stop or start any service, open or configure the SDR, inspect or
  configure GPIO, execute WsprryPi, generate a tone, or emit RF.
- Do not reuse any prior digest or infer authorization from earlier messages.
- Do not modify WsprryPi, its submodules, sibling repositories, or historical
  evidence.
- Do not claim calibrated frequency or power, spectral compliance, WSPR decode,
  or hardware qualification.
- If either host cannot be reached or a non-interference precondition fails,
  stop without partial live execution and report the blocker.

## Repository publication and exit criteria

Run proportionate repository checks for this prompt-only change, inspect the
complete staged diff, and commit and push only the attributable prompt. Do not
create or switch branches, rewrite history, force-push, open a pull request, or
publish candidate artifacts to Git.

Exit only when the exact candidate bytes are present in fresh isolated roots,
all non-hardware checks pass, host services and processes are restored to their
initial state, the repository branch is clean and synchronized, and the exact
canonical SHA-256 is reported with this required next authorization wording:

`I authorize only the Phase 7 bounded live-tone plan with SHA-256 <digest>.`

Do not run the live candidate until that exact digest is separately authorized.
