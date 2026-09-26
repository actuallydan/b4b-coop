# Models: from an FBX to the game

Replace a survivor outfit (third person and first-person arms) or a weapon with your own model, without the Unreal
editor. Your model is fitted onto one of the game's meshes (the *template*): it keeps the template's skeleton,
animations, material slots and physics, and takes your geometry and textures. Setup first: the kit's README.md.
Commands are written as `b4bmod ...` (Windows: `b4bmod.cmd`, Linux: `./b4bmod.sh`).

- [Blender](#blender) · [What goes where](#what-goes-where)
- [Make a survivor model](#make-a-survivor-model) · [Hair](#hair) · [Talking and blinking](#talking-and-blinking) ·
  [Swinging hair and skirts](#swinging-hair-and-skirts) ·
  [Survivor troubleshooting](#survivor-troubleshooting) ·
  [Make a weapon model](#make-a-weapon-model)
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
1. **Your model**: one file of a humanoid, as you downloaded it: FBX, glTF/glb, **VRM** (VRoid and other avatar
   files), OBJ, DAE or .blend, with its textures next to it (or embedded in the file). Tested kinds of rigs:
   Mixamo, UE4 mannequin names, 3ds Max Biped, VRoid/VRM (`J_Bip_...`), Blender **Rigify** (the `DEF-` bones; the
   full rig works too), and rigs with other names that say what each bone is (`upper_arm.L`, `Arm_R`,
   `leg_joint_L_2` ...). Unrigged works too, in an A-pose, T-pose or with the arms hanging down (the weights come
   from the game's mesh; a model built from named parts like `head`, `torso`, `arm-left` keeps each part on its
   limb). Any size and proportions: it is scaled to the survivor's height and **keeps its own proportions** in third
   person (short legs stay short, a long neck stays long: the game fits its animations to each mesh's skeleton, as
   it does for the smaller female survivors); the first-person arms are fitted onto the survivor's arms, so hands
   hold the weapons like the game's. `--proportions fit` stretches the third-person model onto the survivor's
   skeleton instead (the old behaviour; a number such as `0.5` goes halfway). Clothes/hair/eyes may be separate
   objects and materials.
2. **Pick the outfit to replace** (it keeps its skeleton, animations, physics). An Elite outfit is a whole survivor,
   head included:
   ```
   b4bmod find "Heroes/Mom/Meshes/Elite/.*_SKM$"
   ```
3. **Slots** (optional): your materials are put on the outfit's slots automatically, and the pipeline prints what
   went where: skin, face and eyes on the skin (head) slot, hair, alpha cards, lashes and brows on the **Hair** slot
   (masked by the texture's alpha, in your texture's own colours: see [Hair](#hair)), clothes on the outfit's cloth slots by body zone (tops,
   trousers/shoes, gear), eye highlights (transparent overlays) left out. To choose yourself: `b4bmod mesh info
   <outfit>` lists the slots (`mat 4 ... Head`, `mat 5 ... Torso`), then `--slot <your material>=<slot>` or
   `--slot <your material>=drop`; the rest stays automatic.
4. **Run the pipeline** (3P + FP arms + textures, then the add-on):
   ```
   b4bmod survivor mymodel.fbx ^
       --outfit /Game/TU11/Characters/Heroes/Mom/Meshes/Elite/Elite_04/3P_Mom_Elite_04_SKM ^
       --fp     /Game/TU11/Characters/Heroes/Mom/Meshes/Elite/Elite_04/FP_Mom_Elite_04_SKM ^
       -o mymod --title "My survivor" --author you --version 1.0 --zip
   ```
   (`^` continues a line in the Windows Command Prompt; on Linux use `\`, or write it all on one line.)
   - It extracts the templates and every material and texture they use, fits your model in Blender (~1 minute),
     writes `mymod/Gobi/Content/...` and packs it into `mymod.pak` (+ `mymod.zip` for sharing with `--zip`).
     `--pak name.pak` names it, `--install` also installs it, `--no-pack` stops before packing.
   - Textures are found through the materials' image nodes (embedded ones too), else by file name next to the
     model (`<material>_albedo/_normal/_roughness/...`; a model with one material takes the one texture set in its
     folder), or given: `--tex jacket=textures/Jacket_` (a file prefix or a folder). File names win over how a
     texture is wired (a mask map plugged into base colour is used as a mask map). Understood: base colour,
     normal, roughness, gloss/smoothness, metallic, AO, ORM, Unity mask maps (R metallic, G AO, A smoothness),
     alpha/opacity. Normal maps are taken as OpenGL/glTF style; `--normal-dx` if yours are DirectX style.
   - More options: `--lods 1,0.5,0.3,0.15,0.06` (LOD ratios; models under ~1500 triangles keep every LOD whole),
     `--bonemap map.json` (your bone names -> the game's), `--weights transfer` (take the game mesh's weights),
     `--max-texture 2048` (smaller textures: an add-on about 4x smaller), `--proportions fit|0.5` (third person:
     stretch onto the survivor's skeleton instead of keeping the model's proportions), `--hair tint` (the game's hair shader, one
     colour: [Hair](#hair)), `--work DIR` (keep the intermediate
     glTF/PNGs there). All of them: `b4bmod model help`.
5. **Check** before the game (optional): render the fitted model with the textures made for the game, standing and
   with its limbs bent; look for stretched or stuck vertices:
   ```
   blender -b --python blender/preview.py -- <work>/fit3p/lod0.glb out.png --textures <work>/preview_textures_3p.json --views front,side,back
   blender -b --python blender/preview.py -- <work>/fit3p/lod0.glb out.png --textures <work>/preview_textures_3p.json --pose test --views front,front3q
   ```
   (the first-person arms: `fitfp/lod0.glb` with `preview_textures_fp.json`). Run it in the kit folder; `blender` is
   the path `b4bmod status` shows. Step 4 prints the command with `<work>` filled in (or give `--work DIR` there).
6. **Install and test**: `b4bmod install mymod.pak`, start the game, wear the outfit (customization screen, or chat
   `/model mom_elite_04`). Other players see it only if they have the add-on too.

## Talking and blinking
The survivors' faces are moved by face bones (jaw, lips, lids, brows ...): lip-sync while they speak, blinks,
expressions. The survivor pipeline rigs your model's face to those bones automatically, so your character talks and
blinks in game like the survivor it replaces (and everyone with the add-on sees it). It prints where it found the face:
```
b4bfit: face: eye_l, eye_in_l, eye_out_l from bone ORG-eye.L
b4bfit: face: mouth_l, mouth_r, lip_up, lip_lo, crease from mouth-open shape key
b4bfit: face: chin, nose from face profile
b4bfit: face: 1947 vertices skinned to face bones (575 jaw/lower lip, 681 eyelids, 200 eyeballs); 57 face bones moved onto the model's face
```
What helps it (best first): face bones in your rig (Rigify `DEF-`/`ORG-` lip, eye, chin bones; VRoid eye bones; a
`jaw` or eye bone in other rigs), shape keys for an open mouth and a blink (VRM/VRoid `A` and `Blink`, ARKit `jawOpen`
and `eyeBlinkLeft`, names with `mouth open`, `jaw open`, `blink`, `eyes closed`), eyes as their own mesh or material
(`eye`, `iris`, `sclera`), and otherwise the shape of the face seen from the side (nose, lip line, chin). What it
can't find is guessed from the survivor's face scaled to yours (`scaled from the template` in the log).
- **Check it before the game**: step 4 prints a preview command; `--face-pose` takes one of the survivor's mouth
  shapes (`AH`, `E`, `OW`, `MBP`, `CH`, `L`) or expressions (`Joy`, `Anger`, `Surprise`, `Sad`, `Fear` ...),
  `--face-blink 28` closes the lids like the game's blink:
  ```
  blender -b --python blender/preview.py -- <work>/fit3p/lod0.glb face.png --face <work>/face_preview.json --face-pose AH --textures <work>/preview_textures_3p.json
  ```
  `--face-view mouth` or `--face-view eyes` (with `--zoom 2`) looks closer.
- A mouth without an inside (no teeth, tongue or mouth material and nothing behind the lips) gets a dark mouth cavity,
  so an open mouth doesn't show a hole (`face: mouth: ... added a mouth cavity` in the log; `--mouth off` leaves it out,
  `--mouth on` adds it anyway).
- With a blink shape key (VRoid and most avatars) the eyes close like the key closes them, big anime eyes included; lashes
  and eyeliner that the key moves follow the lids. Without one, the lids take the survivor's lid weights.
- `face: WARNING: no eyes found`: the face won't blink. Give the eyes' positions: open the model in Blender, snap the 3D
  cursor onto each pupil (Shift+right-click), read its location (N panel, View tab) and add
  `--face-eyes 0.032,-0.105,1.62;-0.032,-0.105,1.62` (both eyes, metres, as the file imports into Blender).
- `--face off` leaves the face on the head bone (no talking or blinking), as before.

## Swinging hair and skirts
Two options make long hair and skirts move with the character instead of sticking to the head and hips:
```
b4bmod survivor model.vrm --outfit .../3P_Holly_Elite_00_SKM ... --hair-physics auto --cloth auto
b4bfit: hair: 3859 of 13057 hair vertices on the survivor's physics hair bones (hair_00,hair_01,hair_02)
b4bfit: cloth: ['F00_001_01_Bottoms_01_CLOTH'] -> 568 cloth faces, simulation mesh 7x20 (140 vertices), waist 105 cm, hem 72 cm
  cloth: section on slot flannel1 (two-sided variant of Body)
```
- `--hair-physics auto`: the hair behind and below the head goes on the survivor's ponytail bones, which the game
  swings with physics. Only survivors that have them: **Holly** (e.g. Elite 00; hair_00..02), Holly Elite 06,
  Walker Elite 03, Doc Elite 03, **Mom** (two pigtails). On others the log says so and the hair stays
  on the head. `--hair-swing 0.5` swings it half as much (hair that clips into the back).
- `--cloth auto`: a skirt or dress (a material named skirt/dress/kilt, or "bottoms" that covers the gap between the
  legs; trousers are left alone) becomes cloth: the game simulates it, it sways when walking, trails behind when
  running and settles when standing. `--cloth <material>,<material>` picks the materials yourself. Needs an outfit
  that has cloth in the game: **Holly Elite 00** (its tied flannel), Holly Elite 04, Karlee Elite 06, Doc Elite 03,
  Walker Elite 07 (tested: Holly Elite 00); other outfits: the log says so and the skirt is skinned to the legs as
  before. The skirt uses the outfit's two-sided variant of its material when there is one (else folds can show
  see-through gaps).
- Both are shown to everyone who has the add-on (each machine simulates its own copy). Far away (the last two
  LODs) the skirt is skinned, not simulated.

## Add an outfit
The survivor pipeline above **replaces** the template: everyone with your add-on sees your model instead of Mom's
Elite 04. Add `--as <name>` and it **adds** an outfit instead (Left 4 Dead style): nothing of the game is replaced, and
players put it on with the chat command `/model <name>`, on any survivor.
```
b4bmod survivor mymodel.fbx ^
    --outfit /Game/TU11/Characters/Heroes/Mom/Meshes/Elite/Elite_04/3P_Mom_Elite_04_SKM ^
    --fp     /Game/TU11/Characters/Heroes/Mom/Meshes/Elite/Elite_04/FP_Mom_Elite_04_SKM ^
    --slot body=Head --slot jacket=Torso --slot boots=Legs ^
    --as casual_joe -o mymod --title "Casual Joe" --zip
```
- `<name>`: lower-case letters, digits and `_` (up to 32, starting with a letter). It is what players type, so make
  it unique (`yourname_outfit`), not a survivor or outfit name the game has (`holly`, `mom_elite_04`: those win).
- Everything else works as above; the template is only the starting point (skeleton, slots, materials). The files go
  to `mymod/Gobi/Content/b4bcoop/outfits/<name>/` (your meshes, textures and the template's material instances,
  pointed at your textures), and `mymod/addoninfo.txt` gets a line the b4bcoop mod reads:
  `outfit=casual_joe|mom|/Game/b4bcoop/outfits/casual_joe/3P_..._SKM.3P_..._SKM|/Game/.../FP_..._SKM.FP_..._SKM|Casual Joe`
  (name | template survivor | third-person mesh | first-person arms | title; `--title` is the title). Several
  outfits in one add-on: run it once per outfit with the same `-o mymod` (the pack step packs all of them).
- In game: `/model list outfits` lists the outfits of your add-ons, `/model casual_joe` puts it on (third person and
  your first-person arms), `/model reset` takes it off. Nothing is saved.
- **Other players**: those with the same add-on see your outfit; those without it (or without b4bcoop) see your
  survivor in their base pieces (head, torso, legs of your profile), never an empty or broken model. The host lets it
  through without having the add-on; a host with `/models off` or `addons_policy=none` refuses it.
- Weapons: see "Add a weapon look" below.
- Picking it in the game's customization screen is not supported (only `/model`).

## Hair
Materials placed on the outfit's **Hair** slot (hair, alpha cards, lashes, brows, a transparent iris layer) are drawn
with your texture's own colours: gradients, highlights, dyed tips, a hair clip in the hair texture all stay. The
texture's alpha is the mask (below about 1/3 is see-through; soft edges are dithered, the game smooths them), both
sides of each card render. A colour the material multiplies the texture by (glTF base colour factor, VRoid/MToon
hair colour) is applied, so a grey VRoid hair texture comes out in the hair colour VRoid shows.

The game's own hair shader can't draw a colour texture, so the pipeline gives the slot a copy of a game material that
can (two-sided, masked like the game's hair, lit like cloth). `--hair tint` uses the game's hair shader instead: one
colour from root to tip (your texture's average), with its hair shine. Use it for hair of one colour if you prefer
that look.

## Survivor troubleshooting
What the survivor pipeline prints, and what to do about it.

| Message / what you see | Why, and the fix |
|---|---|
| `the model's skeleton: no bone found for pelvis, ...` | The bone names didn't say which bone is which. It lists what it recognised and writes `bonemap_template.json` (every bone of your model) into the work folder: fill in the missing ones (`"Bone_023": "upperarm_l"`), delete the rest, pass `--bonemap bonemap.json`. The b4b bones you need: pelvis, spine_01, head, upperarm/lowerarm/hand and thigh/calf/foot with `_l`/`_r` |
| `--bonemap: 'x' is not a template bone` / `the model has no bone 'x'` | a typo on the right / left side of your bone map |
| `orient: ... scale 0.01` or `scale 100` with a warning | the file's units are off (centimetres read as metres, or a scaled armature). The fit still works; if the model looks wrong, apply the scale in Blender (Ctrl+A > All Transforms) and export again |
| `proportions (model / survivor, same height): legs x0.67, torso x1.64, neck x0.70 ...; kept` | your model's segments against the survivor's at the same height. By default (`--proportions own`) they are **kept**: the third-person mesh gets a skeleton with your joints and the game fits the animations to it (feet on the ground, pelvis at your model's height). The first-person arms are always fitted onto the survivor's (`first-person arms: fitted onto the FP skeleton`): the game moves every first-person bone itself |
| `... stretched onto the survivor's joints` / `legs, torso differ by more than 25 %` | you ran `--proportions fit`: the model is stretched/squashed onto the survivor's skeleton (what the kit did before). Drop the option to keep your proportions, or give a number between 0 (fit) and 1 (own) |
| `pelvis 75.0 cm above the ground (survivor 102.9), head joint 164.9 cm ...` | where your model's hips and head end up. Very short or very long legs are fine; the game scales the hip motion to your pelvis height |
| Third person: the hands don't meet the weapon's grips | your model's arms are much longer or shorter than the survivor's (the proportions line says `arms x...`): the hands stay where your arms end. `--proportions 0.5` (halfway) or `fit` moves them to the survivor's; a survivor closer to your model's build (the female survivors are smaller) helps too |
| `torso ... fitted as one piece, stretched x1.00` | torso and neck are turned (and with `fit` stretched) as one piece each, so they don't bulge in bands |
| `shape keys ... removed` | face expressions / morphs: survivors have none; a mouth-open and a blink key are used to rig the face first ([Talking and blinking](#talking-and-blinking)) |
| `face: ... from scaled from the template` | that part of the face wasn't found on your model (see [Talking and blinking](#talking-and-blinking)); check the mouth with the face preview |
| The lip line opens in the wrong place (upper lip moves with the jaw) | give the model a mouth-open shape key or lip bones, or `--face off` |
| `unrigged: left arm is 50 deg from the template's pose` | an unrigged model not in an A-pose: it is un-posed automatically. If the arms come out bent or stuck to the body, rig the model (Mixamo auto-rigger, Blender Rigify) and try again |
| `materials -> slots (auto ...)` table | where each material went. Wrong? `--slot <material>=<slot>` or `=drop` |
| `material x: its basecolor image 'y' is not next to the model` | the file references a texture on its author's disk: copy the images next to the model, or `--tex x=<folder or file prefix>` |
| `... is linked as base colour but its name says mask` | fine: the file name wins (a mask map is used as one) |
| `... is shared with other outfits; yours goes to ...` | the survivor's template shares that texture or material with other outfits: your copy is made in the outfit's folder, other outfits stay as they are |
| Hair is one colour / a bit dark | only with `--hair tint` (the game's hair shader: one colour root to tip, your texture's average, root 40 % darker). The default `--hair texture` keeps your texture's colours |
| Hair much darker / another colour than the texture file | the material multiplies the texture by a colour (glTF base colour factor, VRoid/MToon hair colour): that is applied, as in Blender / VRoid. VRM 0.x colours are read as sRGB, like VRoid does |
| Eyes look flat | eyes go on the skin slot (the game's eye shader has no texture for yours); a transparent iris layer goes on the hair slot, masked, in its own colours (`--hair tint`: in the hair colour); highlights are left out |
| Skirts, long hair, capes don't swing | `--hair-physics auto --cloth auto` on a survivor/outfit that supports it ([Swinging hair and skirts](#swinging-hair-and-skirts)); capes and coats: not supported |
| A low-poly model turns into triangles at a distance | fixed: models under ~1500 triangles keep every LOD whole (older kits: `--lods 1,1,1,1,1`) |
| The add-on is 150-200 MB | 4096 textures (several materials packed into one texture set): `--max-texture 2048` |
| The first-person arms are the old ones | give `--fp` (the outfit's `FP_..._SKM`): the arms are cut from your own model (faces skinned to the arms) |

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

## Add a weapon look
The weapon pipeline above **replaces** the weapon for everyone who has your add-on. Add `--as <name>` and it **adds** a
look instead: the game's AR02 stays as it is, and a player who wants your model on their AR02 types `/model <name>`
(or presses Use in the `~` window's Models tab).
```
b4bmod weapon ak.fbx --fp-mesh AR02 ^
    --slot AkMaterial=AR02_Reciever_M --slot Ammunition=AR02_Mag_M ^
    --tex AkMaterial=Textures/AK_1/AK_1_ --tex Ammunition=Textures/Ammunition/Ammunition_ ^
    --as ak47 -o mymod --title "AK-47" --zip
```
- `<name>`: as for outfits (lower-case letters, digits, `_`, up to 32; unique, and not the name of an outfit).
- The files go to `mymod/Gobi/Content/b4bcoop/weapons/<name>/`: the first-person mesh, the third-person static mesh
  (`3P_<Code>_SM`, what other players see), the 3P skeletal mesh if the weapon has one, your textures and the
  weapon's default material instances pointed at them. `mymod/addoninfo.txt` gets
  `weapon=ak47|AR02|/Game/b4bcoop/weapons/ak47/AR02_SKM.AR02_SKM|/Game/.../3P_AR02_SM.3P_AR02_SM|/Game/.../3P_AR02_SKM.3P_AR02_SKM|AK-47`
  (name | weapon code | first-person mesh | 3P static mesh | 3P skeletal mesh | title). Keep the copies' names: the
  mod finds the weapon's meshes by those names. Several looks in one add-on: one run each, same `-o mymod`.
- A weapon the player drops keeps the look on the floor (the game shows `3P_<Code>_SM` there, your copy of it is
  put on); whoever picks it up gets their own choice. Left as the game's: weapon skins (your look replaces the skin
  while it is on), world pickups nobody dropped, the dropped magazine. Those flags (`--skins`, `--mag-static`, the
  pickup static mesh `<Code>_Pickup_SM`, unused by the game) are ignored with `--as`.
- **Other players**: those with the same add-on see your model in your hands (third person); everyone else sees the
  normal weapon, never an empty hand. The host lets it through without having the add-on; `/models off` or
  `addons_policy=none` on the host turn it off.

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
- Cloth only for a skirt/dress, and only on outfits that have cloth in the game; no morph targets (face shapes:
  templates with morph targets are refused). The face is rigged to the survivor's face bones (talks, blinks, expressions:
  [Talking and blinking](#talking-and-blinking)); your own face shapes are not kept.
- Hair: on the Hair slot, masked by the texture's alpha, lit like cloth (no anisotropic hair shine); on any other slot
  alpha cards render as solid cards.
- Long hair swings only on survivors with ponytail bones; capes, coats and other dangling parts move stiffly with the
  bone they hang from.
- The weapon's sights stay where the template's are: a model with a different sight height aims slightly off through
  its own sights.
- Only on your PC and for players who have the add-on: others see the normal model.

Tested (2026-09-25, in game with others watching): a VRoid anime character (VRM, 17 materials, alpha hair) on Holly,
a Mixamo-rigged monster (FBX, Unity mask map) on Walker, a UE4-named bulky man (FBX) on Hoffman, a short woman with a
Blender Rigify rig (glb) on Doc and an unrigged blocky character built from parts (FBX) on Karlee, each with its
first-person arms. Earlier: a MakeHuman survivor (3P and FP arms, animated, both players' views) and an
AK on the AR02 (first person with reload moving the model's magazine, other survivors' hands, skins retargeted). Not
seen in game yet: the muzzle flash position, the dropped magazine. Hair in its texture colours with alpha (VRoid,
MakeHuman afro), in Fort Hope and a mission: seen in game. Format details and evidence:
docs/investigations/mesh-mods.md in the b4b-coop repository.
