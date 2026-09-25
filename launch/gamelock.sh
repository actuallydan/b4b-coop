#!/usr/bin/env bash
# Exclusive use of the shared game install for live tests (several agents/sessions on one machine).
#   launch/gamelock.sh acquire <owner>   # waits (polls every 30s) until free; stale after 3h
#   launch/gamelock.sh release <owner>
#   launch/gamelock.sh status
# Hold it from before `launch/install.sh` of a test build until the live test is done.
# Lanes (launch/lane.sh): two independent installs, each with its own lock; B4B_LANE picks one for every script.
#   B4B_LANE=1 (default): native Steam game, /tmp/b4b-game.lock, prefixes/test<n>, game port 7787, agents 47112+
#   B4B_LANE=2: Flatpak Steam game copy, /tmp/b4b-game-lane2.lock, prefixes/lane2/test<n>, 7887, agents 47140+,
#     windows "B4B L2 #n".
# Each lane's folder holds one of Dan's player installs: acquire backs it up (launch/lane-restore.sh; waits while
# the player's own game runs there), release stops the lane's test instances (multi-stop.sh) and restores it
# byte-identically (test logs go to ~/.local/share/b4b-coop/lane<n>-logs/<time>/). No reinstall needed.
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
    while p=$("$here/lane-restore.sh" busy); do echo "lane $B4B_LANE: the player's game is running (pids $p), waiting"; sleep 30; done
    "$here/lane-restore.sh" backup
    echo "acquired by $owner" ;;
  release)
    owner="${2:?owner}"
    if [[ -f "$L/owner" && "$(cut -d' ' -f1 "$L/owner")" == "$owner" ]]; then
      "$here/multi-stop.sh" >/dev/null; "$here/lane-restore.sh" restore
      rm -rf "$L"; echo released
    else echo "not held by $owner"; fi ;;
  touch) [[ -f "$L/owner" ]] && touch "$L/owner" ;;
  status) cat "$L/owner" 2>/dev/null || echo free ;;
esac
