# Phase 6 actual-host preflight adversarial assessment

## Result

Two adversarial passes found and closed objective implementation issues. The
final software/evidence review has no unresolved finding within Phase 6. The
actual candidate remains truthfully blocked; this review does not waive any
recorded blocker or authorize Phase 7.

## Pass 1 findings and remediation

1. Group parsing did not recognize Linux `id` output such as `986(gpio)`.
   Parsing and tests now cover parenthesized group names.
2. Successful kernel, OS, clock, module, and service observations were retained
   in raw records but omitted from the result. They are now explicit checks.
3. Process inspection retained complete arguments, creating avoidable secret
   exposure. It now retains process names without command arguments.
4. The validator authenticated files and command vectors but did not recompute
   all check and blocker semantics. It now rejects semantic drift,
   missing/reordered probes, and invalid timing.

## Pass 2 findings and remediation

1. A required probe with exit zero and empty stdout could be reported as a
   successful observation. Required observations now reject empty evidence and
   a regression test covers the clock case observed on both actual hosts.
2. Passing executable checks displayed a failure-oriented diagnostic. The
   diagnostic now agrees with the observed outcome.

Reassessment confirmed that the fixed vocabulary rejects `sudo`, shells,
chaining/metacharacters, service mutation, `/dev` access, and unsafe Git
inspection; plan confirmation and explicit enablement are both required;
execution is bounded; bundles are never reused; and positive qualification or
Phase 7 authorization cannot be emitted.
