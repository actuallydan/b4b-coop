"""Fit a modder's model (FBX, glTF, VRM, OBJ, .blend) onto a B4B template skeleton, headless in Blender. Writes glTF files
that `skmgltf.py import` turns into a cooked skeletal mesh, plus a manifest (materials -> template slots, textures,
atlas rectangles) for the texture step. Driven by b4bmodel.py (`b4bmod survivor|weapon`); usable on its own.
Guide: docs/meshes.md; how it works: docs/investigations/mesh-mods.md §6 (b4b-coop repository).

  blender -b --python blender/b4bfit.py -- character --template T.glb --source model.fbx --out DIR
        [--mode 3p|fp] [--proportions own|fit|0..1] [--bonemap map.json] [--lods 1,0.5,0.25,0.12,0.05]
        [--slot SRCMAT=SLOT]... [--drop REGEX]
        [--weights source|transfer] [--twist template|none] [--textures DIR] [--facing -y] [--atlas SET=m1,m2]...
        [--drop_mat MATERIAL]... [--face auto|off] [--hair_bones a,b,c;d,e [--hair_swing 1]] [--cloth auto|MAT,...]
        [--slotset SLOT=SET]... [--tex MAT=<prefix|dir>]... [--probe 1]
  blender -b --python blender/b4bfit.py -- weapon --template T.glb --source gun.fbx --out DIR
        [--forward +x] [--up +z] [--scale fit|<factor>] [--anchor trigger|grip|none] [--part REGEX=BONE]...
        [--slot SRCMAT=SLOT]... [--lods 1,0.5] [--textures DIR]
  blender -b --python blender/b4bfit.py -- convert <in> <out.glb>
  blender -b --python blender/b4bfit.py -- inspect <in> <out.json>     objects, materials, bones
  blender -b --python blender/b4bfit.py -- compose <jobs.json>          texture sets -> PNGs (b4bmodel)

character: the source's armature is mapped onto the template's bones by name (UE4 mannequin names as is, Mixamo,
  3ds Max Biped, VRoid/VRM, Rigify DEF- bones, else a generic reading of the names; or --bonemap {"srcbone": "b4bbone"}),
  turned to face +X like the template, then posed so every mapped limb joint lands on the template's joint (rotation
  + stretch along the bone; torso and neck as one piece each); the pose is applied to the meshes, so they sit in the
  template's bind pose. Weights: the source's, renamed; other bones (twist, helpers, face, hair) give theirs to the
  mapped bone they follow; with --twist template each limb's weight is split among the template's twist bones as the
  template mesh splits it at the nearest point. An unrigged source (no armature) is stood up by its bounds, its arms
  un-posed from a T-pose/arms-down onto the template's A-pose, weights from the template mesh (named parts like
  arm-left only take that limb's bones). fp: the same against an FP_Biped export, then only faces skinned to the arms
  are kept.
weapon: rigid parts. Source objects are bound to weapon bones by name (magazine/mag -> mag, bolt, trigger,
  charging handle, ...; --part overrides; everything else -> the template's main weapon bone), turned so the barrel
  points along the template's +X and scaled to the template's length, and moved so the trigger (object named
  trigger, else --anchor none) sits on the template's trigger bone. Markers (empties or objects) named muzzle, mag,
  shell_eject, ... become socket/bone positions in the manifest.
Materials: every source material maps to a template slot (--slot, else by name, else the first slot). Several
  materials on one slot are packed into an atlas (UVs remapped into tiles; the texture step composes the images).
"""
import bpy, bmesh, json, math, os, re, sys
from mathutils import Vector, Matrix, Quaternion
from mathutils.kdtree import KDTree

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def log(*a):
    print("b4bfit:", *a, flush=True)


def parse(argv, multi=("slot", "part", "tex", "atlas", "slotset", "drop_mat")):
    pos, opts = [], {k: [] for k in multi}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            k = a[2:].replace("-", "_")
            v = argv[i + 1] if i + 1 < len(argv) else ""
            if k in multi: opts[k].append(v)
            else: opts[k] = v
            i += 2
        else:
            pos.append(a); i += 1
    return pos, opts


# ---- scene helpers --------------------------------------------------------------------------------------------------

def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_any(path):
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path, use_anim=False, ignore_leaf_bones=False, automatic_bone_orientation=False)
    elif ext in (".glb", ".gltf", ".vrm"):                # VRM 0.x/1.0 = glTF 2.0 with extensions (ignored)
        bpy.ops.import_scene.gltf(filepath=path)
        vrm0_colors(path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".dae":
        bpy.ops.wm.collada_import(filepath=path)
    elif ext == ".blend":
        with bpy.data.libraries.load(path) as (src, dst):
            dst.objects = list(src.objects)
        for o in dst.objects:
            if o is not None: bpy.context.scene.collection.objects.link(o)
    else:
        raise SystemExit(f"unsupported model type {ext} (fbx, glb, gltf, vrm, obj, dae, blend; others: open it in "
                         f"Blender and export FBX or glTF)")
    new = [o for o in bpy.data.objects if o not in before]
    shapes = {pb.custom_shape for a in new if a.type == "ARMATURE" for pb in a.pose.bones if pb.custom_shape}
    for o in list(new):                             # bone display shapes (the glTF importer's "Icosphere")
        if o in shapes:
            new.remove(o); bpy.data.objects.remove(o)
    bpy.context.view_layer.update()
    # drop animation data: we fit the rest pose
    for o in new:
        if o.animation_data: o.animation_data_clear()
    return new


def vrm0_colors(path):
    """VRM 0.x MToon: the colour is materialProperties[]._Color, an sRGB (Unity) value that exporters such as VRoid
    Studio also copy unconverted into glTF's (linear) baseColorFactor, which Blender's importer puts on a Multiply node.
    MToon renderers (UniVRM, three-vrm) read it as sRGB: so do we (VRoid hair: a grey texture x a dark hair colour)."""
    import struct
    try:
        with open(path, "rb") as f:
            head = f.read(20)
            if head[:4] != b"glTF": return
            j = json.loads(f.read(struct.unpack_from("<I", head, 12)[0]))
    except (OSError, ValueError):
        return
    props = {m.get("name"): m for m in j.get("extensions", {}).get("VRM", {}).get("materialProperties", [])}
    for mat in bpy.data.materials:
        mp = props.get(re.sub(r"\.\d{3}$", "", mat.name))
        c = (mp or {}).get("vectorProperties", {}).get("_Color") if mp and mp.get("shader") == "VRM/MToon" else None
        if not c or not mat.node_tree: continue
        lin = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c[:3]]
        for n in mat.node_tree.nodes:
            if n.type in ("MIX", "MIX_RGB") and n.blend_type == "MULTIPLY":
                const = [i for i in n.inputs if i.enabled and i.type == "RGBA" and not i.is_linked]
                if len(const) == 1: const[0].default_value = (*lin, 1.0)


def select_only(objs, active=None):
    for o in bpy.context.view_layer.objects:
        if o is not None: o.select_set(False)
    for o in objs:
        if o is not None: o.select_set(True)
    bpy.context.view_layer.objects.active = active or (objs[0] if objs else None)


def apply_transforms(objs):
    """Bake object transforms (FBX cm scale, axis rotations) into data, parents first."""
    for o in sorted(objs, key=lambda o: len(o.users_collection) and depth(o)):
        pass
    roots = [o for o in objs if o.parent is None or o.parent not in objs]
    select_only(objs, objs[0])
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def depth(o):
    d = 0
    while o.parent: o = o.parent; d += 1
    return d


def bone_world(arm, name, tail=False):
    b = arm.data.bones[name]
    return arm.matrix_world @ (b.tail_local if tail else b.head_local)


def mesh_children(arm, objs):
    return [o for o in objs if o.type == "MESH" and (o.parent == arm or any(m.type == "ARMATURE" and m.object == arm
                                                                                for m in o.modifiers))]


# ---- bone maps ------------------------------------------------------------------------------------------------------
# A source rig is mapped onto the template's bones by name. Known naming schemes first (UE4 mannequin as is, Mixamo,
# 3ds Max Biped, VRoid/VRM, Blender Rigify DEF- bones), then a generic reader of bone names (side + body part + finger
# number, e.g. "upper_arm.L", "Arm_L", "LeftForeArm", "R_Thigh", "thumb.02.R") for everything else. Only bones that
# weight vertices (and their ancestors) are candidates, so control/helper bones of animation rigs are ignored.

B4B_MAIN = ["pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "neck_02", "head",
            "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l", "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
            "thigh_l", "calf_l", "foot_l", "ball_l", "thigh_r", "calf_r", "foot_r", "ball_r"]
FINGERS = ["thumb", "index", "middle", "ring", "pinky"]
for s in ("l", "r"):
    for f in FINGERS:
        B4B_MAIN += [f"{f}_0{k}_{s}" for k in (1, 2, 3)]
REQUIRED = ["pelvis", "spine_01", "head", "upperarm_l", "lowerarm_l", "hand_l", "upperarm_r", "lowerarm_r", "hand_r",
            "thigh_l", "calf_l", "foot_l", "thigh_r", "calf_r", "foot_r"]
# bones only re-weighted (joint not moved onto the template's): moving them would shift the model's own face
BIND_ONLY = {"jaw"}
SPINE_CHAIN = {"pelvis", "spine_01", "spine_02", "spine_03"}
NECK_CHAIN = {"neck_01", "neck_02"}

MIXAMO = {"hips": "pelvis", "spine": "spine_01", "spine1": "spine_02", "spine2": "spine_03", "neck": "neck_01",
          "head": "head"}
BIPED = {"pelvis": "pelvis", "spine": "spine_01", "spine1": "spine_02", "spine2": "spine_03", "neck": "neck_01",
         "head": "head"}
VROID = {"c_hips": "pelvis", "c_spine": "spine_01", "c_chest": "spine_02", "c_upperchest": "spine_03",
         "c_neck": "neck_01", "c_head": "head"}
RIGIFY = {"spine": "pelvis", "spine.001": "spine_01", "spine.002": "spine_02", "spine.003": "spine_03",
          "spine.004": "neck_01", "spine.005": "neck_02", "spine.006": "head", "jaw": "jaw"}
for side, s in (("Left", "l"), ("Right", "r")):
    MIXAMO.update({f"{side}Shoulder": f"clavicle_{s}", f"{side}Arm": f"upperarm_{s}",
                   f"{side}ForeArm": f"lowerarm_{s}", f"{side}Hand": f"hand_{s}", f"{side}UpLeg": f"thigh_{s}",
                   f"{side}Leg": f"calf_{s}", f"{side}Foot": f"foot_{s}", f"{side}ToeBase": f"ball_{s}"})
    L = side[0]
    VROID.update({f"{L}_Shoulder": f"clavicle_{s}", f"{L}_UpperArm": f"upperarm_{s}", f"{L}_LowerArm": f"lowerarm_{s}",
                  f"{L}_Hand": f"hand_{s}", f"{L}_UpperLeg": f"thigh_{s}", f"{L}_LowerLeg": f"calf_{s}",
                  f"{L}_Foot": f"foot_{s}", f"{L}_ToeBase": f"ball_{s}"})
    RIGIFY.update({f"shoulder.{L}": f"clavicle_{s}", f"upper_arm.{L}": f"upperarm_{s}", f"forearm.{L}": f"lowerarm_{s}",
                   f"hand.{L}": f"hand_{s}", f"thigh.{L}": f"thigh_{s}", f"shin.{L}": f"calf_{s}",
                   f"foot.{L}": f"foot_{s}", f"toe.{L}": f"ball_{s}"})
    BIPED.update({f"{L} Clavicle": f"clavicle_{s}", f"{L} UpperArm": f"upperarm_{s}", f"{L} Forearm": f"lowerarm_{s}",
                  f"{L} Hand": f"hand_{s}", f"{L} Thigh": f"thigh_{s}", f"{L} Calf": f"calf_{s}",
                  f"{L} Foot": f"foot_{s}", f"{L} Toe0": f"ball_{s}"})
    for fi, (f, m) in enumerate((("Thumb", "thumb"), ("Index", "index"), ("Middle", "middle"), ("Ring", "ring"),
                                 ("Pinky", "pinky"))):
        for k in (1, 2, 3):
            MIXAMO[f"{side}Hand{f}{k}"] = f"{m}_0{k}_{s}"
            VROID[f"{L}_{'Little' if f == 'Pinky' else f}{k}"] = f"{m}_0{k}_{s}"
            RIGIFY[f"{'thumb' if m == 'thumb' else 'f_' + m}.0{k}.{L}"] = f"{m}_0{k}_{s}"
            BIPED[f"{L} Finger{fi}{'' if k == 1 else k - 1}"] = f"{m}_0{k}_{s}"
TABLES = {"Mixamo": MIXAMO, "3ds Max Biped": BIPED, "VRoid/VRM": VROID, "Rigify": RIGIFY}
TABLES = {n: {k.lower(): v for k, v in t.items()} for n, t in TABLES.items()}


def norm_name(n):
    n = n.split("|")[-1].split(":")[-1]
    n = re.sub(r"^(mixamorig\d*[:_]|bip0?0?1[ _]|def[-_]|j_bip_|armature[:_]|bone[_ ]|b_)", "", n, flags=re.I)
    return n.strip().lower()


# generic reader: tokens of a bone name
_SKIP_RX = re.compile(r"(^|[^a-z])(end|nub|tip|top_?end|twist|roll|helper|ik|pole|target|mch|org|wgt|vis|ctrl|ctl|"
                      r"tweak|fk|socket|null|dummy|adj|sec|bust|breast|hair|skirt|tail|prop|weapon|attach)([^a-z]|$)"
                      r"|headtop|_end$|\.end$|end$", re.I)
_PARTS = [  # (regex on the side-stripped name, part); first match wins, order matters
    (r"thumb", "thumb"), (r"index|pointer|fore_?finger", "index"), (r"middle|mid_?finger", "middle"),
    (r"ring", "ring"), (r"pinky|little|small_?finger", "pinky"),
    (r"toe|ball", "ball"), (r"foot|ankle", "foot"),
    (r"shin|calf|lower_?leg|knee|leg_?lower|leg2|lowleg", "calf"), (r"thigh|up_?leg|upper_?leg|leg_?upper|leg1|upleg", "thigh"),
    (r"hand|wrist|palm", "hand"), (r"fore_?arm|lower_?arm|arm_?lower|elbow|arm2|lowarm", "lowerarm"),
    (r"upper_?arm|arm_?upper|up_?arm|arm1|uparm", "upperarm"), (r"clavicle|shoulder|collar", "clavicle"),
    (r"(^|_)arm(_|$)", "arm"), (r"(^|_)leg(_|$)", "calf_or_thigh"),
    (r"pelvis|hips?$|^hip|root_?hips", "pelvis"), (r"neck", "neck"), (r"(^|_)head(_?\d+)?$|skull", "head"),
    (r"upper_?chest|chest|spine|torso|back|abdomen|waist|belly|ribs", "spine"), (r"jaw", "jaw"),
]
_PARTS = [(re.compile(a), b) for a, b in _PARTS]


def side_of(raw):
    """('l'|'r'|None, name with the side marker removed), from Left/Right, L_/_L/.L/ L, l_/_l (lower case too)."""
    n = raw.split("|")[-1].split(":")[-1]
    n = re.sub(r"^(mixamorig\d*[:_]|bip0?0?1[ _]|def[-_]|j_bip_|armature[:_]|bone[_ ]|b_)", "", n, flags=re.I)
    for rx, sd in ((r"left", "l"), (r"right", "r")):
        m = re.search(rx, n, re.I)
        if m: return sd, (n[:m.start()] + n[m.end():])
    m = re.search(r"(^|[ _.\-])([LlRr])([ _.\-]|$)", n) or re.search(r"(?<=[a-z0-9])([LR])$", n)
    if m:
        g = m.group(2) if m.re.groups >= 2 else m.group(1)
        return g.lower(), (n[:m.start()] + " " + n[m.end():])
    return None, n


def parse_bone(raw):
    """(part, side, finger index or None) or None for a bone name the generic reader doesn't understand."""
    if _SKIP_RX.search(raw.lower()): return None
    sd, rest = side_of(raw)
    rest = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", rest).lower()
    rest = re.sub(r"[ .\-]+", "_", rest).strip("_")
    base = re.sub(r"_?\d+$", "", rest).strip("_")
    num = re.search(r"(\d+)\D*$", rest)
    for rx, part in _PARTS:
        if rx.search(base):
            if part in FINGERS:
                if sd is None: return None
                k = int(num.group(1)) if num else 1
                if k == 0: k = 1                        # thumb_0 / finger0 numbering
                return (part, sd, k)
            if part in ("pelvis", "neck", "head", "spine", "jaw"):
                return (part, None, int(num.group(1)) if num else 0) if sd is None else None
            return (part, sd, None) if sd else None
    return None


def generic_map(arm, cands):
    """Generic mapping of the candidate bones. The spine chain is the path from the pelvis to the head: its bones are
    spread over spine_01..03 and neck_01/02."""
    parsed = {b.name: parse_bone(b.name) for b in arm.data.bones if b.name in cands}
    out = {}
    fingers = {}
    # limbs named only "arm"/"leg" with numbered joints (arm_joint_L_1, leg_2_R ...): spread along the chain
    for part, names in (("arm", {3: ["upperarm", "lowerarm", "hand"], 4: ["clavicle", "upperarm", "lowerarm", "hand"]}),
                        ("calf_or_thigh", {2: ["thigh", "calf"], 3: ["thigh", "calf", "foot"],
                                           4: ["thigh", "calf", "foot", "ball"]})):
        for sd in ("l", "r"):
            chain = sorted([n for n, p in parsed.items() if p and p[0] == part and p[1] == sd],
                           key=lambda n: depth_bone(arm.data.bones[n]))
            if len(chain) < 2: continue
            use = names.get(len(chain)) or names[max(names)]
            for n, t in zip(chain, use):
                out[f"{t}_{sd}"] = n
                parsed[n] = None
    for n, p in parsed.items():                      # a lone "arm" is the upper arm
        if p and p[0] == "arm": parsed[n] = ("upperarm", p[1], None)
    for n, p in parsed.items():
        if not p: continue
        part, sd, k = p
        if part in FINGERS:
            fingers.setdefault((part, sd), []).append((k, n))
        elif part in ("clavicle", "upperarm", "lowerarm", "hand", "thigh", "calf", "foot", "ball", "jaw"):
            key = "jaw" if part == "jaw" else f"{part}_{sd}"
            if key not in out or depth_bone(arm.data.bones[n]) < depth_bone(arm.data.bones[out[key]]):
                out[key] = n            # the segment nearest the root (Rigify: upper_arm.L, not upper_arm.L.001)
    # finger chains: the three bones nearest the hand, by depth (1-based numbering or not)
    for (part, sd), lst in fingers.items():
        lst.sort(key=lambda kn: depth_bone(arm.data.bones[kn[1]]))
        for i, (k, n) in enumerate(lst[:3]):
            out[f"{part}_0{i + 1}_{sd}"] = n
    # plain "Leg" as thigh when there's a separate lower leg, else as calf
    for n, p in parsed.items():
        if p and p[0] == "calf_or_thigh":
            key = "thigh_" + p[1] if f"calf_{p[1]}" in out and f"thigh_{p[1]}" not in out else "calf_" + p[1]
            out.setdefault(key, n)
    # pelvis: named, else the common ancestor of both thighs
    pel = next((n for n, p in parsed.items() if p and p[0] == "pelvis"), None)
    if pel is None and "thigh_l" in out and "thigh_r" in out:
        al = ancestors(arm, out["thigh_l"]); ar = set(ancestors(arm, out["thigh_r"]))
        pel = next((a for a in al if a in ar), None)
    head = next((n for n, p in sorted(parsed.items(), key=lambda x: depth_bone(arm.data.bones[x[0]]))
                 if p and p[0] == "head"), None)
    if pel: out["pelvis"] = pel
    if head: out["head"] = head
    if pel and head and pel in ancestors(arm, head):
        path = [a for a in reversed(ancestors(arm, head)) if a != pel and pel in ancestors(arm, a)]
        necks = [a for a in path if parsed.get(a) and parsed[a][0] == "neck"]
        spines = [a for a in path if a not in necks]
    elif pel and not head:
        # no bone named head: the last of a neck chain (neck_joint_1, neck_joint_2) is the head
        necks = sorted([n for n, p in parsed.items() if p and p[0] == "neck"], key=lambda n: depth_bone(arm.data.bones[n]))
        if len(necks) >= 2 and pel in ancestors(arm, necks[-1]):
            head = out["head"] = necks[-1]
            path = [a for a in reversed(ancestors(arm, head)) if a != pel and pel in ancestors(arm, a)]
            necks = [a for a in path if parsed.get(a) and parsed[a][0] == "neck"]
            spines = [a for a in path if a not in necks]
        else:
            spines = necks = []
    else:
        spines = necks = []
    if pel and head:
        spread(out, spines, ["spine_01", "spine_02", "spine_03"])
        spread(out, necks, ["neck_01", "neck_02"])
    return out


def spread(out, bones, names):
    """Map a chain of source bones onto template chain names: first and last kept, the rest spread evenly."""
    if not bones: return
    if len(bones) <= len(names):
        for n, b in zip(names, bones): out[n] = b
        return
    for i, n in enumerate(names):
        out[n] = bones[round(i * (len(bones) - 1) / (len(names) - 1))]


def ancestors(arm, name):
    b = arm.data.bones[name].parent
    out = []
    while b is not None:
        out.append(b.name); b = b.parent
    return out


def weighted_bones(arm, meshes):
    """Bones that weight any vertex of the meshes, plus their ancestors (the rest are control/helper bones)."""
    names = {b.name for b in arm.data.bones}
    used = set()
    for m in meshes:
        idx = {g.index: g.name for g in m.vertex_groups if g.name in names}
        if not idx: continue
        hit = set()
        for v in m.data.vertices:
            for ge in v.groups:
                if ge.weight > 1e-4 and ge.group in idx: hit.add(ge.group)
        used |= {idx[i] for i in hit}
    for n in list(used):
        used |= set(ancestors(arm, n))
    return used or names


def build_bonemap(arm, targets, user_map=None, meshes=()):
    """source bone name -> template bone name, and a description of what was recognised.
    targets = set of template bone names."""
    tl = {t.lower(): t for t in targets}
    cands = weighted_bones(arm, meshes) if meshes else {b.name for b in arm.data.bones}
    options = []
    # template names as they are (UE4 mannequin / B4B rigs)
    m = {}
    for b in arm.data.bones:
        if b.name in cands and norm_name(b.name) in tl: m[b.name] = tl[norm_name(b.name)]
    options.append(("UE4 mannequin names", m))
    for tname, table in TABLES.items():
        m = {}
        for b in arm.data.bones:
            if b.name not in cands: continue
            t = table.get(norm_name(b.name))
            if t is not None and t.lower() in tl: m[b.name] = tl[t.lower()]
        options.append((tname, m))
    g = generic_map(arm, cands)
    options.append(("generic (read from the bone names)", {s: tl[t.lower()] for t, s in g.items() if t.lower() in tl}))

    def score(m):
        v = set(m.values())
        return (sum(1 for r in REQUIRED if r in v), len(v))
    kind, best = max(options, key=lambda o: score(o[1]))
    best = dict(best)
    # fill gaps of a named scheme with the generic reading (e.g. a Mixamo rig with extra spine bones)
    have = set(best.values())
    for s, t in options[-1][1].items():
        if t not in have and s not in best:
            best[s] = t; have.add(t)
    if user_map:
        srcnames = {b.name for b in arm.data.bones}
        for k, v in user_map.items():
            if v not in targets: raise SystemExit(f"--bonemap: {v!r} is not a template bone (template bones: "
                                                  f"{', '.join(b for b in B4B_MAIN if b in targets)} ...)")
            if k not in srcnames: raise SystemExit(f"--bonemap: the model has no bone {k!r}")
            best = {s: t for s, t in best.items() if t != v}
            best[k] = v
    # one source bone per target: keep the one nearest the root
    seen = {}
    for sb in sorted(best, key=lambda n: depth_bone(arm.data.bones[n])):
        t = best[sb]
        if t not in seen: seen[t] = sb
    return {sb: t for t, sb in seen.items()}, kind


_LAYER_RX = re.compile(r"^(org|mch|def|ctrl|ctl|drv|jnt|bind)[-_.]", re.I)


def own_bones(arm, bmap, inv, tp):
    """{bone: the mapped bone it follows} for every bone of the source armature (None: above the pelvis, e.g. root)."""
    alias = {}
    for n in bmap:
        alias.setdefault(_LAYER_RX.sub("", n).lower(), n)
    pos = {b.name: (b.head_local.copy(), b.tail_local.copy()) for b in arm.data.bones}
    # mapped segments: joint -> next mapped joint (or the bone's own tail)
    segs = {}
    for n, t in bmap.items():
        aim = next((x for x in AIM.get(t, []) if x in inv), None)
        segs[n] = (pos[n][0], pos[inv[aim]][0] if aim else pos[n][1])

    def seg_dist(p, a, b):
        ab = b - a
        f = 0.0 if ab.length_squared < 1e-12 else max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
        return (a + ab * f - p).length

    above = set(ancestors(arm, inv["pelvis"])) if "pelvis" in inv else set()   # root, Global, Position ...
    owner = {}
    for b in sorted(arm.data.bones, key=depth_bone):
        n = b.name
        if n in bmap: owner[n] = n; continue
        if n in above: owner[n] = None; continue
        a = alias.get(_LAYER_RX.sub("", n).lower())
        if a: owner[n] = a; continue
        po = owner.get(b.parent.name) if b.parent else None
        if po: owner[n] = po; continue
        mid = (pos[n][0] + pos[n][1]) / 2
        owner[n] = min(segs, key=lambda m: seg_dist(mid, *segs[m])) if segs else None
    return owner


def bonemap_help(arm, bmap, missing, out_dir, targets):
    """Write <out>/bonemap_template.json (every source bone, the mapped ones filled in) and return the error text."""
    os.makedirs(out_dir, exist_ok=True)
    tmpl = {b.name: bmap.get(b.name, "") for b in arm.data.bones}
    p = os.path.join(out_dir, "bonemap_template.json")
    json.dump(tmpl, open(p, "w"), indent=1)
    have = ", ".join(f"{s}->{t}" for s, t in sorted(bmap.items(), key=lambda x: B4B_MAIN.index(x[1])
                                                     if x[1] in B4B_MAIN else 999)[:40])
    return (f"the model's skeleton: no bone found for {', '.join(missing)}.\n"
            f"  recognised so far: {have or 'nothing'}\n"
            f"  Write a bone map: a JSON file {{\"<your bone>\": \"<b4b bone>\", ...}} and pass --bonemap <file>. A starting "
            f"point with all {len(tmpl)} of your bones is in\n  {p}\n  (fill in the empty ones you need, delete the rest). "
            f"b4b bones: {', '.join(b for b in B4B_MAIN[:23] if b in targets)}, fingers like index_01_l.")


def depth_bone(b):
    d = 0
    while b.parent: b = b.parent; d += 1
    return d


# segment end used to aim each template bone (its "tail"): the head of the first of these that is mapped
AIM = {"pelvis": ["spine_01", "spine_02", "spine_03", "neck_01", "head"], "spine_01": ["spine_02", "spine_03", "neck_01", "head"],
       "spine_02": ["spine_03", "neck_01", "head"], "spine_03": ["neck_01", "neck_02", "head"],
       "neck_01": ["neck_02", "head"], "neck_02": ["head"]}
for s in ("l", "r"):
    AIM.update({f"clavicle_{s}": [f"upperarm_{s}"], f"upperarm_{s}": [f"lowerarm_{s}"], f"lowerarm_{s}": [f"hand_{s}"],
                f"hand_{s}": [f"middle_01_{s}", f"index_01_{s}", f"ring_01_{s}"], f"thigh_{s}": [f"calf_{s}"],
                f"calf_{s}": [f"foot_{s}"], f"foot_{s}": [f"ball_{s}"]})
    for f in FINGERS:
        AIM[f"{f}_01_{s}"] = [f"{f}_02_{s}"]; AIM[f"{f}_02_{s}"] = [f"{f}_03_{s}"]


# ---- template ------------------------------------------------------------------------------------------------------

class Template:
    def __init__(self, path):
        objs = import_any(path)
        self.arm = next(o for o in objs if o.type == "ARMATURE")
        self.arm.name = "B4B_Template"
        self.meshes = [o for o in objs if o.type == "MESH" and not o.name.startswith("Icosphere")]
        for o in objs:
            if o.type == "MESH" and o.name.startswith("Icosphere"): bpy.data.objects.remove(o)
        self.bones = [b.name for b in self.arm.data.bones]
        self.pos = {b.name: self.arm.matrix_world @ b.head_local for b in self.arm.data.bones}
        # slot names = glTF material names of the export (one per UE slot, in slot order) + UE material names
        self.slots = []
        for o in self.meshes:
            for m in o.data.materials:
                if m and m.name not in self.slots: self.slots.append(re.sub(r"\.\d{3}$", "", m.name))

    def hide_meshes(self):
        for o in self.meshes: o.hide_set(True); o.hide_render = True


def facing_frame(pos, lat_a, lat_b, up_a, up_b):
    """Orthonormal frame (lateral, forward, up) from joint positions; lateral = a->b."""
    lat = (pos[lat_b] - pos[lat_a]).normalized()
    up = (pos[up_b] - pos[up_a])
    up = (up - lat * up.dot(lat)).normalized()
    fwd = up.cross(lat).normalized()
    return Matrix((lat, fwd, up)).transposed()


# ---- character ------------------------------------------------------------------------------------------------------

LIMB_FAMILIES = {}
for s in ("l", "r"):
    LIMB_FAMILIES[f"upperarm_{s}"] = [f"upperarm_{s}", f"upperarm_twist_01_{s}", f"shoulder_twist_01_{s}"]
    LIMB_FAMILIES[f"lowerarm_{s}"] = [f"lowerarm_{s}", f"lowerarm_twist_01_{s}", f"elbow_twist_01_{s}",
                                      f"wrist_twist_01_{s}"]
    LIMB_FAMILIES[f"thigh_{s}"] = [f"thigh_{s}", f"thigh_twist_01_{s}", f"hip_twist_01_{s}"]
    LIMB_FAMILIES[f"calf_{s}"] = [f"calf_{s}", f"calf_twist_01_{s}", f"knee_twist_01_{s}"]
    LIMB_FAMILIES[f"foot_{s}"] = [f"foot_{s}", f"foot_twist_01_{s}"]
ARM_BONES_RX = re.compile(r"^(upperarm|lowerarm|hand|wrist|elbow|shoulder|thumb|index|middle|ring|pinky)_")



# ---- proportions: the model's own segment lengths (3P) ---------------------------------------------------------------
# 3P_Biped_SK takes the translation of every body bone (spine, neck, head, arms, legs, twist bones) from the MESH's
# reference skeleton (retarget mode Skeleton), the pelvis height scaled (OrientAndScale), fingers/clavicles/IK bones
# oriented and scaled: retail female heroes use it (Holly's 3P skeleton is 154 cm to the head joint, Walker's 165,
# with other segment ratios, same animations). So a 3P mesh may keep the model's own limb lengths: every segment is
# only turned onto the template's direction (the template's bind rotations stay, the animations need them) and the
# mesh's bind skeleton gets the model's joints. FP_Biped_SK animates every bone's translation (all Animation mode):
# first-person arms are always fitted onto the template. docs/investigations/mesh-mods.md §13.

GROUPS = [("legs", [("thigh", "calf"), ("calf", "foot")]), ("arms", [("upperarm", "lowerarm"), ("lowerarm", "hand")]),
          ("hands", [("hand", "middle_01")]), ("feet", [("foot", "ball")])]
# IK bones sit on their FK bone in retail bind poses (Holly and Walker alike): they follow it
IK_FOLLOW = {"ik_hand_gun": "hand_r", "ik_hand_r": "hand_r", "ik_hand_l": "hand_l", "ik_foot_l": "foot_l",
             "ik_foot_r": "foot_r"}


def proportions_keep(o, mode):
    """--proportions own (default, 1) | fit (0) | a number in between: how much of the model's own segment lengths
    the 3P fit keeps (lengths blended geometrically). FP: always 0."""
    v = str(o.get("proportions") or "own").strip().lower()
    if mode != "3p":
        return 0.0
    if v in ("own", "keep"): return 1.0
    if v == "fit": return 0.0
    try:
        f = float(v)
    except ValueError:
        raise SystemExit(f"--proportions {v}: own, fit or a number from 0 (fit) to 1 (own)")
    return max(0.0, min(1.0, f))


def proportions_report(sp, inv, tp, keep, mode="3p"):
    """Log the model's segment lengths against the template's (sp: source joint positions after the uniform scale)."""
    def length(pairs, side=None):
        tot_s = tot_t = 0.0
        for a, b in pairs:
            a_, b_ = (f"{a}_{side}", f"{b}_{side}") if side else (a, b)
            if a_ not in inv or b_ not in inv or a_ not in tp or b_ not in tp: return None
            tot_s += (sp[inv[b_]] - sp[inv[a_]]).length; tot_t += (tp[b_] - tp[a_]).length
        return tot_s / tot_t if tot_t > 1e-6 else None
    rows = []
    for name, pairs in GROUPS:
        r = [x for x in (length(pairs, "l"), length(pairs, "r")) if x]
        if r: rows.append((name, sum(r) / len(r)))
    top = next((x for x in ("neck_01", "neck_02", "head") if x in inv and x in tp), None)
    if top and "pelvis" in inv: rows.insert(1, ("torso", length([("pelvis", top)])))
    if top and top != "head" and "head" in inv: rows.insert(2, ("neck", length([(top, "head")])))
    for name, a, b in (("shoulders", "upperarm_l", "upperarm_r"), ("hips", "thigh_l", "thigh_r")):
        if a in inv and b in inv:
            rows.append((name, (sp[inv[a]] - sp[inv[b]]).length / max(1e-6, (tp[a] - tp[b]).length)))
    txt = ", ".join(f"{n} x{r:.2f}" for n, r in rows if r)
    how = ("first-person arms: fitted onto the FP skeleton (its animations move every bone)" if mode != "3p" else
           "kept: the mesh's bind skeleton gets the model's joints" if keep == 1.0 else
           "stretched onto the survivor's joints" if keep == 0.0 else f"blended ({keep:.2f} of the model's own)")
    log(f"proportions (model / survivor, same height): {txt}; {how}")
    far = [n for n, r in rows if r and abs(math.log(r)) > 0.25]
    if far and keep == 0.0 and mode == "3p":
        log(f"  {', '.join(far)} differ by more than 25 %: they look stretched or squashed (--proportions own keeps "
            f"them)")


def own_bind_pose(tpl, sarm, meshes, bmap, inv, spos, D_of):
    """The model's own proportions: stand the chained joints on the template's ground (the model's soles where the
    template's are) and return the mapped joints' new positions {template bone: Vector}. Moves every D in D_of."""
    owner = own_bones(sarm, bmap, inv, tpl.pos)
    feet = {inv[x] for x in ("foot_l", "foot_r", "ball_l", "ball_r") if x in inv}
    tground = min((tm.matrix_world @ v.co).z for tm in tpl.meshes for v in tm.data.vertices)
    low = low_any = None
    for m in meshes:
        names = {g.index: g.name for g in m.vertex_groups}
        for v in m.data.vertices:
            if not v.groups: continue
            g = max(v.groups, key=lambda x: x.weight)
            ob = owner.get(names.get(g.group))
            if ob not in D_of: continue
            z = (D_of[ob] @ (m.matrix_world @ v.co)).z
            low_any = z if low_any is None else min(low_any, z)
            if ob in feet: low = z if low is None else min(low, z)
    low = low if low is not None else low_any
    shift = Matrix.Translation(Vector((0, 0, tground - low))) if low is not None else Matrix.Identity(4)
    for k in D_of: D_of[k] = shift @ D_of[k]
    P = {t: D_of[sb] @ spos[sb] for sb, t in bmap.items() if sb in D_of}
    if "pelvis" in P:
        log(f"proportions: pelvis {P['pelvis'].z * 100:.1f} cm above the ground (survivor {tpl.pos['pelvis'].z * 100:.1f}),"
            f" head joint {P['head'].z * 100:.1f} cm (survivor {tpl.pos['head'].z * 100:.1f}); soles on the survivor's "
            f"ground ({(tground - low) * 100 if low is not None else 0:+.1f} cm)")
    return P


def rebind_template(tpl, P, chains, chain_of, o):
    """Move the template's joints onto P (the model's), segment by segment (turn + stretch; bones that aren't mapped
    move with their mapped ancestor, IK bones with their FK bone, `weapon` scales with the shoulder height), deform its
    meshes along, and make that the template armature's rest pose. Records every moved bone in manifest extras
    bind_bones_m (Blender world metres), which the importer writes into the mesh's reference skeleton."""
    arm = tpl.arm
    tp = dict(tpl.pos)
    bones = sorted(arm.data.bones, key=depth_bone)
    D, Q, axis = {}, {}, {}

    def seg(n, a, c):
        dt, dm = tp[c] - tp[a], P[c] - P[a]
        if dt.length < 1e-6 or dm.length < 1e-6: return None
        q = dt.normalized().rotation_difference(dm.normalized())
        st, u = dm.length / dt.length, dt.normalized()
        S = Matrix.Identity(3) + (st - 1.0) * Matrix(((u.x * u.x, u.x * u.y, u.x * u.z), (u.y * u.x, u.y * u.y, u.y * u.z),
                                                      (u.z * u.x, u.z * u.y, u.z * u.z)))
        return Matrix.Translation(P[n]) @ q.to_matrix().to_4x4() @ S.to_4x4() @ Matrix.Translation(-tp[n]), q, (u, st)

    for b in bones:
        n = b.name
        anc = b.parent
        while anc is not None and anc.name not in P: anc = anc.parent
        if n in P:
            ch = chain_of.get(n)
            if ch:
                a, c = chains[ch]
            else:
                a, c = n, next((x for x in AIM.get(n, []) if x in P), None)
            r = seg(n, a, c) if c else None
            if r:
                D[n], Q[n], axis[n] = r
            else:                       # end bones (head, ball, finger tips): the parent's turn, no stretch
                q = Q.get(anc.name, Quaternion()) if anc is not None else Quaternion()
                D[n] = Matrix.Translation(P[n]) @ q.to_matrix().to_4x4() @ Matrix.Translation(-tp[n]); Q[n] = q
        elif anc is not None:
            D[n], Q[n] = D[anc.name], Q[anc.name]
            if anc.name in axis: axis[n] = axis[anc.name]
        else:
            D[n] = Matrix.Identity(4)
    for n, fk in IK_FOLLOW.items():
        if n in tp and fk in D:
            D[n] = Matrix.Translation(D[fk] @ tp[n] - tp[n]); axis.pop(n, None)
    ua = [P[x].z / tp[x].z for x in ("upperarm_l", "upperarm_r") if x in P and tp[x].z > 1e-3]
    if "weapon" in tp and ua:
        w = tp["weapon"]; r = sum(ua) / len(ua)
        D["weapon"] = Matrix.Translation(Vector((w.x, w.y, w.z * r)) - w)
    # stretched bones point along their stretch axis (a pose holds scale along the bone, not shear)
    select_only([arm], arm)
    bpy.ops.object.mode_set(mode="EDIT")
    for eb in arm.data.edit_bones:
        eb.inherit_scale = "NONE"
        eb.use_connect = False
        u, st = axis.get(eb.name, (None, 1.0))
        if u is not None and abs(st - 1.0) > 1e-4:
            roll_ref = eb.z_axis.copy()
            eb.tail = eb.head + u * eb.length
            eb.align_roll(roll_ref)
    bpy.ops.object.mode_set(mode="POSE")
    M = {}
    for pb in sorted(arm.pose.bones, key=lambda x: depth_bone(x.bone)):
        pb.matrix = D[pb.name] @ pb.bone.matrix_local
        bpy.context.view_layer.update()
        M[pb.name] = pb.matrix.copy()
    bpy.ops.object.mode_set(mode="OBJECT")
    for tm in tpl.meshes:
        select_only([tm], tm)
        for md in list(tm.modifiers):
            if md.type == "ARMATURE":
                bpy.ops.object.modifier_apply(modifier=md.name)
    # the posed skeleton becomes the rest pose
    select_only([arm], arm)
    bpy.ops.object.mode_set(mode="EDIT")
    for eb in arm.data.edit_bones:
        m = M[eb.name]
        sy = m.col[1].xyz.length
        length = eb.length
        eb.matrix = m.normalized()
        eb.length = max(1e-4, length * sy)
    bpy.ops.object.mode_set(mode="POSE")
    for pb in arm.pose.bones: pb.matrix_basis = Matrix.Identity(4)
    bpy.ops.object.mode_set(mode="OBJECT")
    for tm in tpl.meshes:
        md = tm.modifiers.new("Armature", "ARMATURE"); md.object = arm
    tpl.pos = {b.name: arm.matrix_world @ b.head_local for b in arm.data.bones}
    err = max(((tpl.pos[n] - p).length for n, p in P.items() if n in tpl.pos), default=0.0)
    moved = {n: p for n, p in tpl.pos.items() if (p - tp[n]).length > 1e-6}
    o.setdefault("extras", {})["bind_bones_m"] = {n: [p.x, p.y, p.z] for n, p in moved.items()}
    far = max(((tpl.pos[n] - tp[n]).length for n in moved), default=0.0)
    log(f"bind skeleton: {len(moved)} bones moved to the model's proportions (up to {far * 100:.1f} cm; joint error "
        f"{err * 100:.2f} cm)")


def fit_character(o):
    tpl = Template(o["template"])
    src_objs = import_any(o["source"])
    src_objs = [x for x in src_objs if x.name in bpy.data.objects]
    if o.get("drop"):
        rx = re.compile(o["drop"], re.I)
        for x in list(src_objs):
            if x.type == "MESH" and rx.search(x.name):
                log("dropping", x.name); bpy.data.objects.remove(x); src_objs.remove(x)
    if o.get("drop_mat"):
        dm = {x.lower() for x in o["drop_mat"]}
        for x in [x for x in src_objs if x.type == "MESH"]:
            mats_x = list(x.data.materials)
            gone = lambda i: (re.sub(r"\.\d{3}$", "", mats_x[i].name).lower() in dm) if i < len(mats_x) and mats_x[i] \
                else NO_MATERIAL in dm                     # faces without a material: --slot none=drop
            if not any(gone(i) for i in {p.material_index for p in x.data.polygons}): continue
            bm = bmesh.new(); bm.from_mesh(x.data)
            bmesh.ops.delete(bm, geom=[f for f in bm.faces if gone(f.material_index)], context="FACES")
            bm.to_mesh(x.data); bm.free()
            if not x.data.polygons:
                log("dropping", x.name, "(only dropped materials)"); bpy.data.objects.remove(x); src_objs.remove(x)
    keyed = [x.name for x in src_objs if x.type == "MESH" and x.data.shape_keys]
    face_on = o.get("mode", "3p") == "3p" and o.get("face", "auto") != "off"
    face_src = None
    if face_on and keyed:                           # face rig: what the mouth-open / blink keys move, before removal
        fm = face_module()
        fk = fm.capture_shape_keys([x for x in src_objs if x.type == "MESH"], fm.gltf_morph_names(o["source"]))
        if fk: log("face: shape keys used as landmarks: " + ", ".join(f"{r} {n}" for r, ns in fk.items() for n in ns))
    for x in src_objs:
        if x.type == "MESH" and x.data.shape_keys:
            x.shape_key_clear()                     # the base shape stays
    if keyed:
        log(f"shape keys (face expressions, morphs) removed from {keyed}: survivors have no morph targets; the face "
            + ("is rigged to the survivor's face bones instead" if face_on else "follows the head (and jaw) bones"))
    arms = [x for x in src_objs if x.type == "ARMATURE"]
    meshes = [x for x in src_objs if x.type == "MESH"]
    if not meshes: raise SystemExit("no mesh in the model (after --drop)")
    mode = o.get("mode", "3p")
    targets = set(tpl.bones)
    # FBX files often carry cm scale / axis rotations on the objects: bake them into the data first
    apply_transforms(src_objs)
    fix_inverted_normals(meshes)

    own_bind = chains = chain_of = None
    if arms:
        sarm = max(arms, key=lambda a: len(a.data.bones))
        user_map = None
        if o.get("bonemap"):
            try:
                user_map = json.load(open(o["bonemap"], encoding="utf-8-sig"))
            except (OSError, ValueError) as e:
                raise SystemExit(f"--bonemap {o['bonemap']}: not a readable JSON file ({e})")
            user_map = {k: v for k, v in user_map.items() if v}
        skinned = mesh_children(sarm, meshes)
        bmap, kind = build_bonemap(sarm, targets, user_map, skinned)
        missing = [b for b in REQUIRED if b not in bmap.values()]
        log(f"source armature {sarm.name!r}: {len(sarm.data.bones)} bones, {len(bmap)} mapped ({kind})")
        log("  " + ", ".join(f"{s}->{t}" for s, t in sorted(bmap.items(), key=lambda x: B4B_MAIN.index(x[1])
                                                               if x[1] in B4B_MAIN else 999)))
        if missing:
            raise SystemExit(bonemap_help(sarm, bmap, missing, o["out"], targets))
        inv = {v: k for k, v in bmap.items()}
        smeshes = skinned
        others = [m for m in meshes if m not in smeshes]
        if others:
            log("meshes not skinned to the rig (parented rigidly to head):", [m.name for m in others])
        # 1. orient + scale: source frame -> template frame
        spos = {b.name: sarm.matrix_world @ b.head_local for b in sarm.data.bones}
        sp = {t: spos[s] for s, t in bmap.items()}
        tp = tpl.pos
        Ts = facing_frame(sp, "upperarm_r", "upperarm_l", "pelvis", "head")
        Tt = facing_frame(tp, "upperarm_r", "upperarm_l", "pelvis", "head")
        R = (Tt @ Ts.inverted()).to_4x4()
        sh = (sp["head"] - (sp["foot_l"] + sp["foot_r"]) / 2).length
        th = (tp["head"] - (tp["foot_l"] + tp["foot_r"]) / 2).length
        k = th / sh
        pel_s, pel_t = sp["pelvis"], tp["pelvis"]
        G = Matrix.Translation(pel_t) @ R @ Matrix.Scale(k, 4) @ Matrix.Translation(-pel_s)
        log(f"orient: rotate {math.degrees(R.to_quaternion().angle):.1f} deg, scale {k:.3f} "
            f"(model {sh * 100:.0f} cm head to feet, template {th * 100:.0f} cm)")
        unit_hint(sh, k)
        for x in [sarm] + meshes:
            if x.parent is None or x.parent not in [sarm] + meshes:
                x.matrix_world = G @ x.matrix_world
        apply_transforms([sarm] + meshes)
        for m in others:                           # rigid pieces (glasses, hats): follow the head
            vg = m.vertex_groups.new(name=inv["head"]); vg.add(range(len(m.data.vertices)), 1.0, "REPLACE")
            md = m.modifiers.new("Armature", "ARMATURE"); md.object = sarm
        smeshes = mesh_children(sarm, meshes)
        # proportions: the model's segments against the survivor's (legs, torso, neck, arms)
        keep = proportions_keep(o, mode)
        proportions_report({b.name: b.head_local.copy() for b in sarm.data.bones}, inv, tp, keep, mode)
        # 2. pose every mapped bone so its joint lands on the template joint and it aims at the template's next joint;
        #    bones in between (twist, extra spine/neck segments, helpers) and below (face, hair) move with their mapped
        #    ancestor's deformation
        select_only([sarm], sarm)
        bpy.ops.object.mode_set(mode="EDIT")
        for eb in sarm.data.edit_bones:
            eb.inherit_scale = "NONE"
            eb.use_connect = False
        # point every mapped bone at its aim joint (rest orientation only; the mesh doesn't move), so the stretch
        # along the segment is a scale along the bone's own axis: no shear, which a pose can't hold
        ebs = sarm.data.edit_bones
        # chains fitted as one piece: (first mapped bone, the next mapped joint above the chain)
        chains, chain_of = {}, {}
        for cname, members, tops in (("spine", ["pelvis", "spine_01", "spine_02", "spine_03"], ["neck_01", "neck_02", "head"]),
                                     ("neck", ["neck_01", "neck_02"], ["head"])):
            have = [x for x in members if x in inv and x in tp]
            top = next((x for x in tops if x in inv and x in tp), None)
            if have and top:
                chains[cname] = (have[0], top)
                for x in have: chain_of[x] = cname
        for sb, t in bmap.items():
            aim = next((x for x in AIM.get(t, []) if x in inv and x in tp and x not in BIND_ONLY), None)
            if aim is None or t in BIND_ONLY: continue
            eb, ea = ebs[sb], ebs[inv[aim]]
            d = ea.head - eb.head
            if t in chain_of:
                base, top = chains[chain_of[t]]
                d = (ebs[inv[top]].head - ebs[inv[base]].head).normalized() * max(d.length, 1e-3)
            if d.length > 1e-5:
                roll_ref = eb.z_axis.copy()
                eb.tail = eb.head + d
                eb.align_roll(roll_ref)
        bpy.ops.object.mode_set(mode="POSE")
        order = sorted(sarm.pose.bones, key=lambda pb: depth_bone(pb.bone))
        spos = {b.name: b.head_local.copy() for b in sarm.data.bones}          # armature space == world now
        worst = 0.0
        D_of = {}
        chain_D = {}

        # template hierarchy: with the model's own proportions each joint is carried by the transform of its parent
        # in the TEMPLATE's tree (source rigs differ: Rigify hangs thighs and shoulders off ORG- bones)
        tparent = {b.name: b.parent.name if b.parent else None for b in tpl.arm.data.bones}
        tdepth = {b.name: depth_bone(b) for b in tpl.arm.data.bones}

        def mapped_tparent(t):
            p = tparent.get(t)
            while p is not None and (p not in inv or inv[p] not in D_of): p = tparent.get(p)
            return p

        def joint_target(sb, t):
            """Where a mapped joint goes: the template's joint (fit); with the model's own proportions (keep > 0),
            where its mapped parent's transform carries it (segments only turned, joints chained from the pelvis)."""
            if keep == 0.0: return tp[t]
            p = mapped_tparent(t)
            return D_of[inv[p]] @ spos[sb] if p is not None else tp[t].copy()

        def chain_fit(chain):
            base, top = chains[chain]
            a_s, b_s = spos[inv[base]], spos[inv[top]]
            ds, dt = b_s - a_s, tp[top] - tp[base]
            if ds.length <= 1e-5 or dt.length <= 1e-5: return None
            q = ds.normalized().rotation_difference(dt.normalized())
            st = (dt.length / ds.length) ** (1.0 - keep)
            y = ds.normalized()
            S = Matrix.Identity(3) + (st - 1.0) * Matrix(((y.x * y.x, y.x * y.y, y.x * y.z),
                                                          (y.y * y.x, y.y * y.y, y.y * y.z),
                                                          (y.z * y.x, y.z * y.y, y.z * y.z)))
            a_t = joint_target(inv[base], base)
            log(f"  {chain} ({base} -> {top}) fitted as one piece, stretched x{st:.2f}")
            return Matrix.Translation(a_t) @ q.to_matrix().to_4x4() @ S.to_4x4() @ Matrix.Translation(-a_s)
        # phase 1: the fit transform D of every mapped bone (joint onto the template joint, aimed, stretched)
        q_of = {}
        fit_order = order if keep == 0.0 else \
            sorted((pb for pb in order if bmap.get(pb.name) in tdepth), key=lambda pb: tdepth[bmap[pb.name]])
        for pb in fit_order:
            t = bmap.get(pb.name)
            if t is None or t in BIND_ONLY: continue
            rest = pb.bone.matrix_local.copy()
            ch = chain_of.get(t)
            if ch is not None and ch not in chain_D:
                chain_D[ch] = chain_fit(ch)
            if chain_D.get(ch) is not None:
                # torso and neck: one transform per chain (pelvis -> neck, neck -> head), so segment lengths that
                # differ from the template's (Mixamo hips, VRM and Rigify spines) don't squash and stretch it in bands
                D_of[pb.name] = chain_D[ch]
                q_of[pb.name] = chain_D[ch].to_quaternion()
                continue
            aim = next((x for x in AIM.get(t, []) if x in inv and x in tp and x not in BIND_ONLY), None)
            head_s = spos[pb.name]
            head_t = joint_target(pb.name, t)
            if aim:
                dir_s = spos[inv[aim]] - head_s
                dir_t = tp[aim] - tp[t]
            else:
                dir_s = dir_t = None
            if dir_s is not None and dir_s.length > 1e-6 and dir_t.length > 1e-6:
                q = dir_s.normalized().rotation_difference(dir_t.normalized())
                st = (dir_t.length / dir_s.length) ** (1.0 - keep)
            else:
                # no aim joint (end bones, head): keep the nearest mapped parent's rotation change
                if keep == 0.0:
                    par = pb.parent
                    while par is not None and par.name not in q_of: par = par.parent
                    q = q_of[par.name] if par is not None else Quaternion()
                else:
                    p = mapped_tparent(t)
                    q = q_of[inv[p]] if p is not None else Quaternion()
                st = 1.0
            # stretch along the bone's own axis (= the segment, see above) about the head; rotate; move the head
            # onto the template joint
            ya = rest.col[1].xyz.normalized()
            S = Matrix.Identity(3) + (st - 1.0) * Matrix(((ya.x * ya.x, ya.x * ya.y, ya.x * ya.z),
                                                          (ya.y * ya.x, ya.y * ya.y, ya.y * ya.z),
                                                          (ya.z * ya.x, ya.z * ya.y, ya.z * ya.z)))
            D_of[pb.name] = Matrix.Translation(head_t) @ q.to_matrix().to_4x4() @ S.to_4x4() @ Matrix.Translation(-head_s)
            q_of[pb.name] = q
            worst = max(worst, ((D_of[pb.name] @ head_s) - head_t).length)
        own_bind = own_bind_pose(tpl, sarm, smeshes, bmap, inv, spos, D_of) if keep > 0.0 else None
        # which mapped bone each other bone moves with (and gives its weights to): its mapped ancestor, or the mapped
        # bone of the same name in another layer (Rigify ORG-/MCH- vs DEF-), else the mapped bone nearest to it
        # (helper bones parented outside the deform chain, e.g. MakeHuman elbow/knee helpers)
        owner = own_bones(sarm, bmap, inv, tp)
        # phase 2: pose every bone, root first (a pose is stored relative to the parent's)
        D_used = {}
        for pb in order:
            t = bmap.get(pb.name)
            if t is not None and t not in BIND_ONLY:
                D = D_of[pb.name]
            else:
                ob = owner.get(pb.parent.name) if (t in BIND_ONLY and pb.parent) else owner.get(pb.name)
                D = D_of.get(ob, Matrix.Identity(4)) if ob else Matrix.Identity(4)
            pb.matrix = D @ pb.bone.matrix_local
            D_used[pb.name] = D
            bpy.context.view_layer.update()
        log(f"pose fit: max joint error {worst * 100:.2f} cm")
        bpy.ops.object.mode_set(mode="OBJECT")
        # 3. bake the pose into the meshes
        for m in smeshes:
            select_only([m], m)
            for md in list(m.modifiers):
                if md.type == "ARMATURE" and md.object == sarm:
                    bpy.ops.object.modifier_apply(modifier=md.name)
        # 4. weights: rename to template bones (other bones -> their owner above)
        def target_of(name):
            ob = owner.get(name)
            return bmap[ob] if ob in bmap else "pelvis"
        if face_on:                                 # face rig: which vertices the rig's jaw/lower lip moves
            face_module().capture_jaw_weights(smeshes)
        for m in smeshes:
            rename_groups(m, {g.name: target_of(g.name) for g in m.vertex_groups})
        if face_on:
            face_src = face_module().capture_source_bones(sarm, D_used)
        bpy.data.objects.remove(sarm)
    else:
        # unrigged: stand it like the template (the model faces --facing, default -y = Blender's front view), scale to
        # the template's height, feet on the ground, centred on the pelvis; weights come from the template mesh
        log("source has no armature: placing it by its bounding box, weights from the template mesh (nearest surface)")
        for x in meshes:                             # a hierarchy of parts (head, torso, arm-left ...): flatten it
            if x.parent is not None:
                mw = x.matrix_world.copy(); x.parent = None; x.matrix_world = mw
        smeshes = meshes
        o["weights"] = "transfer"
        face = AXES[o.get("facing", "-y")]
        tfwd = Vector((1, 0, 0))                    # B4B heroes face +X
        R = face.rotation_difference(tfwd).to_matrix().to_4x4()
        for x in meshes:
            if x.parent is None or x.parent not in meshes: x.matrix_world = R @ x.matrix_world
        apply_transforms(meshes)
        vs = [x.matrix_world @ v.co for x in meshes for v in x.data.vertices]
        lo = Vector((min(v.x for v in vs), min(v.y for v in vs), min(v.z for v in vs)))
        hi = Vector((max(v.x for v in vs), max(v.y for v in vs), max(v.z for v in vs)))
        # the template's stature from its skeleton (an FP arms mesh has no feet): ground 4.5 cm below the balls of
        # the feet, top of the head 11.5 % above the head joint (measured on the 3P hero meshes)
        ground = (tpl.pos["ball_l"].z + tpl.pos["ball_r"].z) / 2 - 0.045
        th = (tpl.pos["head"].z - ground) * 1.115
        k = th / max(1e-6, hi.z - lo.z)
        pel = tpl.pos["pelvis"]
        G = Matrix.Translation(Vector((pel.x, pel.y, ground))) @ Matrix.Scale(k, 4) @ \
            Matrix.Translation(-Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z)))
        for x in meshes:
            if x.parent is None or x.parent not in meshes: x.matrix_world = G @ x.matrix_world
        apply_transforms(meshes)
        log(f"unrigged: scale {k:.3f}")
        unit_hint((hi.z - lo.z) / 1.13, k)             # the head joint sits about 1/1.13 of the stature up
        # arms in another pose than the template's A-pose (T-pose, hanging down): pose the template's arms like the
        # model's, take the weights from the posed template, then un-pose the model into the template's bind pose
        unpose_arms(tpl, meshes)
        o["weights"] = "done"
    if own_bind:
        # the template (skeleton + meshes) takes the model's proportions too: later steps (twist weights, the face)
        # compare against it, and its joints become the mesh's bind skeleton (manifest extras bind_bones_m)
        rebind_template(tpl, own_bind, chains, chain_of, o)
    if o.get("weights") == "transfer":
        transfer_weights(tpl, smeshes, all_groups=True)
    elif o.get("twist", "template") == "template":
        transfer_weights(tpl, smeshes, all_groups=False)
    if face_on and not o.get("probe"):
        rig_face_bones(o, tpl, smeshes, face_src)
    if mode == "3p" and not o.get("probe"):
        smeshes = secondary_motion(o, tpl, smeshes)
    if mode == "fp":
        for m in smeshes:
            keep_arms(m)
        smeshes = [m for m in smeshes if len(m.data.polygons)]
    if o.get("probe"):
        mats = []
        for m in smeshes:
            for mt in used_materials(m):
                n = re.sub(r"\.\d{3}$", "", mt.name) if mt else NO_MATERIAL
                if n not in mats: mats.append(n)
        os.makedirs(o["out"], exist_ok=True)
        json.dump({"materials": mats}, open(os.path.join(o["out"], "probe.json"), "w"))
        log("probe: materials", mats)
        return
    tpl.hide_meshes()
    # bind to the template armature
    for m in smeshes:
        for md in list(m.modifiers):
            if md.type == "ARMATURE": m.modifiers.remove(md)
        m.parent = tpl.arm
        m.matrix_parent_inverse = tpl.arm.matrix_world.inverted()
        md = m.modifiers.new("Armature", "ARMATURE"); md.object = tpl.arm
    finish(o, tpl, smeshes)


UNIT_MIXUPS = [(1 / 2.54, "centimetres stored as inches"), (2.54, "inches stored as centimetres"),
               (0.01, "centimetres read as metres"), (100.0, "metres read as centimetres"),
               (0.1, "millimetres read as centimetres"), (0.001, "millimetres read as metres"),
               (1 / 30.48, "centimetres stored as feet"), (0.3048, "feet read as metres")]


def unit_hint(sh, k):
    """The file's unit scale, judged by the height of the head joint (a person: about 1.3-1.8 m). A wrong unit only
    changes the uniform scale the fit applies anyway; this tells the modder what happened."""
    if 0.6 <= sh <= 2.6: return
    f, why = min(UNIT_MIXUPS, key=lambda x: abs(math.log(sh * x[0] / 1.55)))
    fits = 0.9 <= sh * f <= 2.3
    log(f"  the file's unit scale looks off: its head joint is {sh * 100:.0f} cm above the feet (a person: 130-180)"
        + (f"; x{f:.4g} gives {sh * f * 100:.0f} cm ({why}: the exporter's unit setting)" if fits else "")
        + f". Harmless: the model is scaled to the survivor's height (x{k:.3f})")


def fix_inverted_normals(meshes):
    """Custom (split) normals that point against the faces' winding (game rips: the ripper flipped one of them). The
    game draws one side of each face (by winding) and lights it by the normals: inverted normals render nearly black.
    When the faces point outward (judged from each mesh's centre, over the whole model) the normals are turned around;
    when the faces point inward too, the model is inside out: said, not changed."""
    bad, n_all, out, n_faces = [], 0, 0, 0
    for m in meshes:
        me = m.data
        if not me.polygons or not getattr(me, "has_custom_normals", False): continue
        cn = me.corner_normals
        dis = sum(1 for p in me.polygons for li in p.loop_indices if p.normal.dot(cn[li].vector) < 0)
        n_all += 1
        if dis > 0.9 * len(me.loops):
            bad.append(m)
            c = sum((v.co for v in me.vertices), Vector()) / len(me.vertices)
            out += sum(1 for p in me.polygons if p.normal.dot(p.center - c) > 0)
            n_faces += len(me.polygons)
    if not bad: return
    if out < 0.6 * n_faces:
        log(f"  normals: {len(bad)} objects' normals point against their faces, and the faces point inward: the model "
            f"may be inside out (Blender: select all, Mesh > Normals > Recalculate Outside, export again)")
        return
    for m in bad:
        me = m.data
        me.normals_split_custom_set([(-c.vector).to_tuple() for c in me.corner_normals])
    log(f"  normals: {len(bad)} of {n_all} objects had their normals pointing inward, against the faces (seen in game "
        f"rips): turned around ({', '.join(m.name for m in bad[:6])}{' ...' if len(bad) > 6 else ''})")


def face_module():
    """blender/b4bface.py (face bones: talking, blinking), next to this script."""
    d = os.path.dirname(os.path.abspath(__file__))
    if d not in sys.path: sys.path.insert(0, d)
    import b4bface
    return b4bface


def rig_face_bones(o, tpl, meshes, src_bones):
    """3P: skin the face to the template's face bones and record their new bind positions for the importer
    (manifest extras face_bones_m: {bone: [x, y, z]} Blender world metres)."""
    moved = face_module().rig_face(tpl, meshes, src_bones, log)
    if moved:
        o.setdefault("extras", {})["face_bones_m"] = {b: [p.x, p.y, p.z] for b, p in moved.items()}


def dangle_module():
    """blender/b4bdangle.py (hair on physics bones, skirts as cloth), next to this script."""
    d = os.path.dirname(os.path.abspath(__file__))
    if d not in sys.path: sys.path.insert(0, d)
    import b4bdangle
    return b4bdangle


def secondary_motion(o, tpl, meshes):
    """3P: --hair_bones "a,b,c;d,e": skin the back hair to the survivor's simulated hair chains (--hair_swing 0..1);
    --cloth auto|MAT[:cape|:lower],...: split skirts, coat tails and capes off as cloth sections and build their
    simulation meshes (manifest extras "cloth", written by modkit/cloth.py). Returns the meshes (cloth pieces added)."""
    if not o.get("hair_bones") and o.get("cloth", "off") in ("off", ""): return meshes
    dm = dangle_module()
    if o.get("hair_bones"):
        slot_of = dict(x.split("=", 1) for x in o.get("slot", []))
        def is_hair(mat):
            if not mat or dm.NOT_HAIR_RX.search(mat): return False
            sl = slot_of.get(mat)
            return bool(re.search(r"hair", sl, re.I)) if sl else bool(dm.HAIR_RX.search(mat))
        chains = [c.split(",") for c in o["hair_bones"].split(";") if c]
        dm.rig_hair(tpl, meshes, chains, is_hair, log, float(o.get("hair_swing", 1.0)))
    if o.get("cloth", "off") not in ("off", ""):
        mats = dm.cloth_materials(meshes, tpl, o["cloth"], log)
        pieces, sims = dm.cloth_regions(tpl, meshes, mats, log)
        if sims:
            meshes = [m for m in meshes if len(m.data.polygons)] + pieces
            o.setdefault("extras", {})["cloth"] = sims
        elif o["cloth"] not in ("auto", "on"):
            log("cloth: nothing to simulate")
    return meshes


def used_materials(m):
    """The materials a mesh's faces use (slots without faces, e.g. after --drop or the FP cut, don't count)."""
    idx = sorted({p.material_index for p in m.data.polygons})
    mats = list(m.data.materials)
    return [mats[i] if i < len(mats) else None for i in idx] if mats else [None]


def rename_groups(m, mapping):
    """Merge vertex groups by target name (weights summed)."""
    import collections
    acc = collections.defaultdict(dict)
    idx2name = {g.index: mapping.get(g.name, g.name) for g in m.vertex_groups}
    for v in m.data.vertices:
        for ge in v.groups:
            t = idx2name[ge.group]
            acc[t][v.index] = acc[t].get(v.index, 0.0) + ge.weight
    m.vertex_groups.clear()
    for t, d in acc.items():
        g = m.vertex_groups.new(name=t)
        for vi, w in d.items():
            g.add([vi], w, "REPLACE")


def mesh_weights(m):
    names = {g.index: g.name for g in m.vertex_groups}
    return [{names[ge.group]: ge.weight for ge in v.groups if ge.weight > 0} for v in m.data.vertices]


def set_weights(m, W):
    m.vertex_groups.clear()
    groups = {}
    for vi, d in enumerate(W):
        for n, w in d.items():
            if w <= 0: continue
            g = groups.get(n) or groups.setdefault(n, m.vertex_groups.new(name=n))
            g.add([vi], w, "REPLACE")


def transfer_weights(tpl, meshes, all_groups):
    """all_groups: copy the nearest template vertex's weights (unrigged source). Otherwise only split each limb bone's
    weight among its twist bones in the template's proportions at the nearest template vertex."""
    kd_pts, kd_w = [], []
    dg = bpy.context.evaluated_depsgraph_get()
    for tm in tpl.meshes:
        W = mesh_weights(tm)
        for v, w in zip(tm.data.vertices, W):
            kd_pts.append(tm.matrix_world @ v.co); kd_w.append(w)
    kd = KDTree(len(kd_pts))
    for i, p in enumerate(kd_pts): kd.insert(p, i)
    kd.balance()
    fam_of = {b: fam for fam, bs in LIMB_FAMILIES.items() for b in bs}
    for m in meshes:
        W = mesh_weights(m)
        out = []
        for v, w in zip(m.data.vertices, W):
            p = m.matrix_world @ v.co
            if all_groups:
                hits = kd.find_n(p, 4)
                acc = {}
                tot = 0.0
                for co, i, d in hits:
                    f = 1.0 / max(d, 1e-4)
                    tot += f
                    for n, x in kd_w[i].items(): acc[n] = acc.get(n, 0.0) + x * f
                out.append({n: x / tot for n, x in acc.items()})
                continue
            nw = {}
            tw = None
            for n, x in w.items():
                fam = LIMB_FAMILIES.get(n)
                if not fam:
                    nw[n] = nw.get(n, 0.0) + x; continue
                if tw is None:
                    tw = {}
                    for co, i, d in kd.find_n(p, 3):
                        for tn, tx in kd_w[i].items(): tw[tn] = tw.get(tn, 0.0) + tx
                parts = {b: tw.get(b, 0.0) for b in fam if b in tpl.bones}
                s = sum(parts.values())
                if s <= 1e-6:
                    nw[n] = nw.get(n, 0.0) + x
                else:
                    for b, y in parts.items():
                        if y > 0: nw[b] = nw.get(b, 0.0) + x * y / s
            out.append(nw)
        set_weights(m, out)
    log("weights:", "copied from the template" if all_groups else "limb weights split among the template's twist bones")


def part_bones(name):
    """Template bones a named part of an unrigged model may be weighted to (None: any)."""
    p = parse_bone(name) or parse_bone(re.sub(r"[-_ .]?(mesh|geo|obj)\d*$", "", name, flags=re.I))
    if not p: return None
    part, sd = p[0], p[1]
    arm_all = ("clavicle", "upperarm", "lowerarm", "hand", "wrist", "elbow", "shoulder") + tuple(FINGERS)
    fam = {"upperarm": arm_all, "lowerarm": ("lowerarm", "hand", "wrist", "elbow") + tuple(FINGERS),
           "hand": ("hand", "wrist") + tuple(FINGERS), "clavicle": ("clavicle", "upperarm", "shoulder"),
           "thigh": ("thigh", "calf", "foot", "ball", "knee", "hip"), "calf_or_thigh": ("thigh", "calf", "foot", "ball", "knee", "hip"),
           "calf": ("calf", "foot", "ball", "knee"), "foot": ("foot", "ball"), "ball": ("ball",)}.get(part)
    if fam and sd: return {"prefixes": fam, "side": sd}
    if part == "head": return {"exact": ("head", "neck_02")}
    if part in ("spine", "pelvis"): return {"exact": ("pelvis", "spine_01", "spine_02", "spine_03", "neck_01")}
    return None


def allowed(bone, rule):
    if rule is None: return True
    if "exact" in rule: return bone in rule["exact"]
    return bone.endswith("_" + rule["side"]) and bone.startswith(rule["prefixes"])


def unpose_arms(tpl, meshes):
    """Unrigged model: bring its arms into the template's bind pose. The template armature is posed so its arms point
    like the model's (upperarm rotated about the shoulder), the weights are copied from the posed template mesh (named
    parts like "arm-left" only take bones of that part), then the inverse pose is applied to the model."""
    arm = tpl.arm
    tv = [tm.matrix_world @ v.co for tm in tpl.meshes for v in tm.data.vertices]
    mv = [(m, m.matrix_world @ v.co) for m in meshes for v in m.data.vertices]
    rules = {m.name: part_bones(m.name) for m in meshes}
    named = {m.name: r for m, r in ((m, rules[m.name]) for m in meshes) if r}
    if named: log("unrigged: parts by name (their vertices only take those bones):",
                  {n: (r["prefixes"][1] + "_" + r["side"]) if "prefixes" in r else r["exact"][0] for n, r in named.items()})
    tlo = Vector((min(p.x for p in tv), min(p.y for p in tv), min(p.z for p in tv)))
    thi = Vector((max(p.x for p in tv), max(p.y for p in tv), max(p.z for p in tv)))
    rot = {}
    for sd, sign in (("l", 1), ("r", -1)):          # heroes face +X: their left is +Y
        ua = tpl.pos.get(f"upperarm_{sd}")
        if ua is None: continue
        # the template's arm tip: its most lateral vertices on that side, same measure as for the model
        def tip(points):
            pts = sorted(points, key=lambda p: -sign * p.y)[:max(8, len(points) // 400)]
            return sum(pts, Vector()) / len(pts)
        t_tip = tip([p for p in tv if sign * p.y > 0])
        arm_objs = [m for m in meshes if (named.get(m.name) or {}).get("side") == sd and
                    "upperarm" in (named.get(m.name) or {}).get("prefixes", ())]
        if arm_objs:
            pts = [p for m, p in mv if m in arm_objs]
            shoulder = max(pts, key=lambda p: p.z)          # the part's top end
            top = [p for p in pts if p.z > shoulder.z - 0.03]
            shoulder = sum(top, Vector()) / len(top)
            m_tip = max(pts, key=lambda p: (p - shoulder).length)
        else:
            # the template's shoulder, placed proportionally in the model's bounds
            mlo = Vector((min(p.x for m, p in mv), min(p.y for m, p in mv), min(p.z for m, p in mv)))
            mhi = Vector((max(p.x for m, p in mv), max(p.y for m, p in mv), max(p.z for m, p in mv)))
            f = Vector(((ua.x - tlo.x) / max(1e-6, thi.x - tlo.x), (ua.y - tlo.y) / max(1e-6, thi.y - tlo.y),
                        (ua.z - tlo.z) / max(1e-6, thi.z - tlo.z)))
            shoulder = Vector((mlo.x + f.x * (mhi.x - mlo.x), mlo.y + f.y * (mhi.y - mlo.y), mlo.z + f.z * (mhi.z - mlo.z)))
            m_tip = tip([p for m, p in mv if sign * p.y > 0])
        dt, dm = (t_tip - ua), (m_tip - shoulder)
        if dt.length < 1e-4 or dm.length < 1e-4: continue
        q = dt.normalized().rotation_difference(dm.normalized())
        ang = math.degrees(q.angle)
        log(f"unrigged: {'left' if sd == 'l' else 'right'} arm is {ang:.0f} deg from the template's pose")
        if ang > 8: rot[sd] = q
    # pose the template (armature space == world: the template armature has no transform of its own)
    select_only([arm], arm)
    bpy.ops.object.mode_set(mode="POSE")
    for sd, q in rot.items():
        pb = arm.pose.bones[f"upperarm_{sd}"]
        head = arm.matrix_world @ pb.bone.head_local
        M = Matrix.Translation(head) @ q.to_matrix().to_4x4() @ Matrix.Translation(-head)
        pb.matrix = arm.matrix_world.inverted() @ M @ arm.matrix_world @ pb.bone.matrix_local
    bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode="OBJECT")
    # weights from the posed template (evaluated meshes), restricted by part names
    dg = bpy.context.evaluated_depsgraph_get()
    pts, wts = [], []
    for tm in tpl.meshes:
        ev = tm.evaluated_get(dg)
        me = ev.to_mesh()
        W = mesh_weights(tm)
        for v, w in zip(me.vertices, W):
            pts.append(tm.matrix_world @ v.co); wts.append(w)
        ev.to_mesh_clear()
    kd = KDTree(len(pts))
    for i, p in enumerate(pts): kd.insert(p, i)
    kd.balance()
    for m in meshes:
        rule = rules[m.name]
        out = []
        for v in m.data.vertices:
            p = m.matrix_world @ v.co
            acc, tot = {}, 0.0
            for co, i, d in kd.find_n(p, 16 if rule else 4):
                w = {n: x for n, x in wts[i].items() if allowed(n, rule)}
                if not w: continue
                f = 1.0 / max(d, 1e-4); tot += f
                for n, x in w.items(): acc[n] = acc.get(n, 0.0) + x * f
            if not acc and rule:                     # nothing of that part nearby: the part's first bone
                first = rule["exact"][0] if "exact" in rule else f"{rule['prefixes'][0]}_{rule['side']}"
                acc, tot = {first: 1.0}, 1.0
            out.append({n: x / tot for n, x in acc.items()} if tot else {"pelvis": 1.0})
        set_weights(m, out)
    log("weights: copied from the template" + (" (posed like the model)" if rot else ""))
    if not rot: return
    # un-pose: skin to the template with the inverse arm rotation, apply
    select_only([arm], arm)
    bpy.ops.object.mode_set(mode="POSE")
    for sd, q in rot.items():
        pb = arm.pose.bones[f"upperarm_{sd}"]
        head = arm.matrix_world @ pb.bone.head_local
        M = Matrix.Translation(head) @ q.inverted().to_matrix().to_4x4() @ Matrix.Translation(-head)
        pb.matrix = arm.matrix_world.inverted() @ M @ arm.matrix_world @ pb.bone.matrix_local
    bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode="OBJECT")
    for m in meshes:
        md = m.modifiers.new("Unpose", "ARMATURE"); md.object = arm
        select_only([m], m)
        bpy.ops.object.modifier_apply(modifier=md.name)
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    log("unrigged: arms moved into the template's pose")


def keep_arms(m):
    W = mesh_weights(m)
    armv = [sum(x for n, x in w.items() if ARM_BONES_RX.match(n)) / max(1e-6, sum(w.values())) >= 0.5 for w in W]
    bm = bmesh.new(); bm.from_mesh(m.data)
    kill = [f for f in bm.faces if not all(armv[v.index] for v in f.verts)]
    bmesh.ops.delete(bm, geom=kill, context="FACES")
    bm.to_mesh(m.data); bm.free()
    log(f"fp: {m.name}: kept {len(m.data.polygons)} arm faces")


# ---- materials, atlas, LODs, export ---------------------------------------------------------------------------------

TEX_KEYS = {"basecolor": ("albedo", "basecolor", "base_color", "basemap", "base_map", "diffuse", "color", "colour", "_bc",
                          "_d.", "_d_", "_diff", "_col", "_alb"),
            "normal": ("normal", "_n.", "_n_", "_nrm", "_nor", "norm"),
            "roughness": ("rough",), "metallic": ("metal",), "ao": ("_ao", "occlusion", "ambient"),
            "orm": ("rmao", "_orm", "orm.", "_arm.", "occlusionroughnessmetallic"), "mask": ("maskmap", "mask_map", "_mask"),
            "gloss": ("gloss", "smooth"), "alpha": ("alpha", "opacity", "transparen"),
            "skip": ("emissi", "height", "displace", "_disp", "bump", "spec", "sss", "subsurface", "cavity", "curvature",
                     "thickness", "id_map", "_id.", "matcap", "shade", "_rim", "outline")}
IMG_EXT = (".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp", ".webp", ".dds")   # DDS: BC1-BC7 (game rips)
NO_MATERIAL = "none"                  # the label of faces / objects without a material (--slot none=..., --tex none=...)
TEX_CACHE = None                      # set per run: where embedded (packed) images are written


def role_from_name(path):
    """What an image file is, from its name (None: can't tell). Checked most specific first."""
    fl = os.path.basename(path).lower()
    for key in ("skip", "normal", "orm", "mask", "roughness", "gloss", "metallic", "ao", "alpha", "basecolor"):
        if any(w in fl for w in TEX_KEYS[key]): return key
    return None


def image_file(img):
    """A file path for a Blender image: its file, else its embedded bytes (glb/vrm/FBX embedded media) written to the
    run's texture cache."""
    if img is None: return None
    p = bpy.path.abspath(img.filepath) if img.filepath else ""
    if p and os.path.isfile(p): return p
    if TEX_CACHE and (img.packed_file is not None or img.has_data or img.size[0]):
        os.makedirs(TEX_CACHE, exist_ok=True)
        base = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.splitext(img.name)[0]) or "image"
        if img.packed_file is not None:
            data = bytes(img.packed_file.data)
            ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg" if data[:2] == b"\xff\xd8" else ".bin"
            out = os.path.join(TEX_CACHE, base + ext)
            if ext != ".bin":
                with open(out, "wb") as f: f.write(data)
                return out
        out = os.path.join(TEX_CACHE, base + ".png")
        try:
            img.filepath_raw = out; img.file_format = "PNG"; img.save()
            return out
        except RuntimeError:
            return None
    return p or None                   # a missing file: the caller looks for it by name next to the model


def socket_image(sock, seen=None):
    """Follow links back from a shader socket to an image texture node (the Image)."""
    if not sock.is_linked: return None
    n = sock.links[0].from_node
    if n.type == "TEX_IMAGE":
        return n.image
    for inp in n.inputs:
        if inp.type in ("RGBA", "VECTOR", "VALUE") and inp.is_linked:
            r = socket_image(inp)
            if r: return r
    return None


def classify_files(files):
    out = {}
    for f in sorted(files):
        k = role_from_name(f)
        if k and k != "skip" and k not in out: out[k] = f
    return out


_IMAGES = {}


def images_in(dirs):
    for d in dirs:
        if d not in _IMAGES:
            _IMAGES[d] = sorted(os.path.join(root, f) for root, _, files in os.walk(d) for f in files
                                if f.lower().endswith(IMG_EXT))
        yield from _IMAGES[d]


# a base colour file's own name part: "head" of head.png / head_d.dds / Head_BaseColor.tga / all_color.png
_BC_SUFFIX_RX = re.compile(r"[_\-. ](albedo|base_?colou?r|diffuse|diff|colou?r|col|bc|d|alb|c)$", re.I)
_HAIR_NAME_RX = re.compile(r"hair|fur\b|ponytail|bangs?\b|fringe|braid|beard|mustache|moustache|wig|lash|brow(?!n)", re.I)


def companion_maps(bc_path, dirs, name=""):
    """Maps that belong to a base colour file by name: head.png -> head_n.dds (normal), head_ao.dds, head_rough.png
    ... (game rips and many exports keep one set per texture: <name>_n/_normal/_ao/_orm/_mask/_alpha next to it; the
    material only links the colour, or has generic names like 'Material #25'). {role: file}"""
    stem = os.path.splitext(os.path.basename(bc_path))[0]
    root = _BC_SUFFIX_RX.sub("", stem) or stem
    out = {}
    for f in sorted(set(images_in(list(dirs) + [os.path.dirname(bc_path)])),
                    key=lambda f: (f.lower().endswith(".dds"), f)):     # PNG/TGA before a DDS of the same name
        s = os.path.splitext(os.path.basename(f))[0]
        if s.lower() == stem.lower() or not s.lower().startswith(root.lower()): continue
        rest = s[len(root):]
        if not rest or rest[0] not in "_-. ": continue                 # hair_n, not hairband
        k = role_from_name("x" + rest + os.path.splitext(f)[1])        # what the suffix says, not the name part
        if k and k not in ("skip", "basecolor"): out.setdefault(k, f)
    return out


def mask_like_alpha(path):
    """A file's alpha channel looks like a cut-out mask (hair strands): plenty of clear and of solid texels."""
    st = alpha_stats(path)
    return bool(st) and st[0] > 0.15 and st[0] < 0.9 and st[1] < 0.5


def material_textures(mat, tex_dirs, user=None, n_materials=1):
    """Texture files of a material: --tex MAT=<prefix|dir> first, then the shader's linked images (embedded ones
    written out), then files named after the material in the model's folder / --textures; a model with one material
    and one texture set in its folder takes that set. Names that say what a file is win over the socket it hangs on
    (a mask map linked as base colour)."""
    base = re.sub(r"\.\d{3}$", "", mat.name).lower() if mat else NO_MATERIAL
    for k, v in (user or {}).items():
        if k.lower() == base:
            if not (os.path.isdir(v) or os.path.isdir(os.path.dirname(v) or ".")):
                raise SystemExit(f"--tex {k}={v}: no such folder or file prefix")
            files = ([os.path.join(v, f) for f in os.listdir(v)] if os.path.isdir(v) else
                     [os.path.join(os.path.dirname(v), f) for f in os.listdir(os.path.dirname(v) or ".")
                      if f.startswith(os.path.basename(v))])
            imgs = [f for f in files if f.lower().endswith(IMG_EXT)]
            got = classify_files(imgs)
            plain = sorted((f for f in imgs if role_from_name(f) is None),
                           key=lambda f: (len(os.path.basename(f)), f.lower().endswith(".dds"), f))
            if "basecolor" not in got and plain:
                got["basecolor"] = plain[0]            # --tex x=textures/head: head.png (a name that says nothing)
            if got: return with_companions(got, base, tex_dirs, mat)
            log(f"  --tex {k}={v}: no texture file recognised there (names with albedo/basecolor/diffuse, normal, "
                f"roughness, metallic, ao ...)")
    out = {}
    if mat and mat.node_tree:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf:
            for key, sock in (("basecolor", "Base Color"), ("normal", "Normal"), ("roughness", "Roughness"),
                              ("metallic", "Metallic"), ("alpha", "Alpha")):
                img = socket_image(bsdf.inputs[sock])
                if img is not None:
                    out[key] = (image_file(img), img.name)
        if not out:
            # no Principled BSDF links (unlit/toon materials, e.g. VRM MToon): the shader's image nodes, by colour space
            for n in mat.node_tree.nodes:
                if n.type != "TEX_IMAGE" or n.image is None: continue
                key = role_from_name(n.image.name) or ("normal" if n.image.colorspace_settings.name == "Non-Color"
                                                       else "basecolor")
                if key != "skip" and key not in out: out[key] = (image_file(n.image), n.image.name)
    res = {}
    for k, (p, name) in out.items():
        # files that don't exist (FBX with absolute paths from the author's machine): look next to the model / --textures
        if not p or not os.path.isfile(p):
            p = find_file(os.path.basename(p or name), tex_dirs)
            if not p:
                log(f"  material {base}: its {k} image {name!r} is not next to the model (copy it there or use --tex)")
                continue
        role = role_from_name(p)
        if role and role != "skip" and role != k and k != "alpha" and role != "basecolor" \
                and not (k == "basecolor" and role == "alpha"):
            log(f"  material {base}: {os.path.basename(p)} is linked as {k} but its name says {role}: used as {role}")
            k = role
        elif role == "skip" and k != "alpha":
            log(f"  material {base}: {os.path.basename(p)} ({k}) looks like a map the game doesn't use: ignored")
            continue
        res.setdefault(k, p)
    out = res
    if "basecolor" not in out and mat is not None:
        # nothing linked: guess by material name
        key_name = base.replace(" ", "_")
        for f in images_in(tex_dirs):
            fl = os.path.basename(f).lower().replace(" ", "_")
            if key_name and key_name in fl:
                k = role_from_name(f)
                if k and k != "skip": out.setdefault(k, f)
    if "basecolor" not in out and n_materials == 1:
        got = classify_files(list(images_in(tex_dirs)))
        if "basecolor" in got:
            log(f"  material {base}: using the texture set in the model's folder: "
                f"{', '.join(os.path.basename(v) for v in got.values())}")
            for k, v in got.items(): out.setdefault(k, v)
    return with_companions(out, base, tex_dirs, mat)


def with_companions(out, base, tex_dirs, mat):
    """Add the maps named after the base colour file (companion_maps) that the material doesn't link, and for hair
    whose colour has no alpha the cut-out mask some games keep in the normal map's alpha."""
    bc = out.get("basecolor")
    if not bc or not os.path.isfile(bc): return out
    add = {k: v for k, v in companion_maps(bc, tex_dirs).items() if k not in out}
    if add:
        log(f"  material {base}: maps named after {os.path.basename(bc)}: "
            + ", ".join(f"{os.path.basename(v)} ({k})" for k, v in add.items()))
        out.update(add)
    if "alpha" in out and out["alpha"] != bc: return out
    names = base + " " + os.path.basename(bc)
    if _HAIR_NAME_RX.search(names) and not mask_like_alpha(bc):
        n = out.get("normal")
        if n and os.path.isfile(n) and mask_like_alpha(n):
            log(f"  material {base}: {os.path.basename(bc)} has no cut-out alpha; the strands' mask is the alpha of "
                f"{os.path.basename(n)} (hair in game rips): used as the opacity")
            out["alpha"] = n
    return out


def basecolor_factor(mat):
    """The colour a material multiplies its base colour texture by (linear RGB), else None: glTF baseColorFactor, VRM
    MToon _Color (VRoid hair and brows: a grey texture x the hair colour), a Multiply node in a hand-made material.
    Blender's glTF importer builds it as a Mix node (Multiply, RGBA) between the image and the shader."""
    if not (mat and mat.node_tree): return None
    for n in mat.node_tree.nodes:
        if n.type not in ("MIX", "MIX_RGB") or n.blend_type != "MULTIPLY": continue
        if n.type == "MIX" and getattr(n, "data_type", "RGBA") != "RGBA": continue
        cols = [i for i in n.inputs if i.enabled and i.type == "RGBA"]
        fac = next((i for i in n.inputs if i.enabled and i.type == "VALUE"), None)
        if len(cols) != 2 or (fac is not None and fac.is_linked): continue
        img = [c for c in cols if c.is_linked and c.links[0].from_node.type == "TEX_IMAGE"]
        const = [c for c in cols if not c.is_linked]
        if len(img) != 1 or len(const) != 1: continue
        f = fac.default_value if fac is not None else 1.0
        rgb = [1.0 + f * (x - 1.0) for x in list(const[0].default_value)[:3]]
        if max(abs(x - 1.0) for x in rgb) < 1e-3: return None
        return rgb
    return None


def bsdf_values(mat):
    """Constant material values (used where the material has no texture for them)."""
    out = {}
    if mat and mat.node_tree:
        b = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if b:
            out["basecolor"] = list(b.inputs["Base Color"].default_value)[:3]
            out["roughness"] = b.inputs["Roughness"].default_value
            out["metallic"] = b.inputs["Metallic"].default_value
        f = basecolor_factor(mat)
        if f: out["basecolor_factor"] = f
    elif mat is not None:
        out["basecolor"] = list(mat.diffuse_color)[:3]
        out["roughness"] = mat.roughness
        out["metallic"] = mat.metallic
    return out


def find_file(name, dirs):
    for d in dirs:
        for root, _, files in os.walk(d):
            if name in files: return os.path.join(root, name)
            low = {f.lower(): f for f in files}
            if name.lower() in low: return os.path.join(root, low[name.lower()])
    return None


REPEAT_MAX = 4          # an atlas tile holds at most 4x4 repeats of a tiling texture (more: squeezed)


def uv_islands(me, uv, faces):
    """Faces grouped into UV islands (connected through shared vertices with the same UV)."""
    parent = list(range(len(faces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    first = {}
    loops = me.loops
    for fi, p in enumerate(faces):
        for li in p.loop_indices:
            u, v = uv.data[li].uv
            j = first.setdefault((loops[li].vertex_index, round(u, 4), round(v, 4)), fi)
            if j != fi:
                a, b = find(fi), find(j)
                if a != b: parent[a] = b
    out = {}
    for fi in range(len(faces)): out.setdefault(find(fi), []).append(fi)
    return list(out.values())


def uv_prepare(me, uv, faces):
    """Move every UV island by whole tiles so its centre lies in the 0..1 tile (identical look with a repeating
    texture) and measure what still lies outside: {"faces", "outside" (faces), "repeat" [nx, ny] a tile would need to
    hold the islands as repeats, "islands", "moved"}."""
    isl = uv_islands(me, uv, faces)
    moved = 0
    for fis in isl:
        lis = [li for fi in fis for li in faces[fi].loop_indices]
        us = [uv.data[li].uv[0] for li in lis]; vs = [uv.data[li].uv[1] for li in lis]
        du, dv = math.floor((min(us) + max(us)) / 2), math.floor((min(vs) + max(vs)) / 2)
        if du or dv:
            moved += len(fis)
            for li in lis:
                u, v = uv.data[li].uv
                uv.data[li].uv = (u - du, v - dv)
    outside, nx, ny = 0, 1, 1
    for fis in isl:
        lis = [li for fi in fis for li in faces[fi].loop_indices]
        us = [uv.data[li].uv[0] for li in lis]; vs = [uv.data[li].uv[1] for li in lis]
        # as repeats: the island moved so it starts in the first tile
        nx = max(nx, math.ceil(max(us) - math.floor(min(us) + 1e-3) - 1e-3))
        ny = max(ny, math.ceil(max(vs) - math.floor(min(vs) + 1e-3) - 1e-3))
        for fi in fis:
            fu = [uv.data[li].uv for li in faces[fi].loop_indices]
            if min(x[0] for x in fu) < -0.01 or max(x[0] for x in fu) > 1.01 or \
                    min(x[1] for x in fu) < -0.01 or max(x[1] for x in fu) > 1.01:
                outside += 1
    return {"faces": faces, "islands": isl, "outside": outside, "repeat": [min(nx, REPEAT_MAX), min(ny, REPEAT_MAX)],
            "moved": moved}


def uv_to_tile(me, uv, w, rect, rep, what):
    """UVs (after uv_prepare) into an atlas tile. rep [nx, ny] > 1: the texture is drawn nx x ny times in the tile, so
    islands start in the first repeat and are scaled by 1/n; else faces still outside are moved by whole tiles one by
    one, and what spans more than one tile is squeezed onto the tile's edge (logged)."""
    faces = w["faces"]
    nx, ny = rep
    squeezed = 0
    if (nx, ny) != (1, 1):
        for fis in w["islands"]:
            lis = [li for fi in fis for li in faces[fi].loop_indices]
            du = math.floor(min(uv.data[li].uv[0] for li in lis) + 1e-3)
            dv = math.floor(min(uv.data[li].uv[1] for li in lis) + 1e-3)
            for li in lis:
                u, v = uv.data[li].uv
                uv.data[li].uv = ((u - du) / nx, (v - dv) / ny)
    else:
        for p in faces:
            fu = [uv.data[li].uv for li in p.loop_indices]
            lo_u, hi_u = min(x[0] for x in fu), max(x[0] for x in fu)
            lo_v, hi_v = min(x[1] for x in fu), max(x[1] for x in fu)
            if lo_u >= -0.01 and hi_u <= 1.01 and lo_v >= -0.01 and hi_v <= 1.01: continue
            du, dv = math.floor((lo_u + hi_u) / 2), math.floor((lo_v + hi_v) / 2)
            for li in p.loop_indices:
                u, v = uv.data[li].uv
                uv.data[li].uv = (u - du, v - dv)
            if hi_u - lo_u > 1.02 or hi_v - lo_v > 1.02 or hi_u - du > 1.01 or lo_u - du < -0.01 or \
                    hi_v - dv > 1.01 or lo_v - dv < -0.01:
                squeezed += 1
    x0, y0, tw, th = rect
    for p in faces:
        for li in p.loop_indices:
            u, v = uv.data[li].uv
            u = min(max(u, 0.0), 1.0); v = min(max(v, 0.0), 1.0)
            # Blender UV origin is bottom-left, atlas rects are image (top-left) based
            uv.data[li].uv = (x0 + u * tw, 1.0 - (y0 + (1.0 - v) * th))
    if w["moved"]:
        log(f"  {what}: {w['moved']} faces' UVs moved by whole texture repeats into 0..1 (same look)")
    if squeezed:
        log(f"  {what}: {squeezed} faces span more than one texture repeat: squeezed into the tile (a small smear)")


def assign_slots(o, tpl, meshes, tex_dirs):
    """Rename materials to template slots and lay out texture sets.

    A texture set is the group of textures a slot's material instance samples (BC/N/PBR...). By default each slot is
    its own set. --atlas SET=mat1,mat2,... packs those source materials into one set as a grid of tiles (UVs remapped
    into each material's tile); --slotset SLOT=SET makes a slot sample another slot's set (retail FP arm skin samples
    the outfit's torso textures). The same --atlas list in the 3P and the FP run gives both the same layout.
    Returns the manifest part: {"slots": {slot: set}, "sets": {set: {"grid": g, "tiles": [...]}}}."""
    user = {}
    for x in o.get("slot", []):
        a, b = x.split("=", 1)
        user[a.lower()] = b
    atlases = {}
    for x in o.get("atlas", []):
        a, b = x.split("=", 1)
        atlases[a] = [m.strip().lower() for m in b.split(",") if m.strip()]
    slotset = {}
    for x in o.get("slotset", []):
        a, b = x.split("=", 1)
        slotset[a.lower()] = b
    tex_user = dict(x.split("=", 1) for x in o.get("tex", []))
    slots_lower = {s.lower(): s for s in tpl.slots}
    srcmats = []
    for m in meshes:
        for mat in used_materials(m):
            if mat not in srcmats: srcmats.append(mat)

    def base(mat):
        return re.sub(r"\.\d{3}$", "", mat.name) if mat else NO_MATERIAL

    slot_of = {}
    for mat in srcmats:
        bn = base(mat)
        slot = user.get(bn.lower()) or user.get((mat.name if mat else "none").lower()) or slots_lower.get(bn.lower())
        if slot is None:
            for k, v in user.items():
                if k.endswith("*") and bn.lower().startswith(k[:-1].lower()): slot = v
        if slot is None:
            slot = tpl.slots[0]
            log(f"material {bn!r}: no --slot given, using the template's first slot {slot!r}")
        if slot.lower() not in slots_lower:
            raise SystemExit(f"--slot {bn}={slot}: the template has no slot {slot!r} (slots: {tpl.slots})")
        slot_of[mat] = slots_lower[slot.lower()]
    # --tiles FILE (b4bmodel): {"alias": {set: {material: material whose tile it shares}}, "repeat": {set: {material:
    # [nx, ny]}}}: materials drawing the same textures share one atlas tile; the FP run takes the 3P run's repeats
    tiles_cfg = json.load(open(o["tiles"])) if o.get("tiles") else {}
    alias = {st.lower(): {k.lower(): v for k, v in d.items()} for st, d in tiles_cfg.get("alias", {}).items()}
    forced = {st.lower(): {k.lower(): v for k, v in d.items()} for st, d in tiles_cfg.get("repeat", {}).items()}
    sets = {}
    slots_used = {}
    plan = []                                   # (mat, slot, set, tile material, rect, grid)
    for mat in srcmats:
        slot = slot_of[mat]
        st = slotset.get(slot.lower(), slot)
        slots_used[slot] = st
        canon = alias.get(st.lower(), {}).get(base(mat).lower(), base(mat))
        cn = canon.lower()
        if st in atlases:
            names = atlases[st]
            if cn not in names:
                raise SystemExit(f"material {base(mat)!r} goes to slot {slot} (texture set {st}), but --atlas {st}= "
                                 f"doesn't list it (--atlas {st}={','.join(names)})")
            g = math.ceil(math.sqrt(len(names)))
            i = names.index(cn)
            rect = ((i % g) / g, (i // g) / g, 1.0 / g, 1.0 / g)
        else:
            g, rect = 1, (0.0, 0.0, 1.0, 1.0)
            if st in sets and sets[st]["tiles"][0]["material"].lower() != cn:
                raise SystemExit(f"materials {sets[st]['tiles'][0]['material']!r} and {base(mat)!r} both use texture "
                                 f"set {st}: list them in --atlas {st}=...")
        tiles = sets.setdefault(st, {"grid": g, "tiles": []})["tiles"]
        if not any(t["material"].lower() == cn for t in tiles):
            if cn == base(mat).lower():
                tx = material_textures(mat, tex_dirs, tex_user, len(srcmats))
            else:                               # the tile of another material with the same textures
                src = next((x for x in srcmats if base(x).lower() == cn), mat)
                tx = material_textures(src, tex_dirs, tex_user, len(srcmats))
            tiles.append({"material": canon, "rect": rect, "textures": tx, "values": bsdf_values(mat)})
        plan.append((mat, slot, st, cn, rect, g))
    # UVs: whole islands moved by whole tiles into 0..1 first (the same look with a repeating texture: game rips often
    # sit at v -1..0); what still reaches past the tile in an atlas is drawn as repeats of the texture in the tile, or
    # (a few faces only) squeezed onto the tile's edge
    groups = {}                                 # (mesh, material index) -> UV island work
    for mat, slot, st, cn, rect, g in plan:
        for m in meshes:
            uv = m.data.uv_layers.active
            if uv is None: continue
            idxs = [i for i, mm in enumerate(m.data.materials) if mm == mat] or ([0] if mat is None and not m.data.materials else [])
            for i in idxs:
                faces = [p for p in m.data.polygons if p.material_index == i or
                         (mat is None and p.material_index >= len(m.data.materials))]
                if faces: groups[(m.name, i)] = uv_prepare(m.data, uv, faces)
    need = {}
    for mat, slot, st, cn, rect, g in plan:
        if g == 1: continue
        for m in meshes:
            for i, mm in enumerate(list(m.data.materials) or [None]):
                w = groups.get((m.name, i))
                if w and mm == mat and w["outside"] > 0.02 * len(w["faces"]):
                    r = need.setdefault((st, cn), [1, 1])
                    r[0] = max(r[0], w["repeat"][0]); r[1] = max(r[1], w["repeat"][1])
    for mat, slot, st, cn, rect, g in plan:        # the 3P run's repeats win (FP arms: the same texture set)
        r = forced.get(st.lower(), {}).get(cn)
        if g > 1 and r: need[(st, cn)] = list(r)
    for st, info in sets.items():
        for t in info["tiles"]:
            r = need.get((st, t["material"].lower()))
            if r and tuple(r) != (1, 1): t["repeat"] = list(r)
    for mat, slot, st, cn, rect, g in plan:
        slotmat = bpy.data.materials.get(slot) or bpy.data.materials.new(slot)
        rep = need.get((st, cn), [1, 1])
        for m in meshes:
            uv = m.data.uv_layers.active
            if mat is None and not m.data.materials:
                m.data.materials.append(None)   # an object without material slots: give it one
            for i, mm in enumerate(m.data.materials):
                if mm != mat: continue
                w = groups.get((m.name, i))
                if g > 1 and uv is not None and w:
                    uv_to_tile(m.data, uv, w, rect, rep, f"{base(mat)} ({m.name})")
                m.data.materials[i] = slotmat
        log(f"material {base(mat)} -> slot {slot}, texture set {st}" + (f" tile {rect}" if g > 1 else "") +
            (f" (tile of {cn})" if cn != base(mat).lower() else "") +
            (f", texture repeated {rep[0]}x{rep[1]} in the tile" if tuple(rep) != (1, 1) else ""))
    # atlases listed but with materials not present in this model (e.g. FP arms): still record the full layout so
    # the texture step composes the same image in both runs
    for st, names in atlases.items():
        if st in sets:
            have = {t["material"].lower() for t in sets[st]["tiles"]}
            g = math.ceil(math.sqrt(len(names)))
            for i, n in enumerate(names):
                if n not in have:
                    sets[st]["tiles"].append({"material": n, "rect": ((i % g) / g, (i // g) / g, 1.0 / g, 1.0 / g),
                                              "textures": {}, "absent": True})
    return {"slots": slots_used, "sets": sets}


LOD_MIN_TRIS = 1500      # never decimate below this (a low-poly model's LODs stay whole: boxes collapse otherwise)


def make_lods(o, meshes):
    ratios = [float(x) for x in o.get("lods", "1").split(",")]
    lods = []
    total = sum(len(m.data.polygons) for m in meshes)
    for li, r in enumerate(ratios):
        if li == 0:
            lods.append(meshes); continue
        r = min(1.0, max(r, LOD_MIN_TRIS / max(1, total)))
        copies = []
        for m in meshes:
            c = m.copy(); c.data = m.data.copy(); c.name = f"{m.name}_LOD{li}"
            bpy.context.scene.collection.objects.link(c)
            if r >= 0.999:
                copies.append(c); continue
            d = c.modifiers.new("Decimate", "DECIMATE"); d.ratio = r; d.use_collapse_triangulate = True
            # keep the decimate before the armature modifier
            while c.modifiers[0].name != "Decimate":
                bpy.context.view_layer.objects.active = c
                bpy.ops.object.modifier_move_up(modifier="Decimate")
            select_only([c], c)
            bpy.ops.object.modifier_apply(modifier="Decimate")
            copies.append(c)
        lods.append(copies)
        log(f"LOD{li}: ratio {r}: {sum(len(c.data.polygons) for c in copies)} faces")
    return lods


def export_glb(path, arm, meshes):
    for x in bpy.context.view_layer.objects:
        x.hide_set(x not in [arm] + meshes)
    select_only([arm] + meshes, arm)
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_all_influences=True,
                              export_skins=True, export_animations=False, export_morph=False,
                              export_apply=False, export_yup=True)


def finish(o, tpl, meshes):
    global TEX_CACHE
    out = o["out"]
    os.makedirs(out, exist_ok=True)
    TEX_CACHE = os.path.join(out, "textures")
    tex_dirs = [os.path.dirname(os.path.abspath(o["source"]))] + ([o["textures"]] if o.get("textures") else [])
    for m in meshes:                                           # triangulate + clean up before LODs and export
        select_only([m], m)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.quads_convert_to_tris()
        bpy.ops.object.mode_set(mode="OBJECT")
    mats = assign_slots(o, tpl, meshes, tex_dirs)
    lods = make_lods(o, meshes)
    files = []
    for li, lm in enumerate(lods):
        p = os.path.join(out, f"lod{li}.glb")
        export_glb(p, tpl.arm, lm)
        files.append(p)
    man = {"kind": o["kind"], "template": o["template"], "source": o["source"], "lods": files,
           "slots": mats["slots"], "sets": mats["sets"], "extras": o.get("extras", {})}
    json.dump(man, open(os.path.join(out, "manifest.json"), "w"), indent=1)
    log("wrote", out, [os.path.basename(f) for f in files])


# ---- weapon ---------------------------------------------------------------------------------------------------------

PART_RULES = [(r"mag(azine)?|clip|drum", "mag"), (r"bolt|slide", "bolt"), (r"trigger", "trigger"),
              (r"charg(ing)?[_ ]?handle|cocking", "charging_handle"), (r"safety|selector|fire[_ ]?mode", "safety_selector"),
              (r"ejector|ejection|dust[_ ]?cover", "ejection_port_door"), (r"hammer", "hammer"),
              (r"stock", "stock"), (r"cylinder", "cylinder")]
MARKERS = ("muzzle", "shell_eject", "mag", "ironsights", "scope", "laser", "flashlight", "grip", "trigger")
AXES = {"+x": Vector((1, 0, 0)), "-x": Vector((-1, 0, 0)), "+y": Vector((0, 1, 0)), "-y": Vector((0, -1, 0)),
        "+z": Vector((0, 0, 1)), "-z": Vector((0, 0, -1))}


def fit_weapon(o):
    tpl = Template(o["template"])
    src_objs = [x for x in import_any(o["source"]) if x.name in bpy.data.objects]
    apply_transforms([x for x in src_objs if x.type in ("MESH", "EMPTY")])
    meshes = [x for x in src_objs if x.type == "MESH"]
    fix_inverted_normals(meshes)
    for x in src_objs:
        if x.type == "ARMATURE":
            log("ignoring the source armature: weapon parts are bound rigidly by object name")
    # template geometry: weapon bones and extent come from the template mesh
    tb = set(tpl.bones)
    main = next((b for b in ("gun", "weapon", "root") if b in tb and b != "root"), None) or "gun"
    if "gun" in tb: main = "gun"
    tverts = [tm.matrix_world @ v.co for tm in tpl.meshes for v in tm.data.vertices]
    tlo = Vector((min(p.x for p in tverts), min(p.y for p in tverts), min(p.z for p in tverts)))
    thi = Vector((max(p.x for p in tverts), max(p.y for p in tverts), max(p.z for p in tverts)))
    # orientation: source forward/up -> template +X / +Z (glTF import of a B4B export: UE X = Blender X, UE Z = Blender Z)
    fwd = AXES[o.get("forward", "+x")]; up = AXES[o.get("up", "+z")]
    side = up.cross(fwd)
    Rsrc = Matrix((fwd, side, up)).transposed()
    R = Rsrc.inverted().to_4x4()
    for x in src_objs:
        if x.parent is None: x.matrix_world = R @ x.matrix_world
    apply_transforms([x for x in src_objs if x.type in ("MESH", "EMPTY")])
    sverts = [m.matrix_world @ v.co for m in meshes for v in m.data.vertices]
    slo = Vector((min(p.x for p in sverts), min(p.y for p in sverts), min(p.z for p in sverts)))
    shi = Vector((max(p.x for p in sverts), max(p.y for p in sverts), max(p.z for p in sverts)))
    sc = o.get("scale", "fit")
    k = (thi.x - tlo.x) / max(1e-6, shi.x - slo.x) if sc == "fit" else float(sc)
    # anchor: source trigger -> template trigger bone (else bounding-box centre -> template bbox centre)
    trig_src = next((x for x in src_objs if re.search(r"trigger", x.name, re.I)), None)
    anchor = o.get("anchor", "trigger")
    if anchor == "trigger" and trig_src is not None and "trigger" in tpl.pos:
        a_s = obj_center(trig_src)
        a_t = tpl.pos["trigger"]
    else:
        a_s = (slo + shi) / 2; a_t = (tlo + thi) / 2
    G = Matrix.Translation(a_t) @ Matrix.Scale(k, 4) @ Matrix.Translation(-a_s)
    for x in src_objs:
        if x.parent is None: x.matrix_world = G @ x.matrix_world
    apply_transforms([x for x in src_objs if x.type in ("MESH", "EMPTY")])
    log(f"weapon: scale {k:.3f} (source length {(shi.x - slo.x):.3f} m, template {(thi.x - tlo.x):.3f} m), "
        f"anchor {anchor if trig_src is not None else 'bbox'}")
    # markers -> positions (UE cm, component space of the template skeleton = Blender metres * 100, y/z as is)
    markers = {}
    for x in src_objs:
        n = x.name.lower()
        for mk in MARKERS:
            if x.type == "EMPTY" and re.search(mk, n):
                markers[mk] = list(x.matrix_world.translation)
    if "muzzle" not in markers:
        # front-most point of the model, at the height/side of the barrel end
        front = max(sverts_now(meshes), key=lambda p: p.x)
        near = [p for p in sverts_now(meshes) if p.x > front.x - 0.01]
        markers["muzzle"] = [front.x, sum(p.y for p in near) / len(near), sum(p.z for p in near) / len(near)]
        log("muzzle marker: none in the source, using the barrel tip", [round(v, 3) for v in markers["muzzle"]])
    # bind parts
    rules = [(re.compile(a, re.I), b) for a, b in PART_RULES]
    for spec in o.get("part", []):
        a, b = spec.split("=", 1); rules.insert(0, (re.compile(a, re.I), b))
    bound = []
    for m in meshes:
        bone = main
        for rx, b in rules:
            if rx.search(m.name) and b in tb: bone = b; break
        m.parent = None
        m.matrix_world = m.matrix_world
        m.vertex_groups.clear()
        g = m.vertex_groups.new(name=bone); g.add(range(len(m.data.vertices)), 1.0, "REPLACE")
        m.parent = tpl.arm; m.matrix_parent_inverse = tpl.arm.matrix_world.inverted()
        md = m.modifiers.new("Armature", "ARMATURE"); md.object = tpl.arm
        bound.append((m.name, bone))
    log("parts:", bound)
    for x in src_objs:
        if x.type == "EMPTY": bpy.data.objects.remove(x)
    tpl.hide_meshes()
    o["extras"] = {"markers_m": markers, "bone_positions_m": {b: list(p) for b, p in tpl.pos.items()},
                   "parts": bound, "scale": k}
    finish(o, tpl, meshes)


def obj_center(x):
    if x.type != "MESH": return x.matrix_world.translation.copy()
    vs = [x.matrix_world @ v.co for v in x.data.vertices]
    return sum(vs, Vector()) / len(vs)


def sverts_now(meshes):
    return [m.matrix_world @ v.co for m in meshes for v in m.data.vertices]


# ---- inspect --------------------------------------------------------------------------------------------------------

def alpha_stats(path):
    """(fraction of texels with alpha < 0.5, fraction with 0.05 < alpha < 0.95) of an image, None without alpha."""
    import numpy as np
    try:
        img = bpy.data.images.load(path, check_existing=False)
    except RuntimeError:
        return None
    if img.channels < 4 or not img.size[0]:
        bpy.data.images.remove(img); return None
    if img.size[0] * img.size[1] > 1024 * 1024:
        img.scale(max(1, img.size[0] // 2), max(1, img.size[1] // 2))
    a = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    al = a[3::4]
    return float((al < 0.5).mean()), float(((al > 0.05) & (al < 0.95)).mean())


def inspect(src, out, tex_user=None):
    global TEX_CACHE
    TEX_CACHE = os.path.join(os.path.dirname(out), "textures")
    objs = import_any(src)
    tex_dirs = [os.path.dirname(os.path.abspath(src))]
    info = {"objects": [], "materials": [], "armatures": [], "material_info": {}}
    mats = []
    for o in objs:
        if o.type == "MESH" and not o.name.startswith("Icosphere"):
            info["objects"].append({"name": o.name, "vertices": len(o.data.vertices), "faces": len(o.data.polygons),
                                    "materials": [re.sub(r"\.\d{3}$", "", m.name) if m else None
                                                  for m in o.data.materials],
                                    "parent": o.parent.name if o.parent else None,
                                    "shape_keys": len(o.data.shape_keys.key_blocks) if o.data.shape_keys else 0})
            for m in used_materials(o):
                n = re.sub(r"\.\d{3}$", "", m.name) if m else NO_MATERIAL
                if n not in info["materials"]: info["materials"].append(n); mats.append(m)
        elif o.type == "ARMATURE":
            info["armatures"].append({"name": o.name, "bones": [b.name for b in o.data.bones]})
        elif o.type == "EMPTY":
            info["objects"].append({"name": o.name, "empty": True, "parent": o.parent.name if o.parent else None})
    faces, bare = {}, []
    for o in objs:
        if o.type != "MESH": continue
        for poly in o.data.polygons:
            if poly.material_index < len(o.data.materials) and o.data.materials[poly.material_index]:
                n = re.sub(r"\.\d{3}$", "", o.data.materials[poly.material_index].name)
            else:
                n = NO_MATERIAL
                if o.name not in bare: bare.append(o.name)
            faces[n] = faces.get(n, 0) + 1
    if bare:
        info["material_info"][NO_MATERIAL] = {"textures": {}, "faces": faces.get(NO_MATERIAL, 0), "objects": bare}
    real = [m for m in mats if m is not None]
    for m in real + ([None] if NO_MATERIAL in info["materials"] else []):
        n = re.sub(r"\.\d{3}$", "", m.name) if m else NO_MATERIAL
        tx = material_textures(m, tex_dirs, tex_user, len(real))
        if m is None:
            if NO_MATERIAL in info["material_info"]: info["material_info"][n]["textures"] = tx
            continue
        mi = {"textures": tx, "faces": faces.get(n, 0), "blend": getattr(m, "blend_method", "OPAQUE"),
              "factor": basecolor_factor(m)}
        ap = tx.get("alpha") or tx.get("basecolor")
        if ap and os.path.isfile(ap):
            st = alpha_stats(ap)
            if st: mi["alpha_clear"], mi["alpha_soft"] = st
        info["material_info"][n] = mi
    json.dump(info, open(out, "w"), indent=1)
    log("wrote", out)


# ---- texture composition (numpy; no Pillow needed) --------------------------------------------------------------------

def load_px(path, size, gray=False, height=None):
    """Image file (PNG, JPEG, TGA, DDS ...) -> float32 array (height or size, size, 4), top row first, 0..1 as stored
    (no colour management)."""
    import numpy as np
    h = height or size
    try:
        img = bpy.data.images.load(path, check_existing=False)
    except RuntimeError as e:
        raise SystemExit(f"{path}: Blender can't read this image ({e}); convert it to PNG")
    img.colorspace_settings.name = "Non-Color"
    if not img.size[0]:
        bpy.data.images.remove(img)
        raise SystemExit(f"{path}: Blender can't read this image (a DDS in a format it doesn't know?); convert it to PNG")
    if img.size[0] != size or img.size[1] != h:
        img.scale(size, h)
    a = np.empty(size * h * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    a = a.reshape(h, size, 4)[::-1]
    return a


def tile_px(path, pw, rep=None):
    """A tile's image (pw x pw): the file, or rep [nx, ny] repeats of it (a tiling texture in an atlas tile)."""
    import numpy as np
    nx, ny = rep or (1, 1)
    if (nx, ny) == (1, 1): return load_px(path, pw)
    a = load_px(path, max(1, pw // nx), height=max(1, pw // ny))
    t = np.tile(a, (ny, nx, 1))
    if t.shape[0] != pw or t.shape[1] != pw:
        t = t[(np.arange(pw) * t.shape[0]) // pw][:, (np.arange(pw) * t.shape[1]) // pw]
    return t


def decode_normal(n, what=""):
    """A normal map texel array -> tangent-space RGB (OpenGL/DirectX as stored). Understood: RGB normal maps,
    two-channel (BC5/ATI2: B empty) and DXT5nm (X in alpha, R constant) with Z rebuilt; anything else (a flow or
    specular map named _n) is left out (None)."""
    import numpy as np
    r, g, b, a = n[..., 0], n[..., 1], n[..., 2], n[..., 3]
    if b.mean() > 0.6:
        return n[..., :3]
    if r.std() < 0.02 and a.std() > 0.05:
        x, y, kind = a, g, "DXT5nm (X in alpha)"
    elif b.std() < 0.02:
        x, y, kind = r, g, "two-channel (BC5)"
    else:
        log(f"  {what}: not a normal map (blue channel {b.mean():.2f} on average, a normal map's is about 1): left out")
        return None
    z = np.sqrt(np.clip(1.0 - (2 * x - 1) ** 2 - (2 * y - 1) ** 2, 0.0, 1.0)) * 0.5 + 0.5
    log(f"  {what}: {kind} normal map, Z rebuilt")
    return np.stack([x, y, z], axis=-1)


def image_mean(path):
    import numpy as np
    img = bpy.data.images.load(path, check_existing=False)
    img.colorspace_settings.name = "Non-Color"
    a = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return a.reshape(-1, 4).mean(axis=0)


def save_px(arr, path):
    import numpy as np
    h, w = arr.shape[:2]
    img = bpy.data.images.new("out", w, h, alpha=True)
    img.colorspace_settings.name = "Non-Color"
    img.pixels.foreach_set(np.ascontiguousarray(arr[::-1]).astype(np.float32).ravel())
    img.filepath_raw = path; img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def lin2srgb(c):
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def tinted(rgb, val):
    """sRGB texels x the material's base colour factor (linear, as glTF/Blender multiply), back to sRGB."""
    f = val.get("basecolor_factor")
    if not f: return rgb
    import numpy as np
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4) * np.array(f, np.float32)
    return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.maximum(lin, 0) ** (1 / 2.4) - 0.055).astype(np.float32)


def tile_alpha(tx, pw, rep=None):
    """A tile's opacity: its alpha texture (A if it has an alpha channel, else R), else the base colour's alpha."""
    import numpy as np
    for k in ("alpha", "basecolor"):
        p = tx.get(k)
        if p and os.path.exists(p):
            a = tile_px(p, pw, rep)
            if a[..., 3].min() < 0.99: return a[..., 3]
            if k == "alpha": return a[..., 0]
    return np.ones((pw, pw), np.float32)


def hair_multimask(j, size):
    """Master_Hair_M's "Hair MultiMask" (Enable MultiMask on): A = strand alpha (masked, dithered). R/G/B (root, depth,
    id-style masks) = the retail texture's average inside its strands. Also writes <out>.json with the hair colour
    (mean base colour of the opaque texels, linear) for the MI's RootColor/TipColor."""
    import numpy as np
    canvas = np.zeros((size, size, 4), np.float32)
    rgb = np.array([0.45, 0.28, 0.3], np.float32)
    if j.get("mean_from"):
        r = load_px(j["mean_from"], 256)
        m = r[..., 3] > 0.5
        if m.any(): rgb = r[..., :3][m].mean(axis=0)
    canvas[..., :3] = rgb
    cols, weights = [], []
    for t in j["tiles"]:
        x0, y0, w, h = t["rect"]
        px, py, pw = int(round(x0 * size)), int(round(y0 * size)), int(round(w * size))
        tx, val = t.get("textures", {}), t.get("values", {})
        rep = t.get("repeat")
        a = tile_alpha(tx, pw, rep)
        canvas[py:py + pw, px:px + pw, 3] = a
        if tx.get("basecolor") and os.path.exists(tx["basecolor"]):
            c = tinted(tile_px(tx["basecolor"], pw, rep)[..., :3], val)
            m = a > 0.5
            if m.any():
                cols.append(c[m].mean(axis=0)); weights.append(float(m.sum()))
        elif "basecolor" in val:
            cols.append(np.array([lin2srgb(x) for x in val["basecolor"]])); weights.append(1.0)
    srgb = np.average(np.array(cols), axis=0, weights=weights) if cols else np.array([0.3, 0.2, 0.12])
    lin = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in srgb.tolist()]
    json.dump({"color_linear": lin, "color_srgb": srgb.tolist(), "retail_rgb": rgb.tolist()}, open(j["out"] + ".json", "w"))
    log("hair: colour (sRGB)", [round(x, 3) for x in srgb.tolist()], "multimask RGB", [round(float(x), 3) for x in rgb])
    return canvas


def fill_transparent(rgb, a, thresh=0.5):
    """Colour of the see-through texels = the colour of the nearest strands (pull-push over a mip pyramid), so mips
    and filtering at strand edges blend strand colour, not whatever the see-through texels held (often black/white)."""
    import numpy as np
    w = (a >= thresh).astype(np.float32)
    if w.all() or not w.any(): return rgb
    levels = [(rgb * w[..., None], w)]
    while levels[-1][1].shape[0] > 1:
        c, ww = levels[-1]
        h = c.shape[0] // 2
        c2 = c[:h * 2, :h * 2].reshape(h, 2, h, 2, 3).sum(axis=(1, 3))
        w2 = ww[:h * 2, :h * 2].reshape(h, 2, h, 2).sum(axis=(1, 3))
        levels.append((c2, w2))
    fill = levels[-1][0] / np.maximum(levels[-1][1], 1e-6)[..., None]
    for c, ww in reversed(levels[:-1]):
        up = np.repeat(np.repeat(fill, 2, axis=0), 2, axis=1)[:c.shape[0], :c.shape[1]]
        own = c / np.maximum(ww, 1e-6)[..., None]
        fill = np.where((ww > 0)[..., None], own, up)
    out = rgb.copy()
    m = w == 0
    out[m] = fill[m]
    return out


def hair_basecolor(j, size):
    """Hair on a colour-textured masked material (b4bmodel --hair texture): RGB = the model's hair colour texture
    (sRGB as is), A = its alpha (strand coverage: masked + dithered in game); see-through texels take the colour of
    the nearest strands. Also writes <out>.json with the mean colour of the opaque texels (log / preview)."""
    import numpy as np
    canvas = np.zeros((size, size, 4), np.float32)
    canvas[..., :3] = 0.23
    cols, weights = [], []
    for t in j["tiles"]:
        x0, y0, w, h = t["rect"]
        px, py, pw = int(round(x0 * size)), int(round(y0 * size)), int(round(w * size))
        tx, val = t.get("textures", {}), t.get("values", {})
        rep = t.get("repeat")
        a = tile_alpha(tx, pw, rep)
        region = canvas[py:py + pw, px:px + pw]
        if tx.get("basecolor") and os.path.exists(tx["basecolor"]):
            region[..., :3] = tinted(tile_px(tx["basecolor"], pw, rep)[..., :3], val)
        elif "basecolor" in val:
            region[..., :3] = [lin2srgb(c) for c in val["basecolor"]]
        region[..., 3] = a
        m = a > 0.5
        if m.any():
            cols.append(region[..., :3][m].mean(axis=0)); weights.append(float(m.sum()))
    canvas[..., :3] = fill_transparent(canvas[..., :3], canvas[..., 3])
    srgb = np.average(np.array(cols), axis=0, weights=weights) if cols else np.array([0.3, 0.2, 0.12])
    json.dump({"color_srgb": srgb.tolist(), "opaque": float(np.mean(canvas[..., 3] > 0.5))}, open(j["out"] + ".json", "w"))
    log("hair: colour texture, mean (sRGB)", [round(float(x), 3) for x in srgb], "coverage",
        round(float(np.mean(canvas[..., 3] > 0.5)), 3))
    return canvas


def compose(job_path):
    """Jobs: [{out, size, role basecolor|normal|pbr|zero|mean|hairmm|hairbc, tiles, mean_from, normal_dx}]"""
    import numpy as np
    jobs = json.load(open(job_path))
    for j in jobs:
        size = j["size"]
        mean = image_mean(j["mean_from"]) if j.get("mean_from") else np.array([0.5, 0.5, 0.5, 1.0])
        role = j["role"]
        if role == "zero":
            canvas = np.zeros((size, size, 4), np.float32)
        elif role == "hairmm":
            canvas = hair_multimask(j, size)
        elif role == "hairbc":
            canvas = hair_basecolor(j, size)
        elif role == "mean":
            canvas = np.tile(mean.astype(np.float32), (size, size, 1))
        else:
            fill = {"normal": (0.5, 0.5, 1.0, 1.0), "basecolor": (0.23, 0.23, 0.23, mean[3]),
                    "pbr": (1.0, 0.7, 0.0, mean[3])}[role]
            canvas = np.tile(np.array(fill, np.float32), (size, size, 1))
            for t in j["tiles"]:
                x0, y0, w, h = t["rect"]
                px, py, pw = int(round(x0 * size)), int(round(y0 * size)), int(round(w * size))
                tx, val = t.get("textures", {}), t.get("values", {})
                ok = lambda k: tx.get(k) and os.path.exists(tx[k])
                rep = t.get("repeat")
                load = lambda p: tile_px(p, pw, rep)
                region = canvas[py:py + pw, px:px + pw]
                if role == "basecolor":
                    if ok("basecolor"):
                        region[..., :3] = tinted(load(tx["basecolor"])[..., :3], val)
                    elif "basecolor" in val:
                        region[..., :3] = [lin2srgb(c) for c in val["basecolor"]]
                    region[..., 3] = mean[3]
                elif role == "normal":
                    n = decode_normal(load(tx["normal"]), f"{t['material']}: {os.path.basename(tx['normal'])}") \
                        if ok("normal") else None
                    if n is not None:
                        region[..., 0] = n[..., 0]
                        region[..., 1] = n[..., 1] if j.get("normal_dx") else 1.0 - n[..., 1]
                        region[..., 2] = n[..., 2]
                        region[..., 3] = 1.0
                else:   # pbr: R = AO, G = roughness, B = metallic, A = the retail texture's average
                    # Unity HDRP mask map: R metallic, G AO, B detail mask, A smoothness
                    mask_i = {"ao": 1, "roughness": 3, "metallic": 0}
                    def chan(key, orm_i, const):
                        if ok(key): return load(tx[key])[..., 0]
                        if ok("orm"): return load(tx["orm"])[..., orm_i]
                        if ok("mask"):
                            c = load(tx["mask"])[..., mask_i[key]]
                            return 1.0 - c if key == "roughness" else c
                        if key == "roughness" and ok("gloss"): return 1.0 - load(tx["gloss"])[..., 0]
                        return np.full((pw, pw), const, np.float32)
                    region[..., 0] = chan("ao", 0, 1.0)
                    region[..., 1] = chan("roughness", 1, val.get("roughness", 0.7))
                    region[..., 2] = chan("metallic", 2, val.get("metallic", 0.0) if j.get("kind") != "character" else 0.0)
                    region[..., 3] = mean[3]
        save_px(np.clip(canvas, 0, 1), j["out"])
        log("composed", os.path.basename(j["out"]), role, size)


# ---- convert --------------------------------------------------------------------------------------------------------

def convert(src, dst):
    import_any(src)
    for o in list(bpy.data.objects):
        if o.type == "MESH" and o.name.startswith("Icosphere"): bpy.data.objects.remove(o)
    bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", export_all_influences=True, export_animations=False)
    log("wrote", dst)


if __name__ == "__main__":
    pos, opts = parse(argv)
    if not pos:
        print(__doc__); sys.exit(0)
    reset()
    kind = pos[0]
    opts["kind"] = kind
    if kind == "character":
        fit_character(opts)
    elif kind == "weapon":
        fit_weapon(opts)
    elif kind == "convert":
        convert(pos[1], pos[2])
    elif kind == "inspect":
        inspect(pos[1], pos[2], dict(x.split("=", 1) for x in opts.get("tex", [])))
    elif kind == "compose":
        compose(pos[1])
    else:
        raise SystemExit(f"unknown mode {kind}")
