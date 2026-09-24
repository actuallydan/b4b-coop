#!/usr/bin/env bash
# Build a zip for other players: dwmapi.dll + b4bcoop.ini template + README.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
"$root/native/build.sh" >/dev/null
out="$root/dist/b4bcoop"; rm -rf "$out"; mkdir -p "$out"
cp "$root/native/out/dwmapi.dll" "$out/"
# Windows launcher: lives next to Back4Blood.exe and starts it directly (no EAC bootstrapper, so the agent loads).
printf '%s\r\n' '@echo off' \
  'rem b4bcoop: start Back 4 Blood directly (no EAC bootstrapper) so dwmapi.dll loads. Steam must be running.' \
  'cd /d "%~dp0"' \
  'echo 924970> steam_appid.txt' \
  'set SteamAppId=924970' \
  'set SteamGameId=924970' \
  'start "" Back4Blood.exe %*' > "$out/Play B4B co-op.cmd"
cat > "$out/b4bcoop.ini" <<'INI'
; b4bcoop settings. Pick ONE of these (remove the leading ';' to enable a line).
; Host: your offline Fort Hope becomes a server others can join (UDP 7777 must be reachable).
;host=1
; Join: when you reach offline Fort Hope, connect to the host's IP.
;join=HOST.IP.GOES.HERE
; Experimental, no port forwarding: host over Steam's relay network instead (transport=steam), and friends
; join with your Steam ID (17 digits; the host's log says "join steam:7656..."): join=steam:76561198000000000
;transport=steam
; Flashlight toggle key (default L; 0 disables).
;flashlight_key=L
; Host only: allow 5 survivors (default 4).
;teamsize=5
; Blocks all third-party network traffic (Epic/WB/Turtle Rock services) while you play. If something won't
; start or connect, try netguard=off and tell us.
;netguard=block
INI
cat > "$out/README.txt" <<'TXT'
b4bcoop - private Back 4 Blood co-op (offline mode + listen server). No WB/Turtle Rock servers.

INSTALL
1. Steam > Back 4 Blood > right click > Manage > Browse local files.
2. Open Gobi\Binaries\Win64 and copy dwmapi.dll, b4bcoop.ini and "Play B4B co-op.cmd" there
   (next to Back4Blood.exe).
3. Edit b4bcoop.ini: to join a friend, set   join=<their IP>   (remove the ';').
4. Windows: start the game with "Play B4B co-op.cmd" (double-click; Steam must be running). Launching from
   Steam goes through Easy Anti-Cheat, which keeps the mod from loading.
   Linux/Steam Deck: launch from Steam as usual, with Steam > Back 4 Blood > Properties > Launch Options:
       WINEDLLOVERRIDES="dwmapi=n,b" %command%

PLAY
- Start the game, choose to play OFFLINE, and go to Fort Hope. You'll connect to the host automatically
  (it retries every 20 seconds until the host is up).
- When the host starts a mission from the war table, you follow automatically.

UNINSTALL: delete dwmapi.dll, b4bcoop.ini and "Play B4B co-op.cmd" from Gobi\Binaries\Win64 (and the launch option on Linux).
Log file for troubleshooting: Gobi\Binaries\Win64\b4bcoop-<number>.log
TXT
(cd "$root/dist" && rm -f b4bcoop.zip && zip -qr b4bcoop.zip b4bcoop)
echo "$root/dist/b4bcoop.zip"
