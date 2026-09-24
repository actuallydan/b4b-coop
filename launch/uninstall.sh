#!/usr/bin/env bash
set -euo pipefail
game="${B4B_DIR:-$HOME/.local/share/Steam/steamapps/common/Back 4 Blood}"
bin="$game/Gobi/Binaries/Win64"
rm -rf "$bin/ue4ss" "$bin/dwmapi.dll" "$bin/b4bcoop.log" "$bin/steam_appid.txt"
echo "removed b4bcoop/UE4SS from $bin"
