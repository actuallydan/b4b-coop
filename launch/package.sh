#!/usr/bin/env bash
# Build the player zip. Its layout mirrors the game folder, so installing = copying its contents into the folder
# Steam's "Browse local files" opens (docs/investigations/launch.md):
#   xinput1_3.dll                         Windows: lets a normal Steam "Play" skip the EAC bootstrapper
#   Gobi/Binaries/Win64/X3DAudio1_7.dll   the mod (Windows and Linux/Steam Deck, no launch options)
#   Gobi/Binaries/Win64/b4bcoop.ini       settings
#   b4bcoop-README.txt
# Also dist/b4bcoop-legacy.zip (old dwmapi.dll + launcher .cmd), kept until the Windows flow is verified.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
"$root/native/build.sh" >/dev/null
dist="$root/dist"; out="$dist/b4bcoop"; legacy="$dist/b4bcoop-legacy"
rm -rf "$out" "$legacy"; mkdir -p "$out/Gobi/Binaries/Win64" "$legacy"
cp "$root/native/out/xinput1_3.dll" "$out/"
cp "$root/native/out/X3DAudio1_7.dll" "$out/Gobi/Binaries/Win64/"
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

UNINSTALL: delete xinput1_3.dll (game folder), and X3DAudio1_7.dll + b4bcoop.ini (Gobi\Binaries\Win64).
Both DLL names are ordinary Windows components the game looks for in its own folder first; ours load the
real ones from the system folder, so sound and controllers work as usual.
Upgrading from an older b4bcoop: delete Gobi\Binaries\Win64\dwmapi.dll and "Play B4B co-op.cmd", and on
Linux remove the WINEDLLOVERRIDES launch option.
To play online with Easy Anti-Cheat without uninstalling: add  -b4bcoop=off  to the launch options.
Log files for troubleshooting: Gobi\Binaries\Win64\b4bcoop-*.log
TXT
sed -i 's/$/\r/' "$out/b4bcoop-README.txt"

# legacy layout: dwmapi.dll + Windows launcher, Linux needs WINEDLLOVERRIDES="dwmapi=n,b" %command%
cp "$root/native/out/dwmapi.dll" "$ini" "$legacy/"
printf '%s\r\n' '@echo off' \
  'rem b4bcoop: start Back 4 Blood directly (no EAC bootstrapper) so the mod loads. Steam must be running.' \
  'cd /d "%~dp0"' \
  'echo 924970> steam_appid.txt' \
  'set SteamAppId=924970' \
  'set SteamGameId=924970' \
  'start "" Back4Blood.exe %*' > "$legacy/Play B4B co-op.cmd"

(cd "$out" && rm -f "$dist/b4bcoop.zip" && zip -qr "$dist/b4bcoop.zip" .)
(cd "$dist" && rm -f b4bcoop-legacy.zip && zip -qr b4bcoop-legacy.zip b4bcoop-legacy)
echo "$dist/b4bcoop.zip"
echo "$dist/b4bcoop-legacy.zip (old dwmapi.dll layout)"
