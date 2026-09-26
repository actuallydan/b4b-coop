"""Secondary motion for survivor models fitted by b4bfit.py (3P): long hair that swings and skirts that sway.

The game gives heroes two kinds of it, and a custom mesh can use both without new blueprints:
  1. Hair on the survivor's physics bones. Some survivors have a ponytail/braid chain under `head` (Holly, Holly
     Elite 06, Walker Elite 03, Doc Elite 03: hair_00..02; Mom: hair_00_l/r..hair_02_l/r) whose bodies are
     PhysType_Simulated in the survivor's physics asset; the hero anim blueprint's RigidBody node simulates them.
     `rig_hair` skins the model's back hair to that chain (by height below its root, behind the head).
  2. Cloth. A skirt becomes a cloth section: `cloth_region` separates its faces into an object the importer turns into
     its own section, and builds a low-poly simulation mesh around it (rings from the waist down, the waist ring fixed);
     modkit/cloth.py writes it into the template's clothing asset and maps the render vertices onto it.
How it works: docs/investigations/mesh-mods.md §14 (b4b-coop repository).
"""
import bmesh, math, re
from mathutils import Vector
from mathutils.kdtree import KDTree

HAIR_RX = re.compile(r"hair|fur\b|ponytail|pony_?tail|afro|bangs?\b|fringe|braid|\bbun\b|mane|wig", re.I)
NOT_HAIR_RX = re.compile(r"lash|brow(?!n)|eyeline|eye_?liner|iris|eye|beard|mustache|moustache|stubble", re.I)
SKIRT_RX = re.compile(r"skirt|dress|kilt|gown|tutu", re.I)
BOTTOMS_RX = re.compile(r"bottom|lower|pants|trouser|shorts?\b|jean", re.I)
CLOTH_PREFIX = "B4BCLOTH_"


def smooth(e0, e1, x):
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0))) if e1 != e0 else float(x >= e0)
    return t * t * (3 - 2 * t)


def weights_of(m):
    names = {g.index: g.name for g in m.vertex_groups}
    return [{names[g.group]: g.weight for g in v.groups if g.weight > 0} for v in m.data.vertices]


def set_weights(m, W):
    m.vertex_groups.clear()
    groups = {}
    for vi, d in enumerate(W):
        for n, w in d.items():
            if w <= 1e-4: continue
            g = groups.get(n) or groups.setdefault(n, m.vertex_groups.new(name=n))
            g.add([vi], w, "REPLACE")


def vertex_materials(m):
    mats = [re.sub(r"\.\d{3}$", "", x.name) if x else "" for x in m.data.materials]
    out = [""] * len(m.data.vertices)
    for p in m.data.polygons:
        n = mats[p.material_index] if p.material_index < len(mats) else ""
        for v in p.vertices: out[v] = n
    return out


# ---- 1. hair on the survivor's physics bones ------------------------------------------------------------------------

def chain_weights(s, n):
    """Tent weights along a chain of n bones for chain parameter s (0 = root joint, i = joint i, beyond n-1 = past the
    last joint): bone i owns the segment after its joint."""
    s = max(0.5, min(n - 0.5, s))
    w = [max(0.0, 1.0 - abs(s - (i + 0.5))) for i in range(n)]
    t = sum(w) or 1.0
    return [x / t for x in w]


def chain_param(z, zs):
    """Chain parameter of height z along joints at heights zs (hanging down: decreasing)."""
    if z >= zs[0]: return 0.0
    for i in range(len(zs) - 1):
        if zs[i] >= z >= zs[i + 1]:
            return i + (zs[i] - z) / max(1e-6, zs[i] - zs[i + 1])
    last = max(1e-3, zs[-2] - zs[-1]) if len(zs) > 1 else 0.05
    return len(zs) - 1 + (zs[-1] - z) / last


def rig_hair(tpl, meshes, chains, is_hair_mat, log=print, swing=1.0):
    """Skin the back hair of `meshes` (template bind pose, Blender metres, heroes face +X) to the survivor's simulated
    hair chains (lists of bone names, root first). A vertex takes the chain in proportion to how far below the chain's
    root it hangs (full 7 cm below) and how far behind the head centre it is (none 3 cm in front); along the chain by
    height. swing scales that share (0..1). Returns the number of vertices moved onto the chains."""
    chains = [[b for b in c if b in tpl.pos] for c in chains]
    chains = [c for c in chains if c]
    if not chains or "head" not in tpl.pos: return 0
    H = tpl.pos["head"]
    moved, total = 0, 0
    for m in meshes:
        mats = vertex_materials(m)
        W = weights_of(m)
        changed = False
        for v, mat in zip(m.data.vertices, mats):
            if not is_hair_mat(mat): continue
            total += 1
            p = m.matrix_world @ v.co
            best = min(chains, key=lambda c: min((p - tpl.pos[b]).length for b in c))
            C = [tpl.pos[b] for b in best]
            a_back = smooth(0.03, -0.03, p.x - H.x)
            a_low = smooth(C[0].z, C[0].z - 0.07, p.z)
            d = max(0.0, min(1.0, swing)) * a_back * a_low
            if d <= 1e-3: continue
            cw = chain_weights(chain_param(p.z, [c.z for c in C]), len(best))
            w = {k: x * (1 - d) for k, x in W[v.index].items()}
            for b, x in zip(best, cw):
                if x > 0: w[b] = w.get(b, 0.0) + d * x
            W[v.index] = w
            moved += 1
            changed = True
        if changed: set_weights(m, W)
    log(f"hair: {moved} of {total} hair vertices on the survivor's physics hair bones "
        f"({'; '.join(','.join(c) for c in chains)})")
    return moved


# ---- 2. skirts as cloth ------------------------------------------------------------------------------------------

def is_skirt_shaped(pts, tpl):
    """A skirt covers the gap between the legs below the crotch; trousers/shorts leave it open."""
    if not pts or "thigh_l" not in tpl.pos: return False
    crotch = (tpl.pos["thigh_l"].z + tpl.pos["thigh_r"].z) / 2
    cy = (tpl.pos["thigh_l"].y + tpl.pos["thigh_r"].y) / 2
    below = [p for p in pts if p.z < crotch - 0.03]
    if len(below) < 20: return False
    mid = [p for p in below if abs(p.y - cy) < 0.03]
    # trousers put next to nothing on the middle line below the crotch; a skirt has its front and back panels there
    return len(mid) / len(below) > 0.04 and any(p.x > tpl.pos["pelvis"].x for p in mid) \
        and any(p.x < tpl.pos["pelvis"].x for p in mid)


def cloth_materials(meshes, tpl, spec, log=print):
    """Materials to simulate as cloth: spec 'auto' = named like a skirt/dress, or like bottoms and skirt-shaped;
    otherwise a comma list of material names."""
    names = set()
    for m in meshes:
        names.update(x for x in vertex_materials(m) if x)
    if spec and spec not in ("auto", "on"):
        want = {x.strip().lower() for x in spec.split(",") if x.strip()}
        out = {n for n in names if n.lower() in want}
        missing = want - {n.lower() for n in out}
        if missing: log(f"cloth: no material {sorted(missing)} (materials: {sorted(names)})")
        return out
    out = set()
    for n in sorted(names):
        if SKIRT_RX.search(n): out.add(n); continue
        if BOTTOMS_RX.search(n):
            pts = [m.matrix_world @ v.co for m in meshes for v, mat in zip(m.data.vertices, vertex_materials(m))
                   if mat == n]
            if is_skirt_shaped(pts, tpl): out.add(n)
            else: log(f"cloth: {n!r} looks like trousers/shorts (open between the legs): not simulated")
    return out


def cloth_region(tpl, meshes, mats, log=print, rings=7, segments=20):
    """Split the faces of `mats` below the waist off into one new object per mesh (named B4BCLOTH_...), and build the
    simulation mesh around them. Returns (new objects, sim dict) or ([], None). sim (Blender metres): verts, tris
    (rings x segments grid, row-major, ring 0 = waist), depth (0 at the waist ring .. 1 at the hem ring), weights
    ({bone: w} per sim vertex, from the template's surface: pelvis/spine/thighs only)."""
    if not mats: return [], None
    waist_cap = tpl.pos["spine_01"].z + 0.02 if "spine_01" in tpl.pos else 1e9
    pieces, pts = [], []
    for m in list(meshes):
        mi = {i for i, x in enumerate(m.data.materials) if x and re.sub(r"\.\d{3}$", "", x.name) in mats}
        if not mi: continue
        bm = bmesh.new(); bm.from_mesh(m.data)
        mw = m.matrix_world
        sel = [f for f in bm.faces if f.material_index in mi and (mw @ f.calc_center_median()).z < waist_cap]
        if not sel: bm.free(); continue
        for f in sel:
            for v in f.verts: pts.append(mw @ v.co)
        # duplicate: the cloth part becomes its own object, the rest keeps everything else
        c = m.copy(); c.data = m.data.copy(); c.name = CLOTH_PREFIX + m.name
        for col in m.users_collection: col.objects.link(c)
        keep = {f.index for f in sel}
        bm2 = bmesh.new(); bm2.from_mesh(c.data)
        bm2.faces.ensure_lookup_table()
        bmesh.ops.delete(bm2, geom=[f for f in bm2.faces if f.index not in keep], context="FACES")
        bm2.to_mesh(c.data); bm2.free()
        bmesh.ops.delete(bm, geom=sel, context="FACES")
        bm.to_mesh(m.data); bm.free()
        pieces.append(c)
    if not pieces: return [], None
    top = max(p.z for p in pts)
    hem = min(p.z for p in pts) - 0.01
    xs = [p.x for p in pts]; ys = [p.y for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    # radius per (ring, sector): the outermost cloth vertex in that bin
    R = [[None] * segments for _ in range(rings)]
    dz = (top - hem) / (rings - 1)
    for p in pts:
        k = int(round((top - p.z) / dz))
        j = int(round((math.atan2(p.y - cy, p.x - cx) % (2 * math.pi)) / (2 * math.pi) * segments)) % segments
        r = math.hypot(p.x - cx, p.y - cy)
        for kk in (k - 1, k, k + 1):                       # a little vertical smoothing: rings see their neighbours
            val = r if kk == k else r * 0.98
            if 0 <= kk < rings and (R[kk][j] is None or val > R[kk][j]): R[kk][j] = val
    for k in range(rings):                                 # empty sectors: nearest filled one on the ring, else ring above
        filled = [j for j in range(segments) if R[k][j] is not None]
        for j in range(segments):
            if R[k][j] is not None: continue
            if filled:
                jj = min(filled, key=lambda f: min((f - j) % segments, (j - f) % segments)); R[k][j] = R[k][jj]
            else:
                R[k][j] = R[k - 1][j] if k else 0.12
    verts, depth = [], []
    for k in range(rings):
        z = top - k * dz
        for j in range(segments):
            a = 2 * math.pi * j / segments
            r = R[k][j] + 0.004                            # just outside the render surface
            verts.append(Vector((cx + r * math.cos(a), cy + r * math.sin(a), z)))
            depth.append(k / (rings - 1))
    tris = []
    for k in range(rings - 1):
        for j in range(segments):
            a, b = k * segments + j, k * segments + (j + 1) % segments
            c, d = a + segments, b + segments
            tris += [(a, b, d), (a, d, c)]
    # sim vertex skinning: the template body's weights near it (hips and legs only)
    allowed = {"pelvis", "spine_01", "spine_02", "thigh_l", "thigh_r"}
    kd_pts, kd_w = [], []
    for tm in tpl.meshes:
        for v, w in zip(tm.data.vertices, weights_of(tm)):
            ww = {n: x for n, x in w.items() if n in allowed}
            if sum(ww.values()) > 0.5:
                kd_pts.append(tm.matrix_world @ v.co); kd_w.append(ww)
    kd = KDTree(len(kd_pts))
    for i, p in enumerate(kd_pts): kd.insert(p, i)
    kd.balance()
    sw = []
    for k, v in enumerate(verts):
        acc = {}
        for co, i, dist in kd.find_n(v, 6):
            f = 1.0 / max(dist, 1e-3)
            for n, x in kd_w[i].items(): acc[n] = acc.get(n, 0.0) + x * f
        if k < segments:                                   # the waist ring rides the hips, not the legs
            acc = {n: x for n, x in acc.items() if n in ("pelvis", "spine_01", "spine_02")} or {"pelvis": 1.0}
        t = sum(acc.values()) or 1.0
        top4 = sorted(acc.items(), key=lambda x: -x[1])[:4]
        t4 = sum(x for _, x in top4) or 1.0
        sw.append({n: x / t4 for n, x in top4})
    log(f"cloth: {sorted(mats)} -> {sum(len(c.data.polygons) for c in pieces)} cloth faces, simulation mesh "
        f"{rings}x{segments} ({len(verts)} vertices), waist {top * 100:.0f} cm, hem {hem * 100:.0f} cm")
    return pieces, {"verts": [list(v) for v in verts], "tris": tris, "depth": depth, "weights": sw,
                    "rings": rings, "segments": segments, "length_m": top - hem}
