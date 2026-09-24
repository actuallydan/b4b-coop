#!/usr/bin/env bash
# Launch B4B under Proton directly (no EAC bootstrapper), with UE4SS's dwmapi proxy loaded.
# Steam must be running. Extra args are passed to the game.
set -euo pipefail
steam="$HOME/.local/share/Steam"
game="${B4B_DIR:-$steam/steamapps/common/Back 4 Blood}"
proton="${PROTON:-$steam/steamapps/common/Proton - Experimental/proton}"
export STEAM_COMPAT_DATA_PATH="$steam/steamapps/compatdata/924970"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$steam"
export SteamAppId=924970 SteamGameId=924970
export WINEDLLOVERRIDES="dwmapi=n,b${WINEDLLOVERRIDES:+;$WINEDLLOVERRIDES}"
cd "$game/Gobi/Binaries/Win64"
# Stops steam_api from relaunching the game through the Steam client.
echo 924970 > steam_appid.txt
exec "$proton" run ./Back4Blood.exe -log "$@"
