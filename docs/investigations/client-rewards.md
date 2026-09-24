# Clients keep what they earn (issue #4)

Build 14216215. Sources: host log `b4bcoop-552.log` (two-machine session 2026-09-23, client `offline.76561198975681908`
joined at 22:05, mission Evansburgh B ended 22:12:34), same-machine client log `b4bcoop-776.log`, the Windows
client's `docs/logs/2026-09-23-windows-client/PlayerProfileSettings.json`, the host's profile (read only), and static
analysis of `Back4Blood.exe`. No live game was used.

## 1. Summary

The suspicion is half right. The host computes every player's rewards. Only some of them reach a remote client:

| Earned by a remote client | What happens today | Why |
|---|---|---|
| Supply points (mission success/failure) | **Lost** | Host executes the command on the client's profile component. That component is not local, so the command is dropped. |
| Skull totem points | **Lost** | Same path. |
| Duffel-bag rewards (unlocks, consumables) | **Lost** | Same path (`RewardDuffelBags`). |
| Burn cards used in the mission | **Not charged** (the client keeps them) | Same path (GCM charges on leaving the start saferoom). |
| Stats (kills, missions completed, …) | **Kept** (native) | Host sends `ClientApplyStatDeltas`; the client reconciles into its own profile on EndPlay/OnLeavingMap. |
| Starting locations (map unlocks) | **Kept** (native, static evidence only) | Client runs the unlock itself from `GobiPlayerState::OnRep_UnlockedNewMap`. |
| Achievement rewards (`CompleteAchievement`) | Unknown, probably lost | Server-side tracker; no client RPC exists. |
| Things the client does in menus (decks, cosmetics, caravan purchases, recent character) | **Kept** (native) | Executed on the client's own, local, profile component. |
| Campaign run | **Host only** | The run belongs to `RunOwner` (the host). The client records nothing and cannot resume it alone. |

**Nothing is written to the host's profile on a client's behalf.** Offline writes go only to the `LocalPlayer` that
owns the controller, and a remote controller has none.

The fix (implemented, `native/src/rewards.c`): the game still ships client RPCs for exactly the lost commands
(`ClientExecute{AdjustSupplyPoints,UnlockProduct,AdjustConsumableQuantity,AdjustSkullTotemPoints}Command`). Their
client side runs the command through the normal offline path. Nothing in this build calls them any more. The host
agent now calls them.

## 2. Reward flow

### Command execution (`UGobiPlayerProfileComponent`, "PPC")
All profile changes are `FPlayerProfileCommand` subclasses (vtable: `[1]` GetType → `EPlayerProfileCommandType`, `[4]`
apply to `FPlayerProfileData`). They all go through:

`PPC::ExecuteCommand(PPC*, const FPlayerProfileCommand*, bool bPersist)` @ `0x141BC4970` (28 callers):
```
pc = PPC->OwnerPrivate (+0xD8), must be GobiPlayerControllerBase; GameInstance must have GobiPlayerProfileManager (+0xA30)
local = pc->IsLocalController()                      // vtable +0x6B8
if (local) ApplyCommandToOfflineData(PPC, cmd)       // 0x141BC4F70, logs "ApplyCommandToOfflineData:%s"
if (OnlineModeSubsystem(+0xA0) == 1 /*Online*/)      // skipped offline
    local ? 0x141BC4C10 (the "…bPersist:%d" Hydra path) : 0x140B92AF0 (`ret` — compiled out)
broadcast OnCommandExecuted (PPC +0x130, global list 0x146491760)
```
`ApplyCommandToOfflineData` @ `0x141BC4F70` gets its target from `0x141BC6930`: `pc->GetLocalPlayer()` must be a
`GobiLocalPlayer`, then `GobiLocalPlayer.PlayerProfileSettings` (+0x2F8) → `OfflineData` (+0xE0). It calls `cmd->Apply`
and then `SaveGame` (vtable +0x288). So **offline data is per LocalPlayer, gated on `IsLocalController`**. `EOnlineMode`
does not gate the offline apply. It only selects the (dead) online branch.

### Who issues which command
| Command (type) | Issuer | Runs on |
|---|---|---|
| AdjustSupplyPoints (5) | `0x141ECE2A0` "adjusting SP by %i" (also adds to replicated `GobiPlayerState.PostRoundBonusSP` +0x7A0) ← `RewardSurvivorsForSuccess` `0x141A0A010`, `…ForFailure` `0x141A0C860`, PvP, challenges `0x141B99C20` | server |
| AdjustSkullTotemPoints (20) | `RewardSurvivorsForSuccess` ("adjusting STP by %i") | server |
| UnlockProduct (6) / AdjustConsumableQuantity (19) | `0x141BD7610` ← `RewardDuffelBags` `0x141A0ADC0`; GCM burn-card charge `0x141768A30` ("Charging hydra ID %s for burn card %s") | server |
| AdjustStatValue (7) | `PlayerStatsComponent` reconcile `0x141C24D80` → `0x141BC5B90` | both (see below) |
| UnlockStartingLocation (8) | `0x141BCEFE0` "Unlocking up to map…" ← `OnMissionEnd` `0x141A08140` (server) **and** `OnRep_UnlockedNewMap` `0x142136240` (client) | both |
| CompleteAchievement (16) | `AchievementTrackerComponent` `0x141C5EF30` | server (client: unverified) |
| Decks, skins, customization, caravans, purchases, badge, spray, recent character | UI code | the client itself |

### What a remote client gets
- **Server-side commands on a remote PPC are dropped.** The non-local branch of `ExecuteCommand` is `ret`, and
  `ApplyCommandToOfflineData` is never reached (not local, and there is no LocalPlayer). Host log at mission end:
  ```
  [offline.76561198063588550]: adjusting SP by 73
  [offline.76561198063588550]: ApplyCommandToOfflineData:AdjustSupplyPoints      <- host: applied
  [offline.76561198975681908]: adjusting SP by 69                                <- client: nothing follows
  [offline.76561198975681908]: unlocking evans_b on Easy                         <- nothing follows
  [offline.76561198975681908]: adjusting stat RiddenKilled:base by 1 …           <- nothing follows (host copy)
  ```
- **Nothing is sent to the client for these commands.** The PPC has `ClientExecute*Command` RPCs (client-side
  `_Implementation`s `0x141BC5230/5330/5430/5570` log `"[CLIENT RPC] …"` and call `ExecuteCommand(…, true)`; `_Validate`s
  return true). But the `FName` globals for these RPCs (`0x14690CD70..CD90`) are referenced only by their static
  initialisers, so no code sends them. The same is true of the `ServerExecute*Command` RPCs.
- **Stats do arrive.** At mission end, `0x141C24370` logs "forcing network flush" for a remote owner
  (`Role==Authority && !IsLocalController`) and sends `PlayerStatsComponent::ClientApplyStatDeltas` (`0x141C24530`).
  The client adds the deltas to its own component. On EndPlay/OnLeavingMap it reconciles them into its own profile.
  Seen in the same-machine client log `b4bcoop-776.log` 21:22:48 (`adjusting stat …` + `ApplyCommandToOfflineData:AdjustStatValue`
  on the client, which left mid-mission). This also works when the client leaves early.
- **Starting locations**: the host sets the replicated `bUnlockedNewMap` for the client. The client's
  `OnRep_UnlockedNewMap` runs the same unlock against its own profile. This is static evidence only.
- The Windows client's profile after the session has `supplyPoints.acquired 4021`, `campaignRuns {}`, and
  `evans_b/evans_c` unlocked on easy. Without its log and a before-copy, it cannot show whether the 69 SP arrived.
  Static analysis says it did not.

### Host profile safety
Every `ApplyCommandToOfflineData` line in the host log carries the host's id. A remote PC has no `GobiLocalPlayer`,
so `0x141BC6930` returns null even if something bypassed the `IsLocalController` gate. On the host, the remote PPC's
`HydraPublicId` (+0x1A8) **is** set (`[offline.76561198975681908]: profile loaded` at join; the log prefix is read from
+0x1A8). That contradicts the static guess in `card-draft.md` §1 and may bear on why card ownership passed natively in
the two-machine session.

## 3. Campaign runs
- The mission URL carries `RunOwner=1`. `CampaignRunComponent.CampaignRunOwners` holds the host's id. On the host:
  `Initialized campaign run component for ResumedRun owner:offline.<host>` and at mission end
  `updating campaign run 1790208526 [OFFLINE]` (`0x141BC1780`), host id only.
- Client: `campaign run applied: 0`, `clearing campaign run data`. Its profile has `campaignRuns: {}`.
- So the run lives only in the host's profile. A client can continue it only by joining the host again. It cannot
  resume solo. This matches retail, where a campaign belonged to the party leader. **No change proposed.** Rewards
  earned during the run (SP, stats, unlocks) are what the client keeps.

## 4. Fix (implemented: `native/src/rewards.c`)
Hook `PPC::ExecuteCommand` `0x141BC4970` (32-byte signature check). The detour calls the original first, then, if the
component's owner is a `PlayerController` whose `Player` is not a `LocalPlayer` (a remote client on the listen host)
and the command type is one of:

| type | RPC sent to that client |
|---|---|
| 5 AdjustSupplyPoints | `ClientExecuteAdjustSupplyPointsCommand` |
| 6 UnlockProduct | `ClientExecuteUnlockProductCommand` |
| 19 AdjustConsumableQuantity | `ClientExecuteAdjustConsumableQuantityCommand` |
| 20 AdjustSkullTotemPoints | `ClientExecuteAdjustSkullTotemPointsCommand` |

it copies the command into the UFunction's parms (`Command` property, offset/size from reflection) and calls
`ProcessEvent` on the PPC. UE routes the NetClient RPC to the owning connection. On the client, the game's own
`_Implementation` runs `ExecuteCommand` on the local controller, so the native offline path writes the client's own
`PlayerProfileSettings.json`. The host's profile is untouched.

- Not forwarded: AdjustStatValue and UnlockStartingLocation (both already reach the client natively; forwarding
  would double-count). Other command types on a remote PPC are logged (`rewards: not forwarded: command type N`) so a
  live test shows what else the host generates (expect 16 CompleteAchievement).
- Idempotence: UnlockProduct is a set insert. SP, STP and consumables are deltas, so they must not also be applied
  natively on the client. Static analysis finds no client-side issuer for them, except challenge rewards
  (`0x141B99C20`, online-only feature). A live test must confirm there is no double count.
- Kill switch: env `B4BCOOP_NO_REWARDS=1`.
- Shared-file edits: `main.c` (`rewards_init()` after `cards_init()`), `cmds.h` (prototype).

Caveat in the client `_Implementation`s: they skip the command when the first local PC is a
`SocialSpacePlayerController` (Fort Hope). Mission rewards arrive in mission maps, so this is fine. A reward issued
in camp would still be lost.

## 5. Live test plan (needs 2 instances with **separate** profiles)
Same-machine tests with one Steam account share `offline.<steamid>` and one `PlayerProfileSettings.json`. Each
instance saves its in-memory copy over the other's, so profile diffs are meaningless there. Use a second account or
redirected save dirs. Before each run, copy both profiles aside and diff them after.

1. Load: host log `rewards: ExecuteCommand hooked`.
2. Mission success with the client alive: host `Rewarding <client> with N SupplyPoints` →
   `rewards: forwarding AdjustSupplyPoints (N) to remote player offline.<client>`. Client log
   `[CLIENT RPC] adjusting SP by N` + `ApplyCommandToOfflineData:AdjustSupplyPoints`. Client profile
   `supplyPoints.acquired` += N exactly once. Host profile changes only by the host's own reward.
3. Mission failure: same, via `RewardSurvivorsForFailure`.
4. Duffel bag picked up by the client: forwarded UnlockProduct/AdjustConsumableQuantity; new entry in client `unlocks`
   or `consumables`.
5. Client equips a burn card: on leaving the saferoom, host `Charging hydra ID … for burn card …` for the client →
   forwarded `AdjustConsumableQuantity (-1)`; client `consumables[...].spent` += 1.
6. Stats: client log `starting profile reconcile` + `ApplyCommandToOfflineData:AdjustStatValue` under the client's id;
   `missionsCompleted_Unsecured.<map>::<diff>` += 1 in the client profile. The host profile gets no client stats.
7. New map unlock for the client: client log `Unlocking up to map …` + `unlocking X on Y` +
   `ApplyCommandToOfflineData:UnlockStartingLocation`.
8. Client leaves mid-mission: stats applied on its EndPlay, no SP (expected). No errors on the host from the RPC to a
   closing connection.
9. Check which `rewards: not forwarded: command type N` lines appear, especially 16 (achievements). Decide whether
   CompleteAchievement needs forwarding. It has no RPC; it could be split into UnlockProduct + AdjustSupplyPoints,
   but only if the host would not re-award it every mission.
10. Post-round screen on the client shows the same N as its profile delta.

## 6. Addresses
| What | VA |
|---|---|
| `PPC::ExecuteCommand` (hook) | `0x141BC4970` |
| `ApplyCommandToOfflineData` / `…bPersist` (online) | `0x141BC4F70` / `0x141BC4C10` |
| offline target (`GobiLocalPlayer.PlayerProfileSettings`) | `0x141BC6930` |
| AdjustSP helper | `0x141ECE2A0` |
| `RewardSurvivorsForSuccess` / `…ForFailure` / `RewardDuffelBags` | `0x141A0A010` / `0x141A0C860` / `0x141A0ADC0` |
| Client RPC impls (SP, STP, consumable, unlock) | `0x141BC5230`, `0x141BC5330`, `0x141BC5430`, `0x141BC5570` |
| PPC vtable (impl +0x450, validate +0x448) | `0x1454E66A8` |
| Stats: network flush / `ClientApplyStatDeltas` send / reconcile | `0x141C24370` / `0x141C24530` / `0x141C24D80` |
| Starting-location unlock / `OnRep_UnlockedNewMap` | `0x141BCEFE0` / `0x142136240` |
| Offsets | PPC owner +0xD8, PPC `HydraPublicId` +0x1A8, `PostRoundBonusSP` +0x7A0, AdjustSP `Delta` +0x8 |
