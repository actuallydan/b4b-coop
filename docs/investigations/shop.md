# Add-on shop: the `~` window's Browse tab (#36)

Status 2026-09-27, build 14216215, Proton (lane 2, Flatpak Steam). Code: `native/src/shop.c` (tab, downloads, pending
changes), `native/src/updcore.c` (catalog JSON + signature, streaming SHA-256; unit tests `native/test/run.sh`),
`native/src/addons.c` (`addons_add_runtime`), `native/src/paks.c` (runtime mount), `native/src/imgdecode.c`
(thumbnails), `native/src/overlay.cpp` (`ov_texture`/`ov_image`), `tools/shop-catalog.py` (build + sign the catalog),
`tools/shop-test.py` (live test). Player page: docs/COMMANDS.md "Browse (add-on shop)".

## TL;DR
- A "Workshop"-like Browse tab: **Get the add-on list** (a click; nothing automatic) downloads `catalog.json` + its
  ed25519 signature (the b4bcoop release key), shows the add-ons with thumbnail, license, author, kind, size, what
  they add/replace, search and a filter. **Add** downloads the pak, checks size + SHA-256 against the signed list,
  puts it into `b4bcoop-addons\<id>.pak`, appends it to `addonlist.txt` and mounts it **right away** when that is
  safe. **Remove** switches it off and deletes it at the next start. **Update** (newer file in the list) is swapped in
  at the next start. `shop=0` hides the tab.
- **Live mounting works** (§1) for add-ons whose files are all new to the game and that are cosmetic: added outfits
  and weapon looks are usable with `/model` right after Add, in Fort Hope and in a running mission. Anything that
  replaces an existing file (a retail asset or another add-on's) waits for a restart; the agent decides from the
  files, not from the catalog. Mounted paks are never unmounted.
- Thumbnails are reasonable (§3): stb_image (PNG + JPEG only), bytes decoded only after their SHA-256 matched the
  signed list, scaled to 96 px, uploaded through ImGui 1.92's own texture list (no D3D12 code of ours).
- Privacy: nothing about installed add-ons is sent to anyone. GitHub sees the catalog fetch and each download (like
  any download from GitHub); other players see nothing (the #35 rules are unchanged).

## 1. Spike: mounting at runtime
`FPakPlatformFile::Mount` (0x143913E60) on the game thread, the same call the Initialize hook uses at start, with our
three signature exemptions. Results on the dev build (`tools/shop-test.py`, §5):

| Case | Result |
|---|---|
| New-path add-on (outfit, `Gobi/Content/b4bcoop/outfits/casual_joe/...`) in Fort Hope | `addons: casual_joe.pak -> mounted (read order 1000, at runtime)`; `/model casual_joe` right after: `models: hero slot N wears outfit casual_joe`, the outfit on the hero in 3P (meshes load through the new pak) |
| New-path weapon look (`ak47`) during a mission (match running) | mounted at runtime, `/model ak47` → `your AR02 now looks like AK-47`; game alive and playing 20 s later |
| Replacement (`holly_green`: `UI/Textures/.../Image_Holly_PartyPortrait`) | not mounted live (by rule): `applies after a restart (it replaces files of the game or of another add-on)`; mounted at the next start |
| Removal | never live: switched off + deleted by the next start, before anything is mounted |

Why replacements wait for a restart (not "after a map change"): a package already loaded keeps its objects; a later
load of the same path would come from the new pak, but its bulk data does not: textures stream mips from `.ubulk`
long after the `.uasset` was read, by offsets from the package that was loaded. Mounting a replacement under a loaded
texture mixes the old package's offsets with the new file (the "mixed package" crash addons.md warns about, created
live). A map change doesn't reliably unload such packages (UI textures, hero assets are kept). So the rule is simple
and safe: only a pak whose **every file path is new** (`FileExists` on the pak layer, vtable slot 14, is false for
all of them) and that is **cosmetic** is mounted live; nothing loaded can refer to a path that didn't exist.
`addons_add_runtime()` checks that; the catalog's `adds`/`replaces` are only shown to players. As a client in
someone else's session nothing is mounted live either (the host's `addons_policy` was checked against the add-ons
mounted at join, #35): it applies at the next start.

Visual check of a live-mounted replacement after a map change (dev `mountpak holly_green.pak` in a mission, then
`/restart`): not observed, the random bot team had no Holly (screenshots in the scratch folder only). The rule above
doesn't depend on it.

Hooks: player builds used to install no pak hook at all without add-ons. With the shop on (default) they now
install only the `Initialize` hook (it just remembers the `FPakPlatformFile`); the two signature exemptions
(GetPakSignatureFile, the precacher's chunk check) go in with the first runtime mount (MinHook at runtime, game
thread). Dev builds do the same (`mountpak` installs them on first use), so the dev test exercises the player path.
`shop=0` or `addons=0` restores the old behavior (no hook without add-ons).

Outfits and weapon looks of a runtime add-on: `models_outfits_refresh()` / `wlooks_refresh()` append the new
entries (existing ones, with their loaded meshes, stay as they are).

## 2. Catalog format and trust chain
One JSON file, `catalog.json`, with a detached signature `catalog.json.sig` (64 raw bytes, ed25519 over the exact
bytes, `tools/sign-release.sh sign`), fetched from
`https://raw.githubusercontent.com/actuallydan/back4blood-shop/main/catalog.json` (+ `.sig`; raw.githubusercontent.com
is inside netguard's updater scope; no API call, so no 60/hour limit).
```json
{"b4bcoop-shop": 1, "updated": "2026-09-27", "addons": [
 {"id": "casual_joe", "name": "Casual Joe", "author": "...", "license": "CC0-1.0", "license_url": "https://...",
  "version": "1.0", "class": "cosmetic", "kinds": "textures, materials, meshes",
  "adds": ["outfit casual_joe (mom)"], "replaces": [], "description": "...",
  "size": 83436658, "sha256": "<64 hex>", "content_id": "<pak index SHA1>",
  "url": "https://github.com/actuallydan/back4blood-shop/releases/download/casual_joe-v1.0/casual_joe.pak",
  "thumb": "https://raw.githubusercontent.com/actuallydan/back4blood-shop/main/thumbs/casual_joe.png",
  "thumb_sha256": "<64 hex>", "min_b4bcoop": "0.8.0"}]}
```
- Trust: release key → catalog signature → each pak's and thumbnail's SHA-256. The download host doesn't matter for
  integrity (a pak that doesn't match is deleted: "Download damaged ... Nothing was changed.").
- Parser rules (`upd_catalog_parse`): format > 1 → "needs a newer b4bcoop"; entries missing id/name/license/url/size/
  sha256, bad id (`[a-z0-9][a-z0-9_-]{0,31}`, it becomes the file name `<id>.pak`), non-https URL (http only to
  127.0.0.1, and the downloader refuses plain HTTP in player builds), duplicates → skipped and counted; a thumbnail
  without its SHA-256 is dropped; unknown keys ignored. Limits: catalog 4 MB, 256 entries, pak 1 GB, thumbnail 1 MB.
- `min_b4bcoop`: Add is greyed out with "needs b4bcoop X or newer (Updates tab)".
- Stale-catalog replay (an old signed list served again) is possible in principle but only through GitHub's TLS
  endpoints; the tab shows `updated`.
- Key: the task asked for the existing release key; the agent keeps it as its own constant use (`updater_release_pubkey`)
  so a separate shop key is a one-line change. Open question for Dan: the shop repo's workflow would need the release
  secret, i.e. anyone who can change that repo's workflows could sign b4bcoop releases. A separate shop key (public
  half built into the agent next to the release key) avoids that.

### Shop repo workflow (design; the repo doesn't exist yet)
- Layout: `shop.json` (per add-on: id, pak file or release asset, license, license_url, author, thumb, min version;
  format in `tools/shop-catalog.py`), `thumbs/`, `catalog.json` + `.sig` (generated, committed).
- An add-on = one GitHub release `<id>-v<version>` with `<id>.pak` as its asset (publicly downloadable; the modkit's
  `b4bmod pack` output).
- Workflow on `release: published` and on push to `shop.json`: download the release paks, `tools/shop-catalog.py build
  shop.json -o catalog.json` (computes size/SHA-256/content id/class/adds/replaces from each pak; refuses unknown or
  non-public licenses, bad ids, over-1 MB thumbnails), `shop-catalog.py sign "$KEY" catalog.json` with the key from a
  secret, `verify` against the committed public key (like release.yml), commit `catalog.json` + `.sig`.
- Review: new add-ons arrive as PRs to `shop.json` (license check by a human; the tool only checks the string).

## 3. Thumbnails
- Decoder: stb_image v2.30 (public domain), pinned in `tools/fetch-deps.sh` (commit 2c980bb, sha256 of
  `stb_image.h`), compiled with `STBI_ONLY_PNG`, `STBI_ONLY_JPEG`, no stdio/HDR, `STBI_MAX_DIMENSIONS 2048`. Its
  input is only bytes whose SHA-256 the signed list names, so a hostile picture needs the signing key.
- Box-filtered to fit 96×96 (36 KB RGBA each), at most 128 per session, cached by SHA-256 across list refreshes.
- Upload: ImGui 1.92's texture system: `ov_texture()` creates an `ImTextureData` (RGBA32), `RegisterUserTexture`;
  the DX12 backend creates and uploads it inside the next `RenderDrawData` on the render thread (the overlay's
  snapshot already carries `Textures`). Textures are never destroyed during the session (a frame in flight could
  still use one); the SRV heap went from 64 to 256 slots, `OV_MAX_TEX` = 160. Worst case ~6 MB of GPU memory.
- Live: PNG and JPEG thumbnails `uploaded` in the tab (screenshot §5).

## 4. Files, next start, updates
- `b4bcoop-addons\<id>.pak` (the add-on), `b4bcoop-addons\.shop\` (hidden): `<id>.pak.part` while downloading
  (deleted on any failure), `<id>.pak.new` (a downloaded update), `pending.txt` (`remove <file>` / `update <file>`).
- `shop_early()` runs from `addons_scan()` in DllMain before `addonlist.txt` is read: deletes removed paks (their
  addonlist line is dropped), renames `.new` over the old pak, deletes `pending.txt`. Only `<shop id>.pak` names are
  accepted. Undo before the restart rewrites `pending.txt` (and switches the add-on back on).
- Installed state: the file `<id>.pak` in the folder; its SHA-256 (computed after the list arrives) against the list
  tells "installed" from "update available". The row shows the Add-ons tab's state (`on`, `on after restart`, ...).

## 5. Tests
- Unit (`native/test/run.sh`): 84 checks (+17): streaming SHA-256 against one-shot, catalog parse (skipped entries,
  lists, thumbnails without hash, truncated JSON, no format, newer format), ids, Monocypher- and OpenSSL-signed
  catalogs, an edited byte refused.
- Live (`B4B_LANE=2 B4B_STEAM=flatpak B4B_GPU=4090 tools/shop-test.py`, dev build, one instance, local catalog on
  127.0.0.1 signed with a throwaway key, own `addons_dir=`): **32/32** (before and after merging #35;
  `/tmp/b4b-shop-20260927-171732`, screenshots kept in `~/.local/share/b4b-coop/shop-test/evidence/`): no add-ons
  folder at start; bad catalog signature → "Not shown: the add-on list's signature is not valid"; 4 add-ons listed,
  PNG + JPEG thumbnails uploaded; `needs b4bcoop 9.0.0 or newer`; damaged pak → "Download damaged (its SHA-256 does
  not match the signed list). Nothing was changed.", truncated → "Download incomplete (4183199 of 12549599 bytes)",
  nothing left behind; Add casual_joe (the tab's button) in Fort Hope → mounted at runtime, worn with `/model`;
  holly_green → "applies after a restart (it replaces files of the game or of another add-on)", not mounted; in a
  running mission Add ak47 → mounted, `your AR02 now looks like AK-47`, playing 20 s later; Remove ak47 (two clicks)
  → switched off; restart → `shop: removed ak47.pak`, gone from addonlist.txt, holly_green + casual_joe mounted at
  start; a newer holly_green in the list → Update → `.shop\holly_green.pak.new` → restart → `shop: updated
  holly_green.pak`, the new file in place.
- `tools/e2e.py --quick` (lane 2, Flatpak Steam) with this build after merging main: 14/14.
- Test paks (never committed): `~/.local/share/b4b-coop/shop-test/src/` (casual_joe = CC0 test outfit, ak47 = the CC0
  loafbrr AK, holly_green/magenta = the #17 portrait tests).

## 6. Open
- Player build not driven live (no command server): the Initialize-only hook path is the same code as the dev build
  now; a player-build smoke test (Browse → Add with the real catalog) comes with the real shop repo.
- Native Windows: WinHTTP to raw.githubusercontent.com, renames in the add-ons folder while a pak is mounted (the
  engine keeps pak handles open: Remove/Update never touch a mounted file before the next start).
- No real catalog yet (the shop repo). A separate shop signing key (§2) is Dan's call.
