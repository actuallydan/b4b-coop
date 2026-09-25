"""Fit a modder's model (FBX, glTF, OBJ, .blend) onto a B4B template skeleton, headless in Blender. Writes glTF files
that `skmgltf.py import` turns into a cooked skeletal mesh, plus a manifest (materials -> template slots, textures,
atlas rectangles) for the texture step. Driven by tools/modkit/b4bmodel.py; usable on its own. mesh-mods.md §6-§7.

  blender -b --python tools/modkit/blender/b4bfit.py -- character --template T.glb --source model.fbx --out DIR
        [--mode 3p|fp] [--bonemap map.json] [--lods 1,0.5,0.25,0.12,0.05] [--slot SRCMAT=SLOT]... [--drop REGEX]
        [--weights source|transfer] [--twist template|none] [--textures DIR] [--facing -y] [--atlas SET=m1,m2]...
        [--slotset SLOT=SET]... [--tex MAT=<prefix|dir>]... [--probe 1]
  blender -b --python tools/modkit/blender/b4bfit.py -- weapon --template T.glb --source gun.fbx --out DIR
        [--forward +x] [--up +z] [--scale fit|<factor>] [--anchor trigger|grip|none] [--part REGEX=BONE]...
        [--slot SRCMAT=SLOT]... [--lods 1,0.5] [--textures DIR]
  blender -b --python tools/modkit/blender/b4bfit.py -- convert <in> <out.glb>
  blender -b --python tools/modkit/blender/b4bfit.py -- inspect <in> <out.json>     objects, materials, bones
  blender -b --python tools/modkit/blender/b4bfit.py -- compose <jobs.json>          texture sets -> PNGs (b4bmodel)

character: the source's armature is mapped onto the template's bones by name (UE4 mannequin names as is, Mixamo,
  3ds Max Biped; or --bonemap {"srcbone": "b4bbone"}), turned to face +X like the template, then posed bone by bone
  so every mapped joint lands on the template's joint (rotation + stretch along the bone); the pose is applied to the
  meshes, so they sit in the template's bind pose. Weights: the source's, renamed; weights of unmapped source bones go
  to their nearest mapped ancestor; with --twist template each limb's weight is split among the template's twist
  bones as the template mesh splits it at the nearest point. An unrigged source (no armature) gets its weights
  from the template mesh (--weights transfer, nearest surface). fp: the same against an FP_Biped export, then only
  faces skinned to the arms are kept.
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


def parse(argv, multi=("slot", "part", "tex", "atlas", "slotset")):
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
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
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
        raise SystemExit(f"unsupported model type {ext} (fbx, glb, gltf, obj, dae, blend)")
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

B4B_MAIN = ["pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "neck_02", "head",
            "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l", "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
            "thigh_l", "calf_l", "foot_l", "ball_l", "thigh_r", "calf_r", "foot_r", "ball_r"]
FINGERS = ["thumb", "index", "middle", "ring", "pinky"]
for s in ("l", "r"):
    for f in FINGERS:
        B4B_MAIN += [f"{f}_0{k}_{s}" for k in (1, 2, 3)]

MIXAMO = {"hips": "pelvis", "spine": "spine_01", "spine1": "spine_02", "spine2": "spine_03", "neck": "neck_01",
          "head": "head"}
BIPED = {"pelvis": "pelvis", "spine": "spine_01", "spine1": "spine_02", "spine2": "spine_03", "neck": "neck_01",
         "head": "head"}
for side, s in (("Left", "l"), ("Right", "r")):
    MIXAMO.update({f"{side}Shoulder": f"clavicle_{s}", f"{side}Arm": f"upperarm_{s}",
                   f"{side}ForeArm": f"lowerarm_{s}", f"{side}Hand": f"hand_{s}", f"{side}UpLeg": f"thigh_{s}",
                   f"{side}Leg": f"calf_{s}", f"{side}Foot": f"foot_{s}", f"{side}ToeBase": f"ball_{s}"})
    for f, m in (("Thumb", "thumb"), ("Index", "index"), ("Middle", "middle"), ("Ring", "ring"), ("Pinky", "pinky")):
        for k in (1, 2, 3):
            MIXAMO[f"{side}Hand{f}{k}"] = f"{m}_0{k}_{s}"
    L = side[0]
    BIPED.update({f"{L} Clavicle": f"clavicle_{s}", f"{L} UpperArm": f"upperarm_{s}", f"{L} Forearm": f"lowerarm_{s}",
                  f"{L} Hand": f"hand_{s}", f"{L} Thigh": f"thigh_{s}", f"{L} Calf": f"calf_{s}",
                  f"{L} Foot": f"foot_{s}", f"{L} Toe0": f"ball_{s}"})
    for fi, m in enumerate(FINGERS):
        for k in range(3):
            BIPED[f"{L} Finger{fi}{'' if k == 0 else k}"] = f"{m}_0{k + 1}_{s}"
MIXAMO = {k.lower(): v for k, v in MIXAMO.items()}
BIPED = {k.lower(): v for k, v in BIPED.items()}


def norm_name(n):
    n = n.split("|")[-1]
    n = re.sub(r"^(mixamorig\d*[:_]|bip0?1[ _]|def[-_]|armature[:_])", "", n, flags=re.I)
    return n.strip().lower()


def build_bonemap(arm, targets, user_map=None):
    """source bone name -> template bone name. targets = set of template bone names."""
    tl = {t.lower(): t for t in targets}
    cands = []
    for table in (None, MIXAMO, BIPED):
        m = {}
        for b in arm.data.bones:
            n = norm_name(b.name)
            t = tl.get(n) if table is None else table.get(n)
            if t is not None and t.lower() in tl:
                m[b.name] = tl[t.lower()]
        cands.append(m)
    best = max(cands, key=len)
    if user_map:
        for k, v in user_map.items():
            if v not in targets: raise SystemExit(f"--bonemap: {v!r} is not a template bone")
            best[k] = v
    # one source bone per target: keep the one nearest the root
    seen = {}
    for sb in sorted(best, key=lambda n: depth_bone(arm.data.bones[n])):
        t = best[sb]
        if t not in seen: seen[t] = sb
    return {sb: t for t, sb in seen.items()}


def depth_bone(b):
    d = 0
    while b.parent: b = b.parent; d += 1
    return d


# segment end used to aim each template bone (its "tail"): the head of this child
AIM = {"pelvis": "spine_01", "spine_01": "spine_02", "spine_02": "spine_03", "spine_03": "neck_01",
       "neck_01": "head", "neck_02": "head"}
for s in ("l", "r"):
    AIM.update({f"clavicle_{s}": f"upperarm_{s}", f"upperarm_{s}": f"lowerarm_{s}", f"lowerarm_{s}": f"hand_{s}",
                f"hand_{s}": f"middle_01_{s}", f"thigh_{s}": f"calf_{s}", f"calf_{s}": f"foot_{s}",
                f"foot_{s}": f"ball_{s}"})
    for f in FINGERS:
        AIM[f"{f}_01_{s}"] = f"{f}_02_{s}"; AIM[f"{f}_02_{s}"] = f"{f}_03_{s}"


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


def fit_character(o):
    tpl = Template(o["template"])
    src_objs = import_any(o["source"])
    src_objs = [x for x in src_objs if x.name in bpy.data.objects]
    if o.get("drop"):
        rx = re.compile(o["drop"], re.I)
        for x in list(src_objs):
            if x.type == "MESH" and rx.search(x.name):
                log("dropping", x.name); bpy.data.objects.remove(x); src_objs.remove(x)
    arms = [x for x in src_objs if x.type == "ARMATURE"]
    meshes = [x for x in src_objs if x.type == "MESH"]
    if not meshes: raise SystemExit("no mesh in the source")
    mode = o.get("mode", "3p")
    targets = set(tpl.bones)
    # FBX files often carry cm scale / axis rotations on the objects: bake them into the data first
    apply_transforms(src_objs)

    if arms:
        sarm = max(arms, key=lambda a: len(a.data.bones))
        user_map = json.load(open(o["bonemap"])) if o.get("bonemap") else None
        bmap = build_bonemap(sarm, targets, user_map)
        missing = [b for b in ("pelvis", "spine_01", "head", "upperarm_l", "lowerarm_l", "hand_l", "upperarm_r",
                               "lowerarm_r", "hand_r", "thigh_l", "calf_l", "foot_l", "thigh_r", "calf_r", "foot_r")
                   if b not in bmap.values()]
        log(f"source armature {sarm.name!r}: {len(sarm.data.bones)} bones, {len(bmap)} mapped")
        if missing:
            raise SystemExit(f"source rig: no bone found for {missing}; pass --bonemap {{\"<source bone>\": "
                             f"\"<b4b bone>\"}} (b4b bones: {sorted(targets)[:12]} ...)")
        inv = {v: k for k, v in bmap.items()}
        smeshes = mesh_children(sarm, meshes)
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
        log(f"orient: rotate {math.degrees(R.to_quaternion().angle):.1f} deg, scale {k:.3f}")
        for x in [sarm] + meshes:
            if x.parent is None or x.parent not in [sarm] + meshes:
                x.matrix_world = G @ x.matrix_world
        apply_transforms([sarm] + meshes)
        for m in others:                           # rigid pieces (glasses, hats): follow the head
            vg = m.vertex_groups.new(name=inv["head"]); vg.add(range(len(m.data.vertices)), 1.0, "REPLACE")
            md = m.modifiers.new("Armature", "ARMATURE"); md.object = sarm
        smeshes = mesh_children(sarm, meshes)
        # 2. pose every mapped bone so its joint lands on the template joint and it aims at the template's next joint
        select_only([sarm], sarm)
        bpy.ops.object.mode_set(mode="EDIT")
        for eb in sarm.data.edit_bones:
            eb.inherit_scale = "NONE"
            eb.use_connect = False
        bpy.ops.object.mode_set(mode="POSE")
        order = sorted(sarm.pose.bones, key=lambda pb: depth_bone(pb.bone))
        spos = {b.name: b.head_local.copy() for b in sarm.data.bones}          # armature space == world now
        worst = 0.0
        for pb in order:
            t = bmap.get(pb.name)
            if t is None: continue
            aim = AIM.get(t)
            head_s = spos[pb.name]
            head_t = tp[t]
            if aim and aim in inv and aim in tp:
                dir_s = spos[inv[aim]] - head_s
                dir_t = tp[aim] - head_t
            else:
                dir_s = dir_t = None
            rest = pb.bone.matrix_local.copy()
            if dir_s is not None and dir_s.length > 1e-6 and dir_t.length > 1e-6:
                q = dir_s.normalized().rotation_difference(dir_t.normalized())
                st = dir_t.length / dir_s.length
            else:
                # no aim joint (end bones, head): keep the parent's rotation change
                par = pb.parent
                while par is not None and par.name not in bmap: par = par.parent
                q = par.matrix.to_quaternion() @ par.bone.matrix_local.to_quaternion().inverted() if par else Quaternion()
                st = 1.0
            # stretch along the bone's own axis, about the head (no shear, so the pose stays loc/rot/scale); rotate;
            # move the head onto the template joint
            ya = rest.col[1].xyz.normalized()
            S = Matrix.Identity(3) + (st - 1.0) * Matrix(((ya.x * ya.x, ya.x * ya.y, ya.x * ya.z),
                                                          (ya.y * ya.x, ya.y * ya.y, ya.y * ya.z),
                                                          (ya.z * ya.x, ya.z * ya.y, ya.z * ya.z)))
            D = Matrix.Translation(head_t) @ q.to_matrix().to_4x4() @ S.to_4x4() @ Matrix.Translation(-head_s)
            pb.matrix = D @ rest
            bpy.context.view_layer.update()
            got = pb.matrix.translation
            worst = max(worst, (got - head_t).length)
        log(f"pose fit: max joint error {worst * 100:.2f} cm")
        bpy.ops.object.mode_set(mode="OBJECT")
        # 3. bake the pose into the meshes
        for m in smeshes:
            select_only([m], m)
            for md in list(m.modifiers):
                if md.type == "ARMATURE" and md.object == sarm:
                    bpy.ops.object.modifier_apply(modifier=md.name)
        # 4. weights: rename to template bones, unmapped -> nearest mapped ancestor
        def target_of(name):
            b = sarm.data.bones.get(name)
            while b is not None and b.name not in bmap: b = b.parent
            return bmap[b.name] if b is not None else "pelvis"
        for m in smeshes:
            rename_groups(m, {g.name: target_of(g.name) for g in m.vertex_groups})
        bpy.data.objects.remove(sarm)
    else:
        # unrigged: stand it like the template (the model faces --facing, default -y = Blender's front view), scale to
        # the template's height, feet on the ground, centred on the pelvis; weights come from the template mesh
        log("source has no armature: placing it by its bounding box, weights from the template mesh (nearest surface)")
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
        tv = [tm.matrix_world @ v.co for tm in tpl.meshes for v in tm.data.vertices]
        th = max(v.z for v in tv) - min(v.z for v in tv)
        k = th / max(1e-6, hi.z - lo.z)
        pel = tpl.pos["pelvis"]
        G = Matrix.Translation(Vector((pel.x, pel.y, min(v.z for v in tv)))) @ Matrix.Scale(k, 4) @ \
            Matrix.Translation(-Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z)))
        for x in meshes:
            if x.parent is None or x.parent not in meshes: x.matrix_world = G @ x.matrix_world
        apply_transforms(meshes)
        log(f"unrigged: scale {k:.3f}; the arms must already match the template's pose (A-pose, about 45 degrees)")
    if o.get("weights") == "transfer":
        transfer_weights(tpl, smeshes, all_groups=True)
    elif o.get("twist", "template") == "template":
        transfer_weights(tpl, smeshes, all_groups=False)
    if mode == "fp":
        for m in smeshes:
            keep_arms(m)
        smeshes = [m for m in smeshes if len(m.data.polygons)]
    if o.get("probe"):
        mats = []
        for m in smeshes:
            for mt in m.data.materials:
                n = re.sub(r"\.\d{3}$", "", mt.name) if mt else None
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


def keep_arms(m):
    W = mesh_weights(m)
    armv = [sum(x for n, x in w.items() if ARM_BONES_RX.match(n)) / max(1e-6, sum(w.values())) >= 0.5 for w in W]
    bm = bmesh.new(); bm.from_mesh(m.data)
    kill = [f for f in bm.faces if not all(armv[v.index] for v in f.verts)]
    bmesh.ops.delete(bm, geom=kill, context="FACES")
    bm.to_mesh(m.data); bm.free()
    log(f"fp: {m.name}: kept {len(m.data.polygons)} arm faces")


# ---- materials, atlas, LODs, export ---------------------------------------------------------------------------------

TEX_KEYS = {"basecolor": ("albedo", "basecolor", "base_color", "diffuse", "color", "_bc", "_d", "col"),
            "normal": ("normal", "_n", "nrm", "norm"),
            "roughness": ("rough",), "metallic": ("metal",), "ao": ("_ao", "occlusion", "ambient"),
            "orm": ("rmao", "orm", "_arm")}


def socket_image(sock, seen=None):
    """Follow links back from a shader socket to an image texture node."""
    if not sock.is_linked: return None
    n = sock.links[0].from_node
    if n.type == "TEX_IMAGE":
        return bpy.path.abspath(n.image.filepath) if n.image and n.image.filepath else (n.image.name if n.image else None)
    for inp in n.inputs:
        if inp.type in ("RGBA", "VECTOR", "VALUE") and inp.is_linked:
            r = socket_image(inp)
            if r: return r
    return None


def classify_files(files):
    out = {}
    for f in sorted(files):
        fl = os.path.basename(f).lower()
        for key, words in TEX_KEYS.items():
            if key not in out and any(w in fl for w in words):
                out[key] = f; break
    return out


def material_textures(mat, tex_dirs, user=None):
    """Texture files of a material: --tex MAT=<prefix|dir> first, then the shader's linked images, then files named
    after the material in the model's folder / --textures."""
    base = re.sub(r"\.\d{3}$", "", mat.name).lower() if mat else ""
    for k, v in (user or {}).items():
        if k.lower() == base:
            files = ([os.path.join(v, f) for f in os.listdir(v)] if os.path.isdir(v) else
                     [os.path.join(os.path.dirname(v), f) for f in os.listdir(os.path.dirname(v) or ".")
                      if f.startswith(os.path.basename(v))])
            got = classify_files([f for f in files if f.lower().endswith((".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp"))])
            if got: return got
    out = {}
    if mat and mat.node_tree:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf:
            for key, sock in (("basecolor", "Base Color"), ("normal", "Normal"), ("roughness", "Roughness"),
                              ("metallic", "Metallic"), ("alpha", "Alpha")):
                p = socket_image(bsdf.inputs[sock])
                if p: out[key] = p
    # files that don't exist (FBX with absolute paths from the author's machine): look next to the model / --textures
    for k, p in list(out.items()):
        if not os.path.isfile(p):
            hit = find_file(os.path.basename(p), tex_dirs)
            if hit: out[k] = hit
            else: del out[k]
    # nothing linked: guess by material name
    if "basecolor" not in out and mat is not None:
        base = re.sub(r"\.\d{3}$", "", mat.name).lower()
        for d in tex_dirs:
            for root, _, files in os.walk(d):
                for f in files:
                    fl = f.lower()
                    if not fl.endswith((".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp")): continue
                    if base.replace(" ", "_") not in fl.replace(" ", "_"): continue
                    for key, words in TEX_KEYS.items():
                        if key not in out and any(w in fl for w in words):
                            out[key] = os.path.join(root, f)
    return out


def bsdf_values(mat):
    """Constant material values (used where the material has no texture for them)."""
    out = {}
    if mat and mat.node_tree:
        b = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if b:
            out["basecolor"] = list(b.inputs["Base Color"].default_value)[:3]
            out["roughness"] = b.inputs["Roughness"].default_value
            out["metallic"] = b.inputs["Metallic"].default_value
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
        for mat in m.data.materials:
            if mat not in srcmats: srcmats.append(mat)

    def base(mat):
        return re.sub(r"\.\d{3}$", "", mat.name if mat else "none")

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
    sets = {}
    slots_used = {}
    for mat in srcmats:
        slot = slot_of[mat]
        st = slotset.get(slot.lower(), slot)
        slots_used[slot] = st
        bn = base(mat).lower()
        if st in atlases:
            names = atlases[st]
            if bn not in names:
                raise SystemExit(f"material {base(mat)!r} goes to slot {slot} (texture set {st}), but --atlas {st}= "
                                 f"doesn't list it")
            g = math.ceil(math.sqrt(len(names)))
            i = names.index(bn)
            rect = ((i % g) / g, (i // g) / g, 1.0 / g, 1.0 / g)
        else:
            g, rect = 1, (0.0, 0.0, 1.0, 1.0)
            if st in sets and sets[st]["tiles"][0]["material"] != base(mat):
                raise SystemExit(f"materials {sets[st]['tiles'][0]['material']!r} and {base(mat)!r} both use texture "
                                 f"set {st}: list them in --atlas {st}=...")
        tiles = sets.setdefault(st, {"grid": g, "tiles": []})["tiles"]
        if not any(t["material"] == base(mat) for t in tiles):
            tiles.append({"material": base(mat), "rect": rect, "textures": material_textures(mat, tex_dirs, tex_user),
                          "values": bsdf_values(mat)})
        slotmat = bpy.data.materials.get(slot) or bpy.data.materials.new(slot)
        for m in meshes:
            uv = m.data.uv_layers.active
            for i, mm in enumerate(m.data.materials):
                if mm != mat: continue
                if g > 1 and uv is not None:
                    x0, y0, w, h = rect
                    oob = 0
                    for poly in m.data.polygons:
                        if poly.material_index != i: continue
                        for li in poly.loop_indices:
                            u, v = uv.data[li].uv
                            if u < -0.01 or u > 1.01 or v < -0.01 or v > 1.01: oob += 1
                            u = min(max(u, 0.0), 1.0); v = min(max(v, 0.0), 1.0)
                            # Blender UV origin is bottom-left, atlas rects are image (top-left) based
                            uv.data[li].uv = (x0 + u * w, 1.0 - (y0 + (1.0 - v) * h))
                    if oob: log(f"  {mat.name}: {oob} UVs outside 0..1 clamped (tiling textures can't be atlased)")
                m.data.materials[i] = slotmat
        log(f"material {base(mat)} -> slot {slot}, texture set {st}" + (f" tile {rect}" if g > 1 else ""))
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


def make_lods(o, meshes):
    ratios = [float(x) for x in o.get("lods", "1").split(",")]
    lods = []
    for li, r in enumerate(ratios):
        if li == 0:
            lods.append(meshes); continue
        copies = []
        for m in meshes:
            c = m.copy(); c.data = m.data.copy(); c.name = f"{m.name}_LOD{li}"
            bpy.context.scene.collection.objects.link(c)
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
    out = o["out"]
    os.makedirs(out, exist_ok=True)
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

def inspect(src, out):
    objs = import_any(src)
    info = {"objects": [], "materials": [], "armatures": []}
    for o in objs:
        if o.type == "MESH" and not o.name.startswith("Icosphere"):
            info["objects"].append({"name": o.name, "vertices": len(o.data.vertices), "faces": len(o.data.polygons),
                                    "materials": [re.sub(r"\.\d{3}$", "", m.name) if m else None
                                                  for m in o.data.materials]})
            for m in o.data.materials:
                n = re.sub(r"\.\d{3}$", "", m.name) if m else None
                if n not in info["materials"]: info["materials"].append(n)
        elif o.type == "ARMATURE":
            info["armatures"].append({"name": o.name, "bones": [b.name for b in o.data.bones]})
        elif o.type == "EMPTY":
            info["objects"].append({"name": o.name, "empty": True})
    json.dump(info, open(out, "w"), indent=1)
    log("wrote", out)


# ---- texture composition (numpy; no Pillow needed) --------------------------------------------------------------------

def load_px(path, size, gray=False):
    """Image file -> float32 array (size, size, 4), top row first, 0..1 as stored (no colour management)."""
    import numpy as np
    img = bpy.data.images.load(path, check_existing=False)
    img.colorspace_settings.name = "Non-Color"
    if img.size[0] != size or img.size[1] != size:
        img.scale(size, size)
    a = np.empty(size * size * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    a = a.reshape(size, size, 4)[::-1]
    return a


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


def compose(job_path):
    """Jobs: [{out, size, role basecolor|normal|pbr|zero|mean, tiles, mean_from, normal_dx}]"""
    import numpy as np
    jobs = json.load(open(job_path))
    for j in jobs:
        size = j["size"]
        mean = image_mean(j["mean_from"]) if j.get("mean_from") else np.array([0.5, 0.5, 0.5, 1.0])
        role = j["role"]
        if role == "zero":
            canvas = np.zeros((size, size, 4), np.float32)
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
                region = canvas[py:py + pw, px:px + pw]
                if role == "basecolor":
                    if ok("basecolor"):
                        region[..., :3] = load_px(tx["basecolor"], pw)[..., :3]
                    elif "basecolor" in val:
                        region[..., :3] = [lin2srgb(c) for c in val["basecolor"]]
                    region[..., 3] = mean[3]
                elif role == "normal":
                    if ok("normal"):
                        n = load_px(tx["normal"], pw)
                        region[..., 0] = n[..., 0]
                        region[..., 1] = n[..., 1] if j.get("normal_dx") else 1.0 - n[..., 1]
                        region[..., 2] = n[..., 2]
                        region[..., 3] = 1.0
                else:   # pbr: R = AO, G = roughness, B = metallic, A = the retail texture's average
                    def chan(key, orm_i, const):
                        if ok(key): return load_px(tx[key], pw)[..., 0]
                        if ok("orm"): return load_px(tx["orm"], pw)[..., orm_i]
                        return np.full((pw, pw), const, np.float32)
                    region[..., 0] = chan("ao", 0, 1.0)
                    region[..., 1] = chan("roughness", 1, val.get("roughness", 0.7))
                    region[..., 2] = chan("metallic", 2, val.get("metallic", 0.0))
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
        inspect(pos[1], pos[2])
    elif kind == "compose":
        compose(pos[1])
    else:
        raise SystemExit(f"unknown mode {kind}")
