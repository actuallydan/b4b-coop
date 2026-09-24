#!/usr/bin/env bash
# Install the b4bcoop agent into the game (no Steam launch options needed, docs/investigations/launch.md):
#   Gobi/Binaries/Win64/X3DAudio1_7.dll   the agent (loads on Proton and Windows from the game's own folder)
#   xinput1_3.dll (game root)             Windows launch redirect past the EAC bootstrapper (inert under Proton)
# `install.sh --legacy`: the old dwmapi.dll agent instead (Proton needs WINEDLLOVERRIDES="dwmapi=n,b" %command%).
# Removes the other variant and UE4SS if present.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
game="${B4B_DIR:-$HOME/.local/share/Steam/steamapps/common/Back 4 Blood}"
bin="$game/Gobi/Binaries/Win64"
out="$here/native/out"
[[ -f $out/X3DAudio1_7.dll && -f $out/dwmapi.dll && -f $out/xinput1_3.dll ]] || "$here/native/build.sh"
rm -rf "$bin/ue4ss"
# rm first: a running game maps the old file, and overwriting it in place would corrupt that mapping
rm -f "$bin/dwmapi.dll" "$bin/X3DAudio1_7.dll" "$game/xinput1_3.dll"
if [[ ${1:-} == --legacy ]]; then
  cp "$out/dwmapi.dll" "$bin/dwmapi.dll"
  echo "installed b4bcoop (legacy dwmapi.dll) into $bin"
else
  cp "$out/X3DAudio1_7.dll" "$bin/X3DAudio1_7.dll"
  cp "$out/xinput1_3.dll" "$game/xinput1_3.dll"
  echo "installed b4bcoop into $bin (+ xinput1_3.dll launch redirect in $game)"
fi
