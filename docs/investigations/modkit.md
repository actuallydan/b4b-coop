# Mod maker's kit as its own deliverable (#21, epic #23)

Status 2026-09-25, build 14216215. Code: `modkit/` (author docs: `modkit/README.md`, `modkit/docs/`). Branch
`models-modkit`.

## Decisions (Dan)
- Mod tooling is separate from what players use: nothing of it in the player zip or the agent (`dumpassets` stays
  dev-only). Own folder `modkit/`, own README, own zip `dist/modkit/b4bcoop-modkit-<version>.zip`
  (`modkit/package.sh`, reproducible; release.yml uploads it next to the player zip).
- Offline extraction for modders = CUE4Parse (pakx), not the game-side `dumpassets`.
- No third-party piece bundled; the README says how to get each one. The pak AES key is built into `b4bmod.py` (Dan: it is public and already in git history).

## Findings
- **CUE4Parse from NuGet** (`CUE4Parse` 1.2.2.202609, Apache-2.0) reads B4B (`GAME_Back4Blood`); no source clone
  needed. It pulls `Microsoft.Bcl.Memory` 9.0.0 (GHSA-73j8-2gch-69rq): pinned 9.0.20 in `pakx.csproj`.
- **No proprietary Oodle needed.** CUE4Parse's default Oodle decompressor is `OodleSharp` 0.0.1 (NuGet, MIT, a managed
  port by NotOfficer). `pakx extract 'Heroes/Walker/'` without a native library: 801 files, 1.82 GB, byte-identical
  to the run with `liboodle-data-shared.so` (`diff -r`), and the 453 files also in the `dumpassets` dump are
  identical to it. Speed: 15.8 s managed vs 3.2 s native (1.8 GB). Dan's call: open source only, so the native
  option was removed (pakx never calls `OodleHelper.Initialize`, which would **download** the library from the
  OodleUE GitHub release when given a missing path, as FModel does). The kit never uses or downloads Oodle.
- **Key check**: `pakx key` decrypts every encrypted index (AES-256-ECB, footer offset/size at +53/+45) and compares
  SHA1 with the footer (+25): 61/61 OK in 0.07 s; a key with one changed byte -> `wrong AES key: it does not decrypt
  pakchunk0-WindowsNoEditor.pak`. `list`/`extract` run the same check first. The key is built in (`AES_KEY` in
  `b4bmod.py`); `--aes-key`, `B4B_AES_KEY` or `b4bmod.ini` (`config aes_key`, stored only after the check) override
  it if a game update changes it. `modkit/package.sh` refuses any other 64-hex string.
- `find` now lists through pakx (no Python `cryptography` needed): 289,479 files, cache per pak set
  (`listing/files-<sha1 of pak names+sizes>.tsv`).
- UAssetAPI: GitHub archive of commit 3228c1e (83 MB, SHA-256 `3c044cc8…4e92f`) instead of `git clone` (Windows
  modders may have no git). Its csproj packs `../README.md`: the top-level files must be kept (NU5019 otherwise).
- Windows under Wine: Windows Python 3.12 runs `b4bmod.cmd` (no `py` -> `python` fallback), status/config/mesh/
  pack/install/check with `C:\` and `Z:\` paths; its pak is byte-identical to the Linux one. .NET 10 does not start
  under Wine 11.12 or Proton Experimental (`Could not load file or assembly ...System.Runtime.dll. Module not found`;
  the SDK muxer's `--list-sdks` works; MSBuild also needs `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` there for ICU),
  so the .NET tools and the PowerShell .NET install are untested on Windows.
- FBX: `skmgltf.py import` converts FBX/OBJ/DAE/.blend itself (headless Blender, `blender/b4bfit.py convert`);
  b4bmod only checks that Blender is there and passes its `config blender` path as `B4B_BLENDER` (its own FBX->glTF
  fallback is gone). DAE only with Blender 4.x (5.x has no Collada importer). Blender round trip glb -> fbx -> glb of the blocky FP arms: joints 0.000 cm off the bind pose, import OK.
- `mesh import` defaults `--lods` to the template's LOD count (3P hero: 5), since low settings draw LOD1+.
- `tree` extracts what it references, a level per round (AR01: 12 MIs, then 38 textures/parents, 128 MB, 4 s).

## Layout
`modkit/b4bmod.py` (entry), `b4bmod.cmd`, `b4bmod.sh`, `addon.py`, `b4bpak.py` (moved from tools/; `tools/b4bpak.py`
forwards), `dotnet/b4bmod`, `dotnet/pakx`, `docs/`, `package.sh`, and the mesh tools (`skm.py`, `skmgltf.py`,
`upkg.py`, `sm.py`, `b4bmodel.py`, `blender/`; moved from `tools/modkit/` after models-fullmodel). Dev-only
`pakscan.py`, `customversions.py`, `assetcheck/`, `uassetrt/`, `testassets/` stay in `tools/modkit/`.
Deps: `<kit>/deps` (zip) or the repo's `vendor/` (dev). Data: `~/.local/share/b4b-coop/`, Windows
`%LOCALAPPDATA%\b4b-coop\` (`B4B_MODKIT_DATA`).

## Full-model pipeline in the kit (2026-09-25, models-modkit2)
`b4bmod survivor|weapon <model> ... -o <moddir>` = extract each template (`--outfit --fp --fp-mesh --3p-mesh --static
--mag-static`) plus what it references (the `tree` loop), `b4bmodel.py` with `--src` and `B4B_BLENDER`/`B4B_GAME`/
`B4B_AES_KEY` from b4bmod's settings, then `addon.py pack` (`<moddir>.pak`, `--pak`, `--zip`) and `--install`;
`b4bmod model ...` = b4bmodel.py as is. Checked from a fresh kit zip (fresh `B4B_MODKIT_DATA`, empty extract folder,
`setup` fetching UAssetAPI): the MakeHuman survivor (Mom Elite 04, 37 files, id `ea2589b7`) and the CC0 AK on AR02
(136 files, id `5d03441f`) paks are **byte-identical** to `tools/modkit/b4bmodel.py` before the move (itself identical
to the models-fullmodel outputs); `check`, `install`/`--install` (scratch game folder), `mesh import` of an FBX OK.

## Open
- A Windows run of `setup` and the .NET tools.
- Not live-tested as a whole in game: the kit's output is the same bytes the verified tools produce (texture/MI/mesh
  writers unchanged; extraction identical to `dumpassets`).
