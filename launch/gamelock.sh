#!/usr/bin/env bash
# Exclusive use of the shared game install for live tests (several agents/sessions on one machine).
#   launch/gamelock.sh acquire <owner>   # waits (polls every 30s) until free; stale after 3h
#   launch/gamelock.sh release <owner>
#   launch/gamelock.sh status
# Hold it from before `launch/install.sh` of a test build until after multi-stop.sh + reinstalling main's build.
# Lanes (launch/lane.sh): two independent installs, each with its own lock; B4B_LANE picks one for every script.
#   B4B_LANE=1 (default): native Steam game, /tmp/b4b-game.lock, prefixes/test<n>, game port 7787, agents 47112+
#   B4B_LANE=2: Flatpak Steam game copy, /tmp/b4b-game-lane2.lock, prefixes/lane2/test<n>, 7887, agents 47140+,
#     windows "B4B L2 #n". That folder holds Dan's second account's player build: acquire backs it up
#     (launch/lane-restore.sh; also waits while that account's game is running), release stops lane-2 test
#     instances and restores it byte-identically (no reinstall of main's build needed on lane 2).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
source "$here/lane.sh"
L=$lane_lock
case "${1:-status}" in
  acquire)
    owner="${2:?owner}"
    while ! mkdir "$L" 2>/dev/null; do
      if [[ -f "$L/owner" ]] && (( $(date +%s) - $(stat -c %Y "$L/owner") > 10800 )); then
        echo "stale lock from $(cat "$L/owner"), taking it"; rm -rf "$L"; continue
      fi
      echo "game busy: $(cat "$L/owner" 2>/dev/null || echo '?'), waiting"; sleep 30
    done
    echo "$owner $(date -Is)" > "$L/owner"
    if [[ $B4B_LANE == 2 ]]; then
      while p=$("$here/lane-restore.sh" busy); do echo "lane 2: the Flatpak game is running (pids $p), waiting"; sleep 30; done
      "$here/lane-restore.sh" backup
    fi
    echo "acquired by $owner" ;;
  release)
    owner="${2:?owner}"
    if [[ -f "$L/owner" && "$(cut -d' ' -f1 "$L/owner")" == "$owner" ]]; then
      if [[ $B4B_LANE == 2 ]]; then "$here/multi-stop.sh" >/dev/null; "$here/lane-restore.sh" restore; fi
      rm -rf "$L"; echo released
    else echo "not held by $owner"; fi ;;
  touch) [[ -f "$L/owner" ]] && touch "$L/owner" ;;
  status) cat "$L/owner" 2>/dev/null || echo free ;;
esac
