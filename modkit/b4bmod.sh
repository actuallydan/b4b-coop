#!/bin/sh
# b4bmod for Linux: runs b4bmod.py with python3.
exec python3 "$(dirname "$0")/b4bmod.py" "$@"
