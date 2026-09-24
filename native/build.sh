#!/usr/bin/env bash
# Cross-compile with zig cc (docs/investigations/launch.md). Each flavor produces:
#   X3DAudio1_7.dll  the agent, as an X3DAudio1_7 proxy (Gobi/Binaries/Win64; loads on Windows and Proton as is)
#   dwmapi.dll       the same agent as a dwmapi proxy (legacy; Proton needs WINEDLLOVERRIDES="dwmapi=n,b")
#   xinput1_3.dll    Windows launch redirect for the game's root folder (skips the EAC bootstrapper); no dev code
# Flavors:
#   native/build.sh            dev build -> native/out/: TCP command server (127.0.0.1:47112), test commands,
#                              offline=1 sign-in automation (launch/install.sh, launch/multi.sh)
#   native/build.sh --release  player build -> native/out/release/ (defines B4B_RELEASE): none of the above; what
#                              launch/package.sh ships
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
zig="$here/../vendor/zig/zig"
mh="$here/../vendor/minhook"
flavor=dev out="$here/out" defs=()
for a in "$@"; do
  case "$a" in
    --release) flavor=release out="$here/out/release" defs=(-DB4B_RELEASE) ;;
    *) echo "usage: build.sh [--release]" >&2; exit 2 ;;
  esac
done
mkdir -p "$out"
cc() { "$zig" cc -target x86_64-windows-gnu -shared -O2 -g0 -s -Wall -Wno-unused-function "${defs[@]}" "$@"; }
agent() {   # agent <proxy name> <output file>
  cc -I"$mh/include" -I"$here/src" \
    "$here"/src/*.c "$here/proxy/$1.c" "$mh"/src/hook.c "$mh"/src/buffer.c "$mh"/src/trampoline.c "$mh"/src/hde/hde64.c \
    "$here/proxy/$1.def" -lws2_32 -o "$out/$2"
}
agent x3daudio1_7 X3DAudio1_7.dll
agent dwmapi dwmapi.dll
cc "$here/launcher/redirect.c" "$here/proxy/xinput1_3.c" "$here/proxy/xinput1_3.def" -o "$out/xinput1_3.dll"
rm -f "$out"/*.lib "$out"/*.pdb
echo "built $out/{X3DAudio1_7,dwmapi,xinput1_3}.dll ($flavor)"
