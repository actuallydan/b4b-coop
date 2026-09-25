# b4bcoop mod maker's kit (b4bmod)

Make add-ons for Back 4 Blood with the [b4bcoop](https://github.com/actuallydan/b4b-coop) mod: new textures and
material colours, your own models (FBX or glTF) for survivors and weapons, packed as one add-on file that players drop
into their game folder. No Unreal editor needed.

This kit is **for mod makers only**. Players don't need it: they install the b4bcoop mod and the add-ons you share.

The kit is scripts and source code only. It contains no game files and no third-party programs. `b4bmod setup`
downloads the open-source pieces from their official sources; the rest you get yourself, as described below.

- [Install (Windows)](#install-windows) · [Install (Linux / Steam Deck)](#install-linux--steam-deck)
- [What you need, and where each piece comes from](#what-you-need-and-where-each-piece-comes-from)
- [The AES key](#the-aes-key)
- [From an FBX to an add-on](#from-an-fbx-to-an-add-on) · [Commands](#commands)
- [Where files go](#where-files-go) · [Troubleshooting](#troubleshooting) · [Tested on](#tested-on)

Guides: [docs/textures.md](docs/textures.md) (textures, materials) · [docs/meshes.md](docs/meshes.md) (models,
Blender) · [docs/addons.md](docs/addons.md) (packing, testing, sharing).

## Install (Windows)
1. **Python 3.10 or newer** from [python.org](https://www.python.org/downloads/windows/) (the installer, not the
   Microsoft Store version). In the installer, tick **Add python.exe to PATH**.
2. Extract this zip to a folder you can write to, e.g. `C:\b4bmod` (not inside `Program Files`).
3. Open a command prompt in that folder: in File Explorer, click the address bar, type `cmd`, press Enter.
4. Run:
   ```
   b4bmod setup
   ```
   It downloads the .NET 10 SDK (if you don't have it) and UAssetAPI into the kit's `deps` folder, builds the tools,
   and lists what is still missing. Nothing is installed system-wide and no admin rights are needed. Takes a few
   minutes the first time (about 250 MB).
5. Set the game's AES key ([The AES key](#the-aes-key)):
   ```
   b4bmod config aes_key 0x0208...
   ```
   b4bmod checks it against the game's files and tells you if it is wrong.
6. `b4bmod status` should end with `ready.`

b4bmod finds Back 4 Blood in your Steam libraries. If not: `b4bmod config game "D:\SteamLibrary\steamapps\common\Back 4 Blood"`
(the folder with `Back4Blood.exe` and `Gobi` in it).

Use the Command Prompt or PowerShell (in PowerShell type `.\b4bmod.cmd` instead of `b4bmod`). Put paths with spaces and
search patterns in double quotes.

## Install (Linux / Steam Deck)
Python 3.10+ (preinstalled on most distributions and SteamOS), `bash` and `curl` (for Microsoft's .NET install
script). Extract the zip, then in a terminal in that folder:
```
./b4bmod.sh setup
./b4bmod.sh config aes_key 0x0208...
./b4bmod.sh status
```
The game is found in `~/.local/share/Steam`, `~/.steam/steam` and Flatpak Steam, including extra Steam libraries.
Everywhere in the guides, `b4bmod` means `./b4bmod.sh` on Linux.

## What you need, and where each piece comes from
| Piece | What for | License | How you get it |
|---|---|---|---|
| Python 3.10+ | runs b4bmod | PSF | python.org (Windows), your distribution (Linux) |
| .NET 10 SDK | builds and runs the two C# tools (`dotnet\b4bmod`: textures, materials; `dotnet\pakx`: reading the game's paks) | MIT (Microsoft) | `b4bmod setup` uses yours if `dotnet --list-sdks` shows a 10.x SDK; otherwise it runs Microsoft's official install script (`https://dot.net/v1/dotnet-install.ps1` / `.sh`) into `deps\dotnet`. Manual: `winget install Microsoft.DotNet.SDK.10` or [dotnet.microsoft.com](https://dotnet.microsoft.com/download/dotnet/10.0) |
| UAssetAPI | reads and writes the game's cooked asset files | MIT | `b4bmod setup` downloads the tested version (commit `3228c1e`) from GitHub, checks its SHA-256, and unpacks it to `deps\UAssetAPI`. Manual: download `https://github.com/atenfyr/UAssetAPI/archive/3228c1e86261aa08131f7ec0ff1a395f5d0b2a84.zip` and extract it so that `deps\UAssetAPI\UAssetAPI\UAssetAPI.csproj` exists |
| CUE4Parse | reads the game's `.pak` files (the same library FModel uses) | Apache-2.0 | NuGet package `CUE4Parse` 1.2.2.202609: `dotnet` downloads it on the first build, into your NuGet cache (`%USERPROFILE%\.nuget\packages`). It brings **OodleSharp** (MIT), an open-source Oodle decoder |
| BCnEncoder.Net, StbImageSharp, StbImageWriteSharp | texture compression, PNG read/write | MIT / Unlicense | NuGet, same as above |
| Oodle | the game's files are Oodle-compressed | proprietary (Epic Games / RAD) | **Not needed**: CUE4Parse decompresses with OodleSharp. Optional, about 5x faster extraction of big folders: a native Oodle library. We never ship or download it; see [Optional: native Oodle](#optional-native-oodle) |
| The AES key | the game's pak index is encrypted | (a number) | you look it up: [The AES key](#the-aes-key) |
| Blender 4.2+ | editing models, FBX conversion | GPL | [blender.org](https://www.blender.org/download/) (only for models) |
| Back 4 Blood (Steam) | the game files everything starts from | | read only; b4bmod writes into the game folder only for `install` / `uninstall` (`b4bcoop-addons`) |
| The b4bcoop mod | loads add-ons in the game | | the player zip from the b4bcoop releases, to test your add-on |

### Optional: native Oodle
Only for speed. Ways UE modders usually have it:
- **FModel** downloads it automatically: look in FModel's output/data folder (usually `Output\.data` next to
  FModel.exe) for `oodle-data-shared.dll` (older FModel versions: `oo2core_9_win64.dll`).
- The **OodleUE** project's GitHub releases (`github.com/WorkingRobot/OodleUE`, the library built from the
  Unreal Engine sources): `clang-cl-x64-release.zip` has `bin\oodle-data-shared.dll`; for Linux,
  `gcc-x64-release.zip` has `lib/liboodle-data-shared.so`. That is where FModel and CUE4Parse get it.

Then `b4bmod config oodle "C:\path\to\oodle-data-shared.dll"` (`b4bmod config oodle none` to go back). Oodle is
Epic Games' / RAD's proprietary code under the Unreal Engine license: make sure you may use it, and don't
redistribute it.

## The AES key
The file list inside Back 4 Blood's `.pak` files is encrypted with AES-256. The key is the same for every copy of the
game (it is built into the game; one key per game build), and it is publicly known: UE modders collect such keys in
community AES key lists for FModel and UModel. Search for "Back 4 Blood AES key" (for example the UE4/UE5 AES key
lists on GitHub or the FModel / UModel communities). It is 64 hexadecimal digits, `0x0208…7CFD`.

This kit doesn't contain the key. Give it to b4bmod once:
```
b4bmod config aes_key 0x<the 64 hex digits>
```
b4bmod decrypts every pak index with it and compares the result with the checksum stored in the pak, so a wrong key
is caught at once (`wrong AES key: it does not decrypt pakchunk0-WindowsNoEditor.pak`). Also possible: the
environment variable `B4B_AES_KEY`, or `--aes-key <key>` on any command.

## From an FBX to an add-on
The short version, for a survivor outfit (details: [docs/meshes.md](docs/meshes.md), [docs/textures.md](docs/textures.md)):
```
b4bmod find "Heroes/Walker/Meshes/Elite/Elite_00/.*_SKM$"                         1. pick the game mesh to replace
b4bmod mesh info   /Game/Characters/Heroes/Walker/Meshes/Elite/Elite_00/3P_Walker_Elite_00_SKM
b4bmod mesh export /Game/Characters/Heroes/Walker/Meshes/Elite/Elite_00/3P_Walker_Elite_00_SKM ref.glb
    2. in Blender: import ref.glb and your FBX, fit your model to the reference skeleton and pose, skin it to that
       armature, name its materials after the slots, delete the reference mesh, export FBX (Add Leaf Bones off)
b4bmod mesh import /Game/Characters/Heroes/Walker/Meshes/Elite/Elite_00/3P_Walker_Elite_00_SKM my.fbx -o mymod
b4bmod tree        /Game/Characters/Heroes/Walker/Meshes/Elite/Elite_00/3P_Walker_Elite_00_SKM     3. its textures
b4bmod texture     /Game/.../Textures/Walker_Elite_00_A_Body_BC_T my_body_color.png -o mymod           (one per texture)
b4bmod pack mymod -o my_walker.pak --title "My Walker" --author me --version 1.0 --category survivors --zip   4.
b4bmod install my_walker.pak                                                                       5. start the game
```
Do the same for the first-person arms (`FP_Walker_Elite_00_SKM`): they have their own pose. Share `my_walker.zip`.

## Commands
`b4bmod help` prints all of them. The main ones:

| Command | What it does |
|---|---|
| `setup`, `status`, `config [key [value]]` | get and check the tools; settings `aes_key`, `game`, `oodle`, `blender` |
| `find "<regex>"` | search the game's asset paths (`/Game/...`) |
| `extract <asset or "folder/*">` | copy game files out of the paks (the other commands do this for what they need) |
| `info`, `tree <asset>` | what an asset is; mesh → materials → textures |
| `export <texture> <out.png>`, `texture <texture> <in.png> -o <moddir>` | texture to PNG and back |
| `mi <material instance> [set <param> <value>...] -o <moddir>` | material parameters |
| `mesh info / export / import / edit` | skeletal meshes: slots and LODs; to glTF; your FBX/glTF back; quick edits |
| `pack <moddir> -o <name>.pak [--title ...] [--zip]` | the add-on |
| `install <pak>`, `uninstall <name>`, `check [<pak>]` | into / out of `<game>\b4bcoop-addons`; what the game will load |

`<asset>` is a path from `find`, like `/Game/Characters/Heroes/Walker/Meshes/Elite/Elite_00/3P_Walker_Elite_00_SKM`.
`<moddir>` is your mod's working folder (any name); its `Gobi\Content\...` part is what `pack` puts in the add-on.

## Where files go
- The kit folder: the scripts, `deps\` (.NET, UAssetAPI) and the tool builds (`dotnet\*\bin`).
- Your data, Windows `%LOCALAPPDATA%\b4b-coop\`, Linux `~/.local/share/b4b-coop/` (`B4B_MODKIT_DATA` to move it):
  `b4bmod.ini` (settings), `extract\` (game files you extracted: a survivor's folder is 0.3-2 GB), `listing\`
  (the `find` index), `tmp\`.
- Your mod folders and add-ons: wherever you run the commands (use `-o`).
- Extracted game files are for making your mod on your PC. Share only add-ons (and only the files you changed).

## Troubleshooting
- `'py' is not recognized` / `'python' is not recognized`: install Python from python.org with "Add python.exe to
  PATH", then open a new command prompt.
- `no AES key set` / `wrong AES key`: see [The AES key](#the-aes-key).
- `Back 4 Blood not found`: `b4bmod config game "<game folder>"`.
- `building b4bmod failed`: the lines above it are the compiler's; the first build needs internet access for NuGet.
  `b4bmod setup` rebuilds everything.
- Git Bash turns `/Game/...` into `C:/Program Files/Git/Game/...`; b4bmod undoes that, but the Command Prompt or
  PowerShell avoid it.
- `can't write ...: close the game first`: the game keeps its add-ons open while it runs.
- Your add-on doesn't show: `/addons` in the game's chat lists it with the reason if it didn't load; `b4bmod check`
  shows the same offline. The b4bcoop mod must be installed.

## Tested on
- **Linux** (Arch, Python 3.14, .NET SDK 10.0.401, Blender 5.1): everything, from a fresh unzip of this kit
  (`setup` downloading .NET and UAssetAPI) through `find`, `extract`, `tree`, `export`, `texture`, `mi`, `mesh
  export/import` (FBX and glTF), `pack`, `install`, `check`. Extracted files are byte-identical to what the running
  game reads (801/801 files of a survivor, also identical with and without native Oodle).
- **Windows**: not yet run on a Windows PC. Run under Wine with Windows Python 3.12: `b4bmod.cmd`, `status`,
  `config`, `mesh info/export/import`, `pack`, `install`, `check`, `uninstall` (Windows paths, `%LOCALAPPDATA%`);
  the add-on it packed is byte-identical to the Linux one. Not run on Windows: `setup` (PowerShell .NET install)
  and the two .NET tools (`find`, `extract`, `info`, `tree`, `export`, `texture`, `mi`), because .NET 10 doesn't
  start under Wine; they are plain .NET code with nothing Linux-specific. Reports welcome.

Unofficial; not affiliated with Turtle Rock Studios or Warner Bros. Games. The kit is MIT licensed (LICENSE); the
pieces it downloads keep their own licenses.
