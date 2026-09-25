# Texture and material mods without the editor (#18 "CLI tier", #21, epic #23)

Status 2026-09-25, build 14216215. Tool: `modkit/b4bmod.py` (front end) + `modkit/dotnet/b4bmod/` (.NET 10,
UAssetAPI for packages, BCnEncoder.Net for BC1/BC3/BC4/BC5/BC7, StbImageSharp for PNG). Packing:
`modkit/b4bpak.py` (dev) or the add-on packer (#20). Mounting: dev ini `modpaks=` (model-mods-paks.md §5).

## TL;DR
- **Texture pixel mods work in game, verified live** (Proton, `multi.sh 2`, dev ini `modpaks=`): Walker's Elite_00
  outfit repainted (body 4096² BC1, arms 2048² BC1, gear BC7 **shrunk 2048² → 1024²**), seen on the hero in third
  person, in the client's first-person arms and after a map change; default-skin textures of every pistol, rifle
  and SMG repainted (61 BC7 textures), seen on the bots' rifles in a mission.
- **Material-instance edits work in game, verified live** (#18 experiment 1): `Walker_Elite_00_A_Head_MI` with
  `Roughness Multiply` 0.9 → 0.5 and `Base Color` repointed to another package's texture. A/B screenshots against the
  unmodded head; the running MI holds the new values (read from memory). The new import needs no extra handling
  beyond the preload dependency the tool adds.
- No editor needed: `modkit/b4bmod.py` (written for Linux and Windows, run on Linux) finds, lists, exports to
  PNG, re-encodes (BC1/BC3/BC4/BC5/BC7, full mip chain, any power-of-two size) and edits MIs, writing a
  `<moddir>/Gobi/Content/...` folder for `b4bpak.py pack` or the add-on packer (#20).
- Extraction: offline from the retail paks since the modkit (#21): `b4bmod extract` (CUE4Parse + the modder's AES
  key; bytes identical to `dumpassets`). The dev agent's `dumpassets` stays dev-only.

## 1. For mod authors
Moved to the mod maker's kit: **modkit/docs/textures.md** (commands through `modkit/b4bmod.py`; setup and third-party
pieces in modkit/README.md). Extraction is offline now (`b4bmod extract`, CUE4Parse; no game or dev build needed).

## 2. Formats (verified)
Cooked `UTexture2D` is stock 4.25 (`modkit/dotnet/b4bmod/Texture.cs`). After the tagged properties and the object
GUID bool: 2 × `FStripDataFlags`, `bCooked` u32 = 1, then per cooked format `FName PixelFormat`, `int64 SkipOffset`
(**absolute** offset in the combined .uasset+.uexp stream of the next FName), `FTexturePlatformData` {SizeX, SizeY,
PackedData (NumSlices | bHasOptData<<30 | bIsCubemap<<31), FString PixelFormat, [8 B opt data], FirstMipToSerialize,
NumMips, mips, bIsVirtual u32}, and `FName None`. Each mip: `bCooked` u32, `FByteBulkData` header {Flags u32,
ElementCount i32, SizeOnDisk i32, OffsetInFile i64} + inline payload, SizeX/Y/Z.

| Seen in 370 textures (Walker, weapons, shared, UI) | |
|---|---|
| Formats | `PF_DXT1` (BC1: base colour, drench masks), `PF_BC5` (normals), `PF_BC7` (PBR/masks/heads/gear), UI `PF_BC7` without mips |
| Sizes | 4096², 2048², 1024², 512², 256², 128² (+ 320×208 UI); always the full mip chain down to 1×1 |
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
- **Extraction**: solved by the modkit (`b4bmod extract`, offline, CUE4Parse; its managed Oodle decoder means no
  proprietary library either).
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
