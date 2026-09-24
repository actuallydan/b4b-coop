# b4b-coop

Unofficial private co-op for **Back 4 Blood**: up to 4 players, no Warner Bros / Turtle Rock servers.

It takes the game's offline mode, which keeps your progression locally, and turns the host's offline session into a listen
server that friends join directly. A small injected DLL handles hosting, joining and following the
host into missions.

> Unofficial and unaffiliated. For playing a game you own with friends in offline mode. It never touches the
> official online services. Use at your own risk. See [Safety & disclaimer](#safety--disclaimer).

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
   - Hosts only let in their **Steam friends** by default. `allow_steamids=<id64>,<id64>` lets specific people in,
     `allow_joins=anyone` turns the check off.
4. Press Play in Steam. No launch options, no scripts.
5. Pick **Offline**, go to Fort Hope. Clients connect automatically. When the host starts a mission from the war
   table, everyone follows.

Why those names: the game looks for `X3DAudio1_7.dll` (DirectX audio) in its own folder first, on Windows and under
Proton, and our copy forwards to the real one. On Windows, Steam's Play button starts a small launcher that would
start Easy Anti-Cheat, which keeps mods out; `xinput1_3.dll` is loaded by that launcher and makes it start the game
directly instead (only while the mod is installed; `-b4bcoop=off` in the launch options turns that off).
Details: `docs/investigations/launch.md`. The Windows part of this flow is not yet verified on a Windows PC; until it
is, `b4bcoop-legacy.zip` (old `dwmapi.dll` + `Play B4B co-op.cmd`, built next to it by `launch/package.sh`) still works.

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

## Safety & disclaimer
- **Unofficial.** Not made, endorsed or supported by Turtle Rock Studios or Warner Bros. Games.
- **Offline mode only.** It uses the game's offline mode and, while it runs, blocks the game's online services
  (Epic/WB/Turtle Rock). Your offline progress is saved on your PC as usual.
- **Don't use it for online play.** For online play, remove the mod's files (or, on Windows, add `-b4bcoop=off` to
  the launch options so Steam's Play goes through Easy Anti-Cheat again). Playing online with a modified game or
  without anti-cheat can break the game's terms.
- **No game files.** The zip holds only this project's own two DLLs, a config file and text files; it replaces no
  game file.
- **Open source, built in public.** Every release is built by GitHub Actions from this repository
  (`.github/workflows/release.yml`), with a `SHA256SUMS` file and a signed build provenance attestation. To check a
  download: `sha256sum -c --ignore-missing SHA256SUMS` (Windows: `Get-FileHash b4bcoop.zip`), and
  `gh attestation verify b4bcoop.zip -R actuallydan/b4b-coop`. The build is reproducible: `tools/fetch-deps.sh --build`
  then `launch/package.sh` gives the same DLL hashes (`SHA256SUMS` lists them).
- **Why antivirus may warn.** `X3DAudio1_7.dll` is an unsigned DLL that the game loads in place of a DirectX DLL and
  that hooks game functions; `xinput1_3.dll` changes which program Steam's launcher starts (Windows). That is also
  what some malware does, so heuristic scanners sometimes flag them. There is no installer, nothing is downloaded,
  and the only network traffic is the co-op session (directly or over Steam). If you don't trust the zip, build it
  yourself from the source.
- **Your save.** The DLL checks every reward a host sends before it touches your save, and ignores anything outside
  normal mission limits. Only people you allow (Steam friends by default) can join your game.
- **No warranty.** MIT licensed ([LICENSE](LICENSE)), provided as is. Back up
  `%LOCALAPPDATA%\Back4Blood\Steam\Saved\SaveGames` if your progress matters to you.

## Development
See `CLAUDE.md` (layout, setup, progress) and `docs/NOTES.md` (engine findings).
`tools/fetch-deps.sh` then `native/build.sh` (cross-compiles on Linux with zig; dev build with the local command
server and test commands) → `launch/package.sh` (player build, `native/build.sh --release`) → `dist/b4bcoop.zip`.
