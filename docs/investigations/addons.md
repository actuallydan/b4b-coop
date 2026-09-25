# Add-ons: L4D-style loader, format and enable list (#20)

Status 2026-09-25, build 14216215, Proton. Epic #23. Code: `native/src/addons.c` (loader, `/addons`),
`native/src/paks.c` (mount + signature exemptions, see model-mods-paks.md), `tools/modkit/addon.py` (packer).
Player page: docs/COMMANDS.md "Add-ons".

## TL;DR
- Drop-in: `<game>\b4bcoop-addons\<name>.pak` (next to `Back4Blood.exe`, outside `Gobi\Content\Paks`, which the
  engine mounts itself with signature checks). An add-on zip holds `b4bcoop-addons/<name>.pak`: unzip into the game
  folder. Restart to apply; no in-game browser.
- One file per add-on: our pak format (tools/b4bpak.py) with an extra root entry `b4bcoop-addoninfo.txt`
  (title, author, version, category, description). A pak without it still loads (title = file name).
- `addonlist.txt` in the folder: `<file>.pak=1|0`, top to bottom = load order, later wins. New paks are appended,
  switched on, sorted by name; the agent rewrites the file then. `/addons on|off` edits it.
- Conflicts (same file path in two enabled add-ons) and "mixed" packages (a package's .uasset/.uexp/.ubulk ending up
  from different add-ons: can crash) are logged, listed by `/addons`, and one chat notice is shown after the first
  map load.
- Player builds: paks.c installs **no hook** unless at least one add-on will be mounted. Retail paks keep every
  signature check either way.
- Client-side only, no protocol bump.

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
```
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

## 6. For #22 (multiplayer)
- Add-ons are client-side: nothing is sent, no protocol change. Visual-only add-ons are safe to differ between
  players; anything touching gameplay data (collision, hitboxes, data tables) is not.
- #22 can compare the content ids (index SHA1s) of enabled add-ons in the login options (like `?b4bcoop=`), and
  decide per category what must match. `addons.c` already has the list in load order with ids.
