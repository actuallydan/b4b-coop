"""Blender (headless) test-mesh generator: replace the mesh of a skmgltf.py export with boxes rigidly skinned to
bones (a "new mesh" made in Blender, not derived from the retail one).

  blender -b --python tools/modkit/blender/blocky.py -- <in.glb from skmgltf export> <out.glb> fp|3p

Material names select the template's material slots on import (slot names of the template mesh).
"""
import bpy, sys
from mathutils import Vector, Matrix

argv = sys.argv[sys.argv.index("--") + 1:]
src, dst, mode = argv[0], argv[1], argv[2]

# (bone, end bone or None = bone tail, width, depth in cm, material)
FP = [(s + x) for x in ("_l", "_r") for s in ()] + [
    (f"upperarm{x}", f"lowerarm{x}", 9, 9, "Torso") for x in ("_l", "_r")] + [
    (f"lowerarm{x}", f"hand{x}", 7, 7, "Torso") for x in ("_l", "_r")] + [
    (f"hand{x}", f"middle_01{x}", 8, 3.5, "ArmSkin") for x in ("_l", "_r")] + [
    (f"{f}_01{x}", f"{f}_03{x}", 1.8, 1.8, "ArmSkin") for x in ("_l", "_r") for f in ("index", "middle", "ring", "pinky", "thumb")]
TP = [("pelvis", "spine_02", 30, 20, "Legs"), ("spine_02", "neck_01", 36, 22, "Torso1"),
      ("neck_01", "head", 10, 10, "Head"), ("head", None, 20, 22, "Head")] + [
    (f"clavicle{x}", f"upperarm{x}", 8, 8, "Torso1") for x in ("_l", "_r")] + [
    (f"upperarm{x}", f"lowerarm{x}", 10, 10, "Torso1") for x in ("_l", "_r")] + [
    (f"lowerarm{x}", f"hand{x}", 8, 8, "Torso1") for x in ("_l", "_r")] + [
    (f"hand{x}", f"middle_01{x}", 8, 3.5, "Head") for x in ("_l", "_r")] + [
    (f"thigh{x}", f"calf{x}", 15, 15, "Legs") for x in ("_l", "_r")] + [
    (f"calf{x}", f"foot{x}", 11, 11, "Legs") for x in ("_l", "_r")] + [
    (f"foot{x}", f"ball{x}", 10, 8, "Legs") for x in ("_l", "_r")]
segs = FP if mode == "fp" else TP

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
for o in list(bpy.data.objects):
    if o.type == "MESH": bpy.data.objects.remove(o)
bones = arm.data.bones
mats = {}
parts = []
for b, e, w, d, mname in segs:
    if b not in bones: print("skip", b); continue
    h = arm.matrix_world @ bones[b].head_local
    t = arm.matrix_world @ (bones[e].head_local if e and e in bones else bones[b].tail_local)
    if e is None and b == "head":          # head box: up from the head joint
        t = h + Vector((0, 0, 0.22))
    axis = (t - h)
    L = axis.length
    if L < 1e-4: continue
    z = axis.normalized()
    x = z.orthogonal().normalized(); y = z.cross(x)
    rot = Matrix((x, y, z)).transposed().to_4x4()
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    c = bpy.context.active_object
    c.matrix_world = Matrix.Translation((h + t) / 2) @ rot @ Matrix.Diagonal((w / 100, d / 100, L, 1))
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    vg = c.vertex_groups.new(name=b); vg.add(range(len(c.data.vertices)), 1.0, "REPLACE")
    if mname not in mats: mats[mname] = bpy.data.materials.new(mname)
    c.data.materials.append(mats[mname])
    parts.append(c)
bpy.ops.object.select_all(action="DESELECT")
for p in parts: p.select_set(True)
bpy.context.view_layer.objects.active = parts[0]
bpy.ops.object.join()
body = bpy.context.active_object
body.name = "BlockyBody"
body.parent = arm
mod = body.modifiers.new("Armature", "ARMATURE"); mod.object = arm
print("blocky:", len(body.data.vertices), "verts", len(body.data.polygons), "faces", list(mats))
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", export_all_influences=True)
