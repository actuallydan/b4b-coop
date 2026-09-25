"""Headless preview render of a model file (fbx, glb/gltf, obj, blend): front, side and back views side by side,
Cycles on the CPU (works without a GPU). For checking a model before and after the pipeline steps.

  blender -b --python blender/preview.py -- <model> <out.png> [--size 512] [--pose test]
      [--views front,side,back,top,front3q] [--zoom 1.0] [--focus <object name substring>]
  --pose test bends arms, legs, spine and head of a B4B skeleton (skmgltf export or b4bfit output) to check weights.
"""
import bpy, math, os, sys
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out = argv[0], argv[1]
opts = {"size": "512", "views": "front,side,back", "zoom": "1.0", "focus": "", "pose": ""}
i = 2
while i < len(argv):
    opts[argv[i].lstrip("-")] = argv[i + 1]; i += 2
size = int(opts["size"])

bpy.ops.wm.read_factory_settings(use_empty=True)
ext = os.path.splitext(src)[1].lower()
if ext == ".fbx": bpy.ops.import_scene.fbx(filepath=src)
elif ext in (".glb", ".gltf"): bpy.ops.import_scene.gltf(filepath=src)
elif ext == ".obj": bpy.ops.wm.obj_import(filepath=src)
elif ext == ".blend": bpy.ops.wm.open_mainfile(filepath=src)
for o in list(bpy.data.objects):
    if o.type in ("CAMERA", "LIGHT"): bpy.data.objects.remove(o)
# --pose test: bend the B4B skeleton's limbs (checks skin weights: no exploding or stuck vertices)
TEST_POSE = {"upperarm_l": (0, 0, -50), "upperarm_r": (0, 0, 50), "lowerarm_l": (0, 0, -70), "lowerarm_r": (70, 0, 0),
             "hand_l": (0, 60, 0), "spine_02": (0, 25, 0), "neck_01": (0, 0, 25), "head": (20, 0, 0),
             "thigh_l": (0, 0, 45), "calf_l": (0, 0, -70), "thigh_r": (0, 0, -20), "foot_r": (0, 0, 30)}
if opts["pose"] == "test":
    import math as _m
    for a in [o for o in bpy.data.objects if o.type == "ARMATURE"]:
        for name, (x, y, z) in TEST_POSE.items():
            pb = a.pose.bones.get(name)
            if pb:
                pb.rotation_mode = "XYZ"
                pb.rotation_euler = (_m.radians(x), _m.radians(y), _m.radians(z))
    bpy.context.view_layer.update()
for o in list(bpy.data.objects):                  # glTF importer bone-shape helpers
    if o.type == "MESH" and (o.name.startswith("Icosphere") or not o.users_scene or o.hide_render):
        bpy.data.objects.remove(o)
meshes = [o for o in bpy.data.objects if o.type == "MESH" and (not opts["focus"] or opts["focus"] in o.name)]
dg = bpy.context.evaluated_depsgraph_get()
pts = []
for o in meshes:
    ev = o.evaluated_get(dg)
    m = ev.to_mesh()
    pts += [o.matrix_world @ v.co for v in m.vertices]
    ev.to_mesh_clear()
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
c = (lo + hi) / 2
ext_ = max(hi - lo) / float(opts["zoom"])
print("bounds", tuple(round(x, 3) for x in lo), tuple(round(x, 3) for x in hi))

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 16
scene.render.resolution_x = scene.render.resolution_y = size
scene.render.film_transparent = False
world = bpy.data.worlds.new("w"); scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.6, 0.6, 0.62, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
sun.data.energy = 3.0
sun.rotation_euler = (math.radians(50), 0, math.radians(30))
scene.collection.objects.link(sun)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.type = "ORTHO"
cam.data.ortho_scale = ext_ * 1.1
scene.collection.objects.link(cam)
scene.camera = cam
dirs = {"front": Vector((0, -1, 0)), "back": Vector((0, 1, 0)), "side": Vector((1, 0, 0)),
        "left": Vector((-1, 0, 0)), "top": Vector((0, 0, 1)), "front3q": Vector((0.7, -0.7, 0.2)).normalized()}
tiles = []
base = os.path.splitext(out)[0]
for v in opts["views"].split(","):
    d = dirs[v]
    cam.location = c + d * ext_ * 3
    look = (c - cam.location).normalized()
    cam.rotation_euler = look.to_track_quat("-Z", "Y").to_euler()
    sun.rotation_euler = (look * -1 + Vector((0.3, -0.2, 0.6))).normalized().to_track_quat("Z", "Y").to_euler()
    p = f"{base}_{v}.png"
    scene.render.filepath = p
    bpy.ops.render.render(write_still=True)
    tiles.append(p)
# stitch
imgs = [bpy.data.images.load(p) for p in tiles]
import numpy as np
arrs = [np.array(im.pixels[:]).reshape(size, size, 4) for im in imgs]
full = np.concatenate(arrs, axis=1)
res = bpy.data.images.new("out", size * len(arrs), size)
res.pixels[:] = full.ravel()
res.filepath_raw = out; res.file_format = "PNG"; res.save()
for p in tiles: os.remove(p)
print("wrote", out)
