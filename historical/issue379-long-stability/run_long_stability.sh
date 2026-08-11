#!/bin/bash
set -eu
work=/home/pi/issue379-long-stability
ppm=2.353615654
samples=75000000
tone_pid=""
cleanup() {
    if [ -n "$tone_pid" ]; then
        sudo kill -INT "$tone_pid" 2>/dev/null || true
        wait "$tone_pid" 2>/dev/null || true
    fi
    sudo i2cset -y 1 0x60 3 0xFF 2>/dev/null || true
    sudo systemctl start wsprrypi.service soapyremote-server.service
}
trap cleanup EXIT INT TERM
cd "$work"
sudo systemctl stop wsprrypi.service soapyremote-server.service
sleep 2
printf 'session_start_utc=%s\n' "$(date -u +%FT%TZ)"
printf 'session_uptime='; uptime -p
printf 'session_temp='; vcgencmd measure_temp
for band in 2m 30m; do
    if [ "$band" = 2m ]; then center=144515500; else center=10165200; fi
    printf '%s_start_utc=%s\n' "$band" "$(date -u +%FT%TZ)"
    printf '%s_start_temp=' "$band"; vcgencmd measure_temp
    sudo ./long_si5351_tone \
        --i-understand-this-enables-one-attenuated-long-si5351-tone \
        "$band" "$ppm" > "$band-tone.log" 2>&1 &
    tone_pid=$!
    sleep 10
    ./fixed_capture "$center" "$samples" 25 "$band-tone0-300s.cf32" \
        > "$band-capture.log" 2>&1
    capture_rc=$?
    wait "$tone_pid"
    tone_rc=$?
    tone_pid=""
    printf '%s_capture_rc=%s %s_tone_rc=%s\n' "$band" "$capture_rc" "$band" "$tone_rc"
    printf '%s_end_temp=' "$band"; vcgencmd measure_temp
    printf '%s_reg3=' "$band"; sudo i2cget -y 1 0x60 3
done
printf 'session_end_utc=%s\n' "$(date -u +%FT%TZ)"
