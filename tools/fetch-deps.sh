#!/usr/bin/env bash
# Fetch build/tooling dependencies into vendor/ (not committed): zig (cross-compiler), MinHook, Dear ImGui, embeddable
# Windows Python (runs inside the game's Proton prefix for memory tooling). Also creates the host .venv.
#   tools/fetch-deps.sh          everything (development)
#   tools/fetch-deps.sh --build  only what native/build.sh needs: zig + MinHook + ImGui (CI)
# zig, MinHook and ImGui are pinned (version + sha256, commit; ImGui also the sha256 of the files the build compiles)
# so every build uses the same compiler and libraries.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
v="$root/vendor"; mkdir -p "$v"
ZIG_VERSION=0.15.2
ZIG_SHA256=02aa270f183da276e5b5920b1dac44a63f1a49e55050ebde3aecc9eb82f93239   # zig-x86_64-linux-0.15.2.tar.xz
MINHOOK_COMMIT=8af6b4acae5a9388fd742b56fa79ece89d96f823                          # TsudaKageyu/minhook
IMGUI_COMMIT=f1cc2ae15e53a861a874c3034aae6798fde194ab                            # ocornut/imgui v1.92.9b (MIT)
IMGUI_SHA256=3104b07fe14084b78d567321434d7975616c82da26ecab54bb986cb826b928cf    # the sources below, concatenated
IMGUI_FILES="imgui.h imgui_internal.h imgui.cpp imgui_draw.cpp imgui_tables.cpp imgui_widgets.cpp imconfig.h imstb_rectpack.h
  imstb_textedit.h imstb_truetype.h backends/imgui_impl_dx12.h backends/imgui_impl_dx12.cpp backends/imgui_impl_win32.h
  backends/imgui_impl_win32.cpp"
build_only=0; [[ ${1:-} == --build ]] && build_only=1

if [[ ! -x "$v/zig/zig" ]]; then
  tmp=$(mktemp -d)
  curl -sSfLo "$tmp/zig.tar.xz" "https://ziglang.org/download/$ZIG_VERSION/zig-x86_64-linux-$ZIG_VERSION.tar.xz"
  echo "$ZIG_SHA256  $tmp/zig.tar.xz" | sha256sum -c --quiet -
  tar xJf "$tmp/zig.tar.xz" -C "$v" && rm -rf "$tmp"
  mv "$v/zig-x86_64-linux-$ZIG_VERSION" "$v/zig"
fi
[[ $("$v/zig/zig" version) == "$ZIG_VERSION" ]] || { echo "vendor/zig is not zig $ZIG_VERSION" >&2; exit 1; }

if [[ ! -d "$v/minhook" ]]; then
  git init -q "$v/minhook"
  git -C "$v/minhook" fetch -q --depth 1 https://github.com/TsudaKageyu/minhook.git "$MINHOOK_COMMIT"
  git -C "$v/minhook" checkout -q FETCH_HEAD
fi
[[ $(git -C "$v/minhook" rev-parse HEAD) == "$MINHOOK_COMMIT" ]] || echo "warning: vendor/minhook is not at the pinned commit $MINHOOK_COMMIT" >&2

if [[ ! -d "$v/imgui" ]]; then   # the `~` overlay (native/src/overlay.cpp, #26)
  git init -q "$v/imgui"
  git -C "$v/imgui" fetch -q --depth 1 https://github.com/ocornut/imgui.git "$IMGUI_COMMIT"
  git -C "$v/imgui" checkout -q FETCH_HEAD
fi
[[ $(cd "$v/imgui" && cat $IMGUI_FILES | sha256sum | cut -d' ' -f1) == "$IMGUI_SHA256" ]] ||
  { echo "vendor/imgui does not match the pinned sources ($IMGUI_COMMIT)" >&2; exit 1; }

if [[ $build_only == 0 ]]; then
  if [[ ! -x "$v/winpy/python.exe" ]]; then
    tmp=$(mktemp -d); curl -sSfLo "$tmp/py.zip" https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip
    mkdir -p "$v/winpy" && unzip -q "$tmp/py.zip" -d "$v/winpy" && rm -rf "$tmp"
  fi
  [[ -d "$root/.venv" ]] || { python3 -m venv "$root/.venv" && "$root/.venv/bin/pip" -q install capstone pefile; }
fi
echo "deps ready in $v"
