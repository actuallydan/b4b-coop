# Third-person view (#25)

Spike 2026-09-25, build 14216215, `launch/multi.sh 2` on Proton. Result: **works**. First prototyped as a host-only
cheat; now a personal chat command for everyone: `/thirdperson` (`native/src/thirdperson.c`, permission
`CMD_ANYONE`, no `/cheats on`). No gameplay tag is added; the view byte is written directly.

## How the hero's view is chosen
`PlayerViewComponent` (on `Hero_BP_C`, found as the live component whose `OwnerPrivate` (+0xD8) is the pawn):

| offset | what |
|---|---|
| +0xF8 / +0x14C | `ThirdPersonViewConfig` / `FirstPersonViewConfig` (camera, spring arm, mesh tags) |
| +0x148 | `bSupportsFirstPerson` |
| +0x188 / +0x1A8 / +0x1C8 | `ThirdPersonTags` / `ThirdPersonOrbitTags` / `ThirdPersonOccludedTags` |
| +0x1F8 | owner character (unreflected) |
| **+0x200** | **requested view: 1 first person, 2 third person, 3 orbit** (unreflected) |
| +0x201 | view applied last by UpdateView |
| +0x215 | `IsThirdPerson()` result (0x142247B50 just returns this byte) |
| +0x228 | `GameplayTagsComponent` |

- `OnOwnerTagChange` impl 0x141C26C40: only when the changed tag is in ThirdPersonTags/OrbitTags, asks the owner's tag
  interface (GTC+0xE8, vtbl+0x58 = HasAny) → writes +0x200 (3 if any orbit tag, else 2 if any 3P tag, else 1), then
  `UpdateView(this, 0)`. It is the only writer of +0x200.
- `UpdateView(this, bool bForce)` 0x141C27250: 0x141C26EE0 ("can be first person": owner OK, `bSupportsFirstPerson`,
  life state, +0x200 == 1) picks 1P; otherwise +0x200 decides. Swaps camera/spring arm/mesh and broadcasts
  `OnViewChanged(bThirdPerson)` (weapons, flashlight, audio, anims follow). Called from possession / life-state
  changes too (0x141B969E0 & co.), which keep +0x200.
- Live tag lists (host and client identical):
  `ThirdPersonTags: Hero.Status.Pounced Hero.Status.Harpooned Hero.Actions.Healing Hero.View.ThirdPerson
  LifeState.Hero.RescueFromRespawn Hero.Status.MindControlled Hero.Status.Grappled.Sleeper Hero.Status.GrabbedByBrute`,
  `ThirdPersonOrbitTags: Player.View.ThirdPersonOrbit`, `ThirdPersonOccludedTags: Hero.View.ThirdPersonOccluded`.
  `Hero.View.ThirdPerson` is the pure view tag. Not needed: `GameplayTagsComponent` has no callable adder and the byte
  + UpdateView gives the same result without touching gameplay tags (the others carry gameplay meaning).
- 3P config: `Components.Player.ThirdPerson.Camera/SpringArm/Mesh`. The 3P camera follows the control rotation:
  at yaw -87 both views report `GetCameraRotation` (0, -87, 0); 3P camera at (11257, 466, 765) vs 1P (11268, 251, 765)
  = 215 units behind, slightly to the side.

## Implementation (`native/src/thirdperson.c`)
- `/thirdperson [on|off|status]` (no word = toggle): write +0x200 = 2 and call UpdateView(pvc, 1) (signature-checked,
  22 bytes).
- Every tick while on: ADS = any of the hero's `ADSComponent`s (owner weapon's Owner == hero, refreshed every 1 s)
  with `bIsHoldingADS` → want 1, else 2. A 2 the game wrote itself (tag-driven, e.g. healing, pounced) or orbit (3) is
  left alone; when the game drops back to 1 we write 2 again.
- Lifetime: on until `/thirdperson` again or the game quits (not saved). A new local hero (next chapter, camp, another
  host's session, bot take-over) is found by the tick and switched; `/cheats off` and camp don't touch it.
- Local only: nothing is replicated (`RepViewData` only carries an orbit rotation), every machine picks its own hero's
  view; others always see the 3P body. No protocol change (CLAUDE.md "Versioning": neither side needs anything from
  the other).
- Permission: admin.c's dispatcher checks every chat command's `CMD_ANYONE` / `CMD_HOST` / `CMD_CHEAT` (cmds.h)
  before its handler; cheats.c's verbs report theirs through `cheats_perm()`.

## Verified live (spike, as a host-only cheat)
- Camp and mission: host `want 2 applied 2 is3p 1`, camera behind the hero (screenshots). Client's view of the host
  hero unchanged.
- Move: W held 2 s in 3P moved the hero (11268,251 → 10749,170).
- ADS flip: `HeroADSKeyboard` bound to NumPad1 (dev `cheatprobe bind`), held 3 s: `want 1 ... ads 1` (iron sights on
  screen), released: back to `want 2 ... ads 0`.
- Flashlight in 3P: `flashlight on` on host and client, `light=on`, beam visible on the wall from the client's 3P view.
- Client: `cheatprobe view 2` (now `thirdperson view 2`) on the client works the same (local camera only; the host sees nothing different).
  `/thirdperson` on a client answers "host only" (cheat convention).
- Chat path: `type "/cheats on"`, `type "/thirdperson"` on the host. `endmission 1` → next chapter (1-3): new pawn in
  3P by itself (`cheats ON ... third person`); `/cheats off` → `want 1 applied 1`.

## Verified live, as a personal command (2026-09-25, `multi.sh 2`)
- Client typed `/thirdperson` in chat (`type`), cheats off: client `want 2 applied 2 is3p 1`, camera behind its hero
  (screenshot); host `want 1`. Host typed it too: both 3P. Client `/god`, `/cheats on` → "host only"; host `/god`
  without cheats → "cheats are off".
- Camp → mission (character select, ready) → both heroes 3P by themselves; `endmission 1` → 1-3: both new pawns 3P.
- ADS flip on host and client: right mouse via `cheatprobe input rmb 3` (SendInput, window raised with wmctrl) →
  `want 1 ... ads 1`, released → `want 2`. (Posted messages and a NumPad1 `HeroADSKeyboard` binding didn't aim.)
- **Fire and reload in 3P**: host `input lmb 1` → SMG clip 32 → 16, impacts around the screen centre (a bot and the
  floor in front); `input 0x52` (R) → 32. Client: pistol 15 → 14, R → 15. The view stays 3P (`want 2`).
- Client `/thirdperson off` (typed) → `want 1 applied 1`; host still 3P.

## Aim in 3P: shots come from the eyes, not the camera (2026-09-25, `multi.sh 1`, measured)
- `ThirdPersonSpringArm` (SpringArmComponent on Hero_BP_C): TargetArmLength **300**, SocketOffset/TargetOffset 0,
  pivot at the eyes, follows the control rotation. `ThirdPersonCamera` = its camera. Measured with dev
  `thirdperson aim` (PlayerCameraManager.GetCameraLocation/Rotation vs the pawn's GetActorEyesViewPoint + a
  KismetSystemLibrary.LineTraceSingle along each ray): the game's camera sits exactly on the eye ray, 300 behind
  (0.0-1.5 units off it, pitch -29..0). So with the game's camera the crosshair ray **is** the line of fire: hits
  land exactly under the crosshair at every range, but the hero's own head sits on the crosshair (screenshots).
- Where shots really land: new DecalComponents after a shot (dev `thirdperson decals`: new/moved components, their
  RelativeLocation = world impact point). With SocketOffset Y = -80 (camera 80 to the left), wall 438 ahead:
  crosshair ray hit (11188, -80, 765), eye ray hit (11268, -187.5, 765), **impact (11262.8, -187.8, 765.7)**: 5
  units from the eye-ray point (spread). Y = +80 (arm shortened to 162 by the wall behind): 2 impacts at x 11275 /
  11282 vs eye ray 11268 and crosshair 11311. The fire trace uses the hero's eye view point, not the camera.
- Consequence: any side/height offset moves the crosshair off the line of fire by exactly that offset (world units,
  parallel rays): the point under the crosshair and the impact are `sqrt(side²+height²)` cm apart at every range
  (angle ≈ offset/distance: 40 cm is 4.6° at 5 m, 1.1° at 20 m). ADS is 1P, so always exact.
- Real fix (not done): aim correction. Trace from the camera along the crosshair, then make the weapon fire from the
  eyes toward that point, e.g. hook the pawn's GetActorEyesViewPoint (exec thunk 0x1441ABEC0 calls the virtual) or
  whatever the fire path uses, only for the local hero in our 3P. Open questions: which function the fire modes
  (FireModeSingleTraceComponent & co.) call, and whether the host re-checks a client's shot against its replicated
  view rotation (a client's corrected shots could be rejected). Needs a 2-instance test.

## Camera settings (accessibility, 2026-09-25)
- `/thirdperson distance|side|height|fov <n>`, `side left|right|swap`, `reset`; ini `thirdperson_distance`
  (default **180**, game 300), `thirdperson_side` (40, right shoulder; Dan's call until aim correction exists), `thirdperson_height` (0), `thirdperson_fov` (0 = game's).
  distance = ThirdPersonSpringArm.TargetArmLength (+0x230), side/height = SocketOffset.Y/Z (+0x234), fov =
  ThirdPersonCamera.FieldOfView (+0x230). Found by name on the hero with the PlayerViewComponent (one scan per
  hero), written every tick while on (float stores; covers a new hero), the game's values restored on
  `/thirdperson off`. The game's own 3P moments get the same camera while /thirdperson is on.
- Live: default 180 applied in camp and on the mission hero; chat (`type`) `/thirdperson side right` → SocketOffset
  (0, 40, 0), `height 20` → (0, 40, 20), `fov 100` → PlayerCameraManager.GetFOVAngle 100; screenshots over the right
  shoulder. Default side 40 (over the shoulder: the head no longer covers the crosshair; hits land 40 units beside it until
  aim correction exists); the command prints the aim offset when side/height aren't 0.
- Local only: component values on the local hero, nothing replicated, no protocol change. `e2e.py --quick` 12/12.

## Weapon switch in 3P
- Selection (InventoryComponent.SelectedItemActor) changes as fast in 3P as in 1P: SMG → machete 0.418 s (3P) vs
  0.400 s (1P) after the key command (dev `thirdperson watch`; includes CLI latency).
- Not measured: time until the new *gun* can fire (slot 2 was a melee weapon). Likely cause of "looks slow": in 3P
  you see the 3P body's full-length equip montage (FireModeBase has separate FP/3P montages; ItemMeshManagement
  swaps meshes on OnOwnerViewChanged), while 1P shows the short FP one. If gun readiness waits for FP anim notifies
  (FireModeBaseComponent.OnFPAnimNotify) and the hidden FP mesh doesn't tick its montage, it would really be slower;
  test: `thirdperson watch 3` + slot key + hold fire with two guns, 1P vs 3P, first new decal time.

## Game's own 3P moments with /thirdperson on
- Incap: `cheatprobe hp Hergmgurk 0` → downed, a bot picked the hero up ("Vigor!"), view `want 2 applied 2` the whole
  time and after; nothing stuck. A dead bot (`hp #1 0` hit a bot) didn't touch the local view.
- Grabbed (2026-09-25, lane 2, host in 3P, bots away): `/spawn stalker 1` grappled the host: view `want 3 applied 3`
  (the game's orbit camera) the whole grab; after `/killall` back to `want 2`, our camera (180 / side 40, `thirdperson
  aim`: 180.0 along, 40.0 off) at once. Screenshots: game's grapple camera, then over the shoulder again.
- Healing (host in 3P, hp set to 60, a bandage via `giveitem 0 <#>`, key 4, left mouse held): "Healing..." bar in our
  3P, view 2 throughout, hp 60 -> 92, camera settings intact. (No medkit pickup on that map; the bandage uses the same
  heal action.) Downed (`cheatprobe hp #0`) and `/revive`: view 2 throughout and after.

## Not verified
- `/freeze` (cheat) also blocks the local player's firing.

## Dev tools added (dev builds)
`thirdperson view [1|2|3]` (dump the component, tag lists, owner tags, each ADS component; a digit sets the view),
`thirdperson use` (the hero's HeroUseComponent: potential/spotting usable, prompt, probe sizes, an item pickup's
observable state and rules, the item observer), `thirdperson usables [range] [substr|!substr]` (UsableComponents
around: owner, class, location, enabled, prompt, vtable +0x410), `thirdperson lookat <n> [dist [dz [z]]]` (host: stand
dist from usable n and face it; in our offset 3P the crosshair is put on it), `thirdperson los <n>`, `thirdperson
itemfix 0|1` (the #27 fix on/off), `fnprobe <va> [label]` (testing.c: count a native function's calls and return
values; `fnprobe reset|off`),
`thirdperson [on|off|status|distance ...]` (the chat command), `thirdperson aim [pitch yaw]` (camera vs eye ray and
where each hits; with pitch/yaw sets the control rotation), `thirdperson arm [i sx sy sz]` (the hero's spring arms; sets
arm i's SocketOffset), `thirdperson decals [class]` (new/moved DecalComponents = shot impacts), `thirdperson watch [s]`
(log selected-item and decal-count changes with timestamps), `cheatprobe press <vk|lmb|rmb> [s]` (hold a key via window
messages), `cheatprobe input <vk|lmb|rmb> [s]` (the same through SendInput: the prefix's own wineserver input queue,
works for mouse buttons when the window is raised; this is how fire/ADS are tested), `cheatprobe bind [axis] [<Name>
<Key>]` (list / add input mappings; not saved).

## Aim correction (2026-09-25, lane 2)
- Implemented (thirdperson.c, `thirdperson_aimfix=1` default): MinHook on the hero class's GetActorEyesViewPoint
  (vtable +0x5F0; GetBaseAimRotation +0x6E0 hooked too, unused). For the local hero in our 3P with an offset camera, the
  eye rotation is turned towards the point under the crosshair: camera ray (rebuilt each call from the camera's
  offset to the eyes measured last frame from PlayerCameraManager.CameraCachePrivate POV), traced (Visibility, starting
  beside the eyes), cached per frame. Game thread only.
- Fire path (dev `thirdperson callers <s> [all]`): per shot one extra eyes call from 0x141930ceb (func 0x141930c50:
  eyes for an autonomous/authority pawn, GetBaseAimRotation for a simulated proxy (+0x120 == 1); callers 0x141940150,
  0x141942000, 0x141943930, 0x141946816) and one from 0x141ef8514. Per-frame callers 0x141531fbd, 0x141bfc088.
- **Verified live on a client (lane 2, `multi.sh 2`)**: side 40 at a wall 600 away: without the fix the impact decal is
  at the eye-ray point (17-80 units off the crosshair point), with it at the crosshair point (0-5 units = spread);
  `thirdperson aim` then shows both rays hitting the same point (0.00 deg). Visibility traces hit a common's capsule.
- **The host takes the client's hits (no protocol change needed)**, `multi.sh 2`, client in 3P side 40, bots out of
  the way (incapped + `/tp` to the end saferoom; otherwise they kill every spawned common within a second), `/cheats on`,
  `/god all`, a spawned common (20 hp, `thirdperson targets` on the host). The client's control rotation (what the host
  replicates) was set with `thirdperson aim <p> <y>`, the client's own shot skewed with `aimtest eyes <yaw>`, aimfix 0,
  one short burst (`cheatprobe input lmb 0.08`), rotation checked unchanged after the shot; A and B each run twice, same result:
  - A: control rotation on the common, shot skewed +45 (client decal on the wall beside it): host hp **20 -> 20**.
  - B: control rotation 45 deg off, shot skewed -45 onto the common (client blood decals at it): host hp **20 -> 0.6**.
  - aimfix 1, crosshair (camera ray) on a common's head at 360: `thirdperson aim` 0.00 deg, host hp 20 -> 0.
  The host does call the fire-path view function (0x141930ceb, "other eyes" in `thirdperson callers 3 all`) once per
  client shot, but damage follows the client's own trace: a hit is decided by the shooter's game, so the correction
  works client-only. Nothing seen that rejects a shot 45 deg away from the replicated view.

## Other results (2026-09-25, lane 2)
- Gun-to-gun switch (pistol → AR, key then fire held, `thirdperson watch`): first shot 1.172 / 1.174 / 1.170 s in 3P vs
  1.178 / 1.171 / 1.180 s in 1P (selection at 0.40-0.42 s): no difference.
- Hotkeys now read the game's own input (PlayerController.IsInputKeyDown, cmds.c `cmds_hotkey_down`), GetAsyncKeyState
  only as a fallback: N typed into the open chat box no longer toggles 3P (verified), and raising/switching the window
  (wmctrl) no longer fires it (0 spurious toggles over two focus changes).
- `cheatprobe hp <#>` without a value is lethal (it downs the hero), careful.
- #27 (interactions in 3P): see "Interactions in third person" below (fixed).
- Possible 1-frame first-person flash when a game-driven 3P moment ends (the game writes 1, our next tick writes 2); an UpdateView hook would remove it if it shows.

## Open
- A shoulder-swap hotkey (`/thirdperson side swap` exists) if wanted.

## Interactions in third person (#27, 2026-09-25, lane 2)
Result: **item pickups (weapons, ammo, items, card shrines, collectibles, food) failed in 3P; fixed, client-side, no
protocol change.** Everything else tried already worked in the game's own 3P.

How the game picks what F uses: `HeroUseComponent` tick (0x141BFBF50) takes the pawn's `GetActorEyesViewPoint`
(vtable +0x5F0, call at 0x141BFC082) and probes from the eyes (0x141BFDBA0): a sphere 65 ahead / 65 radius (130 reach;
75/75 while something is already selected), widened by `SpottingHeightIncrease` (500) to find a "spotting" candidate;
candidates filtered by the usable's CanUse (vtable +0x410; the bool arg = lenient spotting call), scored by the angle
to the eye direction (0x141BFE750), LoS traced from the eyes (0x141BFEC10, channel 3, ignoring the user); the best one
that also passes the strict CanUse and the small sphere becomes `PotentialUsableComponent` (+0x150, prompt and F), else
only `SpottingUsableComponent` (+0x158, blue outline). Nothing there depends on the camera; the only view check in the
tick is while holding a use (+0x215 → the lenient continue check 0x141BFEFF0: 2D distance < CancelDistance + CanUse),
which only makes holds in 3P more forgiving.

Why pickups failed: `ItemPickupUsableComponent`'s strict CanUse (0x1418FBA10) needs the local hero's entry in the
pickup's `ItemObservableComponent.ObservableStates` (weak pointer at the usable's +0x660; entries of 0x20: TargetPlayer,
HeroUseComponent, Widget, +0x18 observed, +0x19 tooltip shown) with both flags set (remote users skip this check on
the server: `Controller` vtable +0x6B8 not local → true). Those flags come from the hero's `ItemObserverComponent`:
1. **It switches itself off in 3P.** Its refresh (0x1418F3600, bound to `OnViewChanged` and `OnUIScreenOpened`) sets
   `ObserverComponent.bEnabled` (+0x108) = no blocking UI screen open && ... && `!PlayerViewComponent.IsThirdPerson`
   (weak +0x138, byte +0x215). Measured: 3P observer `enabled 0`, entry `+18 00 +19 00`, `fnprobe` on the strict
   CanUse: 62 of 62 strict calls false in 3P vs 61/61 true in 1P.
2. **Its view is the first-person camera.** The observation system gathers each observer's view once per frame
   (0x140ECC480, game thread; sparse array at +0x40, records 0x68: +0x18 observer, +0x20 location, +0x2C rotation,
   +0x38 direction) from `ObserverComponent.ViewComponent` (+0x110, `FirstPersonCamera`, set at BeginPlay; without
   one it uses `GetActorEyesViewPoint`). An item's rule (`ObservationStartRules`: flags 2, 0-300 units, collision 4,
   sphere 30) is a ray-sphere test, and the FP camera looks from the eyes along the control rotation: parallel to our
   offset crosshair ray, 40 units beside it, so an item under the crosshair at arm's length is missed.
3. The observer's own tick (0x1418F2510) picks the tooltip to show (+0x19, 0x1418F0BD0) by distance from
   `ViewComponent` and angle to the (aim-corrected) eye direction; it does nothing without a `ViewComponent`, so
   clearing that (tried) breaks the tooltips.

Fix (thirdperson.c "Item pickups in third person"), only while our 3P is on (view byte 2, not the game's orbit):
- MinHook on the refresh: in our 3P it runs with the view byte reading first person (restored right after), so every
  other condition (open screens) still decides. `thirdperson: item observer hook installed`.
- MinHook on the gather: after it, with an offset camera (the aim correction's `aim_ok`), our observer's record gets
  the eyes' view point as the aim correction turns it (toward the point under the crosshair). `thirdperson:
  observation view hook installed`. With `thirdperson_aimfix=0` it stays the FP camera: pickups then react
  `side`/`height` units beside the crosshair, like shots.

Inventory (dev `thirdperson usables`/`lookat`/`use`, harness: stand 80-150 units away at floor height, put the
crosshair (3P) or the eyes (1P) on the item, read `PotentialUsableComponent`); "3P vanilla" = `thirdperson itemfix 0`:

| Interactable | 1P | 3P vanilla | 3P fixed |
|---|---|---|---|
| Weapon/item pickups, camp range (38 kinds: AR, SMG, SG, HG, LMG, sniper, melee, grenades, bandage, medkit, pills, attachment) | 38/38 | 0/38 (blue outline only) | 38/38 (the same one: two overlapping Molotovs pick the neighbour in both views) |
| Mission pickups: weapons, heavy/light ammo, food, Morbid collectible, card shrine (WildCard, Legendary) | all | none | all |
| Vendors (Brynn, Dusty, Garner, Phillips = war table "open campaign menu", character select, leaderboard) | ok | ok | ok |
| Saferoom vendor (UsableComponent "press F to shop"), saferoom exit door (DoorUsable) | ok | ok | ok |
| Revive a downed hero (IncapUsable, "hold F to revive") | ok | ok | ok |

- Screenshots: camp M4 `F PICK UP M4 CARBINE` tooltip only with the fix; mission card shrine: blue outline, no prompt
  → `F PICK UP` + the card (`HEADS, YOU LOSE`); client in the saferoom aiming at an M4: vanilla 3P shows `PRESS F TO
  SHOP` (the vendor behind it wins), fixed shows the M4 tooltip; client at a WildCard shrine: `HOLD TO BUY 500 / LIFE
  INSURANCE`.
- Using them in 3P (F via `cheatprobe press 0x46`): host picked up the M4 (slot 1 became the M4), bought a Legendary
  card (`LogUse: CardShrine_Legendary... EndUse Reason=SuccessfulUse`, shrine disabled after). **Client** (`multi.sh
  2`, host `tp 1` next to the item, client aims): picked up an AR (host log `AR01_1_Pickup... BeginUse / EndUse
  Reason=SuccessfulUse`, gone on the client) and bought a WildCard card (1 s hold, host `SuccessfulUse`). The host
  accepts: the server's CanUse skips the observation for a remote user, so no protocol change.
- Holding F to revive in 3P (host, a bot downed with `cheatprobe hp #3 0`): "Reviving [BOT] WALKER..." bar in our
  3P view, `LogUse: Hero_BP_C_... EndUse Reason=SuccessfulUse` after 2.4 s. (`/freeze` blocks the local player's use
  too, like firing; a hero downed by `hp <#> 0` once does not go down from it again.)
- Not tried / not reproducible here: ledge-hang pick-up, supply crates (same base UsableComponent CanUse as the vendor
  and jukebox, no observer), healing a teammate with a bandage (weapon-slot key and left mouse don't reach the
  headless instances), mission objectives (none on Evansburgh B). `/freeze` also keeps the host's `tp` of a client
  hero from reaching the client.
- `e2e.py --quick` 13/13 (lane 2) with the fix.
