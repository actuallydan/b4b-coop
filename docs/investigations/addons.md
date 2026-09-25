# Add-ons: L4D-style loader, format and enable list (#20)

Status 2026-09-25, build 14216215, Proton. Epic #23. Code: `native/src/addons.c` (loader, `/addons`),
`native/src/paks.c` (mount + signature exemptions, see model-mods-paks.md), `tools/modkit/addon.py` (packer).
Player page: docs/COMMANDS.md "Add-ons".

## TL;DR
- Drop-in: `<game>\b4bcoop-addons\<name>.pak` (next to `Back4Blood.exe`, outside `Gobi\Content\Paks`, which the
  engine mounts itself with signature checks). An add-on zip holds `b4bcoop-addons/<name>.pak`: unzip into the game
  folder. Restart to apply; no in-game browser.
- One file per add-on: our pak format (tools/b4bpak.py) with an extra root entry `b4bcoop-addoninfo.txt`
  (title, author, version, category, description, content). A pak without it still loads (title = file name).
- `addonlist.txt` in the folder: `<file>.pak=1|0`, top to bottom = load order, later wins. New paks are appended,
  switched on, sorted by name; the agent rewrites the file then. `/addons on|off` edits it.
- Conflicts (same file path in two enabled add-ons) and "mixed" packages (a package's .uasset/.uexp/.ubulk ending up
  from different add-ons: can crash) are logged, listed by `/addons`, and one chat notice is shown after the first
  map load.
- Player builds: paks.c installs **no hook** unless at least one add-on will be mounted. Retail paks keep every
  signature check either way.
- Multiplayer (#22, §6-§7): add-ons stay local (each player sees their own, others see vanilla). Each add-on is
  classified from its files as cosmetic or gameplay-affecting; joiners send a summary in the login URL and the host's
  `addons_policy=any|cosmetic|none|match` (default `cosmetic`) refuses the rest with a message on both sides. No
  protocol bump (§7).

## 1. Format
Folder (default `<DLL dir>\..\..\..\b4bcoop-addons`, normalized with GetFullPathName; ini `addons_dir=<windows path>`,
`addons=0` = off). Only `*.pak` files directly in it.

Add-on pak = pak v9, magic 0x18772, uncompressed, plain index, mount point `../../../`, names `Gobi/Content/...`
(or `Engine/Content/...`), plus `b4bcoop-addoninfo.txt` at the root (`../../../b4bcoop-addoninfo.txt` in the
engine's file system: harmless, never read by the game, excluded from conflicts):
```
title=Holly magenta portrait
author=...
version=1.0
category=ui
description=...
content=cosmetic (textures)
```
`content=` is written by `addon.py pack` from the files (§6); the agent derives it again and only logs a mismatch.
The parser also takes L4D KeyValues lines (`addontitle "..."`, `addonauthor ...`; the `addon` prefix is dropped, braces
ignored), so an L4D-style addoninfo.txt mostly works as is. Suggested categories (free text):
survivors ridden weapons items ui sounds maps misc.

Content id for #22: the index SHA1 from the pak footer (it covers every entry's SHA1), logged as `id <sha1>` and
printed by `addon.py pack/info`.

## 2. Loader (addons.c)
- `addons_scan()` runs in DllMain from `paks_early_init` (before the engine exists; kernel32 file I/O only): reads
  `b4bcoop.ini` (`addons`, `addons_dir`), `addonlist.txt`, lists `*.pak`, appends new ones, rewrites the list if it
  changed (`.tmp` + MoveFileEx), then for each present add-on in order: footer checks (magic, version 9, index not
  encrypted/frozen, bounds), reads the index and **verifies its SHA1** (a corrupt index would be an engine Fatal at
  mount), walks the entries (bounds-checked), reads the addoninfo entry (uncompressed, data at entry offset + 53 bytes
  of in-data FPakEntry). Returns the number of add-ons to mount. Non-ASCII file or folder names are refused (the
  exemption matching compares narrowed paths).
- Read order: 1000 + position among present add-ons (retail 4, +100 per `_P` patch level; dev `modpaks=` 3000+,
  `mountpak` default 3500). `FindFileInPakFiles` takes the highest read order, so later = wins.
- Conflicts: a hash map file path (lower case, `../` stripped) -> last add-on; every overwrite by another add-on
  counts for that (loser, winner) pair. Then package stems (path minus `.uasset/.uexp/.ubulk/.uptnl/.umap`) -> owner;
  two owners for one stem = "mixed". Logged as `addons: conflict: ...`; `addons_init()` (init_thread) queues one
  `chat_local_later` notice (mixed first, then a single conflict by title, else a count, else unloadable add-ons).
- `addons_mount()` from the `FPakPlatformFile::Initialize` hook, right after the retail paks:
  `paks_mount_unsigned(path, order)` (bSigned cleared for that Mount only, path recorded for the other two
  exemptions).
- `/addons` (`/addon`) is a chat command for everyone (admin.c table), game thread: `list` (default), `on|off <#>`,
  `info <#>`; `<#>` = number, file name, title or a unique substring. States: on, off, on/off after restart,
  on NOT LOADED (why in `info`), MOUNT FAILED, missing (listed but file gone: kept in the list so its setting
  survives).

## 3. Packer (tools/modkit/addon.py)
- `pack <src> [-o out.pak] [--title ... --zip]`: `<src>` = folder of cooked files laid out like the game
  (`Gobi/Content/...`: what `dumpassets` writes and the modkit tools produce) with optional `addoninfo.txt` at its
  top, or one of our uncompressed paks (repacked). Refuses files outside `Gobi/`/`Engine/` and non-ASCII names; warns
  about a `.uasset` without `.uexp` and a lone `.uexp/.ubulk` (partial package = mixes with the game's own).
  Deterministic output (sorted entries, fixed zip timestamps). `--zip` = `b4bcoop-addons/<name>.pak` in a zip.
- `info <pak>`, `check <addons dir>`: the same order/on-off/conflict rules as the agent, offline.

## 4. Verification
Offline (Proton 10 wine, harness linking addons.c with stubbed LOG/mount/chat): new add-ons appended sorted and
switched on; truncated pak -> `damaged, or not a Back 4 Blood add-on pak`; text file -> `not a pak file`; order from
the list; `off` add-on not mounted; conflict (2 files) reported with the later add-on winning; `.uexp`-only add-on
after a full one -> "mixed" warning; `/addons on 1` rewrites the list and says `applies after restart`.

## 5. Live results (player build, test instance 1, add-ons in the real default folder)
Player builds have no `offline=1`; a no-click sign-in came from the command line:
`launch/instance.sh 1 -Port=7787 +b4bcoop_join proto:2 ver:0.3.0 addr:127.0.0.1:7799` (auto sign-in Offline, 6 failed
joins to a dead loopback port, then `auto: hosting` in its own Fort Hope). Test content: the #17 magenta Holly
HUD portrait, and a green copy (BC7 mode-6 block `4000e0ff0700feff0100000000000000` over the same 320x208 mip),
packed with `addon.py pack` (evidence under `~/.local/share/b4b-coop/addons-test/evidence/`).

| Run | addonlist.txt | Result |
|---|---|---|
| 1 | magenta=1, green=0 | `addons: ... 2 add-on(s), 1 to mount, 0 conflict(s)`; only magenta mounted (order 1000); `paks: precacher: first read from a mod pak (...holly_magenta.pak)`; HUD portrait **magenta** in Fort Hope |
| 2 | magenta=1, green=1 | `addons: conflict: holly_magenta.pak and holly_green.pak both change 2 file(s) ...: holly_green.pak wins`; both mounted (1000, 1001); chat `[local coop, delayed] Add-on conflict: "Holly green portrait" overrides "Holly magenta portrait". /addons`; portrait **green** |
| 3 | no folder | `addons: no add-ons folder (...)`, no `paks:` line at all: no hook installed |

No `Fatal`/`Corrupt file` in any run. This also re-verifies the #17 path-matching refactor: the exemptions match
the absolute `Z:\...\b4bcoop-addons\*.pak` paths (`no .sig lookup for mod pak ...`, precacher line above), with
spaces in the path. Not run live: `/addons` typed in chat (same code as the harness, which printed the outputs
above; player builds have no `type` command), `addons_dir=`, a damaged pak.

## 6. Content class (#22): cosmetic vs. gameplay-affecting
Code: `native/src/addonclass.c` (agent, at load, from the pak) and `classify()` in `tools/modkit/addon.py` (at pack,
written as `content=`): same rules, kept in sync. The author's label is never trusted.
- Every `.uasset` is read (header only: summary, name map, import map, export map; B4B = legacy -7, unversioned,
  104-byte export entries, like `tools/modkit/upkg.py`). Each export's class: import -> (module = the class import's
  outer package, class name); an export class (index > 0) = a blueprint's default object.
- **Cosmetic** when every export's class is one of: textures (`Texture2D`, `TextureCube`, arrays, volume, render
  targets, light profiles), materials (`Material`, `MaterialInstanceConstant`, `MaterialFunction*`,
  `SubsurfaceProfile`), meshes (`SkeletalMesh` + `SkeletalMeshSocket`, `MorphTarget`, LOD settings, `StaticMesh`,
  `StaticMeshSocket`, `/Script/ClothingSystemRuntime{Common,Nv}`), sounds (Engine sound classes, `SoundNode*`, all of
  `/Script/AkAudio`), UI (all of `/Script/UMG`, `/Script/MovieScene{,Tracks}`, fonts, `StringTable`, Slate style
  assets, a widget blueprint's class/functions/default object), effects (Cascade `Particle*`/`Distribution*`, all of
  `/Script/Niagara`). A `SkeletalMesh` package must import a `Skeleton` (it uses one of the game's skeletons).
- **Gameplay** for anything else; named reasons: physics asset, physical material, collision (`BodySetup`, so a
  static mesh with collision is gameplay), navigation collision, data/curve tables, curves, blueprint, animation
  blueprint, skeleton, animation (`AnimSequence`, montages, blend spaces, pose assets: notifies can drive gameplay),
  map (`.umap`, `World`), level sequence, instance of another package's blueprint class, unknown class.
- Other files: `.ubulk`/`.uptnl` (bulk data) cosmetic; `.uexp` without its `.uasset` in the same add-on gameplay
  (class unknown); `.locres`/`.ufont` ui, `.bnk`/`.wem` sounds, shader libraries materials; `.ini` gameplay (config);
  any other type gameplay.
- Checked on 1477 packages under `~/.local/share/b4b-coop/{extract,meshes,texwork}`: C (built natively with a small
  driver) and Python give identical results. Extracted game packages: 447 textures, 151 materials, 619 meshes
  (all 601 mesh-mod outputs incl. 12 with cloth) cosmetic; animations, blueprints, `GuidDataTable`
  (`*_Customization_DT`), static meshes (all have `BodySetup` + `NavCollision`), skeletons, physics assets gameplay.
- Logged per add-on: `addons:    content: cosmetic (textures)` / `content: gameplay (1 file): data table:
  Walker_Customization_DT.uasset`; `its addoninfo says content=cosmetic; the files say gameplay (the files decide)`.
  `/addons` shows `[on, cosmetic]`, `/addons info` the kind, the first gameplay file and the short id.

## 7. Multiplayer (#22)
L4D-like: add-ons are local content. Nothing is sent to other machines except a summary at login; nothing is
downloaded. A player sees their own add-ons on everyone (e.g. a survivor skin on every survivor wearing that outfit),
the others see vanilla. Code: `native/src/addons_mp.c`, gate in `admin.c` PreLogin, option in `cmds.c` `cmd_join`.
- **Summary** (client, every join and rejoin: cmds.c appends it to the join URL that travel.c reopens):
  `?b4bcoopaddons=<C>c<G>g[,g<id8>-<Title>...][,c<id8>...][,+<n>]` = counts of mounted cosmetic/gameplay add-ons,
  then gameplay entries (8-hex short content id + title as `[A-Za-z0-9_]`, 24 max), then cosmetic ids. Capped at 400
  chars (the whole login URL is one FString, max 1024 on the wire; with the game's own options it was ~95 chars);
  what doesn't fit is counted in `+<n>`. Always sent (`0c0g` with no add-ons), so "absent" = older b4bcoop.
- **Host gate** (`admin.c` PreLogin: join policy, protocol, then add-ons, all before the game's own PreLogin), ini
  `addons_policy=` / chat `/addons policy <x>` (session only), default = `ADDONS_POLICY_DEFAULT` in addons_mp.c:
  `cosmetic` refuses G > 0; `none` refuses any add-on; `match` needs the joiner's gameplay ids = the host's (cosmetic
  free); `any` never refuses. Joiner's login error, e.g. `Host allows cosmetic add-ons only; you have gameplay
  add-ons: Walker data table test. Switch them off (/addons off <#>) and restart the game.` / `Host requires the same
  gameplay add-ons as theirs. You lack: X. Switch off: Y. Then restart the game.`; host chat `<name> could not join:
  they have gameplay add-ons (...); addons_policy=cosmetic.` (once per player and policy). The client stops its
  auto-join on it (`chat_on_join_failed`: " add-ons"), like a version mismatch: only a restart can change add-ons.
- **`/addons players`** (host): the summary of each accepted login, keyed by its NetConnection. B4B's PreLogin gets
  `Connection->PlayerId` as the unique id, so the connection is the entry of `NetDriver.ClientConnections` with
  `conn + offset(PlayerId) == uid` (all local copies share one Steam id, name and IP, so those can't tell players
  apart). Players are found through `PlayerState.Owner` -> `PlayerController.Player` (the connection), which survives
  seamless travel; a non-seamless map change logs in again.
- **No protocol bump.** Old client -> new host: no summary, and older b4bcoop has no add-on loader, so none is the
  truth (logged as `no add-on summary (older b4bcoop)`; only `match` with host gameplay add-ons refuses it). New
  client -> old host: the option is ignored. Nothing a client must understand changed; the refusal is an ordinary
  login error. A modified client can lie about its add-ons: the policy is for consistency among friends, not
  anti-cheat.
- Not done: the Steam rich presence doesn't advertise the policy (a refused Steam join finds out at login, after
  connecting); a host's own gameplay add-ons under `cosmetic` are not flagged to joiners.

### Live results (2026-09-25, Proton, local copies, `launch/multi.sh 2`, add-on folders per instance via
`addons_dir=`; test add-ons built with `addon.py pack` under `~/.local/share/b4b-coop/addons-mp/`, screenshots in
`addons-mp/evidence/`, not committed)
- Test content: `walker_checker.pak` = the #18 Walker Elite 00 body/arms/gear textures (checker, blue), cosmetic
  (textures), id 33c6d9d8; `walker_dt_test.pak` = an unmodified copy of `Walker_Customization_DT`, gameplay (data
  table), id 23a4441c.
- Dev build, host without add-ons, client with `walker_checker`: login `?b4bcoop=2?b4bcoopver=0.3.0?b4bcoopaddons=
  1c0g,c33c6d9d8?Name=...`, host `addons: login Hergmgurk: 1c0g,c33c6d9d8`, `/addons players` lists it. Both
  `/model walker_elite_00`: the client sees the host's hero in the checker/blue outfit, the host sees the client's
  hero in the normal outfit (Fort Hope). Mission Evansburgh B (re-login after the server travel carries the summary
  again), `endmission 1`, seamless chapter transition to C: both in C with heroes, still connected, `/addons players`
  still maps the client (same connection). No errors on either side.
- Dev build, host with `walker_checker`, client with `walker_dt_test`: refused with the `cosmetic` message (host log +
  chat line; client `NetworkFailure ... 'Host allows cosmetic add-ons only; ...'`, `auto: join ... stopped`, the game's
  "Unable to join" popup, then chat `Could not join: Host allows ...`). `/addons policy match` -> `Switch off: Walker
  data table test`; `/addons policy any` -> joined, `/addons players`: `0 cosmetic, 1 gameplay / gameplay 23a4441c
  Walker data table test`.
- **Player build** (no-click start: `instance.sh 1 -Port=7787 +b4bcoop_join proto:2 ver:0.3.0 addr:127.0.0.1:7799`,
  client `instance.sh 2 +b4bcoop_join ... addr:127.0.0.1:7787`), host without add-ons, default policy: client with
  `walker_dt_test` refused (`addons: login ... refused: Host allows cosmetic add-ons only; ...`, client `auto: join
  127.0.0.1:7787 stopped`); client restarted with `walker_checker` instead: `addons: login Hergmgurk: 1c0g,c33c6d9d8`,
  `Join succeeded`.
- Offline harness (Proton 10 wine; addons.c + addonclass.c + addons_mp.c with stubbed engine): the four policies
  against 7 summaries (none, empty, cosmetic only, gameplay, unlisted `+n`, junk), `/addons players|policy`, a pak
  whose addoninfo claims `content=cosmetic` for a data table (logged, classified gameplay).
- `tools/e2e.py --quick` checks the summary in the login and `/addons players` on the host.
