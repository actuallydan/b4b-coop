#!/usr/bin/env python3
"""b4bmodel: a modder's model (FBX, glTF, OBJ, .blend) -> a Back 4 Blood survivor outfit or weapon, cooked, textured,
ready for `addon.py pack`. Mod makers run it as `b4bmod survivor|weapon ...` (which also extracts the templates, packs
and installs); guide: docs/meshes.md. How it works: docs/investigations/mesh-mods.md §6 (b4b-coop repository).

  b4bmodel.py survivor <model> --outfit <3P outfit SKM> [--fp <FP arms SKM>] -o <moddir>
        [--slot MAT=SLOT]... [--tex MAT=<file prefix|dir>]... [--lods 1,0.5,0.3,0.15,0.06] [--fp-lods 1,0.5]
        [--bonemap map.json] [--drop REGEX] [--weights source|transfer] [--twist template|none]
  b4bmodel.py weapon <model> --fp-mesh <FP weapon SKM> [--3p-mesh <3P weapon SKM>] [--static <SM>]... -o <moddir>
        [--slot MAT=SLOT]... [--tex MAT=...]... [--forward +x] [--up +z] [--scale fit|F] [--part REGEX=BONE]...
        [--mag-static <SM>] [--lods 1,0.5] [--skins retarget|keep]
  b4bmodel.py textures <manifest.json> --mesh <SKM> -o <moddir>      (the texture step alone)

  common: --src <extract folder> (where the game's files were extracted: b4bmod extract ...), --work <dir>,
          --normal-dx (the model's normal maps are DirectX style; default: OpenGL/glTF style, green flipped),
          --quality fast|balanced|best (texture encoder), --keep-work

<SKM>/<SM> = a game path (/Game/.../3P_Mom_Elite_04_SKM) or a .uasset file. What it does:
  1. exports the template meshes to glTF (skmgltf.py export),
  2. fits the model onto them in Blender (blender/b4bfit.py: skeleton fit, weights, slots, atlases, LODs),
  3. cooks the meshes (skmgltf.py import, sm.py import for static meshes) into <moddir>/Gobi/Content/...,
  4. builds the textures of every texture set it used from the model's images (base colour, normal, roughness,
     metallic, AO; missing channels filled from the retail texture's average) and encodes them into the template's own
     texture packages with `b4bmod texture` (only textures in the template's own folder are replaced; shared ones such
     as micro-detail maps are never touched).
Blender: B4B_BLENDER=<path> or `blender` on PATH. Texture encoding: b4bmod.py next to this file (needs `b4bmod setup`).
"""
import json, math, os, re, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import skm, skmgltf, upkg

FIT = os.path.join(HERE, "blender", "b4bfit.py")


def find_b4bmod():
    p = os.path.join(HERE, "b4bmod.py")
    return p if os.path.exists(p) else None


def die(msg):
    print(f"b4bmodel: {msg}", file=sys.stderr)
    raise SystemExit(1)


def log(*a):
    print("b4bmodel:", *a, flush=True)


class Opts:
    def __init__(self, argv, multi=("slot", "tex", "part", "static", "fp_slot")):
        self.pos, self.o = [], {k: [] for k in multi}
        i = 0
        while i < len(argv):
            a = argv[i]
            if a in ("-o", "--out"):
                self.o["out"] = argv[i + 1]; i += 2
            elif a.startswith("--") and a[2:].replace("-", "_") in ("normal_dx", "keep_work"):
                self.o[a[2:].replace("-", "_")] = True; i += 1
            elif a.startswith("--"):
                k = a[2:].replace("-", "_")
                if k in multi: self.o[k].append(argv[i + 1])
                else: self.o[k] = argv[i + 1]
                i += 2
            else:
                self.pos.append(a); i += 1

    def get(self, k, d=None): return self.o.get(k, d)
    def __getitem__(self, k): return self.o[k]


def src_dir(o):
    return o.get("src") or os.environ.get("B4B_EXTRACT") or os.path.join(os.path.expanduser("~"), ".local", "share",
                                                                        "b4b-coop", "extract")


def asset_file(asset, src):
    if asset.endswith(".uasset") and os.path.exists(asset): return os.path.abspath(asset)
    f = upkg.game_path_to_file(asset if asset.startswith("/Game/") else "/Game/" + asset.lstrip("/"), src)
    if not f or not os.path.exists(f):
        die(f"{asset}: not extracted under {src} (b4bmod extract '{asset}' first, or --src <folder>)")
    return f


def out_file(asset_path, moddir):
    gp = upkg.file_to_game_path(asset_path)
    return os.path.join(moddir, "Gobi", "Content", *gp[len("/Game/"):].split("/")) + ".uasset"


def blender():
    return os.environ.get("B4B_BLENDER") or shutil.which("blender") or die("Blender not found: set B4B_BLENDER")


def run_blender(args):
    cmd = [blender(), "-b", "--factory-startup", "--python", FIT, "--"] + args
    r = subprocess.run(cmd, capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith("b4bfit:") or "Error" in line or "Traceback" in line: print("  " + line)
    if r.returncode or "Traceback" in r.stdout + r.stderr:
        sys.stderr.write(r.stdout[-4000:] + r.stderr[-4000:])
        die("Blender step failed: " + " ".join(args[:1]))


def inspect(model, work):
    out = os.path.join(work, "inspect.json")
    run_blender(["inspect", os.path.abspath(model), out])
    return json.load(open(out))


def template_glb(skm_file, work):
    out = os.path.join(work, os.path.basename(skm_file)[:-7] + ".glb")
    if not os.path.exists(out):
        skmgltf.export(skm_file, out)
    return out


# ---- materials of a template mesh -----------------------------------------------------------------------------------

def mi_chain(obj_path, src):
    """Texture/scalar parameters of a material instance, resolved up the parent chain as far as it is extracted.
    Returns (textures {param: path}, master path)."""
    tex, master = {}, None
    seen = 0
    while obj_path and seen < 10:
        f = upkg.game_path_to_file(obj_path, src)
        if not f or not os.path.exists(f):
            master = obj_path; break
        try:
            parent, t, s_, v = upkg.read_material_instance(f)
        except StopIteration:                      # a Material (master), not an instance
            master = obj_path; break
        for k, x in t.items(): tex.setdefault(k, x)
        obj_path = parent; seen += 1
    return tex, master


def mesh_slots(skm_file, src, static=False):
    """[(slot name, MI path, {param: texture}, master)] of a skeletal (or static) mesh."""
    if static:
        import sm
        s = sm.StaticMesh(skm_file)
        mats = s.m["static_materials"]
    else:
        s = skm.SkeletalMesh(skm_file)
        mats = s.m["materials"]
    out = []
    for m in mats:
        mi = s.pkg.obj_path(m["material"])
        tex, master = mi_chain(mi, src)
        out.append((s.name(m["slot_name"]), mi, tex, master))
    return out


def basecolor_of(tex):
    for k, v in tex.items():
        if re.search(r"base ?colou?r|base surface|albedo|diffuse", k, re.I): return v
    return None


def owned_prefix(skm_file):
    gp = upkg.file_to_game_path(skm_file)
    folder = gp.rsplit("/", 1)[0]
    if folder.endswith("/Meshes"): folder = folder[:-len("/Meshes")]
    return folder + "/"


# ---- textures -------------------------------------------------------------------------------------------------------

def role_of(param):
    p = param.lower()
    if "detail" in p or "microtile bc" in p: return None
    if re.search(r"base ?colou?r|base surface|albedo|diffuse", p): return "basecolor"
    if "normal" in p: return "normal"
    if re.fullmatch(r"pbr( map)?", p): return "pbr"
    if "microtile" in p and "mask" in p: return "zero"
    return "mean"


def srgb_to_linear(c): return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
def linear_to_srgb(c): return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


class TexTool:
    """Collects texture jobs (compose in Blender, encode with b4bmod texture)."""
    def __init__(self, o):
        self.b4bmod = find_b4bmod() or die(f"b4bmod.py not found next to {__file__} (texture encoding)")
        self.src = src_dir(o)
        self.moddir = o["out"]
        self.quality = o.get("quality", "balanced")
        self.normal_dx = bool(o.get("normal_dx"))
        self.work = o["work"]
        self.jobs = []
        self.done = set()

    def retail_png(self, tex_file):
        png = os.path.join(self.work, "retail_" + os.path.basename(tex_file)[:-7] + ".png")
        if not os.path.exists(png):
            for mip in (4, 3, 2, 1, 0):
                r = subprocess.run([sys.executable, self.b4bmod, "export", tex_file, png, "--mip", str(mip),
                                    "--src", self.src], capture_output=True, text=True)
                if r.returncode == 0 and os.path.exists(png): break
        return png if os.path.exists(png) else None

    def compose_set(self, set_info, params, owned):
        """params: {param name: texture path} of the slot's MI chain. set_info: manifest set (grid, tiles)."""
        tiles = set_info["tiles"]
        g = set_info.get("grid", 1)
        base = 1024
        for t in tiles:
            for k in ("basecolor", "normal"):
                p = t["textures"].get(k)
                if p and os.path.exists(p): base = max(base, png_size(p))
        size = min(4096, 1 << math.ceil(math.log2(max(256, base * g))))
        for param, tex in params.items():
            if tex in self.done or not tex.startswith(owned): continue
            role = role_of(param)
            if role is None: continue
            f = upkg.game_path_to_file(tex, self.src)
            if not f or not os.path.exists(f): continue
            out = os.path.join(self.work, os.path.basename(f)[:-7] + ".png")
            self.jobs.append({"out": out, "size": size if role in ("basecolor", "normal", "pbr") else 256,
                              "role": role, "tiles": tiles, "mean_from": self.retail_png(f),
                              "normal_dx": self.normal_dx, "asset": f, "path": tex})
            self.done.add(tex)

    def run(self):
        if not self.jobs: return
        jp = os.path.join(self.work, "texture_jobs.json")
        json.dump(self.jobs, open(jp, "w"), indent=1)
        run_blender(["compose", jp])
        for j in self.jobs:
            r = subprocess.run([sys.executable, self.b4bmod, "texture", j["asset"], j["out"], "-o", self.moddir,
                                "--quality", self.quality, "--src", self.src], capture_output=True, text=True)
            if r.returncode:
                sys.stderr.write(r.stdout + r.stderr); die(f"texture encoding failed for {j['path']}")
            log(f"  {j['path'].split('/')[-1].split('.')[0]}: {j['role']} {j['size']}x{j['size']}")
        self.jobs = []


def png_size(p):
    """Width of a PNG/JPEG without an image library (PNG IHDR; others: assume 2048)."""
    with open(p, "rb") as f:
        h = f.read(24)
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        import struct
        return max(struct.unpack(">II", h[16:24]))
    return 2048


def textures_for(manifest, mesh_file, tt, static=False):
    slots = {s: (mi, tex, master) for s, mi, tex, master in mesh_slots(mesh_file, tt.src, static)}
    owned = owned_prefix(mesh_file)
    for set_name, info in manifest["sets"].items():
        if set_name not in slots:
            log(f"  texture set {set_name}: no slot of that name in {os.path.basename(mesh_file)}"); continue
        mi, params, master = slots[set_name]
        log(f"texture set {set_name} ({mi.split('.')[-1]}): tiles {[t['material'] for t in info['tiles']]}")
        tt.compose_set(info, params, owned)
    tt.run()


# ---- survivor -------------------------------------------------------------------------------------------------------

def fit_args(o, keys=("bonemap", "drop", "weights", "twist")):
    a = []
    for k in keys:
        if o.get(k): a += ["--" + k, o[k]]
    for t in o.get("tex", []): a += ["--tex", t]
    return a


def survivor(o):
    model = o.pos[0]
    src = src_dir(o.o)
    work = o.o["work"]
    moddir = o.o["out"]
    tp = asset_file(o.get("outfit") or die("--outfit <3P outfit SKM> is required"), src)
    fp = asset_file(o["fp"], src) if o.get("fp") else None
    info = inspect(model, work)
    mats = [m for m in info["materials"] if m]
    log(f"model: {len(info['objects'])} objects, materials {mats}, "
        f"{sum(len(a['bones']) for a in info['armatures'])} bones")
    s3 = mesh_slots(tp, src)
    slot_names = [s for s, *_ in s3]
    user = dict(x.split("=", 1) for x in o.o["slot"])
    slot3 = {}
    for m in mats:
        s = user.get(m) or next((x for x in slot_names if x.lower() == m.lower()), None)
        if s is None:
            die(f"material {m!r}: say which slot of {os.path.basename(tp)} it goes to: --slot {m}=<slot> "
                f"(slots: {slot_names})")
        slot3[m] = s
    bc3 = {s: basecolor_of(tex) for s, mi, tex, master in s3}
    master3 = {s: master for s, mi, tex, master in s3}
    # texture set of a 3P slot = the slot itself, unless another slot samples the same base colour texture
    set_of_slot3 = {}
    for s in slot_names:
        set_of_slot3[s] = next((x for x in slot_names if bc3[x] and bc3[x] == bc3[s]), s)
    atlas = {}
    for m, s in slot3.items():
        atlas.setdefault(set_of_slot3[s], []).append(m)
    fp_slot, fp_slotset = {}, {}
    if fp:
        sf = mesh_slots(fp, src)
        fuser = dict(x.split("=", 1) for x in o.o["fp_slot"])
        for s, mi, tex, master in sf:
            bc = basecolor_of(tex)
            st = next((x for x in slot_names if bc and bc3[x] == bc), s)
            fp_slotset[s] = set_of_slot3.get(st, st)
        for m, s in slot3.items():
            if m in fuser:
                fp_slot[m] = fuser[m]; continue
            # same master material as in 3P (skin -> the FP skin slot, cloth -> the FP cloth slot)
            cands = [x for x, mi, tex, master in sf if master == master3[s]] or [sf[0][0]]
            fp_slot[m] = cands[0]
        # which materials survive in first person (faces skinned to the arms): only those need the FP texture set
        dp = os.path.join(work, "fpprobe")
        run_blender(["character", "--template", template_glb(fp, work), "--source", os.path.abspath(model), "--out",
                     dp, "--mode", "fp", "--probe", "1"] + fit_args(o.o))
        keep = set(json.load(open(os.path.join(dp, "probe.json")))["materials"])
        fp_slot = {m: s for m, s in fp_slot.items() if m in keep}
        for m, s in fp_slot.items():
            st = fp_slotset[s]
            if m not in atlas.setdefault(st, []): atlas[st].append(m)
        log(f"FP slots: {fp_slot}; FP texture sets: {fp_slotset}")
    atlas_args = []
    for st, ms in atlas.items():
        if len(ms) > 1: atlas_args += ["--atlas", f"{st}=" + ",".join(ms)]
    log("texture sets:", {k: v for k, v in atlas.items()})
    slotset3 = [x for s in slot_names if set_of_slot3[s] != s for x in ("--slotset", f"{s}={set_of_slot3[s]}")]
    # 3P
    d3 = os.path.join(work, "fit3p")
    run_blender(["character", "--template", template_glb(tp, work), "--source", os.path.abspath(model), "--out", d3,
                 "--mode", "3p", "--lods", o.get("lods", "1,0.5,0.3,0.15,0.06")] + fit_args(o.o) + atlas_args +
                slotset3 + [x for m, s in slot3.items() for x in ("--slot", f"{m}={s}")])
    man3 = json.load(open(os.path.join(d3, "manifest.json")))
    skmgltf.import_gltf(tp, man3["lods"], out_file(tp, moddir))
    mans = [(man3, tp)]
    if fp:
        df = os.path.join(work, "fitfp")
        run_blender(["character", "--template", template_glb(fp, work), "--source", os.path.abspath(model), "--out",
                     df, "--mode", "fp", "--lods", o.get("fp_lods", "1,0.5")] + fit_args(o.o) + atlas_args +
                    [x for s, st in fp_slotset.items() if st != s for x in ("--slotset", f"{s}={st}")] +
                    [x for m, s in fp_slot.items() for x in ("--slot", f"{m}={s}")])
        manf = json.load(open(os.path.join(df, "manifest.json")))
        skmgltf.import_gltf(fp, manf["lods"], out_file(fp, moddir))
        mans.append((manf, fp))
    tt = TexTool(o.o)
    # a texture set is named after the 3P slot whose material instance owns it; compose each once
    sets = {}
    for man, _ in mans:
        for k, v in man["sets"].items():
            cur = sets.setdefault(k, {"grid": v["grid"], "tiles": []})
            for t in v["tiles"]:
                old = next((x for x in cur["tiles"] if x["material"].lower() == t["material"].lower()), None)
                if old is None: cur["tiles"].append(t)
                elif old.get("absent") and not t.get("absent"): cur["tiles"][cur["tiles"].index(old)] = t
    textures_for({"sets": sets}, tp, tt)
    log("done:", moddir)


# ---- weapon ---------------------------------------------------------------------------------------------------------

def weapon(o):
    import sm
    model = o.pos[0]
    src = src_dir(o.o)
    work = o.o["work"]
    moddir = o.o["out"]
    fpm = asset_file(o.get("fp_mesh") or die("--fp-mesh <FP weapon SKM> is required"), src)
    tpm = asset_file(o["3p_mesh"], src) if o.get("3p_mesh") else None
    info = inspect(model, work)
    mats = [m for m in info["materials"] if m]
    log(f"model: objects {[x['name'] for x in info['objects']]}, materials {mats}")
    sf = mesh_slots(fpm, src)
    names = [s for s, *_ in sf]
    user = dict(x.split("=", 1) for x in o.o["slot"])
    slot = {}
    for m in mats:
        s = user.get(m) or next((x for x in names if x.lower() == m.lower()), None)
        if s is None: die(f"material {m!r}: --slot {m}=<slot> (slots of {os.path.basename(fpm)}: {names})")
        slot[m] = s
    common = ["--source", os.path.abspath(model)] + fit_args(o.o, ()) + \
             [x for k in ("forward", "up", "scale", "anchor") if o.get(k) for x in ("--" + k, o[k])] + \
             [x for p in o.o["part"] for x in ("--part", p)]
    df = os.path.join(work, "fitfp")
    run_blender(["weapon", "--template", template_glb(fpm, work), "--out", df, "--lods", o.get("lods", "1,0.5")] +
                common + [x for m, s in slot.items() for x in ("--slot", f"{m}={s}")])
    manf = json.load(open(os.path.join(df, "manifest.json")))
    sockets = weapon_sockets(manf, fpm)
    skmgltf.import_gltf(fpm, manf["lods"], out_file(fpm, moddir), sockets=sockets)
    mans = [(manf, fpm)]
    fp_set_of_slot = {s: s for s in names}
    if tpm:
        s3 = mesh_slots(tpm, src)
        n3 = [s for s, *_ in s3]
        # 3P slot for each FP slot: same MI base colour texture (the 3P MIs are children of the FP ones)
        bcf = {s: basecolor_of(t) for s, mi, t, ma in sf}
        slot3 = {}
        for m, s in slot.items():
            c = [x for x, mi, t, ma in s3 if basecolor_of(t) == bcf[s]] or \
                [x for x in n3 if x.lower().replace("_3p", "") == s.lower()]
            slot3[m] = c[0] if c else n3[0]
        d3 = os.path.join(work, "fit3p")
        run_blender(["weapon", "--template", template_glb(tpm, work), "--out", d3, "--lods", o.get("lods3", "1,0.5,0.25,0.12")] +
                    common + [x for m, s in slot3.items() for x in ("--slot", f"{m}={s}")])
        man3 = json.load(open(os.path.join(d3, "manifest.json")))
        bones = weapon_bones(man3, tpm)
        skmgltf.import_gltf(tpm, man3["lods"], out_file(tpm, moddir), bones=bones)
        mans.append((man3, tpm))
    # static meshes (what other players, world pickups and the dropped magazine show): moved over from the 3P fit, or
    # from the FP fit for the many weapons without a 3P skeletal mesh (retail FP and 3P static meshes are the same
    # size, only turned: the transform between the retail meshes carries the model over)
    sman, sref = (man3, tpm) if tpm else (manf, fpm)
    for spec in o.o["static"]:
        smf = asset_file(spec, src)
        sm.import_static_from_skinned(smf, sman["lods"], sref, out_file(smf, moddir), slot_map=None)
    if o.get("mag_static"):
        smf = asset_file(o["mag_static"], src)
        sm.import_static_from_skinned(smf, sman["lods"], sref, out_file(smf, moddir), only_bone="mag")
    tt = TexTool(o.o)
    for man, mesh in mans:
        textures_for(man, mesh, tt)
    # textures only the static meshes' materials use (e.g. a 3P normal map on a weapon without a 3P skeletal mesh)
    for spec in o.o["static"] + ([o["mag_static"]] if o.get("mag_static") else []):
        smf = asset_file(spec, src)
        textures_for(static_manifest(sman, sref, smf, src), smf, tt, static=True)
    if o.get("skins", "retarget") == "retarget":
        retarget_skins(fpm, tt, o.o)
    log("done:", moddir)


def static_manifest(man, mesh_file, sm_file, src):
    """The fit's texture sets renamed to the static mesh's slots: the slot with the same material instance, else the
    same name without _FP/_3P/_LOD, else the same base colour texture, else the only slot."""
    ks = mesh_slots(mesh_file, src)
    ss = mesh_slots(sm_file, src, static=True)
    base = lambda mi: re.sub(r"_(fp|3p|lod)(?=_|$)", "", mi.split(".")[-1].lower())
    sets = {}
    for k, kmi, ktex, _ in ks:
        if k not in man["sets"]: continue
        hit = [s for s, mi, t, _ in ss if mi == kmi] or [s for s, mi, t, _ in ss if base(mi) == base(kmi)] or \
              [s for s, mi, t, _ in ss if basecolor_of(t) and basecolor_of(t) == basecolor_of(ktex)]
        if hit: sets.setdefault(hit[0], man["sets"][k])
    if not sets and len(ss) == 1 and man["sets"]:
        sets[ss[0][0]] = next(iter(man["sets"].values()))
    return {"sets": sets}


def retarget_skins(fp_mesh, tt, o):
    """Weapon skins are material sets painted for the retail UVs (Skin_Sets/<set>/..._MI, also under /Game/TUxx/).
    On a new mesh they would show the skin's textures on the model's own UVs, so every skin material instance of
    this weapon gets the model's textures instead (base colour, normal, PBR of the same part and view). A player with
    a skin equipped then sees the model as made."""
    owned = owned_prefix(fp_mesh)                          # /Game/Items/Weapons/Assault/AR02/
    rel = owned[len("/Game/"):]
    b4bmod = tt.b4bmod
    rx = r"(TU[0-9]+/)?" + re.escape(rel) + r"Skin_Sets/.*_MI\.(uasset|uexp)$"
    subprocess.run([sys.executable, b4bmod, "extract", "--regex", rx, "-o", tt.src], capture_output=True, text=True)
    skins = []
    for root, _, files in os.walk(os.path.join(tt.src, "Gobi", "Content")):
        for f in files:
            if f.endswith("_MI.uasset"):
                gp = upkg.file_to_game_path(os.path.join(root, f))
                if re.match(r"/Game/(TU[0-9]+/)?" + re.escape(rel) + "Skin_Sets/", gp) and "/Skin_Default/" not in gp:
                    skins.append(os.path.join(root, f))
    if not skins:
        log("skins: none found (b4bmod extract needs the AES key); weapon skins will show their own textures"); return
    # default look per (part, view): the Skin_Default MI chain's textures (only parts whose textures we replaced)
    defaults = {}
    for root, _, files in os.walk(os.path.join(tt.src, "Gobi", "Content", *rel.strip("/").split("/"), "Skin_Sets",
                                               "Skin_Default")):
        for f in files:
            m = re.match(r".*_([A-Za-z]+)_(FP|3P)_MI\.uasset$", f)
            if not m: continue
            tex, _ = mi_chain(upkg.file_to_game_path(os.path.join(root, f)) + "." + f[:-7], tt.src)
            roles = {role_of(k): v for k, v in tex.items() if role_of(k) in ("basecolor", "normal", "pbr")}
            if any(v in tt.done for v in roles.values()):
                defaults[(m.group(1).lower(), m.group(2))] = (tex, roles)
    n = 0
    for f in sorted(skins):
        name = os.path.basename(f)[:-7]
        view = "3P" if "_3P" in name else "FP"
        part = next((p for (p, v) in defaults if v == view and re.search(r"_" + p + r"_", name, re.I)), None)
        if part is None: continue
        dtex, roles = defaults[(part, view)]
        try:
            _, own, _, _ = upkg.read_material_instance(f)
        except Exception:
            continue
        sets = []
        pnames = {role_of(k): k for k in dtex}            # the default MI's parameter name for each role
        for k in own:                                     # the skin's own names win (same master, same names)
            if role_of(k) in roles: pnames[role_of(k)] = k
        for role, tex in roles.items():
            sets += ["set", pnames[role], tex.split(".")[0]]
        r = subprocess.run([sys.executable, b4bmod, "mi", f] + sets + ["-o", tt.moddir, "--src", tt.src],
                           capture_output=True, text=True)
        if r.returncode:
            log(f"  skin {name}: {r.stderr.strip()[-200:]}"); continue
        n += 1
    log(f"skins: {n} skin material instance(s) of {rel} now use the model's textures")


def blender_to_ue(p):
    """Blender world (template glTF import) metres -> UE cm: glTF (x, y, z) = Blender (x, z, -y); UE = (x, z, y) of glTF."""
    return (p[0] * 100.0, -p[1] * 100.0, p[2] * 100.0)


def weapon_sockets(man, mesh_file):
    """Move the template's muzzle sockets to the model's muzzle (all sockets named *muzzle*)."""
    mk = man["extras"].get("markers_m", {})
    out = {}
    if "muzzle" in mk:
        mu = blender_to_ue(mk["muzzle"])
        s = skm.SkeletalMesh(mesh_file)
        for e in s.pkg.exports:
            if s.pkg.class_name(e) != "SkeletalMeshSocket": continue
            data = s.pkg.export_data(e); r = upkg.R(data); props = {}
            s.pkg.skip_tagged(r, props)
            name = s.pkg.fname(upkg.R(data, props["SocketName"]["off"]))
            if name.lower() == "muzzle": out[name.lower()] = mu
    return out


def weapon_bones(man, mesh_file):
    mk = man["extras"].get("markers_m", {})
    s = skm.SkeletalMesh(mesh_file)
    names = {s.name(b[:2]).lower() for b in s.m["refskel"]["bones"]}
    out = {}
    if "muzzle" in mk and "muzzle" in names:
        out["muzzle"] = blender_to_ue(mk["muzzle"])
    return out


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__); return
    cmd = sys.argv[1]
    o = Opts(sys.argv[2:])
    if not o.get("out"): die("-o <moddir> is required")
    o.o["out"] = os.path.abspath(o.o["out"])
    o.o["work"] = os.path.abspath(o.get("work") or tempfile.mkdtemp(prefix="b4bmodel_"))
    os.makedirs(o.o["work"], exist_ok=True)
    if cmd == "survivor":
        survivor(o)
    elif cmd == "weapon":
        weapon(o)
    elif cmd == "textures":
        man = json.load(open(o.pos[0]))
        textures_for(man, asset_file(o.get("mesh") or die("--mesh <SKM>"), src_dir(o.o)), TexTool(o.o))
    else:
        die(f"unknown command {cmd}")
    if not o.get("keep_work") and not o.get("work_given"):
        pass
    log("work files:", o.o["work"])


if __name__ == "__main__":
    main()
