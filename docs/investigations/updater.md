# In-game updater (#34)

The `~` window's **Updates** tab checks GitHub for a release, downloads it, verifies it and swaps the files in; the
new version runs from the next game start. Nothing happens without a click (no background check, no auto-update),
nothing is hot-reloaded. Code: `native/src/updater.c` (HTTP, files, state, tab), `native/src/updcore.c` (portable:
SHA-256, signature, manifest, zip, JSON, versions), netguard scope in `native/src/netguard.c`, signing in
`tools/sign-release.sh` + `.github/workflows/release.yml`. Player docs: `docs/COMMANDS.md` "Updates", README "Update".

## 1. Spike: HTTPS from the agent
- **WinHTTP** (loaded with `LoadLibraryW` on the first request, so no player loads it who never clicks; netguard's
  DLL notification hooks `WinHttpConnect` as it maps). Synchronous calls on a worker thread;
  `WINHTTP_ACCESS_TYPE_DEFAULT_PROXY` (no WPAD lookups); timeouts 15/15/20/30 s.
- Works under Wine and Proton, TLS included, following GitHub's redirect by default (WinHTTP's default policy follows
  redirects except HTTPS → HTTP):
  - System Wine (spike exe `winhttp` GET, 2026-09-27): `api.github.com/.../releases/latest` 200 8053 B 200 ms;
    `github.com/.../releases/download/v0.6.1/SHA256SUMS` 200 373 B, final URL `release-assets.githubusercontent.com/...`;
    the 0.6.1 zip 200 685901 B.
  - Proton in the Flatpak Steam sandbox (lane 2, in the game, dev build): the real check (`update check`) and a real
    asset download through the redirect (`update fetch <github.com download URL>`: 373 B, SHA-256 equal to a
    Linux-side download). Log:
    ```
    netguard: updater scope open (GitHub release hosts allowed)
    netguard: allow http api.github.com via X3DAudio1_7.dll (updater)
    netguard: allow dns api.github.com via WINHTTP.dll (updater)
    netguard: allow tcp 140.82.114.6:443 via WINHTTP.dll (updater)
    update: Release v0.6.1 has no in-game update files (it is older than the updater). Get it from GitHub: ...
    netguard: updater scope closed
    ```
  - Native Windows: not run yet (see §6).
- **GitHub**: `GET https://api.github.com/repos/actuallydan/b4b-coop/releases/latest` (or `/releases/tags/v<ver>` for
  a host's version), headers `User-Agent: b4bcoop-updater/<version>` (GitHub requires one), `Accept:
  application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`. Assets by `browser_download_url`
  (`github.com/<repo>/releases/download/<tag>/<name>` → 302 → `release-assets.githubusercontent.com`, earlier
  `objects.githubusercontent.com`). Unauthenticated limit: 60 API requests/hour per IP (`x-ratelimit-remaining`,
  `x-ratelimit-reset`); a check is 1 API request (asset downloads don't count). 403/429 with remaining 0 → "limit
  used up; try again after HH:MM".
- **netguard scope** (`netguard_updater_scope(1/0)` around each check/download): while open, `api.github.com`,
  `github.com` and `*.githubusercontent.com` pass the WinHttpConnect and DNS hooks (reason `updater`), and the IPs they
  resolve to take TCP (flagged `ok_upd[]`: valid only while the scope is open, forgotten for TCP after). Windows'
  WinHTTP may resolve asynchronously (`GetAddrInfoExW` with OVERLAPPED: nothing remembered), so while the scope is open
  a TCP connect to port 443 made by `winhttp.dll`/`webio.dll` itself also passes (`updater-winhttp`): during those
  seconds another WinHTTP user in the process would pass too (the game's services go through libcurl, blocked by
  name as before). Outside the scope the same download fails:
  `update fetch <url> noscope` → `can't reach github.com (no internet, or blocked)`. Dev builds add the `update_api=`
  host to the scope (127.0.0.1 needs nothing: loopback is always allowed).

## 2. What a release carries (release.yml, from 0.6.2 on)
| Asset | What |
|---|---|
| `b4bcoop-<v>.zip` | the player zip (unchanged) |
| `b4bcoop-update.txt` | the manifest (`launch/package.sh` → `tools/sign-release.sh manifest`): `b4bcoop-update 1`, `version=`, `protocol=`, `zip=`, `size=`, `sha256=` |
| `b4bcoop-update.txt.sig`, `b4bcoop-<v>.zip.sig`, `SHA256SUMS.sig` | ed25519 signatures, 64 raw bytes (`openssl pkeyutl -sign -rawin`) |

The check downloads the manifest + its signature (~300 B): the version and protocol shown in the tab are signed. The
download takes the zip + its `.sig`: size and SHA-256 must equal the signed manifest, and the zip's own signature must
verify, before anything is written. Releases without a manifest (0.6.1 and older) are shown with a link to the
release page.

## 3. Signing
- ed25519. CI signs with OpenSSL 3 (`tools/sign-release.sh sign`), the agent verifies with **Monocypher 4.0.2**
  (`crypto_ed25519_check`, `src/optional/monocypher-ed25519.c`: ed25519 with SHA-512, i.e. RFC 8032, compatible with
  OpenSSL; `native/test/run.sh` signs its fixtures with OpenSSL and checks them with Monocypher). Licence: CC0 or
  BSD-2 (we take CC0: no notice needed). Inflate: **zlib's contrib/puff** (v1.3.1, zlib licence, no notice needed in
  binaries). Both pinned in `tools/fetch-deps.sh` (commit + sha256 of the compiled files), like ImGui.
- Key: generated 2026-09-27 with `openssl genpkey -algorithm ed25519`.
  - Private: `~/.local/share/b4b-coop/keys/release-signing.key` on Dan's machine (chmod 600, dir 700, never committed)
    and the GitHub Actions secret `B4B_RELEASE_SIGNING_KEY` (the PEM). Keep one more offline copy (password manager).
  - Public: `docs/release-signing.pub.pem`, raw `3faca059...789a31` in `native/src/signkeys.h` (`B4B_RELEASE_PUBKEY_BYTES`,
    `RELEASE_PUBKEY` in updater.c). It verifies releases only: the add-on shop has its own key (shop.md §2).
    `ci.yml` checks the player DLL carries the committed key; `release.yml` fails without the secret and verifies its
    own signatures against the committed key (a wrong secret can't publish).
- Humans: `tools/sign-release.sh verify docs/release-signing.pub.pem b4bcoop-<v>.zip` (next to its `.sig`).
- **Key rotation** (planned, key still safe): generate a new key; release N+1 is still signed with the old key but its
  agent contains the new public key (then `B4B_RELEASE_PUBKEY_BYTES` becomes the new one, `docs/release-signing.pub.pem` too);
  update the secret before tagging N+2. Players on N update to N+1 with the old key and from then on accept the new
  key. (Accepting two keys at once is a small change in `upd_sig_ok` callers if ever needed.)
- **Key lost or leaked**: generate a new key, put its public half into the agent, update the secret, release. Players
  can't get that release through the tab (their agent rejects the new signature, which is the point); they update
  once by hand (extract the zip), from then on the tab works again. A leaked key additionally means: say so in the
  release notes; an attacker would still need to get players to download from the attacker (the updater only talks
  to GitHub's release of this repo).

## 4. Applying on restart
- The agent is `Gobi\Binaries\Win64\X3DAudio1_7.dll`, mapped by the running game. Windows refuses to overwrite a
  mapped DLL but allows renaming it (the loader opens images with FILE_SHARE_DELETE); Wine allows both. So the swap
  runs right after the verified download, by renames, and the running game simply keeps its mapped old code:
  1. installable zip entries (`upd_install_path`: `xinput1_3.dll` and `b4bcoop-*.txt` in the root,
     `Gobi/Binaries/Win64/X3DAudio1_7.dll`; never `b4bcoop.ini`, bans, add-ons, logs, anything else) are written to
     `<game>\b4bcoop-update\staged\<ver>\`;
  2. each running file moves to `b4bcoop-update\backup\<running ver>\` (older backups deleted: one is kept);
  3. each staged file moves into place; any failure undoes steps 2-3 ("Your current version is unchanged");
  4. `b4bcoop-update\state.txt`: `installed=<new> previous=<old> pending=1 starts=0 files=<list>`.
  One install or go-back per game session (the tab greys out until a restart).
- Rejected alternatives: swapping at the next start from DllMain can't replace the DLL that is running that DllMain
  (it would take two restarts); the root `xinput1_3.dll` launcher runs only on Windows (Wine ignores it: builtin
  xinput wins), so it can't be the mechanism for Proton/Steam Deck.
- **Start check / automatic revert** (`updater_early`, DllMain right after the log opens and the `-b4bcoop=off`
  check; kernel32 file calls only): if `pending=1` and `installed` is this version, `starts++`. 15 s of game ticks
  later (`updater_tick`) → `pending=0` ("started fine"). If a third start finds `starts` already at 2, the new
  version crashed/hung twice before that: it moves its files to `b4bcoop-update\failed\<ver>\` (renaming its own
  mapped DLL), puts the backup back, writes `result=<new> did not start properly 2 times, so <old> was put back` and
  starts nothing this session; the next start runs the old version, whose tab shows that line. If `installed` differs
  from the running version (someone extracted a zip over it), the pending state is dropped.
- **Go back** (tab, two clicks): the same revert by hand, `result=you went back from X to Y`.
- Not covered: a new DLL that Windows can't load at all (the game wouldn't start; no b4bcoop code runs). Builds come
  from the pinned CI toolchain, so unlikely; recovery by hand: move `b4bcoop-update\backup\<old>\...` back or extract
  a release zip. The Windows launcher could check `state.txt` before starting the game; not done (the launcher
  itself is still unverified on Windows).

## 5. Tests
- **Unit** (`native/test/run.sh`, also in CI): 67 checks. SHA-256 vectors (incl. 1M 'a', 56-byte padding edge), version
  order (pre-releases), install filter (ini/bans/add-ons/paths refused), manifest (+ CRLF, 6 malformed), download
  check: good, **bad hash**, **truncated** (n-1, 0), **bad zip signature**, other key, short signature, edited manifest;
  zip reader: deflate + stored, truncated and corrupted zips refused, `../` entry refused; GitHub JSON (escapes,
  surrogates, nested objects, truncated, error object, the real v0.6.1 reply in `native/test/fixtures/`); OpenSSL-made
  signatures verified by Monocypher.
- **Live** (`tools/update-test.py`, lane 2, Flatpak Steam, 2026-09-27): **39/39 PASS**
  (`/tmp/b4b-update-20260927-152633`). A 0.6.2-test dev build (`B4B_BUILD_VERSION`, `B4B_BUILD_OUT` in
  `native/build.sh`) packed like a release, signed with a throwaway key, served by a fake GitHub API on 127.0.0.1
  (asset links 302 like github.com); the instance uses dev ini `update_pubkey=` + `update_api=` (live). Steps: real
  GitHub check over HTTPS + real asset download through the redirect, the same download blocked outside the scope;
  bad manifest signature / bad zip hash / bad zip signature / truncated zip → refused, all files, both inis, bans,
  add-ons unchanged and no backup/staged folder; good release through the tab's button → new DLLs in place, old one in
  the backup, the zip's ini not installed, both inis and add-ons unchanged, this game still logs 0.6.1; restart →
  `b4bcoop 0.6.2-test (protocol 2)`, `start 1 of the freshly installed`, `started fine`; Go back → restart → 0.6.1;
  install again with dev `update_never_healthy=1` → starts 1 and 2 not healthy, start 3 logs `reverted 0.6.2-test ->
  0.6.1 (0.6.2-test did not start properly 2 times, so 0.6.1 was put back)` and starts nothing, failed DLL in
  `failed\0.6.2-test\`, next start 0.6.1 with the result in `update status`.
- Version-mismatch hint (lane 2, `B4B_INI_EXTRA2=b4bcoop_protocol_override=3`): the refused client logs `update: noted
  the host's version 0.6.1 (protocol 2)`; its chat line gets " Press ~, tab Updates, to get the same version." (shown
  after the next map load; not seen in that short run). The host adds a hint only when the joiner's version is newer.
- e2e `--quick` on lane 2 with this build: 14/14.

## 6. Open
- Native Windows: WinHTTP + schannel, renaming the mapped `X3DAudio1_7.dll` (expected to work: FILE_SHARE_DELETE),
  netguard's async-resolve fallback. Test: any Windows PC with the release after this one: Updates → Check.
- The first real signed release (0.6.2) is the first end-to-end run against GitHub's real assets + real key.
- The modkit zip is out of scope (#34).
