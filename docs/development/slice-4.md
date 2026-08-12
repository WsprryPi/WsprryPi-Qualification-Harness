# Slice 4: hardware-free transports and lifecycle supervision

Slice 4 adds structured local command execution, a fail-closed SSH interface,
typed lifecycle adapter contracts, deterministic mock transmitter/receiver,
service and quiescence adapters, and a failure-injected cleanup supervisor.

The local transport uses an absolute executable, an argv list, `shell=False`,
an allowlisted environment, portable subprocess APIs, and a hard external
deadline. It records complete output, return code, cancellation, timeout,
executable hash, and child cleanup. Native Windows termination remains an
actual-host CI validation gate.

SSH is not executed in Slice 4. A sealed deterministic in-process test double
exists only because the future harness may control a remote Raspberry Pi while
capturing locally. Structured remote arguments are encoded as UTF-8 JSON in
URL-safe Base64 with the
`wspq-argv-v1:` marker. The fake API accepts no executable path and calls no
subprocess or network API. A future remote adapter must decode that contract
without an ambient shell. Exit 255 is retained only as simulated evidence.

The supervisor is single-use and installs cleanup evidence before acquisition.
Every mock acquisition, start, monitored execution, stop, release, service
restoration, leak check, and backend inspection is represented by a nonblocking
bounded-operation handle with a unique identity and typed result. Per-operation
and overall monotonic deadlines plus an externally observable cancellation
event apply throughout the lifecycle. Cleanup stops transmitter before
receiver, releases only recorded ownership, restores only changed mock
services, checks every owned handle for leaks, and records backend quiescence
independently. Typed lifecycle phases—not error-message text—select failure
causes. Successful mock orchestration is `inconclusive`, never `qualified`.

The offline SSH contract test record contains its destination, mock-only mode,
simulated version query, intended remote argv, encoded command, deterministic
handle, complete output, timeout/cancellation, disconnect, and cleanup result.
It never records or fabricates an executable path, hash, or process ID. OpenSSH
execution is not implemented in Slice 4.

The supervisor accepts only sealed reviewed mock and local-process operation
implementations. Mock behavior is declarative—poll counts, outcomes, and
cancellation points—and exposes no caller callbacks. Local child output uses
temporary regular files rather than inherited pipes, preventing descendants
from extending a parent timeout by holding a pipe open. Transport and supervisor
loaders enforce typed outcome consistency and the resolved cleanup order.

No physical SDR, transmitter, GPIO, I2C, service manager, SSH endpoint, or RF
path is accessed. Slice 5 is the next unfinished step and requires separate
authorization for bounded live receiver-only validation.
