# Model mods: engine/format facts and authoring toolchain (spike #18, epic #23)

Status 2026-09-24, build 14216215 / CL 1108676. Offline research only (game not run). Pak extraction (#16) and
mounting (#17) are covered elsewhere; this covers authoring and cooking.

## TL;DR
- B4B runs a **Turtle Rock fork of Epic's 4.25-plus branch** (`4.25.0-1108676+Staging-GobiBuild`). Cooked packages
  are the standard 4.25 legacy format (-7), **unversioned** (UE4 version 0, no custom-version table) with **tagged
  properties**. The loader then assumes the exe's own versions: **UE4 519** (one past stock 4.25's 518), licensee 0,
  and the custom-version table below (four values past stock 4.25).
- Known binary deviations in cooked data: skeletal meshes carry ray-tracing data (4.25-plus) and **8 extra bytes
  near the end of USkeletalMesh** (TRS); each static mesh section has `bVisibleInRayTracing` (4.26 backport);
  animations use a modified ACL codec. Textures, material instances, skeletons and data assets use the stock 4.25
  layout. Shaders come from the shared shader library, so no bytecode is stored in packages.
- **Offline editing works on Linux, verified:** UAssetAPI parses every B4B sample (skeleton, skeletal/static mesh,
  material, MIC, texture, anim) and **re-writes it byte-identically**. Editing a hero MIC (scalar parameter plus a
  texture swap to another asset) produces a package that CUE4Parse (Back4Blood mode) reads back with the new values. Not yet
  tested in game: that needs #17.
- **Recommendation:** (1) now, a Linux/Windows CLI kit on UAssetAPI for material-instance and texture-reference
  edits plus pixel injection; (2) next, the stock UE 4.25.4 launcher editor with a template project of generated
  stubs, plus a **post-cook converter** that patches the known mesh deviations. Materials are limited to instances of
  B4B's own master materials, with no static-switch overrides.
- **Biggest blocker:** getting a pak the game will mount. The index uses custom encryption, the paks are signed
  (`*.sig`, "No signature file %s for pak %s"), and the pak format is modified. That is #17. On the content side,
  the unknown 8-byte skeletal-mesh trailer and the master materials and physics assets (only in the encrypted paks,
  #16) block new skeletal meshes.
- **Update (#16/#17, model-mods-paks.md):** both pak blockers are solved. The index is plain AES-256-ECB with the
  community key `0x0208…7CFD` (all 61 index SHA1s match), not a custom scheme, so the key at 0x14187CF20 is
  something else. The agent extracts any file, masters and PAs included, through the engine and mounts our
  unsigned paks with per-pak signature exemptions.

## 1. Engine and format versions

| Item | Value | Source |
|---|---|---|
| Engine version string | `4.25.0-1108676+Staging-GobiBuild` | `version` console output in the agent log |
| Changelists | build CL 1108676; NetCL 1085854 (TRS Perforce, not Epic's CL range) | log (LogNetVersion, PSO cache) |
| Package summary | legacy file version **-7** (4.25 layout), tag 0x9E2A83C1, `PKG_FilterEditorOnly` | headers of carved packages |
| FileVersionUE4 / Licensee in packages | **0 / 0** (unversioned cook), `SavedByEngineVersion` empty, **no custom versions** | same |
| Loader max UE4 version | **519** = `VER_UE4_NON_OUTER_PACKAGE_IMPORT` (4.26 value; stock 4.25 = 518) | `cmp eax, 0x207` next to "Unable to load package (%s) PackageVersion %i, MaxExpected %i…" (0x142648CE7) |
| Loader max licensee version | 0 (compared against the zero register) | same function |
| Property serialization | tagged (no `PKG_UnversionedProperties`), so no .usmap is needed | package flags 0x80000000 |
| Pak | footer magic 0x18772 with reordered fields, entry `CompressionBlockSize`/`Flags` swapped, Oodle, index encrypted with a custom scheme (the public AES key `0xCFC5E8CF…34B130` is in the exe at 0x14187CF20, but plain AES-ECB fails), `.sig` per pak | CUE4Parse `GAME_Back4Blood`, own tests |
| Audio | Wwise 2019.2.4 | log |
| Anim compression | ACL plugin (`AnimBoneCompressionCodec_ACL`), rotation data modified per gildor/Spiritovod | CUE4Parse warning, gildor forum |

UE4 519 only changes editor-only fields (the import `PackageName` and the summary owner GUID are both skipped when
`FilterEditorOnly` is set), so cooked 518 and 519 packages have the same layout.

### Custom versions (as registered in the exe; `tools/modkit/customversions.py`)
Same as stock 4.25 except:

| Custom version | B4B | stock 4.25 | note |
|---|---|---|---|
| FRenderingObjectVersion (Dev-Rendering) | 44 `VolumeExtinctionBecomesRGB` | 43 | 4.26 value (4.25-plus backport) |
| FEditorObjectVersion (Dev-Editor) | 39 `NumberParsingOptionsNumberLimitsAndClamping` | 38 | 4.26 value |
| FFrameworkObjectVersion (Dev-Framework) | **38** | 37 | Epic's enum ends at 37: TRS-added entry |
| FFoliageCustomVersion | **16** | 14 | beyond Epic's last value, 15: TRS |
| FSequencerObjectVersion | 12 | 12 | |
| FReleaseObjectVersion 30, FortniteMain 31, AnimPhys 17, Anim 7, SkeletalMesh 17, Niagara 61, Physics-Ext 37, RecomputeTangent 1, Core 4 | = stock 4.25 | | |

TRS/plugin-only versions: `TRSOccluderVer` 1, `GobiSpatialAudioVolumeVer` 2, `FAssociativePlaneVer` 1,
`AkAcousticVolumeVer`/`AkAcousticPortalVer` 2 (Wwise).

### Branch: Epic 4.25-plus or TRS fork?
Both. The base is Epic's **4.25-plus**: ray-tracing data in skeletal meshes (CUE4Parse models it as
`GAME_UE4_25_Plus`), 4.26 values for Rendering/Editor/UE4 519. TRS then changed it further: Framework 38 and Foliage
16, which Epic never shipped, the 8-byte skeletal-mesh trailer, the pak format and index encryption, the obfuscated
reflection (NOTES.md), and TRS Perforce changelists. For cooking, treat it as "4.25-plus + TRS patches".

### Custom serialization found so far
- **SkeletalMesh:** `FSkeletalMeshLODRenderData` has RayTracingData (4.25-plus). Where stock writes
  `TArray<UObject*> DummyObjs` (int32 0), B4B has **8 bytes**. Across 9 meshes they read `00 00 NN 00 00 00 00 00`,
  where NN matches the LOD count (2 for the FP arms, 5 for heroes, 6 for zombies). Their meaning is still unknown.
- **StaticMesh:** each `FStaticMeshSection` has a `bVisibleInRayTracing` bool (a 4.26 field).
- **AnimSequence:** ACL with modified rotation data. New animations are out of scope; skins reuse existing anims.
- Texture2D, MaterialInstanceConstant, Material, Skeleton, DataTable and Blueprint packages all parse on the stock
  4.25 code path (CUE4Parse `GAME_Back4Blood` only special-cases the items above).

## 2. Toolchain options (ranked by feasibility for Windows and Linux authors)

| # | Option | Covers | Feasibility / effort | Author friction | Legal |
|---|---|---|---|---|---|
| 1 | **(c) No editor: UAssetAPI plus a texture injector** | MIC parameter/texture/parent edits, texture pixel swaps (same or new size), data tables, repointing a mesh's materials, skeleton, or physics asset | **Proven** for MICs (below). Texture pixels via UE4-DDS-Tools (MIT, supports 4.25, stock texture layout). A few days to wrap as a CLI | Low: CLI, runs on Linux and Windows, no 100 GB engine | MIT/Apache tools; ship only our own edited packages, never game files |
| 2 | **(a)+(d) Stock UE 4.25.4 launcher editor, then a B4B converter** | Static meshes, skeletal meshes on the existing hero skeleton, new textures, MICs of B4B masters, sounds (Wwise, see below) | Editor output is close to B4B's. The converter must add the 4 extra bytes to the SKM trailer (8 instead of stock's 4) and the per-LOD ray-tracing data, add the `bVisibleInRayTracing` byte to every static mesh section, and drop stub packages. About 1–2 weeks for the converter plus validation | Medium: Windows editor (Linux: build 4.25 from source, see b), a template project, one extra step after cooking | UE EULA: free to use; cooked output is the author's; the converter is our code |
| 3 | (b) UE 4.25-plus from Epic's GitHub | Same as 2, with native RT and static-mesh layout | Requires an Epic-linked GitHub account, a source build (~100 GB, hours) and still the TRS patches (8-byte trailer, Framework/Foliage versions). Branch availability not checked here | High | Engine source and binaries can only be shared with other UE licensees (EULA); a public repo may hold at most small snippets. We cannot ship a patched editor |
| 4 | (a) Stock 4.25 editor alone | Textures, MICs, data assets, probably | Meshes won't load: the SKM trailer and RT data and the SM section byte are missing, so the loader misreads them. Unversioned cooks would also be read with B4B's newer custom versions | Medium | as 2 |
| 5 | (c) Full mesh writer without an editor | Skeletal meshes from glTF/FBX straight into cooked packages | A cooked LOD render-data writer (positions, tangents, UVs, skin weights, sections, required bones, RT data) using CUE4Parse's reader as the spec. 3–5 weeks, high risk. No existing community tool does this for 4.25 | Low once built | our code |

Materials in every option: new master materials, and MICs that override static switches, need shaders compiled
against B4B's renderer and shipped in a shader library chunk. The stock 4.25 shader source doesn't match TRS's
renderer, and B4B keeps shaders in the shared library (`ShaderArchive-…`), with no bytecode in packages. **Only MICs
that set parameters on B4B's existing masters are safe.** A MIC without a static permutation has no shader map
(`bHasStaticPermutationResource` false).

Sounds: B4B uses Wwise (banks and `.wem` in pakchunks 14/21/22/29/38, with no uassets). Sound replacement is a Wwise
bank problem, not a UE cooking problem, so it is out of scope for this spike.

## 3. What we need from B4B's content, and how a template project references it
Hero rig (plaintext pak33, verified): `/Game/Characters/Heroes/BaseHero/Meshes/3P_Biped_SK`. It has 179 bones
(UE-mannequin style: `root`, `weapon`, `flashlight`, `ik_hand_*`, `pelvis`, …) plus 5 virtual bones, and GUID
373043E2-…. Every hero shares it. Meshes: `3P_<Hero>_…_SKM` (third person, 5 LODs, 10 material slots such as
ArmSkin/Arms/Body/Head/Hair/Lashes/Eye/EyeShadow/Gear/Gear_LOD) and `FP_<Hero>_…_SKM` (arms, `FP_Biped_SKM`
base). Physics: `/Game/Characters/Heroes/<Hero>/Meshes/3P_<Hero>_PA` and shadow
`/Game/Characters/Heroes/BaseHero/Meshes/3P_Biped_CapsuleShadow_PA`. Materials: MICs parented to
`/Game/Materials/Masters/Instances/Master_Hero_Head_M/Master_Hero_Head_Microdetail_MI` and similar, with parameter
names such as `Base Color`, `Normal`, `PBR`, `Roughness Multiply` and a static switch `Enable Microdetail`.
**The masters and the hero PAs are only in the encrypted paks (0/5/30/32)**, so we need #16 to read them.

Referencing strategy (the standard community pattern for cooked UE games):
- **Stubs at the same object path**, generated on the author's machine from their own install, never shipped:
  - skeleton stub: same bone names, hierarchy, ref pose, virtual bones and sockets, generated from `3P_Biped_SK`
    (CUE4Parse → glTF/PSK → editor import, or written directly);
  - master-material stubs: the same parameter names, types and groups, with a trivial graph. Authors create MICs of
    the stub. The cooked MIC references the parent by path, so in game it resolves to B4B's real master. No static
    switch overrides, so no shaders;
  - physics-asset stubs: point to an existing hero PA by path, or ship a new PA (a PhysicsAsset is plain data).
- Stub packages are dropped from the mod pak (converter step). Mods replace existing paths, since the game only
  spawns assets it already references. New asset paths would also need AssetRegistry and data-table entries
  (the cosmetics tables), which is a later step.

## 4. Practical checks (Linux, offline)
1. **Samples without the index:** `tools/modkit/pakscan.py` walks the plaintext FPakEntry headers, decompresses
   with Oodle, names packages from their export table and pairs uasset with uexp. 92.6k entries total: pakchunk0/5/30/32 are
   encrypted (47k entries, including Engine and core content), and the rest are plaintext with 8,018 packages,
   including the hero skeleton, hero SKMs, MICs and textures. Everything used here came from pakchunk33.
2. **CUE4Parse (Apache-2.0, `GAME_Back4Blood`, commit cb72c6e)** loads all 407 packages carved from pak33's
   character folders (skeletons, SKMs, MICs, textures) without errors. The few remaining warnings fit uexp
   mis-pairing between same-size siblings (textures come in ±9-byte pairs, N↔MSK) plus the ACL codec, which
   CUE4Parse doesn't implement. Its pak mount **fails**: 0 of 61 paks mount with the public key, which confirms the
   custom index encryption.
3. **UAssetAPI (MIT, commit 3228c1e, `VER_UE4_25`)**, via `tools/modkit/uassetrt`:

   | Sample | Parsed as | Unparsed native tail | Re-write |
   |---|---|---|---|
   | 3P_Biped_SK (Skeleton + 17 sockets) | 21 NormalExports | 317 KB | byte-identical |
   | 3P_Biped_SKM, 3P_Walker_Elite_00_SKM (SkeletalMesh) | NormalExport | 2 MB / 25 MB | byte-identical |
   | Walker_Elite_00_A_Head_MI (MIC) | NormalExport | 156 KB | byte-identical |
   | 2× Texture2D, BruteDamage_M (Material), helmet StaticMesh, Tallboy_Idle_AS | NormalExport | – | byte-identical |

   Bulk run: **288 of 288** uasset+uexp pairs carved from pak33's character folders re-write byte-identically.
   The 8 packages without a paired uexp fail, as expected.
   UAssetAPI fully handles tagged properties and keeps native data (render data, mips, shader map ids) as opaque
   bytes. **Edit test:** MIC `Roughness Multiply` 0.9 → 0.5, and `Base Color` repointed to
   `/Game/Characters/Heroes/Mom/.../Mom_Elite_00_A_Head_BC_T` (adds 2 imports and 2 names). CUE4Parse in B4B mode
   reads the result back with the new values and no warnings. **Open:** whether the game loads it (needs #17). A
   new import is not added to the package's preload dependencies (event-driven loader), so check that in game.
   Mesh or texture payload edits need a native writer (UE4-DDS-Tools for textures, the converter for meshes).

## 5. Authoring kit (#21) effort estimate (excluding pak writing and mounting, #17)
| Piece | Estimate |
|---|---|
| `b4bmod` CLI on UAssetAPI: MIC params and texture/parent swaps, dependency fix-up, batch from a JSON/YAML recipe | 3–5 d |
| Texture injection via UE4-DDS-Tools (validate on B4B textures and `.ubulk`), wrapped in the CLI | 2–3 d |
| Stub generator from the user's install: skeleton, master-material parameter stubs, PA refs | 1–1.5 wk (needs #16 for masters and PAs) |
| Post-cook converter for stock 4.25.4 output: SKM trailer + RT data, SM section flag, strip stubs, optional re-version | 1–2 wk (after the trailer RE) |
| Template project + docs (Windows editor; Linux route = source build or CLI-only) | 3–4 d |
| **Total** | **≈ 4–6 weeks**. The CLI tier (texture/material mods) is usable after ~1.5 weeks once #17 can mount a pak. |

## 6. Next experiments
1. **(#17 dependency) First in-game load:** mount an uncompressed mod pak holding only the edited
   `Walker_Elite_00_A_Head_MI` and look for a rougher Walker head with Mom's face texture. This tests override
   priority, signing and EDL preload dependencies in one step.
2. **Texture pixels:** carve a texture's `.ubulk` too (extend pakscan pairing), inject a DDS with UE4-DDS-Tools
   (4.25 mode), re-read with CUE4Parse, then test in game.
3. **SKM trailer:** reverse-engineer B4B's `USkeletalMesh::Serialize` tail (what follows the RT data, and the 8
   bytes `00 00 NN 00 …`). Hypothesis: a TRS field keyed on the LOD count, replacing the stock DummyObjs array.
4. **Stock-cook diff (needs a Windows box with UE 4.25.4):** cook a cube SM and an SKM skinned to a stub
   `3P_Biped_SK`, parse them with CUE4Parse in `GAME_UE4_25` and `GAME_Back4Blood` modes, and diff against the B4B
   layout. That diff is the converter spec. Decide between a versioned cook (carries stock 4.25 custom versions,
   which the B4B loader accepts as older) and an unversioned one (read with B4B's newer versions: riskier).
5. **Masters and PAs (after #16):** dump `Master_Hero_*` parameter lists and hero PAs to generate stubs.
6. Check whether the build opens extra shader-library chunks when a pak is mounted (`ShaderArchive-%s-` string
   present), to judge whether custom master materials could ever work.

## 7. Reproduce
```
# dotnet 10 SDK into vendor/ (not committed)
curl -sSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel 10.0 --install-dir vendor/dotnet
git clone https://github.com/atenfyr/UAssetAPI vendor/UAssetAPI          # MIT, tested 3228c1e
# Oodle for reading compressed entries: liboodle-data-shared.so from github.com/WorkingRobot/OodleUE releases
# (built from UE source: EULA-bound, local use only) -> vendor/oodle/
.venv/bin/python tools/modkit/customversions.py
.venv/bin/python tools/modkit/pakscan.py list  "$PAKS/pakchunk33-WindowsNoEditor.pak" > /tmp/idx33.tsv
.venv/bin/python tools/modkit/pakscan.py extract "$PAKS/pakchunk33-WindowsNoEditor.pak" <scratch> '3P_Biped_SK$'
DOTNET_ROOT=vendor/dotnet vendor/dotnet/dotnet build -c Release tools/modkit/uassetrt
DOTNET_ROOT=vendor/dotnet vendor/dotnet/dotnet tools/modkit/uassetrt/bin/Release/net10.0/uassetrt.dll check <scratch>
```
Extracted files stay in scratch/`~/.local/share/b4b-coop/`, never in the repo.

Tool licenses: CUE4Parse Apache-2.0, UAssetAPI MIT, UEViewer/umodel MIT (B4B support in gildor's specific
builds), UE4-DDS-Tools MIT, QuickBMS GPL-2.0 (not needed now). Oodle is proprietary: don't redistribute it, and
write mod paks uncompressed.

Sources: gildor.org topic 7535 (B4B in umodel: "4.25 plus (custom)", RayTracingData, custom ACL, custom pak
encryption), zenhax UE4 pak topic (Spiritovod's B4B BMS: custom encryption, "only paks with decrypted indices"),
CUE4Parse `GAME_Back4Blood` code paths, Epic forum "UE 4.25-plus branch?" (the next-gen console branch).
