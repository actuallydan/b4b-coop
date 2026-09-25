# New assets under new paths: add-ons that add instead of replace (design, #20/#21, epic #23)

Status 2026-09-25, build 14216215, branch `models-next`. Design; the package side is prototyped (`b4bmod rename`,
§2) and a live load check is in §5. Today every add-on replaces game files at their paths: a survivor model *is*
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

## 5. Live check (models-next, 2026-09-25)
See §5 results once run: add-on `test_newpath.pak` (the three `/Game/b4bcoop/test/` packages above, torso texture
tinted red), then `mdl load /Game/b4bcoop/test/3P_Test_SKM.3P_Test_SKM` and `mdl mesh 0 CharacterMesh0 <same>`.

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
