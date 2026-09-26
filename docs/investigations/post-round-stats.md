# Post-round stats for remote players (issue #30)

Build 14216215. Report (Dan, real play): on the post-round screen the other players don't see their kills and the
other per-player stats.

## 1. Result
**Cause: cheats turned on in Fort Hope carried into the mission.** Both of Dan's real host logs (`b4bcoop-552.log`,
0.5.0, with Kopkins; `b4bcoop-556.log`, 0.4.0, with bird-meme.jpg) have `/cheats on` in camp, then the war-table
`servertravel` with cheats still on. cheats.c kept them on because the new map is a mission (the rule meant for
chapter → chapter) and tainted it, so for the whole mission the host held back every `ClientApplyStatDeltas`
(`cheats: not sending ClientApplyStatDeltas to a remote player (cheats were on this map)`, ~20× per mission) and the
rewards (`rewards: NOT forwarding AdjustSupplyPoints (151) ... cheats were on this map`). The client's post-round
summary reads its **own** `PlayerStatsComponent`, which only those deltas fill: every stat showed `(0)` / `+0`.

**Fix** (cheats.c, host only, no protocol bump): leaving Fort Hope turns cheats off (`cheats: off (left Fort Hope)`,
notice `cheats turned off (they don't carry over from Fort Hope)`); the mission is not tainted. Chapter → chapter
still keeps them on, now with a reminder notice (`cheats are still on (this map's rewards and stats aren't sent to
other players' saves)`). Cheats turned on during a mission still withhold that map's stats and rewards (by design:
docs/COMMANDS.md "Cheats"), and the players' post-round summary then shows 0 for that map.

Verified live (lane 2, Flatpak Steam, 2 instances, kills credited with dev `cheatprobe killas`: host 6, client 9):

| Run | cheats state in the mission | Client summary STATS | Client SP |
|---|---|---|---|
| before (old cheats.c): `/cheats on` in camp, `mission Easy` | `on=1 tainted=1` | RIDDEN KILLED (0), ENEMY DAMAGE (0) | 73 shown, not credited (650) |
| after: same steps | `on=0 tainted=0` | RIDDEN KILLED (9), ENEMY DAMAGE (1,059,300) | forwarded (650 → 723) |
| no cheats (old build; new build: e2e) | - | RIDDEN KILLED (9) ... | forwarded |

The host's summary showed its own 6 / 642,000 in every run. Chapter carry-over checked: `cheats on` in Evansburgh C,
`endmission 1`, Evansburgh D has `on=1 tainted=1` and the reminder notice.

## 2. Where the post-round numbers come from (PvE)
Panels (GetPostRoundPanelInfos 0x141D5F0C0): Splash, Lineup, Summary. The Stats table (EPostRoundPanel::Stats) is a
PvP-only panel. The Summary opens behind modal pages (REWARD UNLOCKS, SUPPLY POINTS EARNED) that need Continue.

| Panel | Data | Code |
|---|---|---|
| Lineup (one stat under each hero) | `PlayerSlot.ControllingPlayer` → `GobiPlayerState.PostRoundLineupStats[EPostRoundLineupStat]` (double[11], Replicated) | 0x141E47210 |
| Summary STATS (`SummaryStatEntry_WBP`: name, (total), +this map) | the **local** player's `PlayerStatsComponent` (PS+0x768) banks +0x158 / +0x1078, per `PostRoundSummaryUserWidget.Stats` (+0x470) | 0x141E4E260 (from 0x141E4A930) |
| Stats table (PvP) | each player's `PostRoundStatValues` (TArray<int32>, one per `PostRoundStatConfigs`, Replicated); skips PS with `bFromPreviousLevel` | 0x141E49C90, 0x141BD0220 |

Server side, `GobiPlayerState::Tick` 0x141BCE320 → 0x141BCE650 when `Role == Authority` (+0x120 == 3): copies the
stats component's banks into `PostRoundStatValues` (+0x790), `PostRoundLineupStats` (+0x7E0), `ScoreboardStats`
(+0x838); no component (bots: `HasStats:0`) = all 0. Registered with COND_None (GetLifetimeReplicatedProps 0x141BD8080).

`PlayerStatsComponent` (0x2F68): three banks of 44 `EPlayerProfileStat` entries, 0x58 bytes each, value first.
+0x158 this map (reconciled into the profile and zeroed on OnLeavingMap/EndPlay, 0x141C24D80 "resetting map stats"),
+0x1078 since the component was created (the summary's total; "clearing stats" 0x141C25080 zeroes all three),
+0x1F98 not yet sent to the owning client. `StatTrackerBase::IncrementStatValue` 0x1422B6890 adds to +0x158 and
+0x1078, and to +0x1F98 when the controller is remote; the host flushes +0x1F98 with `ClientApplyStatDeltas`
(0x141C24530, periodic, forced at mission end: `forcing network flush`); the client's `_Implementation` (0x141C25250,
vtable) adds the deltas to its own +0x158 / +0x1078. PS+0x768 is cached from the owner controller in
`GobiPlayerState::SetOwner` 0x141BCD600 / `OnRep_Owner` 0x141BCD790 (logged `SetOwner ... HasStats:%d`).

Without cheats, replication and the client's deltas worked in every test: host and client `poststats` dumps matched
(e.g. `values[3]: 9 0 0 lineup: 9 ... 1059300` on both), lineups identical, both summaries correct, and after a
seamless chapter the client's own component had the new map's deltas (`kills map 7 all 16`).

Side notes: `PostRoundStatValues` grows by one config's worth per seamless chapter on a carried-over PS (BeginPlay
appends zeros to the copied array: `values[9]`); harmless, retail behaviour. Bots never get stats.

## 3. Tools added (`native/src/poststats.c`, `cheats.c`)
- Every build: `poststats: post-round values (host|client)` + one line per player, once per post-round screen, 5 s in
  (`#i name values[n]: ... lineup: ... scoreboard: k p bits=<PS bool byte> stats: kills map M all A` or `stats: none`).
- Dev: `poststats`; `cheatprobe killas <#n> [count]` (host: lethal damage to ridden with player #n as instigator, so
  the kill/damage trackers credit that player); UI probes for headless runs (no input reaches UMG there):
  `uitext <path part> [max]` (live TextBlock/RichTextBlock texts), `callw <path suffix> <Func>`,
  `uihide <path suffix> [visibility]`, `funcs <Class>` (Blueprint classes too), `objat <addr>`.
- Summary on screen in an unattended run: `callw WidgetTree_0.UnlockPreviewPanel OnFadeOutCompleted` on each instance
  (what Continue ends in), then `launch/shot.sh`.

```
export B4B_LANE=2 B4B_STEAM=flatpak B4B_GPU=4090   # or lane 1 when allowed
launch/gamelock.sh acquire <me>; launch/install.sh; launch/multi.sh 2
b4b.py cheat cheats on; b4b.py mission Easy; ...; b4b.py ready
b4b.py cheatprobe killas '#0' 6; b4b.py cheatprobe killas '#2' 9     # the humans' indices from `players`
b4b.py endmission 1; sleep 20; uitext SummaryStatEntry_WBP_C_ on both; callw ... OnFadeOutCompleted; shot.sh 1/2
```
