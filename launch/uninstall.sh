#!/usr/bin/env bash
# Remove every b4bcoop file from the game folder (the README's "Remove" list, plus UE4SS and dev leftovers).
set -euo pipefail
game="${B4B_DIR:-$HOME/.local/share/Steam/steamapps/common/Back 4 Blood}"
bin="$game/Gobi/Binaries/Win64"
rm -rf "$bin/ue4ss" "$bin/dwmapi.dll" "$bin/X3DAudio1_7.dll" "$game/xinput1_3.dll" "$bin"/b4bcoop*.log \
  "$bin/b4bcoop.ini" "$bin/b4bcoop-bans.txt" "$bin/steam_appid.txt" "$bin/Play B4B co-op.cmd" \
  "$game/b4bcoop-README.txt" "$game/b4bcoop-COMMANDS.txt" "$game/b4bcoop-LICENSE.txt" "$game/b4bcoop-addons"
echo "removed b4bcoop/UE4SS from $game"
