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
  (default **180**, game 300), `thirdperson_side` (0), `thirdperson_height` (0), `thirdperson_fov` (0 = game's).
  distance = ThirdPersonSpringArm.TargetArmLength (+0x230), side/height = SocketOffset.Y/Z (+0x234), fov =
  ThirdPersonCamera.FieldOfView (+0x230). Found by name on the hero with the PlayerViewComponent (one scan per
  hero), written every tick while on (float stores; covers a new hero), the game's values restored on
  `/thirdperson off`. The game's own 3P moments get the same camera while /thirdperson is on.
- Live: default 180 applied in camp and on the mission hero; chat (`type`) `/thirdperson side right` → SocketOffset
  (0, 40, 0), `height 20` → (0, 40, 20), `fov 100` → PlayerCameraManager.GetFOVAngle 100; screenshots over the right
  shoulder. Defaults keep side/height 0 so aim stays exact (the command prints the aim offset when they aren't).
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
- Pounce: `/spawn stalker 2` near the team: the bots killed them before a pounce (16 s of polling the owner tags);
  not exercised. Healing with a medkit: not exercised.

## Not verified
- Pounced / grabbed / healing while on (the game's own 3P tags): the logic leaves those views alone (a 2 we didn't
  write, or 3), and our camera settings apply to them; not exercised live.
- Gun-to-gun switch timing 1P vs 3P (above).
- Dev-CLI note: with the window raised by wmctrl, `thirdperson_key` (N) fired once on its own (log `thirdperson:
  hotkey: first person` right after `wmctrl -a`); probably a stale key state at focus change under Wine. Not seen
  otherwise.
- `/freeze` (cheat) also blocks the local player's firing.

## Dev tools added (dev builds)
`thirdperson view [1|2|3]` (dump the component, tag lists, owner tags, each ADS component; a digit sets the view),
`thirdperson [on|off|status|distance ...]` (the chat command), `thirdperson aim [pitch yaw]` (camera vs eye ray and
where each hits; with pitch/yaw sets the control rotation), `thirdperson arm [i sx sy sz]` (the hero's spring arms; sets
arm i's SocketOffset), `thirdperson decals [class]` (new/moved DecalComponents = shot impacts), `thirdperson watch [s]`
(log selected-item and decal-count changes with timestamps), `cheatprobe press <vk|lmb|rmb> [s]` (hold a key via window
messages), `cheatprobe input <vk|lmb|rmb> [s]` (the same through SendInput: the prefix's own wineserver input queue,
works for mouse buttons when the window is raised; this is how fire/ADS are tested), `cheatprobe bind [axis] [<Name>
<Key>]` (list / add input mappings; not saved).

## Open
- Aim correction for side/height offsets (above). A shoulder-swap hotkey (`/thirdperson side swap` exists) if wanted.
