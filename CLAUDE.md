# b4b-coop

Unofficial private co-op for Back 4 Blood (up to 4 players), no WB/Turtle Rock services. Approach: take the
game's **offline mode** (full local progression in `PlayerProfileSettings.json`) and turn the offline session
into a **listen server** others join — Seamless Co-op style. An injected agent DLL does the engine work.

Detailed engine findings (addresses, obfuscated layouts, class names): `docs/NOTES.md`. Read it before touching
`native/` or `tools/`.

## Layout
- `native/` — agent DLL (C, `dwmapi.dll` proxy, zig cc + MinHook). `native/build.sh` → `native/out/dwmapi.dll`.
  - `ue.c/h` reflection layer for B4B's modified UE 4.25 (XOR'd GUObjectArray, shuffled FField, UObject +8).
  - `main.c` Tick hook + game-thread command queue + TCP command server (127.0.0.1:47112, +1 per extra instance).
  - `travel.c` SetClientTravel hook: host's absolute travel → `servertravel ...?listen`; client follow/rejoin.
  - `uelog.c` captures UE_LOG into `Gobi/Binaries/Win64/b4bcoop-<winpid>.log`.
  - `cmds.c` commands: `status players host join exec find call peek`.
- `launch/` — `install.sh` (build+copy DLL; rm before cp — never overwrite a mapped DLL in place), `run.sh` (Proton,
  no EAC), `two.sh` (host+client copies on one machine, labels windows), `winpy.sh`, `probed.sh`, `uninstall.sh`.
- `tools/` — `b4b.py` agent CLI (`B4B_AGENT=1` = second instance), `pe.py` static analysis, `memprobe.py` +
  `probed.py`/`probe.py` live memory (Windows Python inside the prefix), `sdkdump.py`, `winpoke.py`, `fetch-deps.sh`.
- `sdk/` — reflection dump of all `/Script` classes (text). `sdk.json` is regenerated, not committed.

## Setup
`tools/fetch-deps.sh` (zig, MinHook, Windows Python, .venv) → `launch/install.sh`.
Steam launch options: `WINEDLLOVERRIDES="dwmapi=n,b" %command%`. Game build pinned: Steam buildid 14216215;
the agent verifies byte signatures and refuses to hook on mismatch.

## Gotchas
- UE4SS does not work on this game (obfuscated engine) — don't go back to it.
- Wine reparents the game to systemd: `/proc/<pid>/mem` is unreadable (yama=1). Use the Windows-side tools.
- `pkill -f <pattern>` kills your own shell when the pattern appears in the command; kill by PID from `pgrep`.
- Shipping build writes no engine log; the agent enables the UE_LOG gate (0x1469BD96D) and captures it.

## Progress
Done (2026-09-23):
- Recon, own tooling, SDK dump; agent DLL injected and driving the game.
- Offline Fort Hope hosted as listen server (PacketRelayNetDriver/UDP 7777, DTLS); second instance joins.
- Host mission start redirected to server travel; client joins mission, takes over a bot via retail
  PlayerSlotManager/TakeOverBot path. Verified with two instances on one machine.

In progress:
- Automatic client follow into missions (handshake-failure → 3s rejoin). Built, installed, **not yet verified**.

Next:
1. Internet play with a second machine (Tailscale or UDP 7777 forward); package mod for the friend.
2. Per-player progression: send each client's offline profile (decks/unlocks/cosmetics) to the host.
3. Block remaining outbound traffic in offline mode (EOS SDK config polls, Cloudflare/AWS HTTPS).
4. In-game UX for host/join (no CLI), Steam P2P instead of raw IP, seamless travel between mission chapters.
