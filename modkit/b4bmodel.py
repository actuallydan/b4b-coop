#!/usr/bin/env python3
"""b4bmodel: a modder's model (FBX, glTF, OBJ, .blend) -> a Back 4 Blood survivor outfit or weapon, cooked, textured,
ready for `addon.py pack`. Mod makers run it as `b4bmod survivor|weapon ...` (which also extracts the templates, packs
and installs); guide: docs/meshes.md. How it works: docs/investigations/mesh-mods.md §6 (b4b-coop repository).

  b4bmodel.py survivor <model> --outfit <3P outfit SKM> [--fp <FP arms SKM>] -o <moddir>
        [--slot MAT=SLOT|drop]... [--tex MAT=<file prefix|dir>]... [--lods 1,0.5,0.3,0.15,0.06] [--fp-lods 1,0.5]
        [--bonemap map.json] [--drop REGEX] [--weights source|transfer] [--twist template|none] [--facing -y]
        [--face auto|off]    auto: the face is skinned to the survivor's face bones (talks, blinks; mesh-mods.md §12)
        [--mouth auto|on|off]  auto: a model without a mouth interior gets a dark mouth cavity (on: always)
        [--face-eyes x,y,z;x,y,z]  the eyes' positions (Blender coordinates of each pupil, metres) when the log says
                             no eyes were found
        <model>: FBX, glTF/glb, VRM, OBJ, DAE, .blend. Rigs: UE4 mannequin, Mixamo, 3ds Max Biped, VRoid/VRM, Rigify
        (DEF- bones) and most others by bone name (else --bonemap); unrigged in an A-pose, T-pose or arms down.
        Materials without --slot are placed automatically (skin, hair/alpha cards, lashes, eyes, clothes; printed).
        [--proportions own|fit|0..1]   own (default): the model keeps its own limb/torso/neck lengths in third
                                person (the mesh's skeleton gets its joints; the game retargets the animations);
                                fit: stretched onto the survivor's joints; a number blends. FP arms always fit
        [--hair-physics auto|off]  auto: long hair swings on the survivor's physics hair bones (templates with a
                                   hair chain: Holly, Mom, ...; mesh-mods.md §14) [--hair-swing 0..1]
        [--cloth auto|off|MAT,...]  auto: a skirt/dress becomes cloth (templates with a clothing asset: Holly Elite 00)
        [--hair texture|tint]   hair slot: texture (default) = your hair texture's own colours, masked by its alpha;
                                tint = the game's hair shader, one colour root to tip (your texture's average)
        [--as <name> [--as-title <text>]]   an ADDED outfit: new packages under /Game/b4bcoop/outfits/<name>/ and an
                                            `outfit=` line in <moddir>/addoninfo.txt (in game: /model <name>)
  b4bmodel.py weapon <model> --fp-mesh <FP weapon SKM> [--3p-mesh <3P weapon SKM>] [--static <SM>]... -o <moddir>
        [--slot MAT=SLOT]... [--tex MAT=...]... [--forward +x] [--up +z] [--scale fit|F] [--part REGEX=BONE]...
        [--mag-static <SM>] [--lods 1,0.5] [--skins retarget|keep]
        [--as <name> [--as-title <text>]]   an ADDED weapon look: FP mesh + 3P meshes as new packages under
                                            /Game/b4bcoop/weapons/<name>/ and a `weapon=` line in <moddir>/addoninfo.txt
                                            (in game: /model <name> puts it on your weapon of that type)
  b4bmodel.py textures <manifest.json> --mesh <SKM> -o <moddir>      (the texture step alone)

  common: --src <extract folder> (where the game's files were extracted: b4bmod extract ...), --work <dir>,
          --normal-dx (the model's normal maps are DirectX style; default: OpenGL/glTF style, green flipped),
          --quality fast|balanced|best (texture encoder), --max-texture 4096|2048|1024 (largest texture made;
          2048 makes the add-on about 4x smaller), --keep-work

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


# hero hair (Master_Hair_M, BLEND_Masked, two-sided, dithered): with its static switch "Enable MultiMask" (all retail
# hero hair MIs) it samples one RGBA "Hair MultiMask" on UV0 whose A is the strand alpha; colour = RootColor..TipColor
HAIR_MASTER_RX = re.compile(r"/Master_Hair_M(\.|$)")
HAIR_ROOT_DARKEN = 0.6
# --hair texture (default): the hair slot gets a copy of a retail material instance that draws a colour TEXTURE with its
# alpha as the mask: the Cultist Melee's hair (Master_Zombie_Outfit_M: two-sided, masked + dithered like hero hair, cloth
# shading; static switches "Enable BC.A Opacity Mask" on, "Enable Wounds" off). Copying a retail MI keeps its cooked
# shaders (a new switch combination would need shaders the game doesn't have). mesh-mods.md §11.
HAIR_TEX_MI = "/Game/TU11/Characters/Cultists/CultistMelee/Materials/CultistMelee_Hair_MI"
HAIR_TEX_TEXTURES = {"Base Color": ("hairbc", "/Game/TU11/Characters/Cultists/CultistMelee/Textures/CultistMelee_Hair_BC_T"),
                     "Normal Map": ("normal", "/Game/TU11/Characters/Cultists/CultistMelee/Textures/CultistMelee_Hair_N_T"),
                     "PBR Map": ("pbr", "/Game/TU11/Characters/Cultists/CultistMelee/Textures/CultistMelee_Hair_PBR_T")}
# its "Microtile (R)" detail layer (a fabric pattern) is switched on in that MI: its intensities go to 0
HAIR_TEX_SCALARS = {"Detail BC Intensity (R)": 0.0, "Detail Roughness Intensity (R)": 0.0,
                    "Detail NRM Intensity (R)": 0.0, "Detail Height Scale (R)": 0.0,
                    "Fuzz Spread": 0.0, "Fuzz Brightness": 0.1}      # cloth sheen as on Sharice Elite 02's hair
HAIR_MODES = ("texture", "tint")


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
        self.hair_mis = []
        self.kind = o.get("kind")
        self.max_tex = int(o.get("max_texture", 4096))
        if self.max_tex not in (512, 1024, 2048, 4096): die("--max-texture: 512, 1024, 2048 or 4096")
        self.preview = {}                # texture set -> base colour PNG (preview_textures_*.json)
        self.mi_sets = []                # (MI, texture parameter, new texture) for textures that were shared
        self.adopted = {}                # shared MI package -> its copy in the template's folder
        self.meshes = []                 # the cooked meshes written (repointed when an MI is adopted)
        self.hair_mode = o.get("hair", "texture")
        if self.hair_mode not in HAIR_MODES: die(f"--hair: {' or '.join(HAIR_MODES)}")
        self.mi_scalars = []             # (MI, {scalar: value}) set after the textures

    def retail_png(self, tex_file):
        png = os.path.join(self.work, "retail_" + os.path.basename(tex_file)[:-7] + ".png")
        if not os.path.exists(png):
            for mip in (4, 3, 2, 1, 0):
                r = subprocess.run([sys.executable, self.b4bmod, "export", tex_file, png, "--mip", str(mip),
                                    "--src", self.src], capture_output=True, text=True)
                if r.returncode == 0 and os.path.exists(png): break
        return png if os.path.exists(png) else None

    def compose_set(self, set_info, params, owned, master=None, mi=None, set_name=None):
        """params: {param name: texture path} of the slot's MI chain. set_info: manifest set (grid, tiles)."""
        tiles = set_info["tiles"]
        hair = bool(master and HAIR_MASTER_RX.search(master))
        g = set_info.get("grid", 1)
        base = 1024
        for t in tiles:
            for k in ("basecolor", "normal"):
                p = t["textures"].get(k)
                if p and os.path.exists(p): base = max(base, png_size(p))
        size = min(self.max_tex, 1 << math.ceil(math.log2(max(256, base * g))))
        hair_size = min(size, 2048)                  # the strand mask needs less (retail hero hair masks: 2048 or less)
        if hair and self.hair_mode == "texture" and mi:
            self.hair_texture_set(tiles, mi, owned, size, set_name)
            return
        for param, tex in params.items():
            if tex in self.done: continue
            role = role_of(param)
            if hair and param.lower() == "hair multimask": role = "hairmm"
            if role is None: continue
            f = upkg.game_path_to_file(tex, self.src)
            if not f or not os.path.exists(f): continue
            asset, path = f, tex
            if not tex.startswith(owned):
                # a texture shared with other outfits (e.g. Holly Elite 00's head colour lives in Elite_02, hair masks in
                # Meshes/Shared): write ours as a new texture in the template's folder and point the slot's material
                # instance at it, instead of changing the other outfits. The MI itself is adopted into the folder too
                # when it's shared (then the meshes are repointed to the copy).
                if role not in ("basecolor", "normal", "pbr", "hairmm") or not mi: continue
                mi = self.adopt_mi(mi, owned)
                if mi is None: continue
                path = owned + "Textures/" + tex.split("/")[-1].split(".")[0]
                asset = self.copy_pkg(tex, path)
                self.mi_sets.append((mi, param, path))
                log(f"  {param} of {mi.split('.')[-1]}: {tex.split('.')[0]} is shared with other outfits; yours goes to {path}")
            out = os.path.join(self.work, os.path.basename(f)[:-7] + ".png")
            self.jobs.append({"out": out, "size": hair_size if role == "hairmm" else
                              size if role in ("basecolor", "normal", "pbr") else 256,
                              "role": role, "tiles": tiles, "mean_from": self.retail_png(f),
                              "normal_dx": self.normal_dx, "asset": asset, "path": path, "kind": self.kind})
            self.done.add(tex)
            if role in ("basecolor", "hairmm") and set_name: self.preview.setdefault(set_name, out)
            if role == "hairmm":
                # Master_Hair_M has no colour texture: the colour is RootColor -> TipColor along the strand (MultiMask).
                # Take it from the model's hair texture (compose writes its average next to the PNG).
                if mi and mi.startswith(owned): self.hair_mis.append((mi, out + ".json"))
                else: log(f"  hair: {mi} is shared with other outfits: its colours stay the game's")

    def hair_texture_set(self, tiles, mi, owned, size, set_name):
        """--hair texture: a copy of HAIR_TEX_MI in the template's folder with our colour (RGB + alpha), normal and PBR
        textures; the meshes' hair slot is pointed at it (the template's hair MI stays as it is, unused by our meshes)."""
        base = re.sub(r"_?MI$", "", mi.split(".")[0].split("/")[-1])
        new_mi = owned + "Materials/" + base + "Color_MI"
        if new_mi in self.done: return
        self.done.add(new_mi)
        self.b4b("info", HAIR_TEX_MI, *[t for _, t in HAIR_TEX_TEXTURES.values()], what="extracting the hair material")
        refs = []
        has = {k for t in tiles for k in t.get("textures", {})}
        for param, (role, tex) in HAIR_TEX_TEXTURES.items():
            path = owned + "Textures/" + base + "Color_" + tex.rsplit("_", 2)[-2] + "_T"
            asset = self.copy_pkg(tex, path)
            refs.append(f"{tex}={path}")
            out = os.path.join(self.work, path.split("/")[-1] + ".png")
            # colour at full size; normal / PBR maps only as big as needed (anime hair has none: flat, constant)
            n = size if role == "hairbc" else \
                size if role == "normal" and "normal" in has else \
                min(size, 2048) if role == "pbr" and has & {"roughness", "orm", "mask", "gloss", "ao", "metallic"} else 256
            self.jobs.append({"out": out, "size": n, "role": role, "tiles": tiles,
                              "mean_from": self.retail_png(upkg.game_path_to_file(tex, self.src)),
                              "normal_dx": self.normal_dx, "asset": asset, "path": path, "kind": self.kind})
            if role == "hairbc" and set_name: self.preview.setdefault(set_name, out)
        self.copy_pkg(HAIR_TEX_MI, new_mi, refs)
        self.mi_scalars.append((new_mi, HAIR_TEX_SCALARS))
        self.repoint(mi.split(".")[0], new_mi, "hair: colour texture (masked by its alpha) instead of the game's hair shader")

    def repoint(self, pkg, new, why):
        """Every mesh written so far that uses material package `pkg` is pointed at `new` instead."""
        name = pkg.split("/")[-1]
        for mf in self.meshes:
            if not os.path.exists(mf): continue
            if not any(n == pkg for cp, cn, outer, n in upkg.Package(mf).imports):
                continue
            tmp = os.path.join(self.work, "repoint", "Gobi", "Content", os.path.basename(mf))
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            for ext in (".uasset", ".uexp"):
                shutil.copyfile(mf[:-7] + ext, tmp[:-7] + ext)
            self.b4b("rename", tmp, upkg.file_to_game_path(mf), "-o", self.moddir, "--ref", f"{pkg}={new}",
                     what=f"pointing {os.path.basename(mf)} at {new}")
            log(f"  {os.path.basename(mf)[:-7]}: slot material {name} -> {new} ({why})")

    def mi_ref(self, mi):
        """An MI for `b4bmod mi`: the copy in the mod folder if there is one (adopted, or edited before)."""
        f = upkg.game_path_to_file(mi.split(".")[0], self.moddir)
        return f if f and os.path.exists(f) else mi.split(".")[0]

    def b4b(self, *args, what=""):
        r = subprocess.run([sys.executable, self.b4bmod] + list(args) + ["--src", self.src], capture_output=True, text=True)
        if r.returncode:
            sys.stderr.write(r.stdout + r.stderr); die(f"{what or args[0]} failed")
        return r

    def copy_pkg(self, pkg, new, refs=()):
        """A copy of a game package under a new path in the mod folder; returns its file."""
        f = upkg.game_path_to_file(pkg.split(".")[0], self.src)
        self.b4b("rename", f, new, "-o", self.moddir, *[x for r in refs for x in ("--ref", r)], what=f"copying {pkg}")
        return upkg.game_path_to_file(new, self.moddir)

    def adopt_mi(self, mi, owned):
        """The slot's material instance, owned by the template's folder: a shared one (Holly_Hair_MI) is copied to
        <folder>/Materials/ and every mesh written so far that uses it is repointed to the copy."""
        pkg = mi.split(".")[0]
        if pkg.startswith(owned): return mi
        if pkg in self.adopted: return self.adopted[pkg]
        f = upkg.game_path_to_file(pkg, self.src)
        if not f or not os.path.exists(f): return None
        cls, _ = main_class(f)
        if cls != "MaterialInstanceConstant":
            log(f"  {pkg.split('/')[-1]} is a master material ({cls}): its textures stay the game's"); return None
        new = owned + "Materials/" + pkg.split("/")[-1]
        self.copy_pkg(pkg, new)
        name = pkg.split("/")[-1]
        self.repoint(pkg, new, "a copy: the original is shared")
        self.adopted[pkg] = f"{new}.{name}"
        return self.adopted[pkg]

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
        for mi, param, path in self.mi_sets:
            self.b4b("mi", self.mi_ref(mi), "set", param, path, "-o", self.moddir, what=f"material {mi}: {param}")
        self.mi_sets = []
        for mi, vals in self.mi_scalars:
            sets = [x for k, v in vals.items() for x in ("set", k, f"{v:g}")]
            self.b4b("mi", self.mi_ref(mi), *sets, "-o", self.moddir, what=f"material {mi}")
            log(f"  {mi.split('/')[-1]}: {', '.join(f'{k} {v:g}' for k, v in vals.items())}")
        self.mi_scalars = []
        for mi, stats in self.hair_mis:
            c = json.load(open(stats))["color_linear"]
            root = ",".join(f"{x * HAIR_ROOT_DARKEN:.4f}" for x in c) + ",1"
            tip = ",".join(f"{x:.4f}" for x in c) + ",1"
            r = subprocess.run([sys.executable, self.b4bmod, "mi", self.mi_ref(mi), "set", "RootColor", root,
                                "set", "TipColor", tip, "-o", self.moddir, "--src", self.src],
                               capture_output=True, text=True)
            if r.returncode:
                sys.stderr.write(r.stdout + r.stderr); die(f"hair material {mi}: setting its colours failed")
            log(f"  {mi.split('.')[-1]}: hair colour root {root} tip {tip} (linear)")
        self.hair_mis = []


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
        tt.compose_set(info, params, owned, master, mi, set_name)
    tt.run()


# ---- survivor -------------------------------------------------------------------------------------------------------

def fit_args(o, keys=("bonemap", "drop", "weights", "twist", "facing", "face", "mouth", "face_eyes")):
    a = []
    for k in keys:
        if o.get(k): a += ["--" + k, o[k]]
    for t in o.get("tex", []): a += ["--tex", t]
    for m in o.get("drop_mat", []): a += ["--drop_mat", m]
    return a


# ---- which template slot each of the model's materials goes to (--slot overrides) -----------------------------------
SLOT_SKIP_RX = re.compile(r"(_lod$|teeth|glass|lens|eye|hidden|cloth\d*$|occ)", re.I)
MAT_HAIR_RX = re.compile(r"hair|fur\b|ponytail|pony_?tail|afro|bangs?\b|fringe|braid|\bbun\b|mane|beard|mustache|"
                         r"moustache|wig|sideburn", re.I)
MAT_OVERLAY_RX = re.compile(r"lash|brow(?!n)|eyeline|eye_?liner|stubble", re.I)
MAT_EYE_RX = re.compile(r"eye|iris|cornea|pupil|sclera", re.I)
MAT_SKIN_RX = re.compile(r"skin|face|head|flesh|body|mouth|teeth|tongue|nail", re.I)
MAT_CLOTH_ZONES = [(re.compile(r"pant|trouser|jean|short|skirt|leg|bottom|shoe|boot|sock|feet|foot|lower", re.I),
                    re.compile(r"leg|pant|lower", re.I)),
                   (re.compile(r"gear|bag|belt|hat|cap\b|helmet|glove|glass|accessor|strap|armou?r|mask|pouch|holster|"
                               r"jewel|necklace|ear", re.I), re.compile(r"gear|acc", re.I)),
                   (re.compile(r".", re.I), re.compile(r"torso|body|top|upper|jacket|shirt|arms?$", re.I))]


def slot_kind(name, master):
    m = (master or "").split(".")[-1]
    if SLOT_SKIP_RX.search(name) or not master: return None
    if "Hair" in m: return "lashes" if "lash" in name.lower() else "hair"
    if "Head_M" in m or "Skin" in m: return "skin"
    if "Outfit" in m or "Cloth" in m or "Gear" in m: return "cloth"
    return None


def auto_slots(mats, minfo, s3, user):
    """{material: slot} for every kept material, and the dropped ones. --slot MAT=SLOT (or =drop) wins; the rest by
    what the material looks like: alpha cards and hair names -> the hair slot (alpha kept, one colour), lashes/brows
    -> the hair slot, eyes and skin -> the skin (head) slot, clothes -> the outfit's cloth slots by body zone.
    Eye overlays (mostly transparent, e.g. VRM eye highlights) are dropped: an opaque slot would show them as cards."""
    kinds = {}
    for sname, mi, tex, master in s3:
        k = slot_kind(sname, master)
        if k: kinds.setdefault(k, []).append(sname)
    skin = sorted(kinds.get("skin", []), key=lambda n: (0 if "head" in n.lower() else 1))
    cloth = sorted(kinds.get("cloth", []), key=lambda n: (0 if re.search(r"body|torso", n, re.I) else 1))
    hair = kinds.get("hair", [])
    out, drop, why = {}, [], {}
    single = len(mats) == 1
    for m in mats:
        if m in user or any(k.endswith("*") and m.lower().startswith(k[:-1].lower()) for k in user):
            v = user.get(m) or next(v for k, v in user.items() if k.endswith("*") and m.lower().startswith(k[:-1].lower()))
            if v.lower() == "drop": drop.append(m); why[m] = "--slot drop"
            else: out[m] = v; why[m] = "--slot"
            continue
        inf = minfo.get(m, {})
        clear = inf.get("alpha_clear", 0.0)
        alpha = clear > 0.2 and (inf.get("blend", "OPAQUE") != "OPAQUE" or "alpha" in inf.get("textures", {}) or clear > 0.4)
        # the material's name and its texture file names (MakeHuman eyes: material "low-poly", texture brown_eye.png)
        n = m.lower() + " " + " ".join(os.path.basename(p).lower() for p in inf.get("textures", {}).values())
        if MAT_OVERLAY_RX.search(n) or (MAT_HAIR_RX.search(n) and not MAT_EYE_RX.search(n)) or \
                (alpha and not MAT_EYE_RX.search(n) and not MAT_SKIN_RX.search(n)):
            if hair: out[m] = hair[0]; why[m] = "hair / alpha cards" if not MAT_OVERLAY_RX.search(n) else "lashes / brows (alpha)"
            elif MAT_OVERLAY_RX.search(n): drop.append(m); why[m] = "lashes/brows, the template has no hair slot"
            elif cloth: out[m] = cloth[0]; why[m] = "hair, but the template has no hair slot (renders opaque)"
            continue
        if MAT_EYE_RX.search(n):
            if clear > 0.5 and re.search(r"highlight|extra|spec|reflect|shine|catch", n):
                drop.append(m); why[m] = "eye highlight overlay (transparent)"; continue
            if clear > 0.5 and hair:
                out[m] = hair[0]; why[m] = "eye layer with alpha (e.g. iris): hair slot, masked, in the hair colour"
                continue
            if skin: out[m] = skin[0]; why[m] = "eyes (opaque, on the skin slot)"; continue
        if (MAT_SKIN_RX.search(n) or single) and skin and not re.search(r"cloth|suit|shirt|pant", n):
            out[m] = skin[0]; why[m] = "skin" if not single else "the model's only material (skin shader)"
            continue
        if not cloth:
            if skin: out[m] = skin[0]; why[m] = "no cloth slot in the template"; continue
            die(f"material {m!r}: {os.path.basename(s3[0][1])}'s slots have no cloth or skin material to put it on; "
                f"--slot {m}=<slot>")
        for rx_mat, rx_slot in MAT_CLOTH_ZONES:
            if rx_mat.search(n):
                hit = next((c for c in cloth if rx_slot.search(c)), cloth[0])
                out[m] = hit; why[m] = "clothes"; break
    log("materials -> slots (auto; override with --slot MAT=SLOT or --slot MAT=drop):")
    for m in mats:
        log(f"  {m:32s} -> {out.get(m, 'dropped'):12s} ({why.get(m, '')})")
    return out, drop


def survivor(o):
    o.o["kind"] = "character"
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
    user = {}
    for x in o.o["slot"]:
        if "=" not in x: die(f"--slot {x!r}: write --slot <your material>=<slot> (or =drop)")
        a, b = x.split("=", 1)
        if a not in mats and not a.endswith("*"):
            die(f"--slot {x}: the model has no material {a!r} (its materials: {mats})")
        if b.lower() != "drop" and b.lower() not in {n.lower() for n in slot_names}:
            die(f"--slot {x}: {os.path.basename(tp)} has no slot {b!r} (slots: {slot_names})")
        user[a] = next((n for n in slot_names if n.lower() == b.lower()), b)
    slot3, drop = auto_slots(mats, info.get("material_info", {}), s3, user)
    o.o["drop_mat"] = drop
    mats = [m for m in mats if m not in drop]
    if not mats: die("every material of the model is dropped: nothing left to fit")
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
                 "--mode", "3p", "--lods", o.get("lods", "1,0.5,0.3,0.15,0.06"),
                 "--proportions", o.get("proportions") or "own"] + fit_args(o.o) + atlas_args +
                slotset3 + [x for m, s in slot3.items() for x in ("--slot", f"{m}={s}")] + dangle_args(o.o, tp, src))
    man3 = json.load(open(os.path.join(d3, "manifest.json")))
    # the game's hair shader tints vertex-coloured strands (retail: mostly black); the colour-texture material doesn't
    # (the cultist's hair is white, the default)
    hair = {x: (0, 0, 0, 0) for x, mi, tex, master in s3 if master and HAIR_MASTER_RX.search(master)} \
        if o.get("hair", "texture") == "tint" else {}
    skmgltf.import_gltf(tp, man3["lods"], out_file(tp, moddir), slot_colors=hair,
                        bind_bones=bind_bone_moves(man3), bones=face_bone_moves(man3),
                        cloth=man3.get("extras", {}).get("cloth"))
    face_preview(o.o, tp, man3, work)
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
    tt.meshes = [out_file(m, moddir) for _, m in mans]
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
    for (man, _), tag in zip(mans, ("3p", "fp")):
        prev = {slot: tt.preview[st] for slot, st in man["slots"].items() if st in tt.preview}
        json.dump(prev, open(os.path.join(work, f"preview_textures_{tag}.json"), "w"), indent=1)
    log(f"check it before the game: blender -b --python {os.path.join(HERE, 'blender', 'preview.py')} -- "
        f"{os.path.join(work, 'fit3p', 'lod0.glb')} preview.png --textures {os.path.join(work, 'preview_textures_3p.json')}"
        f" --views front,side,back [--pose test]")
    log("done:", moddir)


# ---- added outfits (--as <name>): the result as NEW packages, nothing of the game replaced -------------------------
# docs/investigations/new-assets.md. The pipeline writes into a staging folder at the template's paths; every package
# it wrote (meshes, textures, hair MIs) and every material instance of the template's own folder the meshes use
# (directly or as a parent) is copied under /Game/b4bcoop/outfits/<name>/ with `b4bmod rename`, references rewritten
# between the copies (mesh -> MIs -> textures). Shared game packages (skeleton, physics asset, master materials,
# textures the pipeline didn't change) stay referenced as they are. The addoninfo gets an `outfit=` line the b4bcoop
# agent reads (`/model <name>`).
OUTFIT_ROOT = "/Game/b4bcoop/outfits/"
OUTFIT_NAME_RX = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def main_class(f):
    p = upkg.Package(f)
    e = next((x for x in p.exports if x["outer"] == 0), p.exports[0] if p.exports else None)
    return p.class_name(e) if e else None, p


def outfit_name(o):
    n = (o.get("as") or "").lower()
    if not OUTFIT_NAME_RX.match(n):
        die(f"--as {o.get('as')!r}: a name of letters, digits and _ (up to 32, starting with a letter), e.g. --as zombie_mom")
    return n


def as_copy(o, root, name, stage, meshes):
    """Copy what the pipeline wrote for `meshes` (+ the template folder's material instances they use) to
    <root><name>/, references rewritten between the copies. Returns {old package: new package}."""
    moddir, src = o["out"], src_dir(o)
    owned = {owned_prefix(m) for m in meshes}

    def file_of(pkg):   # what the pipeline wrote, else the game's (extracted) package
        for base in (stage, src):
            f = upkg.game_path_to_file(pkg, base)
            if f and os.path.exists(f): return f
        return None

    copy, todo = {}, [upkg.file_to_game_path(m) for m in meshes]
    while todo:
        pkg = todo.pop()
        if pkg in copy or not pkg.startswith("/Game/"): continue
        f = file_of(pkg)
        written = bool(f) and os.path.abspath(f).startswith(os.path.abspath(stage) + os.sep)
        if not f:
            if any(pkg.startswith(x) for x in owned): die(f"{pkg}: not extracted (b4bmod extract '{pkg}')")
            continue
        cls, p = main_class(f)
        # the template folder's material instances come along even when unchanged: they point at textures we replace
        if not written and not (cls == "MaterialInstanceConstant" and any(pkg.startswith(x) for x in owned)): continue
        copy[pkg] = (f, cls)
        if cls in ("SkeletalMesh", "StaticMesh", "MaterialInstanceConstant"):
            todo += [n for cp, cn, outer, n in p.imports if cn == "Package" and n.startswith("/Game/")]
    new, seen = {}, {}
    for pkg in sorted(copy):
        base = pkg.rsplit("/", 1)[1]
        if base.lower() in seen: die(f"--as: {pkg} and {seen[base.lower()]} have the same name; can't put both in one folder")
        seen[base.lower()] = pkg
        new[pkg] = root + name + "/" + base
    old_dir = os.path.join(moddir, "Gobi", "Content", *root[len("/Game/"):].split("/"), name)
    if os.path.isdir(old_dir): shutil.rmtree(old_dir)   # a previous run of this outfit / weapon look
    refs = [x for old, nw in sorted(new.items()) for x in ("--ref", f"{old}={nw}")]
    b4bmod = find_b4bmod() or die("b4bmod.py not found next to b4bmodel.py (rename)")
    for pkg in sorted(copy, key=lambda k: {"Texture2D": 0, "MaterialInstanceConstant": 1}.get(copy[k][1], 2)):
        f, cls = copy[pkg]
        r = subprocess.run([sys.executable, b4bmod, "rename", f, new[pkg], "-o", moddir, "--src", src] + refs,
                           capture_output=True, text=True)
        if r.returncode:
            sys.stderr.write(r.stdout + r.stderr); die(f"--as: copying {pkg} failed")
        log(f"  {cls or '?'}: {pkg} -> {new[pkg]}")
    # the copies must not point at anything we copied (they would show the game's version) and must load their own
    for pkg, nw in new.items():
        f = upkg.game_path_to_file(nw, moddir)
        p = upkg.Package(f)
        stale = sorted({n for cp, cn, outer, n in p.imports if cn == "Package" and n in new})
        if stale: die(f"--as: {nw} still references {stale}")
    return new


def add_info_line(moddir, key, name, line):
    """<moddir>/addoninfo.txt: `line` replaces the `key=<name>|...` line (several names allowed)."""
    info = os.path.join(moddir, "addoninfo.txt")
    lines = open(info, encoding="utf-8-sig").read().splitlines() if os.path.exists(info) else []
    lines = [x for x in lines if not re.match(rf"\s*{key}\s*=\s*{re.escape(name)}\s*\|", x, re.I)] + [line]
    with open(info, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(lines) + "\n")


def as_title(o, name):
    return (o.get("as_title") or name).replace("|", "/").replace("\r", " ").replace("\n", " ")


def as_outfit(o, name, stage, meshes):
    new = as_copy(o, OUTFIT_ROOT, name, stage, meshes)
    obj = lambda pkg: f"{new[pkg]}.{pkg.rsplit('/', 1)[1]}" if pkg in new else ""
    p3 = upkg.file_to_game_path(meshes[0])
    pf = upkg.file_to_game_path(meshes[1]) if len(meshes) > 1 else None
    m = re.search(r"/Heroes/([^/]+)/", p3)
    hero = m.group(1).lower() if m else "-"
    line = f"outfit={name}|{hero}|{obj(p3)}|{obj(pf) if pf else ''}|{as_title(o, name)}"
    add_info_line(o["out"], "outfit", name, line)
    log(f"outfit {name}: {len(new)} package(s) under {OUTFIT_ROOT}{name}/ (template: {hero}); addoninfo: {line}")
    log(f"in game: /model {name}")


# ---- added weapon looks (--as <name>): the weapon's meshes as NEW packages (docs/investigations/new-assets.md §9) ---
# Only the meshes the weapon actor itself shows are copied: the first-person mesh, the 3P static mesh (3P_<Code>_SM,
# what other players see) and a 3P skeletal mesh if given. World pickups, the dropped magazine and the weapon skins
# stay the game's (a look is chosen per player: /model <name>; skins don't apply to it). The agent pairs each copy
# with the weapon's mesh of the same name, so the copies keep the template's base names.
WEAPON_ROOT = "/Game/b4bcoop/weapons/"


def as_weapon(o, name, stage, fp, statics, skm3p):
    meshes = [fp] + statics + ([skm3p] if skm3p else [])
    new = as_copy(o, WEAPON_ROOT, name, stage, meshes)
    obj = lambda f: (lambda pkg: f"{new[pkg]}.{pkg.rsplit('/', 1)[1]}" if pkg in new else "")(upkg.file_to_game_path(f)) if f else ""
    code = re.sub(r"_SKM$", "", os.path.basename(fp)[:-len(".uasset")], flags=re.I)
    sm3p = next((f for f in statics if os.path.basename(f).lower().startswith("3p_")), statics[0] if statics else None)
    line = f"weapon={name}|{code}|{obj(fp)}|{obj(sm3p)}|{obj(skm3p)}|{as_title(o, name)}"
    add_info_line(o["out"], "weapon", name, line)
    log(f"weapon look {name}: {len(new)} package(s) under {WEAPON_ROOT}{name}/ (weapon {code}); addoninfo: {line}")
    log(f"in game: /model {name} (on your {code})")


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


def bind_bone_moves(man):
    """Bind skeleton of a model fitted with its own proportions (b4bfit rebind_template): {bone: UE cm} for skmgltf."""
    return {b.lower(): blender_to_ue(p) for b, p in man.get("extras", {}).get("bind_bones_m", {}).items()}


def dangle_args(o, tp, src):
    """b4bfit flags for secondary motion (blender/b4bdangle.py, cloth.py): --hair-physics auto puts the back hair on
    the template's simulated hair chain (if its physics asset has one), --cloth auto|MAT,... makes the skirt cloth
    (if the template has a clothing asset)."""
    import cloth
    a = []
    if o.get("hair_physics", "off") != "off":
        chains, pa = cloth.hair_chains(tp, src)
        if chains:
            a += ["--hair_bones", ";".join(",".join(c) for c in chains)]
            if o.get("hair_swing"): a += ["--hair_swing", o["hair_swing"]]
        else:
            log(f"hair: {os.path.basename(tp)}'s physics asset ({(pa or '?').split('.')[-1]}) has no simulated hair "
                f"bones: the hair moves with the head (templates with a hair chain: Holly, Holly Elite 06, Walker "
                f"Elite 03, Doc Elite 03, Mom)")
    if o.get("cloth", "off") != "off":
        if cloth.cloth_assets(skm.SkeletalMesh(tp)):
            a += ["--cloth", o["cloth"]]
        else:
            log(f"cloth: {os.path.basename(tp)} has no clothing asset: skirts are skinned (outfits with cloth: Holly "
                f"Elite 00/04, Karlee Elite 06, Doc Elite 03, Walker Elite 07, Jim Torso 01)")
    return a


def face_bone_moves(man):
    """Face bones' new bind positions (b4bfit's face rig, blender/b4bface.py) for skmgltf: {bone: UE cm}."""
    return {b.lower(): blender_to_ue(p) for b, p in man.get("extras", {}).get("face_bones_m", {}).items()}


def face_preview(o, skm_file, man, work):
    """<work>/face_preview.json for `blender/preview.py --face`: the mesh's bind skeleton (UE, face bones moved) and
    the survivor's face poses (visemes, expressions), when its pose asset can be extracted."""
    moves = face_bone_moves(man)
    if not moves: return
    s = skm.SkeletalMesh(skm_file)
    skmgltf.set_bone_positions(s, {**bind_bone_moves(man), **moves})       # in memory: the rest pose as written
    rs = s.m["refskel"]
    names = [s.name(b[:2]) for b in rs["bones"]]
    rest = {n: [names[b[2]] if b[2] >= 0 else None, list(p[0:4]), list(p[4:7])] for n, b, p in
            zip(names, rs["bones"], rs["pose"])}
    hero = re.search(r"/Heroes/([^/]+)/", upkg.file_to_game_path(skm_file) or "")
    poses = {}
    f = face_pose_asset(o, hero.group(1)) if hero else None
    if f:
        import poseasset
        poses = poseasset.read(f)["poses"]
    out = os.path.join(work, "face_preview.json")
    json.dump({"rest": rest, "moved": moves, "poses": poses}, open(out, "w"))
    log(f"face: {len(moves)} face bones on the model's face; preview: blender -b --python blender/preview.py -- "
        f"{man['lods'][0]} face.png --face {out} --face-pose AH")


def face_pose_asset(o, hero):
    """The survivor's face pose asset file (FacePoses_<Hero>_PoseAsset; names vary), extracted if needed."""
    import glob
    def look():
        d = os.path.join(src_dir(o), "Gobi", "Content", "Characters", "Heroes", hero, "Animations")
        return next(iter(sorted(glob.glob(os.path.join(d, "[Ff]ace[Pp]oses_*PoseAsset*.uasset")))), None)
    f = look()
    if not f and find_b4bmod():
        subprocess.run([sys.executable, find_b4bmod(), "extract", "--regex",
                        f"(?i)/Heroes/{hero}/Animations/FacePoses_[^/]*PoseAsset", "-o", src_dir(o)],
                       capture_output=True, text=True)
        f = look()
    return f


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
    if cmd == "survivor" and o.get("as"):
        name, moddir = outfit_name(o), o.o["out"]
        stage = os.path.join(o.o["work"], "stage")
        shutil.rmtree(stage, ignore_errors=True)
        o.o["out"] = stage
        survivor(o)
        o.o["out"] = moddir
        src = src_dir(o.o)
        meshes = [out_file(asset_file(o[k], src), stage) for k in ("outfit", "fp") if o.get(k)]
        as_outfit(o.o, name, stage, meshes)
    elif cmd == "survivor":
        survivor(o)
    elif cmd == "weapon" and o.get("as"):
        name, moddir, src = outfit_name(o), o.o["out"], src_dir(o.o)
        stage = os.path.join(o.o["work"], "stage")
        shutil.rmtree(stage, ignore_errors=True)
        o.o["out"] = stage
        o.o["skins"] = "keep"      # skins don't apply to an added look (its made-up skin row is the look)
        o.o.pop("mag_static", None)  # the dropped magazine and world pickups stay the game's
        o.o["static"] = [x for x in o.o["static"] if os.path.basename(x).lower().startswith("3p_")]
        weapon(o)
        o.o["out"] = moddir
        fp = out_file(asset_file(o["fp_mesh"], src), stage)
        statics = [out_file(asset_file(x, src), stage) for x in o.o["static"]]
        skm3p = out_file(asset_file(o["3p_mesh"], src), stage) if o.get("3p_mesh") else None
        as_weapon(o.o, name, stage, fp, statics, skm3p)
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
