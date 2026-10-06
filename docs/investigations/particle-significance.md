# Particle significance: client crash at the Titan Tunnels finale (issue #46)

Build 14216215. Minidump analysis of the 0.9.2 crash report (Windows 11 client, 2026-10-05 19:58), static analysis of
`Back4Blood.exe`, live runs with `launch/multi.sh 2` on Proton (lane 1, 2026-10-05). Implementation:
`native/src/fxsig.c`.

## Crash analysis

- `EXCEPTION_ACCESS_VIOLATION`, execute at address 0. Four task-graph threads (`0x55cc` faulting; the log names
  `TaskGraphThreadHP 18..21`) and the game thread were all in the same frames: a `ParallelFor` (`0x140E64680`)
  run by the game thread's tick.
- Stack: `0x143B2C0F8` `UActorComponent::GetWorld_Uncached` (`Owner->GetWorld()`, vtable `+0x150`: 0) <-
  `0x143E5D43D` (PSC render-time check, reads `WorldPrivate +0xE0`, `Template +0x4C8`) <- `0x1419C3DD7` the game's
  particle **significance function** (`0x1419C3D90`: `Cast<UParticleSystemComponent>`, then that check) <-
  `0x140ED4378` `USignificanceManager` update lambda <- `ParallelFor` <- task graph.
- The object in `rbx` (`0x1DB1D8009F0`) is a freed `UParticleSystemComponent` (class pointer still there, base chain
  depth 5 = exactly PSC; vtable, flags and index overwritten by the allocator's free list); its owner actor
  (`+0xD8`) is freed too. Its `FManagedObjectInfo` (vtable `0x1452CA480`, object `+0x10`, significance function
  `0x1419C3D90` and post function `0x1419C3E00` inside) still holds the pointer.
- No agent code on any crashing thread: `X3DAudio1_7.dll` appears only as the game-thread caller of
  `UGameEngine::Tick` (tick hook) and as a stale uelog return address.
- Timing: Titan dead at 19:58:21, `PlayCinematic : Cinematic4` streams out levels at 23.486 (the forced GC that
  comes with it), crash at 23.677.

## The mechanism

- The game registers every active PSC with the world's `USignificanceManager` (tag "Particles"): the static
  multicast delegate `UParticleSystemComponent::OnSystemPreActivationChange` (`0x1466FF690`, broadcast by
  `0x143E6B0A0`) is bound to `0x1419C3EA0 (PSC*, bool bActivating)`, which (un)registers through the manager's
  vtable `+0x268` / `+0x270` and keeps bit `0x80` of `PSC+0x4F1`.
- Broadcasts: `ActivateSystem` (`0x143E63680`, `true`, only if not active, then sets `bIsActive`);
  `ResetParticles` (`0x143E631D0`) and `DeactivateSystem` (`false`, **only while `bIsActive`** (`+0xC2` bit 0)).
  `OnUnregister` (`0x143E5E510`, PSC vtable `0x145C8BCB0 +0x2C0`) calls `ResetParticles`.
- `bIsActive` is a replicated `UActorComponent` property. `OnRep_IsActive` (`0x143B2EEE0`, vtable `+0x260`, not
  overridden by PSCs) only calls `SetComponentTickEnabled`. A client PSC that activated locally and then gets
  `bIsActive = 0` from the server stays registered while inactive; when its actor is destroyed or its level
  streamed out, `ResetParticles` doesn't broadcast, GC frees it, and the next significance update calls through it.
- Why retail clients never hit it: `ActivateSystem` returns early for templates, unregistered components and when
  `0x141233880` is false (stock 4.25's `IsTemplate() || !IsRegistered() || !FApp::CanEverRender()`), so a
  dedicated server never has an active particle and never sends a `bIsActive` change. Our listen host renders, so it
  does. Retail code path, exposed by the listen server.
- Seen live: on every Titan Tunnels client, right after load, `FallingRock_Chamber02_{First,Second,Third}Rock.
  RockDustTell1` (`VFX_TT_RockTell_01_P`, replicated: `+0xC0` = `0x1B`) are registered but inactive (dev log
  `fxsig: replicated bIsActive=0 on a registered particle component`); the host has none. The non-replicated
  emitters of `MAP_Finleyville_Rescue_A_VFX` (`+0xC0` = `0x03`) are fine. Which replicated PSC was freed in the
  reported crash is not recoverable from the dump (name pool not captured); the Titan's own effects, destroyed
  2 s before the stream-out GC, fit.

## Repro and fix

- Repro (dev build, lane 1, Proton): `multi.sh 2`, `mission /Game/Maps/Missions/TitanTunnels/MAP_PERS_TitanTunnels
  Easy`, client `fxsig guard off`, wait for the 3 orphans (`fxsig`), host `fxsig kill FallingRock_Chamber02_`
  (destroys the 12 Chamber 2 rocks, replicated), client `fxsig gc`: the client crashes with the reported stack
  (`0x143B2C0F2` <- `0x143E5D43D` <- `0x1419C3DD7` <- `0x140ED4378` <- `0x140E647E3` <- `0x140E6438F` <-
  `0x142356A70` <- `0x1423565DC` <- `0x1424DC6DD`, several threads).
- Fix: hook `ResetParticles`; if the PSC is still registered (`+0x4F1 & 0x80`) but not active, call `0x1419C3EA0(psc,
  false)` first (the game's own unregistration, which also runs the post-significance function with "removed").
  Both roles, local only, no protocol change. Same repro with the guard on: `fxsig: inactive particle component
  left the significance manager (3 so far)`, GC frees the components (PSC count 457 -> 385), no crash; Cinematic4
  after `endmission 1` streams out the same levels as the report without a crash.
- A plain guard-off run through `endmission 1` + Cinematic4 did not crash (the three rock orphans are not in a
  streamed-out level and the map change takes the manager with the world); the reported crash needs an orphan freed
  by that GC, which needs real play up to the Titan's death.
- Dev commands: `fxsig` (counts, orphans), `fxsig fix`, `fxsig list <path part>`, `fxsig guard on|off`,
  `fxsig kill <actor name part>`, `fxsig gc`.
