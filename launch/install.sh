#!/usr/bin/env bash
# Install the b4bcoop agent (native/out/dwmapi.dll) into the game. Removes UE4SS if present.
# Steam launch options must contain: WINEDLLOVERRIDES="dwmapi=n,b" %command%
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
game="${B4B_DIR:-$HOME/.local/share/Steam/steamapps/common/Back 4 Blood}"
bin="$game/Gobi/Binaries/Win64"
[[ -f "$here/native/out/dwmapi.dll" ]] || "$here/native/build.sh"
rm -rf "$bin/ue4ss"
# rm first: a running game maps the old file, and overwriting it in place would corrupt that mapping
rm -f "$bin/dwmapi.dll"
cp "$here/native/out/dwmapi.dll" "$bin/dwmapi.dll"
echo "installed b4bcoop into $bin"
