# Slot guard: more humans than survivor slots (issue #7)

Build 14216215. Static analysis of `Back4Blood.exe` plus live runs with `launch/multi.sh` (2026-09-24).
Implementation: `native/src/slotguard.c`.

## Crash analysis

Any host (vanilla 4 slots or `teamsize=N`) crashed when one more human joined than it had hero slots. The sequence,
from the host log of the baseline run (`five-players.md` §5):

1. `GameSession.MaxPlayers` is 16 (and `teamsize` sets `net.MaxPlayersOverride = N+2`), so the engine lets the
   5th human in.
2. `APlayerSlotManager::RequestSlot` (`0x141A15F70`) finds no free slot: `failed to claim slot on team TeamA`, then
   `DUMP SLOTS`. Its caller (`0x1419FF550`) calls the PC's virtual `+0xDF8`,
   **`AGobiPlayerController::OnFailedToClaimSlot` (`0x141B9B680`)**: `Failed To claim slot!`, state goes to
   Spectating. This is a retail path, but retail matchmaking never lets a 5th player in.
3. About a second later (after the client reports its loading screen gone) the PC goes `Spectating -> Playing` and
   the game mode restarts it: `RestartPlayerAtPlayerStart`.
4. **`0x1419FE7D0` is `AHeroGameMode::RestartPlayerAtPlayerStart(Controller, StartSpot)`**, not a possess handler:
   it first calls the engine's `AGameModeBase::RestartPlayerAtPlayerStart` (`0x143CB3180`, which spawns and possesses
   the hero), then reads `Controller->PlayerState` (`+0x2A8`), resolves the weak pointer at `GobiPlayerState+0x554`
   (`FWeakObjectPtr::Get`, `0x1426F7D20`) and does `cmp byte [slot+0x2BA], 1` (`PlayerSlot.Team`) with no null check:
   `EXCEPTION_ACCESS_VIOLATION` reading `0x2BA` at `0x1419FE875`. Crash stack:
   `0x1419FE875` <- `0x141C1B1A0` <- `0x141C19A34` <- `0x141C173D0` (spawn manager).

Other findings:
- `AGameSession::ApproveLogin` = `0x143CC0440` (`FString* (this, FString* Error, const FString* Options)`). The
  "Server full." check compares `GameMode->GetNumPlayers()` (vtable `+0x6B0`) with `net.MaxPlayersOverride` if > 0,
  else `GameSession.MaxPlayers`. It is called twice per join (Gobi's PreLogin and the engine's).
- `APlayerSlotManager::GetNumFreeSlots(Team)` (`0x141A19D90`) counts slots whose `OwningPlayer` is null. A bot owns
  its slot too (a player state owned by `BotController_BP_C`), so that count says "full" when bots fill the team.
  RequestSlot still gives a joining human the bot's slot (`Bot was occupying slot. Setting Spectator!`: the human owns
  the slot and spectates the bot with the retail "Press SPACE to take over" prompt). So the gate counts humans, not
  free slots.
- `GobiPlayerState.ClientKickWithDisconnectError` (the RPC behind retail "Kicking remote clients with
  EDisconnectError::HostStartedSoloGame") does nothing on our clients: its implementation (`0x141BD5DD0`, PS vtable
  `+0x910`) walks the client's Gobi party members and returns when it finds none. Our clients never are in a party.
- `UNetConnection::Close()` = `0x143E02670` (one argument).

## Fix (`native/src/slotguard.c`), host side

1. **Login gate** (hook `ApproveLogin`): if this is a listen server with a slot manager and the humans in the game
   (player states owned by a `PlayerController`, host included) are >= the hero team's slot count, the join is
   refused with the engine's own "Server full." We don't build an FString: for that one call we set
   `net.MaxPlayersOverride = 1`, so the engine's `NumPlayers >= limit` check fails and allocates the message itself,
   then restore the cvar. Bots are not counted, so hot-join into a bot's slot still works.
2. **Fallback kick** (hook `OnFailedToClaimSlot`): if a remote player still gets no slot (two joiners racing for
   the last slot, a slot reserved for someone else), close its connection on the next tick. The client gets the
   game's disconnect handling.
3. **Crash guard** (hook `AHeroGameMode::RestartPlayerAtPlayerStart`): a Gobi player whose slot weak pointer is
   null is not spawned at all (the original would spawn and then crash), and is kicked as in 2. Logged as
   `slotguard: RestartPlayerAtPlayerStart for ... no slot`. Every normal restart logs
   `slotguard: restart <pc> slot X -> Y`; in all runs X == Y and non-null (the slot is set by RequestSlot at login,
   before the restart), so skipping on a null slot never skips a player that would have been fine.

Client side (`uelog.c` -> `cmds_auto_join_backoff`): when a join fails with `PendingConnectionFailure ... Server
full.`, the ini auto-join waits 60 s instead of 20 s before the next attempt (each attempt reloads the camp and pops
the "session is full" message).

Command: `slotguard` (status: hero_slots, humans, counters), `slotguard gate 0|1` (turn the gate off to exercise the
fallback), `slotguard kick <PlayerArray index>` (testing).

## Live results

Five local instances, one Steam account (`launch/multi.sh`), 2026-09-24.

| Test | Result |
|---|---|
| Crash repro, first build | The first detour treated `0x1419FE7D0` as a 2-argument `PossessedBy` and dropped the third argument: the host crashed inside `AGameModeBase::RestartPlayerAtPlayerStart` on the first spawn. That is how the function was identified. |
| `multi.sh 5`, no teamsize, Fort Hope | Host survives. Joins 2-4: `login approved (humans=1..3 hero_slots=4)`. 5th: `login REJECTED (humans=4 hero_slots=4, no free slot)`, `PreLogin failure: Server full.` Host `players` = 4, every slot human, every hero spawned. |
| Rejected client | `NetworkFailure: PendingConnectionFailure, Error: 'Server full.'`, travels `?closed` to **its own Fort Hope** (standalone, `netdriver: none`) with the game's popup "DISCONNECTED FROM SERVER / Unable to join the session. The session is either full or unavailable." (`slot-guard/rejected-client.jpg`). Without a back-off the ini auto-join retried every 20 s (camp reload + popup each time); now `auto: host is full, next join attempt in 60s` (120 s in this run's build, 60 s in the final one). |
| Fallback, gate off (`slotguard gate 0`), 5th joins | `failed to claim slot` -> `Failed To claim slot!` -> `slotguard: remote player ... got no slot, kicking` -> connection closed 10 ms later. Client: `ConnectionLost`, popup "DISCONNECTED FROM SERVER / Your connection to the game server was lost.", back in its own Fort Hope. Host fine. |
| Crash guard, gate off (earlier build, kick RPC that turned out to be a no-op) | The slotless 5th stayed connected; 2 s later the game restarted it: `RestartPlayerAtPlayerStart for ..., which has no slot: not spawning a hero`. **No crash** (the exact spot that crashed before). The player stayed a spectator with no pawn. |
| Hot-join, vanilla 4 slots | Host + 2 clients start `mission Easy`, `ready`: 3 humans + 1 bot. A 4th instance started mid-mission: `login approved (humans=3 hero_slots=4)`, `Bot was occupying slot. Setting Spectator!`, the client spectates the bot with the retail "PRESS SPACE TO TAKE OVER [BOT] MOM" prompt (`slot-guard/hotjoin-takeover-prompt.jpg`). A 5th instance: `login REJECTED (humans=4 ...)`, back-off. Then one client was killed; after its connection timed out (~90 s) `ReplaceWithBot`, and the waiting 5th instance's next attempt was approved (`humans=3`) and took the bot's slot. |
| `teamsize=5`, `multi.sh 5` | All 5 in Fort Hope (`login approved (humans=1..4 hero_slots=5)`), no rejection. `mission Easy`: all 5 follow into Evansburgh_B within ~30 s, 5 human slots, no rejection during the follow (the old connections are gone after the non-seamless travel, so `humans` restarts at 0). |
| `teamsize=5`, 6th instance | Fort Hope: `login REJECTED (humans=5 hero_slots=5)`, client back in its own camp. In the mission its retries were rejected again until a client was killed: then it hot-joined into the bot's slot, and `takeover 1` (host, `ServerTakeOverBot`) completed the take-over: `TakeOverBot`, `Spectating -> Playing`, the 6th instance plays the hero with 4 teammates in the HUD (`slot-guard/hotjoin-after-reject.jpg`). |
| Normal restarts | Every `slotguard: restart <pc> slot X -> Y` (humans and bots, Fort Hope and mission) had X == Y, non-null. |

Not tested live: a true race (two joiners inside the ~2-4 s PreLogin -> Login window for the last slot). The host side of
it is the same as the gate-off test (a player approved with no free slot), which the fallback handled.

## Limits
- A player whose game crashed rejoins a full session only after the host drops the old connection
  (`ConnectionTimeout`, 90 s as tuned by `cmds.c`): until then the old player state still counts as a human. The
  client's retries are rejected meanwhile (60 s back-off). Before the gate, such a rejoin got in and reclaimed its
  reserved slot (`another Player was occupying slot`).
- The fallback kick shows "Your connection to the game server was lost." rather than "full": the client is told by a
  connection close, not an NMT_Failure message.
- The gate counts every player state owned by a `PlayerController`. A spectator-only player (none exist in this mode)
  would be counted as a human.

## Test helpers added (`testing.c`)
- `takeover <slot>` (host): the human who owns a slot but spectates its bot takes it over
  (`GobiPlayerController::ServerTakeOverBot(AssignedPawn)`), the unattended equivalent of pressing Space.
- `tp volumes | tp <slot> <x> <y> <z> | tp <slot> volume <n>` (host): list `FlashlightVolume`s, teleport a hero
  (`K2_SetActorLocation`, teleport). Used for the flashlight live test.
