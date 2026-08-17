# GitHub Actions Node 24 maintenance prompt

## Objective

Remove the GitHub Actions Node.js 20 deprecation annotations from the portable
CI matrix by updating only the affected official actions to their current
Node-24-native major releases, then prove that the complete macOS, Ubuntu, and
native Windows workflow remains green and warning-free for this cause.

## Verified context

- CI run `32040089103` completed successfully for Python 3.11 and 3.13 on
  macOS, Ubuntu, and native Windows.
- Every matrix job warned that `actions/checkout@v4` and
  `actions/setup-python@v5` target Node.js 20 and were being forced onto Node
  24 by GitHub-hosted runners.
- The same action runtime emitted `punycode` and legacy `url.parse()`
  deprecation messages.
- The official action documentation currently demonstrates
  `actions/checkout@v7` with `actions/setup-python@v7`; both are Node-24-native.
- The completed hosted runners reported versions newer than the minimum Node
  24 action requirement.

## Scope and requirements

1. Preserve the existing triggers, permissions, operating-system matrix,
   Python versions, pip cache, commands, timeouts, and job semantics.
2. Change only `actions/checkout@v4` to `actions/checkout@v7` and
   `actions/setup-python@v5` to `actions/setup-python@v7`.
3. Do not add the insecure Node 20 compatibility environment override.
4. Validate the workflow syntax and inspect the complete diff for accidental
   permission, trigger, cache, or matrix changes.
5. Run all safe applicable local repository checks, recognizing that official
   JavaScript action runtimes can be proven only by the resulting hosted CI.
6. After pushing, poll the exact resulting run to completion and inspect its
   logs and annotations for Node 20, `punycode`, and `url.parse()` warnings.
7. Treat any remaining warning from these action steps as an unresolved
   finding; do not call the slice clean merely because jobs pass.

## Non-goals

- Do not change Python dependencies, source code, schemas, CMake, compiler
  warnings, hardware workflows, or release automation.
- Do not pin unofficial forks or suppress warnings.
- Do not touch either Pi, services, SDR, GPIO, transmitter, or RF.
- Do not conflate unrelated MSVC compiler warnings with the Node runtime slice.

## Validation and exit criteria

Run workflow-aware tests plus formatting, lint, type checking, the complete
unit suite, historical provenance verification, a hardware-free simulator,
package build, and native CMake/CTest as applicable. Independently review the
staged workflow and prompt. Commit and push only attributable changes. Exit
only when the branch is clean and synchronized and the new CI revision is
green on all six matrix jobs with the targeted Node-related warnings absent.
