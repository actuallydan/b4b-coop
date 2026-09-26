# Test instances on the Flatpak Steam client (B4B_STEAM=flatpak)

Goal: while Dan plays on his native Steam account (Hergmgurk, 76561198063588550), test instances talk only to the
Flatpak Steam client (account dreamsofants, 76561198994546085, `~/.var/app/com.valvesoftware.Steam`, lane 2's game).

## Result (2026-09-26): NOT isolated, mode refused
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

run.sh now refuses `B4B_STEAM=flatpak` while the Flatpak Steam shares the host's IPC namespace.

## Other findings
- Rendering: a game in the Flatpak sandbox under the host's headless gamescope needs the Flatpak extension
  `org.freedesktop.Platform.VulkanLayer.gamescope//25.08` (installed --user) and gamescope's Wayland socket exposed
  (`--filesystem=xdg-run/$GAMESCOPE_WAYLAND_DISPLAY`); without them the swap chain fails (`E_INVALIDARG`).
- `steam-runtime-launch-client --bus-name=com.steampowered.PressureVessel.LaunchAlongsideSteam -- <cmd>` (run from any
  sandbox of the app) executes `<cmd>` inside the Flatpak Steam's own sandbox (same user/pid/IPC namespaces as `steam`,
  verified with `readlink /proc/self/ns/*`); env via `--env`, cwd via `--directory`. It sees only the Flatpak home.

## Next (needs a Flatpak Steam restart, i.e. Dan's OK)
1. Start the Flatpak Steam with its own IPC namespace: `flatpak run --unshare=ipc com.valvesoftware.Steam -silent`
   (or `flatpak override --user --unshare=ipc com.valvesoftware.Steam`), plus the test prefixes in its view
   (`--filesystem=~/.local/share/b4b-coop/prefixes/lane2`, or move them under its home).
2. Launch the game inside that sandbox through LaunchAlongsideSteam (a new `flatpak run` sandbox gets a new IPC
   namespace, not Steam's).
3. Prove it with one instance BEFORE any e2e: agent `status` must show 76561198994546085, and native
   `gameprocess_log.txt` must log nothing new.
