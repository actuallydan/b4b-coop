#!/usr/bin/env bash
# Screenshot local test instance N (window labelled "B4B #N" by multi.sh): launch/shot.sh N out.png [scale%]
# B4B_LANE=2: windows "B4B L2 #N"
set -euo pipefail
source "$(dirname "$0")/lane.sh"
id=$(wmctrl -l | awk -v t="$lane_win #$1" 'index($0, t) {print $1; exit}')
[[ -n $id ]] || { echo "no window labelled $lane_win #$1" >&2; exit 1; }
import -window "$id" -resize "${3:-50}%" "$2"
