# Manual flashlight toggle (issue #2)

Build 14216215. Static analysis (SDK dump + `Back4Blood.exe` disassembly); verified live 2026-09-24 (see "Phase 2 live results").

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
Two local instances (`launch/two.sh`, since replaced by `launch/multi.sh`), host + client in a mission, both heroes in view:
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

## Phase 2 live results (2026-09-24)

Local instances (`launch/multi.sh`, one Steam account), Evansburgh_B on Easy, match in progress. Host = instance 1,
client = instance 2 (`B4B_AGENT=1`); three more clients were connected (the session was the 5-player slot-guard run),
which also gave a third machine to check replication on. New helpers: `flashlight list` (every hero light as this
machine sees it) and `tp` in `testing.c` (teleport a hero into a `FlashlightVolume`, host).

| Step | Result |
|---|---|
| Init | Every instance logs `flashlight: key=0x4c sticky=1` and `flashlight: hooks installed`. |
| 1. `flashlight status` | Host `light=off role=authority ... hooks=1 key=0x4c`, client `light=off role=client ... hooks=1`. |
| 2. Client `flashlight on` | Client: `requested on from host`. Host log `flashlight: toggled -> 1 (manual, sticky)`. Client status `light=on` about a second later. Host `flashlight list`: the client's hero `light=on role=authority manual=yes`. A third client (instance 3) sees that hero `light=on role=proxy`: the replicated `bLightVisibilityRequested` reached the other machines. Visible in the client's view (`flashlight/client-on-off.jpg`, lit saferoom, subtle). |
| 3. Client `flashlight off` | Host `toggled -> 0`, client `light=off`. |
| 4. Host `flashlight toggle` | Host `flashlight: on`, `toggled -> 1`; the client lists the host's hero `light=on role=proxy`. |
| 5a. Sticky, host's own hero | Host `flashlight off` (manual), `tp 0 volume 0` (dark interior, `bEnableFlashlight=1`): `kept manual state 0, ignored volume request 1`, `volume_requests=1`, light stays off. `flashlight auto`: `automatic again, light=on` and the hero says "Lights on." (`flashlight/host-auto-in-volume.jpg`). |
| 5b. Sticky, client's hero | Client light manually off, host `tp 3 volume 2`: `kept manual state 0, ignored volume request 1`, light stays off on both. Client `flashlight on` inside the volume: on (`flashlight/client-sticky-in-volume.jpg`, off then on). Teleported back to the saferoom: `kept manual state 1, ignored volume request 0`, light stays on. |
| 6. Regression (no manual toggle) | A hero never toggled by hand, teleported into a volume: `volume_requests=1`, light turned on by the volume as before. |

Not tested: the L hotkey (can't be pressed unattended; only its init line is checked), offline solo, packet loss.

Notes:
- The client's `flashlight status` always shows `manual=no volume_requests=0`: the manual table and the request stack
  exist on the host only. Use the host's `flashlight list` to see them.
- No bugs found in `flashlight.c`.

## Beam width, range and brightness (issue #29)

Static analysis 2026-09-27 (build 14216215); live results below.

### Where the beam lives
- What renders is `UHeroLightComponent.FlashLightComponents` (+0x1B8, `TArray<USpotLightComponent*>`). They are **not**
  replicated: every machine creates them itself from the component's `FlashLightBPs[6]` (+0x100, one Blueprint class
  per `EFlashlightMode`: FirstPerson/ThirdPerson × Epic/Low/SplitScreen). The Blueprint's spot lights are the
  templates: `0x141BF2110` picks the class for a mode, `0x141C127C0` lists its components, `NewObject` copies each one
  onto the hero (`0x141BF29E5` / `0x141BF2D59`), attached to the FP (+0x140) or TP (+0x150) parent component.
- **Rebuild** `0x141BF26A0`: mode = TP when `PlayerViewComponent+0x215` (IsThirdPerson) is set, else FP; Epic vs Low
  from two globals, split screen separately. If the mode differs from `+0x170` (last built mode), it destroys the old
  spotlights (vtable +0x3B8), clears the array and makes new ones. Called by `OnViewChanged` (exec `0x142164950`, sets
  `+0x191` bThirdPerson then rebuild + `0x141BF3400` visibility), by the tick when `r.HDR.EnableHDROutput` changes
  (`+0x193`), and at init. So every 1P↔3P switch (aiming in `/thirdperson`, the game's own TP moments) makes new
  components with the Blueprint's values.
- Tick (`0x141BF1550`, vtable +0x348) writes `Intensity` (+0x244) only: once ×0.5 after a rebuild when HDR output is
  on (`0x141BF172B`, relative), and every frame while `FlickerModifiers` (+0x198) is non-empty (`0x141BF0900`: absolute
  values from `FlashLightConfig` +0x1A8, `LightParameterConfig{Intensity[], Temperature[]}` per spotlight). Those come
  from `GameplayEffectHeroLightComponent` (FirstPerson/ThirdPersonLightConfig): only intensity/temperature, never cone
  or radius. Nothing writes `InnerConeAngle`/`OuterConeAngle` (+0x3C8/+0x3CC) or `AttenuationRadius` (+0x398) after
  creation.
- No dark/bright sensing anywhere (FlashlightVolume only switches on/off), no map-load reset beyond the new hero.

### Implementation (`flashlight.c`, beam tuning)
- `flashlight_width`, `flashlight_range`, `flashlight_brightness`: percent of the game's values (defaults 100), live
  from the ini and from the `~` Flashlight tab (sliders + Reset beam, saved when an edit ends).
- Every frame (only while a setting is off 100, or until our values are undone): the local hero's light component,
  each spotlight in `FlashLightComponents`. A new spotlight (view change) gets its current values as the base. A value
  that differs from what we last wrote was written by the game and becomes the new base (flicker, gameplay effects),
  except the HDR ×0.5, which halves the base. Targets: outer cone × width (capped at 80°), inner cone scaled with it,
  radius × range, intensity × brightness (the cookie light function `Flashlight_Cookie_LF_MI` follows the cone), set through the engine's own `SetOuterConeAngle` / `SetInnerConeAngle` /
  `SetAttenuationRadius` / `SetIntensity` (ProcessEvent: value + render-state update). At 100% nothing is called.
- Local only, both sides: host and client each tune their own hero's light on their own screen. Other players see
  your beam as the game draws it (their machine built the spotlights from the same templates), you see theirs that
  way. No protocol change. When the local hero changes (bot take-over, next map) the old hero's spotlights get the
  game's values back.
- `flashlight status` prints the spotlights (current and game values, units, IES, light function).
- No "wider when dark" auto mode: the game has no light measurement, and one would need a render readback.

### Live results (2026-09-27, lane 2, host + client, Evansburgh_B Easy, both heroes in dark FlashlightVolumes)
- Game values (low settings): FP (mode 1) `[0]` cookie spot outer 45° radius 3300 intensity 2.5, `[1]` fill spot outer
  45° radius 5000 intensity 0.16; TP (mode 4) `[0]` inner 8° outer 30° radius 2200 intensity 2.0, `[1]` intensity 0.
  Epic modes not measured.
- Host, overlay `Width` 170 → outer 76.5° on both spots, visibly much wider spot on the wall (cookie scales with it).
  `/thirdperson on` → new TP spotlights picked up (outer 51.0 = 30 × 1.7, inner 13.6); off → FP 76.5 again.
- Client, overlay width 160 / range 200 / brightness 250 → outer 72°, radius 6600/10000, intensity 6.25/0.39; lit
  wall much wider and brighter on the client's screen. Settings written to the client's own `b4bcoop.ini`.
- Host `Reset beam` → game values back exactly (45 / 3300 / 2.5), ini lines commented out.
- Not tested: flicker/gameplay-effect intensity updates while scaled (logic only), Epic quality, HDR output, a restart
  with the ini values set (same handler as the live path).
