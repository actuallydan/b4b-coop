# Mesh mods: B4B skeletal mesh format, writer and glTF import (#18, #21, epic #23)

Status 2026-09-25, build 14216215. Branch `models-meshes`. Tools: `tools/modkit/upkg.py` (package reader/writer),
`tools/modkit/skm.py` (SKM render data parse/edit/write), `tools/modkit/skmgltf.py` (glTF export/import),
`tools/modkit/blender/blocky.py` (headless Blender test mesh), `tools/modkit/pakx` (offline extraction).

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

## 2. Tools
```
# offline extraction from the retail paks (no game running; same bytes as the agent's dumpassets)
DOTNET_ROOT=vendor/dotnet vendor/dotnet/dotnet build -c Release tools/modkit/pakx
P="$HOME/.local/share/Steam/steamapps/common/Back 4 Blood/Gobi/Content/Paks"
KEY=0x0208250257E8EA16828509DEBF23D703A5B509FE4F15F33F11BEE4BAB1F97CFD
DOTNET_ROOT=vendor/dotnet vendor/dotnet/dotnet tools/modkit/pakx/bin/Release/net10.0/pakx.dll "$P" $KEY \
    vendor/oodle/liboodle-data-shared.so ~/.local/share/b4b-coop/meshes/extract 'Heroes/Holly/.*_SKM\.(uasset|uexp)$'

.venv/bin/python tools/modkit/skm.py info <x_SKM.uasset>            # bounds, materials, LODs, sections
.venv/bin/python tools/modkit/skm.py roundtrip <files...>           # parse + write, byte compare
.venv/bin/python tools/modkit/skm.py edit <in.uasset> <out.uasset> --inflate 2.5 --scale-section '*:5:1.5' --material '*:1:4'
.venv/bin/python tools/modkit/skmgltf.py export <x_SKM.uasset> <out.glb>     # reference for Blender
.venv/bin/python tools/modkit/skmgltf.py import <template_SKM.uasset> <in.glb> <out/Gobi/Content/.../x_SKM.uasset> [--lods N]
.venv/bin/python tools/b4bpak.py pack <out dir containing Gobi/> <mods dir>/b4bmod_x.pak
```
Blender: File > Import > glTF (the export), keep the armature, edit or replace the mesh (vertex groups named after
bones), name materials after the template's slots (`skm.py info` lists them; Blender's `.001` suffixes are ignored),
File > Export > glTF with **Include > "All Bone Influences"** on. Headless test: `blender -b --python
tools/modkit/blender/blocky.py -- <export.glb> <out.glb> fp|3p`.

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
- LODs: `--lods N` writes N copies of the imported mesh and truncates the tagged `LODInfo` array to N (default 1).
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
- Authoring kit (#21): wrap export → Blender → import → pack into one `b4bmod` command; ship a Blender how-to.
- A real model (not boxes) end to end, incl. a hero with head/torso/legs pieces and a weapon (`AR01_SKM`, float UVs).
- Proper LODs (decimation) instead of copies; morph targets (heads); new bones (rebuild the ref skeleton from the
  Skeleton asset); static mesh writer (`bVisibleInRayTracing`, distance fields).
- Whether hero master materials use tessellation (adjacency is stripped on import; boxes rendered fine).
- Everyone in a session needs the same paks (hitboxes follow the physics asset, which stays the template's).
