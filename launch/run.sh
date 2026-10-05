#!/usr/bin/env bash
# Launch B4B under Proton directly (no EAC bootstrapper). The agent (X3DAudio1_7.dll) needs no DLL override;
# dwmapi=n,b is for the legacy dwmapi.dll agent.
# Steam must be running. Extra args are passed to the game.
# B4B_STEAM=flatpak (launch/lane.sh; lane 2, test prefixes only): Proton and the game run inside the Flatpak Steam's
# own sandbox (flatpak enter), sharing its PID and SysV IPC namespaces and /dev/shm, so the game's Steam
# API reaches the Flatpak client (account dreamsofants) and never the native one; Steam3Master = that client's
# loopback service port (both clients share the network namespace, the native one may own the default 57343). Needs
# that Steam started by launch/flatpak-steam.sh (own IPC namespace); refused otherwise.
set -euo pipefail
steam="$HOME/.local/share/Steam"
source "$(dirname "$0")/lane.sh"   # B4B_LANE=2: the Flatpak game copy
game="${B4B_DIR:-$lane_game}"
guard="$HOME/.local/share/b4b-coop/native-steam-in-use"
if [[ $B4B_STEAM == native && -e $guard ]]; then
  echo "run.sh: $guard exists: Dan is using his native Steam account; test instances must run with B4B_STEAM=flatpak" >&2
  exit 1
fi
if [[ $B4B_STEAM == flatpak ]]; then   # preflight before touching the game folder
  app=com.valvesoftware.Steam fphome="$HOME/.var/app/$app"
  # flatpak finds its instances under the real runtime dir (under gamescope XDG_RUNTIME_DIR is its subdir, instance.sh)
  flatpak=(env XDG_RUNTIME_DIR="${B4B_HOST_RUNTIME_DIR:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}}" flatpak)
  [[ -n ${B4B_PREFIX:-} ]] || { echo "run.sh: B4B_STEAM=flatpak needs a test prefix (launch/instance.sh)" >&2; exit 1; }
  [[ $game == "$fphome/"* ]] || { echo "run.sh: B4B_STEAM=flatpak: $game is not in the Flatpak Steam's folder" >&2; exit 1; }
  # Flathub's Steam shares the host's SysV IPC namespace (shared=ipc), which the native Steam uses too: a game that
  # shares it can register with the NATIVE client (Dan's account). Only a Flatpak Steam started by
  # launch/flatpak-steam.sh (own IPC namespace) is accepted, and the game runs in exactly that namespace.
  st=$("$(dirname "$0")/flatpak-steam.sh" status) || {
    echo "run.sh: B4B_STEAM=flatpak refused: $st; (re)start it with launch/flatpak-steam.sh start" \
         "(docs/investigations/flatpak-steam.md)" >&2; exit 1; }
  parent=$(sed -E 's/^flatpak steam: pid ([0-9]+),.*/\1/' <<<"$st") ns=$(readlink "/proc/$parent/ns/ipc")
  service=$(sed -nE 's/.*, service (127\.0\.0\.1:[0-9]+),.*/\1/p' <<<"$st")
  [[ -n $service ]] || { echo "run.sh: B4B_STEAM=flatpak: no service port for the Flatpak Steam ($st)" >&2; exit 1; }
  # The game joins that sandbox with `flatpak enter` (all its namespaces). NOT through Steam's LaunchAlongsideSteam
  # D-Bus service: the native Steam registers the same well-known name, so it may lead to the NATIVE client.
  instance=
  while read -r i c; do [[ $(readlink "/proc/$c/ns/ipc" 2>/dev/null) == "$ns" ]] && { instance=$i; break; }
  done < <("${flatpak[@]}" ps --columns=instance,child-pid,application | awk -v a=$app '$3==a {print $1, $2}')
  [[ -n $instance ]] || { echo "run.sh: B4B_STEAM=flatpak: no Flatpak instance in the Steam client's IPC namespace" >&2; exit 1; }
  inside=$("${flatpak[@]}" enter "$instance" sh -c 'readlink /proc/self/ns/ipc; test -d "$1" && echo prefix' sh "$B4B_PREFIX" 2>&1 || true)
  if [[ $inside != "$ns"$'\n'prefix ]]; then
    echo "run.sh: B4B_STEAM=flatpak refused: flatpak enter $instance is not in the Steam client's IPC namespace or can't" \
         "see $B4B_PREFIX (got: ${inside//$'\n'/ }); launch/flatpak-steam.sh start" >&2; exit 1
  fi
fi
[[ -z ${B4B_DIR:-} && ( $B4B_LANE == 2 || -d $lane_lock ) ]] && "$(dirname "$0")/lane-restore.sh" backup
cd "$game/Gobi/Binaries/Win64"
# Stops steam_api from relaunching the game through the Steam client.
echo 924970 > steam_appid.txt
export SteamAppId=924970 SteamGameId=924970
export WINEDLLOVERRIDES="dwmapi=n,b${WINEDLLOVERRIDES:+;$WINEDLLOVERRIDES}"

if [[ $B4B_STEAM == flatpak ]]; then
  # Inside the Flatpak Steam's own sandbox (`flatpak enter`: same PID/IPC/mount namespaces and /dev/shm as its
  # `steam`): ~ there is $fphome (its Steam at ~/.local/share/Steam), the test prefix is exposed at its host path.
  # flatpak enter starts with an empty environment: Steam's own (from its process) plus ours.
  sgame="$HOME${game#"$fphome"}" ssteam="$HOME/.local/share/Steam"
  # Steam3Master: the game's steamclient.so otherwise connects to 127.0.0.1:57343, which belongs to whichever
  # Steam client started first (flatpak-steam.sh); the native one drops it and the game dies in ~6 s
  envs=(STEAM_COMPAT_DATA_PATH="$B4B_PREFIX" STEAM_COMPAT_CLIENT_INSTALL_PATH="$ssteam" Steam3Master="$service")
  if [[ -n ${GAMESCOPE_WAYLAND_DISPLAY:-} ]]; then
    # under B4B_GPU's gamescope (launch/instance.sh; its sockets in $XDG_RUNTIME_DIR = <runtime>/b4b-lane2): its
    # Vulkan WSI layer (Flatpak extension org.freedesktop.Platform.VulkanLayer.gamescope//25.08) presents through
    # gamescope's Wayland socket; X11 = gamescope's Xwayland (abstract socket: Steam's sandbox shares the network ns)
    envs+=(GAMESCOPE_WAYLAND_DISPLAY="$XDG_RUNTIME_DIR/$GAMESCOPE_WAYLAND_DISPLAY"
           XDG_RUNTIME_DIR="${B4B_HOST_RUNTIME_DIR:-/run/user/$(id -u)}" DISPLAY="$DISPLAY")
  fi
  while IFS= read -r kv; do envs+=("$kv"); done < <(env | grep -E \
    '^(B4B_|VKD3D_|DXVK_|PROTON_|WINE|GAMESCOPE_LIMITER_FILE=|ENABLE_GAMESCOPE_WSI=|STEAM_GAME_DISPLAY_|SteamAppId=|SteamGameId=)')
  mapfile -d '' -t senv < <(grep -zv '^WAYLAND_DISPLAY=' "/proc/$parent/environ")   # no desktop Wayland for the game
  exec "${flatpak[@]}" enter "$instance" env -i "${senv[@]}" "${envs[@]}" sh -c 'cd "$1" && shift && exec "$@"' sh \
    "$sgame/Gobi/Binaries/Win64" "${B4B_FLATPAK_PROTON:-$ssteam/steamapps/common/Proton - Experimental/proton}" \
    run ./Back4Blood.exe -log "$@"
fi

# Steam3Master (the same port race the other way round, flatpak-steam.sh): when the Flatpak Steam started first it owns
# 127.0.0.1:57343 and a native game would reach it and die ~6 s in ("fatal stalled cross-thread pipe"); give the game
# the native client's own service port (57343 if it has it, else its first loopback listener by fd)
if [[ -z ${Steam3Master:-} ]]; then
  np=$(pgrep -u "$(id -u)" -f "^$steam/ubuntu12_32/steam( |$)" | head -1) || true
  if [[ -z $np ]] && pgrep -u "$(id -u)" -f "/com.valvesoftware.Steam/.*/ubuntu12_32/steam( |$)" >/dev/null; then
    echo "run.sh: the native Steam client is not running, only the Flatpak one: the game would reach the Flatpak client" \
         "(wrong account) and die; start the native Steam first" >&2
    exit 1
  fi
  if [[ -n $np ]]; then
    svc=$(ss -ltnpH src 127.0.0.1 2>/dev/null | sed -nE "s/.* 127\.0\.0\.1:([0-9]+) .*pid=$np,fd=([0-9]+).*/\2 \1/p" |
      sort -n | awk '$2 == 57343 {f=$2} NR == 1 {l=$2} END {p = f ? f : l; if (p) print "127.0.0.1:" p}')
    [[ -n $svc && $svc != 127.0.0.1:57343 ]] && export Steam3Master="$svc"
  fi
fi
proton="${PROTON:-$steam/steamapps/common/Proton - Experimental/proton}"
# B4B_PREFIX: alternate compatdata dir (isolated test prefixes, see launch/multi.sh)
export STEAM_COMPAT_DATA_PATH="${B4B_PREFIX:-$steam/steamapps/compatdata/924970}"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$steam"
exec "$proton" run ./Back4Blood.exe -log "$@"
