# TODO: Pico acceptance hardening and test duration

**Status: not started; execution prompt awaiting separate invocation.**

Saving this prompt does not execute it, approve its implementation proposal,
establish a work budget, or authorize hardware access. This document contains
future task instructions, not standing operational authority.

## Execution prompt

You are working in `/Users/lbussy/GitHub/WsprryPi-Qualification-Harness`.

Implement a comprehensive, hardware-free acceptance gate for the complete Pico
qualification workflow. Address the systemic integration weaknesses exposed by
the recent WsprryPico campaign, and assess how to shorten software testing and
physical qualification without weakening their meaning.

Do not merely add a regression for each known failure and declare the framework
ready. Do not optimize test duration by silently reducing coverage,
observations, evidence, or cleanup requirements.

This prompt authorizes local read-only investigation and preparation of a
concrete implementation proposal when explicitly invoked. **Implementation and
repairs require the explicit approval gate below.** Once approved, scoped
implementation, offline testing, adversarial assessment, corrections, and
committing and pushing are authorized within the approved scope and budget.

It does **not** authorize bench-host access, physical USB or SDR access,
transmitter operation, RF, service changes, firmware installation, release
publication, tag movement, merging, or changes to another repository.

The objective is a trustworthy acceptance decision within an agreed
software-work budget, with measured and bounded test runtimes. Do not use
physical qualification to discover ordinary software integration defects.

### 1. Establish budgets and stopping rules

Before substantive investigation, establish an explicit budget for the local
read-only assessment and proposal. Use an applicable limit already supplied by
the user; otherwise, request it.

The implementation proposal must separately establish:

- Maximum elapsed implementation time, including testing, review, validation,
  and delivery.
- Maximum number of repair-and-review cycles.
- Permitted CI workflows, including explicit dispatches and automatically
  triggered workflows.
- Any CI expenditure limit.
- Runtime ceilings for the fast development test subset and complete
  hardware-free acceptance gate.
- Remaining allowance under any existing budget explicitly applicable to this
  task.

Do not invent limits, expand them, or interpret silence as approval. Bundle
missing budget questions into one concise request. Basic local orientation may
proceed while awaiting answers.

Do not implement changes, begin repair work, execute tests, or dispatch CI
before the proposal and required limits are explicitly approved.

Inspect relevant CI triggers during assessment. Count automatically triggered
workflows, including those caused by pushes, against the implementation
allowance. Reserve sufficient allowance for expected delivery-triggered work.

Do not disable required checks or alter workflow triggers merely to evade the
budget. If authorized delivery and the CI allowance conflict, identify that
conflict in the proposal or as soon as discovered.

Track budget consumption. Reserve time for validation, minimum delivery checks,
commit, push, and reporting. Do not start work unlikely to finish within the
remaining allowance.

Stop work when:

1. The current phase's deliverable is complete.
2. The applicable budget or repair-cycle limit is reached.
3. Further progress requires authority or prerequisites outside the approved
   scope.

A budget limit produces an **incomplete** assessment. It never implicitly
authorizes an extension. Report remaining work together rather than requesting
successive small extensions.

When financial CI cost cannot be measured, report workflow counts and available
runner-duration data. Do not invent expenditure figures.

### 2. Establish current repository truth

Follow repository instructions, beginning with these files in order:

1. `CONTRACT.md`
2. `docs/AGENT_OPERATIONS.md`
3. `docs/CURRENT_WORKFLOWS.md`
4. `docs/CAPABILITY_MATRIX.md`
5. Relevant development guides, schemas, current CLI help, production modules,
   and tests.

Inspect the current branch, working tree, worktrees, dependencies, and available
remote references. Preserve existing work. Do not reset, clean, stash, or
overwrite user changes.

The earlier review inspected Pico implementation through `42c18b6`. That
revision is historical context, not a required starting point. Determine the
current maintained source and account for subsequent changes.

Do not alter an active campaign's source, environment, frozen runtime, inputs,
evidence, or controller state. Do not disturb another agent's work.

Treat WsprryPico and other target repositories as read-only inputs. Keep
target-specific evidence, private account material, credentials, and generated
operational output out of harness commits.

During the assessment phase, use local read-only inspection. Identify missing
remote information as a limitation.

After implementation approval, Git network operations and explicitly budgeted
CI operations are authorized for harness development. They do not authorize SSH
or other access to bench hosts.

### 3. Define the acceptance and isolation boundaries

Establish that the actual Pico campaign software works as a connected system,
from composition through transactions, acquired-evidence processing, cleanup,
publication, and independent final semantic validation.

A passing gate establishes software acceptance only. It must never claim
hardware qualification.

Define the isolation threat model and enforceable controls. Prevent acceptance
execution from contacting bench hosts or opening physical devices, including
through accidentally selected production transports or inherited configuration.

Plan tests for attempted escapes through each supported transport and
subprocess boundary.

Use operating-system containment where available and appropriate. Where
enforcement relies on application seams, document that limitation. Do not
present passing tests as proof against arbitrary malicious code or every
possible operating-system escape.

Keep acceptance execution separate from authorized Git and CI network
operations. The gate must not depend on live network services.

Use temporary output directories outside source control. Do not modify original
retained campaign evidence.

### 4. Investigate the known weaknesses

Verify these against current source rather than assuming they remain unresolved:

- Production writers emitted documents their downstream validators rejected.
- A test supplied `analyzer.synthetic`, although the production analyzer schema
  forbade that field.
- Capture timestamps, acquired-evidence bindings, and acquisition bounds
  disagreed between producers and consumers.
- USB polling lost partial frames across deadlines.
- GPS correction evidence could miss the generation frozen by LOAD.
- GPS readiness and scheduling assumed preparation would complete within fixed
  allowances.
- Start-time selection occurred before variable-duration preparation.
- Fractional UTC requests unnecessarily reduced the permitted launch window.
- Receiver cancellation could terminate the process without retaining the native
  cleanup certificate required by the coordinator.
- Initial failures consequently escalated into `cleanup_failed`, obscuring
  their primary causes in user-facing summaries.
- Rehearsal exercised composition while bypassing the production adapter.
- A timing-sensitive CI assertion skipped fake-worker cleanup and prevented
  pytest from exiting.

Distinguish:

- Confirmed current defects.
- Historical defects already corrected.
- Missing coverage.
- Supported architectural concerns.
- Unproven hypotheses.

Keep firmware defects, receiver failures, environmental limitations, and harness
defects separate. Do not count every recorded unsuccessful result as a distinct
harness defect.

### 5. Compare Pi and Pico lifecycle contracts

Compare Pi and Pico lifecycle contracts before designing repairs. Examine preparation/readiness/arming order, deadline composition, ownership after controller loss, cancellation evidence, failure classification, partial-bundle publication, and lifecycle-boundary fault injection. For each difference, identify whether it is a justified backend distinction, a reusable safeguard, or an inconsistency requiring correction. Verify findings against production source and tests; do not assume the Pi implementation is correct. Propose shared abstractions only where semantics match, and preserve existing Pi behavior through focused regression checks.

In particular, examine these candidate lessons against current implementation:

- Separate preparation, capture readiness, and arming explicitly.
- Compose nested deadlines from actual work and remaining parent envelopes.
- Establish which ownership and finite-job guarantees survive controller loss.
- Inject failure and cancellation at every lifecycle boundary.
- Retain valid partial unsuccessful bundles without advertising rejected IQ as
  valid capture evidence.
- Separate controller-delivery latency from actual waveform requirements where
  the governing mode contract permits it.

Reconcile the Pi and Pico treatment of pre-RF receiver cancellation. Determine
what process-cleanup evidence and native device-cleanup evidence each path
requires and actually proves. Do not copy the more permissive behavior merely
to obtain a pass, or assume the stricter behavior is operationally reliable.

Keep the software-work budget distinct from live operational deadlines. Do not
introduce a new live cutoff that interrupts required cleanup.

### 6. Bound historical evidence intake

Use only relevant, locally available retained evidence or explicitly authorized
inputs.

Authenticate inputs using their maintained intake mechanism where available.
Record compatibility limits and authentication that could not be performed.

Do not execute commands or scripts recovered from historical logs. Derive
current operations from maintained source, documentation, and CLI help.

Do not contact a bench host to retrieve missing evidence.

Replay is required only where the approved acceptance specification identifies
it as necessary. Do not allow historical investigation or replay to become an
uncontrolled additional workstream.

Keep original evidence immutable, private content outside Git, and new replay
output separate and non-qualifying.

### 7. Assess test duration before proposing repairs

Prepare a timing assessment using locally available recorded timings, current
source, and contract-derived bounds. Do not execute tests or profiling workloads
before implementation approval.

Separate three workflows:

1. The fast software development loop.
2. Complete hardware-free acceptance.
3. Physical qualification.

For each workflow, identify time attributable to:

- Environment preparation and builds.
- Discovery and admission.
- Protocol exchanges and preparation.
- Readiness sampling.
- UTC-slot and other intentional waits.
- Capture and waveform duration.
- Analysis and decoding.
- Hashing, copying, and artifact publication.
- Independent validation.
- Cleanup.
- CI queue and execution.
- Failure-driven repetition.

Distinguish:

- Unavoidable protocol or measurement duration.
- Necessary software validation.
- Implementation overhead.
- Duplicated work.
- Avoidable waiting.
- Retesting caused by defects.
- Unknown or unmeasured time.

Provide traceable calculations or evidence for estimates. Label estimated,
observed, and contract-derived values separately. Do not treat an earlier
overall duration estimate as established fact.

Identify the critical path. Distinguish wall-clock savings from CPU, storage,
and CI-cost savings.

For each proposed optimization, state:

- The affected workflow and production path.
- The source of the delay.
- Proposed change.
- Expected savings and supporting evidence.
- Measurement uncertainty.
- Effect on coverage, evidence independence, and qualification meaning.
- Required implementation and verification.
- Whether separate approval to change qualification requirements would be
  needed.

Do not add overlapping savings together as though they were independent.

If evidence is insufficient to quantify an opportunity, propose a bounded
offline measurement within the implementation budget. Do not promise
unsupported savings.

### 8. Evaluate test-shortening opportunities

Assess these opportunities before choosing repairs:

#### Simulated time for software scenarios

Scheduling tests **must use simulated time** for scheduling, readiness, timeout,
and failure transitions. Advance clocks and completion events instead of waiting
through production-length waveform durations, UTC slots, lease periods, or
timeout windows. Do not retain production-length sleeps or wall-clock waits in
these tests.

Only separate **short, bounded real-time tests for process supervision** may
wait in real time to verify actual subprocess deadlines, cancellation, shutdown,
and escalation. Use the shortest intervals sufficient to validate those
mechanisms, with explicit upper bounds and guaranteed worker cleanup. State
their individual and aggregate runtime bounds in the proposal for approval.
Simulated time must not replace verification of real process behavior.

Reuse existing production WTP endpoint fixtures' clock-advancement capability
where appropriate rather than inventing another simulated JobService.

#### Earlier detection of evidence incompatibility

Connect production writers and validators before long campaign execution.
Detect schema, timestamp, binding, and final-publication incompatibilities
without transmitting.

#### Reuse of retained evidence

Use authenticated retained captures for analyzer and validator development where
appropriate. Replay remains non-qualifying and does not rewrite original results.

#### Fast and complete test tiers

Define a deterministic fast subset for development and a complete acceptance
gate for stabilized changes.

The fast subset must be clearly identified as partial acceptance. It must not
silently replace required final checks.

#### Reduced redundant processing

Investigate duplicated FFT work, conversions, hashing, copying, and validation
passes.

Optimize only where evidence demonstrates duplication. Preserve independent
validation, artifact authentication, freshness, and bounded resource use.

Any caching must bind all relevant inputs and versions, reject stale or changed
inputs, and avoid hiding defects through warm state. Report cold and warm
behavior separately where applicable.

#### Better WSPR scheduling

Assess whether preparation and analysis scheduling cause unnecessary missed
slots or idle periods.

Preserve ordinary protocol timing, required capture coverage, carrier gates,
and independent observations. Planning a physical optimization does not
authorize its execution.

#### Preservation of unaffected completed configurations

Define when existing completed evidence may remain applicable after a harness
change.

Require explicit source, plan, analyzer, schema, and contract compatibility
reasoning. A matching hash or successful replay alone does not establish
continued live qualification.

Preserve original classifications. Do not silently promote old unsuccessful
evidence or combine incompatible runs.

#### Safe parallel execution

Consider parallelism only for independent software work.

Do not parallelize operations that share a physical device, ownership domain,
mutable output, global configuration, or timing dependency. Account for resource
contention and its effect on elapsed time and reliability.

Do not shorten waveforms, reduce independent observations, drop configurations,
relax quiet margins, weaken cleanup, widen tolerances, or remove required
validation under the label of performance optimization.

Any proposed change to the qualification standard requires separate explicit
approval and a clear account of what the reduced test would establish.

### 9. Perform the initial adversarial assessment

Before implementation, assess the current workflow and proposed gate
adversarially.

Investigate:

- Production components the tests might accidentally bypass.
- Shared assumptions that could let a producer and validator agree while both
  are wrong.
- Fakes that behave more cooperatively than real endpoints.
- Timing conditions the design cannot represent.
- Failure paths that lose evidence, leak workers, or obscure causes.
- Ways fixture execution could reach hardware or publish a false qualification
  claim.
- What existing rehearsal and source-pair CI actually exercise.
- Whether proposed shortening hides timing defects or reduces coverage.
- Whether caching, deduplication, or parallelism invalidates independence.
- Whether the real WTP client and production endpoint remain connected in the
  principal acceptance scenarios.
- Whether the proposed work fits within a bounded implementation budget.

Create an issue ledger containing:

- Issue identifier and severity.
- Concrete evidence or a clearly labeled hypothesis.
- Owning component.
- Reproduction or investigation method.
- Acceptance criteria.
- Verification method.
- Proposed disposition.

A **blocker** is any defect that permits false acceptance, unauthorized effects,
lost ownership, unbounded execution, missing required cleanup proof, invalid
evidence publication, or failure of a required production path.

Do not classify unrelated improvements as blockers.

### 10. Prepare the concrete proposal and acceptance specification

Before coding, present a reviewable proposal containing:

- The systemic diagnosis, supported by current evidence.
- The Pi/Pico lifecycle comparison and disposition of each relevant difference.
- The proposed acceptance architecture.
- Grouped repair scope organized around systemic causes.
- Specific production modules and interfaces expected to change.
- Historical fixes needing verification rather than reimplementation.
- The timing assessment and prioritized shortening opportunities.
- Proposed implementation budget and repair-cycle limit.
- Proposed runtime ceilings for the fast subset and complete gate.
- Expected runtime, storage, and CI cost.
- Dependencies, uncertainty, and unavailable prerequisites.
- Explicit exclusions.
- Minimum delivery checks.

Include an acceptance specification defining:

- The production entrypoint and complete execution path under test.
- Every production component exercised.
- Every substituted boundary and its justification.
- The real WTP client and production endpoint fixture used by the principal
  successful scenarios, including their source/build identities.
- The configuration, mode, and fault coverage map.
- Required outcome, identity, artifact, ownership, settings-preservation, and
  cleanup assertions.
- Qualification-isolation assertions.
- Independent expectations and negative controls.
- Blocker definitions and measurable completion criteria.
- Runtime measurement conditions and performance acceptance criteria.
- What passing will prove and what it will not prove.

Set runtime ceilings against a declared environment and workload. Avoid brittle
subsecond assertions that confuse ordering correctness with machine speed.

If required work cannot fit within the proposed budget, say so. Do not silently
substitute a smaller gate and describe it as comprehensive.

### 11. Obtain explicit implementation approval

**Stop after presenting the proposal and acceptance specification. Request
explicit approval of the grouped repair scope, architecture, timing
optimizations, acceptance criteria, and implementation budget.**

Do not begin implementation, repairs, test execution, CI dispatch, or repository
changes until approval arrives. The user must approve a concrete engineering
effort, not an open-ended intention to improve testing.

After approval:

- Freeze the approved proposal and acceptance specification.
- Proceed autonomously with routine implementation details inside that scope.
- Do not request repeated approval for work already covered.

Any newly discovered issue outside the approved scope must be reported together
with its implications. Discovery does not authorize further implementation.

Continue independent, approved work when useful, but do not implement an
out-of-scope repair. Bundle proposed scope changes rather than seeking
successive small extensions.

Material reductions in required coverage, safety assertions, independence of
validation, qualification requirements, or acceptance criteria require explicit
user approval. Until approved, the original criteria remain controlling.

Record specification changes with their reasons and effects on coverage, cost,
runtime, and claims.

If the original criteria cannot be satisfied within the approved scope and
budget, report **incomplete** rather than redefining acceptance.

### 12. Establish isolation before executing tests

After implementation approval, establish an isolated test environment before
test collection, imports, plugin loading, or subprocess creation.

Inspect and explicitly control:

- Inherited environment variables.
- Hardware and integration flags.
- Device paths and configuration paths.
- Credential-bearing environment and configuration.
- Pytest or other automatically loaded plugins.
- Subprocess environments.
- Working directories and output destinations.

Prevent test code from selecting bench transports or physical devices through
inherited configuration.

Child processes must inherit the same restrictions or execute within an
equivalently controlled boundary. Account for indirect effects, not only direct
Python calls.

Record which access attempts were denied and which observation mechanisms
support the final boundary-use report. An unobserved boundary must be reported
as unobserved, not asserted to have had zero access.

### 13. Implement the connected production workflow and real WTP path

Use an isolated `codex/` branch or worktree as appropriate.

Build one maintained, reproducible acceptance gate connecting the production:

- Pico campaign composition and coordinator.
- WTP client, framing, and transaction controller.
- Production WTP endpoint and JobService through host-built endpoint fixtures.
- Measurement and evidence writers.
- Cleanup handling.
- Bundle publisher.
- Final semantic validator.

The principal successful acceptance scenarios must use the **real WTP client
against the production endpoint fixture**, not a fake WTP peer. Connect that
path to the actual campaign's preparation, receiver coordination, analysis,
cleanup, publication, and final validation.

Exercise HELLO, capability negotiation, CLAIM, LOAD, ARM, RENEW, status/events,
completion, ABORT, and RELEASE where applicable. Retain and validate the wire
audit and transaction identities.

Existing WTP unit tests, endpoint integration tests, and live USB use are
distinct coverage layers. Do not claim complete campaign acceptance merely
because the endpoint tests pass.

Fake peers remain useful for targeted fault injection. Identify every such
substitution and do not count it as production endpoint coverage. Where faults
can be injected around the real client/endpoint boundary, prefer that approach.

Build and bind the required explicit production source-pair fixtures. Missing
endpoint fixtures make required endpoint coverage incomplete; do not silently
skip them or substitute a cooperative fake.

Simulate physical device and receiver boundaries and, where needed, time. Reuse
actual product encoders and production endpoint fixtures where available and
appropriate.

Follow the approved declaration of real and substituted components. Include
subprocesses and indirect effects.

Do not replace the components being evaluated with:

- A fake campaign coordinator.
- Fabricated passing analysis.
- Hand-written success documents.
- A stub final validator.
- A permissive endpoint that ignores production protocol requirements.

Prefer existing abstractions and a small, coherent implementation. Introduce
production seams only where necessary. Preserve default live behavior and
require explicit fixture selection.

Document what every substitution prevents the gate from proving. Host-built
endpoint coverage does not establish physical CDC behavior or firmware timing
on the board.

### 14. Cover the maintained configuration and mode matrix

Exercise production orchestration for all six configurations:

1. GP2/PIO-DMA, GPS disabled, automatic correction off.
2. GP2/PIO-DMA, GPS enabled, automatic correction off.
3. GP2/PIO-DMA, GPS enabled, automatic correction on.
4. Si5351, GPS disabled, automatic correction off.
5. Si5351, GPS enabled, automatic correction off.
6. Si5351, GPS enabled, automatic correction on.

Exercise all five maintained modes:

- TONE.
- WSPR.
- QRSS.
- FSKCW.
- DFCW.

Cover three independent observations per mode and additional WSPR carrier
transactions required by the current contract.

Distinguish explicitly between:

- Orchestration coverage.
- Protocol and transaction coverage.
- Real WTP client/production endpoint coverage.
- Measurement coverage.
- Final-validator coverage.
- Failure and cleanup coverage.

None substitutes for another.

Each scenario designated as successful must traverse actual evidence production
and final validation appropriate to its coverage claim.

Use layered execution to meet the approved runtime ceilings. Run inexpensive
scheduling and fault scenarios extensively, and complete measurement scenarios
for every distinct production path.

For any shared measurement scenario:

- Identify the configurations it represents.
- Demonstrate why their relevant production paths are equivalent.
- Identify differences the shared scenario does not exercise.

Do not infer complete matrix coverage from a representative case without that
evidence. Report uncovered combinations explicitly.

### 15. Exercise adversarial conditions

Inject faults at meaningful boundaries rather than testing only exact
historical failure values.

#### Protocol and transport

Include:

- Fragmented headers and payloads across polling deadlines.
- Delayed, missing, malformed, duplicated, or mismatched responses.
- CRC, length, schema, session, request, and boot-identity violations.
- Lost mutation replies.
- Verification that uncertain mutations are not automatically replayed.
- Bounded event queues and complete retained diagnostics.
- Lease renewal and controller-disappearance behavior under the actual endpoint
  contract.

#### GPS, correction, and scheduling

Include:

- Variable LOAD, management-read, and snapshot latency.
- GPS observations changing between reads and around LOAD.
- Transient readiness recovery.
- Persistent readiness refusal.
- Stale, conflicting, wrong-boot, or unavailable correction generations.
- UTC-second boundaries.
- Missed preparation windows.
- Wall-clock changes.
- Exhausted capture coverage and quiet margins.

#### Receiver and evidence

Include:

- Capture setup failure.
- Early capture exit.
- Partial or rejected output.
- Malformed or contradictory metadata.
- Incorrect sample counts.
- Writer/validator schema disagreement.
- Authenticated-input tampering.
- Missing or mismatched artifact identities.
- Invalid final bundle publication.
- Stale caches or reused artifacts presented under changed inputs.

#### Cancellation and cleanup

Include:

- Cancellation during preparation.
- Cancellation while armed and waiting.
- Cancellation during execution.
- Cancellation during analysis and publication where applicable.
- Successful cleanup.
- Failed cleanup.
- Missing cleanup certificates.
- Workers or children that do not exit.
- Preservation of the primary cause alongside cleanup failures.

#### Portability, performance, and isolation

Include:

- Paths containing spaces.
- Supported Windows path forms.
- Platform-specific adapter selection and refusal.
- Attempts to construct physical transports from fixture execution.
- Attempts to escape through subprocesses or inherited configuration.
- Attempts to use fixture artifacts as live qualification evidence.
- Cold-state execution that cannot depend on previously cached success.
- Resource contention where approved parallelism is used.

Keep scenarios finite. Guarantee cleanup of test-owned resources even when
assertions fail.

### 16. Correct scheduling as a connected timing model

Review preparation, capture, ARM, execution, analysis, and cleanup together.

Do not solve latency problems solely by increasing constants or weakening
deadlines. Derive bounds from owning contracts and bounded operations. Document
remaining policy constants and their basis.

Select flexible start times after preparation where permitted. Preserve
protocol-fixed WSPR slots.

Use monotonic time for elapsed deadlines and explicit UTC conversion for
scheduled starts.

Preserve:

- Receiver readiness before RF.
- Required preamble.
- Complete job capture.
- Required post-job quiet.
- Existing ARM constraints.
- Ownership and cancellation guarantees.

An impossible schedule must refuse before ARM.

Do not recover by silently changing authenticated inputs or replaying uncertain
mutations.

Test meaningful latency ranges and boundary conditions, not only a single
measured bench delay.

Scheduling tests must use simulated time; they must not wait for
production-length windows. Retain only short, explicitly bounded real-time
tests for actual process supervision, as required in section 8.

### 17. Make cancellation produce trustworthy cleanup evidence

Review receiver cancellation end to end.

Implement the approved bounded shutdown path for retaining trustworthy native
cleanup evidence where supported, with bounded escalation for unresponsive
children.

Isolate platform-specific behavior behind explicit adapters. Do not introduce
platform assumptions into the portable core.

Process exit alone must never substitute for device cleanup. Missing cleanup
proof must remain unsuccessful.

Keep these facts distinct in evidence and reporting:

- Primary operation failure.
- Transmitter inactivity and ownership release.
- Receiver process termination.
- Native receiver cleanup certification.
- Settings preservation.
- Final result precedence.

Do not weaken cleanup requirements to turn previously unsuccessful results into
passes.

### 18. Prevent fixture evidence from becoming qualification evidence

Synthetic input may exercise acquired-evidence processing within an isolated
fixture, but must not become externally acceptable live qualification evidence.

Acceptance artifacts must remain distinguishable from live evidence.

Verify that the maintained external qualification-acceptance path rejects
fixture output even when internal measurement documents intentionally exercise
acquired-evidence processing.

Test rejection at the final acceptance boundary, not merely through a label in
a test report.

Preserve strict live validation. Do not weaken authentication, provenance,
schemas, measurement tolerances, ownership, or cleanup requirements to
accommodate fixtures.

Do not rewrite original historical results. Any replay or corrected
interpretation must remain a separate, explicitly non-qualifying artifact.

### 19. Validate retained bundles independently

Validate completed bundles in a fresh process using the maintained public
validation entrypoint and only retained artifacts.

Do not rely on shared in-memory objects, cached successful decisions, or state
left by the writer.

Identify production logic shared between writer and validator.

Use independent expectations and negative controls for critical invariants.
Fresh-process execution alone does not establish logical independence.

Demonstrate that tampering with required retained evidence causes rejection.
Cover missing artifacts, contradictory metadata, changed identities, altered
bounds, invalid cleanup evidence, and fixture-to-live misrepresentation as
applicable.

Performance optimizations must not bypass this independent validation.

### 20. Measure and verify approved runtime improvements

After approval, measure representative baseline and revised runtimes using the
approved environment and workload.

Record:

- Total wall time.
- Major phase durations.
- Test and scenario counts.
- CPU or storage measurements where relevant and available.
- Cold and warm conditions where caching matters.
- Failures, retries, and incomplete measurements.
- Environment details sufficient to interpret the results.

Use bounded repetitions only where needed to distinguish an improvement from
ordinary variation. Do not create an expensive benchmark campaign merely to
claim precise savings.

For each optimization, verify both:

1. The intended runtime or resource improvement.
2. Preservation of required behavior, coverage, evidence independence, and
   failure detection.

Do not claim savings by comparing different workloads without disclosing and
justifying the difference.

Check the fast subset and complete gate against their approved runtime ceilings.
A runtime overrun must be reported and investigated within budget, not hidden
by skipping required tests.

Treat physical campaign savings as estimates unless supported by existing
comparable physical evidence. Do not execute a live campaign to measure them
under this prompt.

### 21. Iterate through approved issues and reassess adversarially

Within the approved scope, budget, and cycle limit:

1. Establish a concrete reproduction or supported failure argument.
2. Correct the issue without weakening the governing contract.
3. Verify the correction at the appropriate layer.
4. Verify that the test detects the original failure.
5. Close the ledger item with evidence.
6. Perform another adversarial assessment for related failures, bypasses, and
   new blind spots.

Do not close an issue merely because tests pass.

Use independent expectations or narrowly scoped negative controls where useful.
Shared implementation logic alone is not an independent oracle.

Assess whether the gate can falsely pass when:

- A required production component is bypassed.
- A required check is removed or disabled.
- A producer emits invalid evidence.
- Cleanup proof is missing.
- A fixture attempts physical or bench-host access.
- An unsuccessful transaction is presented as successful.
- Final validation depends on writer state rather than retained artifacts.
- A performance optimization omits required work or reuses stale results.
- A fake WTP peer replaces the required production endpoint path.

Continue until acceptance is achieved or a stopping rule applies.

Do not expand into unrelated product redesign, cosmetic cleanup, or indefinite
hardening.

If a finding falls outside approved scope, report it and its implications
together with other scope findings. Do not repair it without explicit approval.

If closure requires hardware authority, target-repository changes, unavailable
builds, or another external prerequisite, record the blocker and mark the gate
incomplete.

### 22. Run the final adversarial assessment

After implementation stabilizes, perform a final adversarial assessment against
the frozen acceptance specification.

Check that:

- Every claimed production path is actually exercised.
- Required configurations and modes are covered as claimed.
- The real WTP client/production endpoint path is exercised as specified.
- Equivalence claims have supporting evidence.
- Fault injection reaches the intended boundary.
- Tests detect the defects they claim to prevent.
- Test failures cannot leak workers or hang the suite.
- Isolation controls apply before collection and throughout subprocess execution.
- Fixture execution cannot use supported paths to access hardware or bench hosts.
- Fixture artifacts are rejected by external live qualification acceptance.
- Completed bundles are validated from retained artifacts in a fresh process.
- Cleanup and primary failure causes remain distinct.
- Pi/Pico lifecycle differences have evidence-backed dispositions, and shared
  changes preserve Pi behavior through focused regression checks.
- Runtime reductions preserve required work and qualification meaning.
- Fast and complete test tiers are clearly distinguished.
- Runtime claims match comparable measurements.
- Documentation and CI claims match actual execution.
- No blocking issue remains open.

A clean final assessment means no new blocker was found within the documented
review scope. It is not proof that no defect exists.

If a new blocker appears, reopen the ledger. Use another repair cycle only if
the repair is within approved scope and budget and cycle allowance remain.
Otherwise, report incomplete.

### 23. Validate the implementation

Run directly affected checks during development. Use the approved fast subset
where appropriate.

For this cross-cutting change, run the repository's complete required acceptance
set after implementation stabilizes:

```text
python -m ruff format --check .
python -m ruff check .
python -m mypy src
python -m pytest
python -m build
cmake -S . -B build-native -DWSPQ_BUILD_SOAPY=OFF -DWSPQ_BUILD_TESTS=ON
cmake --build build-native --config Release
ctest --test-dir build-native -C Release --output-on-failure
```

Use temporary output locations or appropriate out-of-tree build paths
consistent with repository instructions.

Apply isolated environment requirements to every test invocation, including
broad suites and child processes.

Also verify:

- Schema source/package synchronization.
- `git diff --check`.
- Applicable production source-pair integration checks, including the required
  real WTP client/endpoint fixtures.
- Appropriate portable CI coverage.
- Installed-package behavior where affected.
- Approved performance acceptance criteria.
- Focused Pi regressions for any affected shared behavior.

Dispatch or trigger CI only within the approved allowance. Account for
delivery-triggered workflows before pushing.

Never treat a missing production fixture as an unexplained skip. Required WTP
endpoint coverage remains incomplete if its source-pair build is unavailable.

Report passes, failures, skips, unavailable checks, and platform limitations
separately.

Do not repeatedly rerun broad checks without a new change, failure, or
unresolved concern that justifies doing so.

If the budget prevents completion of required checks, mark acceptance incomplete.

### 24. Document the maintained gate and timing policy

Document:

- Exact commands for the fast subset and complete gate.
- Dependencies and required source-pair inputs.
- Isolation setup and threat model.
- Expected runtime and resource use under declared conditions.
- Production components exercised, including real WTP client/endpoint coverage.
- Substituted boundaries.
- Configuration, mode, measurement, and fault coverage.
- Timing and cleanup semantics.
- Pi/Pico lifecycle comparison and justified backend distinctions.
- Qualification-evidence isolation.
- Fresh-process validation behavior and shared-logic limitations.
- Cache or parallel-execution rules where implemented.
- What passing each test tier proves.
- What still requires physical validation.
- How failures and runtime overruns are reported and investigated.
- Rules for retaining unaffected completed evidence after a change.

Integrate the gate into the maintained developer workflow and appropriate CI
jobs within approved scope.

Keep target-specific operational evidence and private inputs outside published
documentation and source control.

### 25. Commit and push at the implementation stopping point

After implementation has been explicitly approved, commit and push at its agreed
stopping point whether the result is **accepted** or **incomplete**, subject to
these delivery requirements:

1. Review the diff for scope, correctness, sensitive data, and generated content.
2. Complete minimum delivery checks.
3. Record unresolved findings and validation gaps.
4. Commit scoped harness changes on the selected `codex/` branch.
5. Push that branch to the authorized remote.
6. Verify the pushed commit identity.
7. Report automatically triggered CI and its status.

Minimum delivery checks include:

- Diff review.
- Sensitive-data and generated-content review.
- Syntax/import checks for changed code.
- Directly affected tests.
- Confirmation that unfinished functionality is explicitly selected and isolated
  from default production behavior.

Use an appropriate commit message. Incomplete work must be clearly identified
as incomplete in its maintained status record and final report.

Do not deliver a known production-default regression as an acceptable incomplete
milestone.

If minimum delivery checks cannot be completed, changes cannot be isolated
safely, or delivery would exceed authorized CI limits, stop and report the
delivery blocker.

The instruction to commit and push does not override preservation, security,
or repository safety requirements. Identify exactly what remains uncommitted
or unpushed.

Do not merge, publish releases, move tags, modify another repository, or begin
physical testing.

If committing or pushing is blocked, retain the work and report the exact
blocker. Do not claim delivery succeeded. Do not force-push or overwrite
concurrent work.

The initial assessment and proposal phase does not authorize repository changes,
commits, or pushes.

### 26. Report the outcome

At the approval gate, report the concrete proposal, timing assessment, Pi/Pico
lifecycle comparison, and acceptance specification, and stop for the user's
decision.

After approved implementation, lead the final report with **accepted** or
**incomplete**, using the frozen acceptance criteria.

Include:

- Fundamental changes replacing the previous fix-and-retry approach.
- Confirmed systemic causes and their corrections.
- Historical fixes verified rather than reimplemented.
- Pi/Pico lifecycle differences, their dispositions, and relevant regressions.
- Real WTP client/production endpoint coverage and any substitutions or gaps.
- Adversarial findings and closure evidence.
- Remaining blockers, limitations, and uncovered paths.
- Out-of-scope findings and their implications.
- Material specification changes and approvals.
- Exact validation performed, results, skips, and CI status.
- Baseline and revised software runtimes under comparable conditions.
- Fast-subset and complete-gate runtime-ceiling results.
- Measured savings, estimated physical savings, and their uncertainty.
- Any proposed qualification-standard changes that remain unapproved.
- Budget consumption and number of repair-and-review cycles.
- Branch, commit SHA, remote, and push verification.
- Uncommitted or unpushed work and its delivery blocker.
- Any narrowly defined physical validation still required.

Report boundary use separately:

- RF transmissions and RF budget consumed.
- Physical USB and SDR access.
- Bench-host access.
- Git network operations.
- Explicit and automatically triggered CI workflows.
- CI expenditure or available runner-duration data.
- Isolation controls, denied access attempts, and observation limits.

For this authorized scope, RF, physical device access, and bench-host access must
remain zero. Support the report with available controls and observations. If a
boundary was not observed, say so rather than asserting an unsupported zero.

A passing software gate does not authorize physical testing and does not
establish hardware qualification.
