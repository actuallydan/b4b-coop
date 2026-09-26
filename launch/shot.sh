#!/usr/bin/env bash
# Screenshot local test instance N: launch/shot.sh N out.png [scale%]
# Default: the agent's dev `screenshot` (the frame as presented, ~ overlay included, copied in its D3D12 Present
# hook) into the prefix's Saved/Screenshots, then moved to out.png. Falls back to the engine screenshot (`shot`) when
# that fails, and without B4B_GPU (a window exists) to an X11 grab of the window "B4B #N" (B4B_LANE=2: "B4B L2 #N").
# B4B_SHOT=engine|window forces one method.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
source "$here/lane.sh"
dir="${B4B_TEST_ROOT:-$lane_root}/test$1/pfx/drive_c/users/steamuser/AppData/Local/Back4Blood/Steam/Saved/Screenshots/WindowsNoEditor"
windir='C:\users\steamuser\AppData\Local\Back4Blood\Steam\Saved\Screenshots\WindowsNoEditor'
agent() { B4B_AGENT=$(( $1 - 1 )) timeout 25 "$here/../.venv/bin/python" "$here/../tools/b4b.py" "${@:2}" 2>/dev/null || true; }
mode=${B4B_SHOT:-present}
if [[ $mode == present ]]; then
  mkdir -p "$dir"
  name="b4bcoop-shot-$$-$RANDOM.png"
  r=$(agent "$1" screenshot "$windir\\$name")
  if [[ $r == queued* ]]; then
    for _ in $(seq 40); do [[ -f $dir/$name ]] && break; sleep 0.25; done
    if [[ -f $dir/$name ]]; then
      convert "$dir/$name" -resize "${3:-100}%" "$2"; rm -f "$dir/$name"; exit 0
    fi
    echo "shot.sh: test$1: no frame from the Present hook ($(agent "$1" screenshot | tr -d '\n')); engine screenshot" >&2
  else
    echo "shot.sh: test$1: ${r:-no agent answer}; engine screenshot" >&2
  fi
  mode=engine
fi
if [[ $mode == engine && -z ${B4B_GPU:-} && -z ${B4B_SHOT:-} ]]; then mode=window; fi
if [[ $mode == engine ]]; then
  mark=$(mktemp); trap 'rm -f "$mark"' EXIT
  agent "$1" exec shot >/dev/null
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
