#!/usr/bin/env bash
# Cross-compile the agent DLL (dwmapi.dll proxy) with zig cc.
#   native/build.sh            dev build -> native/out/dwmapi.dll: TCP command server (127.0.0.1:47112), test
#                              commands, offline=1 sign-in automation (launch/install.sh, launch/multi.sh)
#   native/build.sh --release  player build -> native/out/release/dwmapi.dll (defines B4B_RELEASE): none of the
#                              above; what launch/package.sh ships
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
"$zig" cc -target x86_64-windows-gnu -shared -O2 -g0 -s -Wall -Wno-unused-function "${defs[@]}" \
  -I"$mh/include" -I"$here/src" \
  "$here"/src/*.c "$mh"/src/hook.c "$mh"/src/buffer.c "$mh"/src/trampoline.c "$mh"/src/hde/hde64.c \
  "$here/dwmapi.def" -lws2_32 -o "$out/dwmapi.dll"
rm -f "$out"/*.lib "$out"/*.pdb
echo "built $out/dwmapi.dll ($flavor)"
