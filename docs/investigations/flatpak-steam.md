# Test instances on the Flatpak Steam client (B4B_STEAM=flatpak)

Goal: while Dan plays on his native Steam account (Hergmgurk, 76561198063588550), test instances talk only to the
Flatpak Steam client (account dreamsofants, 76561198994546085, `~/.var/app/com.valvesoftware.Steam`, lane 2's game).

## Fix (2026-09-26 evening): working
`launch/flatpak-steam.sh start` + `B4B_STEAM=flatpak` (run.sh/instance.sh):
- Steam's sandbox gets its own SysV IPC namespace: the user override `shared=!ipc` is set only while `flatpak run`
  creates the sandbox and reset once Steam has logged on (Dan's permanent overrides untouched; the running sandbox
  keeps its namespace). Plus `--filesystem=~/.local/share/b4b-coop/prefixes/lane2` and
  `--filesystem=xdg-run/b4b-lane2:create` (headless gamescope sockets). Steam gets a minimal environment (`env -i` +
  session vars), not the agent shell's (the user manager's environment carries API tokens: they'd reach Steam and
  every game it runs). Proof: steam `ipc:[4026534047]`, host `ipc:[4026531839]`; connection_log `[U:1:1034280357]`.
- Why not `flatpak run --unshare=ipc`: Flatpak 1.18's portal forwards it to every sub-sandbox Steam spawns as
  `--noshare=ipc`, which `flatpak run` rejects (`flatpak-spawn -vv true` inside: `error: Unknown option
  --noshare=ipc`, exit 1). The Flathub wrapper's startup check then fails with exit 71 and pops the desktop dialog
  "The unofficial Steam Flatpak app requires a working D-Bus session bus and flatpak-portal service" (two of those on
  Dan's desktop came from this). Via the override the same check passes (exit 0) and the namespace is private.
- The game joins Steam's own sandbox with `flatpak enter <instance>` (all its namespaces; environment = the steam
  process's + ours). NOT `steam-runtime-launch-client --bus-name=com.steampowered.PressureVessel.LaunchAlongsideSteam`:
  the native Steam's launcher service registers the same well-known name (started 18:06 while the Flatpak one ran:
  the Flatpak service then exited "too many times" and the name led to the native client, host ipc namespace). run.sh
  caught it (namespace check) before any game started. run.sh still checks both: steam's namespace != the host's,
  and `flatpak enter` lands in steam's namespace and sees the test prefix.
- Headless gamescope (`B4B_GPU`): instance.sh gives it `XDG_RUNTIME_DIR=<runtime>/b4b-lane2` (its own Wayland
  connection keeps the desktop socket by absolute path); the game gets `GAMESCOPE_WAYLAND_DISPLAY` as an absolute
  path, `DISPLAY` = gamescope's Xwayland (abstract socket; Steam's sandbox shares the network namespace).
- Result, one instance: agent `status` `steam: id=76561198994546085 p2p=on`; the Flatpak client's
  gameprocess_log `AppID 924970 adding PID 622` (sandbox pid); native gameprocess_log: 0 new `adding PID` lines
  (1328 before and after).
- `B4B_LANE=2 B4B_STEAM=flatpak B4B_GPU=4090 tools/e2e.py --quick`: 10/13 (`/tmp/b4b-e2e-l2-20260926-181741`), both
  instances dreamsofants (presence `steam:76561198994546085`), native gameprocess_log still 1328. The 3 failures are
  the profile checks (burn cards host/client, charged once, profile diff): the lane-2 golden profiles are Hergmgurk's
  (publicId `p64c...`, 15 decks) and his `.sav` doesn't load under another account (`[PlayerProfileSettings] Failed
  to deserialize` → blank profile, SP 73 after the run; the save looks account-keyed). Needs dreamsofants golden
  profiles with burn cards (his own real profile is a fresh 2.4 KB one): not done yet.
- multi.sh: the host-up loop pinged a second time after its successful ping; during the first loading seconds one
  ping can take >25 s (game thread busy, slower first starts in the Flatpak sandbox), so it died with "host agent never
  came up" while the agent was up. It now trusts the loop's result.

## First attempt (2026-09-26 afternoon): NOT isolated, mode refused
Attempt: `launch/run.sh` with `B4B_STEAM=flatpak` ran Proton inside a new sandbox of the Flatpak Steam app,
`flatpak run --parent-pid=<flatpak steam> --parent-share-pids --filesystem=<test prefix> --command=<its Proton>`, under
the host's headless gamescope (`B4B_GPU=4090`). Inside that sandbox `~/.steam/steam.pid` = 99 = the Flatpak `steam`,
`/dev/shm` = the Flatpak per-app shm (`u1000-ValveIPCSharedObj-Steam` of the Flatpak client), native Steam's files are
not visible at all. Still:
- agent `status`: `steam: id=76561198063588550` (Hergmgurk); log `presence: steam bound, user 76561198063588550`.
- native Steam `logs/gameprocess_log.txt`: `AppID 924970 adding PID 958|1080|1380|...|2290 as a tracked process` for
  every attempt (15:24-15:29): Flatpak-namespace PIDs, so the native client registered each test game. Several of those
  numbers are live, unrelated host processes (1080 sshd, 1380 dockerd, 2290 syncthing), so the native client may keep
  showing Back 4 Blood as running until it restarts (don't press its Stop button: it would target those PIDs).
- earlier attempts died in SteamAPI_Init with `src/common/pipes.cpp (900) : fatal stalled cross-thread pipe (pipe is
  disconnected)`: cross-talk between the two clients.
Cause (likely): Flathub's Steam has `shared=ipc;network`: its sandbox uses the host's SysV IPC namespace (same
`/proc/<pid>/ns/ipc` for native `steam`, Flatpak `steam` and the host), and steamclient.so's IPC uses SysV semaphores
(`semget/semop`, next to `shm_open`). Both clients' IPC objects live in one SysV namespace, so a game can reach the
native client even though it can't see its files. The two-account session of 2026-09-24 (steam-p2p.md) worked, but
no longer proves isolation.

run.sh refuses `B4B_STEAM=flatpak` while the Flatpak Steam shares the host's IPC namespace.

## Other findings
- Rendering: a game in the Flatpak sandbox under the host's headless gamescope needs the Flatpak extension
  `org.freedesktop.Platform.VulkanLayer.gamescope//25.08` (installed --user) and gamescope's Wayland socket exposed
  (now: the `b4b-lane2` runtime subdir, see Fix); without them the swap chain fails (`E_INVALIDARG`).
- `steam-runtime-launch-client --bus-name=com.steampowered.PressureVessel.LaunchAlongsideSteam -- <cmd>` (run from any
  sandbox of the app) executes `<cmd>` inside the Flatpak Steam's own sandbox (same user/pid/IPC namespaces as `steam`,
  verified with `readlink /proc/self/ns/*`); env via `--env`, cwd via `--directory`. It sees only the Flatpak home.
  Only while the native Steam isn't running: both clients claim that bus name (see Fix). Superseded by `flatpak enter`.
