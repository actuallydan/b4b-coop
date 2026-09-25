#!/usr/bin/env bash
# Build and install the b4bcoop agent into the game (no Steam launch options needed, docs/investigations/launch.md):
#   Gobi/Binaries/Win64/X3DAudio1_7.dll   the agent (loads on Proton and Windows from the game's own folder)
#   xinput1_3.dll (game root)             Windows launch redirect past the EAC bootstrapper (inert under Proton)
# Removes the other variant and UE4SS if present.
#   launch/install.sh             dev build (command server, test commands): what launch/multi.sh and tools/b4b.py need
#   launch/install.sh --release   player build (what launch/package.sh ships), to test it locally
#   --legacy (with either)        dev only: the old dwmapi.dll agent instead (no longer shipped; Proton needs
#                                 WINEDLLOVERRIDES="dwmapi=n,b" %command%)
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
game="${B4B_DIR:-$HOME/.local/share/Steam/steamapps/common/Back 4 Blood}"
bin="$game/Gobi/Binaries/Win64"
out="$here/native/out" flavor="dev build" legacy=0 build=()
for a in "$@"; do
  case "$a" in
    --release) build=(--release); out="$here/native/out/release"; flavor="player build" ;;
    --legacy) legacy=1 ;;
    *) echo "usage: install.sh [--release] [--legacy]" >&2; exit 2 ;;
  esac
done
"$here/native/build.sh" "${build[@]}" >/dev/null
rm -rf "$bin/ue4ss"
# rm first: a running game maps the old file, and overwriting it in place would corrupt that mapping
rm -f "$bin/dwmapi.dll" "$bin/X3DAudio1_7.dll" "$game/xinput1_3.dll"
if [[ $legacy == 1 ]]; then
  cp "$out/dwmapi.dll" "$bin/dwmapi.dll"
  echo "installed b4bcoop ($flavor, legacy dwmapi.dll) into $bin"
else
  cp "$out/X3DAudio1_7.dll" "$bin/X3DAudio1_7.dll"
  cp "$out/xinput1_3.dll" "$game/xinput1_3.dll"
  echo "installed b4bcoop ($flavor) into $bin (+ xinput1_3.dll launch redirect in $game)"
fi
