# b4b-coop

Unofficial private co-op for **Back 4 Blood**: up to 4 players, no Warner Bros / Turtle Rock servers.

It takes the game's offline mode, which keeps your progression locally, and turns the host's offline session into a listen
server that friends join directly. A small injected DLL (`dwmapi.dll`) handles hosting, joining and following the
host into missions.

> Unofficial and unaffiliated. For playing a game you own with friends in offline mode. It never touches the
> official online services. Use at your own risk. See [Safety & disclaimer](#safety--disclaimer).

## Install (players)
1. Download `b4bcoop.zip` from [Releases](../../releases) and unzip it.
2. Steam → Back 4 Blood → Manage → Browse local files → `Gobi/Binaries/Win64`. Copy the zip's files there
   (`dwmapi.dll`, `b4bcoop.ini`, `Play B4B co-op.cmd`).
3. Edit `b4bcoop.ini`:
   - Host: `host=1`. Your PC must accept UDP 7777 (router port-forward + firewall), or use Tailscale/a VPN.
   - Join: `join=<host IP>`.
   - Experimental, not yet tested between two accounts: join over Steam's relay network, no port forwarding:
     `join=steam:<host's SteamID64>`. Hosts accept it next to UDP 7777 (`steam_p2p=0` turns it off).
   - Hosts only let in their **Steam friends** by default. `allow_steamids=<id64>,<id64>` lets specific people in,
     `allow_joins=anyone` turns the check off.
4. Windows: start the game with `Play B4B co-op.cmd` (Steam must be running). A normal Steam launch goes
   through Easy Anti-Cheat and the mod won't load.
   Linux / Steam Deck: launch from Steam with launch options `WINEDLLOVERRIDES="dwmapi=n,b" %command%`
5. Start the game, pick **Offline**, go to Fort Hope. Clients connect automatically. When the host starts a mission
   from the war table, everyone follows.

Uninstall: delete the files you copied (`dwmapi.dll`, `b4bcoop.ini`, `Play B4B co-op.cmd`).

## Status
Working: hosting, joining Fort Hope, following into missions and across chapters, taking over bot slots, your own deck,
**your own rewards** (supply points etc. land in your profile), **burn cards** for everyone, **no third-party network
traffic** in offline co-op, a **manual flashlight toggle** (L), optional **5-player** sessions (`teamsize=5` on the
host), and a clean "Server full." instead of a host crash when too many join.
Not yet: in-game join UI. Steam P2P (no port forwarding) is implemented but not yet tested between two accounts.
Tested on Windows and on Linux (Proton); the newest features were verified on Linux. Supports the current Steam build
only; the DLL checks the build and does nothing on a mismatch.

## Safety & disclaimer
- **Unofficial.** Not made, endorsed or supported by Turtle Rock Studios or Warner Bros. Games.
- **Offline mode only.** It uses the game's offline mode and, while it runs, blocks the game's online services
  (Epic/WB/Turtle Rock). Your offline progress is saved on your PC as usual.
- **Don't use it for online play.** For online play, start the game normally through Steam (Easy Anti-Cheat on) and,
  on Linux, remove the launch option. Playing online with a modified game or without anti-cheat can break the game's
  terms.
- **No game files.** The zip holds only this project's own DLL, a config file, a launcher script and text files.
- **Open source, built in public.** Every release is built by GitHub Actions from this repository
  (`.github/workflows/release.yml`), with a `SHA256SUMS` file and a signed build provenance attestation. To check a
  download: `sha256sum -c --ignore-missing SHA256SUMS` (Windows: `Get-FileHash b4bcoop.zip`), and
  `gh attestation verify b4bcoop.zip -R actuallydan/b4b-coop`. The build is reproducible: `tools/fetch-deps.sh --build`
  then `launch/package.sh` gives the same `dwmapi.dll` hash.
- **Why antivirus may warn.** `dwmapi.dll` is an unsigned DLL that the game loads in place of a Windows DLL and that
  hooks game functions. That is also what some malware does, so heuristic scanners sometimes flag it. It has no
  installer, downloads nothing, and its only network traffic is the co-op session (directly or over Steam). If you
  don't trust the zip, build it yourself from the source.
- **Your save.** The DLL checks every reward a host sends before it touches your save, and ignores anything outside
  normal mission limits. Only people you allow (Steam friends by default) can join your game.
- **No warranty.** MIT licensed ([LICENSE](LICENSE)), provided as is. Back up
  `%LOCALAPPDATA%\Back4Blood\Steam\Saved\SaveGames` if your progress matters to you.

## Development
See `CLAUDE.md` (layout, setup, progress) and `docs/NOTES.md` (engine findings).
`tools/fetch-deps.sh` then `native/build.sh` (cross-compiles on Linux with zig; dev build with the local command
server and test commands) → `launch/package.sh` (player build, `native/build.sh --release`) → `dist/b4bcoop.zip`.
