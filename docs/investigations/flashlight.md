# Manual flashlight toggle (issue #2)

Build 14216215. Static analysis only (SDK dump + `Back4Blood.exe` disassembly); not yet tested live.

## Verdict
**Possible for host and clients, and visible to everyone.** The game ships an unused server RPC that toggles the
light, and the light state is a replicated property. Implemented in `native/src/flashlight.c`.

## How the survivor flashlight works
`UHeroLightComponent` (ActorComponent on every `HeroCharacter`, size 0x1D0):

| offset | field | notes |
|---|---|---|
| 0xE8  | `OnHeroLightStatusChanged` | multicast; `HeroAudioComponent::HeroLightStatusChanged` plays the on/off click |
| 0xF8  | `bLightVisibilityRequested` | **Replicated**, `OnRep_LightVisibilityRequested`; plain bool (not bitfield) |
| 0xF9  | `bCastsThirdPersonShadowsRequested` | Replicated, `OnRep_CastsThirdPersonShadowsRequested` |
| 0x178 | `TArray<FFlashlightEnableRequest>` (not reflected) | `{u8 bEnable, u8 bShadows, AActor* Requestor @8, i32 Priority @0x10}`, sorted by Priority, last = active |
| 0x188 | dark-card `FGameplayTag` (not reflected) | |
| 0x190 | bDarkCardActive (not reflected) | light forced on; volumes ignored while set |
| 0x1B8 | `FlashLightComponents` | the SpotLightComponents that actually render |

Key native functions:
- `0x141BF2090` **SetLightState(this, bool visible, bool shadows)**: no-op if cvar `fl.ForceDisabled` is set. If changed,
  writes 0xF8/0xF9, calls `0x141BF3400` (applies `SetVisibility` to each spotlight in 0x1B8, plus shadows), then
  broadcasts `OnHeroLightStatusChanged`.
- `0x141BF2270` = `ServerUpdateLightVisibilityRequested_Implementation` (vtable 0x1454F1400, slot +0x420):
  `SetLightState(!bLightVisibilityRequested, shadows)`. The `_Validate` (+0x418) always returns true.
  `0x141BF2290` is the same for shadows (`ServerUpdateCastsThirdPersonShadowsRequested`, +0x410).
- `OnRep_LightVisibilityRequested` (exec `0x142164A80`) re-runs SetLightState with the replicated value on every
  non-authority machine.
- `0x141BF1B40` (init/possess): initial state = dark-card tag count > 0, so the light starts **off** normally.

What turns it on/off automatically (authority only):
- **`AFlashlightVolume`** trigger volumes (`bEnableFlashlight`, `bAllowThirdPersonShadows`, `Priority`, dialogue
  fields). `OnOverlapBegin` (exec `0x142066B20`) runs only when the hero's `Role == ROLE_Authority` (Actor +0x120).
  It pushes a request (sorted by Priority) and calls SetLightState only if the new request is on top.
  `OnOverlapEnd` (`0x1420667A0`) removes it and applies the new top, or turns the light off when the stack is empty.
  So it's **edge-triggered**: nothing polls light levels, and nothing re-asserts state between volume transitions.
- **Dark card**: `OnDarkCardChanged(Tag, Count)` forces the light on while the tag count is > 0.
- There's no light sensing, anim or GAS effect that drives visibility. `GameplayEffectHeroLightComponent` only changes
  light parameters (FP/TP `FlashlightViewConfig`), not on/off.

**No manual toggle exists natively.** The UHT stub that would call `ServerUpdateLightVisibilityRequested` was
stripped: its FName global `0x14690D200` is only referenced by its own initializer. The RPC itself is still
registered, and its exec thunk and implementation are intact.

## Authority model / why the RPC is the right path
- State lives on the server and replicates to all (`bLightVisibilityRequested`, OnRep applies it). A client-only write
  to 0xF8 would be local-only and overwritten by the next replication, and other players would never see it.
- `ServerUpdateLightVisibilityRequested` is `Net | NetServer`, **unreliable** (no `NetReliable`). It takes no parameters
  and toggles on the server. Calling it with ProcessEvent on the local hero's component:
  - host / offline: callspace Local → runs the implementation directly → replicates to clients;
  - client: the hero is owned by the client's PlayerController → sent to the host → toggled there → replicates back.
  Both sides run the same binary, so the RPC's net index matches.
- The host doesn't need to cooperate for the basic toggle. Only sticky mode (below) needs the host's DLL.

## Implementation (`native/src/flashlight.c`)
- Agent command `flashlight [on|off|toggle|auto|status]` (e.g. `tools/b4b.py flashlight toggle`). on/off read
  0xF8 and send the toggle RPC only if the state differs.
- Hotkey: `flashlight_key` in `b4bcoop.ini` (letter/digit or VK code, e.g. `0x4C`; `0` disables). Default **L**.
  Polled with GetAsyncKeyState in `cmds_tick`, only while the game window has focus, with a 250 ms debounce.
- **Sticky mode** (`flashlight_sticky=1`, default; only the host's setting matters): hooks SetLightState and
  the toggle implementation. A hero toggled by hand is marked manual, and SetLightState calls whose return address
  is one of the two FlashlightVolume call sites (`0x142066AF3`, `0x142066ED6`) are skipped for that hero only.
  The request stack still updates, so `flashlight auto` (host, own hero) re-applies the volume/dark-card state.
  The override ends when the component is destroyed (map/chapter change). Dark-card transitions aren't blocked.
- Shared-file edits: `cmds.h` (3 decls), `cmds.c` (dispatch + tick), `main.c` (`flashlight_init()`).

## Caveats / open questions
- Unreliable RPC: a toggle can be lost under packet loss. Press again.
- Hotkey fires while typing in chat (no chat-focus detection). L may already be bound in someone's layout; change it in the ini.
- A client can't send `auto`. Its override lasts until the next map.
- `bCastsThirdPersonShadowsRequested` isn't touched: the manual state keeps the current shadow flag.
- Not verified: whether the third-person light of *other* heroes renders on remote machines outside volumes. It should,
  since OnRep applies it to FlashLightComponents on every machine.

## Phase 2 live test plan
Two local instances (`launch/two.sh`), host + client in a mission, both heroes in view:
1. `tools/b4b.py flashlight` on both: expect `light=off role=authority` (host) / `role=client` (client), `hooks=1`.
2. Client: `B4B_AGENT=1 tools/b4b.py flashlight on` → client log has no errors; host log has `flashlight: toggled -> 1`.
   Client `flashlight` status → `light=on` after replication. Visually: client's own light on; host sees the client
   hero's light and hears the click.
3. Client: press **L** in the client window → off on both. Press L in the host window → only the host's hero toggles.
4. Sticky: with the client's light manually off, walk the client through a FlashlightVolume area (a dark interior).
   Expect host log `kept manual state 0, ignored volume request 1` and light still off on both.
   Host: `flashlight auto` on its own hero after a manual toggle → light follows the volume again.
5. Regression: without any toggles, the automatic behavior is unchanged (`volume_requests` count changes, light follows).
6. Offline solo: `flashlight toggle` works with no NetDriver.
