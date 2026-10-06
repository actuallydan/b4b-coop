# Online play with b4bcoop installed (#47)

Goal: keep b4bcoop installed and still play the official online game (Easy Anti-Cheat, publisher services), choosing
**b4bcoop co-op** or **Online** without uninstalling. Hard rule: an online (EAC-protected) game process never loads
any b4bcoop code. Status 2026-10-05: implemented as a launch-time choice in the root launcher; live-tested on Proton
(lane 2) up to EAC's launcher being created (suspended, never run) with the agent moved out; not run on Windows.

## 1. Is the official online service still up? (2026-10-05, public sources only, no sign-in)

| Source | What it shows |
|---|---|
| Steam Web API `ISteamUserStats/GetNumberOfCurrentPlayers` (appid 924970) | **841** players in game at ~22:30 UTC (all modes; offline players count too) |
| Steam store (`appdetails`) | still sold (90% off, $5.99), categories still list Online Co-op and Online PvP, no end-of-service notice |
| Steam news (`ISteamNews`) | last game update **May 2024** ("addresses an Easy Anti-Cheat issue for Steam Deck users", EOS SDK update); later items are only WB sales (latest June 2026); no shutdown announcement |
| Steam community discussions | active (new threads Oct 2-5, 2026), one "won't let me play online" thread, no general outage |
| Press (2022-2024) | Turtle Rock ended content development in late 2022 and said the servers stay up |

So the online service appears to be running, with no announced end. Not verified by connecting (by rule: no online
sign-in with either account). The May 2024 update means EAC works under Proton/Steam Deck for the online game.

## 2. The launch chain with b4bcoop installed (before #47)

Steam's only public entry runs the root `Back4Blood.exe` stub (launch.md §1): it `LoadLibraryW("XINPUT1_3.DLL")`s
(our root `xinput1_3.dll` on Windows), then `CreateProcessW("<root>\start_protected_game.exe" Gobi -SaveToUserDir
<launch options>)` and waits for it. Our launcher patched that call:
- **Windows**: start `Gobi\Binaries\Win64\Back4Blood.exe` directly (no EAC); with `-b4bcoop=off` it passed through to
  EAC **but left `X3DAudio1_7.dll` in `Gobi\Binaries\Win64`**: it is a static import of the game, so the
  EAC-protected game would load it (or EAC would block it and the game fail). The old "play online with
  `-b4bcoop=off`" advice put our (idle) proxy into a protected online game.
- **Proton**: Wine's builtin `xinput1_3` wins, the launcher never loads, so **every** Steam start went through
  `start_protected_game.exe` (EAC's Proton runtime) into a game with the agent loaded. The only thing between that
  and an online sign-in was the player picking Offline at the sign-in popup (netguard blocked the services if they
  picked Online). `-b4bcoop=off` on Proton = the same proxy in an EAC game.

What EAC would see in an online game: every module of the game process (EAC's client checks loaded modules against
its catalog; an unsigned proxy DLL in the game's own folder is exactly what it reports or blocks). The root stub and
anything else outside the protected process are not part of that. Files that nothing loads are not looked at (EAC
checks the game's own files, not extra files in the folder).

Who loads what (static analysis, `pefile`/strings of this build):
| Process | Loads from the game folder | Our files it could load |
|---|---|---|
| root `Back4Blood.exe` (stub, not protected) | `XINPUT1_3.DLL`, `MSVCP140.DLL`, `ucrtbase.dll` by bare name (app dir first) | `xinput1_3.dll` (that is the launcher) |
| `start_protected_game.exe` (EAC bootstrapper, root dir) | static: ADVAPI32, CRYPT32, GDI32, IMM32, KERNEL32, OLEAUT32, SETUPAPI, SHELL32, USER32, VERSION, WINMM, WS2_32, ole32; SDL loads `XInput1_4.dll` first | none (xinput1_3 only if XInput1_4 is missing: Windows 7, unsupported by the game) |
| `Gobi\Binaries\Win64\Back4Blood.exe` (the protected game) | static `X3DAudio1_7.dll`, `dwmapi.dll`, `XINPUT1_4.dll`, ... (app dir = Win64 first) | `X3DAudio1_7.dll`, `dwmapi.dll` (in Win64); never `xinput1_3` (no import, no string) |

## 3. Options

| | How | Verdict |
|---|---|---|
| (a) in-game switch + restart | Online at the sign-in popup → agent writes "next start: online", restarts through the protected path with our DLLs moved, restores later | **Rejected by Dan** (no re-launching the game). Also: the restart would be started by a process that has our agent loaded, and the restore needs a watcher. |
| (b) **launch-time choice in the root launcher** | the stub (unprotected) asks before EAC starts; Online moves the agent DLLs out of Win64 and lets the stub start EAC unchanged; the next co-op pick moves them back | **Chosen** |
| (c) launch option only (`-b4bcoop=off` with the files moved) | same mechanism without a prompt | kept as part of (b) (`-b4bcoop=off|coop|ask`) |
| other DLL names in the stub (msvcp140/ucrtbase proxies, so Proton needs no override) | the stub loads them by bare name; Wine prefers native for msvcp140 in this prefix | rejected: `start_protected_game.exe` also lives in the root dir (its app dir), a fake C++ runtime there could end up in EAC's own bootstrapper |
| a separate launcher exe / script | | no scripts wanted, and Steam has no second launch entry (launch.md §1) |

## 4. Design (implemented)

Launcher `native/launcher/redirect.c` (root `xinput1_3.dll`, loaded by the stub only), at the stub's EAC
`CreateProcessW`:
1. Nothing installed (no agent in Win64, none switched off) or no game exe: pass-through to EAC, no prompt (as before).
2. The choice, first match: `+b4bcoop_join` (Steam Join Game/invite) = co-op; launch option `-b4bcoop=coop` /
   `-b4bcoop=off` or `=online`; ini `launch=coop|online` in `b4bcoop.ini` (the remembered choice), unless **Shift** is
   held or `-b4bcoop=ask`; else the prompt.
3. Prompt: a task dialog (comctl32 v6 through our own manifest, `native/launcher/launcher.rc`, activated only around
   the dialog): "How do you want to play?" with command links **b4bcoop co-op** / **Online**, "Remember my choice"
   (writes `launch=` like the agent's ini writer), footer how to get the question back, controller support (XInput
   1.4 polled on the dialog's timer: A co-op, Y online, X remember, B close). Closing it = nothing starts
   (`ExitProcess(0)` in the stub: no second error box from the stub). Fallback without task dialogs: a Yes/No/Cancel
   message box. With no input at all for 15 s it picks **co-op** (the safe default; the content line counts down
   "Starting b4bcoop co-op in N s..."); any key, mouse move (> 4 px) or controller button stops the countdown. Not
   `GetLastInputInfo`: under Wine a focus change or a screen grab counts as input there (seen: the countdown stopped
   at the moment of a screenshot).
4. **Online**: move `Gobi\Binaries\Win64\X3DAudio1_7.dll` and `dwmapi.dll` (renames, same volume; works while a file is
   mapped) to `<root>\b4bcoop-online\Gobi\Binaries\Win64\` + a `README.txt` there; **verify neither is left in Win64**,
   else put back, show why and start nothing; copy the offline save (`PlayerProfileSettings.sav/.json` in
   `%LOCALAPPDATA%\Back4Blood\Steam\Saved\SaveGames`) to `PlayerProfileSettings-b4bcoop-before-online-<stamp>.*` (5
   kept): an Online sign-in compares the save's `publicId` with the online account and may reset it
   (test-profiles.md), so a player's offline progress gets a copy before every online start; then the stub's own
   `CreateProcessW` of EAC runs unchanged.
5. **Co-op**: move switched-off files back (a copy already back in place, e.g. a re-extracted zip, wins and the
   switched-off one is dropped); `b4bcoop-online` is removed when empty; then the direct start as before. Any failure:
   a message and nothing starts (never an EAC start by surprise).
6. Log `Gobi\Binaries\Win64\b4bcoop-launcher.log`: `agent: N file(s) in place, M switched off`, `choice: online (the
   prompt)`, `switched off: ...`, `switched on: ...`.

The root `xinput1_3.dll` stays during an online game: it is the only thing that can bring the question back, and only
the unprotected stub loads it (§2 table). Residual risk: a process with the game root as its current directory
loading `xinput1_3.dll` by bare name on a PC without the DirectX runtime's System32 copy (System32 comes before the
current directory); none of the three processes above does.

**Proton/Steam Deck**: the launcher needs Wine to prefer the native `xinput1_3`. The agent (`native/src/online.c`,
`online_early` in DllMain) sets `HKCU\Software\Wine\AppDefaults\Back4Blood.exe\DllOverrides` `xinput1_3 =
native,builtin` in the game's prefix (only when our launcher is next to the root stub). Per-exe: the stub and the game
share the name `Back4Blood.exe`; the game doesn't use xinput1_3 at all, `start_protected_game.exe` is unaffected.
The first start after installing (through EAC's launcher, as before) sets it; from the second start Proton gets the
same prompt, and co-op starts skip EAC's launcher like Windows. Removing b4bcoop leaves the value behind; harmless (no
native file in the root = Wine falls back to its builtin).

**Co-op starts skip the game's sign-in** (Dan, follow-up): every agent start is a co-op start (an Online start never
loads the agent), so auto sign-in Offline (signin.c, before only for Steam joins and dev `offline=1`) is now the
default in every build: the agent presses Sign in on the title screen (StartSignIn, ~0.1 s after the screen opens),
and the Online/Offline question is answered **before its popup exists**: hook on the task's start
`SignInTask_OnlineOfflinePopup` 0x141B43960 (it creates the popup widget and logs `[%d] prompting online/offline`;
an earlier startup-options choice answers at once instead). When the task is Running the hook calls the game's own
handler `OnPopupClosed(task, NULL, "Offline")` (the popup argument is unused there) and returns without creating the
popup: `SetOnlineModeForSignIn(Offline)`, task Completed. ini `auto_signin=0` (dev alias `offline=0`) restores the
game's own sign-in (then the OnPopupClosed guard turns Online into Offline). Online starts can't be changed (no
b4bcoop code there): retail's sign-in as is.

Agent safety nets (`online.c`):
- `-b4bcoop=off|online` on the game's command line with the agent loaded (Proton's first start with that launch
  option, or the launcher missing): the agent shows a message and `TerminateProcess` from DllMain, before any game
  code runs, instead of letting that game go online with it.
- The game's own Online/Offline sign-in popup: `SignInTask_OnlineOfflinePopup::OnPopupClosed` (0x141B43CA0; "Offline"
  → `SetOnlineMode(Offline)` 0x141B39F90, anything else stays Online) is hooked; with the agent loaded every answer
  becomes "Offline", and a chat line after the map load says to pick Online at the start.

Other parts:
- **netguard, updater, shop** are agent code: absent from an online game. The updater installs/reverts only while the
  agent runs (files in place); a pending update's start count simply waits for the next co-op start.
- **Add-ons**: `b4bcoop-addons\*.pak` are mounted only by the agent (paks.c); retail mounts `Content/Paks` of the
  project/engine (and needs signed paks). Nothing to move.
- **Steam "Verify integrity"**: our files are extra files, never touched; `b4bcoop-online\` too.
- **EasyAntiCheat\Settings.json**: the stub rewrites `"executable"` every start, as before (unchanged content).
- `uninstall.sh`, README "Uninstall" and `lane-restore.sh` cover `b4bcoop-online\`.
- ini `launch=ask|coop|online` (read by the launcher, written by "Remember my choice"); `~` window Settings "Game
  start" (Ask every time / Always b4bcoop co-op / Always online); no chat command.

## 5. Tests

Unit-ish (system Wine 11.12 + Xvfb, no game): `rundll32 xinput1_3.dll,b4bcoop_prompt_test` (dev export) renders the
task dialog with command links, the checkbox and footer, and the dev auto-answers work (§6).

Live, lane 2 (Flatpak Steam, dreamsofants, `B4B_GPU=4090`, dev build): `tools/online-test.py`. EAC's launcher is
always created **suspended** in tests (dev `-b4bcoop_test_suspend`, and any run with `B4B_LAUNCHER_ANSWER` set):
it never executes, so no game and no online service can be reached; the test then reads its `/proc/<pid>/maps` and
kills it. Results: see §6.


## 6. Results (2026-10-05, lane 2, Proton Experimental in the Flatpak Steam sandbox, dev build)

`tools/online-test.py`: **43/43 PASS** (`/tmp/b4b-online-20261005-235726`; the 41/41 run before the auto sign-in /
countdown follow-up: `/tmp/b4b-online-20261005-225641`):
1. Test prefix without the override, `auto_signin=0`; first start (game exe directly, like Proton's first Steam start): `online:
   Wine: xinput1_3=native,builtin for Back4Blood.exe`; dev `signin online` pressed **Online** at the game's popup:
   `the sign-in popup answered "Online"; b4bcoop is running, so this game signs in Offline`, engine log `[0]
   online/offline prompt closed with response Offline`, signed in, netguard: EOS network disabled, nothing allowed
   but loopback. The value is in the prefix's `user.reg` afterwards.
2. Root stub (`B4B_STUB=1`, no `WINEDLLOVERRIDES` for xinput): the launcher loads through the registry override alone
   (`hooked CreateProcessW`); nobody answers the prompt: `prompt: no input for 15 s: b4bcoop co-op` → `redirect (no
   EAC)`, agent loaded, no `start_protected_game.exe` at any time. Default auto sign-in, the question never shown:
   ```
   23:59:29.442 Created screen 'SignInScreen'.
   23:59:29.548 signin: StartSignIn on 000000008C51A8B0
   23:59:29.563 SignInTask SignInTask_OnlineOfflinePopup_2147480755 Running
   23:59:29.563 signin: Online/Offline question answered Offline before it is shown (co-op start)
   23:59:29.563 [0] online/offline prompt closed with response Offline
   23:59:29.564 SetOnlineModeForSignIn(Offline) set online mode to Offline
   23:59:29.564 SignInTask SignInTask_OnlineOfflinePopup_2147480755 Completed
   ```
   and no `[0] prompting online/offline` (the line the game logs when it creates the popup; present in every earlier
   log).  `~` window Settings "Always b4bcoop co-op" writes `launch=coop`, "Ask every time" comments it out.
3. Prompt → Online + remember:
   ```
   agent: 1 file(s) in place, 0 switched off (b4bcoop-online)
   prompt: dev auto-answer online+remember
   remembered: launch=online in Z:\...\prefixes\lane2\test1\b4bcoop.ini
   choice: online (the prompt)
   switched off: Gobi\Binaries\Win64\X3DAudio1_7.dll -> b4bcoop-online\Gobi\Binaries\Win64\X3DAudio1_7.dll
   test: starting Easy Anti-Cheat's launcher suspended
   online: b4bcoop is switched off; starting Easy Anti-Cheat unchanged
   started pid 412
   ```
   No `X3DAudio1_7.dll`/`dwmapi.dll` left in Win64, the same file (sha256) in `b4bcoop-online`, its README; the
   test prefix's offline save copied to `PlayerProfileSettings-b4bcoop-before-online-<stamp>.sav` (same sha256).
   `start_protected_game.exe Gobi -SaveToUserDir -log -b4bcoop_test_suspend` exists (suspended), its 16 mapped files
   are the exe, Wine's loader/ntdll and libc: nothing of ours (the stub, checked the same way, maps the launcher, so
   the check sees our files when they are there). No game process. Killed; still switched off afterwards.
4. Remembered Online: `choice: online (b4bcoop.ini launch=)`, no prompt, EAC's launcher suspended again. Then
   `+b4bcoop_join steam:<own id>`: `choice: b4bcoop co-op (a Steam join)`, `switched on: ...`, same sha256 back in
   Win64, `b4bcoop-online` gone, agent loaded.
5. `-b4bcoop=ask` with `launch=online`: `but -b4bcoop=ask: asking`; cancel: `nothing started`, the stub exits, nothing
   moved.
6. `-b4bcoop=off` on the game exe with the agent loaded: `closing the game before it starts`, the process gone, no
   `init: tick hooked`.

The prompt alone under system Wine 11.12 + Xvfb (`rundll32 xinput1_3.dll,b4bcoop_prompt_test`): the task dialog with
both command links, "Remember my choice" and the footer; auto-answers online+remember → (2, 1), coop → (1, 0),
cancel → (4, 0); `wait` (no answer): the content counts down ("Starting b4bcoop co-op in 13 s...", screenshot
`/tmp/b4b-online-prompt/prompt-countdown.png`) and returns co-op (1, 0) at 15 s. Not tested: input stopping the
countdown (no input device in the headless runs).

`tools/e2e.py --quick` on lane 2: 14/14, twice before the follow-up (`/tmp/b4b-e2e-l2-20261005-223918`, `-225354`)
and with it (`/tmp/b4b-e2e-l2-20261006-000100`: both instances `answered Offline before it is shown`, no `prompting
online/offline`).
Release build: no test hooks in `xinput1_3.dll` (no `-b4bcoop_test_suspend`, `B4B_LAUNCHER_ANSWER`, prompt export),
reproducible (two builds, same sha256).
`native/test/run.sh`: 88 + 32 checks pass.

Safety of the tests: EAC's launcher was never resumed (created with `CREATE_SUSPENDED`; in dev builds every unattended
run, `B4B_LAUNCHER_ANSWER` set, forces it), so no EAC code, no protected game and no publisher service ran; the only
online sign-in attempt (step 1) was turned into Offline by the hook before the game's handler ran (the dev command
refuses to press Online without that hook).

## 7. Not tested / open
- Input stopping the 15 s countdown (keys, mouse, controller); Windows' task dialog redrawing the countdown line.
- **Native Windows**: the prompt, the renames, a real EAC start with the agent moved out, Defender/Smart App Control
  on the launcher, Shift detection while Steam starts the game. Test: install the zip, Play → Online → Task Manager:
  `Back4Blood.exe` (Win64) has no `X3DAudio1_7.dll` from the game folder in its modules (Process Explorer / `tasklist
  /m X3DAudio1_7.dll` shows the System32 one), the EAC splash appears; quit, Play → co-op → b4bcoop log as usual.
- A real online session (not allowed in testing): whether the online game behaves normally with the extra (unloaded)
  files around. Expected yes: they are not loaded.
- Steam-started Proton (the registry override in Steam's own prefix for the game, not a test prefix), Steam Deck
  Game Mode (dialog focus, controller input to the dialog).
- Shift held at the start (no keyboard in the headless test).

## 8. Open questions
- Does a real online sign-in leave the local offline progress alone? test-profiles.md saw a save rejected and reset
  when the saved `publicId` didn't match the sign-in's id; a save written by offline/b4bcoop play may carry
  `offline.<steamid64>`. Not testable without going online; the launcher's save backup is the safety net.
- Proton: is it acceptable that b4bcoop writes the `AppDefaults\Back4Blood.exe` DllOverride into the game's prefix
  (and that co-op starts on Proton then skip EAC's launcher, like Windows)? The alternative is a launch option
  (`WINEDLLOVERRIDES="xinput1_3=n,b" %command%`) for Linux players who want the question.
- Online sessions keep the root `xinput1_3.dll` in place (the question needs it). Fine with §2's analysis, or should
  Online also move it (then nothing brings the question back automatically; co-op only by moving files back by hand)?
