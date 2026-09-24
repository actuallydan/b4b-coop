#!/usr/bin/env bash
# Cross-compile with zig cc (docs/investigations/launch.md):
#   out/X3DAudio1_7.dll  the agent, as an X3DAudio1_7 proxy (Gobi/Binaries/Win64; loads on Windows and Proton as is)
#   out/dwmapi.dll       the same agent as a dwmapi proxy (legacy; Proton needs WINEDLLOVERRIDES="dwmapi=n,b")
#   out/xinput1_3.dll    Windows launch redirect for the game's root folder (skips the EAC bootstrapper)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
zig="$here/../vendor/zig/zig"
mh="$here/../vendor/minhook"
mkdir -p "$here/out"
cc() { "$zig" cc -target x86_64-windows-gnu -shared -O2 -g0 -s -Wall -Wno-unused-function "$@"; }
agent() {   # agent <proxy name> <output file>
  cc -I"$mh/include" -I"$here/src" \
    "$here"/src/*.c "$here/proxy/$1.c" "$mh"/src/hook.c "$mh"/src/buffer.c "$mh"/src/trampoline.c "$mh"/src/hde/hde64.c \
    "$here/proxy/$1.def" -lws2_32 -o "$here/out/$2"
}
agent x3daudio1_7 X3DAudio1_7.dll
agent dwmapi dwmapi.dll
cc "$here/launcher/redirect.c" "$here/proxy/xinput1_3.c" "$here/proxy/xinput1_3.def" -o "$here/out/xinput1_3.dll"
rm -f "$here"/out/*.lib "$here"/out/*.pdb
echo "built $here/out/{X3DAudio1_7,dwmapi,xinput1_3}.dll"
