# Model mods: extracting through the engine's pak layer (#16) and mounting our own paks (#17)

Status 2026-09-24, build 14216215 / CL 1108676, Proton, test instance `launch/multi.sh 1`. Epic #23. Code:
`native/src/paks.c` (mount + signature exemptions in both builds since #20, the rest dev-only), `modkit/b4bpak.py` (pak writer; `tools/b4bpak.py` forwards to it), `tools/modkit/assetcheck` (CUE4Parse checks).

## TL;DR
- **#16 works.** The agent enumerates the pak directory index and reads files through `FPakPlatformFile`, so it
  gets the plain `.uasset/.uexp/.ubulk` bytes the engine sees (decrypted, Oodle-decompressed). `dumpassets
  /Game/Characters/Heroes/Walker/*` wrote 453 files (1.04 GB) in under 20 s. All 453 are byte-identical to an
  independent offline decode, and CUE4Parse (`GAME_Back4Blood`) parses all 233 packages dumped (Walker, the hero
  skeletons, master material, shared textures and materials) without errors: 14 skeletal meshes, 2 skeletons,
  135 textures, MICs and a master material.
- **#17 works.** A pak we write (v9, B4B footer, uncompressed, unencrypted, no `.sig`) is mounted by the agent right
  after the retail paks (ini `modpaks=<dir>`) at read order 1000, and its files win. Proof: Holly's HUD party
  portrait (`Image_Holly_PartyPortrait`, BC7), repainted solid magenta with a byte-level edit, shows magenta in
  Fort Hope. `mountpak` also works at runtime.
- **Signatures:** retail paks are signed, and B4B checks them on three paths. Our paks are exempted by identity on
  all three; the retail paks keep every check (a signed mount of an unsigned pak still dies with "Corrupt file").
  The community method, `-fileopenlog`, instead turns signing off for all paks.
- Correction to #18: the index encryption is plain AES-256-ECB with the community key
  `0x0208…7CFD` (not in this repo: the modkit takes it from the modder, modkit/README.md "The AES key"). For all 61 retail paks, the decrypted index
  matches the footer SHA1 (`tools/b4bpak.py list --aes-key`), and CUE4Parse reads the paks with that key (the
  453/453 comparison below). The key found in the exe by #18 is not the pak key. The encryption is not custom; only the footer and entry layout are.

## 1. The pak platform file (static + live)
| What | Where | Notes |
|---|---|---|
| `FPakPlatformFile` vtable | 0x145BBD358 | `GetName()` (slot 13) returns L"PakFile" (0x1438FEE90) |
| `Initialize(Inner, CmdLine)` | 0x143912700, slot 4 | hooked from DllMain; mounts the retail paks, sets `bSigned` |
| `Mount(Pak, Order, MountPoint, bLoadIndex)` | 0x143913E60 | stock 4.25 signature; `new FPakFile(LowerLevel, Pak, this->bSigned, bLoadIndex)` at 0x143913F74 |
| `bSigned` | pf+0x30 | = `GetPakSigningKeysDelegate` bound && no `-fileopenlog` (0x143912C20..0x143912D4B); 1 in retail |
| `PakFiles` | pf+0x10 | `TArray<{uint32 ReadOrder; FPakFile*}>`; `FPakFile::PakFilename` (FString) at +0x08, `FPakFile::bSigned` at +0x148 |
| `OpenRead(Name, bAllowWrite)` | slot 24, 0x143916380 | returns an `IFileHandle` that decrypts and decompresses |
| `IterateDirectory(Dir, FDirectoryVisitor&)` | slot 33, 0x143900B80 | pak directory index plus the lower level |
| `FPlatformFileManager` | 0x14667B008 | not needed: the Initialize hook captures the instance |

The IPlatformFile vtable matches stock 4.25 through `OpenRead` (24). Slot 25 is an extra function that returns -1,
so everything after it is shifted by one: `OpenReadNoBuffering` 26, `OpenWrite` 27, `DirectoryExists` 28,
`CreateDirectory` 29, `DeleteDirectory` 30, `GetStatData` 31, then `IterateDirectory` in MSVC overload order
(`Func` 32, `Visitor&` 33) and `IterateDirectoryStat` (34/35). `IFileHandle` is stock: 0 deleting dtor, 1 Tell,
2 Seek, 3 SeekFromEnd, 4 Read, 5 Write, 6 Flush, 7 Truncate, 8 Size (FPakFileHandle vtables 0x145BBD028/070/0B8/5F0).
`FDirectoryVisitor` is {vtable {deleting dtor, `bool Visit(const TCHAR*, bool bIsDirectory)`}, flags byte at +8}
(4.26-style), as used by the micropatch module (vtable 0x14527EC70).

Live: 61 retail paks at read order 3 (`../../../Gobi/Content/Paks/pakchunk*-WindowsNoEditor.pak`, no `_P` patches)
and `bSigned`=1. Pak filenames stay in memory after startup (the index isn't unloaded), so `IterateDirectory`
lists everything.

## 2. #16: `dumpassets` (dev build)
```
B4B_AGENT=0 .venv/bin/python tools/b4b.py dumpassets '/Game/Characters/Heroes/Walker/*' [windows outdir]
B4B_AGENT=0 .venv/bin/python tools/b4b.py dumpassets status
B4B_AGENT=0 .venv/bin/python tools/b4b.py paks          # mounted paks and read orders
```
`/Game/` maps to `../../../Gobi/Content/` and `/Engine/` to `../../../Engine/Content/`. `*` also matches `/`.
The agent walks the directories below the glob's literal prefix with `IterateDirectory`, opens every match with
`OpenRead` and writes it to `<outdir>/Gobi/Content/...`, on a worker thread. The default outdir is
`%WINEHOMEDIR%\.local\share\b4b-coop\extract` (Proton), i.e. `~/.local/share/b4b-coop/extract`. With no Wine
home, you must pass an outdir: the agent never writes into the game folder.

Validation (`tools/modkit/assetcheck`, CUE4Parse at cb72c6e, `GAME_Back4Blood`):
- `--compare`: the same 453 Walker files decoded offline by CUE4Parse from the retail paks (AES key above plus Oodle)
  are **byte-identical** to the engine's copies (453/453).
- Parsing: **233/233 packages OK**, for example `3P_Walker_Torso_00_SKM` (5 LODs, 24,295 LOD0 vertices, skeleton
  `3P_Biped_SK`, materials `Walker_Torso_00_A_MI` and `_LOD_MI`), `3P_Biped_SK` (179 bones) and `FP_Biped_SK` (197),
  `Walker_Torso_00_A_BC_T` (DXT1 4096², 13 mips, `.ubulk`), `Walker_Head_00_A_BC_T` (BC7), `_N_T` (BC5), and
  `Master_Hero_Outfit_M` (Material). The masters were only available from encrypted paks until now: #18's blocker 5
  is solved.

### Format deviations observed (on top of #18's list)
- Package summary: tag 0x9E2A83C1, LegacyFileVersion -7, LegacyUE3 0, **FileVersionUE4 0 / Licensee 0 / 0 custom
  versions (unversioned cook)**, PackageFlags 0x80000000 (FilterEditorOnly only, so tagged properties). Parsers must
  be told the engine version (CUE4Parse `GAME_Back4Blood`, UAssetAPI `VER_UE4_25`).
- Every package tested parses with CUE4Parse's B4B options (`SkeletalMesh.HasRayTracingData`,
  `StaticMesh.HasVisibleInRayTracing`), so no other export-layout deviation turned up in meshes, skeletons, textures
  or materials. Engine version string: `4.25.0-1108676+Staging-GobiBuild`.

## 3. Pak format the game accepts
Stock UE 4.25 pak **v9** (`FrozenIndex`) with three TRS changes (the same ones in trumank's `repak` branch
`patch-back4blood` and CUE4Parse):
- **Footer** (222 bytes, like stock v9): `Version u32 (9), Magic u32 0x00018772, EncryptionKeyGuid[16],
  bEncryptedIndex u8, IndexHash[20] (SHA1 of the plain index), IndexSize u64, IndexOffset u64, bIndexIsFrozen u8,
  CompressionMethods 5 × char[32]`. Stock order is Guid, bEncrypted, Magic, Version, Offset, Size, Hash; stock
  magic is 0x5A6F12E1. `FPakFile::Initialize` (0x14390CD30) probes versions 9..1 by footer size, and
  `FPakInfo::Serialize` is at 0x1438FC4D0.
- **FPakEntry**: `Offset i64, Size i64, UncompressedSize i64, CompressionMethodIndex u32, Hash[20],
  [CompressionBlocks if compressed], CompressionBlockSize u32, Flags u8`. Stock has Flags before
  CompressionBlockSize. The in-data header before each file is the same entry with Offset 0.
- **Index**: stock (`FString MountPoint, int32 NumEntries, {FString Name, FPakEntry}...`).
- Retail: index AES-256-ECB (key GUID 0), data unencrypted (flags 0), Oodle in 64 KB blocks with offsets relative to
  the entry, mount points `../../../` or deeper (e.g. `../../../Gobi/Content/Modes/`).

What we write (`tools/b4bpak.py pack <dir> <out.pak>`; `info` and `list [--aes-key]` too): v9, magic 0x18772, key
GUID 0, `bEncryptedIndex` 0, no compression methods, every entry stored uncompressed with its SHA1, mount point
`../../../`, names `Gobi/Content/...`. The engine accepts it as is.

## 4. Signature handling
The `.sig` files are stock `FPakSignatureFile` (magic 0x73832DAA, version 1, RSA-encrypted SHA1 of the chunk-hash
table, one CRC per 64 KB chunk). B4B checks paks on three paths:
1. **Sync reader:** `FPakFile::CreatePakReader` (0x14390C9D0) wraps the reader in `FSignedArchiveReader` +
   `FChunkCacheWorker` (0x143918140) if `FPakFile::bSigned` (copied from `pf->bSigned` at Mount) or with
   `-signedpak`/`-signed`.
2. **`GetPakSignatureFile(Pak)`** (0x143903560): loads `<pak>.sig` and validates the RSA table. On a missing or bad
   `.sig` it broadcasts the pak-corrupt delegate. The game's handler (0x140BAB560) stores the name, and
   `FEngineLoop::Tick` then ends the game with **`LogEngine Fatal: Corrupt file: <pak>`** (0x140BA0B51). The
   precacher calls this for *every* pak it registers (`RegisterPakFile`, 0x1439048E5), signed or not.
3. **Precacher (async loads, `pakcache.Enable`):** signature checks are global (`bEnableSignatureChecks` at
   precacher+0x2C0). Every read's completion lambda (0x143923BE0, capture {precacher, IndexToFill, bDoCheck = 1})
   goes through `StartSignatureCheck` → `DoSignatureCheck` (0x143908AD0), which compares each chunk's CRC with
   `FPakData::Signatures->ChunkHashes` (FPakData 0x98 bytes: Name FName +0x2C, Signatures +0x88).

What happened with an unsigned mod pak:
| Exemption applied | Result |
|---|---|
| `bSigned` cleared for our Mount only | Mount OK, sync reads OK (`dumpassets`); the first async read crashes in `DoSignatureCheck` (0x143908C14, null ChunkHashes) |
| + precacher `bDoCheck` cleared for reads of our paks | `LogEngine Fatal: Corrupt file: …b4bmod_hollyportrait.pak` (path 2 broadcast from `RegisterPakFile`) |
| + `GetPakSignatureFile` returns an empty pointer, without broadcasting, for our paks | **works**: startup, main menu, Fort Hope, overrides visible |
| negative control: `mountpak <unsigned pak> 1200 signed` (no exemption) | `Mount` FAILED, immediately followed by `Corrupt file` Fatal |

All three exemptions match by identity: the exact paths we passed to `Mount` (case-insensitive). Retail paks still
go through all three checks, and `pf->bSigned` stays 1. The micropatch module (`FMicropatchModule`, 0x140D7D580)
mounts downloaded patch paks at order 100 and requires a `.sig` next to them ("No signature file %s for pak %s").

## 5. Mount order and timing
- `Mount` sorts by read order, highest first (`FindFileInPakFiles`). Retail paks have order 3, and `_P.pak` adds
  100 × (chunk version + 1) (code at 0x143914398). Ours start at 1000 (`MOD_ORDER`), plus one per pak in the
  directory, in `FindFirstFile` order.
- **Startup:** `paks_early_init` (DllMain; X3DAudio1_7.dll is a static import, so this runs before the exe's entry
  point) hooks `FPakPlatformFile::Initialize`. Mod paks are mounted right after the retail paks, about 0.6 s into
  PreInit, before any package loads (log: `paks: mount … order 1000 -> ok` before `init: tick hooked`). Assets
  loaded at startup, like the HUD portraits, therefore come from our pak.
- **Runtime:** `mountpak <windows path> [order]` on the game thread works. A text file in a pak mounted at order
  1100 replaced the one in an earlier mod pak at 1001. Assets already loaded stay as they are until the engine
  reloads them.

## 6. Override proof
1. `dumpassets '/Game/UI/Textures/Common/Characters/Heroes/Image_Holly_PartyPortrait.*'` → `.uasset` (881 B) +
   `.uexp` (66,868 B; one BC7 320×208 mip inline at offset 280).
2. `assetcheck --paint` overwrote the mip with BC7 mode-6 blocks for (255,1,255,255), without changing any size.
3. `b4bpak.py pack` → `b4bmod_hollyportrait.pak` (2 files) in `~/.local/share/b4b-coop/spike-pak/mods`, with
   `modpaks=Z:\home\dan\.local\share\b4b-coop\spike-pak\mods` in the test instance's ini.
4. In game (screenshot `~/.local/share/b4b-coop/spike-pak/shot-mod.png`; baseline `shot-camp.png`), the HUD portrait
   bottom left is a solid magenta rectangle. The engine's `OpenRead` returns our `.uexp` (`cmp`-identical), and the
   log shows `paks: precacher: first read from a mod pak (…b4bmod_hollyportrait.pak)`.

## 7. Reproduce
```
launch/gamelock.sh acquire <me>; launch/install.sh
B4B_INI_EXTRA='modpaks=Z:\home\dan\.local\share\b4b-coop\spike-pak\mods' launch/multi.sh 1
B4B_AGENT=0 .venv/bin/python tools/b4b.py dumpassets '/Game/UI/Textures/Common/Characters/Heroes/Image_Holly_PartyPortrait.*'
DOTNET_ROOT=vendor/dotnet vendor/dotnet/dotnet build -c Release tools/modkit/assetcheck
A=tools/modkit/assetcheck/bin/Release/net10.0/assetcheck.dll
DOTNET_ROOT=vendor/dotnet vendor/dotnet/dotnet $A --paint ~/.local/share/b4b-coop/extract \
  Gobi/Content/UI/Textures/Common/Characters/Heroes/Image_Holly_PartyPortrait.uasset ~/.local/share/b4b-coop/modsrc
.venv/bin/python tools/b4bpak.py pack ~/.local/share/b4b-coop/modsrc <mods dir>/b4bmod_test.pak
# restart the instance; mod paks are mounted at PakPlatformFile init
```
Everything extracted or packed stays under `~/.local/share/b4b-coop/`: game assets, extracted files and paks are
never committed.

## 8. Risks and next steps
- **Biggest risk for the epic: authoring new content, not delivering it.** Byte-level edits and existing-asset swaps
  now go end to end. New skeletal meshes still need #18's converter: stock 4.25 cooks lack the ray-tracing data and
  the 8-byte skeletal-mesh trailer. New master materials would also need B4B-compatible shaders.
- Since #20 the mount path and the signature exemptions are in player builds too, for the add-ons in
  `<game>\b4bcoop-addons\` (docs/investigations/addons.md); `modpaks=` stays a dev-only raw mount (order 3000+). They rest on 5 byte-signed hooks (Initialize, precacher lambda,
  DoSignatureCheck layout, GetPakSignatureFile, Mount) and will break with any game update (the build is pinned).
- Everyone in a session needs the same mod paks: content can decide gameplay (collision, hitboxes). A later
  add-on system (#20-#22) should put a pak hash into the join handshake.
- Next: #18 experiment 1 (edited MIC in game) is now possible. Also: textures with `.ubulk` mips and a hero outfit
  swap (repoint a hero's mesh materials).
