#!/usr/bin/env bash
# Cross-compile the agent DLL (dwmapi.dll proxy) with zig cc.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
zig="$here/../vendor/zig/zig"
mh="$here/../vendor/minhook"
mkdir -p "$here/out"
"$zig" cc -target x86_64-windows-gnu -shared -O2 -g0 -s -Wall -Wno-unused-function \
  -I"$mh/include" -I"$here/src" \
  "$here"/src/*.c "$mh"/src/hook.c "$mh"/src/buffer.c "$mh"/src/trampoline.c "$mh"/src/hde/hde64.c \
  -lws2_32 -o "$here/out/dwmapi.dll"
rm -f "$here"/out/*.lib "$here"/out/*.pdb
echo "built $here/out/dwmapi.dll"
