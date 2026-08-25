# Raspberry Pi OS helper deployment validation

This document specifies a deployment procedure; it does not install, enable,
start, or contact a helper. Actual-host validation remains separate.

## Immutable installation model

Use Raspberry Pi OS with Python 3.11 or newer. Build the reviewed sdist and
wheel on a trusted build host, record their SHA-256 values, and transfer the
selected wheel through the maintainer's normal release channel. Create a
dedicated unprivileged account and an administrator-owned directory at
`/opt/wsprrypi-qualification`. Create its virtual environment at
`/opt/wsprrypi-qualification/venv`, install the exact versioned wheel, and make
the environment non-writable by the helper account. Never install from a
mutable source checkout for qualification.

The administrator-owned configuration is
`/etc/wsprrypi-qualification/helper.json`; runtime state is restricted to
`/var/lib/wsprrypi-qualification`. Files should be owned by `root`, readable by
the dedicated helper group only where required, and not writable by the helper
account. Populate every absolute executable path and SHA-256 from the installed
host. The helper and each provider recheck identity before execution.

Copy and customize `deployment/raspberry-pi-os/helper-config.example.json`.
The read-only GPIO provider is
`deployment/raspberry-pi-os/wspq-gpio-inspect`; it is included in source and
wheel artifacts. Deployment must pin and recheck its absolute path, size, and
SHA-256 through the existing provider configuration before invocation. It
accepts only `gpio-inspect` and invokes exactly `/usr/bin/pinctrl get PIN`.
Portable consumers invoke the packaged resource with the selected Python
interpreter; they do not rely on a Unix executable mode surviving a Windows
wheel installation.
Validate it offline with the maintained Python loader before startup. The
helper identity, protocol version, exact service allowlist, provider selection,
GPIO line contract, and Si5351 bus/address/output contract must be
deployment-specific. For live keyed coordination, generate the immutable helper
configuration with
`runtime_helper_config(document, plan_digest_at_startup=True)`; this intentionally
omits `plan_sha256` from the translated helper document while retaining it in
the deployment record. The resolved plan binds the generated configuration
artifact; the launcher then supplies the separately authorized plan digest and
expected helper/configuration hashes at startup. A runtime-bound configuration
containing a plan digest is rejected. A universal receiver authorization never
imports these facts or authorizes transmission.

The deployment document is not passed directly to the persistent helper. After
validation, the maintained `runtime_helper_config()` translator emits the
helper's smaller runtime schema; write that generated document to the configured
runtime path. Deployment-only facts therefore cannot be mistaken for helper
protocol inputs.

When the helper account needs elevation for service changes, add the optional
`executables.service_privilege_wrapper` binding. The translator places its
absolute path and SHA-256 in the runtime helper configuration. The service
backend rechecks it and the pinned `systemctl` executable before every request
and invokes the wrapper with non-interactive arguments only. For `/usr/bin/sudo`,
provision narrow passwordless policy for the exact allowlisted service actions
and verify it with `sudo -n -l`; never grant shell or wildcard service access.

For Raspberry Pi transmitter helpers, also add
`executables.process_privilege_wrapper`. The translator binds it separately as
`process_privilege_wrapper_path` and `process_privilege_wrapper_sha256`. A keyed
resolved plan must independently bind that same wrapper artifact. Process-start
requests name its digest, while the helper constructs the fixed
`/usr/bin/sudo -n -- EXACT_EXECUTABLE EXACT_ARGUMENTS` invocation. Do not place
`sudo` in application argv, permit an interactive prompt, or allow an arbitrary
shell command through sudo policy.

GPIO and Si5351 operations remain read-only. Each provider is a directly pinned
executable. If a Python interpreter launches a fixture or provider script, that
script is independently hashed and rechecked before every call. Provider
programs must not spawn descendants. A provider timeout records cleanup as
unverified and blocks the operation; it never claims process-tree cleanup.

## Optional service template

`wspq-capability-helper.service.in` is a review template, not an installation
script. Inspect the substituted account, paths, sandboxing, and write scope.
Installing or enabling it is an actual-host administrative action requiring
separate authorization. The helper reads JSON lines, owns every child by an
opaque handle, applies hard deadlines, and cleans owned children on graceful
EOF/shutdown. Logs remain in the selected service manager; run evidence belongs
in the immutable harness bundle.

## Verification, upgrade, rollback, and removal

Hardware-free verification consists of building the wheel, installing it in a
temporary virtual environment, importing the package, checking all entry
points and schemas, and validating a configuration against controlled fake
executables. It must not run `systemctl`, GPIO, I2C, SSH, or SoapySDR.

For an upgrade, stop only the explicitly configured helper instance after
confirming it owns no child, retain the previous wheel/configuration hashes,
create a new immutable virtual environment, validate it, then atomically select
the reviewed version. Rollback selects the retained prior environment and its
matching configuration. Removal first verifies no owned child remains, then
removes only the explicit unit/configuration/environment/state paths. These
steps are operator procedures, not commands executed by the validator.

The supported split-host topology can use `wspr5` for the SDR and `wspr4` for
transmission. Before any
connection, read-only inspection, service action, SDR opening, or RF, the
operator must establish that neither host has ongoing work that could be
interrupted. Uncertainty fails closed.

## Temporary run staging

Actual-host orchestration may use `RemoteStage` when the reviewed runtime or a
native helper must be copied for one bounded run instead of installed. The
controller creates a fresh, mode-0700 directory under `/tmp`, copies only an
explicit file list with `scp`, and exposes only controller-generated absolute
paths. Host names, remote names, and stage identifiers are validated before
transport. The stage is a context manager: partial-copy failure, campaign
failure, cancellation, and success all enter the same removal path. Removal is
performed by a fixed Python operation and is successful only when the remote
directory is confirmed absent. An unverified removal is a cleanup failure.

Staging does not itself authorize execution, device access, or RF. The caller
must still bind the staged executable and configuration into the applicable
production plan, install operational cleanup before enabling hardware, and
retain the normal result evidence. Staged content is never installed over a
target checkout or persistent runtime. `tests/unit/test_remote_staging.py`
covers normal removal, partial-copy removal, unsafe targets, and cleanup-failure
precedence; actual-host smoke tests must additionally verify post-run process
absence and hardware quiescence.

Mutable runtime inputs require a stricter boundary than ordinary file staging.
Discover all declared Git roots, retain the tracked source only as provenance,
and create the mutable copy under the fresh deployment namespace with exclusive
creation, bounded permissions, byte/hash verification, and an external working
directory. The finalized source binding, staged binding, protected roots, and
working directory enter the resolved plan before authorization. The helper
rejects pinned mutable inputs without this repository guard, rechecks it before
spawn, and records post-process integrity. Cleanup removes only the owned stage;
it never deletes or repairs a repository path.
