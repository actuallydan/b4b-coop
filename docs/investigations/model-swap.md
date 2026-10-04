# Runtime model swaps, Tier 0 (issue #19, epic #23)

Build 14216215. SDK dump + `Back4Blood.exe` disassembly, verified live 2026-09-24/25 with `launch/multi.sh 2`
(host + client on Proton). Implemented in `native/src/models.c`; player page: `docs/commands-models.md`.

## Verdict
- **Survivor outfits, colour variants and pieces, across survivors: work, and everyone sees them**, also players
  without b4bcoop. The look is the game's own replicated customization set; we only choose rows the game would never
  offer (another survivor's). No new RPC, no validation to get around, nothing saved.
- **NPC bodies (Fort Hope NPCs, survivors, guards, POWs, cultists): work, same skeleton as the survivors.** No
  customization row exists for them, so they travel as a made-up row name in the same replicated set and every
  machine with b4bcoop puts the mesh on; players without b4bcoop keep seeing the survivor. Protocol bumped 1 -> 2.
- **Ridden: not swap-safe** (other skeletons, see below). Not offered.

## How a survivor's look is built
- `HeroDefinitions_DT` (`CharacterDefinitionRow`, 12 rows, slugs `hero_1`..`hero_12`): per survivor
  `CharacterCustomizationTable` (+0x88, e.g. `Holly_Customization_DT`) and `DefaultSkinSet` (+0x90, a handle to that
  table's default outfit row, e.g. `holly_elite_00`).
- `<Hero>_Customization_DT` (`CharacterCustomizationRow`, 0x320; 250 rows in all, 10-28 per survivor): `Slot`
  (+0x48, `ECharacterCustomizationSlot` Head/Torso/Legs/Outfit), `FirstPersonMeshDefinition` (+0x50) and
  `ThirdPersonMeshDefinition` (+0x118), each a `HeroMeshDefinition` {`MaterialSlotOverrides`, `InGameMaterialOverrides`
  (soft materials), soft `Mesh` +0xA0}. Row names are GUIDs. Outfits are `Elite_00..07` meshes (TU03..TU15 folders);
  rows with material overrides on the same mesh are colour variants.
- `FCharacterCustomizationSet` (0x88) = 4 `FDataTableRowHandle` (0x20 each here: table, row, unreflected display
  string) + `LastEquipSlot` (+0x80). **LastEquipSlot decides**: Outfit -> the outfit mesh on the body, head/legs
  components emptied; otherwise the 3 pieces (all three must be valid rows).
- `IsValid` (0x141B757C0): outfit row found in its table, else head+torso+legs all found. The lookup is only
  `handle.table->RowMap[handle.row]`: **any customization table works**, there is no "row belongs to this hero" check.
- Where it lives: `APlayerSlot::CurrentCustomizationSet` (+0x318, **Replicated**). Also stored in the player's
  profile (`PlayerProfileData.EquippedCharacterCustomizationSets`, per hero; `LogCustomization` lines) and in the host's
  campaign-run data (`CampaignRunPlayerSlotData.CustomizationSet` +0x28).
- Pawn (`Hero_BP_C`, `HeroCharacter`), 4 skeletal mesh components, weak pointers at +0x17C8..+0x17E0:

  | component | skeleton | anim | physics |
  |---|---|---|---|
  | `CharacterMesh0` (body / outfit) | `3P_Biped_SK` | `3P_Hero_ABP_C` | the mesh's own (`3P_Holly_PA`, `3P_Walker_Elite_03_PA` ...) |
  | `ThirdPersonHeadMesh`, `ThirdPersonLegsMesh` | `3P_Biped_SK` | master pose = `CharacterMesh0` | |
  | `FirstPersonArms` (`FPRigSkeletalMeshComponent`) | `FP_Biped_SK` | `FP_Hero_ABP_C` | `FP_Biped_PA` |

  **All 12 survivors share both skeletons and both anim blueprints**; only physics assets differ, and they come with
  the mesh.

## Who applies it (the replication path)
- Client -> host: `AGobiPlayerState::ServerSelectCustomizationSet(set)` (Net, Reliable, Server). Exec thunk 0x14213A4D0
  -> vtable +0x8B8 `_Validate` = 0x140B947E0 (`return true`) -> +0x8C0 `_Implementation` 0x141BD6DC0: copies the 4
  (table, row) pairs + LastEquipSlot into `OwnedPlayerSlot->CurrentCustomizationSet` and calls the slot's OnRep.
  **No validation**: any row of any table is accepted (vanilla game behavior).
- `APlayerSlot::OnRep_CurrentCustomizationSet` (exec 0x140C41EF0 -> vtable +0x658 -> 0x141A123C0): if `AssignedPawn`
  (+0x708) is a live `HeroCharacter`, `0x141BE5E20(pawn, &set)`: IsValid, async-load the meshes
  (`HeroMeshDefinition` loader 0x141B74740 logs "Forcing load for hero mesh definition ..."), then set the components
  and materials. It runs on the server (called by the implementation) and on every client (property replication).
- The game itself sends the set: `ClientInitCustomizationRowForSelectedCharacter(heroRow)` (server -> owning client)
  makes the client read its profile (`FCharacterCustomizationUtils::GetProfileCustomization`, drops locked sets) and
  call `ServerSelectCustomizationSet`. Seen at every hero pick (Fort Hope, each mission's character select, each
  chapter): first an outfit-only set, ~2 s later the full set. Bots get theirs from the host.
- Profile writes are a separate path (0x141BC3C00 "applying customization set to %s" ->
  `EquipCharacterCustomizationSetCommand`, customization screen only). `ServerSelectCustomizationSet` never touches the
  profile.

So appearance is **set on the server and replicated**; each machine applies it locally from the replicated slot.

## Which swaps are safe
From the asset registry (`IAssetRegistry::GetAssetsByClass("SkeletalMesh")` on the `AssetRegistryImpl` CDO: 607
skeletal meshes, 456 under `/Characters/`) and by loading samples (`mdl load`):

| group | skeleton | swap-safe on a survivor |
|---|---|---|
| survivor outfits, heads, torsos, legs (12 survivors, 250 rows) | `3P_Biped_SK` + `FP_Biped_SK` | yes, via the game's own path |
| Fort Hope NPCs (Vanessa, Emmett, Dusty, Rogers, Smithy, Tina, Josh ...), `NPC/Survivors` (M/F 01-09), FtHope guards, POWs (TU11), cultists (TU11), `BaseHero/3P_Survivor_6_F` | `3P_Biped_SK` | yes (54 meshes), body only |
| Ridden: `Zombies/Common` (+Armored), Tallboy/Crusher/Squeezer, Bloater, Chaser (Stalker/Hocker), Snitcher, Brute, Breaker | `3P_Common_SK` | no: the hero anim BP targets `3P_Biped_SK` |
| Hag, Sleeper, Titan, Sentinels, `CultistPetTallboy` | own skeletons (`Hag_SK`, `Sleeper_SK` ...) | no |

- **Animation**: same skeleton = the hero's `3P_Hero_ABP_C` drives any `3P_Biped_SK` mesh (verified: idle, walk, aim
  look right on Vanessa, Emmett, POW_Female_01, every outfit). A mesh on another skeleton would get no valid pose.
- **First person**: survivor rows carry their FP arms (swapped with the outfit); NPCs have none, so the wearer keeps
  their survivor's arms.
- **Physics asset**: comes with the mesh (`3P_Biped_PA`, `3P_Biped_F_PA` for NPCs), so ragdoll and hit bodies match the
  mesh. Weak spots are keyed by bone names of the shared skeleton.
- **Materials**: survivor rows bring their own overrides. For NPC bodies we empty `OverrideMaterials` before setting
  the mesh (the previous outfit's overrides don't fit its slots).

## Implementation (`native/src/models.c`)
- **Catalogue** (built on first use): every row of every `CharacterDefinitionRow` table's customization table. Names
  from the third-person mesh: `3P_Holly_Elite_04_SKM` -> `holly_elite_04`; same mesh again (colour variant) ->
  `_v2`, `_v3`. Survivor names from the table (`Holly_Customization_DT` -> `holly`), since slugs are `hero_N`. NPC
  names from the asset registry path (`Vanessa_SKM` -> `vanessa`), filtered to `/Characters/NPC(s)/`, `/Cultists/`,
  `BaseHero/3P_Survivor*`; each mesh's skeleton is checked (`3P_Biped_SK`) when first loaded.
- **Your own look** (`/model <name>`, host or client): a session wish {row per slot}. Every 0.5 s, once our slot has
  a pawn, a chosen hero and a complete set from the game, and 3 s after it changed (let the game's own two sends
  settle): if the slot's set doesn't match, send `ServerSelectCustomizationSet(current set + picks)`. A piece pick
  clears the outfit and fills missing pieces with the hero's first row for that slot. Re-sent after every map load,
  hero pick, checkpoint restart, rejoin; up to 10 tries per map (replication of the slot can lag seconds after a
  restart), stopped at once by the host's refusal notice.
- **Host on others** (`/model <player> <name>`): same wish keyed by Steam id (bots: `slot:<n>`), written straight into
  the slot + its OnRep (replicates). A bot's forced look is dropped when a human owns that slot again (takeover).
- **Reset**: calls `ClientInitCustomizationRowForSelectedCharacter` on the player state (a client RPC; for a remote
  player it runs on their machine, b4bcoop or not): the game re-reads the profile and re-sends it. Bots: their hero's
  `DefaultSkinSet`.
- **NPC bodies**: the outfit handle is (the hero's own table, `b4bcoop.npc.<name>`), LastEquipSlot Outfit. FNames
  replicate as strings, so the made-up row reaches everyone. The game finds no such row (IsValid) and applies
  nothing; `tick_npc` on every b4bcoop machine sees the row and does `CharacterMesh0.SetSkeletalMesh(npc)` (blocking
  `LoadAsset_Blocking` the first time), empties head/legs. Re-checked every 0.5 s, so a later game re-apply (async
  load finishing, respawn) is overridden again.
- **Host hooks**:
  - `ServerSelectCustomizationSet_Implementation` (0x141BD6DC0): always refuses a set whose handle is not a
    `CharacterCustomizationRow` table row (or an NPC name we know): the game would read any table's row as a
    customization row. With `/models off`, refuses sets with another survivor's rows or an NPC body, and tells that
    player (`ClientTeamMessage` notice, their agent stops resending). Records each slot's last clean set.
  - `FCampaignRunData::RefreshFromGameState` (0x1416E84D0, logs "Refreshing campaign run from game state"; runs at
    chapter end and checkpoint restart): the run copies every slot's `CurrentCustomizationSet` into the host's saved
    campaign run. Before the call, slots wearing another survivor's rows or an NPC get their own set back (fields only,
    no OnRep), restored right after. Without this, a swapped set was saved into the run (seen in the profile JSON:
    `campaignPlayerSlotData[].customizationSet` pointing into another survivor's table).
- `/models off`: refuse (above), drop the host's forced looks, reset every slot with foreign rows (players: profile
  re-init; bots: default skin), including the host's own.
- Dev CLI: `model ...` / `models ...` = the chat commands; `mdl dump` (heroes, slot sets, mesh components with
  skeleton/physics/anim), `mdl rows [filter]`, `mdl reg [filter] [class]` (asset registry), `mdl load <path>`,
  `mdl mesh <hero#> <component> <path>` (local SetSkeletalMesh), `mdl skm`, `mdl setslot`, `mdl rpc`, `mdl reinit`,
  `mdl bring <hero#> [dist]` (host: put a hero in front of you), `mdl look <hero#>` (turn your view to it).

## Live results (2026-09-24/25, host + client, local copies)
Visual checks by screenshots on both windows (kept out of git), state checks with `mdl dump` on both machines.
- Fort Hope: host (Holly) `/model walker_elite_03` -> the client sees Walker's elite outfit on the host; client
  (Hoffman) `/model karlee_elite_03` -> the host sees it. Pieces: client `holly_head_03` + `walker_legs_01` on
  Hoffman's torso, seen by the host. Whole survivor: `/model mom`.
- Mission (Evansburgh B) after character select: both wishes re-applied to the newly picked heroes (~3 s after the
  pick); host-forced bot looks (`doc_elite_03`, `jim`, `karlee_elite_05`) seen on the client.
- Chapter B -> C (seamless travel): re-applied. Checkpoint restart after a hero was killed
  (`LifeStateComponent::Kill` + `/restart`): the new pawns wear the looks.
- NPC bodies: client `/model vanessa`, host `/model emmett`, bot `pow_female_01`: each side sees the others' NPC body,
  animated, in Fort Hope and in the mission.
- Bot takeover: host forced `jim_elite_02` on the client's bot after `/leave`; the client rejoined, took over the bot:
  forced look dropped ("taken over by a player"), the client's own `/model vanessa` re-applied.
- `/models off` (typed in chat): 1-4 looks reset (the client's via profile re-init); the client's next `/model` was
  refused with the notice and not resent. `/models on`, `/model reset` (profile look back).
- Saves: after a chapter end the host's new campaign run holds only each survivor's own rows (hook log
  `campaign run saved with 4 survivor(s) in their own look`); `equippedCharacterCustomizationSets` unchanged in both
  profiles; no `b4bcoop.npc` string in any save.
- `tools/e2e.py --quick` on this build (protocol 2): 12/12 passed (371 s).

## Cutscenes, lineups, mannequins (#37, 2026-10-04, lane 2 / Flatpak Proton)
- **Bug**: in non-interactive cutscenes (#37: the Act 3 escape; tested on the escape of `MAP_PERS_TheClog_E`, "The Broken Bird", which works the same way), players in an
  added outfit or an NPC body showed as their own survivor. Cutscenes don't film the heroes: they use level-placed
  `PlayerStandIn_BP` actors (`APlayerStandIn`, in the cinematic sublevels `Method_TheClogE_00x0_Option4`), whose
  replicated `CustomizationSet` (+0x330) is copied from the slot (`SetAppearanceToMatchPlayerSlot`). Their update
  (0x1416118F0: `ThirdPersonMainMesh`/`ThirdPersonHeadMesh`/`ThirdPersonLegsMesh` at weak +0x3BC/+0x3C4/+0x3CC) runs
  the same set apply as the hero (0x141B75AD0); the made-up outfit row isn't in the table, so the apply showed the
  set's pieces. `tick_npc` only knew the heroes. Same for the lineup/character-select/customization mannequins
  (`CustomizationMannequin_BP`, `DesiredCustomizationSet`), which inline the same per-slot apply.
- **Fix**: hook on the per-slot apply **0x141B75B90** `bool (ctx {set*, comps*}, slot, FP comp, 3P comp)`, the one
  function every set-wearing actor goes through (heroes, stand-ins, mannequins). It returns false when the row isn't
  in its table; for the outfit slot the caller then applies the pieces, and on true with LastEquipSlot Outfit it empties
  head and legs. After the original: outfit slot, false, row `b4bcoop.*` with its look here -> SetSkeletalMesh of the
  look on the 3P component (overrides emptied), the outfit's FP arms on the FP component (NPC bodies: the torso piece's
  arms, by calling the original for the torso slot with no 3P component), answer true. Synchronous like the game's own
  apply. Logged `models: b4bcoop.<...> on <actor> (<class>)` (first 50, then every 100th). With #33 the add-on outfits
  are real rows on machines that have the add-on (new-assets.md §11), so this hook mainly carries NPC bodies and
  `outfits_screen=0`; machines without the add-on still show the pieces.
- **Live** (`multi.sh 2`, `/model` and `mission .../MAP_PERS_TheClog_E Easy`, cutscene started with `callp
  CutsceneCoordinator StartCutscene`): with the add-on rows (both machines with the add-ons): every stand-in of the
  host (Ciri) and the client (Lara Croft) wears the outfit on both machines (`mdl standins`: 6 + 3 stand-ins with
  `/Game/b4bcoop/outfits/...`); the post-round lineup after `endmission 1` shows both outfits on host and client
  (`end_contact.png`). Hook path (host `outfits_screen=0` + `/model ciri`, client `addons=0` + `/model vanessa`): host log
  `b4bcoop.outfit.ciri on PlayerStandIn_BP3_...` (6 stand-ins), `b4bcoop.npc.vanessa on PlayerStandIn_BP_...`,
  `... on CustomizationMannequin_BP_3`; client: Vanessa on its 3 stand-ins and its mannequin, the host's Ciri as
  Evangelo's base pieces (no add-on: fallback, not invisible). Screenshots: host sees Ciri on the train roof, the
  client Evangelo at the same moment (`cs2_pair.png`, `~/.local/share/b4b-coop/screen-test/shots/`, not committed).
- **Act 3's own escape, "T-5" = `MAP_PERS_CDC_D`** (2026-10-04, lane 2, host `/model ciri` or `zoeyl4d`, client
  `laracroft` or `coach`, add-ons on both; `mission /Game/Maps/Missions/CDC/MAP_PERS_CDC_D Easy`, `callp
  CutsceneCoordinator StartCutscene`): the real-time part (`MAP_CDC_D_Cinematic`, ~10 s: body pile, helicopter) has 8
  stand-ins per machine; host and client stand-ins of both players carry `/Game/b4bcoop/outfits/...` on both machines
  (`mdl standins`), bots their retail outfit. The heroes are only small figures in the helicopter door in that shot.
  Then the act ending is a **pre-rendered video** (`PreRenderedCinematicScreen`, `CinematicDefs_DT` row `Cinematic3`,
  WmfMedia): its survivors are baked in and can't show added outfits. The act-end lineup after it shows both added
  outfits on host and client.
- Dev: `mdl standins [PlayerStandIn|CustomizationMannequin]` (set + mesh components of every such actor).

## Limits / open
- NPC bodies are seen only by players with b4bcoop (protocol 2). Without it: the survivor (the made-up outfit row is
  ignored by the game; the pieces of the set, if complete, are shown).
- NPC bodies: no first-person arms; incap/death material effects that target the survivor's material slots may look
  off (not checked).
- `/models off` can't tell a player's own outfit change from a `/model` of their own survivor's outfit (the game
  doesn't check unlocks for remote players), so same-survivor swaps stay possible. A host without b4bcoop accepts
  every swap (vanilla behavior).
- A host-forced bot look follows the slot (new heroes on the next map keep the forced look).
- Mixing pieces of different survivors can show neck/waist seams. Voice, name, portrait and perks stay the
  survivor's.
- Local test copies share one Steam id; `/model <player>` by name/number on them relies on player order.
- Not run on native Windows or with a real second account.
