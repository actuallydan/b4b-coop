# Post-round stats for remote players (issue #30)

Build 14216215. Report (Dan, real play): on the post-round screen the other players don't see their kills and the
other per-player stats. Status: **not reproduced yet** (the live run was stopped before the mission: the test lane
was needed for real play). Static analysis and the host log of a real session (`b4bcoop-552.log`, 0.5.0, 2026-09-25,
host + Kopkins over Steam P2P) below; the agent now logs every machine's post-round values so the next session shows
which link breaks.

## 1. Where the numbers come from

| Panel | Data | Code |
|---|---|---|
| Stats table (one row per stat, one column per player) | each player's `GobiPlayerState.PostRoundStatValues` (TArray<int32>, one per `PostRoundStatConfigs` entry, Replicated) | `PostRoundStatsUserWidget::InitializeStats` 0x141E49C90: local PS's team, `GameState.PlayerArray`, skips PS with `bFromPreviousLevel` (PS+0x2AA bit 0x40); per PS 0x141BD0220 pairs config i with `PostRoundStatValues[i]` (no bounds check) |
| Lineup (stat under each hero) | `PlayerSlot.ControllingPlayer` → `PostRoundLineupStats[EPostRoundLineupStat]` (double[11], Replicated) | 0x141E47210 |
| Summary (own stats, old → new) | the **local** player's `PlayerStatsComponent` banks, read directly (PS+0x768) | 0x141E4E260 (ShowStat) |

Server side, `GobiPlayerState::Tick` 0x141BCE320 calls 0x141BCE650 when `Role == Authority` (+0x120 == 3; verified
against the `BeginPlay Role:` log code): throttled, it copies the stats component's banks into
`PostRoundStatValues` (+0x790), `PostRoundLineupStats` (+0x7E0) and `ScoreboardStats` (+0x838); **no component
(PS+0x768 null) zeroes them all**. All three are registered with no condition (GetLifetimeReplicatedProps
0x141BD8080, params zeroed: COND_None, not push-based).

`PlayerStatsComponent` (size 0x2F68): three banks of 44 `EPlayerProfileStat` entries, 0x58 bytes each, value first:
- +0x158 this map: reconciled into the profile and zeroed on OnLeavingMap/EndPlay (0x141C24D80, "resetting map stats").
- +0x1078 since the component was created ("clearing stats" 0x141C25080 zeroes all three banks).
- +0x1F98 not yet sent to the owning client: flushed by `ClientApplyStatDeltas` (0x141C24530; forced at mission end,
  "forcing network flush"); the client's `_Implementation` (0x141C25250, vtable) adds each delta to its own +0x158 and
  +0x1078.
`StatTrackerBase::IncrementStatValue` 0x1422B6890 adds to +0x158 and +0x1078, and to +0x1F98 when the owning
controller is not local.

PS+0x768 is cached from the owner controller (`FindComponentByClass`) in `GobiPlayerState::SetOwner` 0x141BCD600 and
`OnRep_Owner` 0x141BCD790 (0x141BCE540), logged as `SetOwner ... HasProfile:%d HasStats:%d`.

## 2. What the real session's host log shows
- The remote player's mission PS had its component: `GobiPlayerState_BP_C_2147472888 SetOwner 0//GobiPlayerController_BP_C_2147472894 HasProfile:1 HasStats:1` (19:01:25).
- The host tracked the remote player's kills: at OnLeavingMap after the post-round, `[offline.76561198084368598]:
  adjusting stat RiddenKilled:base by 163` (the host itself: 202).
- So on the host, that PS's replicated values should have been non-zero during the post-round. Bots have no stats
  component (`HasStats:0`): their columns are always 0.
- Mission end: `InProgress -> WaitingForPlayerStatSync` (forced flush for the remote player), 2 s timer, then
  `WaitingPostMatch` (post-round screen).

Nothing found statically that would blank remote players on a client: every panel is fed from replicated
PlayerState values or from the client's own component, which receives the deltas. Open candidates, to check live:
1. The host's values for the remote PS are zero after all (PS+0x768 stale or null at the time, e.g. after a
   seamless chapter transition), which the lineup/table would show on every machine.
2. The client's copy of the remote PS is filtered out (`bFromPreviousLevel` set on a PS carried over by seamless
   travel) or its values arrive late (PS net update rate vs. the moment the table is built).
3. It is the Summary panel on the client (own component): deltas missing or late.

## 3. Diagnostics added (`native/src/poststats.c`)
- Every build logs once per post-round screen, 5 s in, on host and clients: `poststats: post-round values (host|client)`
  then per player `#i <name> values[n]: ... lineup: ... scoreboard: k p bits=<PS bool byte> stats: kills map M all A`
  (`stats: none` = no component on that machine, normal for other players on a client and for bots).
- Dev: `poststats` (same dump, any time); `cheatprobe killas <#n> [count]` (host: lethal damage to ridden credited to
  player #n, so a client gets kills in unattended tests).

## 4. Live test (to run)
```
export B4B_GPU=4090; launch/gamelock.sh acquire <me>; launch/install.sh
B4B_INI_EXTRA="allow_joins=anyone" launch/multi.sh 2      # allow_joins: when no Steam client is running
B4B_AGENT=0 tools/b4b.py mission Easy; ...ready
B4B_AGENT=0 tools/b4b.py cheatprobe killas '#0' 8; ... killas '#1' 8   # after the horde spawns (or /spawn with cheats
                                                                          # off again before the end: cheats taint stats)
B4B_AGENT=0 tools/b4b.py poststats; B4B_AGENT=1 tools/b4b.py poststats  # before the end
B4B_AGENT=0 tools/b4b.py endmission 1
# every ~8 s during the ~2 min post-round: launch/shot.sh 1 host-N.png; launch/shot.sh 2 client-N.png; poststats on both
```
Compare the Stats/Lineup/Summary panels between host and client with the `poststats` values on each side.
