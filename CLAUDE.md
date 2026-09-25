# b4b-coop

Unofficial private co-op for Back 4 Blood (up to 4 players), no WB/Turtle Rock services. Approach: take the
game's **offline mode** (full local progression in `PlayerProfileSettings.json`) and turn the offline session
into a **listen server** others join — Seamless Co-op style. An injected agent DLL does the engine work.
Player defaults (0.3.0): every offline Fort Hope hosts, friends join through Steam "Join Game" over Steam P2P only,
the game's UDP socket is bound to 127.0.0.1 (nothing reachable from the network); `host_ip=1` is the advanced IP
opt-in. Player docs: README.md (top) and its copy in `launch/package.sh` (`b4bcoop-README.txt`); every chat command
and ini key: `docs/COMMANDS.md` (shipped as `b4bcoop-COMMANDS.txt`; update it with any player-facing change).

Detailed engine findings (addresses, obfuscated layouts, class names): `docs/NOTES.md`. Read it before touching
`native/` or `tools/`.

## Layout
- `native/` — agent DLL (C, zig cc + MinHook), built as a proxy of `X3DAudio1_7.dll` (loads from the game dir on Proton
  and Windows with no launch options) and of `dwmapi.dll` (legacy, dev only: `launch/install.sh --legacy`, not shipped). Proxies are generated into `native/proxy/` by
  `tools/gen-proxy.py`. `native/launcher/redirect.c` → `xinput1_3.dll` for the game root: on Windows the Steam
  launcher stub loads it and it starts the game instead of the EAC bootstrapper. docs/investigations/launch.md.
  `native/build.sh` → `native/out/` (dev build); `native/build.sh --release` → `native/out/release/` (player build,
  defines `B4B_RELEASE`: no TCP command server, no dev/test commands, no `offline=1` automation; what
  `launch/package.sh` ships); both produce all three DLLs and generate `native/out/gen/b4bcoop_version.h` from
  `VERSION` (see "Versioning"). Dev-only code is under `#ifndef B4B_RELEASE` (whole
  `testing.c`, the command server, every CLI-only `*_cmd` handler). Player builds are verified from their log
  (`b4bcoop loaded (player build) as X3DAudio1_7.dll`), not with `tools/b4b.py`.
  - `ue.c/h` reflection layer for B4B's modified UE 4.25 (XOR'd GUObjectArray, shuffled FField, UObject +8).
  - `main.c` Tick hook; logs `b4bcoop <version> (protocol N)` at load; `-b4bcoop=off` on the command line = the agent
    starts nothing. Dev builds: game-thread command queue + TCP command server (127.0.0.1:47112, first free of
    +0..7; `B4B_COOP_PORT` pins it).
  - `travel.c` SetClientTravel hook: host's absolute travel → `servertravel ...?listen`; client follow/rejoin.
  - `netguard.c` outbound-traffic guard from DllMain (DNS/WinHTTP/TCP allowlist, EOS network off; `netguard`
    command, `netguard=` ini keys); docs/investigations/outbound-traffic.md.
  - `uelog.c` captures UE_LOG into `Gobi/Binaries/Win64/b4bcoop-<winpid>.log` (`b4bcoop-<B4B_COOP_TAG>-<winpid>.log`).
  - `cards.c` host card-ownership override for remote players (interim).
  - `flashlight.c` manual flashlight toggle (`flashlight` command, `flashlight list`, ini hotkey); verified live.
    docs/investigations/flashlight.md.
  - `rewards.c` host forwards remote players' dropped rewards (SP, STP, unlocks, consumables) to their clients via the
    game's unused ClientExecute*Command RPCs. Details: `docs/investigations/client-rewards.md`.
  - `rewardguard.c` client: validates those RPCs before they reach the offline save (SP/STP bounds, burn-card -1 only
    for a card this client played this map, duffel-bag products only); rejects logged `rewardguard: REJECTED`. Dev:
    `rewardguard [products|difficulty|product <row>]`, host `rewardtest sp|stp|cons|unlock|slb ...`.
    client-rewards.md §8.
  - `burncards.c` host: remote players can play burn cards (quantity trusted), and each charge is keyed to the player
    who played it. Details: `docs/investigations/burn-cards.md`.
  - `signin.c` auto sign-in Offline (press Sign in, answer the Online/Offline popup): armed by a Steam join
    (presence.c); dev builds also by ini `offline=1`.
  - `testing.c` (dev builds only) unattended testing: `signin`, `mission [raw] [map] [difficulty]`,
    `ready [vote]`, `endmission [1|0]`, `burncard list|status|charge|map|[row]`, `callp <Class> <Func> [args]`,
    `takeover <slot>` (finish a hot-join bot take-over), `tp volumes|<slot> <x y z>|<slot> volume <n>`, rewards Easy
    never gives: `stp <N>` (forces the skull-totem count for the next `endmission 1`), `items` / `giveitem <slot> <#>`
    (hand a pickup, e.g. a duffel bag, to a hero; `giveitem <slot> row <DataTable> <Row>` any item row, e.g. a weapon),
    `duffelreward <slot> <product guid> [delta]`.
  - `paks.c` (model mods #23) the engine's pak layer. Both builds: mounts our unsigned paks right after the retail
    ones, exempted by identity from the three signature paths (player builds hook nothing when there is no add-on).
    Dev only: `dumpassets <glob> [outdir]` extracts files as the engine reads them (IterateDirectory + OpenRead on
    FPakPlatformFile), `paks`, `mountpak <path> [order]`, ini `modpaks=<windows dir>` (raw paks, order 3000+). Pak
    writer: `modkit/b4bpak.py` (`tools/b4bpak.py` forwards). docs/investigations/model-mods-paks.md.
  - `addons.c` add-ons (#20): `<game>\b4bcoop-addons\*.pak` (ini `addons_dir=`, `addons=0`), read in DllMain:
    `addonlist.txt` on/off + load order (later wins, read order 1000+), embedded `b4bcoop-addoninfo.txt`, index SHA1
    checked, conflicts (same file; "mixed" = one package from two add-ons) logged + one chat notice; chat
    `/addons [on|off|info <#>]` (applies on restart). Packer `modkit/addon.py pack|info|check`.
    Client-side only, no protocol bump. docs/investigations/addons.md.
  - `teamsize.c` opt-in 5+ player team (`teamsize=N` ini/command, raises `Config.TeamSize` before InitSlots; `slots`
    dumps the slot layout). docs/investigations/five-players.md.
  - `lineup.c` post-round/pre-round/character-select lineup with 5+ heroes (#8): spawns an extra mannequin when the
    hero team has more slots than the lineup level's 4 and places it in the back row; `lineup` dumps it.
    five-players.md §6.
  - `slotguard.c` host: a joiner with no free survivor slot gets "Server full." at login (bots' slots count as free),
    a slotless player is kicked instead of spawned (was a host crash, #7); `slotguard` command.
    docs/investigations/slot-guard.md.
  - `chat.c` in-game chat commands: hooks the local player's Say/SayTeam, `/cmd` is run locally and never sent;
    replies as local chat lines; host notices via ClientTeamMessage with our own type. Test: `type <text>` (real key
    presses), `click <x> <y>` (mouse click, e.g. post-round Continue), `chat status`, `popup [close]` (dev builds).
    `admin.c` the commands (`/help join host leave players ping kick ban lock bots restart say ready ...`, same verbs
    on the dev CLI) and the host's PreLogin gate (join policy first, then the b4bcoop protocol (`?b4bcoop=` login
    option; mismatch → "Host runs b4bcoop X (protocol N); you have Y (protocol M). Everyone needs the same version."
    on both sides), then bans in `b4bcoop-bans.txt`, lock). B4B's PreLogin gets the options as a parsed TArray of
    {FString key, value} (`options_str`), not one FString. Every chat command has a permission (`cmds.h`: `CMD_ANYONE`
    own game only / `CMD_HOST` / `CMD_CHEAT` host + cheats on; admin.c `CMDS`, cheats.c `VERBS` via `cheats_perm()`),
    checked once in `admin_slash` before any handler. docs/investigations/chat-commands.md.
  - `models.c` `/model` runtime model swaps (#19, `models` branch): another survivor's outfits/pieces through the
    game's own replicated `PlayerSlot.CurrentCustomizationSet` (`ServerSelectCustomizationSet`, no validation), NPC
    bodies as a made-up row `b4bcoop.npc.<name>` put on by every b4bcoop machine; host `/model <player>`,
    `/models off` (hook on the RPC implementation), campaign-run save keeps own looks. Dev `mdl ...`.
    docs/investigations/model-swap.md, player page docs/commands-models.md.
  - `cheats.c` Cheats: opt-in, host-only sandbox through chat (`/cheats on|off`, then `/god /heal /revive /ammo /copper
    /card /fly /noclip /walk /tp /freecam /size /horde /director /spawn /killall /freeze /slomo /win /lose`, host's
    own save `/supply /unlockall`); off again back in camp; every cheat touching others is a host notice; a map with
    cheats on sends remote players no rewards (rewards.c), stats or achievements. Dev `cheat <cmd>`, `cheatprobe`.
    Player reference: the Cheats section of `docs/COMMANDS.md`. Host-side only, no protocol bump.
  - `thirdperson.c` `/thirdperson` (CMD_ANYONE, host and clients, no cheats): the local hero's own over-the-shoulder
    camera (PlayerViewComponent +0x200 + UpdateView), first person while aiming, game's own 3P moments left alone; on
    until toggled off (all maps, sessions; not saved). Local only, no protocol bump. Ini `thirdperson=1` (start on),
    `thirdperson_key=` (toggle, default N). Dev `thirdperson view [1|2|3]`. docs/investigations/third-person.md.
  - `joinpolicy.c` host: who may join. Default only the host's Steam friends (`ISteamFriends::HasFriend`) and its own
    SteamID; ini `allow_joins=friends|anyone`, `allow_steamids=<id64>,...`; dev `allow_self=0`, `joinpolicy [check
    <id64>]`. Checked at the Steam P2P session request (steamnet.c, authenticated id) and in PreLogin (admin.c; IP
    joins, `host_ip=1` only: the login's claimed id). docs/investigations/steam-p2p.md "Join policy".
  - `presence.c` Steam "Join Game": while hosting, rich presence `connect=+b4bcoop_join steam:<id64> proto:<n>
    ver:<x.y.z>` (+ `addr:<ip:port>` only with `host_ip=1`); join requests (callback 337) and the same string on the
    command line become a session join target (overrides host=/join=, auto sign-in Offline), or stop at once with the
    version message if `proto:` differs. `presence [on|off]`, `steamjoin <string>` (simulate), `invite`, `friends`;
    ini `presence=0`, `presence_addr=` (host_ip=1 only). docs/investigations/steam-invites.md.
  - `steamnet.c` Steam P2P: UDP shim under the retail net driver (ws2_32 sendto/recvfrom ↔ ISteamNetworking P2P,
    Steam peers get fake 198.18.x.y addresses); `join steam:<id64>`, SteamID in `status`, `steamnet [on|off]`, ini
    `steam_p2p=0`. Its `bind` hook puts the game exe's wildcard UDP binds on 127.0.0.1 unless `host_ip=1` (not other
    modules': Windows steamclient64 binds in-process). USteamNetDriver can't work here (no STEAM socket subsystem).
    docs/investigations/steam-p2p.md (incl. "Loopback binding", "Two-account result").
  - `cmds.c` dev commands: `status players host join leave exec find call peek poke`; config = `b4bcoop.ini` next to the DLL
    or `B4B_COOP_CONFIG=<windows path>` (`cmds_config_path()`; no ini = defaults; keys `host` (default 1, 0 when
    `join=` is set) `join host_ip steam_p2p allow_joins allow_steamids flashlight_*`, dev `offline allow_self
    b4bcoop_protocol_override`). `coop_join(target)` = join entry point (`steam:<id64>`; `ip[:port]` only 127.x unless
    `host_ip=1`, else refused with `coop_ip_join_off_msg()`; appends `?b4bcoop=<proto>?b4bcoopver=<ver>`; any thread);
    `join=` may list alternatives (`steam:<id>,1.2.3.4:7777`); `coop_host`, `coop_leave`; `coop_version/protocol`.
- `launch/` — `install.sh [--release] [--legacy]` (build+copy DLLs, dev by default, `--legacy` = dev-only dwmapi.dll;
  rm before cp — never overwrite a mapped DLL in place), `package.sh` (player zip `dist/b4bcoop-<version>.zip`
  mirroring the game folder, its `b4bcoop-README.txt` (CRLF, mirrors README's player section: keep in sync) +
  `dist/SHA256SUMS`, reproducible), `run.sh` (Proton, no EAC; `B4B_PREFIX` = alternate compatdata; writes
  `steam_appid.txt`), `multi.sh`/`multi-stop.sh`/`instance.sh`/`shot.sh` (N local test instances, below), `gamelock.sh`,
  `winpy.sh`, `probed.sh`, `uninstall.sh` (the README's Remove list).
- `tools/` — `b4b.py` agent CLI (`B4B_AGENT=n-1` = instance n), `appinfo.py` (Steam appinfo.vdf dump), `testprefix.py` (test prefixes), `pe.py` static analysis, `memprobe.py` +
  `probed.py`/`probe.py` live memory (Windows Python inside the prefix), `sdkdump.py`, `winpoke.py`, `fetch-deps.sh`.
- `modkit/` — the mod maker's kit (#21), a separate deliverable (players never need it; nothing of it is in the player
  zip or the agent): `b4bmod.py` (one command: setup/status/config, find/extract (offline, `dotnet/pakx` = CUE4Parse
  from NuGet with its managed Oodle decoder), info/tree/export/texture/mi (`dotnet/b4bmod`, UAssetAPI), mesh
  info/export/import/edit (`skm.py`, `skmgltf.py`, `upkg.py`), survivor/weapon (`b4bmodel.py` + `blender/b4bfit.py`
  in headless Blender, static meshes `sm.py`: model → fitted, LODs, textures, then packed/installed), pack/install/
  check (`addon.py`)), `b4bmod.cmd`/`b4bmod.sh`, `README.md` (Windows first; how to get every third-party piece),
  `docs/` (author guides; models: `docs/meshes.md`), `package.sh` → `dist/modkit/b4bcoop-modkit-<version>.zip`
  (reproducible; refuses 64-hex strings). The pak AES key is built into b4bmod.py (public, same for every copy;
  `config aes_key`, `B4B_AES_KEY`, `--aes-key` override); pakx checks it against every pak index SHA1. No native
  Oodle, ever: CUE4Parse's OodleSharp only. Dev-only asset tools stay in `tools/modkit/` (`pakscan.py`,
  `customversions.py`, `assetcheck/`, `uassetrt/`, `testassets/`). Data: `~/.local/share/b4b-coop/`
  (`%LOCALAPPDATA%\b4b-coop` on Windows).
- `sdk/` — local only (gitignored, kept out of the public repo): reflection dump of all `/Script` classes.
  Regenerate with `tools/sdkdump.py` (see docs/NOTES.md).

## Setup
`tools/fetch-deps.sh` (zig 0.15.2 sha256-checked, MinHook pinned to commit 8af6b4a, Windows Python, .venv; `--build`
= only zig + MinHook) → `launch/install.sh`. CI: `.github/workflows/ci.yml` builds dev + player on every push/PR;
`release.yml` builds the zip on a `v<version>` tag (must match `VERSION`) and publishes it with `SHA256SUMS` and a
build provenance attestation. No Steam launch options needed (the agent is `X3DAudio1_7.dll`; the dev-only legacy
`dwmapi.dll` needs `WINEDLLOVERRIDES="dwmapi=n,b" %command%`). Steam's only public launch entry runs the root `Back4Blood.exe` stub →
`start_protected_game.exe` (EAC) → `Gobi/Binaries/Win64/Back4Blood.exe Gobi -SaveToUserDir`. Game build pinned: Steam buildid 14216215;
the agent verifies byte signatures and refuses to hook on mismatch.

## Versioning
`VERSION` (repo root) holds both numbers; `native/build.sh` turns them into `native/out/gen/b4bcoop_version.h`
(`B4B_VERSION`, `B4B_PROTOCOL`), and `launch/package.sh` names the zip after the version.
- **version** (semver): bump for **every release**; tag the release `v<version>` (release.yml checks it).
- **protocol** (integer): bump whenever host and client behavior must match: new RPC use, changed reward/burn-card
  forwarding rules, anything a client must understand or a host must expect from the client. The host refuses a
  login with another protocol (admin.c), and a Steam Join Game to a host with another protocol stops before
  connecting (presence.c). A pure host-side or client-side fix needs no protocol bump.
- Test a mismatch: dev ini `b4bcoop_protocol_override=N` fakes another protocol (e.g.
  `B4B_INI_EXTRA2="b4bcoop_protocol_override=2" launch/multi.sh 2`).

## How to run N local instances (unattended)
`launch/multi.sh N` (N = 1-5; install the DLL first). Instance 1 hosts (by default: its ini has no `host=` line), the
others join `127.0.0.1:7787` (loopback: allowed without `host_ip`, all game sockets are on 127.0.0.1); no clicks:
the agent presses Sign in and answers the Online/Offline popup with Offline. Returns when the host sees N players
(3 instances ≈ 90 s). Then e.g. `B4B_AGENT=0 .venv/bin/python tools/b4b.py mission Normal` starts a new Evansburgh
campaign run on the host (same path as the war table); clients follow automatically. `launch/multi-stop.sh` kills
only test instances (SIGKILL by PID, matched on `B4B_PREFIX` in /proc/<pid>/environ).
- Instance n: prefix `~/.local/share/b4b-coop/prefixes/test<n>` (clone of the real one, ~600 MB; muted, 960x540,
  low settings, ui cvars in its Engine.ini), config `<prefix>/b4bcoop.ini`, agent port 47112+n-1 (`B4B_AGENT=n-1`),
  log `b4bcoop-test<n>-<winpid>.log`, window "B4B #n" (`launch/shot.sh n out.png`).
- Env: `B4B_GAME_PORT` (7787 — deliberately not 7777, so tests can't reach a real session), `B4B_STAGGER`,
  `B4B_TIMEOUT`, `B4B_FRESH=1` (re-clone), `B4B_BLANK="2 3"` (fresh offline profile for those instances),
  `B4B_INI_EXTRA="netguard=off;netguard_eos=0"` (extra `b4bcoop.ini` lines for every instance), `B4B_INI_EXTRA<n>`
  (instance n only), `B4B_PORT_BASE` (agent ports; 47120 when another game with the agent holds 47112).
- **Test prefixes are shared between sessions: change them (`testprefix.py`, `B4B_FRESH`, `B4B_BLANK`, editing their
  `b4bcoop.ini` or saves) only while holding `launch/gamelock.sh`.** `testprefix.py` refuses to touch a prefix a game
  process is running on (`B4B_PREFIX` in /proc/<pid>/environ) unless `--force`.
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

## Regression suite
**Before merging/releasing: run `tools/e2e.py --quick`** (install the DLL under test first; the suite takes
`launch/gamelock.sh` itself, or pass `--no-lock` if you already hold it). It launches `multi.sh 2` and checks, each
with a timeout and PASS/FAIL: join, mission follow, client flashlight replicated to the host, a host and a client burn
card charged once to their own profiles, chat `/players` typed on the client, ready + `endmission 1` with the client's
SP forwarded, a seamless chapter transition, both profiles diffed after the deferred save (client SP +forwarded
amount exactly, host only its own), the 0.3.0 defaults (host without `host=`, presence `steam:` + `proto:` and no
`addr:`, protocol in the login options), no public peer on any game socket and nothing bound off loopback (`ss`
sampler). `--full` adds a vanilla
`multi.sh 5` (5th refused "Server full.", host survives) and a `teamsize=5` round (5 follow, SP forwarded to all 4
clients). Summary table at the end, exit 1 on failure; logs, agent transcript, profile diffs, ss samples and
screenshots in `/tmp/b4b-e2e-<time>/` (`--out`).

## Branches
- `main`: shippable. Releases are tagged from here.
- `models`: ALL model/content-mod work (epic #23: spikes #16-#18, Tier 0 #19, add-on system #20-#22). Agents branch
  from `models` and merge back into `models`; nothing model-related lands on `main` until the epic is DONE. Never
  commit game assets, extracted files or paks (copyright); extracted content lives under `~/.local/share/b4b-coop/`.

## Gotchas
- UE4SS does not work on this game (obfuscated engine) — don't go back to it.
- Wine reparents the game to systemd: `/proc/<pid>/mem` is unreadable (yama=1). Use the Windows-side tools.
- `pkill -f <pattern>` kills your own shell when the pattern appears in the command; kill by PID from `pgrep`.
- SIGTERM on a Wine game whose wineserver is gone leaves a zombie with ~200 threads parked in ntsync that still holds
  its UDP port; use SIGKILL. Proton resets `STEAM_COMPAT_DATA_PATH` inside the game (use `WINEPREFIX`/own vars).
- Shipping build writes no engine log; the agent enables the UE_LOG gate (0x1469BD96D) and captures it.
- Native Windows: the exe is ASLR'd (Wine keeps the preferred base), so all static addresses go through `VA()` in
  `ue.h`. System d3d/dxgi DLLs load our dwmapi too and import ordinal-only exports, so every proxy
  (`native/proxy/*.c/.def`) is generated by `tools/gen-proxy.py` to re-export every real export. Both agent names
  may be present: one agent per process (named mutex; a dwmapi.dll from the same dir wins). A plain Steam Play is
  meant to work via the root `xinput1_3.dll` (unverified on Windows); fallback: launch.md §3 B (launch option). Build on Windows: Windows zig in `vendor/zig`, Git Bash.

## Progress
Verified live (2026-09-23/24; details and evidence in `docs/investigations/*.md` and closed GitHub issues):
- Offline Fort Hope as listen server (PacketRelayNetDriver, UDP 7777, DTLS); auto-host (default) / auto-join;
  camp → mission via server-travel redirect; chapter → chapter via the game's own seamless travel; clients take over
  bots through the retail PlayerSlotManager path. Real two-machine session with a Windows client on its own account.
- Remote players keep their own deck (`cards.c` safety net), their rewards (`rewards.c`: supply points etc. forwarded
  to their own profile, #4) and can play burn cards, charged to their own profile (`burncards.c`, #6).
- No third-party traffic offline (`netguard.c`, #5): 0 public connections in a 16-min session on Proton.
- Manual flashlight toggle with replication and sticky mode (`flashlight.c`, #2).
- 5-player co-op, opt-in `teamsize=5` (`teamsize.c`, #1; post-round lineup shows all 5, `lineup.c`, #8); joins
  beyond the slot count are rejected with "Server full." instead of crashing the host (`slotguard.c`, #7).
- Unattended N-instance local testing (`launch/multi.sh`, `testing.c`, #3).

- Two real Steam accounts on one machine (2026-09-24): Steam P2P join, mission follow and per-player rewards verified
  (docs/investigations/steam-p2p.md, "Two-account result"); second account runs in Flatpak Steam.
- 0.3.0 defaults (2026-09-24, local copies on Proton): hosting with no `host=` line, presence `steam:` + `proto:` and
  no `addr:`, game UDP on 127.0.0.1 (host and client) with loopback joins working (`e2e.py --quick` 12/12),
  remote-IP joins refused with the message, protocol mismatch refused on both sides, `host_ip=1` back to 0.0.0.0 and
  IP joins.
- Model mods (`models` branch, 2026-09-25, epic #23, local sessions on Proton): add-ons in player builds (#20),
  multiplayer rules `addons_policy` (#22), texture/material edits, SKM + static-mesh writing, a CC0 FBX survivor
  (3P + FP arms) and an AK replacing AR02 as add-ons; the separate `modkit/` (b4bmod, own zip). Not run on real
  Windows (modkit .NET tools); hair renders opaque, no facial animation.

Known issues / open:
- #8 (fixed): the 5th hero in the post-round lineup stands in the back row, dimmer and without a name plate.
- Not yet run on native Windows: netguard (WinHTTP path), rewards/burn cards/slot guard/5 players across machines,
  and the no-script launch (root `xinput1_3.dll` redirect + `X3DAudio1_7.dll`; test script in
  docs/investigations/launch.md §6). On Proton the `X3DAudio1_7.dll` agent is verified with a plain Steam launch.
- All local test copies share one Steam id; two-account behavior is only covered by the one real session.
- Steam Join Game/invites (`presence.c`), now the players' main join path: rich presence, launch-command-line join and
  simulated join requests verified on one account; the real callback, the Join Game menu and Steam-initiated launch
  need the two-account plan in docs/investigations/steam-invites.md (#10). Local copies on one account overwrite each
  other's rich presence.
- A client that disconnects before the saferoom-exit charge keeps its burn card. Skull totem points and duffel-bag
  rewards reach the client (verified, client-rewards.md §6b), but a remote player's duffel roll can't see what they
  own, so they may get a product they already have (a no-op).

Next:
1. Two-account test of Steam's own Join Game click / invite (#10; plan in docs/investigations/steam-invites.md).
2. Real multi-machine session on the new build (Windows client, launch.md §6): netguard, rewards, burn cards, 5
   players, and whether Windows shows any Firewall prompt.
