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
  - `uelog.c` captures UE_LOG into `Gobi/Binaries/Win64/b4bcoop-<winpid>.log` (`b4bcoop-<B4B_COOP_TAG>-<winpid>.log`).
  - `cards.c` host card-ownership override for remote players (interim).
  - `flashlight.c` manual flashlight toggle (`flashlight` command, ini hotkey); docs/investigations/flashlight.md.
  - `testing.c` unattended testing: auto sign-in Offline (`offline=1`), `signin`, `mission [raw] [map] [difficulty]`.
  - `cmds.c` commands: `status players host join exec find call peek`; config = `b4bcoop.ini` next to the DLL or
    `B4B_COOP_CONFIG=<windows path>` (`cmds_config_path()`; keys `host join offline flashlight_*`).
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
  `B4B_TIMEOUT`, `B4B_FRESH=1` (re-clone), `B4B_BLANK="2 3"` (fresh offline profile for those instances).
- The real prefix and its SaveGames are never written. Profile truth is the AES `PlayerProfileSettings.sav`; the
  `.json` is an export the game overwrites, so editing it does nothing. All copies share one Steam account: same
  name, same `offline.<steamid64>` id on the host.
- `-Port=` on the command line sets the listen port (UE `FURL` default port); in use → it binds the next one.
- Known: 5 instances → the host crashes in Fort Hope when the 5th hero spawns (4 slots); 4 is the working maximum.

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
Done (2026-09-23):
- Recon, own tooling, SDK dump; agent DLL injected and driving the game.
- Offline Fort Hope hosted as listen server (PacketRelayNetDriver/UDP 7777, DTLS); second instance joins.
- Host mission start redirected to server travel; client joins mission, takes over a bot via retail
  PlayerSlotManager/TakeOverBot path. Verified with two instances on one machine.
- Automatic client follow into missions verified: early follow fails DTLS handshake → agent rejoins 3s later →
  client claims a slot (~13s end to end).
- **Real two-machine session (2026-09-23)**: friend on a separate PC + Steam account joined via `b4bcoop.ini`
  (auto-host/auto-join) — client on **Windows**, EAC did not block the DLL — followed into a mission (~9s), and stayed connected across a chapter transition — the
  game's own seamless travel (`bSeamless: 1`) handles chapter-to-chapter; our redirect only covers camp → mission.

Known issues:
- Card draft: host had no profile for remote players, so every deck card failed ownership → client got a 15-card
  draft and actually played with no deck cards. Fix `native/src/cards.c` (host hook on 0x14176DDA0, remote humans
  own their deck) installed. Two-machine session: no draft, and the remote player confirmed they had **their own**
  selected deck, yet the override never fired (host logged `Equipped custom preset 0 to slot 1`). Why ownership
  passed natively there (vs the same-account local test) is not understood yet; client log pending.
  Details: `docs/investigations/card-draft.md`.

Next:
0. Read the friend's client log: explain why card ownership passed natively for a different account.
1. Per-player progression: send each client's offline profile (decks/unlocks/cosmetics) to the host.
2. Block remaining outbound traffic in offline mode (EOS SDK config polls, Cloudflare/AWS HTTPS).
3. In-game UX for host/join (no ini/CLI), Steam P2P instead of raw IP + port forwarding.
