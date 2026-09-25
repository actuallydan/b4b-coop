# Models (skeletal meshes): from an FBX to the game

Replace a survivor outfit, first-person arms or a weapon with your own model, without the Unreal editor. Your model
is fitted onto one of the game's meshes (the *template*): it keeps the template's skeleton, animations, material slots
and physics, and takes your geometry. Setup first: the kit's README.md. Commands are written as `b4bmod ...`
(Windows: `b4bmod.cmd`, Linux: `./b4bmod.sh`).

Needs **Blender** (free, blender.org; 4.2 LTS or newer; tested with 5.1). b4bmod finds it on PATH or in
`C:\Program Files\Blender Foundation\`; otherwise `b4bmod config blender "<path to blender.exe>"`.

## What goes where
| You want to replace | Template mesh | Skeleton |
|---|---|---|
| A survivor outfit, third person (what others and cameras see) | `.../<Hero>/Meshes/Elite/Elite_NN/3P_<Hero>_Elite_NN_SKM` (or head/torso/legs pieces under `Base/`) | `3P_Biped_SK` (179 bones; a hero mesh uses a subset) |
| The same outfit's first-person arms | `.../FP_<Hero>_Elite_NN_SKM` | `FP_Biped_SK` (197 bones, **another bind pose**: about 1.09x taller, arms differ) |
| A weapon in first person | `/Game/Items/Weapons/<Class>/<Code>/Meshes/<Code>_SKM` | `FP_Biped_SK` (weapon bones `gun`, `mag`, `bolt`, ...) |
| The same weapon in the world / on other survivors | `.../Meshes/3P_<Code>_SKM` | its own (`<Code>_SK`) |

A survivor needs both the 3P and the FP mesh, and a weapon both its FP and 3P mesh, or players see your model in
one view and the original in the other. Fit each one against its own template export (the FP pose is not the 3P pose).

## Step by step
1. **Pick the template** and look at it:
   ```
   b4bmod find "Heroes/Walker/Meshes/Elite/Elite_00/.*_SKM$"
   b4bmod mesh info /Game/Characters/Heroes/Walker/Meshes/Elite/Elite_00/3P_Walker_Elite_00_SKM
   ```
   Lines like `mat 0 Walker_Elite_00_A_Arms_FP_MI Arms` are the **material slots** (slot 0 is named `Arms`),
   `lods N` is the LOD count, `bones` the skeleton size.
2. **Export the template** and open it in Blender:
   ```
   b4bmod mesh export /Game/.../3P_Walker_Elite_00_SKM walker_ref.glb
   ```
   Blender: File > Import > glTF 2.0 > `walker_ref.glb`. You get the armature and the game's mesh, one material per
   slot, named after the slots. The size is real-world (1 Blender unit = 1 m = 100 Unreal units).
3. **Bring in your model**: File > Import > FBX (or glTF). Then:
   - Scale, turn and move it onto the reference mesh. It must be in the **same pose** as the reference (the template's
     bind pose): pose your model's own rig to match first, apply the pose, then remove your rig.
   - Skin it to the **reference armature**: select your mesh, then the armature, Ctrl+P > With Automatic Weights.
     Better for clothes: copy the game's weights with a Data Transfer modifier on your mesh (source: the reference
     mesh, Vertex Data > Vertex Groups, Nearest Face Interpolated), apply it, then parent with Ctrl+P > With Empty
     Groups. Vertex groups must have the bone names of the armature.
   - Don't rename, add, move or delete bones. Bones your mesh doesn't use are fine.
   - **Materials**: one material per slot you use, named exactly like the slot (`Head`, `Torso`, `Legs`, `Arms`,
     `ArmSkin`, ...). Blender's `.001` endings are ignored. Other names: `--material <your name>=<slot number>` on
     import.
   - Delete the reference mesh (keep the armature).
4. **Export**: select the armature and your mesh.
   - FBX: File > Export > FBX, Limit to Selected Objects, Object Types Armature + Mesh, Armature: **Add Leaf Bones
     off**. b4bmod converts the FBX with Blender (in the background) before importing it.
   - or glTF: File > Export > glTF 2.0 (.glb), Include > Limit to Selected Objects, Data > Skinning > **Include All
     Bone Influences**.
5. **Import into the game's format**:
   ```
   b4bmod mesh import /Game/.../3P_Walker_Elite_00_SKM mywalker.fbx -o mymod
   ```
   It prints how far each joint is from the template's bind pose (should be about 0 cm: the pose from step 3 is
   right) and which slot each material went to, and writes `mymod/Gobi/Content/.../3P_Walker_Elite_00_SKM.uasset` +
   `.uexp`. LODs: as many as the template, each a copy of your mesh (lower graphics settings draw LOD1 and up);
   `--lods N` to change that.
6. **Textures**: your model has its own UV layout, so the template's textures won't fit it. Replace the textures its
   materials use with yours (textures.md): `b4bmod tree <template>` lists each slot's material and its Base Color,
   Normal Map and PBR/mask textures; then `b4bmod texture <that texture> <your png> -o mymod` for each. Textures are
   often shared: the 3P outfit and its FP arms both use `..._Arms_BC_T`, variants A/B/C share normal maps. A texture
   you replace changes every mesh that uses it, so replace those meshes too (or make a material that points at new
   textures: textures.md §4).
7. **Pack and install**:
   ```
   b4bmod pack mymod -o walker_mymodel.pak --title "My Walker" --author you --version 1.0 --category survivors --zip
   b4bmod install walker_mymodel.pak
   ```
   Start the game, check `/addons`, pick that outfit for the survivor.

## Limits
- The skeleton, animations, hitboxes (physics asset) and material slots stay the template's. No new bones.
- No cloth simulation for your mesh (the template's cloth is left unused), no morph targets (face shapes: templates
  with morph targets are refused, which excludes most heads).
- LODs are copies of your mesh, no automatic simplification: keep the polygon count reasonable.
- Static meshes (props, weapon pickups `_SM`) are not supported yet.
- Only on your PC: other players see the normal model.

Status: Blender-made meshes verified in game on a survivor (3P) and first-person arms, and the FBX path through this
command; a complete real survivor model and a weapon end to end are being verified. Format details and evidence:
docs/investigations/mesh-mods.md in the b4b-coop repository.
