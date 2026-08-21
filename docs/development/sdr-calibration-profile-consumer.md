# Frozen SDR calibration-profile consumer

The harness consumes the customer-facing native SDR Calibration Profile as an
external, versioned interface. The accepted contract is exactly:

- schema name `sdr-calibration-profile`;
- schema version `1.0.0`;
- upstream revision `faae3ea76ee9611e379fa2b3c99fb92bebd48041`; and
- upstream schema SHA-256
  `2a2ef74f783e6962159c41283a70fc5dced70e7cfc2f6ae2eb4bbc5ff52b9930`.

The review-facing schema and packaged runtime copy are byte-identical and
schema-pin tests protect both. The harness does not depend on a sibling
SDR-Calibration checkout at runtime.

## Current boundary

`evaluate-sdr-calibration` is hardware-free. It validates the native profile,
verifies the RFC 8785 payload SHA-256, validates a run-specific application
request, and applies the upstream version-1 evaluation semantics. It records
the indicated error, estimated true frequency, expanded uncertainty, target
offset, reliability quotient, selected segment, and qualification usability.

The application request separately records the observed receiver identity and
effective binding configuration. Exact object equality prevents a profile for
another device, clock, sample rate, bandwidth, driver-applied frequency
correction, firmware, antenna port, tuner path, or binding extension from being
silently reused.

This consumer deliberately does not:

- alter receiver tuning or set a SoapySDR frequency correction;
- substitute receiver calibration for WsprryPi transmitter PPM;
- attach calibration to carrier, CW, WSPR, recorded, or live workflows;
- access an SDR, GPIO, I2C, a service, another host, or RF;
- verify Ed25519 signatures; or
- make a hardware, transmitter, power, or spectral-compliance claim.

Signed profiles fail closed until a reviewed Ed25519 verifier and trust-store
policy exist. Unsigned profiles still require a matching canonical SHA-256.

## Offline command

```text
wsprrypi-qualification evaluate-sdr-calibration PROFILE.json APPLICATION.json
```

Exit status `0` means the application result is `qualification_capable`.
Status `1` is a structurally valid but non-qualification-usable application,
and status `2` is invalid input, unsupported schema/version, failed integrity,
or another contract error.

The current consumer is standalone. Any caller that binds its result into a
recorded or live plan must also bind the exact source profile. Both paths require exactly
profile version `1.0.0`; neither may silently fall back to uncalibrated carrier
offset interpretation when calibration is required.
