# Production capability adapters: hardware-free implementation

This phase adds fail-closed production contracts for OpenSSH control, the
native SoapySDR capture helper, WsprryPi child ownership, narrowly scoped
service restoration, and distinct GPIO and Si5351 quiescence inspection.

No public live command exists. Every external adapter method requires an
ephemeral `RuntimeAuthorization`; transmitter launch additionally requires its
separate RF authorization bit and a digest of the complete resolved session
plan. Committed profile fields cannot satisfy either. Capability reporting
remains read-only and reports live adapters unsupported until a provider and
separately authorized live validation are configured.

`ResolvedCapabilityPlan` binds transport, receiver/transmitter enablement,
named services, quiescence backend, and the overall deadline. It contains no
runtime authorization. `compose_capability_session` validates already acquired
adapter evidence against that plan and can report only `inconclusive`; it does
not execute providers or establish qualification.

## Boundaries

- `OpenSshCapability` records the destination, intended argument vector, and a
  base64url-encoded JSON argument vector consumed by the maintained
  `wspq-remote-exec` helper. It accepts an injected launcher; tests use only
  `SealedFakeLauncher` and do not make a connection. Both local and remote
  command deadlines are explicit, and the remote helper applies its own bound.
- `SoapyCaptureCapability` passes the complete fixed receiver contract to the
  maintained helper and authenticates receiver identity, actual settings,
  clipping threshold, exact-count metadata, and retained CF32 bytes.
  Tests use a fake launcher that creates synthetic output and never loads
  SoapySDR.
- `WsprryPiProcessCapability` accepts only a semantically reconstructed
  application plan bound into the complete session plan. It records ownership
  immediately after process creation, before waiting, and separates application
  exit from timeout, cancellation, disconnect, and cleanup.
- `NarrowServiceCapability` can inspect or change only explicitly allowed
  service names and restores only a state it changed.
- GPIO and Si5351 inspectors are separate read-only contracts. GPIO requires
  the configured pin in its explicit idle direction; Si5351 requires the
  configured bus/address and all required outputs disabled.

`JsonHelperClient`, `HelperServiceProvider`, `HelperGpioProvider`, and
`HelperSi5351Provider` are the production OS/hardware isolation boundary.
`SshOwnedProcessLauncher` uses a remote start/wait/stop helper protocol so the
remote handle is recorded before waiting and cleanup can target that identity.
The installed `wspq-capability-helper --serve --config ...` implements the
matching persistent, versioned JSON-lines server and owns bounded child
processes across start/wait/stop requests. Its identity, plan digest, service
allowlist, provider paths, and provider SHA-256 identities come from the
independently supplied remote configuration rather than the client request.
Provider hashes are rechecked before every invocation. An autonomous watchdog enforces
every owned-child deadline even after a client disconnect. Service, GPIO, and
Si5351 operations are disabled unless an explicit provider and resolved
allowlist are configured.
On a control-response timeout, the transport closes the request stream and
waits for the helper's bounded shutdown cleanup. It does not abruptly kill the
watchdog; an unverifiable cleanup is reported as failure while the helper keeps
ownership until its child deadline.
`SystemctlServiceBackend` is the narrowly scoped Raspberry Pi OS service
provider; GPIO and Si5351 retain injectable read-only providers because this
phase does not access either hardware interface.

The provider protocols and pinned JSON-helper boundary are deliberately small
so operating-system and hardware implementation stays isolated from the
portable coordinator. Unsupported or unconfigured helpers must fail preflight;
they may not be replaced with shell snippets or inferred defaults.

## Validation and next gate

Unit tests inject deterministic providers and cover authorization, paths with
spaces, SSH outcome classification, exact capture count, overflow rejection,
output collision, WsprryPi timeout ownership, service restoration, GPIO state,
and Si5351 identity/output state. Schemas are packaged with the wheel and kept
byte-identical to review copies.

The next gate is a separately authorized, read-only real-capability preflight.
It must pause before the first SSH connection, SDR enumeration/open, service
inspection, GPIO inspection, or I2C transaction. Transmitter launch and RF
remain later and separately authorized.
