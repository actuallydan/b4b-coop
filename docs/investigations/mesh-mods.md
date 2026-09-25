# Mesh mods: B4B skeletal mesh format, writer and glTF import (#18, #21, epic #23)

Status 2026-09-25, build 14216215. Branches `models-meshes`, `models-fullmodel`. Tools: `modkit/upkg.py` (package
reader/writer, property dump, MI reader), `modkit/skm.py` (SKM render data parse/edit/write),
`modkit/skmgltf.py` (glTF/FBX export/import), `modkit/sm.py` (static meshes), `modkit/b4bmodel.py`
(model -> survivor / weapon pipeline, `b4bmod survivor|weapon`), `modkit/blender/b4bfit.py` (headless Blender fitting),
`modkit/blender/preview.py` (headless renders), `tools/modkit/testassets/mpfb_survivor.py` (test human),
`modkit/dotnet/pakx` (offline extraction, run via `modkit/b4bmod.py`). Guides: modkit/docs/meshes.md.

## TL;DR
- **Format solved.** `skm.py` parses the whole cooked `USkeletalMesh` native part and writes it back
  **byte-identically for 593/593 retail `*_SKM` packages** (heroes, zombies, weapons, props; cloth, vertex colours,
  8 influences, 16/32-bit indices included). The "unknown 8-byte trailer" is not a new field in `USkeletalMesh`: it is
  a TRS **bool32 in `FSkeletalMeshRenderData::Serialize`** before the stock `NumInlinedLODs`/`NumNonOptionalLODs` bytes,
  then the stock empty `DummyObjs` array (details §1).
- Ray-tracing data: every retail LOD stores an **empty** `TArray<uint8> RayTracingData` (int32 0). RT geometry is built
  at runtime; a writer only has to write the count.
- **glTF round trip is lossless** for geometry: export retail → glTF → import gives identical positions, UVs, indices
  and skin weights (tangents ±1 LSB). Through Blender 5.1 (import + export): positions/UVs/indices identical, tangents
  recomputed by Blender (MikkTSpace), weights identical once "all bone influences" is on.
- **New meshes from Blender import** onto a template mesh (its skeleton, physics asset, material slots, LOD settings):
  a box figure built in Blender imports as a valid package (CUE4Parse reads it; our parser re-reads it).
- **Verified in game** (2026-09-25, Proton, `launch/multi.sh 1`, mission Evansburgh): (1) retail Holly Elite_04 3P
  and FP meshes edited by `skm.py edit --inflate` render inflated and animate normally; (2) a box figure made in
  headless Blender and imported with `skmgltf.py import` onto Mom Elite_04 (3P, 1 LOD, adjacency stripped) and onto her
  FP arms renders, animates with `3P_Hero_ABP`/`FP_Hero_ABP` and takes the template's materials. No mesh warnings in
  the log (§4).
- **Real models end to end, verified live** (§8, 2-player sessions, Proton): a clothed, textured MakeHuman character
  (CC0, from an **FBX** with a Mixamo rig) as Mom's Elite 04 outfit (3P + FP arms, 5 decimated LODs, own textures), and
  a CC0 AK (**FBX** + PNGs) as the AR02 (FP skeletal, 3P skeletal, 3P/pickup static meshes, dropped magazine), both as
  add-ons. Seen: survivor idle/aiming in Fort Hope and a mission, FP arms holding weapons, the AK in first person, the
  FP **reload animation moving our magazine**, the 3P AK in a bot's hands; add-on player vs. vanilla player both ways.
  Not seen: firing / muzzle flash (fire can't be simulated), the pickup and dropped-magazine static meshes in the world.
- `b4bmod survivor|weapon` (`modkit/b4bmodel.py`) does it in one command (~40 s / ~30 s): fit in Blender (bone map, pose
  fit onto the template joints, weights, slots/atlases, LOD decimation), cook, build the textures into the template's own
  texture packages, retarget weapon skins. FBX, glTF, OBJ, DAE, .blend in; unrigged characters get the template's weights.
- **Static meshes solved**: `sm.py` parses/writes cooked `UStaticMesh` **byte-identically for 2118/2118 retail meshes**
  (§1b); the section's extra TRS bool is `bVisibleInRayTracing`. **Other players see a weapon as a static mesh**
  (`3P_<Code>_SM` on the weapon actor's `BaseStaticMesh_3P`), not `3P_<Code>_SKM`.
- No retail survivor, FP-arms or weapon mesh has morph targets (0 of 320 hero/weapon SKMs): faces are bone-driven, so
  the importer never has to write morphs. Weapon **skins** are material sets for the retail UVs: the pipeline points
  every skin MI of the weapon at the model's textures (a player with a skin equipped sees the model as made).

## 1. Cooked layout (static RE of Back4Blood.exe + 593-file validation)
Functions (VA, build 14216215):

| Function | VA | Notes |
|---|---|---|
| `USkeletalMesh::Serialize` | 0x144006BD0 | stock order; bCooked read inline; `DummyObjs` at 0x1440073E8 (0x14262A0D0) |
| `FSkeletalMeshRenderData::Serialize` | 0x144022EE0 | LOD array (0x144031BB0), **bool32 → +0x16**, u8 NumInlinedLODs (+0x11), u8 NumNonOptionalLODs (+0x12); +0x13/+0x14 = Num - NumInlined; +0x15 = owner `bSupportRayTracing` (+0x198 bit 0) |
| `FSkeletalMeshLODRenderData::Serialize` | 0x14401FB20 | stock 4.25 |
| `…::SerializeStreamedData` | 0x14401F770 | stock 4.25 + `RayTracingData` (0x143F9CDE0, TArray<uint8>) last |
| `…::SerializeAvailabilityInfo` | 0x14401F970 | only for non-inlined LODs (none in retail) |

Before loading, `USkeletalMesh::Serialize` seeds the render data's +0x16 with bit 2 of the mesh flag byte +0x168
(SDK order at +0x168: bUseFullPrecisionUVs, bUseHighPrecisionTangentBasis, **bExplicitCPUSkinning**, bCPUAccessOnly,
bHasBeenSimplified, bHasVertexColors (bit 5, confirmed: gates the colour buffer), …, bEnablePerPolyCollision (bit 7)),
so the TRS bool is most likely the cooked `bExplicitCPUSkinning`. Retail value: 0 in all 593 meshes.

Export data after the tagged properties (all little-endian, `bool` = 4 bytes, `strip` = 2 bytes FStripDataFlags):
```
int32 bHasGuid (0)
strip (1,0)                                   editor data stripped
FBoxSphereBounds ImportedBounds               7 floats
TArray<FSkeletalMaterial>                     {i32 Material, FName SlotName, bool bImportedName (0) [FName],
                                               FMeshUVChannelInfo {bool, bool, float[4]}}
FReferenceSkeleton                            TArray<{FName, i32 Parent}>, TArray<FTransform 40B>, TMap<FName,i32>
bool bCooked (1)
TArray<FSkeletalMeshLODRenderData>            below
bool  b4b_render_flag (0)          <- TRS (render data +0x16)
uint8 NumInlinedLODs (= LOD count) <- stock
uint8 NumNonOptionalLODs (0)       <- stock
TArray<UObject*> DummyObjs (0)     <- stock
```
Old reading ("stock DummyObjs = 4 bytes, B4B = 8 bytes `00 00 NN 00 00 00 00 00`") = CUE4Parse reading the TRS bool as
NumInlined/NumNonOptional (0, 0) and then the real NumInlined (NN), NumNonOptional (0) and DummyObjs as "8 bytes".

LOD (`FSkeletalMeshLODRenderData`):
```
strip (1,0); bool bIsLODCookedOut (0); bool bInlined (1 in all retail)
TArray<int16> RequiredBones                   all ref bones for LOD0-3
TArray<FSkelMeshRenderSection>:
   strip (1,0); u16 MaterialIndex; u32 BaseIndex; u32 NumTriangles; bool bRecomputeTangent
   (no RecomputeTangentsVertexMaskChannel byte: FRecomputeTangentCustomVersion is 1)
   bool bCastShadow; u32 BaseVertexIndex; TArray<FMeshToMeshVertData 64B> ClothMappingData
   TArray<u16> BoneMap; i32 NumVertices; i32 MaxBoneInfluences; i16 CorrespondClothAssetIndex
   FClothingSectionData {FGuid, i32}; [strip.class & 1 == 0:] DuplicatedVertices {TArray<u32> DupVertData,
   TArray<{u32 Length, u32 Index}> DupVertIndexData}; bool bDisabled
TArray<int16> ActiveBoneIndices               = bone maps + all ancestors, sorted
u32 BuffersSize                               = exact byte size of the streamed block below (verified)
-- streamed block --
strip (1, class)                              class & 1 = CDSF_AdjacencyData stripped
FMultisizeIndexContainer                      u8 size (2|4), bulk {i32 elemsize, i32 count, data}; absolute vertex indices
FPositionVertexBuffer                         i32 stride 12, i32 n, bulk float3
FStaticMeshVertexBuffer                       strip; i32 NumTexCoords; i32 n; bool FullPrecisionUVs; bool HighPrecTangents;
                                              bulk {TangentX, TangentZ} FPackedNormal (int8/127; TangentX.w 127,
                                              TangentZ.w = binormal sign ±127); bulk UV (half2 or float2), vertex-major
FSkinWeightVertexBuffer                       strip; bool bVariableBonesPerVertex (0); u32 MaxInfluences (4|8);
                                              u32 NumBones (= n*Max); u32 n; bool b16BitBoneIndex (0);
                                              bulk u8 [indices into the section BoneMap x Max, weights x Max (sum 255)];
                                              lookup: strip, i32 0, bulk (4, 0)
[bHasVertexColors] FColorVertexBuffer         strip; i32 stride 4; i32 n; bulk BGRA
[!adjacency stripped] FMultisizeIndexContainer adjacency (12 indices per triangle)
[any section has cloth] FSkeletalMeshVertexClothBuffer  strip; bulk raw; TArray<{u32,u32}> index mapping
TMap<FName, FRuntimeSkinWeightProfileData>    empty in retail
TArray<uint8> RayTracingData                  empty in retail
```
Retail variety (593 SKMs, 2281 LODs): 4 or 8 influences, never variable/16-bit; 72 LODs with 32-bit indices; weapons
use float UVs, characters half; 1148 LODs with vertex colours (all white on heroes); 30 LODs with cloth; adjacency
present everywhere (retail PC cooks keep it). FP arms: 2 LODs; 3P heroes: 5; zombies: 6.

Static meshes: `FStaticMeshSection` has an extra `bVisibleInRayTracing` bool (CUE4Parse `StaticMesh.HasVisibleInRayTracing`);
no static mesh writer yet (weapons and characters are skeletal).

## 1b. Static meshes (cooked `UStaticMesh`, 2118/2118 byte-identical)
Validated on every `*_SM` under `Items/` and `Environments/Props/[A-C]*` (`sm.py roundtrip`). Stock 4.25 cooked layout
plus the TRS section bool. After the tagged properties:
```
bool bHasGuid; strip; bool bCooked; i32 BodySetup; i32 NavCollision; FGuid LightingGuid; TArray<i32> Sockets
TArray<FStaticMeshLODResources>:
   strip (1, 8 = RT resources stripped); TArray<FStaticMeshSection> {i32 MaterialIndex, u32 FirstIndex, u32 NumTriangles,
     u32 MinVertexIndex, u32 MaxVertexIndex, bool bEnableCollision, bool bCastShadow, bool bForceOpaque,
     bool bVisibleInRayTracing (TRS / 4.26)}
   float MaxDeviation; bool bIsLODCookedOut; bool bInlined (all retail: inlined)
   SerializeBuffers: strip; FPositionVertexBuffer; FStaticMeshVertexBuffer (as in SKMs); FColorVertexBuffer
     (stride 0 when empty); FRawStaticIndexBuffer x5 {bool b32Bit, bulk u8, bool bShouldExpandTo32Bit}: indices,
     reversed (empty in retail), depth-only (= the full index list in retail), reversed depth-only (empty),
     [wireframe: editor, stripped], adjacency (present, empty); per-section + whole FWeightedRandomSampler
     {TArray<float>, TArray<i32>, float} (all empty in retail)
   FStaticMeshBuffersSize {SerializedBuffersSize = payload bytes of all vertex + index buffers, DepthOnlyIBSize,
     ReversedIBsSize} (checked on all 2118)
u8 NumInlinedLODs; strip (distance fields); per LOD bool bValid [+ FDistanceFieldVolumeData] (none valid in the set)
FBoxSphereBounds; bool bLODsShareStaticLighting; 8 x FPerPlatformFloat {bool, float} screen sizes
bool bHasOccluderData; bool bHasSpeedTreeWind; TArray<FStaticMaterial {i32, FName, FMeshUVChannelInfo}>
```
The tagged `ExtendedBounds` (what the engine culls with) is rewritten with the bounds. Collision is a separate
`BodySetup` export (tagged `AggGeom` + cooked PhysX data); the writer keeps the template's (weapons: one box), so a model
of about the template's size keeps sensible collision. `sm.py import` writes plain models (node transforms applied);
`sm.py from-skinned` moves a model fitted onto a skeletal template into a static template's space with the rigid
transform that maps the retail skeletal mesh onto the retail static one (area-weighted surface moments: centroid,
principal axes, signs from the third moment; retail 3P_AR02_SKM -> 3P_AR02_SM and pickup: same size to 0.5 cm, rotated
90° about X; magazine part -> `AR02_MagEmpty_3P_SM`: identity rotation).

**Every material slot must keep a section.** A 3P static weapon with only the slots the model uses (2 of 4) was
invisible on other players (bVisible stayed off on `BaseStaticMesh_3P`); with one zero-area triangle per unused slot,
in slot order like retail, it renders (§8). Game code addresses the weapon's sections by index (skins, attachments,
the magazine during reload), so `from-skinned` always writes all slots. (Skeletal meshes didn't need it.)

## 2. Tools
```
# offline extraction from the retail paks (no game running; same bytes as the agent's dumpassets): modkit/dotnet/pakx via
# the modkit (AES key built in; `b4bmod config aes_key`, B4B_AES_KEY or --aes-key override it)
modkit/b4bmod.sh extract '/Game/Characters/Heroes/Holly/*'          # into ~/.local/share/b4b-coop/extract
modkit/b4bmod.sh find 'Heroes/Holly/.*_SKM$'

.venv/bin/python modkit/skm.py info <x_SKM.uasset>            # bounds, materials, LODs, sections
.venv/bin/python modkit/skm.py roundtrip <files...>           # parse + write, byte compare
.venv/bin/python modkit/skm.py edit <in.uasset> <out.uasset> --inflate 2.5 --scale-section '*:5:1.5' --material '*:1:4'
.venv/bin/python modkit/skmgltf.py export <x_SKM.uasset> <out.glb>     # reference for Blender
.venv/bin/python modkit/skmgltf.py import <template_SKM.uasset> <in.glb|.fbx> <out/Gobi/Content/.../x_SKM.uasset> [--lod <LOD1 model>]... [--lods N] [--socket NAME=x,y,z] [--bone NAME=x,y,z]
.venv/bin/python modkit/sm.py info|roundtrip|sizes <x_SM.uasset>...      # static meshes
.venv/bin/python modkit/sm.py import <template_SM> <model> <out> [--lod ...] | from-skinned <SM> <SKM> <out> <lod0.glb>... [--only-bone mag]
.venv/bin/python modkit/upkg.py props <x.uasset> [export]              # tagged properties, readable
modkit/b4bmod.sh survivor|weapon <model> ... -o mymod [--install]   # the whole pipeline (§6; modkit/docs/meshes.md)
.venv/bin/python tools/b4bpak.py pack <out dir containing Gobi/> <mods dir>/b4bmod_x.pak
```
Blender: File > Import > glTF (the export), keep the armature, edit or replace the mesh (vertex groups named after
bones), name materials after the template's slots (`skm.py info` lists them; Blender's `.001` suffixes are ignored),
File > Export > glTF with **Include > "All Bone Influences"** on. Headless test: `blender -b --python
modkit/blender/blocky.py -- <export.glb> <out.glb> fp|3p`.

Import rules (skmgltf.py):
- Coordinates: glTF = (x, z, y)·0.01 of UE. Swapping Y/Z is a reflection, so UE's clockwise triangles are glTF's
  counter-clockwise ones without reordering; bone quaternions (−x, −z, −y, w); tangent sign negated.
- Joints map to the **template mesh's** reference skeleton by name (a hero mesh has a subset of `3P_Biped_SK`: Holly
  160 of 179 bones). The vertices must be in the template's bind pose: joints that moved are reported
  (Blender re-orients bone axes on import, so only joint positions are compared; `--bind rebind` re-skins to the
  template pose using full matrices, only for rigs whose bone axes were kept).
- One section per template material slot; up to 8 influences (4 unless needed), u8 weights summing to 255, bone map
  per section (≤ 255 bones), duplicated-vertex buffers, active/required bones, bounds, BuffersSize are computed.
- Adjacency is **stripped** (`CDSF_AdjacencyData`, the engine's own path for platforms without tessellation). Vertex
  colours only if the template has `bHasVertexColors` (white by default). UV precision follows the template.
- LODs: each `--lod <model>` is a real LOD (b4bfit decimates them); `--lods N` without `--lod` writes N copies (old
  behaviour). The tagged `LODInfo` array is truncated to the LOD count and every `LODMaterialMap` entry set to -1.
- Input: glTF/glb as is; FBX/OBJ/DAE/.blend are converted by Blender (`blender/b4bfit.py convert`). The model must
  already be on the template's skeleton (bone names, bind pose); `b4bmodel.py`/`b4bfit.py` do that fitting.
- Kept from the template package: skeleton, physics/shadow physics asset, materials, sockets, sampling info, clothing
  assets (left unbound: no section references them). Templates with morph targets are refused.

## 3. What a survivor / weapon / FP arms replacement needs
- **3P survivor body**: `3P_<Hero>_…_SKM` on `3P_Biped_SK` (plus head/legs pieces for non-outfit rows); animated by
  `3P_Hero_ABP` (all heroes share it); physics asset comes from the template. Materials: the template's slots (MICs of
  B4B masters; new textures are the textures agent's side).
- **FP arms**: `FP_<Hero>_…_SKM` on **`FP_Biped_SK`** (197 bones: body, arms, fingers, plus all weapon bones and
  `camera`), 2 LODs, 2 slots (sleeve `Torso`, `ArmSkin`). Its bind pose is **not** the 3P one (≈1.09× taller, arms
  differ), so FP arms are authored against the FP export (`skmgltf.py export FP_…_SKM`), not cut from the 3P body.
- **FP weapons**: `Items/Weapons/<Type>/<Id>/Meshes/<Id>_SKM` are also on `FP_Biped_SK` (weapon bones `gun`, `mag`,
  `bolt`, …), float UVs, vertex colours, 2 LODs. **3P weapons** (`3P_<Id>_SKM`) have their own skeleton (`AR01_SK`).
- **What other players see**: the weapon actor (`AR02_1_BP_C` ...) has `BaseSkeletalMesh_1P` (the FP SKM),
  `BarrelStaticMesh_1P`/`HipSightStaticMesh_1P`/`ADSSightStaticMesh_1P` (attachments), and for third person
  **`BaseStaticMesh_3P` = `3P_<Id>_SM`** (+ `BarrelMesh_3P`, `SightMesh_3P`). `3P_<Id>_SKM` exists for AR01, AR02,
  LMG01, Sni01 only; we replace it too.
- **Who references which weapon mesh** (name maps of all 115,815 retail `.uasset` headers, `b4bmod extract --regex
  '\.uasset$'`, 4 s, 321 MB): world pickups `<Id>_N_Pickup_BP` show **`3P_<Id>_SM`** (with `Skin_Default/*_3P_Glint_MI`
  overrides), not `<Id>_Pickup_SM`: no package references `AR02_Pickup_SM`, `HG01_Pickup_SM`, ... (guns; melee
  `Hatchet01_Pickup_SM` is used). The dropped magazine is the Cascade system `VFX/Systems/EmptyMags/
  VFX_EmptyMag_<Id>_3P_P` (mesh particles of `<Id>_MagEmpty_3P_SM` with `Weapon_<Id>_Mag_FP_MI`), fired by the 3P
  reload montage `BaseAnims3P/.../WPN_<Id>_Stand_Reload_AM`; only 8 exist (AR02, AR03, AR04, HG01, HG03, LMG02, SMG02,
  SMG03; `HG04_Mag_Empty_3P_SM` is only in `ChendaVendor_BP`). `3P_AR02_SKM` is used only by two Finleyville church
  cinematics; `3P_LMG01_SKM` is LMG01's 3P weapon (`LMG01_BP`); `3P_AR01_SKM`, `3P_Sni01_SKM` are unreferenced.
  Other 3P pieces in the weapon BPs: `3P_<Id>_Ironsights_SM` (AR01/03/05, SG02, SMG05, Sni01/02),
  `3P_AR04_Carry_Handle_SM`.
- `b4bmod weapon` infers the meshes from the FP mesh's folder (listing only, no extraction): `3P_<Id>_SKM`,
  `3P_<Id>_SM` + `<Id>_Pickup_SM` (names compared without `_` and case), the folder's one `*Empty*_SM`; the rest is
  printed as "not replaced". Without a 3P SKM the static meshes come from the FP fit (`sm.py from-skinned` with the FP
  SKM as reference): retail FP SKM and 3P SM have the same principal sizes (AR02 17.5/4.5/1.1 vs 17.9/4.4/1.1 cm, HG01,
  SMG01, SG01, AR03, Sni01 alike), rotated 90° about X. Static meshes' own textures (e.g. `SMG02_Primary_3P_N_T`) are
  built too (fit set -> static slot by MI, MI name without `_FP/_3P`, or base colour). AK on AR02 with only `--fp-mesh`:
  pak byte-identical to the explicit-path run (sha256 d49da7d1...). Live: a spawned `AR02_1_Pickup_BP` shows our AK
  (`cheatprobe spawnactor`, `pickup2_crop.png`), with the pickup glint sweep of `Skin_Default/*_3P_Glint_MI` over it.
  Dropped magazine not seen: `cheatprobe emitter VFX_EmptyMag_<Id>_3P_P` shows nothing for the retail HG01 either (the
  test path, not the mesh), and an emptied-clip reload by a bot 1.5 m away showed no falling magazine in 12 frames.
- Sockets: the FP weapon SKM has `SkeletalMeshSocket` exports relative to `gun` (`muzzle`, `holo`, `scope`, `laser`,
  ...); the 3P weapon skeleton has a `muzzle` **bone**. `skmgltf.py import --socket muzzle=x,y,z` / `--bone muzzle=...`
  move them (b4bmodel does it from the model's muzzle marker or barrel tip).
- Not supported: new clothing simulation (cloth assets are PhysX/NvCloth data), morph targets (heads), new bones
  (the mesh's ref skeleton must be a subset of the Skeleton asset), new material masters.

## 4. In-game results (live, 2026-09-25)
Setup: `launch/install.sh` (models dev build), `B4B_INI_EXTRA='modpaks=Z:\...\meshes\paks_edit' launch/multi.sh 1`,
`b4b.py mission Easy`, then in the mission `mdl mesh <hero#> CharacterMesh0|FirstPersonArms <path>` and `mdl bring/look`
to put meshes on bots in front of the camera. Screenshots in `~/.local/share/b4b-coop/meshes/shots/` (not committed).

| Test | Pak | Result |
|---|---|---|
| Retail edit, 3P: `3P_Holly_Elite_04_SKM --inflate 4` (all 5 LODs, cloth section included) | mounted at startup | Bot Holly is visibly puffed up next to a bot wearing the unmodified `…_NonCloth_SKM` (`cmp1.png`, `fp_fat.png`); animation, materials, cloth normal |
| Retail edit, FP: `FP_Holly_Elite_04_SKM --inflate 2.5` | same | thick first-person arms/sleeves (`fp_fat.png`) |
| New mesh, 3P: Blender box figure → `3P_Mom_Elite_04_SKM` (1 LOD, LODInfo truncated, no adjacency, 3 sections on slots Head/Torso/Legs) | `mountpak` at runtime (order 1100), then `mdl mesh` | box figure "[BOT] KARLEE" walks, aims, follows (`blocky7.png`) with Mom's textures |
| New mesh, FP: Blender box arms → `FP_Mom_Elite_04_SKM` (1 LOD) | same | box forearms/hands holding the weapon (`blocky1.png`) |

Log: `paks: precacher: first read from a mod pak (…b4bmod_meshtest.pak)`; no LogSkeletalMesh/LogStreaming entries for the
modded packages (the only streaming errors are retail `MAP_Evansburgh_B_Debug` ones). With the test instance's low
settings `r.SkeletalMeshLODBias=1`, so edits must cover LOD1+ (both tools write every LOD they keep).

## 5. Next / open
- Firing / muzzle flash position and the dropped magazine are not seen live yet (pickup: seen, §3) (fire and weapon
  drops can't be triggered unattended; `giveitem` replaces without dropping).
- ADS: the sight line follows the template's `ironsights` bones; a model with a different sight height aims slightly
  off through its own sights (the AK is within ~1.5 cm). Moving those bones per weapon is untested.
- Hair: done (§9).
- Face animation: the model's face is skinned to `head` (and jaw if the rig has one); B4B's face bones (eyelids, lips)
  don't move it. `--weights transfer` copies the template face's weights instead (untested in game).
- New bones (rebuild the ref skeleton from the Skeleton asset), cloth, new material masters: not supported.
- Hitboxes follow the template's physics asset; everyone sees their own add-ons (addons.md §7).

## 6. How the model pipeline works (`b4bmodel.py`, `blender/b4bfit.py`)
- **Bone map** (character): source bone names are matched as UE4 mannequin names (as is), Mixamo (`mixamorig:Hips`
  -> pelvis, `Spine/Spine1/Spine2` -> spine_01..03, `LeftArm` -> upperarm_l, `LeftHandIndex1` -> index_01_l, ...) or
  3ds Max Biped (`Bip01 L UpperArm`, `L Finger0`...); the table with most hits wins, `--bonemap {"src": "b4b"}` adds or
  overrides. Required: pelvis, spine_01, head, both arms (upperarm/lowerarm/hand) and legs (thigh/calf/foot).
- **Orientation/scale**: frames from the mapped joints (lateral = right->left upper arm, up = pelvis->head); B4B heroes
  face +X. Uniform scale = template head-to-feet height / source's.
- **Pose fit**: every mapped bone, root first, gets a pose that puts its joint on the template joint and aims it at the
  template's next joint (upperarm -> lowerarm, hand -> middle_01, spine_03 -> neck_01, ...), with a stretch along the
  bone's own axis (no shear: `inherit_scale NONE`). Result on the MakeHuman test: 0.00 cm joint error. The armature
  modifier is then applied: the mesh sits in the template's bind pose (A-pose), which is what the game skins against.
- **Weights**: source groups renamed to template bones; unmapped source bones (twist, extra face bones) go to the
  nearest mapped ancestor. `--twist template` (default) splits each limb weight among the template's twist bones
  (`upperarm_twist_01`, `lowerarm_twist_01`, `elbow_twist_01`, `wrist_twist_01`, `thigh_twist_01`, `calf_twist_01`,
  ...) in the proportions of the nearest template vertices (so forearm roll twists like retail). No armature:
  the model is stood up by its bounding box (`--facing`, default -Y = Blender's front) and gets the template's weights
  from the 4 nearest template vertices (`--weights transfer`); arms must already be in an A-pose. Checked with
  `preview.py --pose test` (bent limbs) on the template, the rigged fit, the unrigged fit and a UE-named rig: all
  deform alike.
- **FP arms**: the same fit against the `FP_Biped` export (its own bind pose), then only faces whose vertices are >= 50 %
  weighted to arm bones stay (hands + sleeves). A probe run tells which materials survive in first person.
- **Slots, texture sets, atlases**: each source material goes to a template slot (`--slot MAT=SLOT`). A *texture set*
  is what a slot's material instance samples; b4bmodel finds which slots share one (retail FP `ArmSkin` samples the
  outfit's `Torso` textures with the skin master) and packs every material that must live in one set into a grid atlas
  (UVs remapped into tiles; clamped to 0..1). The same layout is used for 3P and FP. Tile images are composed in Blender
  (numpy): base colour (sRGB as is), normal (green flipped: glTF/Blender maps are OpenGL style; `--normal-dx` if yours
  are DirectX), PBR = R AO, G roughness, B metallic (retail convention, checked on weapon and hero PBR textures), A and
  any other owned texture (hair multimask ...) = the retail texture's average; microtile masks = 0 (no retail fabric
  detail on our UVs). Only textures in the template's own folder are replaced; shared ones never. Encoded by
  `b4bmod texture` (format and sRGB of the original; size = source size x grid, max 4096).
- **LODs**: Blender Decimate (collapse) per LOD ratio (`--lods 1,0.5,0.3,0.15,0.06`), weights kept. `skmgltf.py import`
  writes them as real LODs, keeps the template's `LODInfo` (screen sizes) for as many LODs as written and sets every
  `LODMaterialMap` entry to -1 (retail LOD3/4 remap sections to `*_LOD` materials by section index, which would
  scramble ours). UV channel count = the template's (extra channels = UV0). Up to 8 influences (retail LOD0 uses 8).
- **Weapons**: parts are separate objects; names pick the bone (`mag|magazine|clip|drum` -> mag, `bolt|slide`,
  `trigger`, `charging handle`, `safety|selector`, `ejector|dust cover`, `hammer`, `stock`, `cylinder`; `--part RX=BONE`),
  the rest is `gun`. The model is turned (`--forward/--up`, default +X/+Z), scaled to the template's length
  (`--scale fit`), and moved so its trigger object sits on the template's trigger bone (else bounding-box centres). The
  muzzle is an empty named `muzzle` or the barrel tip; it moves the FP `muzzle` socket and the 3P `muzzle` bone. Static
  meshes (3P, pickup) come from the 3P fit via `sm.py from-skinned`, the dropped magazine from its `mag` part.
- **Skins** (`--skins retarget`, default): every `Skin_Sets/*_MI` of the weapon (also `/Game/TUxx/...`), extracted with
  `b4bmod extract --regex`, gets `Base Surface Texture`/`Base Normal`/`PBR` pointed at the textures we wrote for the same
  part and view (51 of the AR02's 132 skin MIs: the receiver and magazine parts the AK uses; one 3P MI without own
  texture parameters inherits from its FP parent).
- Test asset (not committed; `tools/modkit/testassets/mpfb_survivor.py`, dev only): MPFB 2.0.17 (Blender extension, GPL) + the MakeHuman system
  asset pack (CC0): male, `male_casualsuit05` + `shoes03` + `short02` hair + low-poly eyes, eyebrows baked into the skin
  texture (hero heads are opaque), Mixamo rig, FBX + PNGs. Weapon: "AK" by loafbrr (opengameart.org/content/ak, CC0):
  FBX with separate Bolt/Magazine/Trigger objects, PBR PNGs (albedo, normal, roughness, metalness, AO).

## 7. Guides (for mod makers)
Moved to the kit: [modkit/docs/meshes.md](../../modkit/docs/meshes.md) ("Make a survivor model", "Make a weapon
model", doing the fitting by hand, lower-level tools). The mod maker runs `b4bmod survivor|weapon <model> ...`, which
extracts the templates and what they reference (`tree`), runs `modkit/b4bmodel.py`, packs the add-on (`--install`
installs it); `b4bmodel.py` stays usable directly (`b4bmod model ...` or `python modkit/b4bmodel.py ...`).

## 8. Live results: real models (2026-09-25, Proton, `launch/multi.sh 2`, add-ons per instance via `addons_dir=`)
Add-ons: `survivor_mh.pak` (37 files, cosmetic: textures, meshes) and `ak47_loafbrr.pak` (v1.2: 136 files incl. 51 skin
MIs, cosmetic: textures, materials, meshes). Screenshots in `~/.local/share/b4b-coop/fullmodel/shots/` (not committed).
Dev helpers added: `giveitem <slot> row <DataTable> <Row>` (AR02 = `Weapons_DT DF038C6A4ED79AB7FDCF9CAB8D742DC7`, found by
parsing `Weapons_DT` for `AR02_1_BP`), `poke <addr> <bytes>` (clip count: `ClipAmmoComponent` +0x2AC).

| Session | Test | Result |
|---|---|---|
| 1: host add-ons, client none | Fort Hope, both `/model mom_elite_04`, host looks at the client (`fh_host_3p*.png`) | our survivor, idle animation, jacket/jeans/boots/hair/face; client sees vanilla Mom Elite 04 on the host (`fh_client_look*.png`) |
| 1 | mission, host FP (`m_host_fp.png`) | our FP arms (skin hand, jacket sleeve) holding the SMG; client in 3P shows our model holding a pistol (`m_host_3p_client.png`) |
| 2: client add-ons, host none | Fort Hope: client sees the host as our survivor (`fh2_client_sees_host.png`), host sees vanilla (`fh2_host_sees_client_vanilla.png`); login `2c0g,c91d7af3c,c27afddbc` accepted by `addons_policy=cosmetic` | as expected both ways |
| 2 | AR02 given to both: client FP = our AK geometry with the profile's graffiti skin (before skin retargeting); 3P static AK **invisible** on host hero and bot | fixed by keeping one section per slot (§1b) |
| 2b | same, new `3P_AR02_SM` | host hero (our survivor) holds our 3P AK, visible (`m3_client_host_front.png`) |
| 3: host add-ons v1.2 | host FP holds our AK with our textures despite the equipped skin (`m4_host_fp.png`) | skin retargeting works |
| 3 | clip poked to 0 -> automatic reload (`m4_auto1..6.png`) | FP reload animation: left hand pulls **our magazine** out and inserts it (mag bone), clip 0 -> 20 |
| 3 | bot Heng given AR02, teleported in front of the host (`m4_bots_near.png`) | our 3P AK (static mesh, our textures) in the bot's hands |

No `Fatal`/`LogSkeletalMesh`/`LogStaticMesh` errors in any log. Key presses (`cheatprobe key 0x52` = R, bound to
`AbilityReload`) and mouse clicks don't trigger game actions in an unfocused test window (chat keys do), so reload was
triggered by emptying the clip and firing wasn't tested.

## 9. Hair (models-next, 2026-09-25)
- `Master_Hair_M`: BLEND_Masked, two-sided, DitherOpacityMask, shading model from the material. Every retail hero
  hair MI sets static switch **Enable MultiMask** (+ Use AO, Use PDO, useFacingAO, UsesVertexColors, Variation,
  useFlowMapTexture, SubtractTipFromDepth). Textures: `Hair MultiMask` (the outfit's own `*_Hair_MM_T`, BC7 RGBA) +
  shared Holly `Root/Alpha/ID/Depth` (DXT1, unused with MultiMask on: the MM's channels look like them). On UV0 of
  Mom Elite 04's hair section, MM **A** is the strand coverage (0.27 mean at triangle centres vs 0.24 image mean, 0 in
  gaps); RGB inside strands ~(0.45, 0.28, 0.3). No colour texture: colour = `RootColor` -> `TipColor` (vectors).
- Retail hair vertex colours are mostly **black** (8 % white): with UsesVertexColors, `Vertex Color Multiplier`
  (Mom Elite 04: 0.51, 0, 0.536 = purple) tints the white-coloured strands. Our meshes were white everywhere ->
  purple hair (seen live, `hair_face2_crop.png`).
- Pipeline (`b4bmodel.py`/`b4bfit.py`): a model material put on a slot whose master is `Master_Hair_M` (`--slot
  hair=Hair`) gets role `hairmm`: A = the model's alpha (alpha map, else base colour's alpha), RGB = the retail MM's
  in-strand average; the Hair MI (if owned by the template's folder) gets `RootColor` = 0.6 x and `TipColor` = the
  model's mean hair colour (linear), via `b4bmod mi`; the hair section's vertex colours are written black
  (`skmgltf.import_gltf(slot_colors=)`).
- Live (MakeHuman `short02` hair, 2048² RGBA with alpha, on Mom Elite 04 `Hair`): dark brown hair with see-through
  strand edges at fringe and sideburns (`hair_face3_crop.png`, `hair_face3_zoom.png`) instead of the opaque helmet.
  Screenshots in `~/.local/share/b4b-coop/fullmodel/shots_next/` (not committed).
- Limits: one colour gradient per hair (no per-strand texture colour: the master has none); FP arms never show hair.
