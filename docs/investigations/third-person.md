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

## Not verified
- Precise aim: whether hits land exactly under the crosshair at range (the 3P camera is 215 units behind and a little
  to the side; the game has no 3P reticle, the 1P one is used). Needs a hand test at a wall.
- Incap / pounced / healing while on (the game's own 3P tags): logic leaves those views alone; not exercised.

## Dev tools added (dev builds)
`thirdperson view [1|2|3]` (dump the component, tag lists, owner tags, each ADS component; a digit sets the view),
`thirdperson [on|off|status]` (the chat command), `cheatprobe press <vk|lmb|rmb> [s]` (hold a key via window
messages), `cheatprobe input <vk|lmb|rmb> [s]` (the same through SendInput: the prefix's own wineserver input queue,
works for mouse buttons when the window is raised; this is how fire/ADS are tested), `cheatprobe bind [axis] [<Name>
<Key>]` (list / add input mappings; not saved).

## Open
- A hotkey (like `flashlight_key`) or an ini default could follow if players want it.
