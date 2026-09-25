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
  Camera 30, Flashlight 40, Cheats 50, Models 60 (models.c, models branch), Add-ons 70 (addons.c, models branch), Settings 90,
  Help 100).
- Widgets: `ov_text/_dim/_warn`, `ov_heading`, `ov_button`, `ov_button_confirm` (second click within 3 s),
  `ov_checkbox`, `ov_radio`, `ov_selectable` (list row), `ov_slider(_int)` + `ov_edit_done`, `ov_input_text/int`, `ov_combo`, `ov_key`, tables,
  `ov_tooltip`, `ov_copy`.
- Permissions: `ov_begin_perm(CMD_HOST|CMD_CHEAT)` ... `ov_end_perm()` greys the block out on a client (or with cheats
  off) and shows the reason (a line, and a tooltip on each disabled control). `ov_allowed()` for single controls.
- Actions: `ov_run("kick #%d", i)` = `admin_slash()` (the chat path: same permission check, same handler, reply in the
  window's log). Settings: `ov_setting(key, val, save)` = the module's ini live handler (`cmds_ini_apply`) and, when
  the edit ends, `cmds_ini_set` for that key only (val NULL = default: the line is commented out). Live ini reload
  keeps the window in sync because panels read the modules' current values every frame.
- The window log shows ov_run replies and every local chat line (`chat.c show_text` -> `overlay_note`).
- Dev: `overlay open|close|status|log|tab <name>|press <label>|set <label> <value>|locate <label>|mouse <x> <y>|wheel <n>`
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
| `/addons [list]`, `/addons info <#>` (anyone) | Add-ons: table in load order (on, #, title + state / "not loaded: why", kind), conflicts; click a row = details (author, description, content class + first gameplay file, id Copy, outfits it adds, its conflicts, `/addons info` button); folder path Copy; "Load add-ons" (`addons`, restart) |
| `/addons on\|off <#>` (anyone) | Add-ons: the row's checkbox (runs `/addons on\|off <file>`); beyond the chat: Up/Down (load order, `addons_move`), banner while `addonlist.txt` differs from what is mounted; hand edits of the file are re-read |
| `/addons players`, `/addons policy` (host) | Add-ons: policy radios (`addons_policy`, saved + live via `addons_live`), players' summaries table, `/addons players` button; greyed on a client |
| `/model`, `/model list [<survivor>\|npc\|outfits\|weapons]` (anyone) | Models: "Your look" (now, your pick, host refusal), `/model` button; lists Survivors (header per survivor: whole survivor + outfits/heads/torsos/legs) / NPC bodies (grouped) / Add-on outfits (by add-on), search box; Weapon looks per weapon type with the add-on |
| `/model <name>`, `/model reset` (anyone) | Models: click a name (runs `/model <name>`), Use per weapon look, Reset my look; beyond the chat: Reset per weapon type (`wlooks_reset_code`) |
| `/model <player> <name>\|reset` (host) | Models: "Change the look of" combo (then click a look), Everyone's look table: Change / Reset per row (greyed on a client) |
| `/models [on\|off]` (host) | Models: "Model swaps allowed" checkbox (client: greyed, shows the host's last announced state), `/models` button, add-on policy line |
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

## Models tab (models.c `models_panel`, 2026-09-25, lane 2, `multi.sh 2`, `casual_joe.pak` + `mod_ak47.pak` via `addons_dir=`)
- Driven with `overlay press/set`: search `walker_elite` + click `walker_elite_03` -> `/model walker_elite_03` applied;
  NPC bodies `emmett`, Add-on outfits `casual_joe`, `Use##ak47` (AR02 given with `giveitem`: FP/3P meshes from
  `/Game/b4bcoop/weapons/ak47/`), whole survivor `walker` -> `walker_elite_00`; per-type `Reset##w:AR02` -> row back,
  FP overrides 10.
- Host target combo `#1` + click `karlee_elite_05` -> `/model #1 karlee_elite_05` (client's slot shows it, notice on
  the client); Everyone's look `Reset##1` -> `/model #1 reset`.
- `Model swaps allowed` off -> `/models off`, 3 looks reset; the client's tab shows the refusal line and, in its greyed
  Host section, the announced state off. Client: `casual_joe` and `Use##ak47` from its own tab applied.
- `tools/e2e.py --quick` (lane 2): 14/14, /tmp/b4b-e2e-l2-20260925-180035. Screenshots:
  `~/.local/share/b4b-coop/models-tab/shots/` (not committed).
- Combo labels with spaces need quotes for the dev driver: `overlay set '"Change the look of##target"' "#1"`.
