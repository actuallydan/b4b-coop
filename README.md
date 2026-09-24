# b4b-coop

Unofficial private co-op for **Back 4 Blood**: up to 4 players, no Warner Bros / Turtle Rock servers.

It takes the game's offline mode, which keeps your progression locally, and turns the host's offline session into a listen
server that friends join directly. A small injected DLL (`dwmapi.dll`) handles hosting, joining and following the
host into missions.

> Unofficial and unaffiliated. For playing a game you own with friends in offline mode. It never touches the
> official online services. Use at your own risk.

## Install (players)
1. Download `b4bcoop.zip` from [Releases](../../releases) and unzip it.
2. Steam → Back 4 Blood → Manage → Browse local files → `Gobi/Binaries/Win64`. Copy the zip's files there
   (`dwmapi.dll`, `b4bcoop.ini`, `Play B4B co-op.cmd`).
3. Edit `b4bcoop.ini`:
   - Host: `host=1`. Your PC must accept UDP 7777 (router port-forward + firewall), or use Tailscale/a VPN.
   - Join: `join=<host IP>`.
4. Windows: start the game with `Play B4B co-op.cmd` (Steam must be running). A normal Steam launch goes
   through Easy Anti-Cheat and the mod won't load.
   Linux / Steam Deck: launch from Steam with launch options `WINEDLLOVERRIDES="dwmapi=n,b" %command%`
5. Start the game, pick **Offline**, go to Fort Hope. Clients connect automatically. When the host starts a mission
   from the war table, everyone follows.

Uninstall: delete the files you copied (`dwmapi.dll`, `b4bcoop.ini`, `Play B4B co-op.cmd`).

## Status
Working: hosting, joining Fort Hope, following into missions, taking over bot slots, keeping your own deck.
Not yet: syncing each player's own progression to the host, in-game join UI, Steam P2P (no port forwarding).
Tested on Windows and on Linux (Proton). Supports the current Steam build only; the DLL checks the build and does nothing on a mismatch.

## Development
See `CLAUDE.md` (layout, setup, progress) and `docs/NOTES.md` (engine findings).
`tools/fetch-deps.sh` then `native/build.sh` (cross-compiles on Linux with zig) → `launch/package.sh` → `dist/b4bcoop.zip`.
