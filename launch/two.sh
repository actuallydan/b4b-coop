#!/usr/bin/env bash
# Dev helper: launch a host and a client copy on this machine and label their windows.
here="$(cd "$(dirname "$0")" && pwd)"
"$here/run.sh" >/dev/null 2>&1 &
sleep 20
"$here/run.sh" >/dev/null 2>&1 &
pids=()
for i in $(seq 120); do
  mapfile -t pids < <(pgrep -f '^\./Back4Blood.exe' | sort -n)
  [[ ${#pids[@]} -ge 2 ]] && wmctrl -lp | grep -q " ${pids[1]} .*Back 4 Blood *$" && break
  sleep 1
done
label() { local id; id=$(wmctrl -lp | awk -v p="$1" '$3==p && /Back 4 Blood *$/ {print $1}'); [[ -n $id ]] && wmctrl -i -r "$id" -N "$2 - Back 4 Blood"; }
label "${pids[0]}" "HOST (window 1)"
label "${pids[1]}" "CLIENT (window 2)"
wait
