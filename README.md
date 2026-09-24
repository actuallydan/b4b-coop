# b4b-coop

Unofficial private co-op for **Back 4 Blood**: up to 4 players, no Warner Bros / Turtle Rock servers.

It takes the game's offline mode, which keeps your progression locally, and turns the host's offline session into a listen
server that friends join directly. A small injected DLL handles hosting, joining and following the
host into missions.

> Unofficial and unaffiliated. For playing a game you own with friends in offline mode. It never touches the
> official online services. Use at your own risk.

## Install (players)
Same steps on Windows, Linux and Steam Deck:
1. Download `b4bcoop.zip` from [Releases](../../releases).
2. Steam → Back 4 Blood → Manage → Browse local files. Copy everything in the zip into that folder (its `Gobi`
   folder merges with the game's; no game file is replaced). You get `xinput1_3.dll` next to the game's
   `Back4Blood.exe`, and `X3DAudio1_7.dll` + `b4bcoop.ini` in `Gobi/Binaries/Win64`.
3. Edit `Gobi/Binaries/Win64/b4bcoop.ini`:
   - Host: `host=1`. Your PC must accept UDP 7777 (router port-forward + firewall), or use Tailscale/a VPN.
   - Join: `join=<host IP>`.
   - Experimental, not yet tested between two accounts: join over Steam's relay network, no port forwarding:
     `join=steam:<host's SteamID64>`. Hosts accept it next to UDP 7777 (`steam_p2p=0` turns it off).
4. Press Play in Steam. No launch options, no scripts.
5. Pick **Offline**, go to Fort Hope. Clients connect automatically. When the host starts a mission from the war
   table, everyone follows.

Why those names: the game looks for `X3DAudio1_7.dll` (DirectX audio) in its own folder first, on Windows and under
Proton, and our copy forwards to the real one. On Windows, Steam's Play button starts a small launcher that would
start Easy Anti-Cheat, which keeps mods out; `xinput1_3.dll` is loaded by that launcher and makes it start the game
directly instead (only while the mod is installed; `-b4bcoop=off` in the launch options turns that off).
Details: `docs/investigations/launch.md`. The Windows part of this flow is not yet verified on a Windows PC; until it
is, `dist/b4bcoop-legacy.zip` (old `dwmapi.dll` + `Play B4B co-op.cmd`) still works.

Upgrading from the `dwmapi.dll` version: delete `Gobi/Binaries/Win64/dwmapi.dll` and `Play B4B co-op.cmd`, and on
Linux remove the `WINEDLLOVERRIDES` launch option.

Uninstall: delete `xinput1_3.dll` (game folder), `X3DAudio1_7.dll` and `b4bcoop.ini` (`Gobi/Binaries/Win64`).

## Status
Working: hosting, joining Fort Hope, following into missions and across chapters, taking over bot slots, your own deck,
**your own rewards** (supply points etc. land in your profile), **burn cards** for everyone, **no third-party network
traffic** in offline co-op, a **manual flashlight toggle** (L), optional **5-player** sessions (`teamsize=5` on the
host), and a clean "Server full." instead of a host crash when too many join.
Not yet: in-game join UI. Steam P2P (no port forwarding) is implemented but not yet tested between two accounts.
Tested on Windows and on Linux (Proton); the newest features were verified on Linux. Supports the current Steam build
only; the DLL checks the build and does nothing on a mismatch.

## Development
See `CLAUDE.md` (layout, setup, progress) and `docs/NOTES.md` (engine findings).
`tools/fetch-deps.sh` then `native/build.sh` (cross-compiles on Linux with zig) → `launch/package.sh` → `dist/b4bcoop.zip`.
