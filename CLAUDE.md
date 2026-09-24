# b4b-coop

Unofficial private co-op for Back 4 Blood (up to 4 players), no WB/Turtle Rock services. Approach: take the
game's **offline mode** (full local progression in `PlayerProfileSettings.json`) and turn the offline session
into a **listen server** others join — Seamless Co-op style. An injected agent DLL does the engine work.

Detailed engine findings (addresses, obfuscated layouts, class names): `docs/NOTES.md`. Read it before touching
`native/` or `tools/`.

## Layout
- `native/` — agent DLL (C, `dwmapi.dll` proxy, zig cc + MinHook). `native/build.sh` → `native/out/dwmapi.dll`.
  - `ue.c/h` reflection layer for B4B's modified UE 4.25 (XOR'd GUObjectArray, shuffled FField, UObject +8).
  - `main.c` Tick hook + game-thread command queue + TCP command server (127.0.0.1:47112, first free of +0..7;
    `B4B_COOP_PORT` pins it).
  - `travel.c` SetClientTravel hook: host's absolute travel → `servertravel ...?listen`; client follow/rejoin.
  - `netguard.c` outbound-traffic guard from DllMain (DNS/WinHTTP/TCP allowlist, EOS network off; `netguard`
    command, `netguard=` ini keys); docs/investigations/outbound-traffic.md.
  - `uelog.c` captures UE_LOG into `Gobi/Binaries/Win64/b4bcoop-<winpid>.log` (`b4bcoop-<B4B_COOP_TAG>-<winpid>.log`).
  - `cards.c` host card-ownership override for remote players (interim).
  - `flashlight.c` manual flashlight toggle (`flashlight` command, `flashlight list`, ini hotkey); verified live.
    docs/investigations/flashlight.md.
  - `rewards.c` host forwards remote players' dropped rewards (SP, STP, unlocks, consumables) to their clients via the
    game's unused ClientExecute*Command RPCs. Details: `docs/investigations/client-rewards.md`.
  - `burncards.c` host: remote players can play burn cards (quantity trusted), and each charge is keyed to the player
    who played it. Details: `docs/investigations/burn-cards.md`.
  - `testing.c` unattended testing: auto sign-in Offline (`offline=1`), `signin`, `mission [raw] [map] [difficulty]`,
    `ready [vote]`, `endmission [1|0]`, `burncard list|status|charge|map|[row]`, `callp <Class> <Func> [args]`,
    `takeover <slot>` (finish a hot-join bot take-over), `tp volumes|<slot> <x y z>|<slot> volume <n>`.
  - `teamsize.c` opt-in 5+ player team (`teamsize=N` ini/command, raises `Config.TeamSize` before InitSlots; `slots`
    dumps the slot layout). docs/investigations/five-players.md.
  - `slotguard.c` host: a joiner with no free survivor slot gets "Server full." at login (bots' slots count as free),
    a slotless player is kicked instead of spawned (was a host crash, #7); `slotguard` command.
    docs/investigations/slot-guard.md.
  - `chat.c` in-game chat commands: hooks the local player's Say/SayTeam, `/cmd` is run locally and never sent;
    replies as local chat lines; host notices via ClientTeamMessage with our own type. Test: `type <text>` (real key
    presses), `chat status`, `popup [close]`. `admin.c` the commands (`/help join host leave players ping kick ban
    lock bots restart say ...`, same verbs on the CLI) and the host's PreLogin gate (bans in `b4bcoop-bans.txt`,
    lock). docs/investigations/chat-commands.md.
  - `presence.c` Steam "Join Game": while hosting, rich presence `connect=+b4bcoop_join steam:<id64> addr:<ip:port>`;
    join requests (callback 337) and the same string on the command line become a session join target (overrides
    host=/join=, auto sign-in Offline). `presence [on|off]`, `steamjoin <string>` (simulate), `invite`, `friends`;
    ini `presence=0`, `presence_addr=`. docs/investigations/steam-invites.md.
  - `steamnet.c` Steam P2P: UDP shim under the retail net driver (ws2_32 sendto/recvfrom ↔ ISteamNetworking P2P,
    Steam peers get fake 198.18.x.y addresses); hosts take UDP and Steam joins at once, `join steam:<id64>`, SteamID
    in `status`, `steamnet [on|off]`, ini `steam_p2p=0`. USteamNetDriver can't work here (no STEAM socket subsystem).
    Not yet tested between two accounts. docs/investigations/steam-p2p.md.
  - `cmds.c` commands: `status players host join leave exec find call peek`; config = `b4bcoop.ini` next to the DLL or
    `B4B_COOP_CONFIG=<windows path>` (`cmds_config_path()`; keys `host join steam_p2p offline flashlight_*`).
    `coop_join(target)` = join entry point (`ip[:port]`, `steam:<id64>`; any thread); `join=` may list alternatives (`steam:<id>,1.2.3.4:7777`);
    `coop_host`, `coop_leave`.
- `launch/` — `install.sh` (build+copy DLL; rm before cp — never overwrite a mapped DLL in place), `run.sh` (Proton,
  no EAC; `B4B_PREFIX` = alternate compatdata), `multi.sh`/`multi-stop.sh`/`instance.sh`/`shot.sh` (N local test
  instances, below), `two.sh` (old: two copies on the real prefix), `winpy.sh`, `probed.sh`, `uninstall.sh`.
- `tools/` — `b4b.py` agent CLI (`B4B_AGENT=n-1` = instance n), `testprefix.py` (test prefixes), `pe.py` static analysis, `memprobe.py` +
  `probed.py`/`probe.py` live memory (Windows Python inside the prefix), `sdkdump.py`, `winpoke.py`, `fetch-deps.sh`.
- `sdk/` — local only (gitignored, kept out of the public repo): reflection dump of all `/Script` classes.
  Regenerate with `tools/sdkdump.py` (see docs/NOTES.md).

## Setup
`tools/fetch-deps.sh` (zig, MinHook, Windows Python, .venv) → `launch/install.sh`.
Steam launch options: `WINEDLLOVERRIDES="dwmapi=n,b" %command%`. Game build pinned: Steam buildid 14216215;
the agent verifies byte signatures and refuses to hook on mismatch.

## How to run N local instances (unattended)
`launch/multi.sh N` (N = 1-5; install the DLL first). Instance 1 hosts, the others join `127.0.0.1:7787`; no clicks:
the agent presses Sign in and answers the Online/Offline popup with Offline. Returns when the host sees N players
(3 instances ≈ 90 s). Then e.g. `B4B_AGENT=0 .venv/bin/python tools/b4b.py mission Normal` starts a new Evansburgh
campaign run on the host (same path as the war table); clients follow automatically. `launch/multi-stop.sh` kills
only test instances (SIGKILL by PID, matched on `B4B_PREFIX` in /proc/<pid>/environ).
- Instance n: prefix `~/.local/share/b4b-coop/prefixes/test<n>` (clone of the real one, ~600 MB; muted, 960x540,
  low settings, ui cvars in its Engine.ini), config `<prefix>/b4bcoop.ini`, agent port 47112+n-1 (`B4B_AGENT=n-1`),
  log `b4bcoop-test<n>-<winpid>.log`, window "B4B #n" (`launch/shot.sh n out.png`).
- Env: `B4B_GAME_PORT` (7787 — deliberately not 7777, so tests can't reach a real session), `B4B_STAGGER`,
  `B4B_TIMEOUT`, `B4B_FRESH=1` (re-clone), `B4B_BLANK="2 3"` (fresh offline profile for those instances),
  `B4B_INI_EXTRA="netguard=off;netguard_eos=0"` (extra `b4bcoop.ini` lines for every instance).
- The real prefix and its SaveGames are never written. Profile truth is the AES `PlayerProfileSettings.sav`; the
  `.json` is an export the game overwrites, so editing it does nothing. All copies share one Steam account: same
  name, same `offline.<steamid64>` id on the host.
- Ending a mission unattended: `mission Easy`, wait for both heroes, `ready` (match → InProgress), `endmission 1`
  (success) or `endmission 0` (failure). The post-round screen times out after ~2 min and moves on to the next chapter.
- Profile saves are deferred (~30 s after `ApplyCommandToOfflineData`); wait before `multi-stop.sh` (SIGKILL) or diffing.
- `-Port=` on the command line sets the listen port (UE `FURL` default port); in use → it binds the next one.
- 5 players: `B4B_INI_EXTRA="teamsize=5" launch/multi.sh 5` (opt-in `teamsize` in `native/src/teamsize.c`). Verified: a
  full mission and 2 chapter transitions with 5 humans. Without it, a 5th joiner is refused with "Server full."
  (`slotguard.c`; before that it crashed the host). Results: `docs/investigations/five-players.md` §5,
  `docs/investigations/slot-guard.md`.

## Gotchas
- UE4SS does not work on this game (obfuscated engine) — don't go back to it.
- Wine reparents the game to systemd: `/proc/<pid>/mem` is unreadable (yama=1). Use the Windows-side tools.
- `pkill -f <pattern>` kills your own shell when the pattern appears in the command; kill by PID from `pgrep`.
- SIGTERM on a Wine game whose wineserver is gone leaves a zombie with ~200 threads parked in ntsync that still holds
  its UDP port; use SIGKILL. Proton resets `STEAM_COMPAT_DATA_PATH` inside the game (use `WINEPREFIX`/own vars).
- Shipping build writes no engine log; the agent enables the UE_LOG gate (0x1469BD96D) and captures it.
- Native Windows: the exe is ASLR'd (Wine keeps the preferred base), so all static addresses go through `VA()` in
  `ue.h`. System d3d/dxgi DLLs load our dwmapi too and import ordinal-only exports, so `proxy.c`/`dwmapi.def` are
  generated by `tools/gen-proxy.py` to re-export every real export. Launch with `launch/run.cmd` (no EAC
  bootstrapper; `B4B_DIR` for non-default libraries). Build on Windows: Windows zig in `vendor/zig`, Git Bash.

## Progress
Verified live (2026-09-23/24; details and evidence in `docs/investigations/*.md` and closed GitHub issues):
- Offline Fort Hope as listen server (PacketRelayNetDriver, UDP 7777, DTLS); auto-host/auto-join via `b4bcoop.ini`;
  camp → mission via server-travel redirect; chapter → chapter via the game's own seamless travel; clients take over
  bots through the retail PlayerSlotManager path. Real two-machine session with a Windows client on its own account.
- Remote players keep their own deck (`cards.c` safety net), their rewards (`rewards.c`: supply points etc. forwarded
  to their own profile, #4) and can play burn cards, charged to their own profile (`burncards.c`, #6).
- No third-party traffic offline (`netguard.c`, #5): 0 public connections in a 16-min session on Proton.
- Manual flashlight toggle with replication and sticky mode (`flashlight.c`, #2).
- 5-player co-op, opt-in `teamsize=5` (`teamsize.c`, #1); joins beyond the slot count are rejected with
  "Server full." instead of crashing the host (`slotguard.c`, #7).
- Unattended N-instance local testing (`launch/multi.sh`, `testing.c`, #3).

Known issues / open:
- #8 post-round lineup shows 4 of 5 heroes with teamsize=5 (cosmetic).
- Not yet run on native Windows: netguard (WinHTTP path), rewards/burn cards/slot guard/5 players across machines.
- All local test copies share one Steam id; two-account behavior is only covered by the one real session.
- Steam Join Game/invites (`presence.c`): rich presence, launch-command-line join and simulated join requests verified
  on one account; the real callback, the Join Game menu and Steam-initiated launch need the two-account plan in
  docs/investigations/steam-invites.md. Local copies on one account overwrite each other's rich presence.
- A client that disconnects before the saferoom-exit charge keeps its burn card; skull totem points and duffel-bag
  rewards use the verified forwarding path but weren't awarded in tests.

Next:
1. Real multi-machine session on the new build (Windows client): netguard, rewards, burn cards, 5 players.
2. In-game UX for host/join (no ini/CLI); two-account test of Steam P2P (plan in docs/investigations/steam-p2p.md).
3. #8 lineup.
