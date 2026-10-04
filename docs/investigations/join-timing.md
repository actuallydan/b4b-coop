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

## Open
- Two-account check over real Steam P2P (lane 1 native host + lane 2 Flatpak client): mload/m2c with `steam:`.
- One host crash in the after run (lobby:cold rep 2): the host alone, during its camp → mission LoadMap, in garbage
  collection (minidump: execute fault at a heap address, stack in GC/LoadMap); no client was connected and none of
  the changed code runs there. Earlier lanes have other sporadic crashes in map loads.
