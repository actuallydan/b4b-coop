#!/usr/bin/env bash
# The Flatpak Steam client (account dreamsofants) in the mode B4B_STEAM=flatpak test instances need:
#   launch/flatpak-steam.sh start    # (re)start it with its own SysV IPC namespace + the lane-2 test prefixes in view
#   launch/flatpak-steam.sh stop     # graceful -shutdown
#   launch/flatpak-steam.sh status   # pid, IPC namespace (isolated or not), service port, logged-on account; exit 1
#                                    # unless isolated with a service port
# Why: Flathub's Steam has shared=ipc; steamclient's SysV IPC then reaches the NATIVE client (Dan's account) from any
# sandbox. `flatpak run --unshare=ipc` can't be used: Flatpak 1.18's portal forwards it to every sub-sandbox Steam
# spawns (webhelper, the startup `flatpak-spawn` check) as "--noshare=ipc", which `flatpak run` rejects, and the
# Flathub wrapper then shows the "requires a working D-Bus session bus and flatpak-portal service" dialog. So `start`
# sets the user override shared=!ipc only while the sandbox is created and resets it once Steam is up (Dan's permanent
# overrides stay untouched; the running sandbox keeps its private namespace). Steam gets a minimal environment, not
# the caller's (no tokens from an agent shell in Steam or its games).
# Service port: a game's steamclient.so also reaches its Steam client over loopback TCP at $Steam3Master, default
# 127.0.0.1:57343. Flathub's Steam shares the host's network namespace, so whichever client starts first owns 57343 and
# the other listens on a random port that it only tells the games it launches itself. `status` finds this Steam's
# service port and run.sh passes it to the game as Steam3Master; without it a test game reaches the NATIVE
# client, which drops it ("fatal stalled cross-thread pipe (pipe is disconnected)" ~6 s after start).
# Test games run INSIDE this sandbox via `flatpak enter` (launch/run.sh), never in a new `flatpak run`.
# docs/investigations/flatpak-steam.md.
set -euo pipefail
app=com.valvesoftware.Steam
fphome="$HOME/.var/app/$app"
gsdir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/b4b-lane2"   # headless gamescope sockets of B4B_STEAM=flatpak instances
prefixes="$HOME/.local/share/b4b-coop/prefixes/lane2"

steam_pid() {   # the Flatpak Steam client process (ubuntu12_32/steam inside its sandbox), host pid
  pgrep -u "$(id -u)" -f "^$fphome/.local/share/Steam/ubuntu12_32/steam( |$)" | head -1
}
service_of() {   # the Steam3Master address (ip:port) steam pid $1 listens on: 57343 if it has it, else (57343
  # taken by the other client) its random fallback, the loopback TCP listener it opens first (lowest fd; its other two
  # are HTTP servers). Empty if none.
  ss -ltnpH src 127.0.0.1 2>/dev/null | sed -nE "s/.* 127\.0\.0\.1:([0-9]+) .*pid=$1,fd=([0-9]+).*/\2 \1/p" |
    sort -n | awk '$2 == 57343 {f=$2} NR == 1 {l=$2} END {p = f ? f : l; if (p) print "127.0.0.1:" p}'
}
logged_on() {   # last logon of this client session succeeded, prints the SteamID64
  local a; a=$(tail -20 "$fphome/.local/share/Steam/logs/connection_log.txt" 2>/dev/null |
    awk '/Log session ended/ {id=""} /\[Logged On,.*processing complete/ {match($0, /\[U:1:[0-9]+\]/); id=substr($0, RSTART+5, RLENGTH-6)} END {print id}')
  [[ -n $a ]] && echo $(( 76561197960265728 + a ))
}

case "${1:-status}" in
  status)
    p=$(steam_pid) || true
    [[ -n $p ]] || { echo "flatpak steam: not running"; exit 1; }
    ns=$(readlink "/proc/$p/ns/ipc"); host=$(readlink /proc/1/ns/ipc 2>/dev/null || readlink /proc/self/ns/ipc)
    iso=isolated; [[ $ns == "$host" ]] && iso="SHARED with the host (not usable for test games)"
    svc=$(service_of "$p")
    echo "flatpak steam: pid $p, ipc $ns ($iso), service ${svc:-none}, logged on: $(logged_on || true)"
    [[ $ns != "$host" && -n $svc ]] ;;
  stop)
    [[ -n $(steam_pid) ]] || { echo "not running"; exit 0; }
    timeout 60 flatpak run $app -shutdown >/dev/null 2>&1 || true
    for _ in $(seq 60); do flatpak ps --columns=application | grep -qx $app || { echo stopped; exit 0; }; sleep 1; done
    echo "still running after -shutdown (flatpak kill $app)" >&2; exit 1 ;;
  start)
    [[ -z $(steam_pid) ]] || "$0" stop
    mkdir -p -m 700 "$gsdir"
    had_override=0; [[ -e $HOME/.local/share/flatpak/overrides/$app ]] && had_override=1
    [[ $had_override == 0 ]] || { echo "an override for $app exists; not touching it (flatpak override --user --show $app)" >&2; exit 1; }
    flatpak override --user --unshare=ipc $app
    trap 'flatpak override --user --reset '$app EXIT
    keep=(HOME USER LOGNAME SHELL PATH LANG XDG_RUNTIME_DIR XDG_DATA_DIRS XDG_SESSION_TYPE XDG_CURRENT_DESKTOP
          DBUS_SESSION_BUS_ADDRESS DISPLAY WAYLAND_DISPLAY XAUTHORITY)
    envs=(); for k in "${keep[@]}"; do [[ -n ${!k:-} ]] && envs+=("$k=${!k}"); done
    mark=$(date '+%F %T')
    (cd "$HOME" && env -i "${envs[@]}" setsid -f flatpak run --filesystem="$prefixes" --filesystem="xdg-run/$(basename "$gsdir"):create" \
      $app -silent >/dev/null 2>&1 </dev/null)
    for _ in $(seq 150); do
      p=$(steam_pid) || true
      [[ -n $p ]] && awk -v m="[$mark]" '$0 >= m' "$fphome/.local/share/Steam/logs/connection_log.txt" 2>/dev/null |
        grep -q 'Logged On.*processing complete' && break
      sleep 2
    done
    "$0" status || exit 1
    id=$(logged_on || true)
    [[ -n $id ]] || { echo "flatpak steam didn't log on (login prompt?)" >&2; exit 1; } ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
