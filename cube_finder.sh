#!/bin/sh
# Starts the cube radar. Capture needs root: you will get a pkexec (or sudo) password prompt.
exec python3 "$(dirname "$0")/cube_finder.py" "$@"
