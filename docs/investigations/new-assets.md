# New assets under new paths: add-ons that add instead of replace (design, #20/#21, epic #23)

Status 2026-09-25, build 14216215. Package side (`b4bmod rename`, §2, §5) and added survivor outfits (§8:
`b4bmod survivor --as`, addoninfo `outfit=`, `/model <outfit>`) work live; weapons (§6) and the customization
screen are not built. Today every add-on replaces game files at their paths: a survivor model *is*
Mom's Elite 04 for whoever has the add-on. Goal: an add-on that brings an **extra** outfit (or weapon look) at its own
paths, selectable next to the game's, with nothing of the game's replaced.

## 1. What "adding" needs, piece by piece
| Piece | Replace (today) | Add (this design) |
|---|---|---|
| Packages (mesh, MIs, textures) | template's paths | copies under `/Game/b4bcoop/<addon id>/...` (§2) |
| References between them | template's own | rewritten to the copies (SKM -> MIs -> textures), shared retail ones kept (skeleton, physics asset, masters, micro-detail textures) |
| Something that names the outfit | the retail customization row | a row the agent adds at runtime (§3) |
| Selecting it | customization screen / `/model` | `/model <addon outfit>` (§4); the customization screen later |
| Other players | see it if they have the add-on | same, plus a fallback for those without (§4) |

## 2. Packages at new paths (`b4bmod rename`, prototyped)
- `b4bmod rename <asset> </Game/new/Path/Name> -o <moddir> [--ref </Game/old>=</Game/new>]...` writes a copy as a new
  package. Name-map entries are changed **in place** (package path, object name, and each `--ref` pair's package path
  and name), so every FName index in the export data (bone names, slots, properties) stays valid; asset-registry
  records and tag values are renamed alike. Textures go through the texture writer again (their inline mips and
  `SkipOffset` are absolute offsets that move with the header); `.ubulk` follows the new name.
- A chain: textures first, then MIs with `--ref <old texture>=<new texture>`, then the mesh with `--ref <old MI>=<new
  MI>`. Checked offline: `3P_Test_SKM` (from our fitted Mom Elite 04) -> `Test_Torso_MI` -> `Test_Torso_BC_T` under
  `/Game/b4bcoop/test/`; skm.py and upkg re-read them, the mesh's import table points at the new MI, the MI at the new
  texture, everything else still at the retail packages.
- Pipeline change: `b4bmod survivor|weapon ... --as <name>` would fit/cook as today, then rename every file it wrote
  (template-owned textures, MIs, meshes) to `/Game/b4bcoop/<addon>/<name>/...` with the refs rewritten. Template-owned
  MIs the pipeline didn't edit but the new mesh still uses (e.g. `Head_LOD_MI`, whose textures the pipeline replaced)
  must be copied too, or they would show the retail textures on the model's UVs. Rule: copy the template's whole
  owned material set.
- Why `/Game/b4bcoop/<addon id>/`: add-ons never collide with each other or with the game (the conflict check in
  addons.c is path-based and stays quiet), and the class stays **cosmetic** (addon.py classifies by file type; new
  paths don't change that).

## 3. The engine side: loading packages nobody references
- Loading: `LoadAsset_Blocking` / `StaticLoadObject` with an FSoftObjectPath resolves the package through the pak
  file list (`FPakPlatformFile`), not the asset registry: packages in a mounted add-on load like retail ones (§5).
  paks.c already mounts add-ons before the first load and exempts them from the signature checks.
- Not found through the **asset registry** (the cooked `AssetRegistry.bin` is the game's; add-on packages are not in
  it): `mdl reg`, primary-asset scans and anything that enumerates assets won't list them. So the agent must be told
  the paths: `b4bcoop-addoninfo.txt` gets lines like
  `outfit=<name>|<hero>|/Game/b4bcoop/<id>/<name>/3P_<...>_SKM|/Game/b4bcoop/<id>/<name>/FP_<...>_SKM`
  (`weapon=` likewise: an item code and its meshes; see §6).
- Package names: the loader looks for the object `<path>.<name>`; `rename` changes the export's name with the path,
  so the two match.

## 4. Selecting and replicating an added outfit
Reuse the NPC-body path of models.c (model-swap.md): the look travels as the hero's replicated
`PlayerSlot.CurrentCustomizationSet`, with a **made-up outfit row** `b4bcoop.addon.<addon id>.<name>` in the hero's own
customization table (LastEquipSlot Outfit). The game finds no such row and applies nothing; `tick_npc`-style code on
every b4bcoop machine sees the row, looks the name up in its own add-on list and sets `CharacterMesh0` (3P) and
`FirstPersonArms` (FP, the part NPC bodies lack) to the add-on's meshes, empties head/legs.
- `/model <name>` offers add-on outfits by name next to the retail ones (`/model` list, `/models`), only on the
  machine that has the add-on; the host's `/models off` and the ServerSelectCustomizationSet refusal treat
  `b4bcoop.addon.*` like `b4bcoop.npc.*` (known name or refuse).
- **Players without the add-on**: the row is unknown to them; the game applies nothing, so they keep seeing the
  outfit that was applied before (the survivor's own). Same as the "everyone sees only their own add-ons" rule
  (addons.md §7): no protocol-visible difference except the row name, which protocol 2 already carries for NPCs.
  A player with a different version of the add-on (same name, other content id) sees their own version.
- Saves: models.c's campaign-run hook already swaps foreign rows out before the host saves the run; add
  `b4bcoop.addon.*` to it. The profile is never written by `/model`. If the customization screen is ever used (below),
  a profile could hold an add-on row: on a machine without the add-on the game drops invalid sets
  (`GetProfileCustomization` falls back to the default skin), which needs a live check before shipping that part.
- **Customization screen** (later, optional): the screen lists rows of `<Hero>_Customization_DT`. The agent could add
  a real row at runtime (DataTable `RowMap` insert of a `CharacterCustomizationRow` 0x320 copied from the template row,
  `ThirdPersonMeshDefinition.Mesh`/`FirstPersonMeshDefinition.Mesh` pointed at the add-on paths, a new GUID row
  name). Unknowns: unlock checks (entitlement/`Products_DT`), the thumbnail and name text, and profile persistence.
  Not needed for `/model`.

## 5. Live check (models-next, 2026-09-25, Proton, `multi.sh 2`, add-ons via `addons_dir=`)
Add-on `test_newpath.pak` (7 files: `3P_Test_SKM` -> `Test_Torso_MI` -> `Test_Torso_BC_T` under
`/Game/b4bcoop/test/`, the texture tinted red), loaded with `addons: 3. test_newpath.pak ... cosmetic`.
`mdl load /Game/b4bcoop/test/3P_Test_SKM.3P_Test_SKM` -> loaded (skeleton `3P_Biped_SK`), `mdl mesh 1 CharacterMesh0
<same>` (now clears the component's material overrides) -> the client's hero renders our model with a **red jacket**
(`newpath_face_crop.png`): the mesh at a new path loaded through the pak, its import of the new MI resolved, the MI's
import of the new texture resolved. No LogLinker/LogStreaming errors. So packages at new paths need nothing from the
engine side beyond mounting; what's missing is only something that names them (§3-§4).

## 6. Weapons
A new weapon *look* (skin) is a material set, not a new item: `Skin_Sets/*` MIs are chosen by the equipped skin row
(`<Code>_Customization_DT`). An added skin = new MIs + textures at new paths and a made-up skin row, applied by the
agent to the weapon's mesh components (`BaseSkeletalMesh_1P`, `BaseStaticMesh_3P`) like outfits. A new *model* for an
existing gun (without replacing it for everyone who has the add-on) needs the agent to swap those components' meshes
on the weapon actor for players who chose it; pickups (`<Code>_N_Pickup_BP` -> `3P_<Code>_SM`) and the dropped-magazine
particle (`VFX_EmptyMag_<Code>_3P_P`) keep the retail mesh unless they are also swapped. A new gun (new stats, new item
row) is gameplay content (data tables, blueprints) and out of scope for cosmetic add-ons.

## 7. Work, in order
1. `b4bmod survivor --as <name>`: rename pass over the pipeline output (+ copy the template's other owned MIs).
2. addoninfo `outfit=` lines; addons.c parses them into a list (name, hero, 3P, FP).
3. models.c: catalogue entries from that list, `b4bcoop.addon.<id>.<name>` rows, apply 3P + FP meshes, refusal and
   campaign-run hooks extended. Chat: `/model` lists them.
4. Live: add-on outfit on host and client, both with the add-on; client without it (sees the survivor); map change,
   checkpoint restart, campaign-run save.
5. Later: weapon skins, customization-screen rows.

## 8. Added outfits: implemented (2026-09-25, branch `models-outfits`, build 14216215, Proton, lane 1)
- **modkit** (`b4bmodel.py as_outfit`, `b4bmod survivor ... --as <name> [--title T]`): the pipeline writes into
  `<work>/stage` at the template's paths; then every package it wrote (meshes, textures, hair MI) and every
  `MaterialInstanceConstant` of the template's own folder the meshes import (directly or as a parent) is copied with
  `b4bmod rename` to `/Game/b4bcoop/outfits/<name>/<same base name>` with one `--ref` per copied package (so only
  package-path names change). Unchanged retail textures, skeleton, physics asset, shared/master materials stay
  referenced. Checked after: no copy imports a package that was copied. `<moddir>/addoninfo.txt` gets
  `outfit=<name>|<template survivor>|<3P object path>|<FP object path>|<title>` (replaced per name, several allowed);
  `addon.py pack` keeps these lines (also when repacking a pak), refuses one whose meshes aren't in the add-on.
  Test: CC0 MakeHuman `survivor.fbx` on Mom Elite 04 -> `casual_joe`, 23 packages (12 textures, 9 MIs, 3P + FP
  mesh), 58 files, cosmetic, ~55 s. The pak holds nothing outside `Gobi/Content/b4bcoop/`.
- **Agent**: `addons.c` parses `outfit=` lines (name `[a-z][a-z0-9_]{0,31}`, `/Game/` ASCII paths, `Pkg` ->
  `Pkg.Pkg`); `addons_outfits()` = those of mounted add-ons, a name in two add-ons: the later one wins. `models.c`:
  row `b4bcoop.outfit.<name>` in the wearer's own customization table (like `b4bcoop.npc.*`, §4), resolved after
  catalogue rows and survivor names, before NPCs. `tick_npc` on every machine: body = the 3P mesh (skeleton checked
  `3P_Biped_SK`), head/legs emptied, `FirstPersonArms` = the FP mesh (`FP_Biped_SK`). `compose` completes the set's
  head/torso/legs for made-up rows, which is what machines without the add-on (or b4bcoop) show. Host:
  `set_sane` accepts any well-formed outfit name (the host needn't have the add-on); refused under `/models off`
  (foreign row) and `addons_policy=none` (notice `The host turned model swaps off for add-on outfits
  (addons_policy=none).`); the campaign-run hook and `/models off` reset already cover it (a made-up row is foreign).
- **Protocol: no new bump.** The made-up outfit row needs host support (older hosts refuse it as "not a
  customization row"), which is what protocol 2 (the NPC-body bump on `models`, not released; `main` is 1) already
  stands for; outfits ship in the same unreleased protocol 2.
- Chat: `/model list outfits` (title, add-on), overview line `add-on outfits (/model list outfits): ...`, reply
  `(an add-on outfit: players without that add-on see your survivor)`.

### Live results (`multi.sh 3`: host + client 2 with `casual_joe.pak` via `addons_dir=`, client 3 without)
- Fort Hope: host (Holly) and client 2 (Doc) `/model casual_joe`: `models: hero slot 0/1 wears outfit casual_joe` on
  both; `mdl dump` there: `CharacterMesh0` = `/Game/b4bcoop/outfits/casual_joe/3P_Mom_Elite_04_SKM`,
  `FirstPersonArms` = `.../FP_Mom_Elite_04_SKM`, head/legs empty, overrides 0. Client 3 (no add-on): same set
  (`b4bcoop.outfit.casual_joe`), shows `3P_Doc_Head_03` + `Torso_00` + `Legs_00` (and Holly's pieces): the survivor.
  Screenshots `forthope_1/2.png` (each sees the other in the casual outfit).
- Mission Evansburgh B after character select (timer pick, heroes changed to Evangelo/Holly): re-applied on all
  three machines as above; host view `missionB_host.png` (client's hero in the outfit, host's FP arms in the
  outfit's sleeve). `ready` + `endmission 1`: `models: campaign run saved with 2 survivor(s) in their own look`;
  seamless transition to C: re-applied on all three. No `b4bcoop` string in any profile `.json`/`.sav` afterwards.
- `/models off`: `2 look(s) reset`, client's next `/model casual_joe` refused (`models are off`); `/models on` +
  `/addons policy none`: refused (`add-on outfit, addons_policy=none`), client stops resending.
- Second run (`multi.sh 2`, host with the add-on, client without): the client sees the host's Holly in her base
  pieces (`forthope_client_noaddon.png`), not invisible. Client 3's window in the first run never repainted (stale
  title screen), so its view is only from `mdl dump`.
- Screenshots: `~/.local/share/b4b-coop/outfits/shots/` (not committed). `tools/e2e.py --quick` on this build:
  13/13 PASS.

### Limits / open
- Without the add-on you see the wearer's base pieces, not their equipped Elite outfit (the set holds one outfit
  handle; the made-up row takes it).
- Same name in two different add-ons (or versions) = each player sees their own version.
- Weapons (`--as` for `b4bmod weapon`, skins as made-up rows, §6): not done.

### Customization screen (not built; Dan undecided)
It would take: a real `CharacterCustomizationRow` (0x320) inserted into `<Hero>_Customization_DT.RowMap` at runtime on
every machine (copy of the template row, `ThirdPersonMeshDefinition.Mesh`/`FirstPersonMeshDefinition.Mesh` soft
paths set to the add-on meshes, material overrides emptied, a stable GUID row name derived from the outfit name);
the unlock/entitlement check (`Products_DT` / owned-items lookup the screen uses to grey out or hide rows) passed or
hooked; a display name (FText) and thumbnail (the screen shows a render/texture per row; an add-on would need one);
then the profile: `EquipCharacterCustomizationSetCommand` saves the row into
`equippedCharacterCustomizationSets`, so a machine that later starts without the add-on must survive an unknown row
(`GetProfileCustomization` drops locked/invalid sets to the default skin: needs a live check), and remote players
without the add-on would get a GUID row the game can't find (same fallback as now, but through the game's path
instead of ours). The campaign-run hook would have to treat these rows like made-up ones.
