#!/usr/bin/env bash
# Build the player zip from the player build (native/build.sh --release: no command server, no test commands).
# Its layout mirrors the game folder, so installing = copying its contents into the folder Steam's
# "Browse local files" opens (docs/investigations/launch.md):
#   xinput1_3.dll                         Windows: lets a normal Steam "Play" skip the EAC bootstrapper
#   Gobi/Binaries/Win64/X3DAudio1_7.dll   the mod (Windows and Linux/Steam Deck, no launch options)
#   Gobi/Binaries/Win64/b4bcoop.ini       settings
#   b4bcoop-README.txt, b4bcoop-LICENSE.txt
# Also dist/b4bcoop-legacy.zip (player build of the old dwmapi.dll + launcher .cmd), kept until the Windows flow is
# verified, and dist/SHA256SUMS. Both zips are reproducible. CI runs this on a v* tag (.github/workflows/release.yml).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
"$root/native/build.sh" --release >/dev/null
rel="$root/native/out/release"
dist="$root/dist"; out="$dist/b4bcoop"; legacy="$dist/b4bcoop-legacy"
rm -rf "$out" "$legacy"; mkdir -p "$out/Gobi/Binaries/Win64" "$legacy"
cp "$rel/xinput1_3.dll" "$out/"
cp "$rel/X3DAudio1_7.dll" "$out/Gobi/Binaries/Win64/"
cp "$root/LICENSE" "$out/b4bcoop-LICENSE.txt"
ini="$out/Gobi/Binaries/Win64/b4bcoop.ini"
cat > "$ini" <<'INI'
; b4bcoop settings. Pick ONE of these (remove the leading ';' to enable a line).
; Host: your offline Fort Hope becomes a server others can join (UDP 7777 must be reachable).
;host=1
; Join: when you reach offline Fort Hope, connect to the host's IP.
;join=HOST.IP.GOES.HERE
; Or join over Steam's relay network, no port forwarding (experimental): join=steam:<host's 17-digit Steam ID>
; (a host accepts both; its status/log shows "join me: steam:7656..."). Turn Steam P2P off: steam_p2p=0
;steam_p2p=0
; Flashlight toggle key (default L; 0 disables).
;flashlight_key=L
; Host only: who may join. Default: only your Steam friends. allow_joins=anyone lets in anyone who has your address.
;allow_joins=friends
; Host only: always let these Steam IDs in (17-digit Steam IDs, comma-separated), friends or not.
;allow_steamids=7656119XXXXXXXXXX
; Host only: allow 5 survivors (default 4).
;teamsize=5
; Blocks all third-party network traffic (Epic/WB/Turtle Rock services) while you play. If something won't
; start or connect, try netguard=off and tell us.
;netguard=block
INI
sed -i 's/$/\r/' "$ini"
cat > "$out/b4bcoop-README.txt" <<'TXT'
b4bcoop - private Back 4 Blood co-op (offline mode + listen server). No WB/Turtle Rock servers.

INSTALL (Windows, Linux, Steam Deck - the same steps)
1. Steam > Back 4 Blood > right click > Manage > Browse local files. This opens the game folder
   (the one with Back4Blood.exe, Gobi and EasyAntiCheat in it).
2. Copy everything from this zip into that folder. The zip's Gobi folder merges into the game's
   Gobi folder; nothing of the game is replaced.
3. Edit Gobi\Binaries\Win64\b4bcoop.ini: to join a friend, set   join=<their IP>   (remove the ';').
   To host, set   host=1
4. Press Play in Steam as usual. No launch options needed.

PLAY
- Choose to play OFFLINE and go to Fort Hope. You'll connect to the host automatically
  (it retries every 20 seconds until the host is up).
- When the host starts a mission from the war table, you follow automatically.
- Hosts only accept their Steam friends by default (see allow_joins / allow_steamids in b4bcoop.ini).
- Chat commands: type /help in the chat box.

SAFETY
Unofficial, not affiliated with Turtle Rock Studios or Warner Bros. Games. Offline mode only: it blocks the game's
online services while it runs. Don't use it for online play. No warranty (see b4bcoop-LICENSE.txt).
Source code and how to check this download: https://github.com/actuallydan/b4b-coop

UNINSTALL: delete xinput1_3.dll (game folder), and X3DAudio1_7.dll + b4bcoop.ini (Gobi\Binaries\Win64).
Both DLL names are ordinary Windows components the game looks for in its own folder first; ours load the
real ones from the system folder, so sound and controllers work as usual.
Upgrading from an older b4bcoop: delete Gobi\Binaries\Win64\dwmapi.dll and "Play B4B co-op.cmd", and on
Linux remove the WINEDLLOVERRIDES launch option.
To play online with Easy Anti-Cheat without uninstalling: add  -b4bcoop=off  to the launch options.
Log files for troubleshooting: Gobi\Binaries\Win64\b4bcoop-*.log
TXT
sed -i 's/$/\r/' "$out/b4bcoop-README.txt"

# legacy layout: dwmapi.dll + Windows launcher; Linux needs WINEDLLOVERRIDES="dwmapi=n,b" %command%
cp "$rel/dwmapi.dll" "$ini" "$legacy/"
cp "$root/LICENSE" "$legacy/LICENSE.txt"
printf '%s\r\n' '@echo off' \
  'rem b4bcoop: start Back 4 Blood directly (no EAC bootstrapper) so the mod loads. Steam must be running.' \
  'cd /d "%~dp0"' \
  'echo 924970> steam_appid.txt' \
  'set SteamAppId=924970' \
  'set SteamGameId=924970' \
  'start "" Back4Blood.exe %*' > "$legacy/Play B4B co-op.cmd"

# Reproducible zips: fixed timestamps and file order, no extra attributes.
for d in "$out" "$legacy"; do
  find "$d" -type d -exec chmod 755 {} + ; find "$d" -type f -exec chmod 644 {} +
  find "$d" -exec touch -d '2020-01-01 00:00:00 UTC' {} +
done
rm -f "$dist/b4bcoop.zip" "$dist/b4bcoop-legacy.zip"
(cd "$out" && find . -mindepth 1 | sed 's|^\./||' | LC_ALL=C sort | TZ=UTC zip -qX -@ "$dist/b4bcoop.zip")
(cd "$dist" && find b4bcoop-legacy | LC_ALL=C sort | TZ=UTC zip -qX -@ b4bcoop-legacy.zip)
(cd "$dist" && sha256sum b4bcoop.zip b4bcoop/xinput1_3.dll b4bcoop/Gobi/Binaries/Win64/X3DAudio1_7.dll \
  b4bcoop-legacy.zip b4bcoop-legacy/dwmapi.dll > SHA256SUMS)
echo "$dist/b4bcoop.zip"
echo "$dist/b4bcoop-legacy.zip (old dwmapi.dll layout)"
