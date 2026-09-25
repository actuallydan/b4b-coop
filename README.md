# b4b-coop

Unofficial private co-op for **Back 4 Blood**: play the game's offline mode together with your Steam friends, up to
4 players, with no Warner Bros / Turtle Rock servers. Everyone keeps their own offline progress.

> Unofficial and unaffiliated. For playing a game you own with friends in offline mode. It never touches the
> official online services. Use at your own risk. See [Safety & disclaimer](#safety--disclaimer).


## Install
> Upgrading from an older b4bcoop? Do step 3 of [Uninstall](#uninstall) first.

**Everyone who plays together installs it, and everyone needs the same
version.**

1. Download the latest version of the .zip file from [Releases](../../releases).
    - Should appear as `b4bcoop-<version>.zip`
2. Open the game folder: 
    1. in Steam, right-click **Back 4 Blood** 
    1. Click **Manage**
    1. Click **Browse local files**. 
    1. A folder should open that contains `Back4Blood.exe` and a folder named `Gobi`. 
        1. On Windows this is usually `C:\Program Files (x86)\Steam\steamapps\common\Back 4 Blood` 
        1. On Linux/Steam Deck: `~/.local/share/Steam/steamapps/common/Back 4 Blood`

3. Extract **everything** from the zip into that folder. 
    1. The zip has the same layout as the game folder, so its `Gobi`
   folder merges into the game's `Gobi` folder. No game file is replaced. You get:
   ```
   Back 4 Blood\xinput1_3.dll                          (next to Back4Blood.exe)
   Back 4 Blood\b4bcoop-README.txt, b4bcoop-COMMANDS.txt, b4bcoop-LICENSE.txt
   Back 4 Blood\Gobi\Binaries\Win64\X3DAudio1_7.dll     (the mod)
   Back 4 Blood\Gobi\Binaries\Win64\b4bcoop.ini         (settings, optional)
   ```
4. say **Yes** if asked to merge   

*That's all: no launch options, no scripts to run manually.*

## Play
1. Press **Play** in Steam. Steam itself must be online: joins go through Steam.
2. At the Back 4 Blood title screen, sign in and choose **Offline**.
3. In Fort Hope you are **hosting automatically**: your Steam friends see **Join Game** on you in their Steam friends
   list.
4. Start missions from the war table as usual. Everyone in your game follows you in.

**Join a friend:** in the Steam friends list, right-click your friend while they are in Back 4 Blood → **Join Game**,
or accept their Steam invite. It works with your game closed or running: the game starts if needed, signs in
Offline and joins them.

Only the host's **Steam friends** can join. Press **`~`** in game for the b4bcoop window: players (kick, ban, lock),
join/leave, camera, flashlight, keys, cheats. The same things work as chat commands (`/help` in the game's chat).

## Options
All optional. Open `Gobi\Binaries\Win64\b4bcoop.ini` in a text editor and remove the `;` in front of a line to turn
it on. Saved changes apply within a couple of seconds, also while you play (`host`, `join`, `host_ip` and the network
options need a game restart). Every option, with defaults and examples:
[docs/COMMANDS.md](docs/COMMANDS.md#b4bcoopini-options).
- `host=0`: don't host; your offline game stays private.
- `teamsize=5`: (host) 5 survivors instead of 4.
- `flashlight_key=L`: the key for the manual flashlight toggle (`off` turns it off).
- `allow_joins=anyone`: (host) let in people who aren't your Steam friends; or `allow_steamids=<17-digit Steam ID>`
  for one person.
- `host_ip=1`: **advanced**, only if you know you need it: host and join by IP address instead of through Steam. Needs
  port forwarding (UDP 7777) and triggers the Windows Firewall prompt. Everyone in the game needs it.

All chat commands and options: [docs/COMMANDS.md](docs/COMMANDS.md).

## Uninstall
Delete these files from the game folder (Steam → right-click Back 4 Blood → Manage → Browse local files):
1. Next to `Back4Blood.exe`: 
    - `xinput1_3.dll`
    - `b4bcoop-README.txt`
    - `b4bcoop-COMMANDS.txt`
    - `b4bcoop-LICENSE.txt`.
2. In `Gobi\Binaries\Win64`:
    - `X3DAudio1_7.dll`
    - `b4bcoop.ini`
    - all `b4bcoop-*.log` files
    - and `b4bcoop-bans.txt`
   (only there if you banned someone).
3. Left over from older versions, if present, in `Gobi\Binaries\Win64`: `dwmapi.dll`, `Play B4B co-op.cmd`,
   `steam_appid.txt`. On Linux also remove the launch option `WINEDLLOVERRIDES="dwmapi=n,b" %command%`
   (Steam → right-click Back 4 Blood → Properties → Launch Options).

Steam's "Verify integrity of game files" does **not** remove these: they are extra files, not game files. Your offline
progress stays either way.

## Troubleshooting
- **"Everyone needs the same version"**: someone has another b4bcoop version. Everyone downloads the latest release
  and extracts it again (step 3 of Install).
- **Play online / with Easy Anti-Cheat** without removing the mod: add `-b4bcoop=off` to the launch options (Steam →
  right-click Back 4 Blood → Properties → Launch Options). The game then starts normally. Remove it again to play
  co-op.
- **The log**: `Gobi\Binaries\Win64\b4bcoop-<number>.log` (the newest one). Its first lines show the version, e.g.
  `b4bcoop 0.3.0 (protocol 1)`. No new log after starting the game = the mod didn't load.
- **Windows**: this install layout hasn't been tested on a Windows PC yet (it has on Linux and Steam Deck's Proton). If
  the mod doesn't load there, please [open an issue](../../issues) and attach `Gobi\Binaries\Win64\b4bcoop-launcher.log`
  if that file exists.
- **Join Game missing or not working**: both of you need the mod (same version), Steam online, and the host in Fort
  Hope or a mission. Fallback: the host looks up their 17-digit Steam ID (Steam → click your account name at the top
  right → Account details), and you type `/join steam:<that number>` in the game's chat while in your own Fort Hope.

## Status

The mod works notionally. Development is in progress and it still requires rigorous testing.

Hosting, joining through Steam (no port forwarding required), following into missions and across chapters, taking over bot slots, using your own deck, **your own rewards** (supply points etc. land in your profile), **burn cards** for everyone.

The game has almost the entire retail experience with **no third-party network traffic**.

So far we've implemented:
- a **manual flashlight toggle** (L), optional
- an optional **third-person camera** for your own hero (`/thirdperson`)
- **5-player+** sessions (`teamsize=5` on the host), and a clean "Server full." instead of a host crash when too many
join. Steam joins between two real accounts are verified; clicking Join Game in Steam's own friends list is new in
this version and not yet tested between two accounts.

- Verified on Linux (Proton) and Windows (see Troubleshooting).
- Supports the current Steam build only; the mod checks the build and does nothing on a mismatch presently.

## Safety & disclaimer
- **Unofficial.** Not made, endorsed or supported by Turtle Rock Studios or Warner Bros. Games.
- **Offline mode only.** It uses the game's offline mode and, while it runs, blocks the game's online services
  (Epic/WB/Turtle Rock). Your offline progress is saved on your PC as usual.
- **Don't use it for online play.** For online play, add `-b4bcoop=off` to the launch options (Windows: Steam's Play
  then goes through Easy Anti-Cheat again) or remove the mod's files. Playing online with a modified game or without
  anti-cheat can break the game's terms.
- **Nothing exposed.** By default the mod opens nothing to the network (no Windows Firewall prompt): players connect
  through Steam's peer-to-peer networking, and the game's own network port only listens on your PC itself. Only
  people you allow (Steam friends by default) can join your game.
- **No game files.** The zip holds only this project's own two DLLs, a config file and text files; it replaces no
  game file.
- **Open source, built in public.** Every release is built by GitHub Actions from this repository
  (`.github/workflows/release.yml`), with a `SHA256SUMS` file and a signed build provenance attestation. To check a
  download: `sha256sum -c --ignore-missing SHA256SUMS` (Windows: `Get-FileHash b4bcoop-<version>.zip`), and
  `gh attestation verify b4bcoop-<version>.zip -R actuallydan/b4b-coop`. The build is reproducible:
  `tools/fetch-deps.sh --build` then `launch/package.sh` gives the same DLL hashes (`SHA256SUMS` lists them).
- **Why antivirus may warn.** `X3DAudio1_7.dll` is an unsigned DLL that the game loads in place of a DirectX DLL and
  that hooks game functions; `xinput1_3.dll` changes which program Steam's launcher starts (Windows). That is also
  what some malware does, so heuristic scanners sometimes flag them. There is no installer, nothing is downloaded,
  and the only network traffic is the co-op session over Steam. If you don't trust the zip, build it yourself from the
  source.
- **How it loads.** The game looks for `X3DAudio1_7.dll` (DirectX audio) in its own folder first, on Windows and under
  Proton; ours forwards to the real one. On Windows, Steam's Play button starts a small launcher that would start Easy
  Anti-Cheat, which keeps mods out; `xinput1_3.dll` is loaded by that launcher and makes it start the game directly
  instead (only while the mod is installed and `-b4bcoop=off` isn't set). Details: `docs/investigations/launch.md`.
- **Your save.** The mod checks every reward a host sends before it touches your save, and ignores anything outside
  normal mission limits.
- **No warranty.** MIT licensed ([LICENSE](LICENSE)), provided as is. Back up
  `%LOCALAPPDATA%\Back4Blood\Steam\Saved\SaveGames` if your progress matters to you.

## Development
See `CLAUDE.md` (layout, setup, versioning, progress) and `docs/NOTES.md` (engine findings).
`tools/fetch-deps.sh` then `native/build.sh` (cross-compiles on Linux with zig; dev build with the local command
server and test commands) → `launch/package.sh` (player build, `native/build.sh --release`) →
`dist/b4bcoop-<version>.zip`. The version is in `VERSION`.
