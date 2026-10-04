#!/usr/bin/env python3
"""Source engine character -> rigged glb for the modkit (`b4bmod survivor <glb> --as <name>`). Dev tool for private test
characters from a game you own (e.g. the Left 4 Dead 2 survivors); nothing it extracts or writes is ever committed or
shared.

    tools/modkit/source_survivor.py [--game DIR] [--out DIR] [--sourceio DIR] [--blender EXE] <model> ...

  <model>   path inside the game's VPKs, e.g. models/survivors/survivor_coach.mdl (or a plain name: survivor_coach,
            looked up under models/)
  --game    the Source game folder holding */*_dir.vpk (default: Left 4 Dead 2 in the Steam libraries)
  --out     output folder (default ~/.local/share/b4b-coop/characters/source): <out>/extract/ (the model's files and
            its materials, as in the game) and <out>/glb/<stem>/<stem>.glb (+ <stem>_textures/, the PNGs it embeds)
  --sourceio  the SourceIO Blender add-on folder (default: downloaded once, pinned + SHA-256 checked, into
            ~/.local/share/b4b-coop/deps/sourceio-<ver>/; MIT, https://github.com/REDxEYE/SourceIO)

Steps per model: the .mdl/.vvd/.dx90.vtx/.phy and every material it names (the .mdl's texture dirs x texture names,
then each .vmt's textures, `include` patches followed) are extracted with tools/modkit/vpk.py (the copy the game
loads: update > DLC > base); then headless Blender imports it with SourceIO and tools/modkit/source_survivor_bl.py
writes the glb (plain PBR materials, baked iris UVs, jaw/blink flexes named for the face rigger).
"""
import argparse, hashlib, io, os, re, shutil, struct, subprocess, sys, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vpk  # noqa: E402

DATA = os.path.expanduser("~/.local/share/b4b-coop")
SOURCEIO_VER = "5.5.4"
SOURCEIO_URL = f"https://github.com/REDxEYE/SourceIO/releases/download/{SOURCEIO_VER}/SourceIO.zip"
SOURCEIO_SHA256 = "c6da6d989120b430df0a74315307fc42b8f87fa3c1d98554ee589c7e93f79e4d"


def steam_libraries():
    roots = [os.path.expanduser(p) for p in ("~/.local/share/Steam", "~/.steam/steam",
                                             "~/.var/app/com.valvesoftware.Steam/.local/share/Steam")]
    libs = []
    for r in roots:
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            libs += re.findall(r'"path"\s+"([^"]+)"', open(vdf, errors="replace").read())
        libs.append(r)
    seen, out = set(), []
    for lib in libs:
        lib = os.path.realpath(lib)
        if lib not in seen and os.path.isdir(os.path.join(lib, "steamapps", "common")):
            seen.add(lib); out.append(lib)
    return out


def find_game(name="Left 4 Dead 2"):
    for lib in steam_libraries():
        p = os.path.join(lib, "steamapps", "common", name)
        if os.path.isdir(p):
            return p
    sys.exit(f"source_survivor: {name} not found in the Steam libraries; give --game")


def sourceio_dir():
    base = os.path.join(DATA, "deps", f"sourceio-{SOURCEIO_VER}")
    addon = os.path.join(base, "scripts", "addons", "SourceIO")
    if os.path.exists(os.path.join(addon, "__init__.py")):
        return addon
    print(f"source_survivor: downloading SourceIO {SOURCEIO_VER} ({SOURCEIO_URL})", flush=True)
    data = urllib.request.urlopen(SOURCEIO_URL, timeout=120).read()
    got = hashlib.sha256(data).hexdigest()
    if got != SOURCEIO_SHA256:
        sys.exit(f"source_survivor: SourceIO zip SHA-256 {got}, expected {SOURCEIO_SHA256}")
    tmp = addon + ".part"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        top = z.namelist()[0].split("/")[0]
        z.extractall(tmp)
    shutil.move(os.path.join(tmp, top), addon)
    shutil.rmtree(tmp, ignore_errors=True)
    return addon


# ---------------------------------------------------------------- what a model needs
def mdl_materials(data):
    """(texture dirs, texture names) from a studiomdl header (v44-v49)."""
    def cstr(off):
        return data[off:data.index(b"\0", off)].decode("utf-8", "replace")
    tex_n, tex_off, dir_n, dir_off = struct.unpack_from("<4i", data, 204)
    names = [cstr(tex_off + i * 64 + struct.unpack_from("<i", data, tex_off + i * 64)[0]) for i in range(tex_n)]
    dirs = [cstr(struct.unpack_from("<i", data, dir_off + i * 4)[0]) for i in range(dir_n)]
    return [d.replace("\\", "/").strip("/").lower() for d in dirs], [n.replace("\\", "/").lower() for n in names]


VMT_TEX = re.compile(r'"?(\$[a-z0-9_]+|include)"?\s+"?([^"\s\[\]{}]+)"?', re.I)


def needed(idx, mdl_path):
    v = idx.get(mdl_path)
    if v is None:
        sys.exit(f"source_survivor: {mdl_path} not in the game's VPKs")
    stem = mdl_path[:-4]
    files = [p for p in (stem + e for e in (".mdl", ".vvd", ".dx90.vtx", ".dx80.vtx", ".sw.vtx", ".vtx", ".phy"))
             if p in idx]
    dirs, names = mdl_materials(v.read(mdl_path))
    todo = []
    for n in names:
        for d in dirs:
            p = f"materials/{d}/{n}.vmt".replace("//", "/")
            if p in idx:
                todo.append(p); break
    seen = set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p); files.append(p)
        text = idx[p].read(p).decode("utf-8", "replace")
        for key, val in VMT_TEX.findall(text):
            val = val.replace("\\", "/").lower()
            if key.lower() == "include":
                if val in idx: todo.append(val)
                continue
            for cand in (f"materials/{val}.vtf", f"materials/{val}"):
                if cand in idx and cand not in seen:
                    seen.add(cand); files.append(cand)
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("models", nargs="+")
    ap.add_argument("--game")
    ap.add_argument("--out", default=os.path.join(DATA, "characters", "source"))
    ap.add_argument("--sourceio")
    ap.add_argument("--blender", default=os.environ.get("B4B_BLENDER") or shutil.which("blender") or "blender")
    a = ap.parse_args()
    game = a.game or find_game()
    addon = os.path.abspath(a.sourceio) if a.sourceio else sourceio_dir()
    # BLENDER_USER_SCRIPTS must be the folder holding addons/SourceIO
    scripts = os.path.dirname(os.path.dirname(addon))
    if os.path.basename(addon) != "SourceIO" or os.path.basename(os.path.dirname(addon)) != "addons":
        sys.exit("source_survivor: --sourceio must be a folder .../addons/SourceIO")
    idx = vpk.index(vpk.open_all(game))
    extract = os.path.join(a.out, "extract")
    rc = 0
    for m in a.models:
        p = m.lower().replace("\\", "/")
        if not p.endswith(".mdl"):
            p += ".mdl"
        if p not in idx:
            hits = [k for k in idx if k.startswith("models/") and k.endswith("/" + os.path.basename(p))]
            if len(hits) != 1:
                sys.exit(f"source_survivor: {m}: {'not found' if not hits else 'ambiguous: ' + ', '.join(hits)}")
            p = hits[0]
        files = needed(idx, p)
        for f in files:
            dst = os.path.join(extract, f)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(idx[f].read(f))
        print(f"source_survivor: {p}: {len(files)} files -> {extract}", flush=True)
        stem = os.path.basename(p)[:-4]
        out = os.path.join(a.out, "glb", stem, stem + ".glb")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if os.path.exists(out):
            os.remove(out)
        cmd = [a.blender, "-b", "--factory-startup", "--python", os.path.join(HERE, "source_survivor_bl.py"), "--",
               os.path.join(extract, p), out]
        r = subprocess.run(cmd, env=dict(os.environ, BLENDER_USER_SCRIPTS=scripts), capture_output=True, text=True)
        for line in r.stdout.splitlines():
            if line.startswith("source_survivor:") or "Error" in line:
                print("  " + line, flush=True)
        if r.returncode != 0 or not os.path.exists(out):
            print(r.stdout[-3000:], r.stderr[-3000:], sep="\n")
            print(f"source_survivor: {p}: FAILED", flush=True)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
