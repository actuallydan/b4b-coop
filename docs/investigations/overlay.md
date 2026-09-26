# `~` overlay (#26)

Dear ImGui window over the game (`native/src/overlay.cpp`, API `native/src/overlay.h`), v1.92.9b pinned in
`tools/fetch-deps.sh` (commit + sha256), compiled as C++ objects into every agent flavor (`native/build.sh`; a C++
compile error now fails the build instead of linking a stale object). It replaces the chat commands: every chat
command has a control (checklist below); the chat commands stay.

## How it hooks (nothing is touched before the first open)
- B4B renders with D3D12 (vkd3d-proton under Proton). On the first `~`: a throw-away D3D12 device, queue and swap chain
  on a hidden window give the vtables; MinHook on IDXGISwapChain::Present (vtbl 8), ResizeBuffers (13) and
  ID3D12CommandQueue::ExecuteCommandLists (10). ExecuteCommandLists records the game's direct queue.
- **Threads (changed from the spike):** the ImGui frame is built on the **game thread** (`overlay_tick` ->
  `build_frame`), so panels read UE state and call module code like a chat handler. After `ImGui::Render()` the draw
  lists are cloned (`ImDrawList::CloneOutput`) into a snapshot; Present (RHI thread) renders the snapshot and uploads
  font-atlas changes. The ImGui lock is taken inside each ov_* call only, never while a panel's draw function runs
  (a panel action may make the game thread wait for the render threads: travel, blocking loads).
- Input: the game window's WndProc is subclassed; raw-input mouse deltas move a software cursor, buttons/wheel/keys/
  chars go to ImGui only; key and button releases still reach the game. `cmds_hotkey_down` returns 0 while open (no
  flashlight/third-person key). Key capture for bindings takes the next key down (Esc cancels, Backspace = none,
  mouse middle/4/5 too).

## Panel registry (overlay.h)
- `overlay_add_panel(name, order, draw)` from a module's init; a tab per panel, sorted by order (Session 10, Players 20,
  Camera 30, Flashlight 40, Cheats 50, Settings 90, Help 100; the models branch adds e.g. Models 60, Add-ons 70).
- Widgets: `ov_text/_dim/_warn`, `ov_heading`, `ov_button`, `ov_button_confirm` (second click within 3 s),
  `ov_checkbox`, `ov_radio`, `ov_slider(_int)` + `ov_edit_done`, `ov_input_text/int`, `ov_combo`, `ov_key`, tables,
  `ov_tooltip`, `ov_copy`.
- Permissions: `ov_begin_perm(CMD_HOST|CMD_CHEAT)` ... `ov_end_perm()` greys the block out on a client (or with cheats
  off) and shows the reason (a line, and a tooltip on each disabled control). `ov_allowed()` for single controls.
- Actions: `ov_run("kick #%d", i)` = `admin_slash()` (the chat path: same permission check, same handler, reply in the
  window's log). Settings: `ov_setting(key, val, save)` = the module's ini live handler (`cmds_ini_apply`) and, when
  the edit ends, `cmds_ini_set` for that key only (val NULL = default: the line is commented out). Live ini reload
  keeps the window in sync because panels read the modules' current values every frame.
- The window log shows ov_run replies and every local chat line (`chat.c show_text` -> `overlay_note`).
- Dev: `overlay open|close|status|log|tab <name>|press <label>|set <label> <value>|locate <label>|mouse <x> <y>`
  (press/set drive a control by its ImGui label through the same draw code; disabled controls can't be driven).

## Parity checklist (main's command set)
| Chat verb (perm) | Overlay control |
|---|---|
| `/help` (anyone) | Help tab: version, `/help` button, "Run a chat command" box |
| `/join` (anyone) | Session: Join box (SteamID64 or target) + Join; friend rows: Join (Steam Join Game path) |
| `/host`, `/leave` (anyone) | Session: Host, Leave (confirm) |
| `/players`, `/ping` (anyone) | Players: live table (ping, Steam ID on the host), Ping, List in the log |
| `/flashlight [on\|off\|toggle\|auto\|status]` (anyone; auto host) | Flashlight: status line, Toggle/On/Off, Automatic (greyed on a client) |
| `/thirdperson [on\|off\|status\|distance\|side\|height\|fov\|reset]` (anyone) | Camera: checkbox, sliders (Ctrl+click = number), Swap shoulder, Reset camera, aim correction |
| `/kick`, `/ban` (host) | Players: Kick / Ban (confirm) per row |
| `/unban`, `/bans` (host) | Players: ban list with Unban per row, Unban all (confirm) |
| `/lock`, `/unlock` (host) | Players: Locked checkbox |
| `/teamsize N` (host) | Players: Team size + Apply |
| `/bots on\|off\|default` (host) | Players: radio game default / on / off |
| `/ready [vote]` (host) | Players: Ready everyone, Ready post-round vote |
| `/restart` (host) | Players: Restart mission (confirm) |
| `/say` (host) | Players: message box + Say |
| `/cheats on\|off\|help` (host) | Cheats: Cheats on checkbox, state line; Help: `/cheats help` |
| `/god /heal /revive` (cheat) | Cheats: God on/off, Heal, Revive for the picked player (me / everyone / #n) |
| `/ammo infinite\|off` | Cheats: Infinite reserve ammo checkbox |
| `/copper +N [p]` | Cheats: amount + Give copper |
| `/card <name>\|list [p]` | Cheats: card name box + Give card, List cards (filter = the box) |
| `/fly /noclip /walk` | Cheats: Fly, Noclip (own hero only, greyed otherwise), Walk |
| `/tp [p] <p\|saferoom\|start>` | Cheats: destination combo + Teleport (who = the picker) |
| `/freecam`, `/size <x>` | Cheats: Free camera, size slider (applied on release) |
| `/horde /killall /freeze` | Cheats: Horde, Kill all ridden, Freeze/Unfreeze AI |
| `/director calm\|build\|peak\|fade\|recover` | Cheats: a button per phase |
| `/spawn <type> [n]` | Cheats: type combo + count slider + Spawn |
| `/slomo <x>` | Cheats: game speed slider (on release), Normal speed |
| `/win /lose` | Cheats: Win / Lose the mission (confirm) |
| `/supply +N`, `/unlockall` | Cheats: amount + Add supply points, Unlock all (both confirm); `/unlockall check` via the Help box |
| ini keys | Settings: text size, overlay/flashlight/third-person keys; Camera: `thirdperson*`; Flashlight: sticky; Session: `presence`, `allow_joins`, `allow_steamids` |

Beyond the chat: Steam friends list with Invite (was dev-only `invite`), SteamID Copy.

## Live (2026-09-25, lane 2, `multi.sh 2`)
- Spike (earlier): hooks `Present 00006FFFFCFBAFF0, ResizeBuffers 00006FFFFCFB9560, ExecuteCommandLists
  00006FFFF702AAA0`; renderer `D3D12, 3 buffers, format 24`.
- Game-thread frames: host `frames built=541 drawn=541` in the first seconds; an unfocused instance builds only a few
  frames per second (the game throttles in the background), still correct.
- Driven through each tab (`overlay press/set`), host in Fort Hope: Host ("already hosting"), /players, /ping, Say
  ("[host] hello from the overlay" on both), Lock/Unlock, bots on/default, team size, Ready everyone, third person on,
  Distance 250 + Swap shoulder -> `thirdperson_distance=250`, `thirdperson_side=-40` written; Reset camera -> both
  commented out, camera 180/40; Flashlight Toggle/Automatic; flashlight key K -> `flashlight_key=K` live (status
  `key=0x4b`) and back; Help box `thirdperson status`; Cheats: God refused before Cheats on (disabled), then on, god,
  heal, ammo, off (notices on the client).
- Mission (Evansburgh_B): host Cheats: god on #1, Horde, Spawn hocker, director calm, Kill all ridden (9), off; all
  notices reached the client. Client: Kick, God, Automatic greyed out with the reason (screenshots), Toggle ("requested
  on from host"), third person on. Host Kick on the client's row: `kicked Hergmgurk`, client back in its own camp.
- Real input (SendInput through the focused window): `locate Ping` -> cursor there -> left click 0.05-0.15 s ran
  `/ping` (the spike's missed short click is gone); `~` key opens; typing `h`,`i`,Enter in the Say box sent `/say hi`;
  W held 1 s while open: hero at the same spot, `L` didn't toggle the light; Esc closed; W held 1 s after: moved ~380.
- Live reload: `thirdperson_distance=320` appended to the ini -> slider shows 320, chat/log "applied".
- `B4B_LANE=2 tools/e2e.py --quick --no-lock`: 13/13 PASS incl. the new overlay smoke check (`#1 frames built=232
  drawn=232; #2 frames built=256 drawn=256`), /tmp/b4b-e2e-l2-20260925-163516.
- Not tried: window resize (ResizeBuffers), native Windows, gamepad.

## Dev screenshots of the presented frame (`screenshot <windows path>`)
- Why: engine screenshots (`exec shot`, UE's viewport grab) contain the 3D scene only: no ~ overlay, no UMG, and
  all black on UI-only screens. e2e's `duo-end-*.png` were 370-byte black PNGs (960x540, 1-bit): at `stop()` both
  instances sit on the next chapter's pre-round corruption-card screen (Evansburgh_C) or its loading screen. Sampled
  live every 10 s through the transition: engine mean 0.34 on Evansburgh_B, exactly 0 on Evansburgh_C's pre-round;
  the Present capture showed the "Supply points earned" / "Corruption cards" screens.
- How (overlay.cpp, `#ifndef B4B_RELEASE`): the command installs the Present/ECL hooks if the window was never opened
  and sets a request; the next Present (RHI thread, after the overlay has drawn) copies the current back buffer
  (PRESENT -> COPY_SOURCE -> PRESENT) into a READBACK buffer (`GetCopyableFootprints`, any size) on the game's direct
  queue with its own allocator/list/fence. A worker thread waits for the fence, converts to 8-bit RGB (R8G8B8A8,
  B8G8R8A8/X8, R10G10B10A2 = top 8 bits, R16G16B16A16_FLOAT scRGB -> sRGB, clamped; HDR10/PQ is not decoded) and
  writes an uncompressed PNG (stored deflate blocks, no dependency; `<path>.part`, then renamed). One capture in flight;
  `screenshot` alone prints the last result (`wrote ... (960x540, format 24)`; the game's back buffer is format 24 =
  R10G10B10A2_UNORM).
- `launch/shot.sh` uses it first (path in the prefix's Saved/Screenshots, moved out and resized), then the engine shot,
  then (no `B4B_GPU`) the X11 window grab.
- Live (2026-09-25, lane 2, `B4B_GPU=4090` headless gamescope, `multi.sh 2`): host and client in Fort Hope (HUD
  visible), with the overlay open on the Players tab (both), in Evansburgh_B character select (plus the client's
  Cheats tab, greyed), post-round and pre-round screens; `shot.sh` takes ~0.6 s. `e2e.py --quick --no-lock` 13/13 with
  real `overlay-1/2.png` and `duo-end-1/2.png` (/tmp/b4b-e2e-l2-20260925-203705). Windowed run (no `B4B_GPU`) not
  re-checked: the 5090 was busy.
