"""Secondary motion for imported survivor meshes: the template's physics hair chains, and skirts as cloth.

  hair_chains(skm_file, src)        simulated hair bone chains of the template (its physics asset), for b4bfit
  cloth_assets(skm)                 the template's clothing assets (MeshClothingAssets)
  apply(skm, sims)                  write a simulation mesh (from blender/b4bdangle.py) into the template's clothing
                                    asset and bind the cloth sections of the first LODs to it

Cloth data (UE 4.25 ClothingSystemRuntimeCommon, NvCloth; all tagged properties, uprops.py): per clothing LOD a
ClothPhysicalMeshData (Vertices, Normals, Indices, WeightMaps {1: MaxDistance, 2/3: backstop, 4: anim drive},
InverseMasses, BoneData (skinning to UsedBoneNames, 12 slots), NumFixedVerts, MaxBoneWeights) + CollisionData, then
native TransitionUp/DownSkinData. The fabric is cooked by the engine at load. Render side (skm.py): a cloth section
has one FMeshToMeshVertData per vertex: position/normal/tangent = sum_i bary_i * (V_i - N_i * dist) over a simulation
triangle (V, N = stored vertices/normals: stored normals point along -cross(B-A, C-A)), plus the LOD's cloth vertex
buffer (all cloth sections' records in section order, per section (offset, base vertex)). Checked on retail Holly
Elite 00 (flannel + sleeves). docs/investigations/mesh-mods.md §13.
"""
import copy, math, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skm, upkg, uprops

CLOTH_LODS = 3            # mesh LODs 0..2 simulate (retail: 3 of 5); later LODs skin the skirt to the hips/legs


# ---- template inspection -------------------------------------------------------------------------------------------

def _obj_prop(s, name):
    p = s.props.get(name)
    if not p: return None
    return s.pkg.obj_path(upkg.R(s.data, p["off"]).i32())


def hair_chains(skm_file, src):
    """Chains (root first) of the template's hair bones that its physics asset simulates (PhysType_Simulated bodies
    under `head`; bones in between without a body swing along)."""
    s = skm.SkeletalMesh(skm_file)
    pa = _obj_prop(s, "PhysicsAsset")
    if not pa: return [], None
    f = upkg.game_path_to_file(pa.split(".")[0], src)
    if not f or not os.path.exists(f): return [], pa
    p = upkg.Package(f)
    sim = set()
    for e in p.exports:
        if p.class_name(e) != "SkeletalBodySetup": continue
        t, _ = uprops.parse(p, bytes(p.export_data(e)))
        bn, pt = uprops.find(t, "BoneName"), uprops.find(t, "PhysicsType")
        if bn and pt and p.names[pt.value[0]] == "PhysType_Simulated":
            sim.add(p.names[bn.value[0]].lower())
    bones = [s.name(b[:2]).lower() for b in s.m["refskel"]["bones"]]
    parent = [b[2] for b in s.m["refskel"]["bones"]]
    kids = {}
    for i, par in enumerate(parent): kids.setdefault(par, []).append(i)

    def under_head(i):
        while i >= 0:
            if bones[i] == "head": return True
            i = parent[i]
        return False
    chains = []
    for i, b in enumerate(bones):
        if b in sim and "hair" in b and under_head(i) and bones[parent[i]] not in sim:
            c, j = [b], i
            while len(kids.get(j, [])) == 1:
                j = kids[j][0]; c.append(bones[j])
            chains.append(c)
    return chains, pa


def cloth_assets(s):
    """[(asset name, export)] of MeshClothingAssets, in order (a section's cloth_asset_index indexes this)."""
    p = s.props.get("MeshClothingAssets")
    if not p: return []
    r = upkg.R(s.data, p["off"])
    out = []
    for _ in range(r.i32()):
        i = r.i32()
        out.append((s.pkg.obj(i), s.pkg.exports[i - 1] if i > 0 else None))
    return out


# ---- geometry ------------------------------------------------------------------------------------------------------

def sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def mul(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a):
    l = math.sqrt(dot(a, a))
    return mul(a, 1.0 / l) if l > 1e-12 else (0.0, 0.0, 1.0)


def bary(p, a, b, c):
    v0, v1, v2 = sub(b, a), sub(c, a), sub(p, a)
    d00, d01, d11, d20, d21 = dot(v0, v0), dot(v0, v1), dot(v1, v1), dot(v2, v0), dot(v2, v1)
    den = d00 * d11 - d01 * d01
    if abs(den) < 1e-12: return (1.0, 0.0, 0.0)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    return (1.0 - v - w, v, w)


def point_tri_dist2(p, a, b, c):
    """Squared distance from p to triangle abc (clamped barycentric)."""
    u, v, w = bary(p, a, b, c)
    if u >= 0 and v >= 0 and w >= 0:
        q = add(add(mul(a, u), mul(b, v)), mul(c, w))
        d = sub(p, q); return dot(d, d)
    best = 1e30
    for x, y in ((a, b), (b, c), (c, a)):
        e = sub(y, x); t = max(0.0, min(1.0, dot(sub(p, x), e) / max(dot(e, e), 1e-12)))
        d = sub(p, add(x, mul(e, t))); best = min(best, dot(d, d))
    return best


def solve(q, A, B, C, NA, NB, NC):
    """(u, v, w, d) with u*(A+NA*d) + v*(B+NB*d) + w*(C+NC*d) = q (the GPU skin cache's cloth formula, normals given
    as they enter it: the negated stored normals)."""
    d = 0.0
    nsum = add(add(NA, NB), NC)
    for _ in range(12):
        a, b, c = add(A, mul(NA, d)), add(B, mul(NB, d)), add(C, mul(NC, d))
        n = norm(cross(sub(b, a), sub(c, a)))
        if dot(n, nsum) < 0: n = mul(n, -1)
        e = dot(sub(q, a), n)
        qp = sub(q, mul(n, e))
        u, v, w = bary(qp, a, b, c)
        ni = add(add(mul(NA, u), mul(NB, v)), mul(NC, w))
        k = dot(ni, n)
        if abs(k) < 1e-4: break
        d += e / k
        if abs(e) < 1e-5: break
    return u, v, w, d


class Sim:
    """A simulation mesh in UE space (cm) with the normals the mapping uses."""
    def __init__(self, verts, tris):
        self.V, self.T = verts, [tuple(t) for t in tris]
        acc = [(0.0, 0.0, 0.0)] * len(verts)
        for a, b, c in self.T:
            n = cross(sub(verts[b], verts[a]), sub(verts[c], verts[a]))
            for x in (a, b, c): acc[x] = add(acc[x], n)
        self.Nm = [norm(n) for n in acc]                    # runtime normals (the mapping's)
        self.N = [mul(n, -1) for n in self.Nm]              # stored normals (retail convention)
        self.adj = [[] for _ in verts]
        for ti, t in enumerate(self.T):
            for x in t: self.adj[x].append(ti)

    def candidates(self, p, k=6):
        near = sorted(range(len(self.V)), key=lambda i: dot(sub(self.V[i], p), sub(self.V[i], p)))[:k]
        return sorted({t for i in near for t in self.adj[i]})

    def record(self, p, n, t, tris=None):
        """FMeshToMeshVertData tuple for a point p with unit normal n and tangent t (cm)."""
        tris = tris or self.candidates(p)
        ti = min(tris, key=lambda i: point_tri_dist2(p, *(self.V[x] for x in self.T[i])))
        a, b, c = self.T[ti]
        args = (self.V[a], self.V[b], self.V[c], self.Nm[a], self.Nm[b], self.Nm[c])
        pos = solve(p, *args)
        nrm = solve(add(p, n), *args)
        tan = solve(add(p, t), *args)
        return tuple(pos) + tuple(nrm) + tuple(tan) + (a, b, c, 0, 0.0, 0)


# ---- writing -------------------------------------------------------------------------------------------------------

def blender_to_ue(p):
    return (p[0] * 100.0, -p[1] * 100.0, p[2] * 100.0)


def _pm_set(pm, name, value):
    p = uprops.find(pm, name)
    if p is None: raise SystemExit(f"cloth: the template's clothing asset has no {name}")
    if isinstance(p.value, dict): p.value["items"] = value
    else: p.value = value


def _bone_data(template_el, infl):
    """One ClothVertBoneData element (tagged: NumInfluences, BoneIndices[12], BoneWeights[12])."""
    el = copy.deepcopy(template_el)
    for p in el:
        if p.name == "NumInfluences": p.value = len(infl)
        elif p.name == "BoneIndices": p.value = infl[p.index][0] if p.index < len(infl) else 0
        elif p.name == "BoneWeights": p.value = float(infl[p.index][1]) if p.index < len(infl) else 0.0
    return uprops.Tagged(el)


def inverse_masses(V, T, fixed):
    m = [0.0] * len(V)
    for a, b, c in T:
        cr = cross(sub(V[b], V[a]), sub(V[c], V[a]))
        ar = math.sqrt(dot(cr, cr)) / 2.0
        for x in (a, b, c): m[x] += ar / 3.0
    mean = sum(m) / max(1, len(m)) or 1.0
    return [0.0 if fixed[i] or m[i] <= 0 else mean / m[i] for i in range(len(V))]


def _transitions(sim, n):
    """Identity mapping of a simulation mesh onto itself (clothing LODs share one mesh)."""
    recs = []
    for i, v in enumerate(sim.V):
        tris = sim.adj[i]
        a, b, c = sim.T[tris[0]]
        other = b if a == i else a
        t = sub(sim.V[other], v); t = norm(sub(t, mul(sim.N[i], dot(t, sim.N[i]))))
        recs.append(sim.record(v, sim.N[i], t, tris))
    blob = b"".join(struct.pack("<" + skm.MESH_TO_MESH, *r) for r in recs)
    return blob, len(recs)


def apply(s, sims, log=print, cloth_lods=CLOTH_LODS):
    """s: skm.SkeletalMesh with LODs built by skmgltf (cloth sections flagged `cloth_tag`). Writes the first simulation
    mesh into the template's biggest clothing asset (its config and collision physics asset kept) and binds the cloth
    sections of LODs < cloth_lods to it. Returns the clothing asset's name, or None."""
    if not sims: return None
    assets = cloth_assets(s)
    if not assets:
        log("cloth: the template has no clothing asset: the skirt is skinned (pick an outfit with cloth)"); return None
    if len(sims) > 1: log(f"cloth: {len(sims)} cloth regions; only the first is simulated")
    sd = sims[0]
    pkg = s.pkg
    trees = []
    for ai, (name, e) in enumerate(assets):
        t, end = uprops.parse(pkg, bytes(pkg.export_data(e)))
        lods = uprops.find(t, "LodData").value["items"]
        nv = len(uprops.find(uprops.find(lods[0], "PhysicalMeshData").value, "Vertices").value["items"])
        trees.append((nv, ai, name, e, t, end))
    nv0, ai, aname, ae, tree, tend = max(trees, key=lambda x: x[0])
    V = [blender_to_ue(p) for p in sd["verts"]]
    # the Y flip (Blender -> UE) mirrors the winding: orient so stored normals point away from the skirt's axis
    cx = sum(p[0] for p in V) / len(V); cy = sum(p[1] for p in V) / len(V)
    T = [tuple(t) for t in sd["tris"]]
    sim = Sim(V, T)
    out = sum(dot(sim.N[i], (V[i][0] - cx, V[i][1] - cy, 0.0)) for i in range(len(V)))
    if out < 0:
        T = [(a, c, b) for a, b, c in T]; sim = Sim(V, T)
    L = sd["length_m"] * 100.0
    maxd = [0.0 if d <= 1e-6 else max(2.0, d * L * 0.45) for d in sd["depth"]]
    fixed = [x == 0.0 for x in maxd]
    # bones: the mesh's bone indices by name
    bone_names = [s.name(b[:2]).lower() for b in s.m["refskel"]["bones"]]
    used = []
    for w in sd["weights"]:
        for b in w:
            if b.lower() not in used: used.append(b.lower())
    missing = [b for b in used if b not in bone_names]
    if missing: raise SystemExit(f"cloth: bones {missing} are not in the mesh")
    lod_items = uprops.find(tree, "LodData").value["items"]
    tmpl = lod_items[0]
    tpm = uprops.find(tmpl, "PhysicalMeshData").value
    bd_el = uprops.find(tpm, "BoneData").value["items"][0]
    bone_data = [_bone_data(bd_el, sorted(((used.index(b.lower()), w) for b, w in wd.items()), key=lambda x: -x[1]))
                 for wd in sd["weights"]]
    trans, ntr = _transitions(sim, len(V))
    new_lods = []
    for li in range(cloth_lods):
        el = copy.deepcopy(tmpl)
        pm = uprops.find(el, "PhysicalMeshData").value
        _pm_set(pm, "Vertices", [struct.pack("<3f", *v) for v in V])
        _pm_set(pm, "Normals", [struct.pack("<3f", *n) for n in sim.N])
        _pm_set(pm, "Indices", [x for t in T for x in t])
        wm = uprops.find(pm, "WeightMaps").value["items"]
        for k, v in wm:
            uprops.find(v, "Values").value = list(maxd) if k == 1 else []
        _pm_set(pm, "InverseMasses", inverse_masses(V, T, fixed))
        _pm_set(pm, "BoneData", bone_data)
        _pm_set(pm, "MaxBoneWeights", max(len(w) for w in sd["weights"]))
        _pm_set(pm, "NumFixedVerts", sum(fixed))
        _pm_set(pm, "SelfCollisionIndices", [])
        up = (struct.pack("<i", ntr) + trans) if li > 0 else struct.pack("<i", 0)
        down = (struct.pack("<i", ntr) + trans) if li < cloth_lods - 1 else struct.pack("<i", 0)
        el.tail = up + down
        new_lods.append(el)
    uprops.find(tree, "LodData").value["items"] = new_lods
    uprops.find(tree, "LodMap").value = list(range(cloth_lods))
    uprops.find(tree, "UsedBoneNames").value = [uprops.name_ref(pkg, b) for b in used]
    uprops.find(tree, "UsedBoneIndices").value = [bone_names.index(b) for b in used]
    guid = uprops.find(tree, "AssetGuid").value
    old = bytes(pkg.export_data(ae))
    pkg.set_export_data(ae, uprops.write(pkg, tree) + old[tend:])
    # render side
    bound = 0
    for li, lod in enumerate(s.m["lods"]):
        if li >= cloth_lods: break
        P = lod["positions"]["positions"]
        TG = lod["static_vb"]["tangents"]
        blob, mapping, off = b"", [], 0
        for sec in lod["sections"]:
            if not sec.get("cloth_tag"):
                mapping.append((0, 0)); continue
            recs = []
            for k in range(sec["num_vertices"]):
                vi = sec["base_vertex_index"] + k
                tx = skm.unpack_normal(TG[vi][0:4])[:3]; tz = skm.unpack_normal(TG[vi][4:8])[:3]
                recs.append(sim.record(P[vi], norm(tz), norm(tx)))
            sec["cloth_mapping"] = recs
            sec["cloth_asset_index"] = ai
            ts = two_sided_slot(s, sec["material_index"])
            if ts is not None:
                if li == 0: log(f"cloth: section on slot {s.name(s.m['materials'][ts]['slot_name'])} (two-sided "
                                f"variant of {s.name(s.m['materials'][sec['material_index']]['slot_name'])})")
                sec["material_index"] = ts
            sec["clothing_data"] = (guid, li)
            mapping.append((off, sec["base_vertex_index"]))
            blob += b"".join(struct.pack("<" + skm.MESH_TO_MESH, *r) for r in recs)
            off += len(recs)
            bound += 1
        if off:
            lod["cloth_vb"] = skm.Obj(strip=(1, 0), data=(64, off, blob), index_mapping=mapping)
            fix_buffers_size(s, lod)
    err = map_error(sim, s, cloth_lods)
    log(f"cloth: simulation mesh {len(V)} vertices / {len(T)} triangles written into {aname} "
        f"({cloth_lods} LODs, max distance up to {max(maxd):.0f} cm, {sum(fixed)} fixed, bones {used}); "
        f"{bound} cloth section(s) bound, mapping error max {err:.3f} cm")
    return aname


def two_sided_slot(s, slot):
    """A slot whose material instance is the two-sided variant of `slot`'s (retail: <x>_2sided_MI, a child of <x>_MI
    with the TwoSided override, same textures), else None. Cloth folds show the inside of a one-sided skirt."""
    import re
    def mi(i):
        o = s.pkg.obj(s.m["materials"][i]["material"]) or ""
        return o.lower()
    want = mi(slot)
    for i in range(len(s.m["materials"])):
        n = mi(i)
        if i != slot and n != want and re.sub(r"_?(2|two)_?sided", "", n) == want: return i
    return None


def fix_buffers_size(s, lod):
    """BuffersSize = byte size of the streamed block (skmgltf.build_lod computes it the same way)."""
    ar = skm.Ar(s.pkg); skm.ser_lod(ar, lod, s.m); full = len(ar.b)
    ar2 = skm.Ar(s.pkg)
    skm.ser_strip(ar2, lod["strip"]); ar2.bool32(False); ar2.bool32(True); ar2.packed_array("h", lod["required_bones"])
    ar2.array(lambda x: skm.ser_section(ar2, x), lod["sections"]); ar2.packed_array("h", lod["active_bones"]); ar2.u32(0)
    lod["buffers_size"] = full - len(ar2.b)


def map_error(sim, s, n):
    """Largest distance between a cloth vertex and its reconstruction from the simulation mesh in the bind pose."""
    worst = 0.0
    for lod in s.m["lods"][:n]:
        P = lod["positions"]["positions"]
        for sec in lod["sections"]:
            for k, r in enumerate(sec.get("cloth_mapping") or []):
                i = r[12:15]; b = r[0:4]
                q = [sum(b[j] * (sim.V[i[j]][c] - sim.N[i[j]][c] * b[3]) for j in range(3)) for c in range(3)]
                worst = max(worst, math.dist(q, P[sec["base_vertex_index"] + k]))
    return worst
