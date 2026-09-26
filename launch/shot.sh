#!/usr/bin/env bash
# Screenshot local test instance N (window labelled "B4B #N" by multi.sh): launch/shot.sh N out.png [scale%]
# B4B_LANE=2: windows "B4B L2 #N". B4B_GPU set (headless gamescope, no window): an engine screenshot (`shot`) taken
# through the instance's agent, from its prefix's Saved/Screenshots.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
source "$here/lane.sh"
if [[ -n ${B4B_GPU:-} ]]; then
  dir="${B4B_TEST_ROOT:-$lane_root}/test$1/pfx/drive_c/users/steamuser/AppData/Local/Back4Blood/Steam/Saved/Screenshots/WindowsNoEditor"
  mark=$(mktemp); trap 'rm -f "$mark"' EXIT
  B4B_AGENT=$(( $1 - 1 )) "$here/../.venv/bin/python" "$here/../tools/b4b.py" exec shot >/dev/null
  for _ in $(seq 20); do
    f=$(find "$dir" -name 'ScreenShot*.png' -newer "$mark" 2>/dev/null | sort | tail -1)
    [[ -n $f ]] && break; sleep 0.5
  done
  [[ -n ${f:-} ]] || { echo "no engine screenshot from test$1" >&2; exit 1; }
  sleep 0.5; convert "$f" -resize "${3:-100}%" "$2"; rm -f "$f"; exit 0
fi
id=$(wmctrl -l | awk -v t="$lane_win #$1" 'index($0, t) {print $1; exit}')
[[ -n $id ]] || { echo "no window labelled $lane_win #$1" >&2; exit 1; }
import -window "$id" -resize "${3:-50}%" "$2"
