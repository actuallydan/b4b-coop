# Client gets "pick remaining cards" instead of its deck

Build 14216215. Evidence from host log `b4bcoop-2972.log`, client log `b4bcoop-776.log` (session 2026-09-23 ~21:19),
static analysis of `Back4Blood.exe`, and the host's `PlayerProfileSettings.json` (read-only).

## 1. Root cause

The host's server-side card-ownership check fails for every card of a **remote** player. The listen host has no
profile bound to the remote PlayerController. `GrantLoadoutCardsForSlot` then skips all 15 deck cards and turns each
skipped card into a bonus draw. That produces the 15-draw "pick a card" screen. Each drawn card is then refused
by the same check, so **the client ends up with no deck cards at all**, not just a bad UI flow.

Log chain (host, 21:19:40, when the client takes over the bot slot):
```
GameplayCardManager: ##> ApplyLoadoutForCharacterTransfer ...
GameplayCardManager: ##> GrantLoadoutCardsForSlot <client>
GameplayCardManager: ##>   granting 15 cards
GameplayCardManager Warning: ##> <client> has ...MovementSpeed_03 in a loadout slot but they don't own it   (x15, every card)
```
Client, same instant: `CalculateDrawCount DrawCount 15 = (BonusDraws) 15 + (BaseDraws) 0 + ...` → `InitNumDraws - 15 draws.`
Each pick then fails on both sides:
```
host:   ##> AddDrawnGameplayCard for <client> ...Stamina_02
host:   ##> <client> attempted to play ...Stamina_02 but does not own it
client: LogCardDraw Error: Failed to draw card '...Stamina_02'. Check the server logs for more info.
```
For the host's own slot, the same run gives `DrawCount 0`: its cards pass the check.

**The unlocks are there, so missing unlocks are not the cause.** Both test instances share one profile. The client's deck is
"Fastest Dan Alive" (`offlineData.decks`, the 15 cards above). Every card has its `Card_<name>` product in
`offlineData.unlocks` (787 entries). The host itself owns all of them. The check fails because it cannot find
*any* profile for a remote PC.

**Hypothesis 2 (campaign run state) is ruled out.** The 15 draws come from `GrantLoadoutCardsForSlot`'s leftover counter.
They do not come from `bNeedsCardDraw` or `campaignPlayerSlotData`. The host resumed the same run with `bNeedsCardDraw: true`
and got 0 draws.

### Why the check fails (static analysis)
`GrantLoadoutCardsForSlot` @ `0x141769690`: for each preset card, it calls the internal ownership check
`0x14176DDA0`. On success it applies the card and decrements the counter. On failure it logs the warning. After the
loop, the leftover count is added to slot data `+0xA0` (BonusDraws, `0x141769a85`).

`0x14176DDA0` is `bool GCM::OwnsCard(GCM*, AGobiPlayerState*, const FDataTableRowHandle*, EGameplayCardDeckType)`. It has
7 callers: GrantLoadoutCardsForSlot, AddDrawnGameplayCard (`0x14177499e`), GetRandomDraw (`0x1417726cc`), the draw-pool
builder (`0x141772bf1`), plus 3 more. It returns true when any of these holds:
- the card row is always-unlocked (card data flags `+0x130`, type 7),
- the card is in `PresetCards` (GCM `+0x518`, the 9 whitelisted PvE starter cards) or `GameplayDataSet` (`+0x430`) sets,
- or the card's product (`CardNameToProductHandles`, GCM `+0x758`) is owned by `PlayerState->Owner`:
  - `Owner` IsA `BotController` → true (this is why bots always have full decks),
  - `Owner` IsA `PlayerController` → `0x141c76b30` → `UProgressionUtils::IsProductOwned` (`0x141c76cf0`).

`IsProductOwned(PC, product)` checks, in this order:
1. `PC->GobiPlayerProfileComponent` (`+0x760`) → the unlock set from `0x141bc2420`. In offline mode, that set comes from
   `GobiGameInstance->GobiPlayerProfileManager` (`+0xA30`). Its unreflected `TMap` at `+0xC8` is
   **keyed by the component's `HydraPublicId`** (component `+0x1A8`, confirmed by `GetHydraPublicId` @ `0x142132320`).
2. `PC->Player` (`+0x308`) IsA `GobiLocalPlayer` → the local user's unlock set.
3. DLC entitlement fallback.

`HandleSignInCompleted` (`0x141bbece0`) fills component `HydraPublicId` only from the **local** signed-in user. It matches
by `GetLocalPlayer()->ControllerId`. On the host, a remote PC has no LocalPlayer (its `Player` is a `UNetConnection`), so its
component id stays empty. The manager lookup misses, path 2 does not apply, and every product-gated card is reported as
not owned. On a retail dedicated server, the Hydra backend loaded each player's profile into this manager. Offline, nothing
does that. (The PlayerState does get `HydraPublicId=offline.<steamid>` from the login options, `SetUserIds` at 21:18:57.
The ownership path never reads it.)

## 2. Reference

| What | Where |
|---|---|
| `GCM::OwnsCard` (internal, hook target) | `0x14176DDA0`; rcx=GCM, rdx=AGobiPlayerState*, r8=FDataTableRowHandle*, r9b=deck type; returns al |
| `GrantLoadoutCardsForSlot` | `0x141769690` (leftover → slot `+0xA0` BonusDraws) |
| `AddDrawnGameplayCard` | `0x14177499e` |
| `GetRandomDraw` / pool builder | `0x1417726cc` / `0x141772bf1` |
| `UGameplayCardManager::IsCardUnlocked` (UFunction, own inlined copy) | `native=0x1420a1230` |
| `UProgressionUtils::IsProductOwned` | `0x141c76cf0` (entry via `0x141c76b30`) |
| Offline profile lookup by HydraPublicId | `0x141bc69a0` → `GobiPlayerProfileManager` `0x141a6bd90` (map `+0xC8`, 0xB38-byte entries) |
| `GobiPlayerProfileComponent.HydraPublicId` | component `+0x1A8` (FString, unreflected) |
| `GobiPlayerControllerBase.GobiPlayerProfileComponent` | `+0x760` |
| `PlayerController.Player` / `Actor.Owner` | `+0x308` / `+0x110` |
| GCM props | `PresetCards +0x518`, `GameplayDataSet +0x430`, `CardNameToProductHandles +0x758` |
| Log strings | `"...in a loadout slot but they don't own it"` 0x145449f40, `"...attempted to play %s but does not own it"` 0x14544b450 |

## 3. Fix proposal (host, agent DLL)

Hook `0x14176DDA0` on the host. Call the original first. If it returns false and the PlayerState's owner is a
human PlayerController whose `Player` is not a `LocalPlayer`, return true. One hook fixes the loadout grant, the draw
screen and the draw pool. Host players and bots are unchanged. The client's own UI already limits its deck to cards
the client owns (client-side checks use its local profile), so trusting the deck on the host is fine for private co-op.
With the hook in place, `GrantLoadoutCardsForSlot` grants all 15 cards and `BonusDraws` stays 0, so no draw screen appears.

New `native/src/cards.c` (call `cards_init()` from `init_thread` after `travel_init()`, add a signature to `ue_init`):
```c
// Host: the listen server has no profile for remote players, so the card-ownership check fails for every card of
// theirs and GrantLoadoutCardsForSlot converts the whole deck into "pick a card" draws. Treat remote humans as owners.
#include "MinHook.h"
#include "ue.h"
#include "log.h"

#define ADDR_GCM_OWNSCARD 0x14176DDA0ull  // bool (UGameplayCardManager*, AGobiPlayerState*, const FDataTableRowHandle*, uint8 deck)
// sig (32 bytes, unique in the exe):
// 48 89 5C 24 08 48 89 6C 24 18 56 57 41 56 48 83 EC 20 41 0F B6 D9 49 8B F8 4C 8B F2 48 8B E9 48

typedef uint8_t (*OwnsCardFn)(UObject *gcm, UObject *ps, void *row, uint8_t deck);
static OwnsCardFn orig_owns;
static UClass *cls_pc, *cls_lp;
static int overrides;

static int is_remote_human(UObject *ps) {
    UObject *pc = ps ? ue_get_ptr(ps, "Owner") : NULL;
    if (!pc || !ue_is_a(pc, cls_pc)) return 0;       // bots (BotController) already pass
    UObject *player = ue_get_ptr(pc, "Player");
    return player && !ue_is_a(player, cls_lp);        // UNetConnection => remote client
}

static uint8_t owns_detour(UObject *gcm, UObject *ps, void *row, uint8_t deck) {
    uint8_t r = orig_owns(gcm, ps, row, deck);
    if (!r && is_remote_human(ps)) {
        r = 1;  // later: consult the client's synced unlock set here (profile-sync work)
        if (overrides++ < 20) LOG("cards: ownership override for remote player");
    }
    return r;
}

int cards_init(void) {
    cls_pc = ue_find_class("PlayerController");
    cls_lp = ue_find_class("LocalPlayer");
    if (!cls_pc || !cls_lp) { LOG("cards: classes missing"); return -1; }
    if (MH_CreateHook((void *)ADDR_GCM_OWNSCARD, (void *)owns_detour, (void **)&orig_owns) != MH_OK ||
        MH_EnableHook((void *)ADDR_GCM_OWNSCARD) != MH_OK) { LOG("cards: hook failed"); return -1; }
    LOG("cards: OwnsCard hooked");
    return 0;
}
```
Notes:
- Game thread only (it is called from game code), so no locking is needed.
- A client that already joined and got the empty-deck state keeps it. The grant runs once per slot (slot `+0x59`
  "HasReceivedLoadoutCards"). Test with a fresh join.
- Rejected shortcut: copying the host's `HydraPublicId` into the remote PC's profile component would make the client
  own whatever the *host* owns. That works on one machine and is wrong for a friend with different unlocks.

**Overlap with "send each client's offline profile to the host":** yes, directly. The hook above is the interim version.
With profile sync, the detour should check the card's product against that client's own `offlineData.unlocks`
(key by PlayerState `HydraPublicId` / connection) instead of returning true. The fuller fix inside the same work: register
the client's profile in `GobiPlayerProfileManager`'s cache (map `+0xC8`, keyed by id) and set the remote component's
`HydraPublicId` (`+0x1A8`). Then `IsProductOwned` works natively for everything it gates (cards, and probably
cosmetics, skins and heroes). That needs the unreflected 0xB38 entry layout reversed first. Also note that two instances
on one Steam account both use `offline.<steamid64>`, so ids collide in single-machine tests.

## 4. Not verified / how to verify
- **Remote component `HydraPublicId` is empty on the host.** This comes from static analysis only; `peek` was off-limits.
  To check, add a read-only `profiles` command that, for each PC in the world, calls
  `GobiPlayerProfileComponent.GetHydraPublicId` (UFunction, no side effects) and prints it with `Player`'s class.
- **The fix itself.** Build, install, and have the client join a mission fresh. Expect
  no "don't own it" warnings and `##>      applying loadout card ...` x15 in the host log, `DrawCount 0` on the
  client, and no CardDrawScreen.
- The other 3 callers of `0x14176DDA0` (`0x14176bf89`, `0x14176cd70`, `0x141d88dee`) are unnamed. They are probably
  card-giver or vendor paths, and the hook covers them the same way.
- The obfuscated byte check on the profile component (`+0x1A0`, XOR `0x1fdf9c84c`) at the start of `IsProductOwned`
  looks like an "owns everything" flag. It could be an alternative switch, but it was not decoded.
