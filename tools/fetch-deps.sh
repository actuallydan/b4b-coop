#!/usr/bin/env bash
# Fetch build/tooling dependencies into vendor/ (not committed): zig (cross-compiler), MinHook, embeddable
# Windows Python (runs inside the game's Proton prefix for memory tooling). Also creates the host .venv.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
v="$root/vendor"; mkdir -p "$v"
if [[ ! -x "$v/zig/zig" ]]; then
  curl -sSL https://ziglang.org/download/0.15.2/zig-x86_64-linux-0.15.2.tar.xz | tar xJ -C "$v"
  mv "$v/zig-x86_64-linux-0.15.2" "$v/zig"
fi
[[ -d "$v/minhook" ]] || git clone -q --depth 1 https://github.com/TsudaKageyu/minhook.git "$v/minhook"
if [[ ! -x "$v/winpy/python.exe" ]]; then
  tmp=$(mktemp -d); curl -sSLo "$tmp/py.zip" https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip
  mkdir -p "$v/winpy" && unzip -q "$tmp/py.zip" -d "$v/winpy" && rm -rf "$tmp"
fi
[[ -d "$root/.venv" ]] || { python3 -m venv "$root/.venv" && "$root/.venv/bin/pip" -q install capstone pefile; }
echo "deps ready in $v"
