"""Secondary motion for survivor models fitted by b4bfit.py (3P): long hair that swings and skirts that sway.

The game gives heroes two kinds of it, and a custom mesh can use both without new blueprints:
  1. Hair on the survivor's physics bones. Some survivors have a ponytail/braid chain under `head` (Holly, Holly
     Elite 06, Walker Elite 03, Doc Elite 03: hair_00..02; Mom: hair_00_l/r..hair_02_l/r) whose bodies are
     PhysType_Simulated in the survivor's physics asset; the hero anim blueprint's RigidBody node simulates them.
     `rig_hair` skins the model's back hair to that chain (by height below its root, behind the head).
  2. Cloth. Skirts, dresses, coat tails and capes become cloth sections: `cloth_regions` separates their faces into
     objects the importer turns into their own sections, and builds a low-poly simulation mesh for each garment: a
     closed tube for skirts and closed coats, an open panel (around the back, the front left open) for open-front
     coats and capes; its top row is fixed (the waist, or the shoulder blades for a cape). modkit/cloth.py writes it
     into a clothing asset of the outfit (adding one if the outfit has none) and maps the render vertices onto it.
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


# ---- 2. skirts, coats and capes as cloth ---------------------------------------------------------------------------

ARM_RX = re.compile(r"^(upperarm|lowerarm|hand|wrist|elbow|shoulder|clavicle|thumb|index|middle|ring|pinky)_", re.I)
COAT_RX = re.compile(r"coat|jacket|trench|duster|robe|parka|frock|poncho|tunic|apron", re.I)
CAPE_RX = re.compile(r"cape|cloak|mantle|shawl", re.I)


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


def material_labels(meshes):
    """{material name: name + colour image file names} (game rips: 'Material #35 jacket.png')."""
    import os
    out = {}
    for m in meshes:
        for x in m.data.materials:
            if not x: continue
            n = re.sub(r"\.\d{3}$", "", x.name)
            lab = n
            if x.node_tree:
                imgs = {os.path.basename(nd.image.filepath or nd.image.name) for nd in x.node_tree.nodes
                        if nd.type == "TEX_IMAGE" and nd.image is not None
                        and nd.image.colorspace_settings.name != "Non-Color"
                        and not re.search(r"normal|_n\.|_nrm|mask|rough|metal|_ao|occlusion|_orm|spec|gloss|height|bump",
                                          nd.image.filepath or nd.image.name, re.I)}
                if imgs: lab += " " + " ".join(sorted(imgs))
            out[n] = lab
    return out


def material_points(meshes, name):
    return [m.matrix_world @ v.co for m in meshes for v, mat in zip(m.data.vertices, vertex_materials(m)) if mat == name]


def garment_kind(pts, tpl):
    """'cape' when the garment hangs from the shoulders behind the body and leaves the chest bare, else 'lower'
    (skirts, dresses, coats: simulated from the waist down)."""
    if not pts or "spine_03" not in tpl.pos: return "lower"
    pel, sp3 = tpl.pos["pelvis"], tpl.pos["spine_03"]
    chest = [p for p in pts if sp3.z - 0.12 < p.z < sp3.z + 0.05]
    if max(p.z for p in pts) < sp3.z or len(chest) < 10: return "lower"
    front = sum(1 for p in chest if p.x > sp3.x + 0.03)
    return "cape" if front / len(chest) < 0.1 and min(p.z for p in pts) < pel.z else "lower"


def cloth_materials(meshes, tpl, spec, log=print):
    """{material: kind} to simulate. spec 'auto': named (material or colour image) like a skirt/dress/coat/cape, or
    like bottoms and skirt-shaped; else a comma list of material names, each optionally ':cape' / ':lower' to force
    how it hangs. kind 'lower' = simulated below the waist (skirts, dresses, coat tails), 'cape' = below the shoulder
    blades, hanging behind."""
    labels = material_labels(meshes)
    names = set()
    for m in meshes:
        names.update(x for x in vertex_materials(m) if x)
    out = {}
    if spec and spec not in ("auto", "on"):
        want = {}
        for x in spec.split(","):
            x = x.strip()
            if not x: continue
            k = None
            if re.search(r":(cape|lower)$", x, re.I): x, k = x.rsplit(":", 1)
            want[x.lower()] = k.lower() if k else None
        for n in names:
            if n.lower() in want:
                out[n] = want[n.lower()] or garment_kind(material_points(meshes, n), tpl)
        missing = set(want) - {n.lower() for n in out}
        if missing: log(f"cloth: no material {sorted(missing)} (materials: {sorted(names)})")
        return out
    for n in sorted(names):
        lab = labels.get(n, n)
        if CAPE_RX.search(lab): out[n] = "cape"; continue
        if SKIRT_RX.search(lab) or COAT_RX.search(lab):
            out[n] = garment_kind(material_points(meshes, n), tpl); continue
        if BOTTOMS_RX.search(lab):
            pts = material_points(meshes, n)
            if is_skirt_shaped(pts, tpl): out[n] = "lower"
            else: log(f"cloth: {n!r} looks like trousers/shorts (open between the legs): not simulated")
    return out


def cut_region(tpl, meshes, mats, cap, tag, log=print, dry=False, keep=None):
    """Split the faces of `mats` below height `cap` off into new objects named <tag>... Returns (pieces, points,
    inward fraction) where the inward fraction is the share of faces facing the body axis (a lining). dry: only the
    points, nothing split."""
    pieces, pts, inward, nf = [], [], 0, 0
    axis = tpl.pos["pelvis"]
    for m in list(meshes):
        mi = {i for i, x in enumerate(m.data.materials) if x and re.sub(r"\.\d{3}$", "", x.name) in mats}
        if not mi: continue
        bm = bmesh.new(); bm.from_mesh(m.data)
        mw = m.matrix_world
        nm = mw.to_3x3().inverted().transposed()
        # a coat's sleeves hang below the waist too: faces skinned to the arms stay skinned
        W = weights_of(m)
        arm = [sum(x for b, x in w.items() if ARM_RX.match(b)) / (sum(w.values()) or 1.0) for w in W]
        sel = [f for f in bm.faces if f.material_index in mi and (mw @ f.calc_center_median()).z < cap
               and sum(arm[v.index] for v in f.verts) / len(f.verts) < 0.5
               and (keep is None or keep(mw @ f.calc_center_median()))]
        if not sel: bm.free(); continue
        for f in sel:
            for v in f.verts: pts.append(mw @ v.co)
            c = mw @ f.calc_center_median(); n = nm @ f.normal
            nf += 1
            if n.x * (c.x - axis.x) + n.y * (c.y - axis.y) < 0: inward += 1
        if dry: bm.free(); continue
        c = m.copy(); c.data = m.data.copy(); c.name = tag + m.name
        for col in m.users_collection: col.objects.link(c)
        keep = {f.index for f in sel}
        bm2 = bmesh.new(); bm2.from_mesh(c.data)
        bm2.faces.ensure_lookup_table()
        bmesh.ops.delete(bm2, geom=[f for f in bm2.faces if f.index not in keep], context="FACES")
        bm2.to_mesh(c.data); bm2.free()
        bmesh.ops.delete(bm, geom=sel, context="FACES")
        bm.to_mesh(m.data); bm.free()
        pieces.append(c)
    return pieces, pts, inward / max(1, nf)


def add_back_faces(pieces, log=print, inset=0.0015):
    """One-sided garments show nothing from inside once they swing open: give every face a reversed copy 1.5 mm
    inside (normals pointing inward), so the inside is drawn."""
    n = 0
    for c in pieces:
        me = c.data
        corner = [tuple(x.vector) for x in me.corner_normals]           # the originals' normals (custom or not)
        nf, nl = len(me.polygons), len(me.loops)
        bm = bmesh.new(); bm.from_mesh(me)
        orig = list(bm.faces)
        vn = {v: v.normal.copy() for v in bm.verts}
        dup = bmesh.ops.duplicate(bm, geom=orig)
        new_faces = [g for g in dup["geom"] if isinstance(g, bmesh.types.BMFace)]
        for v_old, v_new in dup["vert_map"].items():
            if isinstance(v_old, bmesh.types.BMVert) and v_old in vn:
                v_new.co = v_old.co - vn[v_old] * inset
        bmesh.ops.reverse_faces(bm, faces=new_faces)
        bm.to_mesh(me); bm.free()
        # originals keep their normals (same loop order: bmesh writes the old faces first); copies get their own
        # (inward) vertex normals
        loops = corner[:nl] + [tuple(me.vertices[me.loops[li].vertex_index].normal) for li in range(nl, len(me.loops))]
        me.normals_split_custom_set(loops)
        n += nf
    log(f"cloth: one-sided garment: {n} faces got a reversed copy (the inside shows when it swings)")


def circ_runs(flags):
    """Circular runs of True in a list: [(start, length)]."""
    n = len(flags)
    if all(flags): return [(0, n)]
    s = next(i for i in range(n) if not flags[i])
    runs, i = [], 0
    while i < n:
        j = (s + i) % n
        if flags[j]:
            k = 0
            while k < n and flags[(j + k) % n]: k += 1
            runs.append((j, k)); i += k
        else:
            i += 1
    return runs


def build_sim(tpl, pts, kind, top, log=print, rows=None):
    """Simulation mesh for one garment (Blender metres, heroes face +X). A closed tube when the garment goes all the way
    round, else an open panel over the covered arc (the front of an open coat, a cape's front left out). Rows from
    `top` (fixed) to the hem, which each column takes from the garment (a coat longer at the back)."""
    centre_bone = "spine_03" if kind == "cape" and "spine_03" in tpl.pos else "pelvis"
    cx, cy = tpl.pos[centre_bone].x, tpl.pos[centre_bone].y
    if kind != "cape":
        xs = [p.x for p in pts]; ys = [p.y for p in pts]
        cy = (min(ys) + max(ys)) / 2                      # left/right: the garment's middle; front/back: the body's
        cx = (cx + (min(xs) + max(xs)) / 2) / 2
    hem = min(p.z for p in pts) - 0.01
    L = top - hem
    ang = lambda p: math.atan2(p.y - cy, p.x - cx) % (2 * math.pi)
    # angular coverage over height bands: a front left open shows as sectors empty in most bands
    NB, NH = 72, 10
    seen = [set() for _ in range(NB)]
    for p in pts:
        seen[int(ang(p) / (2 * math.pi) * NB) % NB].add(min(NH - 1, int((top - p.z) / max(L, 1e-6) * NH)))
    cover = [len(s) / NH for s in seen]
    gaps = [r for r in circ_runs([c < 0.34 for c in cover]) if r[1] < NB]
    gap = max(gaps, key=lambda r: r[1]) if gaps else None
    closed = gap is None or gap[1] * 360 / NB < 25
    rows = rows or max(5, min(10, int(round(L / 0.09)) + 1))
    if closed:
        a0, span, cols = 0.0, 2 * math.pi, 20
    else:
        gs, gl = gap
        # the covered arc: from the gap's end round to its start (the few points inside the gap, e.g. where the
        # fronts meet at the waist, map onto the edge columns), half a bin of margin on both sides
        binw = 2 * math.pi / NB
        a0 = (gs + gl) * binw - binw / 2
        span = (NB - gl) * binw + binw
        cols = max(5, int(round(math.degrees(span) / 18)) + 1)
    col_ang = [a0 + span * j / (cols if closed else cols - 1) for j in range(cols)]

    def col_of(p):
        r = (ang(p) - a0) % (2 * math.pi)
        if closed: return int(round(r / span * cols)) % cols
        if r > span + (2 * math.pi - span) / 2: r -= 2 * math.pi       # just before the arc's start
        return max(0, min(cols - 1, int(round(r / span * (cols - 1)))))
    # per-column hem, smoothed with the neighbours
    bot = [None] * cols
    for p in pts:
        j = col_of(p)
        if bot[j] is None or p.z < bot[j]: bot[j] = p.z
    for j in range(cols):
        if bot[j] is None: bot[j] = hem
    sm = []
    for j in range(cols):
        nb = [bot[j]] + [bot[(j + d) % cols] for d in (-1, 1) if closed or 0 <= j + d < cols]
        sm.append(min(min(nb) + 0.02, bot[j]) - 0.01)
    bot = [max(hem, min(b, top - 0.08)) for b in sm]
    zs = lambda k, j: top - (top - bot[j]) * k / (rows - 1)
    R = [[None] * cols for _ in range(rows)]
    for p in pts:
        j = col_of(p)
        k = int(round((top - p.z) / max(1e-6, top - bot[j]) * (rows - 1)))
        r = math.hypot(p.x - cx, p.y - cy)
        for kk in (k - 1, k, k + 1):
            val = r if kk == k else r * 0.98
            if 0 <= kk < rows and (R[kk][j] is None or val > R[kk][j]): R[kk][j] = val
    for k in range(rows):
        filled = [j for j in range(cols) if R[k][j] is not None]
        for j in range(cols):
            if R[k][j] is not None: continue
            if filled:
                jj = min(filled, key=lambda f: min((f - j) % cols, (j - f) % cols) if closed else abs(f - j))
                R[k][j] = R[k][jj]
            else:
                R[k][j] = R[k - 1][j] if k else 0.12
    verts, depth = [], []
    for k in range(rows):
        for j in range(cols):
            a = col_ang[j]; r = R[k][j] + 0.004                   # just outside the render surface
            verts.append(Vector((cx + r * math.cos(a), cy + r * math.sin(a), zs(k, j))))
            depth.append(k / (rows - 1))
    tris = []
    for k in range(rows - 1):
        for j in range(cols if closed else cols - 1):
            a, b = k * cols + j, k * cols + (j + 1) % cols
            c, d = a + cols, b + cols
            tris += [(a, b, d), (a, d, c)]
    shape = "tube" if closed else f"open panel over {math.degrees(span):.0f} deg"
    return {"verts": [list(v) for v in verts], "tris": tris, "depth": depth, "rows": rows, "segments": cols,
            "closed": closed, "length_m": L, "centre": [cx, cy], "shape": shape, "arc": [a0, span]}


SIM_BONES = {"lower": ({"pelvis", "spine_01", "spine_02", "thigh_l", "thigh_r"}, ("pelvis", "spine_01", "spine_02")),
             "cape": ({"spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r", "neck_01"},
                      ("spine_03", "clavicle_l", "clavicle_r", "neck_01"))}


def sim_weights(tpl, verts, kind, cols):
    """Skinning of the simulation vertices: the template body's weights near each (hips/legs for skirts and coats,
    the upper back for capes); the fixed top row only on the waist / shoulder-blade bones."""
    allowed, top_bones = SIM_BONES[kind]
    kd_pts, kd_w = [], []
    for tm in tpl.meshes:
        for v, w in zip(tm.data.vertices, weights_of(tm)):
            ww = {n: x for n, x in w.items() if n in allowed}
            if sum(ww.values()) > 0.5:
                kd_pts.append(tm.matrix_world @ v.co); kd_w.append(ww)
    kd = KDTree(len(kd_pts))
    for i, p in enumerate(kd_pts): kd.insert(p, i)
    kd.balance()
    out = []
    for k, v in enumerate(verts):
        acc = {}
        for co, i, dist in kd.find_n(Vector(v), 6):
            f = 1.0 / max(dist, 1e-3)
            for n, x in kd_w[i].items(): acc[n] = acc.get(n, 0.0) + x * f
        if k < cols:
            acc = {n: x for n, x in acc.items() if n in top_bones} or {top_bones[0]: 1.0}
        top4 = sorted(acc.items(), key=lambda x: -x[1])[:4]
        t4 = sum(x for _, x in top4) or 1.0
        out.append({n: x / t4 for n, x in top4})
    return out


def limb_family(b):
    """thigh_twist_01_l / hip_twist_01_l -> thigh_l, calf_twist_01_r / knee_twist_01_r -> calf_r, else b."""
    m = re.match(r"^(thigh|hip|calf|knee)_twist_\d+_([lr])$", b)
    if not m: return b
    return ("thigh_" if m.group(1) in ("thigh", "hip") else "calf_") + m.group(2)


def body_capsules(tpl, meshes, log=print, skip_mats=()):
    """Collision capsules fitted to the model's own legs and body (Blender metres): per bone the segment to its child
    joint and the radius at which the bone's skin sits (vertices mostly weighted to it or its twist bones, median
    distance, a little inside). For the clothing asset's collision (cloth.py)."""
    segs = {"pelvis": ("pelvis", "spine_01"), "spine_01": ("spine_01", "spine_02"), "spine_02": ("spine_02", "spine_03"),
            "spine_03": ("spine_03", "neck_01"), "thigh_l": ("thigh_l", "calf_l"), "thigh_r": ("thigh_r", "calf_r"),
            "calf_l": ("calf_l", "foot_l"), "calf_r": ("calf_r", "foot_r")}
    dist = {b: [] for b in segs}
    for m in meshes:
        if m.name.startswith(CLOTH_PREFIX): continue
        mats = vertex_materials(m)
        for v, w, mat in zip(m.data.vertices, weights_of(m), mats):
            if not w or mat in skip_mats: continue          # garments hanging off the body don't count
            acc = {}
            for n, x in w.items(): acc[limb_family(n)] = acc.get(limb_family(n), 0.0) + x
            b, x = max(acc.items(), key=lambda kv: kv[1])
            if b not in segs or x < 0.6 * sum(acc.values()): continue
            a, c = segs[b]
            if a not in tpl.pos or c not in tpl.pos: continue
            A, C = tpl.pos[a], tpl.pos[c]
            p = m.matrix_world @ v.co
            e = C - A; t = max(0.0, min(1.0, (p - A).dot(e) / max(e.length_squared, 1e-9)))
            if 0.1 < t < 0.9: dist[b].append((p - (A + e * t)).length)
    caps = []
    for b, d in dist.items():
        if len(d) < 30: continue
        d.sort()
        r = d[len(d) // 2] * 0.95                       # the skin's median distance, a little inside
        a, c = segs[b]
        caps.append({"bone": b, "a": list(tpl.pos[a]), "b": list(tpl.pos[c]), "r": r})
    if caps:
        log("cloth: collision capsules " + ", ".join(f"{c['bone']} r{c['r'] * 100:.0f}" for c in caps) + " cm")
    return caps


MAXD = {"tube": 0.45, "open": 0.8, "cape": 0.8}     # max distance from the skinned pose at the hem: share of length


def cloth_regions(tpl, meshes, mats, log=print):
    """mats: {material: kind} from cloth_materials. Splits each garment kind's faces off into objects named
    B4BCLOTH_... (the first garment) / B4BCLOTH_R<k>_... and builds its simulation mesh. Returns (new objects, [sim dict]). sim (Blender
    metres): verts, tris, depth (0 at the fixed top row .. 1 at the hem), weights ({bone: w} per sim vertex), kind,
    closed, maxd (max distance share), collision capsules."""
    if not mats: return [], []
    kinds = {}
    for n, k in mats.items(): kinds.setdefault(k, set()).add(n)
    all_pieces, sims = [], []
    caps = None
    for kind in ("lower", "cape"):
        ms = kinds.get(kind)
        if not ms: continue
        if kind == "lower":
            cap = tpl.pos["spine_01"].z + 0.02 if "spine_01" in tpl.pos else 1e9
        else:
            cap = tpl.pos["spine_03"].z if "spine_03" in tpl.pos else 1e9
        tag = f"{CLOTH_PREFIX}R{len(sims)}_" if sims else CLOTH_PREFIX
        _, pts, _ = cut_region(tpl, meshes, ms, cap, tag, log, dry=True)
        if not pts: continue
        top = max(p.z for p in pts)
        if top - min(p.z for p in pts) < 0.12:
            log(f"cloth: {sorted(ms)} hangs only {100 * (top - min(p.z for p in pts)):.0f} cm below the "
                f"{'waist' if kind == 'lower' else 'shoulder blades'}: skinned, not simulated")
            continue
        sim = build_sim(tpl, pts, kind, top, log)
        keep = None
        if not sim["closed"]:
            # faces in the gap (e.g. where a coat's fronts still meet just below the waist) stay skinned: the open
            # panel doesn't reach them
            cx, cy = sim["centre"]; a0, span = sim["arc"]
            keep = lambda c: (math.atan2(c.y - cy, c.x - cx) - a0) % (2 * math.pi) <= span
        pieces, _, inward = cut_region(tpl, meshes, ms, cap, tag, log, keep=keep)
        if not sim["closed"] and inward < 0.2:
            add_back_faces(pieces, log)
        sim["weights"] = sim_weights(tpl, sim["verts"], kind, sim["segments"])
        sim["kind"] = kind
        sim["maxd"] = MAXD["cape" if kind == "cape" else ("tube" if sim["closed"] else "open")]
        if caps is None: caps = body_capsules(tpl, meshes, log, set(mats))
        sim["collision"] = caps
        log(f"cloth: {sorted(ms)} -> {sum(len(c.data.polygons) for c in pieces)} cloth faces ({kind}), simulation mesh "
            f"{sim['shape']}, {sim['rows']}x{sim['segments']} ({len(sim['verts'])} vertices), top {top * 100:.0f} cm, "
            f"hem {(top - sim['length_m']) * 100:.0f} cm")
        all_pieces += pieces
        sims.append(sim)
    return all_pieces, sims
