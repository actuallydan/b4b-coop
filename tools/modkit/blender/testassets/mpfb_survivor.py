"""Test asset: a clothed, rigged, textured human built headless with MPFB 2 (MakeHuman for Blender, GPL code, CC0
assets) and written out as a modder would bring it: one FBX (or glb) plus its texture PNGs.

Not part of the mod pipeline; it only makes the "real humanoid" used to test it (mesh-mods.md §6). Nothing it writes
is committed. Needs (outside the repo, see mesh-mods.md §6):
  - MPFB 2 installed as a Blender extension (extensions.blender.org "mpfb"), with BLENDER_USER_RESOURCES pointing at
    the Blender user dir it was installed into;
  - the MakeHuman system asset pack (CC0) unpacked into MPFB's user data dir (clothes/, skins/, hair/, eyes/, ...).

  BLENDER_USER_RESOURCES=... blender -b --python tools/modkit/blender/testassets/mpfb_survivor.py -- <out dir> \
      [--rig mixamo|game_engine] [--format fbx|glb] [--skin <mhmat>] [--hair <mhclo>] [--clothes a.mhclo,b.mhclo]

Result: <out dir>/survivor.fbx (or .glb) + textures/*.png (diffuse/normal per material, the skin with the eyebrows
baked in). Materials are plain Principled BSDF nodes with image textures, so FBX/glTF exporters carry them.
"""
import bpy, bmesh, os, sys, math, re, shutil
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

argv = sys.argv[sys.argv.index("--") + 1:]
out_dir = os.path.abspath(argv[0])
opts = {"rig": "mixamo", "format": "fbx", "skin": "middleage_caucasian_male.mhmat", "hair": "short02.mhclo",
        "clothes": "male_casualsuit05.mhclo,shoes03.mhclo", "eyes": "low-poly.mhclo", "eyebrows": "eyebrow001.mhclo"}
i = 1
while i < len(argv):
    opts[argv[i].lstrip("-")] = argv[i + 1]; i += 2
os.makedirs(os.path.join(out_dir, "textures"), exist_ok=True)

import addon_utils
addon_utils.enable("bl_ext.user_default.mpfb", default_set=True)
from bl_ext.user_default.mpfb.services.humanservice import HumanService

bpy.ops.wm.read_factory_settings(use_empty=True)
addon_utils.enable("bl_ext.user_default.mpfb", default_set=True)

info = HumanService._create_default_human_info_dict()
info["phenotype"].update(gender=1.0, age=0.62, muscle=0.62, weight=0.55, height=0.55, proportions=0.6)
info["phenotype"]["race"] = {"asian": 0.0, "caucasian": 1.0, "african": 0.0}
info["rig"] = opts["rig"]
info["eyes"] = opts["eyes"]
info["eyebrows"] = opts["eyebrows"]
info["hair"] = opts["hair"]
info["clothes"] = [c for c in opts["clothes"].split(",") if c]
info["skin_mhmat"] = opts["skin"]
info["skin_material_type"] = "MAKESKIN"
info["eyes_material_type"] = "MAKESKIN"
info["name"] = "Survivor"
settings = HumanService.get_default_deserialization_settings()
settings["subdiv_levels"] = 0
basemesh = HumanService.deserialize_from_dict(info, settings)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
print("mpfb:", [(o.name, len(o.data.vertices)) for o in meshes], "rig", arm.name, len(arm.data.bones))


def images_of(mat):
    """(diffuse, normal) image file paths from an MPFB material node tree."""
    diff = nrm = None
    if mat and mat.node_tree:
        for n in mat.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.image and n.image.filepath:
                p = bpy.path.abspath(n.image.filepath)
                low = os.path.basename(p).lower()
                if "normal" in low or n.label.lower().startswith("normal") or "normal" in n.name.lower():
                    nrm = nrm or p
                elif "diffuse" in low or "diffuse" in n.name.lower() or "diffuse" in n.label.lower() or diff is None:
                    if not any(k in low for k in ("_ao", "rough", "spec", "bump", "disp", "sss")):
                        diff = diff or p
    return diff, nrm


# ---- apply everything MPFB keeps live: shape keys (targets), delete-group masks, helper masks ----------------------
for o in meshes:
    bpy.context.view_layer.objects.active = o
    for ob in bpy.context.selected_objects: ob.select_set(False)
    o.select_set(True)
    if o.data.shape_keys:
        bpy.ops.object.shape_key_remove(all=True, apply_mix=True)
    for m in list(o.modifiers):
        if m.type in ("MASK", "SUBSURF"):
            try:
                bpy.ops.object.modifier_apply(modifier=m.name)
            except RuntimeError as e:
                print("modifier", m.name, e); o.modifiers.remove(m)

# ---- bake the eyebrows into the skin texture (they are alpha cards; B4B hero heads are opaque) ------------------------
body = next(o for o in meshes if o.name.endswith(".body") or o == basemesh)
brows = next((o for o in meshes if "eyebrow" in o.name.lower()), None)
skin_diff, _ = images_of(body.active_material)
tex_dir = os.path.join(out_dir, "textures")
skin_out = os.path.join(tex_dir, "survivor_skin_diffuse.png")
if skin_diff:
    skin = bpy.data.images.load(skin_diff)
    W, H = skin.size
    px = np.array(skin.pixels[:], dtype=np.float32).reshape(H, W, 4)
    if brows is not None:
        bdiff, _ = images_of(brows.active_material)
        bimg = bpy.data.images.load(bdiff)
        bw, bh = bimg.size
        bpx = np.array(bimg.pixels[:], dtype=np.float32).reshape(bh, bw, 4)
        dg = bpy.context.evaluated_depsgraph_get()
        bm = bmesh.new(); bm.from_object(body, dg); bm.transform(body.matrix_world)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.faces.ensure_lookup_table()
        uvl = bm.loops.layers.uv.active
        tree = BVHTree.FromBMesh(bm)
        bb = bmesh.new(); bb.from_object(brows, dg); bb.transform(brows.matrix_world)
        bmesh.ops.triangulate(bb, faces=bb.faces[:])
        buv = bb.loops.layers.uv.active
        from mathutils.geometry import barycentric_transform
        def skin_uv(p):
            loc, nrm, fi, d = tree.find_nearest(p)
            f = bm.faces[fi]
            a, b, c = (l.vert.co for l in f.loops)
            ua, ub, uc = (Vector((l[uvl].uv.x, l[uvl].uv.y, 0)) for l in f.loops)
            return barycentric_transform(loc, a, b, c, ua, ub, uc)
        n = 0
        for f in bb.faces:
            suv = [skin_uv(l.vert.co) for l in f.loops]
            tuv = [l[buv].uv.copy() for l in f.loops]
            xs = [u.x * W for u in suv]; ys = [u.y * H for u in suv]
            x0, x1 = max(0, int(min(xs))), min(W - 1, int(max(xs)) + 1)
            y0, y1 = max(0, int(min(ys))), min(H - 1, int(max(ys)) + 1)
            if x1 - x0 > 200 or y1 - y0 > 200: continue          # a card spanning a seam: skip
            (ax, ay), (bx, by_), (cx, cy) = [(u.x * W, u.y * H) for u in suv]
            den = (by_ - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-9: continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            l1 = ((by_ - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
            l2 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
            l3 = 1 - l1 - l2
            inside = (l1 >= -0.01) & (l2 >= -0.01) & (l3 >= -0.01)
            if not inside.any(): continue
            tu = l1 * tuv[0].x + l2 * tuv[1].x + l3 * tuv[2].x
            tv = l1 * tuv[0].y + l2 * tuv[1].y + l3 * tuv[2].y
            sx = np.clip((tu * bw).astype(int), 0, bw - 1); sy = np.clip((tv * bh).astype(int), 0, bh - 1)
            src = bpx[sy, sx]
            a = src[..., 3:4] * inside[..., None] * 0.9
            region = px[y0:y1 + 1, x0:x1 + 1]
            region[..., :3] = region[..., :3] * (1 - a) + src[..., :3] * a
            n += 1
        print("eyebrows baked:", n, "triangles")
        bpy.data.objects.remove(brows)
        meshes.remove(brows)
    skin.pixels[:] = px.ravel()
    skin.filepath_raw = skin_out; skin.file_format = "PNG"; skin.save()

# ---- plain materials for the exporters --------------------------------------------------------------------------
def plain_material(name, diff, nrm):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    if diff:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = bpy.data.images.load(diff)
        nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
        if name.lower().startswith(("hair", "short", "eyebrow", "eyelash")):
            nt.links.new(t.outputs["Alpha"], bsdf.inputs["Alpha"])
    if nrm:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = bpy.data.images.load(nrm)
        t.image.colorspace_settings.name = "Non-Color"
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(t.outputs["Color"], nm.inputs["Color"]); nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Roughness"].default_value = 0.8
    return m


for o in meshes:
    mat = o.active_material
    diff, nrm = images_of(mat)
    if o == body:
        diff = skin_out if skin_diff else diff
    part = re.sub(r"^Survivor\.", "", o.name).split(".")[0].lower() or "part"
    newd = newn = None
    if diff:
        newd = os.path.join(tex_dir, os.path.basename(diff)); shutil.copyfile(diff, newd) if diff != newd else None
    if nrm:
        newn = os.path.join(tex_dir, os.path.basename(nrm)); shutil.copyfile(nrm, newn)
    o.data.materials.clear()
    o.data.materials.append(plain_material(part, newd, newn))
    print("material", o.name, "->", part, os.path.basename(newd or "-"), os.path.basename(newn or "-"))

for o in list(bpy.data.objects):
    if o.type not in ("MESH", "ARMATURE"): bpy.data.objects.remove(o)
out = os.path.join(out_dir, "survivor." + opts["format"])
if opts["format"] == "fbx":
    bpy.ops.export_scene.fbx(filepath=out, use_selection=False, add_leaf_bones=False, path_mode="RELATIVE",
                             mesh_smooth_type="FACE", use_armature_deform_only=True, bake_anim=False)
else:
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_all_influences=True)
print("wrote", out)
