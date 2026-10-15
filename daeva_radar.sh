#!/bin/sh
# Starts Daeva Radar. Capture needs root: you will get a pkexec (or sudo) password prompt.
exec python3 "$(dirname "$0")/daeva_radar.py" "$@"
