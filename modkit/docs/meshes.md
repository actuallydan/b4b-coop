# Models: from an FBX to the game

Replace a survivor outfit (third person and first-person arms) or a weapon with your own model, without the Unreal
editor. Your model is fitted onto one of the game's meshes (the *template*): it keeps the template's skeleton,
animations, material slots and physics, and takes your geometry and textures. Setup first: the kit's README.md.
Commands are written as `b4bmod ...` (Windows: `b4bmod.cmd`, Linux: `./b4bmod.sh`).

- [Blender](#blender) · [What goes where](#what-goes-where)
- [Make a survivor model](#make-a-survivor-model) · [Make a weapon model](#make-a-weapon-model)
- [Doing the fitting yourself](#doing-the-fitting-yourself-mesh-import) · [Lower level](#lower-level) · [Limits](#limits)

## Blender
Models need **Blender** (free, GPL): [blender.org/download](https://www.blender.org/download/), tested with 5.1
(4.2 LTS or newer should work). b4bmod runs it in the background (no window); you only open it yourself for checking
or for [doing the fitting yourself](#doing-the-fitting-yourself-mesh-import).
- Windows: the installer (or the portable zip). b4bmod finds it in `C:\Program Files\Blender Foundation\`; otherwise
  `b4bmod config blender "C:\...\blender.exe"`.
- Linux / Steam Deck: your distribution's package, the blender.org tarball, or Flatpak/Steam (then point b4bmod at
  the executable: `b4bmod config blender /path/to/blender`).
- **No add-ons or extensions needed.** FBX, glTF and OBJ import/export are built into Blender; the pipeline only uses
  Blender's own Python (with the numpy it ships). DAE (Collada) needs Blender 4.x: Blender 5 removed it.
- MPFB / MakeHuman is **not** needed: it only made the kit's own test character.

`b4bmod status` shows the Blender it found.

## What goes where
| You want to replace | Template mesh | Skeleton |
|---|---|---|
| A survivor outfit, third person (what others and cameras see) | `.../<Hero>/Meshes/Elite/Elite_NN/3P_<Hero>_Elite_NN_SKM` (or head/torso/legs pieces under `Base/`) | `3P_Biped_SK` (179 bones; a hero mesh uses a subset) |
| The same outfit's first-person arms | `.../FP_<Hero>_Elite_NN_SKM` | `FP_Biped_SK` (197 bones, **another bind pose**: about 1.09x taller, arms differ) |
| A weapon in first person | `/Game/Items/Weapons/<Class>/<Code>/Meshes/<Code>_SKM` | `FP_Biped_SK` (weapon bones `gun`, `mag`, `bolt`, ...) |
| The same weapon in other survivors' hands (what other players and bots show) **and lying in the world** as a pickup | `.../Meshes/3P_<Code>_SM`, a **static** mesh | none (one piece) |
| The empty magazine a reload drops (AR02-04, HG01, HG03, LMG02, SMG02-03: a particle effect showing this mesh) | `.../Meshes/<Code>_MagEmpty_3P_SM` (names vary: `*Empty*_SM`) | none |
| `<Code>_Pickup_SM` | no game file uses it for guns (replaced anyway) | none |
| (AR01, AR02, LMG01, Sni01 only) the 3P skeletal weapon: LMG01 in other survivors' hands, AR02 in cutscenes | `.../Meshes/3P_<Code>_SKM` | its own (`<Code>_SK`) |

A survivor needs both the 3P and the FP mesh, and a weapon its FP mesh and its static meshes, or players see your
model in one view and the original in the other. `b4bmod survivor` / `b4bmod weapon` do all of them in one command;
for a weapon you only name the FP mesh, the others are found next to it.

A static mesh must keep **one section per material slot** of the template, in slot order, even for slots your model
doesn't use: otherwise the game hides the whole mesh (a weapon becomes invisible in other survivors' hands). The
pipeline does this for you (unused slots get an invisible zero-size triangle).

## Make a survivor model
1. **Your model**: one FBX (or glTF/OBJ/.blend) of a human in an **A-pose** (arms ~45° down) or T-pose, with its
   textures next to it. Best: rigged (Mixamo auto-rigger, a UE4 mannequin rig, 3ds Max Biped); unrigged works if it
   stands in an A-pose (weights come from the game's mesh). Clothes/hair/eyes may be separate objects and materials.
   Alpha hair cards work on the outfit's **Hair** slot (`--slot <hair material>=Hair`): the alpha becomes the game's
   hair mask, the colour the average of your hair texture. Elsewhere only opaque materials look right (bake eyebrows
   into the skin texture).
2. **Pick the outfit to replace** (it keeps its skeleton, animations, physics). An Elite outfit is a whole survivor,
   head included:
   ```
   b4bmod find "Heroes/Mom/Meshes/Elite/.*_SKM$"
   ```
3. **See its slots**: `b4bmod mesh info /Game/TU11/Characters/Heroes/Mom/Meshes/Elite/Elite_04/3P_Mom_Elite_04_SKM`
   (lines like `mat 4 ... Head`, `mat 5 ... Torso`). Skin goes on the head slot (skin shader), clothes on Torso/Legs
   (outfit shader).
4. **Run the pipeline** (3P + FP arms + textures, then the add-on):
   ```
   b4bmod survivor mymodel.fbx ^
       --outfit /Game/TU11/Characters/Heroes/Mom/Meshes/Elite/Elite_04/3P_Mom_Elite_04_SKM ^
       --fp     /Game/TU11/Characters/Heroes/Mom/Meshes/Elite/Elite_04/FP_Mom_Elite_04_SKM ^
       --slot body=Head --slot jacket=Torso --slot hair=Hair --slot eyes=Torso --slot boots=Legs ^
       -o mymod --title "My survivor" --author you --version 1.0 --zip
   ```
   (`^` continues a line in the Windows Command Prompt; on Linux use `\`, or write it all on one line.)
   - It extracts the templates and every material and texture they use, fits your model in Blender (~1 minute),
     writes `mymod/Gobi/Content/...` and packs it into `mymod.pak` (+ `mymod.zip` for sharing with `--zip`).
     `--pak name.pak` names it, `--install` also installs it, `--no-pack` stops before packing.
   - `--slot` names are your model's material names; b4bmod lists them and stops if one has no slot.
   - Textures are found through the materials' image nodes, else by file name next to the model
     (`<material>_albedo/_normal/_roughness/...`), or given: `--tex jacket=textures/Jacket_` (a file prefix or a
     folder). Normal maps are taken as OpenGL/glTF style; `--normal-dx` if yours are DirectX style.
   - More options: `--lods 1,0.5,0.3,0.15,0.06` (LOD ratios), `--bonemap map.json` (your bone names -> the game's),
     `--weights transfer` (take the game mesh's weights), `--work DIR` (keep the intermediate glTF/PNGs there).
     All of them: `b4bmod model help`.
5. **Check** before the game (optional): render the fitted model with its limbs bent, and look for stretched or stuck
   vertices:
   ```
   blender -b --python blender/preview.py -- <work>/fit3p/lod0.glb out.png --pose test --views side,front3q
   ```
   Run it in the kit folder; `blender` is the path `b4bmod status` shows. `<work>` is printed at the end of step 4
   (or give `--work DIR` there).
6. **Install and test**: `b4bmod install mymod.pak`, start the game, wear the outfit (customization screen, or chat
   `/model mom_elite_04`). Other players see it only if they have the add-on too.

## Make a weapon model
1. **Your model**: an FBX with **separate objects per moving part**, named like `Magazine`, `Bolt`, `Trigger` (others
   stay on the gun), barrel along +X and up +Z (else `--forward -y --up +z` ...), real-world size (it is scaled to the
   template's length anyway). Optional: an empty named `muzzle` at the barrel end. Textures next to it.
2. **Pick the weapon to replace** with a similar shape (reload animations move the template's magazine bone: an AK for
   AR02, an M4 for AR01): `b4bmod find "Weapons/Assault/AR02/Meshes/"`.
3. **See its slots**: `b4bmod mesh info /Game/Items/Weapons/Assault/AR02/Meshes/AR02_SKM`. The 3P and static meshes
   follow the FP mesh's slots by material.
4. **Run**:
   ```
   b4bmod weapon ak.fbx --fp-mesh /Game/Items/Weapons/Assault/AR02/Meshes/AR02_SKM ^
       --slot AkMaterial=AR02_Reciever_M --slot Ammunition=AR02_Mag_M ^
       --tex AkMaterial=Textures/AK_1/AK_1_ --tex Ammunition=Textures/Ammunition/Ammunition_ ^
       -o mymod --title "My AK" --author you --version 1.0 --zip
   ```
   - `--fp-mesh AR02` (just the code) works too. The other meshes are found in the FP mesh's folder and printed:
     ```
     weapon: --3p-mesh /Game/Items/Weapons/Assault/AR02/Meshes/3P_AR02_SKM
     weapon: --static /Game/Items/Weapons/Assault/AR02/Meshes/3P_AR02_SM
     weapon: --static /Game/Items/Weapons/Assault/AR02/Meshes/AR02_Pickup_SM
     weapon: --mag-static /Game/Items/Weapons/Assault/AR02/Meshes/AR02_MagEmpty_3P_SM
     weapon: not replaced (the game's own look stays): ...
     ```
     Giving one of `--3p-mesh`, `--static`, `--mag-static` yourself replaces what was found for that flag; `none`
     (e.g. `--mag-static none`) leaves it out; `--no-infer` turns the search off. "Not replaced" lists the folder's
     other meshes, e.g. `3P_AR01_Ironsights_SM`: a separate 3P sight piece that stays on top of your model unless you
     add it with `--static` (it then gets your model too; a blank mesh isn't supported yet).
   - Weapons without a 3P skeletal mesh (all but AR01, AR02, LMG01, Sni01) get their static meshes from the
     first-person fit (the game's FP and 3P meshes are the same size).
   - It also points all of the weapon's skins at your textures (`--skins keep` leaves them alone: then an equipped
     skin shows its own textures on your model).
   - Moving parts: `--part "REGEX=BONE"` if your object names differ (`mag`, `bolt`, `trigger`, `charging_handle`,
     ...). Orientation: `--forward`, `--up`; size: `--scale fit|<factor>`.
5. **Check**: the log prints the scale, where the muzzle went and which object became which part; `blender/preview.py`
   (as above) on `<work>/fitfp/lod0.glb`.
6. **Install and test** as above; the weapon is the same item (AR02) with your look.

## Doing the fitting yourself (`mesh import`)
If you'd rather fit and skin the model in Blender by hand (or `b4bmod survivor` can't map your rig), import it onto
one template mesh at a time:
1. `b4bmod mesh export /Game/.../3P_Walker_Elite_00_SKM walker_ref.glb`; in Blender: File > Import > glTF 2.0. You
   get the armature and the game's mesh, one material per slot, named after the slots. Real-world size (1 Blender
   unit = 1 m = 100 Unreal units).
2. File > Import > FBX (your model). Scale, turn and move it onto the reference mesh, in the **same pose** (the
   template's bind pose: pose your own rig to match, apply the pose, remove your rig). Skin it to the **reference
   armature** (Ctrl+P > With Automatic Weights; for clothes better: a Data Transfer modifier copying the reference
   mesh's vertex groups, then Ctrl+P > With Empty Groups). Don't rename, add, move or delete bones. One material per
   slot, named like the slot (`Head`, `Torso`, ...; `.001` endings are ignored). Delete the reference mesh.
3. Export the armature and your mesh: FBX with **Add Leaf Bones off**, or glTF (.glb) with **Include All Bone
   Influences**.
4. `b4bmod mesh import /Game/.../3P_Walker_Elite_00_SKM mywalker.fbx -o mymod`. It prints how far each joint is from
   the template's bind pose (should be about 0 cm) and which slot each material went to. `--lods N` copies of your
   mesh as LODs (default: the template's count), `--material <your name>=<slot number>` for other names.
5. Textures: `b4bmod tree <template>` lists each slot's textures; `b4bmod texture <texture> <your png> -o mymod` for
   each (textures.md). Textures are often shared (the 3P outfit and its FP arms both use `..._Arms_BC_T`): a texture
   you replace changes every mesh that uses it.
6. `b4bmod pack mymod -o mymod.pak --title ... --zip`, `b4bmod install mymod.pak`. Do the FP arms the same way
   against the FP export (its own pose).

## Lower level
The scripts next to b4bmod, each with `-h` / a usage header; run them with the same Python:
- `skmgltf.py export <SKM.uasset> <out.glb>` (template to glTF), `skmgltf.py import <template SKM.uasset> lod0.glb
  <out.uasset> --lod lod1.glb ... [--socket muzzle=x,y,z] [--bone muzzle=...]`
- `blender -b --python blender/b4bfit.py -- character|weapon ...` (writes `lodN.glb` + `manifest.json`)
- `sm.py from-skinned <SM> <3P SKM> <out> lod0.glb ... [--only-bone mag]`, `sm.py import <SM> model.glb <out>`
- `b4bmod model textures <manifest.json> --mesh <SKM> -o mymod`; `skm.py info|edit`, `upkg.py props <x.uasset>`

## Limits
- The skeleton, animations, hitboxes (physics asset) and material slots stay the template's. No new bones.
- No cloth simulation for your mesh (the template's cloth is left unused), no morph targets (face shapes: templates
  with morph targets are refused, which excludes most heads). The face is skinned to `head` (and the jaw if your rig
  has one): it doesn't blink or talk.
- Hair: on the Hair slot, one colour from root to tip (the game's hair material has no colour texture); on any other
  slot alpha cards render as solid cards.
- The weapon's sights stay where the template's are: a model with a different sight height aims slightly off through
  its own sights.
- Only on your PC and for players who have the add-on: others see the normal model.

Status: verified in game (2026-09-25): a MakeHuman survivor (3P and FP arms, animated, both players' views) and an
AK on the AR02 (first person with reload moving the model's magazine, other survivors' hands, skins retargeted). Not
seen in game yet: the muzzle flash position, the dropped magazine. Hair with alpha on the Hair slot: seen in game. Format details and evidence:
docs/investigations/mesh-mods.md in the b4b-coop repository.
