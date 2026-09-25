# `~` overlay (#26, spike)

Dear ImGui window over the game (`native/src/overlay.cpp`), v1.92.9b pinned in `tools/fetch-deps.sh` (commit + sha256),
compiled as C++ objects into every agent flavor (`native/build.sh`). Design notes are in the file header.

## How it hooks (nothing is touched before the first open)
- B4B renders with D3D12 (vkd3d-proton under Proton). On the first `~`: a throw-away D3D12 device, queue and swap chain
  on a hidden window give the vtables; MinHook on IDXGISwapChain::Present (vtbl 8), ResizeBuffers (13) and
  ID3D12CommandQueue::ExecuteCommandLists (10). ExecuteCommandLists records the game's direct queue; Present, while
  open, records ImGui's draw lists onto the current back buffer and submits them on that queue.
- Input: the game window's WndProc is subclassed; raw-input mouse deltas move a software cursor (the game hides and
  clips the real one), buttons/wheel/keys/chars go to ImGui only; key and button releases still reach the game.
- Settings: the window edits a copy of the /thirdperson settings (`thirdperson_get/apply`, applied on the game thread
  in `overlay_tick`); **Save to b4bcoop.ini** writes them with `cmds_ini_set`.

## Live (2026-09-25, lane 2, `multi.sh 2`, host in a mission)
- First open, log: `overlay: hooks installed (Present 00006FFFFCFBAFF0, ResizeBuffers 00006FFFFCFB9560,
  ExecuteCommandLists 00006FFFF702AAA0)`, `game's direct queue 00000000030D21C0`, `renderer ready (D3D12, 3 buffers,
  format 24, ...)`, `input hooked on window ...`. Draws translucent over the game (screenshots), no crash in ~5 min of
  open/close with the game running.
- Controls drive thirdperson.c live: Reset (clicked through SendInput from a Windows Python in the prefix) took the
  camera from 109 / -56 / 4 / FOV 60 to 180 / 40 / 0 / game's (`thirdperson status`, `thirdperson aim`: 180.0 along,
  40.0 off). Earlier slider drags and Swap shoulder (real mouse input that reached the focused window) changed and
  applied the camera the same way.
- Input captured while open: raw mouse moves the overlay cursor but not the view (control rotation unchanged); `S`
  held 0.6 s: hero didn't move while open, moved 160 units closed; `N` (3P key) ignored while open. `~` and `Esc` close.
- A 0.1 s SendInput click from another process didn't register (2 s hold did); real clicks did. Maybe down+up land in
  one ImGui frame at low frame rate: watch for missed clicks.
- Not tried: Save to b4bcoop.ini from the window, window resize (ResizeBuffers path), native Windows, the client side.
