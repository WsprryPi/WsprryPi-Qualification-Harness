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
