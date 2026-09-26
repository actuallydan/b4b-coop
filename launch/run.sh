#!/usr/bin/env bash
# Launch B4B under Proton directly (no EAC bootstrapper). The agent (X3DAudio1_7.dll) needs no DLL override;
# dwmapi=n,b is for the legacy dwmapi.dll agent.
# Steam must be running. Extra args are passed to the game.
# B4B_STEAM=flatpak (launch/lane.sh; lane 2, test prefixes only): Proton and the game run inside the running Flatpak
# Steam's sandbox, sharing its PID namespace and /dev/shm, so the game's Steam API reaches the Flatpak client
# (account dreamsofants) and never the native one (whose files the sandbox can't even see).
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
  [[ -n ${B4B_PREFIX:-} ]] || { echo "run.sh: B4B_STEAM=flatpak needs a test prefix (launch/instance.sh)" >&2; exit 1; }
  [[ $game == "$fphome/"* ]] || { echo "run.sh: B4B_STEAM=flatpak: $game is not in the Flatpak Steam's folder" >&2; exit 1; }
  # the Flatpak Steam client: the sandbox whose command is `steam` (not its webhelper/pressure-vessel sub-sandboxes)
  parent=
  for p in $(flatpak ps --columns=child-pid,application | awk -v a=$app '$2==a {print $1}'); do
    tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null | grep -qE -- ' -- steam( |$)' && { parent=$p; break; }
  done
  [[ -n $parent ]] || { echo "run.sh: the Flatpak Steam client isn't running (setsid flatpak run $app -silent &)" >&2; exit 1; }
  # Flathub's Steam shares the host's SysV IPC namespace (shared=ipc), which the native Steam uses too: a game started
  # this way registered with the NATIVE client (Dan's account), not the Flatpak one (docs/investigations/flatpak-steam.md).
  if [[ $(readlink "/proc/$parent/ns/ipc") == "$(readlink /proc/self/ns/ipc)" ]]; then
    echo "run.sh: B4B_STEAM=flatpak refused: the Flatpak Steam shares the host's IPC namespace, so the game can reach the" \
         "native Steam client (docs/investigations/flatpak-steam.md)" >&2
    exit 1
  fi
fi
[[ -z ${B4B_DIR:-} && ( $B4B_LANE == 2 || -d $lane_lock ) ]] && "$(dirname "$0")/lane-restore.sh" backup
cd "$game/Gobi/Binaries/Win64"
# Stops steam_api from relaunching the game through the Steam client.
echo 924970 > steam_appid.txt
export SteamAppId=924970 SteamGameId=924970
export WINEDLLOVERRIDES="dwmapi=n,b${WINEDLLOVERRIDES:+;$WINEDLLOVERRIDES}"

if [[ $B4B_STEAM == flatpak ]]; then
  # inside the sandbox $HOME is $fphome (its Steam at ~/.local/share/Steam); the test prefix is exposed at its host path
  sgame="$HOME${game#"$fphome"}" ssteam="$HOME/.local/share/Steam"
  envs=(--env=STEAM_COMPAT_DATA_PATH="$B4B_PREFIX" --env=STEAM_COMPAT_CLIENT_INSTALL_PATH="$ssteam")
  # under B4B_GPU's gamescope (launch/instance.sh): its Vulkan WSI layer (Flatpak extension
  # org.freedesktop.Platform.VulkanLayer.gamescope//25.08) presents through gamescope's own Wayland socket
  for f in "${GAMESCOPE_WAYLAND_DISPLAY:-}" "$(basename "${GAMESCOPE_LIMITER_FILE:-}")"; do
    [[ -n $f && -e ${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/$f ]] && envs+=(--filesystem="xdg-run/$f")
  done
  while IFS='=' read -r k v; do envs+=(--env="$k=$v"); done < <(env | grep -E '^(B4B_|VKD3D_|DXVK_|PROTON_|WINE|SteamAppId=|SteamGameId=)')
  exec flatpak run --parent-pid="$parent" --parent-share-pids --filesystem="$B4B_PREFIX" --cwd="$sgame/Gobi/Binaries/Win64" \
    "${envs[@]}" --command="${B4B_FLATPAK_PROTON:-$ssteam/steamapps/common/Proton - Experimental/proton}" $app \
    run ./Back4Blood.exe -log "$@"
fi

proton="${PROTON:-$steam/steamapps/common/Proton - Experimental/proton}"
# B4B_PREFIX: alternate compatdata dir (isolated test prefixes, see launch/multi.sh)
export STEAM_COMPAT_DATA_PATH="${B4B_PREFIX:-$steam/steamapps/compatdata/924970}"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$steam"
exec "$proton" run ./Back4Blood.exe -log "$@"
