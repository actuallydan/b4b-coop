#!/usr/bin/env bash
# Exclusive use of the shared game install for live tests (several agents/sessions on one machine).
#   launch/gamelock.sh acquire <owner>   # waits (polls every 30s) until free; stale after 3h
#   launch/gamelock.sh release <owner>
#   launch/gamelock.sh status
# Hold it from before `launch/install.sh` of a test build until after multi-stop.sh + reinstalling main's build.
set -euo pipefail
L=/tmp/b4b-game.lock
case "${1:-status}" in
  acquire)
    owner="${2:?owner}"
    while ! mkdir "$L" 2>/dev/null; do
      if [[ -f "$L/owner" ]] && (( $(date +%s) - $(stat -c %Y "$L/owner") > 10800 )); then
        echo "stale lock from $(cat "$L/owner"), taking it"; rm -rf "$L"; continue
      fi
      echo "game busy: $(cat "$L/owner" 2>/dev/null || echo '?'), waiting"; sleep 30
    done
    echo "$owner $(date -Is)" > "$L/owner"; echo "acquired by $owner" ;;
  release)
    owner="${2:?owner}"
    if [[ -f "$L/owner" && "$(cut -d' ' -f1 "$L/owner")" == "$owner" ]]; then rm -rf "$L"; echo released; else echo "not held by $owner"; fi ;;
  touch) [[ -f "$L/owner" ]] && touch "$L/owner" ;;
  status) cat "$L/owner" 2>/dev/null || echo free ;;
esac
