# Join timing (#41)

Issue: joining works, but timing (joining a host who isn't in Fort Hope yet, one player in the lobby before the
other, other combinations) was flaky. Goal: map the join path, find the races, make it simpler and deterministic.

## The join path (client)
1. **Target.** A Steam Join Game / invite / `+b4bcoop_join` on the command line (presence.c `handle_connect`: protocol
   and add-on policy checked first), `/join` and the `~` window's Join (cmds.c `coop_join`), or the ini `join=`.
2. **Sign-in.** A Steam join arms the Offline auto sign-in (signin.c): press Sign in, answer the Online/Offline popup.
   No join starts before it is done (a join's LoadMap during the sign-in resets the profile, test-profiles.md).
3. **Attempt.** `cmd_join` = `open <host>?b4bcoop=<protocol>...` (`steam:` = a fake 198.18.x address carried over
   Steam P2P, steamnet.c). The host's PreLogin (admin.c) checks join policy, protocol, add-on policy, bans, lock, slots.
4. **Follow.** The host's mission start becomes a server travel (travel.c); clients follow it and travel.c rejoins if
   the follow fails (DTLS handshake while the host still loads, `?closed`).

## Matrix (tools/jointest.py)
Host + client on lane-1 test prefixes, the join triggered with the host in one state and the client in another,
repeated; up to three pairs at once (test1+2, 3+4, 5+6; `--gpus 4090,4090,5090`). Loopback IP target (one Steam
account: Steam P2P to "ourselves" loses 30-70 % of packets), `addons=0` (Dan's add-ons in the lane folder fail the
default cosmetic policy). Pass = client connected, in the host's map, with a hero in a mission (play: after the
retail bot take-over), host lists it; title-host cells also need the host to sign in and a mission to take both.

Host states: `boot` (launched with the client), `title` (not signed in), `camp`, `mload` (mission loading at the
trigger), `m2c` (mission started the moment the client is welcomed into the camp), `lobby` (pre-round), `play`
(after `ready`). Client modes: `cold` (Steam launch string on the command line), `ini` (`join=`), `title`, `camp`
(hosting its own camp), `load` / `fhload` (join request during the startup load / right after the title Fort Hope
loads), `p2p` (camp, over Steam P2P).

| cell | before: pass/fail, median s | after: pass/fail, median s |
|---|---|---|
| camp:cold | 2/0, 39 | 2/0, 35 |
| camp:title | 2/0, 11 | 2/0, 6 |
| camp:camp | 2/0, 4 | 2/0, 4 |
| camp:ini | 2/0, 44 | 2/0, 38 |
| boot:cold | 3/0, 47 | 3/0, 44 |
| boot:ini | 3/0, 52 | 3/0, 44 |
| title:cold / title / camp | 5/0 each | 3/0 each |
| camp:fhload | 7/0, 10-11 | 3/0, 8 |
| camp:load | 3/0, 10 | 2/0, 7 |
| mload:cold | 3/0, 43 | 3/0, 41 |
| mload:title | 3/0, 13 | 3/0, 13 |
| mload:camp | 4/0, 13 (one 188 s: see "half-open") | 4/0, 16 (two DTLS failures, retried after 3 s) |
| m2c:camp | 3/0, 24 | 4/0, 18 |
| m2c:cold | 3/0, 58 | 3/0, 45 |
| lobby:cold | 3/0, 37-41 | 2/1, 35 (the fail: host crashed alone in its own mission load, below) |
| lobby:camp | 3/0, 7 | 3/0, 6 |
| play:camp / play:cold | 2/0, 8 / 2/0, 42 | 2/0, 7 / 2/0, 37 |

Before: 72 valid trials (3 more were killed by a second run started by mistake; the first `play` runs failed only
on the driver: a mid-mission hot-join spectates its bot until "Press SPACE to take over", retail behaviour). After:
54/55. Title-cell times include the driver's 30 s wait before signing the host in. Artifacts
`/tmp/b4b-jointest-baseline{,2}/`, `/tmp/b4b-jointest-after/` (2026-10-04).

On loopback nothing failed outright before either; what the matrix and older e2e logs show is time lost and two
mechanisms fighting, which is what turns into "sometimes it doesn't work" over real Steam P2P and slower machines.

## Findings
1. **The auto-join loop raced the host's server travel.** When the host starts a mission, the client gets the
   follow travel (`SetClientTravel type=2`) and for a moment looks like "alone in Fort Hope" (connection gone, camp
   still loaded); the loop then fired its own `open` ~0.2 s later (every e2e log, e.g. lane1-logs/20260930-030708
   test2: `type=2` at 03:05:30.081, `auto: joining` at 03:05:30.250, then a DTLS failure and a travel.c rejoin).
2. **The host kicked clients at mission start.** The offline war-table start runs `KickRemoteClients
   (HostStartedSoloGame)` before its travel; our redirect makes the travel a server travel, but a client still
   loading the camp got the kick instead ("Host closed the connection."), reloaded its own camp and waited for the
   next attempt (m2c cells: 24-58 s).
3. **A fresh join that failed the DTLS handshake (host loading a mission) hung** in the pending connection until the
   next attempt 20 s later; only follows retried after handshake errors (mload:camp rep 2).
4. **Every failed attempt reloaded the client's own camp** (`?closed`), e.g. the add-on policy check's first refusal
   in every `ini` join, then waited up to 20 s.
5. **Half-open connections on the host.** A failed handshake leaves a pending connection on the host for 180 s
   (InitialConnectTimeout, raised by cmds.c for slow loads). Over IP each attempt is a new source port, so harmless
   (it only shows as `client_conns=2`). Over Steam P2P every attempt came from the same fake address, so the next
   attempts' packets went to the dead connection until it timed out (inferred from the code; needs the two-account
   check).
6. **Sign-in detection by "no SignInScreen"** (signin_arm: in Fort Hope with no screen = signed in; signin_step: screen
   gone = signed in) depends on when the screen is created/destroyed. In practice the screen appears ~2 ms after the
   title Fort Hope's LoadMap, before the first tick, so the race never hit (fhload cells), but it was fragile.
7. **Polling.** Sign-in steps and the join loop ran every 2 s (≈6 s per join), retries every 20 s, a Steam target
   was dropped after 6 attempts (~2 min) whatever the host was doing; `/join` had no retry at all.
8. **Joining a host on its title screen works**: its title Fort Hope is already a listen server and signing in loads
   no map; the client stays connected while the host signs in and follows it into a mission (title cells).

## Changes
- **One join target, one loop** (cmds.c): Steam, `/join`, the `~` window and `join=` all go through it. Attempt as soon
  as signed in and in our own camp (checked every 0.25 s); after a failure retry in 3 s (×3), then 8 s, then 15 s; an
  attempt with no answer restarts after 30 s; a session target gives up after 3 minutes without a connection (the
  window restarts on every connection), `join=` never. Refusals keep their rules (full/locked/not a friend 60 s;
  version, ban, add-ons stop).
- **No reload after a failed attempt**: travel.c drops the failed attempt's `?closed` while we are still in our own
  camp (`auto: staying in our camp after the failed join (no reload)`); the retry starts from there.
- **No join while following**: the loop waits while travel.c's rejoin window is open (`travel_following`).
- **No kick at mission start while hosting** (travel.c hooks `UMatchmaking::KickRemoteClients` 0x141AEE600; log
  `travel: not kicking N remote client(s) ... they follow the server travel`).
- **DTLS failure of a fresh join** → `cmds_join_failed` → retry in 3 s.
- **Steam P2P: a new address per client socket** (steamnet.c): the P2P header's tag is `process tag + n` for the
  client's n-th game socket; the host reports the sender's port from the tag, so each attempt is a new connection on
  the host, like a new UDP source port. Older peers send one tag per process (one port, as before): no protocol bump.
- **Sign-in**: `IsSignedIn()` of the local controller (0 on the title, 1 after sign-in and on a connected client),
  steps every 0.5 s.

## Rerun on the merged build (lane 2, dreamsofants, 2026-10-04)
After merging main (0.9.x: default `addons_policy=any`, burn-card rejoin, instant `/model`, dated logs), same 20 cells
× 2 on lane 2 (`B4B_STEAM=flatpak`, three pairs on the 4090, `/tmp/b4b-jointest-l2-final`): **40/40**, medians
camp:cold 38, camp:title 7, camp:camp 4, camp:ini 38 (one attempt now: the host's default policy `any` no longer
refuses the first login), boot 52/55, title 44, fhload 8, load 6, mload 42/14/14, m2c 17/49, lobby 36/6, play 8/39.
Steam P2P on one account (Flatpak): camp 0/1, mload 0/1 before stopping the run: a handshake completes, then the
login never arrives within 90 s (one account's packet loss, as in steam-p2p.md); not a measure of the change.

## Steam P2P on one account (after)
`camp:p2p` 1/3, `mload:p2p` 3/3 (`/tmp/b4b-jointest-p2p`). One account is the lossy case of steam-p2p.md (Steam hands
packets for "our" SteamID to either copy): the failed runs are repeated DTLS handshake failures over 3 minutes. What
they do show: every attempt is a new connection on the host (`NotifyAcceptedConnection` per attempt, the failed ones
time out separately), and one run had a completed handshake cut by the 30 s attempt limit, now 90 s once the host
answered (`cmds_join_answered`).

## e2e
`tools/e2e.py --quick` 14/14 (`/tmp/b4b-e2e-20261004-030836`): the mission follow is one `type=2` travel and
`Welcomed` 7 s later, `travel: not kicking 1 remote client(s)` on the host, no competing join. It needed
`B4B_INI_EXTRA1..5=addons=0`: lane 1's game folder now holds the player's add-ons (several "gameplay" physics assets),
which fail the default cosmetic policy, so the client stops joining (correct behaviour, but it fails e2e's join).
Since #43 the default policy is `any`, so this no longer applies.

## Two-account check
Steam's friends-list **Join Game** is `steam://rungame/924970/<friend id64>/<url-encoded connect string>` in the
Steam client (steamui `JoinGame` → `steam://rungame/<app>/<id>/<connect>`); with the game running Steam posts
`GameRichPresenceJoinRequested_t` (337) to it, so opening that URL in the client's Steam is the real Join Game path
(#10) without clicking in the UI. `tools/jointest2acct.py` does it per trial:
1. Hold both locks: `launch/gamelock.sh acquire <me>` and `B4B_STEAM=flatpak launch/gamelock.sh acquire <me>`.
2. Native Steam signed in to Hergmgurk, `native-steam-in-use` absent; then `launch/flatpak-steam.sh start` (the native
   client first, so each owns its own service port; run.sh passes the right `Steam3Master` either way).
3. Install the build in both lanes: `launch/install.sh` and `B4B_STEAM=flatpak launch/install.sh`.
4. `tools/jointest2acct.py /tmp/b4b-2acct camp*3 mload*3 mpre*3 m2c*2 lobby*3 play*3 invite*2 session*2`: host = lane 1 test1 (`-Port=7787`), client
   = lane 2 test1; the client reads the host's connect from its own Steam (`friends`), its Steam opens the rungame
   URL (`flatpak enter <instance> .../ubuntu12_32/steam steam://rungame/...`). Pass = client log `presence: steam
   join request from <host id>` (callback 337), connected, same map (mission cells: Evansburgh_B with a hero);
   screenshots `<cell>-<n>-host.png` / `-client.png`.
5. What to look at: `mload` (a DTLS failure while the host loads, then a retry that is a new connection on the host:
   `NotifyAcceptedConnection` per attempt, no 180 s wait), `m2c` (`travel: not kicking`, the client follows), `title`
   (callback arms the Offline sign-in, joins after it). Then release both locks.

### Result (2026-10-05, 0.9.2 dev build, one machine: native Steam Hergmgurk hosts, Flatpak Steam dreamsofants joins)
Every join went through Steam's real Join Game path (rungame URL in the client's Steam → callback 337 in the running
game) over Steam P2P between the two accounts (`steamnet`: peer `active=1 relay=0 policy=allowed`, 1 session request).
Artifacts `/tmp/b4b-2acct/{matrix,mpre,session}/`.

| cell | pass | s (click → in) | notes |
|---|---|---|---|
| camp | 3/3 | 6 | one attempt |
| mload (host starts the mission at the click) | 3/3 | 10-11 | one attempt: the P2P session setup (~5 s) outlasts the host's load, the handshake completes in the mission |
| mpre (host starts the mission 2-4 s after the click, mid-handshake) | 3/3 | 14-17 | reps 1-2: DTLS failure → retry 3 s later as a **new connection on the host** (2 × `NotifyAcceptedConnection`, the second completes at once, no 180 s wait); rep 3: in before the travel, then followed |
| m2c | 2/2 | 17-18 | `travel: not kicking`; the host's travel went out while the client still loaded the camp (no PlayerController yet), so the client got "Host closed the connection" when the host's driver closed, and the join loop got it in 3 s later. Same timing as on loopback (18 s) |
| lobby | 3/3 | 7 | |
| play | 3/3 | 10-11 | hot-join, bot handed over (`takeover`) |
| invite (host `invite <id>` → InviteUserToGame `sent`; accepted with the invite's connect string) | 2/2 | 6 | accepting is the same rungame URL the invite's Join button opens; Steam's own invite UI was not clicked |
| session (below) | 2/2 | | |

So the per-socket P2P address change is not broken: over two real accounts the retry after a failed handshake is a
fresh connection and lands in seconds; the one-account p2p failures (above) were the one-account packet loss.

`session` = join in camp (6 s), host `mission Easy` → client follows (10 s, 2 human slots), client plays
Burn_RollGunAR and host Burn_RollGunHG, client `leave`s and comes back through Join Game mid-mission (4-6 s, bot taken
over), `ready` + saferoom charge (`charging b4bcoop.burn.0 -> remote player's profile (rejoined since the card was
played)`, client `[CLIENT RPC] adjusting consumable Burn_RollGunAR by -1`), `endmission 1` (`forwarding
AdjustSupplyPoints (73) to remote player offline.76561198994546085`). Profiles after the deferred save, both reps:
client SP +73 and Burn_RollGunAR.spent +1; host Burn_RollGunHG.spent +1 and its own SP +73; neither got the other's
card.

## Refusals with a reason (0.9.3)
Two gaps after the changes above:
- **Blind rejoins after a refused follow.** When the host refused a reconnect at a mission start (add-ons, version,
  ban, lock, full), chat.c stopped the join loop, but travel.c's rejoin window kept reopening the same login URL
  (up to 20 times, every 5 s; without the add-on claim, so an add-on check could never pass that way).
  Now `travel_end_follow()` closes the window on any refusal with a reason; the `?closed` takes the client back to
  its own camp and the join loop decides: add-ons failing / another version / banned stop, "Server full." / locked /
  not a friend try again in 60 s, a passed add-on check rejoins with `?b4bcoopaddonsok=`.
- **The reason was easy to miss.** A failed join stays in the camp now, so the game shows no popup (its
  `Queued disconnect error` is never shown, also not on the next map load), only a loading screen, and
  `chat_local_later` waited for a map change. Now chat.c puts the reason in a popup: a game message popup that opens
  within 30 s gets `SetText` ("COULD NOT JOIN" + the reason), else our own (`UIBlueprintFunctionLibrary.
  OpenMessagePopup`, OK) once the camp has settled; the chat line follows (`chat_local_soon`: no map change needed).
Live (lane 2, dev build, 2026-10-05; host `addons_policy=none`, client with a cosmetic add-on):
- fresh join: 1 attempt, `auto: join ... stopped`, `chat: refusal popup: opened ours: This host allows no add-ons;
  turn off Walker checker outfit ...` (screenshot: popup with that text), host `asked to check` once.
- refused follow (host `/addons policy none`, dev `/addons unseat`, `mission Easy`): `travel: not rejoining ...:
  refused: add-ons`, own camp, `chat: refusal popup: the game's .../MessagePopup_WBP_C_... now says: ...`, 1 attempt.
- locked (`/lock`, `/kick 1`, client `join`): `The host locked the session.`, `next attempt in 60s`, our popup
  "... Trying again in a minute."; after `/unlock` the next attempt (18:46:58, 57 s later) connected.

## Open
- Two accounts on one machine connect directly (`relay=0`); the relay path across networks is still unchecked.
- Not covered by the two-account run: clicking Join Game / an invite in Steam's own UI, and a Join Game with the game
  closed (Steam-initiated launch); the client's title-screen cell (`title`) was not rerun.
- One host crash in the after run (lobby:cold rep 2): the host alone, during its camp → mission LoadMap, in garbage
  collection (minidump: execute fault at a heap address, stack in GC/LoadMap); no client was connected and none of
  the changed code runs there. Earlier lanes have other sporadic crashes in map loads.
