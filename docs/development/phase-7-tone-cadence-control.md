# Phase 7 tone cadence control

The current production tone path launches a fresh WsprryPi process at each
nominal RF-on boundary. That boundary measures process lifetime, not RF
lifetime. In the consumed drive-0 run, the receiver detected starts about
0.28--0.31 seconds late and the first process reported 1.910380 seconds of
transmission. The +186.35 Hz carrier offset passed the maintained +/-500 Hz
relative-acquisition gate and is unrelated to this cadence defect.

WsprryPi's maintained WebSocket interface supplies the needed separation. It
serializes `tone_start` and `tone_end`, reports start rejection causes, reports
whether a tone was active and stopped, reports scheduler restoration, and
supports `get_tx_state`. A safe harness design can prewarm one separately owned
process with normal transmission disabled, verify its idle state, and then use
acknowledged control transitions for the three bounded intervals.

The existing WebSocket control is not an authorization boundary and must not be
exposed directly for harness control. Production composition must place it
behind the already authenticated helper/SSH channel, or depend on a separately
reviewed WsprryPi change that binds the candidate endpoint to loopback only.

This is not yet an executable harness capability. Before promotion, the
resolved plan must bind an isolated `Transmit=false` configuration and unique
endpoint; the portable controller must implement bounded RFC 6455 messaging;
and failure-injected tests must prove that rejected, delayed, malformed, or
missing replies cannot extend RF time or bypass cleanup. Arbitrary startup
allowances are rejected because they cannot distinguish initialization from RF
and could exceed the six-second cumulative authorization.
