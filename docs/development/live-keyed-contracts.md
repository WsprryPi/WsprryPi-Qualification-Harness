# Live keyed session contracts

`keyed_session_contracts` is the hardware-free contract boundary for future
live QRSS, FSKCW, and DFCW coordination. It constructs and validates documents;
it cannot start a process, contact a host, open an SDR, change a service, touch
GPIO, or enable RF.

The resolved plan fixes the mode, transmitter and receiver identities, RF path,
reference artifacts, deadlines, stopping procedure, and transaction count. Only
canonical uppercase `QRSS`, `FSKCW`, and `DFCW` are accepted. The maintained
canonical digest is SHA-256 over finite JSON encoded with sorted keys, compact
separators, and ASCII escaping. Runtime authorization binds that exact digest,
session, mode, operator, UTC time, and exactly three transactions.

Each transaction records the ordered lifecycle:

1. preflight;
2. cleanup installation;
3. process start;
4. capture completion;
5. analysis completion;
6. cleanup completion; and
7. quiescence verification.

The aggregate requires transactions numbered 1, 2, and 3. Transaction, process,
capture, acquisition, analysis, artifact-path, and artifact-hash identities must
be independent across all three. A transaction cannot make a qualification
claim by itself.

Final status is derived, not trusted. Precedence is cleanup or quiescence
failure, abort, preflight failure, fixture blockage, keyed measurement failure,
inconclusive measurement, then qualification. `qualified` requires all three
transactions to pass with verified cleanup and quiescence. The result binds the
canonical aggregate digest, and the artifact index requires each core contract
document exactly once with safe relative paths and unique identities.

These contracts are preparation for a later coordinator implementation. A
schema-valid or semantically valid document is not runtime authorization to use
hardware and is not live qualification evidence.
