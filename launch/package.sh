#!/usr/bin/env bash
# Build a zip for other players: dwmapi.dll + b4bcoop.ini template + README.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
"$root/native/build.sh" >/dev/null
out="$root/dist/b4bcoop"; rm -rf "$out"; mkdir -p "$out"
cp "$root/native/out/dwmapi.dll" "$out/"
cat > "$out/b4bcoop.ini" <<'INI'
; b4bcoop settings. Pick ONE of these (remove the leading ';' to enable a line).
; Host: your offline Fort Hope becomes a server others can join (UDP 7777 must be reachable).
;host=1
; Join: when you reach offline Fort Hope, connect to the host's IP.
;join=HOST.IP.GOES.HERE
INI
cat > "$out/README.txt" <<'TXT'
b4bcoop - private Back 4 Blood co-op (offline mode + listen server). No WB/Turtle Rock servers.

INSTALL
1. Steam > Back 4 Blood > right click > Manage > Browse local files.
2. Open Gobi\Binaries\Win64 and copy dwmapi.dll and b4bcoop.ini there (next to Back4Blood.exe).
3. Edit b4bcoop.ini: to join a friend, set   join=<their IP>   (remove the ';').
4. Linux/Steam Deck only: Steam > Back 4 Blood > Properties > Launch Options:
       WINEDLLOVERRIDES="dwmapi=n,b" %command%
   (Windows needs no launch option.)

PLAY
- Start the game, choose to play OFFLINE, and go to Fort Hope. You'll connect to the host automatically
  (it retries every 20 seconds until the host is up).
- When the host starts a mission from the war table, you follow automatically.

UNINSTALL: delete dwmapi.dll and b4bcoop.ini from Gobi\Binaries\Win64 (and the launch option on Linux).
Log file for troubleshooting: Gobi\Binaries\Win64\b4bcoop-<number>.log
TXT
(cd "$root/dist" && rm -f b4bcoop.zip && zip -qr b4bcoop.zip b4bcoop)
echo "$root/dist/b4bcoop.zip"
