#!/usr/bin/env bash
# Screenshot local test instance N (window labelled "B4B #N" by multi.sh): launch/shot.sh N out.png [scale%]
set -euo pipefail
id=$(wmctrl -l | awk -v t="B4B #$1" 'index($0, t) {print $1; exit}')
[[ -n $id ]] || { echo "no window labelled B4B #$1" >&2; exit 1; }
import -window "$id" -resize "${3:-50}%" "$2"
