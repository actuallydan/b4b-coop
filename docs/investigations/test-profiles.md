# Test client profiles wiped to blank (2026-09-25)

Symptom: a test client's (test2) offline profile became a blank ~136 KB one (no decks, 0 SP, no burn cards), breaking
`tools/e2e.py` (burn-card and profile-diff checks). Seen once per lane.

## Cause: auto-join raced the sign-in
Evidence: `/tmp/b4b-e2e-l2-20260925-170310/duo-b4bcoop-test2-316.log` (only failing run of 11 inspected).
- The client's `auto: joining 127.0.0.1:7887` (cmds.c auto_tick, ini `join=`) fired 10 ms *before*
  `signin: StartSignIn`, in every run. The join's LoadMap normally lands after the profile load; here the machine was
  slow and it landed in the middle of the SignInTask_Settings chain:
  ```
  17:04:22.228 signin: answering online/offline popup with Offline
  17:04:22.265 LoadMap Level: MAP_PERS_FortHope_A          <- join travel, profile not loaded yet
  17:04:24.236 signin: signed in                           <- SignInScreen gone with the old world (not a sign-in)
  17:04:25.496 Created screen 'SignInScreen' / SetOnlineModeForSignIn(Online)
  17:04:25.613 PlayerProfileSettings version invalid - HydraPublicId mismatch - Local:offline.76561198063588550
               Saved:p64c5cf5ae1684c40a2bbb33a0f451a05
  17:04:25.614 ValidateSettings ... rejecting / Deserialized: {"publicId":"", ... "decks":[] ...}
  17:04:25.618 [PlayerProfileSettings] SaveGame   <- blank profile written over the real one
  ```
- `publicId` in the save is the Hydra (online) id `p64c...` of the account (every clone of the real prefix has it). In
  an Offline sign-in the check is skipped; in the re-created Online-mode sign-in the saved id is compared with the local
  `offline.<steamid64>` and the save is rejected, reset and written. The reset save then has `publicId
  offline.<id>`, so later runs load it fine (that is why lane 1's test2 stayed blank).
- Not the cause: SIGKILL (the before/after files of the failing run are complete JSON), B4B_BLANK, clone age, the
  .sav being machine- or prefix-bound (a .sav copied between prefixes loads fine).

## Fixes
- cmds.c auto_tick: every join (ini `join=` too, not only a Steam session target) waits until the sign-in is finished
  (`signin_pending() || signin_on_title()`). Verified: client logs `signin: signed in` then `auto: joining`.
- Golden profiles (tools/testprefix.py): `<prefix>/profile-golden/` = known-good .sav/.json (clone time, or first use:
  the current profile if healthy, else the real prefix's); `testprefix.py N --golden|--restore`. e2e.py restores it for
  every prefix of a session before the "before" snapshot and logs `profile testN: ...`; `--keep-profiles` skips it.

## Verification (lane 2)
`e2e.py --quick`: 13/13 twice in a row (`/tmp/b4b-e2e-l2-20260925-172523`, `-173049`), then with test2's profile
replaced by the blank one from the incident: restored (`was: publicId offline.76561198063588550, 0 deck(s), SP 0`),
13/13 (`-173601`). One earlier run failed only the loopback check: the host had 4 UDP sockets on 0.0.0.0 with random
ports for one sample during a slow sign-in (EOS_TimedOut; lane 1 busy at the time), unrelated to this change
(`/tmp/b4b-e2e-l2-20260925-171946/ss-samples.txt`).

## Flatpak mode: the second account's own golden profiles (2026-09-26)
`B4B_STEAM=flatpak` runs lane 2 on account dreamsofants (76561198994546085). The lane-2 prefixes are clones of Dan's
real prefix, so their profile and `profile-golden/` are Hergmgurk's (publicId `p64c...`, 15 decks, burn cards). That
`.sav` doesn't load under dreamsofants (`[PlayerProfileSettings] Failed to deserialize` → blank profile: publicId
empty, 0 decks, 0 consumables, SP 73; `/tmp/b4b-e2e-l2-20260926-181741/profile*-after.json`), so e2e's burn-card and
profile-diff checks failed. dreamsofants' own real profile is a fresh 2.4 KB one (no decks, no burn cards).
- tools/testprefix.py: one golden set per Steam mode. Flatpak mode uses `<prefix>/profile-golden-flatpak/`;
  `profile_health` there also rejects another account's profile (publicId not empty / not `offline.<dreamsofants>`),
  counts burn cards (`consumables` rows `Burn_*`, acquired - spent) and accepts SP + (decks or burn cards). No
  fallback to the real prefix in flatpak mode (it holds Hergmgurk's). `--golden-from M` copies test M's golden set.
- Making it (in the game, lane 2, flatpak, holding its lock): one instance on test1, then on the host with dev
  commands: `cheat cheats on`, `cheat supply +50000`, `cheat unlockall` (the backup lands next to the save),
  `duffelreward 0 <product guid> 30` for several burn cards (the duffel-bag issuer: AdjustConsumableQuantity on the
  local profile), `cheat cheats off`; wait for the deferred save, stop, `testprefix.py 1 --golden`, then
  `testprefix.py 2..5 --golden-from 1`.
- Done 2026-09-26: test1 seeded with dreamsofants' real 2.4 KB profile (loads: `[PlayerProfileSettings] Deserialized`),
  then the steps above → `publicId -, 0 deck(s), SP 50073, 300 burn card(s), 74663 B` (372 unlocks; `burncard list`: 11
  playable), golden set on lane2 test1-5. `B4B_LANE=2 B4B_STEAM=flatpak B4B_GPU=4090 tools/e2e.py --quick`: 13/13
  (`/tmp/b4b-e2e-l2-20260926-200831`; host Burn_RollGunHG, client Burn_RollGunAR, client SP +73), native
  gameprocess_log still 1328 `adding PID` lines. Solo decks: none (0 decks is fine: e2e never reads a deck).
