# Models: from an FBX to the game

Replace a survivor outfit (third person and first-person arms) or a weapon with your own model, without the Unreal
editor. Your model is fitted onto one of the game's meshes (the *template*): it keeps the template's skeleton,
animations, material slots and physics, and takes your geometry and textures. Setup first: the kit's README.md.
Commands are written as `b4bmod ...` (Windows: `b4bmod.cmd`, Linux: `./b4bmod.sh`).

- [Blender](#blender) · [What goes where](#what-goes-where)
- [Make a survivor model](#make-a-survivor-model) · [How materials are placed](#how-materials-are-placed) ·
  [Texture sizes](#texture-sizes) · [Hair](#hair) · [Talking and blinking](#talking-and-blinking) ·
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
3. **Slots** (optional, normally not needed): your materials are put on the outfit's slots automatically, and the
   pipeline prints what went where and why: face, eyes and skin on the skin (head) slot (skin away from the head, such
   as bare arms, on the outfit's body skin slot when it has one, e.g. Walker's `ArmSkin`), hair, alpha cards, lashes and
   brows on the **Hair** slot (masked by the texture's alpha, in your texture's own colours: see [Hair](#hair)),
   clothes on the cloth slot that covers the same part of the body (sleeves and gloves on `Arms`, trousers and shoes on
   the legs' slot, hats on `Gear`), eye highlights (transparent overlays) left out. This works without meaningful
   names too (game rips with `Material #25` ...): see [How materials are placed](#how-materials-are-placed). To choose
   yourself: `b4bmod mesh info <outfit>` lists the slots (`mat 4 ... Head`, `mat 5 ... Torso`), then
   `--slot <your material>=<slot>` or `--slot <your material>=drop`; the rest stays automatic.
4. **Run the pipeline** (3P + FP arms + textures, then the add-on):
   ```
   b4bmod survivor mymodel.fbx ^
       --outfit /Game/TU11/Characters/Heroes/Mom/Meshes/Elite/Elite_04/3P_Mom_Elite_04_SKM ^
       -o mymod --title "My survivor" --author you --version 1.0 --zip
   ```
   (`^` continues a line in the Windows Command Prompt; on Linux use `\`, or write it all on one line.)
   - The first-person arms come along: the outfit's own `FP_..._SKM` from the same folder is used (printed as
     `survivor: first-person arms --fp ...`; a warning when the outfit has none). `--fp <FP SKM>` picks other arms,
     `--fp none` keeps the game's arms in first person.
   - It extracts the templates and every material and texture they use, fits your model in Blender (~1 minute),
     writes `mymod/Gobi/Content/...` and packs it into `mymod.pak` (+ `mymod.zip` for sharing with `--zip`).
     `--pak name.pak` names it, `--install` also installs it, `--no-pack` stops before packing.
   - Textures are found through the materials' image nodes (embedded ones too), else by file name next to the
     model (`<material>_albedo/_normal/_roughness/...`; a model with one material takes the one texture set in its
     folder), or given: `--tex jacket=textures/Jacket_` (a file prefix or a folder). File names win over how a
     texture is wired (a mask map plugged into base colour is used as a mask map). Understood: base colour,
     normal, roughness, gloss/smoothness, metallic, AO, ORM, Unity mask maps (R metallic, G AO, A smoothness),
     alpha/opacity. Maps named after a colour image are taken with it (`head.png` -> `head_n.dds`, `head_ao.dds`,
     `head_rough.png`), also when the material links only the colour. PNG, JPEG, TGA, BMP, WebP and **DDS**
     (BC1-BC7, as game files use) are read. Normal maps are taken as OpenGL/glTF style; `--normal-dx` if yours are
     DirectX style (two-channel BC5 and DXT5nm normal maps are understood).
   - More options: `--lods 1,0.5,0.3,0.15,0.06` (LOD ratios; models under ~1500 triangles keep every LOD whole; the
     distance LODs are made from the model welded by position, so flat-shaded models and UV seams stay closed; flat
     faces stay flat),
     `--max-verts N` / `--keep-density` (a model denser than the survivor's own LOD0, e.g. 100k vertices and 170k
     triangles, is decimated to the template's LOD0 budget: the face less than the rest, UV seams kept, cloth
     sections whole; the log prints `LOD0: 114462 -> 67967 vertices, 173852 -> 94094 triangles`. `--max-verts N`
     sets the budget, `--keep-density` keeps every triangle),
     `--bonemap map.json` (your bone names -> the game's), `--weights transfer` (take the game mesh's weights),
     `--max-texture 2048|4096` (largest texture made, see [Texture sizes](#texture-sizes)), `--proportions fit|0.5` (third person:
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

### How materials are placed
Without `--slot`, every material is placed from what the model says about it, most telling first:
1. **Names**: the material's name and its images' file names (`hair`, `lash`, `eye`, `skin`/`face`/`body`, `pants`,
   `shoes`, `gear`/`hat` ...), and the name of an object that has only that material (`TheHat`, `Backpack` -> gear).
2. **What its texels look like**, on the parts of the images its faces actually use (not the whole image): partly
   see-through -> hair cards; mostly skin colours -> skin. An image wired to the colour input that is really a normal
   map (blue ~1) is used as the normal map.
3. **Where it sits on the body**: a quick fit (a few seconds) tells, for every material, which part of the survivor's
   skeleton moves it (head, torso, arms, hands, legs, feet), and the same for each of the outfit's slots. Skin away
   from the head goes on the outfit's body skin slot, clothes on the cloth slot covering the same parts (the gear slot
   takes head-worn things and what is named like gear).
4. **Shared images**: a material placed by its look joins the materials that draw the same image; objects without a
   material take the image their UVs fit (painted texels no material uses: teeth and tongue on the body's image).

The log's table says which rule placed each material. `--slot` always wins, for the materials you name.

### Texture sizes
Each texture the pipeline writes is as big as your images need, never bigger: a 1024 image stays 1024 on a survivor
slot whose own texture is 2048, a model without normal or roughness maps gets small flat ones (256). Materials that
share one of the survivor's textures (an atlas) each get a part the size of their own image, packed into the smallest
power-of-two texture that holds them (square or 2:1). No texture is bigger than the survivor's own one it replaces
(retail hero heads and arms: 2048, bodies: 4096; hair: 2048): when the parts don't fit, the one with the most texels
for the area it covers gives way first (eyeball textures before the body, faces last), so faces keep their detail.
`--max-texture 1024|2048|4096` sets the largest size instead (4096 also above the survivor's own sizes). The log
prints each atlas (`texture set Head: atlas 2048x2048: body 1024x1024, face 1024x1024 ...`) and each texture's size.
The same model and options always give a byte-identical add-on.

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
Long hair, skirts, long coats and capes move with the character instead of sticking to the head and hips (on by
default; `--hair-physics off` / `--cloth off` turn them off):
```
b4bmod survivor model.fbx --outfit .../3P_Walker_Elite_00_SKM ...
b4bfit: cloth: collision capsules pelvis r15, spine_01 r13, spine_03 r14, thigh_l r9, thigh_r r11, calf_l r9, calf_r r8 cm
b4bfit: cloth: ['Coat'] -> 6672 cloth faces (lower), simulation mesh open panel over 315 deg, 10x19 (190 vertices), top 129 cm, hem 5 cm
  cloth: new clothing asset 3P_Walker_Elite_00_SKM_Clothing_0 in 3P_Walker_Elite_00_SKM.uasset (collision: 3P_Walker_Elite_07_Cloth_PA)
```
- `--hair-physics auto`: the hair behind and below the head goes on the survivor's ponytail bones, which the game
  swings with physics. Only survivors that have them: **Holly** (e.g. Elite 00; hair_00..02), Holly Elite 06,
  Walker Elite 03, Doc Elite 03, **Mom** (two pigtails). On others the log says so and the hair stays
  on the head. `--hair-swing 0.5` swings it half as much (hair that clips into the back).
- `--cloth auto`: garments that hang become cloth, which the game simulates: they sway when walking, trail and flare
  when running or turning, and settle when standing. Found by the material's name **or its colour image's name**
  (game rips name materials `Material #35` but the image `jacket.png`):
  - skirts and dresses (skirt, dress, kilt, gown; "bottoms" that cover the gap between the legs; trousers are left
    alone) and **long coats** (coat, jacket, trench, duster, robe, poncho, ...): everything below the waist swings.
    A coat that is open at the front gets an open cloth panel round the back, so its front halves swing apart
    instead of being closed into a tube; the sleeves stay on the arms.
  - **capes** (cape, cloak, mantle): hang from the shoulder blades behind the body.
  - Names alone aren't trusted: a garment must also **hang like one** (from above the crotch to well below it, wider
    than a strand of beads), and parts named like accessories (necklace, belt, strap, bracelet, boots, heels ...)
    stay skinned even when they share the dress's material. So boots whose material is called `dress` stay boots,
    and a necklace or a thigh strap cut from the dress's texture sheet doesn't swing with the skirt. The log says
    what was left out and why (`cloth: 'dress' named like a garment but doesn't hang like one: boot: ...`).
  - Only materials on a **clothing slot** swing: the game draws cloth only with materials made for it
    (`bUsedWithClothing`; the survivors' outfit and hair materials, not the skin ones). Garments (dress, skirt,
    coat, ... in the material name) always go to a clothing slot; one you put on a skin slot yourself stays skinned.
  - `--cloth <material>,<material>` picks them yourself; `<material>:cape` or `<material>:lower` says how it hangs
    when the guess is wrong.
- Works on **any outfit**: one that has cloth in the game (Holly Elite 00/04, Karlee Elite 06, Doc Elite 03, Walker
  Elite 07) lends its own; others get a clothing asset added to your mesh (copied from Walker Elite 07's coat, which
  `b4bmod` extracts for you). Several garments (a skirt and a cape) get one each.
- Each garment is tuned for what it is, from its shape and where its hem ends on the legs (the log says
  `cloth: tuned as long coat (hem at 0.70 of the leg, 74 cm): damping 0.60, gravity x1.50, ...`):
  | Garment | Found as | Moves |
  |---|---|---|
  | skirt | closed all round, hem above the knee | light and lively: sways, flares when turning |
  | long skirt / dress | closed, hem below the knee | a heavier hem, swings slower |
  | jacket tails | open front, hem at the upper thigh | held close (a few cm), settles fast |
  | coat / long coat | open front, hem at the knee or lower (blended by length) | heavy: tails swing out and settle, a weighted hem, no fluttering; upper part stays on the body |
  | cape | hangs from the shoulder blades, chest bare | trails and lifts off the back when running, slides over the back instead of sticking |

  Long coats and capes also collide with themselves (a fold doesn't pass through the rest of the garment).
  `B4B_CLOTH_TUNE='{"damping": 0.5, "gravity": 1.2}'` overrides values for experiments (keys: `damping`, `gravity`,
  `linear_drag`, `linear_inertia`, `angular_inertia`, `bend`, `friction`, `hem_mass`, `maxd` (max distance at the hem,
  share of the length), `maxd_exp`, `self_radius` (cm) ...; `modkit/cloth.py` `NV_DEFAULTS`/`GARMENTS`).
- The cloth bumps into the character's legs and body: capsules fitted to your model's own legs, hips and back, plus
  the game's leg capsules for skirts and coats.
- A garment made of single faces (no lining) gets its inside drawn when it swings open; skirts use the outfit's
  two-sided material variant when there is one.
- Check the cloth before the game: `blender -b --python blender/preview.py -- <work>/fit3p/lod0.glb preview.png --sim
  <work>/fit3p/manifest.json` draws the simulation meshes as red wire over the model.
- Both are shown to everyone who has the add-on (each machine simulates its own copy). Far away (the last two
  LODs) the garment is skinned, not simulated.

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
| Materials named `Material #25`, `Material #26` ... (a model from a game rip with generic material names) | placed by what they look like and where they sit ([How materials are placed](#how-materials-are-placed)); the table says why (`skin colours`, `clothes on the hands ...`, `object TheHat named like gear`) |
| `none (objects without a material ...): their UVs land on all_color.png where no material draws` | objects without a material (often teeth, tongue, the inside of the mouth) take the image their UVs fit: painted texels no other material uses. `none -> dropped`: no image fits; keep them with `--slot none=<slot> --tex none=<image>` |
| `draws the same image as Material #30` | a material placed by its look goes where the other materials with the same image went (one place in the texture instead of two) |
| `material x: maps named after head.png: head_n.dds (normal), head_ao.dds (ao)` | maps next to the colour image with its name and a suffix (`_n`, `_normal`, `_ao`, `_rough`, `_orm`, `_mask`, `_alpha` ...) are used with it |
| `... shares Material #30's tile (same textures)` | several materials draw the same image: they get one place in the texture, not one each |
| `hair.png has no cut-out alpha; the strands' mask is the alpha of hair_n.dds` | some games keep the hair's opacity in the normal map's alpha: it is used as the hair mask. A hat that uses the hair image too goes on Gear when its object is named like one (`TheHat`); else `--slot <its material>=Gear` |
| `... is linked as the colour but its texels are a normal map's` | an image wired to the colour input that is a normal map (blue ~1): used as the normal map |
| `hair_n.dds: not a normal map (blue channel 0.50 ...): left out` | a file named like a normal map that isn't one (a flow or specular map): the slot gets a flat normal map instead |
| `two-channel (BC5) normal map, Z rebuilt` / `DXT5nm (X in alpha)` | compressed normal map layouts of game files: read correctly |
| `Blender can't read this image` | an image format Blender doesn't know (rare DDS variants): convert it to PNG and point `--tex` at it |
| `N faces' UVs moved by whole texture repeats into 0..1 (same look)` | the model's UVs sit outside 0..1 by whole steps (game rips often at -1..0): moved back, the look doesn't change |
| `texture repeated 2x2 in the tile` | a tiling texture (UVs running over several repeats) that shares a texture with other materials: it is drawn repeated inside its tile (less sharp). For full sharpness give that material a slot of its own |
| `faces span more than one texture repeat: squeezed into the tile` | a few faces run over the edge of the texture: their UVs are pressed onto the edge (a small smear there) |
| `normals: 21 of 22 objects had their normals pointing inward ... turned around` | the file's normals pointed into the model while the faces point out (seen in game rips): it rendered nearly black, fixed automatically. `the model may be inside out`: in Blender select all, Mesh > Normals > Recalculate Outside, export again |
| `the file's unit scale looks off: ... x0.3937 gives 166 cm (centimetres stored as inches ...)` | the exporter's unit setting was wrong (or the armature is scaled). Harmless: the model is scaled to the survivor's height anyway; if it still looks wrong, apply the scale in Blender (Ctrl+A > All Transforms) and export again |
| `face: seeds from the eyes` | the head's bounds took in the neck, a beard or hair: the mouth is looked for relative to the eyes instead |
| `material x: its basecolor image 'y' is not next to the model` | the file references a texture on its author's disk: copy the images next to the model, or `--tex x=<folder or file prefix>` |
| `... is linked as base colour but its name says mask` | fine: the file name wins (a mask map is used as one) |
| `... is shared with other outfits; yours goes to ...` | the survivor's template shares that texture or material with other outfits: your copy is made in the outfit's folder, other outfits stay as they are |
| Hair is one colour / a bit dark | only with `--hair tint` (the game's hair shader: one colour root to tip, your texture's average, root 40 % darker). The default `--hair texture` keeps your texture's colours |
| Hair much darker / another colour than the texture file | the material multiplies the texture by a colour (glTF base colour factor, VRoid/MToon hair colour): that is applied, as in Blender / VRoid. VRM 0.x colours are read as sRGB, like VRoid does |
| Eyes look flat | eyes go on the skin slot (the game's eye shader has no texture for yours); a transparent iris layer goes on the hair slot, masked, in its own colours (`--hair tint`: in the hair colour); highlights are left out |
| Skirts, coats or capes don't swing | the log's `cloth:` lines say what was found; name the materials yourself with `--cloth MAT,...` (`MAT:cape` for a cape) ([Swinging hair and skirts](#swinging-hair-and-skirts)); long hair: a survivor with ponytail bones |
| A low-poly model turns into triangles at a distance | fixed: models under ~1500 triangles keep every LOD whole (older kits: `--lods 1,1,1,1,1`) |
| Holes, slits or loose triangles at a distance (flat-shaded models, VRM/glTF models along their UV seams) | fixed: the LODs are made from the model welded by position (older kits: shade smooth / merge by distance in Blender before exporting) |
| A coat flutters like a skirt, or a cape clings to the back | each garment is tuned from its shape and length (`cloth: tuned as ...` in the log); if the guess is wrong, `--cloth MAT:cape` / `MAT:lower`, or try values with `B4B_CLOTH_TUNE` ([Swinging hair and skirts](#swinging-hair-and-skirts)) |
| `texture set Head: atlas 2048x2048: body 1024x1024, ...` | how the materials sharing one texture were packed and how big each one's part is ([Texture sizes](#texture-sizes)) |
| `LOD0: ... is over the budget (the template's LOD0: ...)` | your model is denser than the survivor's own mesh: it was decimated to that (face kept finer, UV seams kept); `--keep-density` keeps every triangle, `--max-verts N` sets the budget |
| `cloth: '...' named like a garment but doesn't hang like one` / `stays skinned: named like an accessory` | a material named dress/skirt/coat sits on the feet or neck, or an accessory shares the dress's material: left skinned. If it really is a skirt: `--cloth <material>` |
| A dress renders dark grey, the game log says `missing bUsedWithClothing ... Default Material` | fixed: garments go to a clothing slot, and cloth only on slots whose material supports it. With `--slot dress=Arm` (a skin slot) the dress stays skinned |
| The add-on is big | textures are as big as your images (at most the survivor's own texture sizes); `--max-texture 2048` or `1024` for a smaller add-on, smaller images in your model do the same |
| The first-person arms are the old ones | the outfit has no `FP_..._SKM` in its folder (the log warns), or `--fp none` was given: give `--fp <FP arms SKM>` (the arms are cut from your own model: faces skinned to the arms) |

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
- Long hair swings only on survivors with ponytail bones. Skirts, long coats and capes swing on any outfit; other
  dangling parts (straps, pouches, a scarf's ends) move stiffly with the bone they hang from.
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
