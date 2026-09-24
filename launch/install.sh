#!/usr/bin/env bash
# Build and install the b4bcoop agent into the game. Removes UE4SS if present.
#   launch/install.sh            dev build (command server, test commands): what launch/multi.sh and tools/b4b.py need
#   launch/install.sh --release  player build (what launch/package.sh ships), to test it locally
# Steam launch options must contain: WINEDLLOVERRIDES="dwmapi=n,b" %command%
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
game="${B4B_DIR:-$HOME/.local/share/Steam/steamapps/common/Back 4 Blood}"
bin="$game/Gobi/Binaries/Win64"
dll="$here/native/out/dwmapi.dll"
case "${1:-}" in
  "") "$here/native/build.sh" >/dev/null ;;
  --release) "$here/native/build.sh" --release >/dev/null; dll="$here/native/out/release/dwmapi.dll" ;;
  *) echo "usage: install.sh [--release]" >&2; exit 2 ;;
esac
rm -rf "$bin/ue4ss"
# rm first: a running game maps the old file, and overwriting it in place would corrupt that mapping
rm -f "$bin/dwmapi.dll"
cp "$dll" "$bin/dwmapi.dll"
echo "installed b4bcoop (${1:-dev build}) into $bin"
