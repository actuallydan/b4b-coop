#!/usr/bin/env bash
# Build the mod maker's kit: dist/modkit/b4bcoop-modkit-<version>.zip (+ dist/modkit/SHA256SUMS), reproducible.
# Separate from the player zip (launch/package.sh): players never need it. Sources only, nothing third-party and no
# game files: `b4bmod setup` fetches .NET + UAssetAPI, NuGet brings CUE4Parse and the codecs, the pak
# AES key is built into b4bmod.py (modkit/README.md). Layout (one top folder):
#   b4bcoop-modkit-<version>/README.md, LICENSE, b4bmod.cmd (Windows), b4bmod.sh, b4bmod.py, addon.py, b4bpak.py,
#   b4bmodel.py, skm.py, skmgltf.py, sm.py, upkg.py, blender/*.py, dotnet/b4bmod/ and dotnet/pakx/ (.NET sources),
#   docs/*.md
set -euo pipefail
unset ZIP ZIPOPT
kit="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$kit")"
version=$(sed -n 's/^version=//p' "$root/VERSION")
name="b4bcoop-modkit-$version"
dist="$root/dist/modkit"; out="$dist/$name"
rm -rf "$dist"; mkdir -p "$out/docs" "$out/blender" "$out/dotnet/b4bmod" "$out/dotnet/pakx"

cp "$kit/README.md" "$kit/b4bmod.cmd" "$kit/b4bmod.sh" "$kit"/*.py "$out/"   # b4bmod, addon, b4bpak, mesh tools
cp "$root/LICENSE" "$out/LICENSE"
cp "$kit"/blender/*.py "$out/blender/"
cp "$kit"/docs/*.md "$out/docs/"
cp "$kit"/dotnet/b4bmod/*.cs "$kit"/dotnet/b4bmod/*.csproj "$out/dotnet/b4bmod/"
cp "$kit"/dotnet/pakx/*.cs "$kit"/dotnet/pakx/*.csproj "$out/dotnet/pakx/"

# nothing proprietary or extracted may slip in
if find "$out" -type f ! \( -name '*.py' -o -name '*.md' -o -name '*.cs' -o -name '*.csproj' -o -name '*.cmd' \
        -o -name '*.sh' -o -name LICENSE \) | grep -q .; then
  echo "unexpected file type in the kit:" >&2; find "$out" -type f | grep -vE '\.(py|md|cs|csproj|cmd|sh)$|/LICENSE$' >&2; exit 1
fi
# no unexpected 64-hex-digit string (secret) in the sources; allowed: UAssetAPI's zip SHA-256 and the game's pak AES key
bad=$(grep -rhoiE '[0-9a-f]{64}' "$out" | grep -viE '^(3c044cc871c41e877f76ce42ced31f0d700ae9febe959d35b03a41ded8c4e92f|0208250257E8EA16828509DEBF23D703A5B509FE4F15F33F11BEE4BAB1F97CFD)$' || true)
[[ -z $bad ]] || { echo "an unexpected 64-hex-digit string is in the kit sources; refusing" >&2; exit 1; }

find "$out" -type d -exec chmod 755 {} + ; find "$out" -type f -exec chmod 644 {} +
chmod 755 "$out/b4bmod.sh" "$out/b4bmod.py"
find "$out" -exec touch -d '2020-01-01 00:00:00 UTC' {} +
(cd "$dist" && find "$name" | LC_ALL=C sort | TZ=UTC zip -qX -@ "$name.zip")
(cd "$dist" && sha256sum "$name.zip" > SHA256SUMS)
echo "$dist/$name.zip"
