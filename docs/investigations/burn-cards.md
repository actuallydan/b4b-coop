# Remote players can't play burn cards (issue #6)

Build 14216215. Static analysis of `Back4Blood.exe` and the SDK dump, plus the test prefixes' profiles (read only).
Verified live on 2026-09-24 with two local instances (§5a).

## 1. Summary

- **Where a client's burn cards come from:** only the client's own profile. The start-saferoom card screen lists them
  with `GameplayCardManager::GetPlayerBurnCards` on the client, which reads the local `PlayerProfileSettings`. Nothing
  about them is replicated or sent to the host, except the one card the player picks: `ServerPlayBurnCard(card row)`.
- **Equip/select:** no server step. There is no burn-card loadout. The choice is made in the client's UI
  (`CardDrawScreen::OfferBurnCards` / `CanPlayBurnCards`, all client-local) and ends in `ServerPlayBurnCard`.
- **Play (host), the blocker:** `GCM::PlayBurnCard` requires `PPC::GetConsumableQuantity(product) > 0`. Offline that
  reads the LocalPlayer's profile. A remote controller has none, so the answer is 0 and the play is dropped without a
  log line.
- **Charge (host), second bug:** the played card is queued in the player's slot under a key: the host's copy of the
  player's profile-component `HydraPublicId`. On leaving the start saferoom the charge looks up a profile component
  by that key, via the PlayerState ids. That breaks in both setups we use:
  - two machines: the host's copy of the remote id is sometimes empty (seen in #4), and then the charge is dropped;
  - same-account local tests: all players have the same id, so the queued cards merge under one key and all of them
    are charged to the first controller in the world's list, usually the host's own profile.
- **Fix (branch `worktree-agent-a29c6151b4756902f`, `native/src/burncards.c`):**
  - trust the remote player's quantity during their `PlayBurnCard`;
  - re-key every queued charge to the exact controller that played the card.

  The charge itself stays native: `AdjustConsumableQuantity(-1)` through `ExecuteCommand`, which `rewards.c` already
  forwards to a remote client. So each player's card is charged once, to their own profile, and the host's profile is
  never charged for someone else.
- **Test tooling:** `burncard` never played anything because it built the card handle with a guessed DataTable. It now
  takes the handles from the game's own list and can print slot state and trigger the charge. Details in §4.

Confidence: high. The root cause was confirmed live (kill-switch baseline: the client's card is rejected), and the fix
was verified live end to end, including the key rewrite and the client-side `ClientExecuteAdjustConsumableQuantityCommand`
path (§5a).

## 2. Host-side path, check by check

### Play: `ServerPlayBurnCard` → `GCM::PlayBurnCard`
`GobiPlayerController::ServerPlayBurnCard` (NetServer; exec `0x142127AF0`, `_Validate` returns true, `_Implementation`
`0x141B9CA60`) takes the PlayerState (`PC+0x2A8`) and the GameState's `GameplayCardManager` (`GobiGameStateBase+0x398`,
getter `0x14176B970`), then calls **`GCM::PlayBurnCard(gcm, ps, cardHandle)` @ `0x141776EA0`**:

| # | Check | Reads a profile? |
|---|---|---|
| 1 | PPC = `PS.PlayerProfileComponent` (`+0x770`) or owner PC's `GobiPlayerProfileComponent` (`+0x760`); non-null | no |
| 2 | slot = `PlayerActiveGameplayCardDataArray` (`GCM+0x298`, 0xD8 each) entry matching `PS.OwnedPlayerSlot` | no |
| 3 | product = `CardNameToProductHandles[card.RowName]` (`GCM+0x758`); must exist | no |
| 4 | `CanBurnCardBePlayedThisMap(card)` `0x1417769D0`: row in the handle's own table, `BurnCardOnePerTeam`, exclusion mods, `PlayerCardsToExclude` | no |
| 5 | `GameState.bTraining` (`+0x589`) → free; else **`PPC::GetConsumableQuantity(product)` `0x141BC2A00` > 0** | **yes** |
| 6 | `slot.BurnCardsPlayedThisMap.Num == 0` (one per player per map) | no |
| 7 | product is a consumable card product `0x141C771D0` | no |

On success it logs `##> %s (hydra id %s) played burn card %s` (Log level, category `LogGameplayCardManager`, so our
log does capture it). Then it applies the card (`AddGameplayCardToSlotByRowHandle` `0x141770C50`, no ownership check),
appends to `BurnCardsPlayedThisMap` (+0x60) and `NumBurnCardsEverPlayed` (+0x70), and **queues the card for the charge**:
unreflected `slot+0x78` (array of card handles), key `slot+0x88 = PPC.HydraPublicId` (`+0x1A8`).

`GetConsumableQuantity` (`0x141BC2A00`) returns `max(acquired - spent, 0)` from `PlayerProfileData.Consumables` (+0x88).
Offline it gets that data from `0x141BC6930`, the LocalPlayer's `PlayerProfileSettings`, which is null for a remote
controller. So it returns 0 → check 5 fails → nothing happens, silently. Online it would have read the Hydra profile
cache (`0x141BC69A0`). This is the same class of bug as `cards.c`.

### Charge: leaving the start saferoom
`GCM::OnSafeRoomStateChanged` (exec `0x142095F70`) → `0x141768A30`. It charges only when Old = `InStartingRoom` (1),
New = `NotInRoom` (0) and `!bTraining`:
1. For each slot, it groups `slot+0x78` by the key `slot+0x88` (a case-insensitive map). **An empty key is skipped and
   its queue discarded.** Every queue is then emptied.
2. For each key: `PPC = FindPPCByHydraId(gcm, key)` @ `0x141BBEB10` walks `World.ControllerList` and returns
   `PC.GobiPlayerProfileComponent` of the **first** `GobiPlayerControllerBase` whose
   `PlayerState.UserIds.HydraPublicId` (`+0x708`) matches. If there is none, the cards are not charged.
3. For each card: `##> Charging hydra ID %s for burn card %s`, then `0x141BC2540(PPC, product, -1, "BurnCardConsumed")`
   (`[id]: adjusting consumable %s by %i`) → `PPC::ExecuteCommand(AdjustConsumableQuantity)`.

For a remote PPC, `ExecuteCommand` drops the command natively. `rewards.c` forwards type 19 with
`ClientExecuteAdjustConsumableQuantityCommand`, and the client applies it to its own profile. The client can't charge
twice: its copy of the slot has no `+0x78` queue (unreflected, not replicated) and nothing else issues this command
(the only other caller of `0x141BC2540` is the duffel-bag reward issuer `0x141BD7610`).

What goes wrong with the native key:
- The key is the host's copy of the remote PPC's `HydraPublicId`. In #4 it was sometimes empty (`[]: adjusting SP`);
  then the charge is discarded (step 1), and the client plays for free.
- Same Steam account (all local tests): host and client both have `offline.<same id>`. Their cards merge under one key,
  and step 2 returns the first matching controller. Both cards would then be charged to one profile, normally the
  host's. The host's own card has the same problem in reverse: it can land on the client.

### Other readers of a remote profile (checked, left alone)
- Other callers of `GetConsumableQuantity`:
  - `0x141CB9480`, `0x141CBBC44..` (caravan/store code, client UI);
  - `ProgressionUtils::GetPlayerConsumableCardCount` (UI).
- The duffel-bag reward roll (`RewardDuffelBags` area, `0x141A0B662` / `0x141A0B81D`,
  `Number of potential un-maxed-out Burn Cards`) has its own inlined copy of the quantity read. For a remote player it
  also sees 0, so it may offer a burn card the client already has at the cap. That is cosmetic, and the fix doesn't
  touch it.
- `GetPlayerBurnCards` / `UIBlueprintFunctionLibrary::CanPlayBurnCards` (`0x141D5B210`) read the local profile. They
  run on the client, for the client, which is correct.

## 3. Fix: `native/src/burncards.c`
Three hooks, each with its prologue signature checked. Seven more byte checks verify the slot layout and the call
sites the fix depends on (`+0x298`/0xD8 stride, `+0x78`, `+0x88` ← `PPC+0x1A8`, the quantity call, the charge's key
and lookup):

| Hook | What it does |
|---|---|
| `GCM::PlayBurnCard` `0x141776EA0` | Notes the playing controller. After the original, if a slot's `+0x78` queue grew, it overwrites that slot's key (`+0x88`) with `b4bcoop.burn.<n>`, bound in a 64-entry table to that controller (pointer + object index). Done for every human player, local or remote. The write uses the game's `FString::operator=` (`0x140BAE580`), so the game's allocator owns the string it later frees. Logs `burncards: remote/local player played <row> (slot i); charge key '<old>' -> b4bcoop.burn.<n>`, or `... was rejected by the host`. |
| `PPC::GetConsumableQuantity` `0x141BC2A00` | Only while a **remote** player's `PlayBurnCard` runs, and only for that player's component: 0 becomes 1. Logs `burncards: trusting remote player's quantity`. |
| `FindPPCByHydraId` `0x141BBEB10` | For a `b4bcoop.burn.<n>` key, returns the bound controller's `GobiPlayerProfileComponent`, or null if the controller is gone (charge dropped, logged). All other keys go to the original. |

Why trust instead of having the client report its counts: the client's UI only offers cards its own profile holds,
the same argument as `cards.c`, and the charge then goes to the client's real profile. A modified client could play a
card it doesn't own. That is acceptable in private co-op, and the charge would then just leave that entry at 0.
Reporting counts would need a client → host channel (no suitable server RPC carries data) and would still end in the
same charge. The trust approach is the smallest change that makes the retail flow work.

Per-player result:
- **Remote player:** plays their own card, and is charged once, on their own profile, through `rewards.c`.
- **Host:** charged once, natively, on their own profile.
- **Host profile:** never charged for a remote player.

Kill switch: env `B4BCOOP_NO_BURNCARDS=1`.

Shared-file edits: `main.c` (`burncards_init()` after `rewards_init()`), `cmds.h` (prototype). The fix depends on
`rewards.c` to forward the remote charge.

## 4. Test tooling (`testing.c`)
Why `burncard` did nothing, even for the host's own player:
- It built the handle as `{find_named("PlayerCards_MASTER_DT"), <row>}`. `PlayBurnCard` looks the product up by row
  name alone, but check 4 (`CanBurnCardBePlayedThisMap`) fetches the row **from the handle's own DataTable**. With the
  wrong table that fails silently.
- The quantity was not the problem: both test profiles hold plenty (e.g. `Burn_TeamLife` 333/22).
- The earlier note that `played burn card` is Verbose was wrong. It is Log level and is captured, so "no log line"
  really meant "rejected".
- `callp` and `burncard list` used `ue_find_first_of`. That returns the first `GameplayCardManager` in the object
  array, which can be a leftover of the previous map. `callp` also could not pass the PlayerState that most GCM
  functions take.

Now:
- `burncard list`: `GetPlayerBurnCards(own PlayerState)`, the handles the UI would offer (quantity > 0 in this
  instance's profile).
- `burncard [row]`: plays that card from the list (default: the first) with `ServerPlayBurnCard`, like the UI. It first
  prints the profile-independent server checks (`IsBurnCard`, `CanBurnCardBePlayedThisMap`,
  `HasPlayedBurnCardThisMap`). On a client this sends the real server RPC. `burncard <row> <table>` keeps the
  explicit-handle form.
- `burncard status`: for each slot, `BurnCardsPlayedThisMap`, `NumBurnCardsEverPlayed`, the number of
  `ActiveHeroCards` with any active `Burn_*` card (the played card's effect), and, on the host, the queued charge count
  and its key.
- `burncard charge` (host): calls `GCM::OnSafeRoomStateChanged(InStartingRoom, NotInRoom)`, the charge that runs when
  the party walks out of the start saferoom. The queues are emptied, so the real exit later charges nothing twice.
- `burncard map`: the old card → product dump.
- `callp` targets the live GCM (`GameState.GameplayCardManager`) or the instance in the current world, and takes object
  args `pc | ps | gs | gcm | world | null`.

## 5. Live test plan
`launch/install.sh`, then `launch/multi.sh 2` (instance 1 hosts; separate prefixes, so separate profiles; same Steam id,
the case that exercises the key fix). `P<n>=~/.local/share/b4b-coop/prefixes/test<n>/pfx/drive_c/users/steamuser/AppData/Local/Back4Blood/Steam/Saved/SaveGames/PlayerProfileSettings.json`.
`b4b` = `.venv/bin/python tools/b4b.py`.

1. Host log: `burncards: PlayBurnCard / GetConsumableQuantity / FindPPCByHydraId hooked` and
   `rewards: ExecuteCommand hooked`. Copy `$P1`, `$P2` aside.
2. `B4B_AGENT=0 b4b mission Easy`, and wait for both heroes. Stay in the start saferoom.
3. `B4B_AGENT=1 b4b burncard list` and `B4B_AGENT=0 b4b burncard list`. Pick two **different** cards that are not
   team-once (e.g. two `Burn_RollGun*` rows): A for the host, B for the client.
4. `B4B_AGENT=1 b4b burncard B` (client → real RPC). Expect `precheck: IsBurnCard 1, CanBurnCardBePlayedThisMap 1,
   HasPlayedBurnCardThisMap 0`. Host log: `burncards: trusting remote player's quantity`,
   `##> … played burn card B`, `burncards: remote player played B (slot i); charge key '…' -> b4bcoop.burn.0`.
   The card's effect shows up for the client's hero.
5. `B4B_AGENT=0 b4b burncard A`. Host log: `burncards: local player played A … -> b4bcoop.burn.1` (no "trusting").
6. `B4B_AGENT=0 b4b burncard status`: both human slots show `played this map 1`, `queued charge 1`, keys
   `b4bcoop.burn.*`. `B4B_AGENT=1 b4b burncard status` shows the same played counts (replicated).
7. `B4B_AGENT=0 b4b ready`, then `B4B_AGENT=0 b4b burncard charge`. Host log:
   - client's card: `burncards: charging b4bcoop.burn.0 -> remote player's profile`, `adjusting consumable … by -1`,
     `rewards: forwarding AdjustConsumableQuantity (-1) to remote player …`;
   - host's card: `burncards: charging b4bcoop.burn.1 -> local player's profile`, then
     `ApplyCommandToOfflineData:AdjustConsumableQuantity`.

   Client log: `[CLIENT RPC] …consumable…` + `ApplyCommandToOfflineData:AdjustConsumableQuantity`. `burncard status`:
   queued 0.
8. Optional: `B4B_AGENT=0 b4b endmission 1` (checks that SP forwarding still works and nothing is charged a second
   time).
9. Wait ≥ 40 s (deferred save), then diff the consumables:
   ```
   for f in "$P1" "$P2" <saved copies>; do python3 -c 'import json,sys; c=json.load(open(sys.argv[1]))["offlineData"]["consumables"]; [print(k.split("RowDisplayName=")[1], v) for k,v in sorted(c.items())]' "$f" > "$(basename "$f").txt"; done
   ```
   Expect: host `A.spent` +1 and nothing else; client `B.spent` +1 and nothing else. Any other change is a bug:
   - host B changed → a remote charge landed on the host;
   - client `spent` +2 → double charge;
   - no client change → forward lost.
10. Retail walk-out (optional, needs a human or a pawn teleport): repeat 3-5 on the next chapter and leave the saferoom
    instead of `burncard charge`. Same log lines, from the real `OnSafeRoomStateChanged`.
11. Baseline: `B4BCOOP_NO_BURNCARDS=1 launch/multi.sh 2`, repeat 4. `burncard status` on the host shows the client slot
    `played this map 0`, and the host log has no `played burn card` for it. This confirms the root cause live.

If step 4 is rejected, the `precheck` line shows which profile-independent check failed; the quantity check is the
only other one.

## 5a. Live results (2026-09-24, branch rebased on main 31d84ae)
Two instances (`launch/multi.sh 2`), same Steam account, Evansburgh Easy. Both profiles backed up before each run;
diffs are of the whole `PlayerProfileSettings.json` after the deferred save (it landed ~2 s after the charge), with the
`campaignRuns` entry (the new run itself) left out.

**Fix, chapter 1** (host A = `Burn_RollGunAR`, client B = `Burn_RollGunSMG`), then `ready`, `burncard charge`,
`endmission 1`:
- Client play: precheck `1/1/0`; host log `trusting remote player's quantity`, `##> … played burn card … Burn_RollGunSMG`,
  `remote player played Burn_RollGunSMG (slot 1); charge key 'offline.76561198063588550' -> b4bcoop.burn.0`.
- Host play: `local player played Burn_RollGunAR (slot 0); charge key 'offline.76561198063588550' -> b4bcoop.burn.1`,
  no "trusting". Both native keys were the same id, the merge case the fix exists for.
- `burncard status` on host and client: slot 0 `played this map 1 Burn_RollGunAR [active Burn_RollGunAR]`, slot 1
  `… Burn_RollGunSMG [active Burn_RollGunSMG]`, host `queued charge 1` with `b4bcoop.burn.1` / `.0`.
- Charge: `charging b4bcoop.burn.1 -> local player's profile` + `ApplyCommandToOfflineData:AdjustConsumableQuantity`;
  `charging b4bcoop.burn.0 -> remote player's profile` + `rewards: forwarding AdjustConsumableQuantity (-1)`. Client:
  `[CLIENT RPC] adjusting consumable Burn_RollGunSMG by -1` + `ApplyCommandToOfflineData`. Queues 0 afterwards.
- `endmission 1`: no second charge; SP 73 forwarded as before.
- Diff: host `Burn_RollGunAR.spent` 15 → 16; client `Burn_RollGunSMG.spent` 13 → 14. After `endmission 1` also
  `supplyPoints.acquired` +73 on each. Nothing else.

**Fix, chapter 2** (host `Burn_RollGunHG`, client `Burn_TeamCurrency_250`, to see an effect in the log), same steps
without `endmission`:
- The client's card took effect on the host at once: `Cause: LoadoutGrantedCurrency AdjustCurrency: 150`,
  `SetCurrency: 1600 => 1750` for both human players.
- Keys `b4bcoop.burn.2` (host) / `.3` (client), charged to local / remote respectively, one `[CLIENT RPC]` on the client.
- Diff: host `Burn_RollGunHG.spent` 4 → 5; client `Burn_TeamCurrency_250.spent` 23 → 24. Nothing else.

**Baseline** (`B4BCOOP_NO_BURNCARDS=1 launch/multi.sh 2`; log `burncards: disabled by B4BCOOP_NO_BURNCARDS`):
- Client `burncard Burn_RollGunSMG`: precheck `1/1/0` (every profile-independent check passes), but the host logs no
  `played burn card`, and `burncard status` shows slot 1 `played this map 0` on both sides. Root cause confirmed.
- Host `Burn_RollGunAR` plays and is charged natively (key `offline.76561198063588550`).
- Diff: host `Burn_RollGunAR.spent` 16 → 17; client unchanged.

Effects: every played card is in its slot's `ActiveHeroCards` (host and replicated to the client), and the team-currency
card paid out. `Burn_RollGun*` effects are not logged, so the weapon roll itself was not observed (the test windows
show the loading screen overlay).

## 6. Unresolved
- The retail walk-out trigger (step 10) was not run. `burncard charge` calls the same `OnSafeRoomStateChanged`
  UFunction, so only the trigger differs.
- The duffel-bag "maxed-out burn card" test sees 0 for remote players (§2), which is cosmetic.
- A remote player who disconnects before the charge keeps the card for free (the charge is dropped, and logged).
- Not tested across two machines / two accounts; the key fix does not depend on the ids, so no difference is expected.

## 7. Addresses
| What | VA |
|---|---|
| `ServerPlayBurnCard` exec / `_Implementation` | `0x142127AF0` / `0x141B9CA60` |
| `GCM::PlayBurnCard` (hook) | `0x141776EA0` |
| `CanBurnCardBePlayedThisMap` (internal) | `0x1417769D0` |
| `PPC::GetConsumableQuantity` (hook) | `0x141BC2A00` |
| offline profile of a PPC (LocalPlayer) / Hydra cache lookup | `0x141BC6930` / `0x141BC69A0` |
| `GetPlayerBurnCards` internal (client UI list) | `0x1417761F0` (UFunction `0x142099480`) |
| UI `CanPlayBurnCards` | `0x141D5B210` |
| `OnSafeRoomStateChanged` exec / charge | `0x142095F70` / `0x141768A30` |
| `FindPPCByHydraId` (hook) | `0x141BBEB10` |
| AdjustConsumableQuantity issuer ("adjusting consumable %s by %i") | `0x141BC2540` |
| `FString::operator=` / `FMemory::Free` | `0x140BAE580` / `0x140C823B0` |
| Offsets | GameState `GameplayCardManager` +0x398, `bTraining` +0x589; PS `PlayerProfileComponent` +0x770, `UserIds.HydraPublicId` +0x708; PC `GobiPlayerProfileComponent` +0x760; slot (0xD8): `BurnCardsPlayedThisMap` +0x60, `NumBurnCardsEverPlayed` +0x70, charge queue +0x78, charge key +0x88 |
