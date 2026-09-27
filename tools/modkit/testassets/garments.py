"""Test asset: puts a generated long coat (open at the front below the waist) or a cape on a rigged character, for
testing cloth on coats and capes (mesh-mods.md §14). Output is CC0 when the input is (made here: geometry built from
the body's own cross-sections, texture drawn by this script). Not part of the mod pipeline; nothing it writes is
committed.

  blender -b --python tools/modkit/testassets/garments.py -- <model.fbx|.glb|.vrm> <out.glb> coat|cape|coat,cape
      [--forward -y] [--color 0.18,0.12,0.08]

The garment is a surface of rings around the body (arms left out) with some clearance that grows towards the hem,
skinned with the nearest body vertices' weights, material "Coat" / "Cape" with a woven-looking texture; the result is
written as one glb (textures embedded) next to which the pipeline finds everything.
"""
import bpy, bmesh, math, os, re, sys
import numpy as np
from mathutils import Vector
from mathutils.kdtree import KDTree

argv = sys.argv[sys.argv.index("--") + 1:]
src, out, kinds = argv[0], os.path.abspath(argv[1]), argv[2].split(",")
opts = {"forward": "-y", "color": ""}
i = 3
while i < len(argv):
    opts[argv[i].lstrip("-")] = argv[i + 1]; i += 2

ARM_RX = re.compile(r"(arm|hand|elbow|wrist|shoulder|clavicle|thumb|index|middle|ring|pinky|little|finger)", re.I)

bpy.ops.wm.read_factory_settings(use_empty=True)
ext = os.path.splitext(src)[1].lower()
if ext == ".fbx": bpy.ops.import_scene.fbx(filepath=src)
else: bpy.ops.import_scene.gltf(filepath=src)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
bodies = [o for o in bpy.data.objects if o.type == "MESH" and o.find_armature() == arm]
fwd = {"-y": Vector((0, -1, 0)), "+y": Vector((0, 1, 0)), "+x": Vector((1, 0, 0)), "-x": Vector((-1, 0, 0))}[opts["forward"]]

# body samples (world), their weights, and whether they belong to an arm
P, Wt, is_arm = [], [], []
for o in bodies:
    names = {g.index: g.name for g in o.vertex_groups}
    mw = o.matrix_world
    for v in o.data.vertices:
        w = {names[g.group]: g.weight for g in v.groups if g.weight > 0 and g.group in names}
        if not w: continue
        P.append(mw @ v.co); Wt.append(w)
        s = sum(w.values())
        is_arm.append(sum(x for n, x in w.items() if ARM_RX.search(n)) / s > 0.4)
zs = [p.z for p in P]
z0, z1 = min(zs), max(zs)
H = z1 - z0
torso = [p for p, a in zip(P, is_arm) if not a]
band = [p for p in torso if abs(p.z - (z0 + 0.55 * H)) < 0.03 * H]
cx = sum(p.x for p in band) / len(band); cy = sum(p.y for p in band) / len(band)
base_ang = math.atan2(fwd.y, fwd.x)
kd = KDTree(len(P))
for k, p in enumerate(P): kd.insert(p, k)
kd.balance()
SECT = 48


def radius_profile(zlo, zhi, dz):
    """Outermost non-arm body radius per (height band, angle sector) around the torso axis."""
    nz = int(round((zhi - zlo) / dz)) + 1
    R = [[0.0] * SECT for _ in range(nz)]
    for p in torso:
        if not (zlo - dz <= p.z <= zhi + dz): continue
        k = min(nz - 1, max(0, int(round((zhi - p.z) / dz))))
        a = (math.atan2(p.y - cy, p.x - cx) - base_ang) % (2 * math.pi)
        j = int(a / (2 * math.pi) * SECT) % SECT
        R[k][j] = max(R[k][j], math.hypot(p.x - cx, p.y - cy))
    for k in range(nz):                                  # fill empty sectors from neighbours, then smooth
        for _ in range(SECT):
            if all(R[k]): break
            R[k] = [r or max(R[k][(j - 1) % SECT], R[k][(j + 1) % SECT]) for j, r in enumerate(R[k])]
        R[k] = [max(R[k][(j + d) % SECT] for d in (-1, 0, 1)) for j in range(SECT)]
    for k in range(1, nz):                               # never narrower than the ring above by much (drapes)
        R[k] = [max(r, R[k - 1][j] * 0.97) for j, r in enumerate(R[k])]
    for _ in range(3):                                   # smooth over height (no steps between bands) and angle
        R = [[max(R[k][j], sum(R[kk][j] for kk in range(max(0, k - 3), min(nz, k + 4))) /
                  (min(nz, k + 4) - max(0, k - 3))) for j in range(SECT)] for k in range(nz)]
        R = [[(R[k][(j - 1) % SECT] + 2 * R[k][j] + R[k][(j + 1) % SECT]) / 4 for j in range(SECT)] for k in range(nz)]
    return R, nz


def garment(kind):
    if kind == "coat":
        ztop, zhem, z_open = z0 + 0.74 * H, z0 + 0.22 * H, z0 + 0.56 * H
        arc = (math.radians(0), math.radians(360))
    else:
        ztop, zhem, z_open = z0 + 0.80 * H, z0 + 0.18 * H, 1e9
        arc = (math.radians(95), math.radians(265))      # behind the body
    dz = 0.02
    R, nz = radius_profile(zhem, ztop, dz)
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    grid = []
    gap = math.radians(28)
    for k in range(nz):
        z = ztop - k * dz
        t = k / max(1, nz - 1)
        row = []
        for j in range(SECT + 1):
            a = arc[0] + (arc[1] - arc[0]) * j / SECT
            aa = a % (2 * math.pi)
            if kind == "coat" and z < z_open and (aa < gap or aa > 2 * math.pi - gap):
                row.append(None); continue               # the open front below the waist
            jj = int(aa / (2 * math.pi) * SECT) % SECT
            r = R[k][jj] + 0.015 + 0.06 * t * t          # clearance, flaring towards the hem
            wa = a + base_ang
            row.append(bm.verts.new((cx + r * math.cos(wa), cy + r * math.sin(wa), z)))
        grid.append(row)
    for k in range(nz - 1):
        for j in range(SECT):
            q = [grid[k][j], grid[k][j + 1], grid[k + 1][j + 1], grid[k + 1][j]]
            if any(v is None for v in q): continue
            if kind == "coat" and j == SECT - 1: q = [grid[k][j], grid[k][0], grid[k + 1][0], grid[k + 1][j]]
            if len(set(q)) < 4: continue
            try:
                f = bm.faces.new(q)
            except ValueError:
                continue
            for lp, (u, v) in zip(f.loops, ((j, k), (j + 1, k), (j + 1, k + 1), (j, k + 1))):
                lp[uv].uv = (u / SECT * 4, 1 - v / nz * 4)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for f in bm.faces: f.smooth = True           # (flat faces export as a triangle soup: LOD decimation shreds it)
    me = bpy.data.meshes.new(kind.title())
    bm.to_mesh(me); bm.free()
    ob = bpy.data.objects.new(kind.title(), me)
    bpy.context.scene.collection.objects.link(ob)
    # outward normals (recalc may pick either side for an open sheet)
    out_votes = sum(1 if (p.normal.x * (p.center.x - cx) + p.normal.y * (p.center.y - cy)) > 0 else -1
                    for p in me.polygons)
    if out_votes < 0:
        for p in me.polygons: p.flip()
    # skinning: nearest non-arm body vertices
    groups = {}
    for v in me.vertices:
        acc = {}
        for co, idx, d in kd.find_n(v.co, 12):
            if is_arm[idx]: continue
            f = 1.0 / max(d, 1e-3)
            for n, x in Wt[idx].items(): acc[n] = acc.get(n, 0.0) + x * f
        top = sorted(acc.items(), key=lambda x: -x[1])[:4]
        s = sum(x for _, x in top) or 1.0
        for n, x in top:
            g = groups.get(n) or groups.setdefault(n, ob.vertex_groups.new(name=n))
            g.add([v.index], x / s, "REPLACE")
    ob.parent = arm
    mod = ob.modifiers.new("Armature", "ARMATURE"); mod.object = arm
    # material + texture: a twill-like weave in the garment colour
    col = [float(x) for x in opts["color"].split(",")] if opts["color"] else \
        ([0.16, 0.11, 0.07] if kind == "coat" else [0.35, 0.03, 0.04])
    n = 512
    yy, xx = np.mgrid[0:n, 0:n]
    weave = 0.85 + 0.15 * (((xx + yy) // 3) % 4 < 2)
    noise = np.random.default_rng(7).normal(0, 0.03, (n, n))
    rgb = np.clip(np.stack([c * (weave + noise) for c in col], -1), 0, 1)
    img = bpy.data.images.new(f"{kind}_color", n, n, alpha=True)
    img.pixels = np.concatenate([rgb, np.ones((n, n, 1))], -1)[::-1].reshape(-1).tolist()
    png = os.path.join(os.path.dirname(out), f"{kind}_color.png")
    img.filepath_raw = png; img.file_format = "PNG"; img.save()
    mat = bpy.data.materials.new(kind.title()); mat.use_nodes = True
    tn = mat.node_tree.nodes.new("ShaderNodeTexImage"); tn.image = img
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    mat.node_tree.links.new(tn.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.9
    me.materials.append(mat)
    print(f"garments: {kind}: {len(me.vertices)} vertices, {len(me.polygons)} faces, top {ztop:.2f} m, "
          f"hem {zhem:.2f} m, {len(groups)} bones")


for k in kinds: garment(k)
os.makedirs(os.path.dirname(out), exist_ok=True)
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_skins=True, export_animations=False)
print("garments: wrote", out)
