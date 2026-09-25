# Texture and material mods without the editor (#18 "CLI tier", #21, epic #23)

Status 2026-09-25, build 14216215. Tool: `tools/modkit/b4bmod.py` (front end) + `tools/modkit/b4bmod/` (.NET 10,
UAssetAPI for packages, BCnEncoder.Net for BC1/BC3/BC4/BC5/BC7, StbImageSharp for PNG). Packing:
`tools/b4bpak.py` (dev) or the add-on packer (#20). Mounting: dev ini `modpaks=` (model-mods-paks.md §5).

## TL;DR
- **Texture pixel mods work in game, verified live** (Proton, `multi.sh 2`, dev ini `modpaks=`): Walker's Elite_00
  outfit repainted (body 4096² BC1, arms 2048² BC1, gear BC7 **shrunk 2048² → 1024²**), seen on the hero in third
  person, in the client's first-person arms and after a map change; default-skin textures of every pistol, rifle
  and SMG repainted (61 BC7 textures), seen on the bots' rifles in a mission.
- **Material-instance edits work in game, verified live** (#18 experiment 1): `Walker_Elite_00_A_Head_MI` with
  `Roughness Multiply` 0.9 → 0.5 and `Base Color` repointed to another package's texture. A/B screenshots against the
  unmodded head; the running MI holds the new values (read from memory). The new import needs no extra handling
  beyond the preload dependency the tool adds.
- No editor needed: `tools/modkit/b4bmod.py` (written for Linux and Windows, run on Linux) finds, lists, exports to
  PNG, re-encodes (BC1/BC3/BC4/BC5/BC7, full mip chain, any power-of-two size) and edits MIs, writing a
  `<moddir>/Gobi/Content/...` folder for `b4bpak.py pack` or the add-on packer (#20).
- Open: extraction still needs the dev agent (`dumpassets`); a player-side extractor or a shipped listing is next.

## 1. For mod authors: step by step

### 0. Once
```
.venv/bin/python tools/modkit/b4bmod.py setup     # .NET 10 SDK + UAssetAPI into vendor/, builds the tool
alias b4bmod='.venv/bin/python tools/modkit/b4bmod.py'
```
Windows (not tried yet): the same with `py tools\modkit\b4bmod.py`; the setup fetches .NET with PowerShell.
`find` needs the `cryptography` Python package (`pip install cryptography`).

### 1. Find the asset
Every game file path is in the pak indexes, which `find` reads offline (cached in `~/.local/share/b4b-coop/listing/`):
```
b4bmod find 'Heroes/Walker/.*_SKM$'                    # Walker's meshes: heads, torsos, legs, outfits (Elite_NN)
b4bmod find 'Heroes/Walker/Meshes/Elite/Elite_00/.*_T$' # textures of one outfit
b4bmod find 'Weapons/Pistol/HG01/.*_T$'                 # a weapon's textures (default + skins)
```
Where things are:

| What | Path | Notes |
|---|---|---|
| Survivors (Walker, Holly, Mom, Doc, Hoffman, Evangelo, Jim, Karlee) | `/Game/Characters/Heroes/<Name>/Meshes/` | `Base/Heads|Torsos|Legs/<Piece>_NN/` (pieces), `Elite/Elite_NN/` (outfits), `Shared/` (hair, eyes, lashes) |
| Later outfits and survivors (Heng, Sharice, Tala, Dan) | `/Game/TU07`, `TU09`, `TU11`, `TU13`, `TU15` `/Characters/Heroes/<Name>/` | same layout |
| Each piece/outfit | `<folder>/3P_<Hero>_<Piece>_SKM` (third person), `FP_..._SKM` (first-person arms), `Materials/*_MI`, `Textures/*_T` | variants A/B/C = colour variants (own MIs + textures) |
| Weapons | `/Game/Items/Weapons/<Class>/<Code>/` (`Pistol/HG01..05`, `Assault/AR01..06`, `SMG/SMG01..05`, `Shotgun/SG01..05`, `LMG/LMG01..02`, `Sniper/SNI01..03` (also `Sni01`, `Sin01` folders), `MachineGun/MG01`, `Bow/Bow01`, `Melee/*`, `Knife/Knife01..12`); later skins under `/Game/TUxx/Items/Weapons/...` | `Meshes/<Code>_SK`, `Textures/` (default look), `Skin_Sets/Skin_Default/*_MI`, `Skin_Sets/Skins_*/Skin_<Name>/{Materials,Textures}` (unlockable skins) |
| Which gun a code is | `/Game/UI/Textures/Common/Items/Weapons/Icon_Weapon_<Code>` | export the icon to PNG and look |

Texture suffixes: `_BC_T` base colour (sRGB), `_N_T` normal map (BC5, only X/Y stored), `_PBR_T` packed
roughness/metal/AO-style masks (linear), `_MSK_T` / `_MM_T` masks, `_ID_T`, `_DMG_T`. `b4bmod info` tells format,
size and sRGB.

### 2. Extract it
The files come out of the game through the agent (dev build, game running; model-mods-paks.md §2):
```
b4bmod extract '/Game/Characters/Heroes/Walker/*'      # = tools/b4b.py dumpassets ..., into ~/.local/share/b4b-coop/extract
```
Then see what a mesh uses:
```
b4bmod tree /Game/Characters/Heroes/Walker/Meshes/Elite/Elite_00/3P_Walker_Elite_00_SKM
  .../Materials/Walker_Elite_00_A_Body_LOD_MI  [MaterialInstanceConstant]
    parent: .../Materials/Walker_Elite_00_A_Body_MI  [MaterialInstanceConstant]
      'Base Color' = .../Textures/Walker_Elite_00_A_Body_BC_T  [Texture2D]  PF_DXT1 4096x4096
      'Normal Map' = .../Textures/Walker_Elite_00_A_Body_N_T  [Texture2D]  PF_BC5 4096x4096
      ...
```

### 3. Paint a texture
```
b4bmod export /Game/.../Walker_Elite_00_A_Body_BC_T body.png    # 4096x4096 PNG of the original (mip 0)
# edit body.png in any paint program, keep the UV layout
b4bmod texture /Game/.../Walker_Elite_00_A_Body_BC_T body.png -o mymod
```
- Output: `mymod/Gobi/Content/.../Walker_Elite_00_A_Body_BC_T.uasset/.uexp/.ubulk`, the cooked texture in the
  original's pixel format with a full mip chain (colour mips in linear light, normal maps renormalised).
- Size: the PNG's size is used (power-of-two sides for textures with mips). Smaller saves memory; `--resize` scales to
  the original size instead.
- `--quality fast|balanced|best` (BC7/BC1 encoder effort; 4096² BC7 `balanced` takes seconds on 16 cores).
- Normal maps: paint/bake a normal map (+Y as in UE, i.e. DirectX convention); only red/green are stored.

### 4. Change a material instance
```
b4bmod mi /Game/.../Walker_Elite_00_A_Head_MI                       # list its parameters
b4bmod mi /Game/.../Walker_Elite_00_A_Head_MI \
    set "Roughness Multiply" 0.5 \
    set "Base Color" /Game/Characters/Heroes/Mom/Meshes/Elite/Elite_00/Textures/Mom_Elite_00_A_Head_BC_T \
    set "Detail Colortint (Pores)" 1,0.2,0.2 \
    -o mymod
```
Values: a number (scalar), `r,g,b[,a]` (vector, linear 0-1), a `/Game/...` texture path or `none` (texture),
`parent <path>` re-parents. A parameter the MI doesn't override yet is added (the parent must have it; `tree`/`mi`
on the parent shows its names). Static switches can't be changed (they select compiled shaders).
Edits read the asset from `-o <moddir>` if it's already there, so several commands build one mod.

### 5. Pack and test
```
b4bmod pak mymod ~/mods/mymod.pak          # plain pak: tools/b4bpak.py pack
```
Dev: `modpaks=<windows path of ~/mods>` in `b4bcoop.ini`, restart the game (mod paks are mounted at startup). The
add-on system (#20) takes the same `mymod/` folder.

Everyone in a session should run the same mods: textures are cosmetic, but each player sees only their own files.

## 2. Formats (verified)
Cooked `UTexture2D` is stock 4.25 (`tools/modkit/b4bmod/Texture.cs`). After the tagged properties and the object
GUID bool: 2 × `FStripDataFlags`, `bCooked` u32 = 1, then per cooked format `FName PixelFormat`, `int64 SkipOffset`
(**absolute** offset in the combined .uasset+.uexp stream of the next FName), `FTexturePlatformData` {SizeX, SizeY,
PackedData (NumSlices | bHasOptData<<30 | bIsCubemap<<31), FString PixelFormat, [8 B opt data], FirstMipToSerialize,
NumMips, mips, bIsVirtual u32}, and `FName None`. Each mip: `bCooked` u32, `FByteBulkData` header {Flags u32,
ElementCount i32, SizeOnDisk i32, OffsetInFile i64} + inline payload, SizeX/Y/Z.

| Seen in 370 textures (Walker, weapons, shared, UI) | |
|---|---|
| Formats | `PF_DXT1` (BC1: base colour, drench masks), `PF_BC5` (normals), `PF_BC7` (PBR/masks/heads/gear), UI `PF_BC7` without mips |
| Sizes | 4096², 2048², 1024², 256², 128² (+ 320×208 UI); always the full mip chain down to 1×1 |
| Streamed mips | every mip > 64 px: flags `0x10501` (PayloadAtEndOfFile, PayloadInSeperateFile, Force_NOT_Inline, NoOffsetFixUp), payload in `.ubulk` at `OffsetInFile` (relative to the `.ubulk`, largest first, no padding) |
| Inline mips | 64 px and below (7 mips): flags `0x48` (ForceInline, SingleUse), `OffsetInFile` = absolute offset of the payload |
| Round trip | `b4bmod texcheck`: **370/370 re-write byte-identically** (native data and `.ubulk`) |

Writing a new size: the mip list is rebuilt with the same inline/streamed rule and flag templates, `SkipOffset` and
inline offsets are recomputed for where UAssetAPI places the export (written twice: the second pass uses the real
export offset), the `.ubulk` is rebuilt, and UAssetAPI updates the export's `SerialSize` and `BulkDataStartOffset`.
Checked by re-reading with our parser and with CUE4Parse (`assetcheck`, `GAME_Back4Blood`).

Encoder quality (re-encoding a decoded retail texture, PSNR vs. the decode): BC5 60.6 dB, BC7 46.3 dB. Time on 16
cores: 4096² BC1 1.6 s, 2048² BC7 `balanced` 8 s, 1024² BC7 `best` 2 s.

Material instances: `MaterialInstanceConstant` parameters are tagged properties (`ScalarParameterValues`,
`VectorParameterValues`, `TextureParameterValues`, each {`ParameterInfo` {Name, Association, Index}, `ParameterValue`,
`ExpressionGUID`}); the MI's native tail (static permutation resource + shader map ids, ~156 KB for a hero head) is kept
as is. A new texture reference adds a package import and an object import and lists the object import among the
export's `CreateBeforeSerialization` preload dependencies, as the cooker does for the MI's own textures (EDL).

## 3. In-game results
Setup: dev build of `models-textures` (= `models`, no native changes), `launch/multi.sh 2` with
`B4B_INI_EXTRA='modpaks=Z:\home\dan\.local\share\b4b-coop\texwork\paks'`, client `model walker_elite_00`,
host `mdl bring` / `mdl look` to put the client's hero in front of the host's camera. Log:
`paks: mount ...b4bmod_walker_tex.pak order 1001 -> ok (unsigned)`, then `paks: no .sig lookup for mod pak ...` when
the assets load. Screenshots (not committed) in `~/.local/share/b4b-coop/texwork/proof_*.png`.

| Mod | What changed | Result |
|---|---|---|
| `Walker_Elite_00_A_Body_BC_T` | 4096² BC1, magenta/yellow checker, same size | shirt and trousers checkered, third person (host view of the client) |
| `Walker_Elite_00_A_Arms_BC_T` | 2048² BC1, cyan/orange checker | sleeves in third person and the client's **first-person arms** (in Fort Hope and in the mission after the map change) |
| `Walker_Elite_00_A_Gear_BC_T` | BC7 **2048² → 1024²** (11 mips, new `.ubulk`), blue | cap, backpack, pouches solid blue, close up (streamed mips come from our `.ubulk`) |
| 61 × `Items/Weapons/{Pistol,Assault,SMG}/<Code>/Textures/*_BC_T` | BC7, green/magenta checker, `--quality fast` | bots' rifles checkered in the saferoom of Evansburgh 1-2 (the host's own SMG wears a skin set, whose textures are in `Skin_Sets/`, so it stayed as it was) |
| `Walker_Elite_00_A_Head_MI` | `Roughness Multiply` 0.9 → 0.5, `Base Color` → `Mom_Elite_00_A_Head_BC_T` | face clearly paler and glossier than the unmodded A/B shot; in memory `TextureParameterValues[0].ParameterValue` = the loaded `Mom_Elite_00_A_Head_BC_T` object, `ScalarParameterValues[1]` = 0.5, and Walker's own head BC is never loaded |
| same MI, `Base Color` → `Walker_Elite_00_A_Arms_BC_T` | control with an unmistakable texture | face and neck in the cyan/orange checker |

No load errors for any modded package; an unrelated agent crash hit the client once while it ran `find` in a loop
during a map load (not a mod issue).

## 4. Limits and next steps
- **Extraction** needs the dev agent (`dumpassets`, game running). Players' builds don't have it. Options: ship
  `dumpassets` in the add-on tooling build, or an offline extractor (CUE4Parse + the pak AES key works, but needs
  Oodle, which we can't ship; the game links it statically).
- Only textures that exist are replaced (same path). New texture paths work as MI references only if the texture
  package is in a mounted pak (tool support is there: `mi ... set <param> /Game/MyMod/...`, untested: needs a
  Texture2D written from scratch, i.e. a template copy renamed; next step).
- Weapon skins: `Skin_Sets/<set>/Textures/` hold each skin's own textures; modding a skin means its textures, the
  default look is `<Code>/Textures/`. Glint/FP/3P MIs are separate (FP = first person, 3P = world model).
- Static switches, new master materials, virtual textures, cubemaps, texture arrays: not supported (need shaders or
  other layouts). Pixel formats: BC1/BC3/BC4/BC5/BC7/BGRA8/G8 (all hero/weapon textures seen use BC1/BC5/BC7).
- A texture's size may change; the engine was fine with 1024² in place of 2048². Mip count is always the full chain.
- Everyone sees only their own files: a mod is cosmetic per machine. The add-on system should still compare mod
  lists (#20).
- Next: MI creation (a new MI under a new path from a template, for colour variants), batch recipes (JSON), and the
  add-on packer taking `<moddir>` (#20).
