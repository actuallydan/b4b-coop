"""glTF 2.0 <-> B4B skeletal mesh (no UE editor). docs/investigations/mesh-mods.md.

  skmgltf.py export <SKM.uasset> <out.glb> [--lod N]
      the mesh with its skeleton (bind pose), weights, UVs, normals/tangents, vertex colours, one glTF material per
      UE material slot (named after the slot). Open in Blender (File > Import > glTF), edit or replace the mesh while
      keeping the armature, export glTF again.
  skmgltf.py import <template SKM.uasset> <in.glb|.gltf|.fbx|.obj|.dae|.blend> <out.uasset> [--lod <LOD1 model>]...
        [--lods N] [--material NAME=SLOT]... [--socket NAME=x,y,z]... [--bone NAME=x,y,z]...
      build new render data from the glTF onto the template's skeleton and write <out>.uasset/.uexp. Everything the
      template's package references stays (skeleton, physics asset, material slots, LOD settings, clothing assets);
      the geometry is replaced. Primitives pick their material slot by glTF material name == slot name (or
      --material), joints map to bones by name.

Coordinates: glTF = (x, z, y) * 0.01 of UE (swapping Y/Z is a reflection, so UE's clockwise triangles become glTF's
counter-clockwise ones without reordering); quaternions (-x, -z, -y, w); tangent sign flips.
"""
import json, math, os, re, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skm

CLOTH_PREFIX = "B4BCLOTH_"          # blender/b4bdangle.py: objects whose faces form a cloth section
CLOTH_RX = re.compile(r"^B4BCLOTH_(?:R(\d+)_)?")

SCALE = 0.01


# ---- small math -------------------------------------------------------------------------------------------------

def q_to_m(q, t=(0, 0, 0), s=(1, 1, 1)):
    x, y, z, w = q
    m = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[m[0][0] * s[0], m[0][1] * s[1], m[0][2] * s[2], t[0]],
            [m[1][0] * s[0], m[1][1] * s[1], m[1][2] * s[2], t[1]],
            [m[2][0] * s[0], m[2][1] * s[1], m[2][2] * s[2], t[2]],
            [0, 0, 0, 1]]


def mm(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def minv(m):
    """Inverse of an affine 4x4."""
    a = [r[:3] for r in m[:3]]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    inv = [[(a[1][1] * a[2][2] - a[1][2] * a[2][1]) / det, (a[0][2] * a[2][1] - a[0][1] * a[2][2]) / det,
            (a[0][1] * a[1][2] - a[0][2] * a[1][1]) / det],
           [(a[1][2] * a[2][0] - a[1][0] * a[2][2]) / det, (a[0][0] * a[2][2] - a[0][2] * a[2][0]) / det,
            (a[0][2] * a[1][0] - a[0][0] * a[1][2]) / det],
           [(a[1][0] * a[2][1] - a[1][1] * a[2][0]) / det, (a[0][1] * a[2][0] - a[0][0] * a[2][1]) / det,
            (a[0][0] * a[1][1] - a[0][1] * a[1][0]) / det]]
    t = [m[0][3], m[1][3], m[2][3]]
    it = [-sum(inv[i][k] * t[k] for k in range(3)) for i in range(3)]
    return [inv[0] + [it[0]], inv[1] + [it[1]], inv[2] + [it[2]], [0, 0, 0, 1]]


def mp(m, p):
    return tuple(m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3))


def mv(m, v):
    return tuple(m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2] for i in range(3))


def norm(v):
    l = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / l for x in v)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


# UE <-> glTF
def p_ue2gl(p): return (p[0] * SCALE, p[2] * SCALE, p[1] * SCALE)
def p_gl2ue(p): return (p[0] / SCALE, p[2] / SCALE, p[1] / SCALE)
def v_ue2gl(v): return (v[0], v[2], v[1])
v_gl2ue = v_ue2gl
def q_ue2gl(q): return (-q[0], -q[2], -q[1], q[3])
q_gl2ue = q_ue2gl


def m_gl2ue(m):
    """Change of basis for a 4x4 in glTF space (metres) to UE space (cm): P M P with P = swap y/z, scale t."""
    perm = [0, 2, 1]
    out = [[m[perm[i]][perm[j]] for j in range(3)] + [m[perm[i]][3] / SCALE] for i in range(3)]
    return out + [[0, 0, 0, 1]]


def m_ue2gl(m):
    perm = [0, 2, 1]
    out = [[m[perm[i]][perm[j]] for j in range(3)] + [m[perm[i]][3] * SCALE] for i in range(3)]
    return out + [[0, 0, 0, 1]]


def bone_globals(refskel):
    """UE model-space bind matrices of the reference skeleton."""
    G = []
    for (ni, nn, parent), pose in zip(refskel["bones"], refskel["pose"]):
        local = q_to_m(pose[0:4], pose[4:7], pose[7:10])
        G.append(mm(G[parent], local) if parent >= 0 else local)
    return G


# ---- glTF writer --------------------------------------------------------------------------------------------------

class GltfBuilder:
    def __init__(self):
        self.j = {"asset": {"version": "2.0", "generator": "b4b-coop skmgltf"}, "buffers": [], "bufferViews": [],
                  "accessors": [], "nodes": [], "meshes": [], "materials": [], "skins": [], "scenes": [], "scene": 0}
        self.bin = bytearray()

    def accessor(self, fmt, rows, typ, ctype, normalized=False, target=None, minmax=False):
        while len(self.bin) % 4: self.bin += b"\0"
        off = len(self.bin)
        st = struct.Struct("<" + fmt)
        for r in rows: self.bin += st.pack(*r)
        bv = {"buffer": 0, "byteOffset": off, "byteLength": len(self.bin) - off}
        if target: bv["target"] = target
        self.j["bufferViews"].append(bv)
        a = {"bufferView": len(self.j["bufferViews"]) - 1, "componentType": ctype, "count": len(rows), "type": typ}
        if normalized: a["normalized"] = True
        if minmax and rows:
            n = len(rows[0])
            a["min"] = [min(r[k] for r in rows) for k in range(n)]
            a["max"] = [max(r[k] for r in rows) for k in range(n)]
        self.j["accessors"].append(a)
        return len(self.j["accessors"]) - 1

    def save_glb(self, path):
        while len(self.bin) % 4: self.bin += b"\0"
        self.j["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(self.j, separators=(",", ":")).encode()
        while len(js) % 4: js += b" "
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(self.bin)))
            f.write(struct.pack("<II", len(js), 0x4E4F534A)); f.write(js)
            f.write(struct.pack("<II", len(self.bin), 0x004E4942)); f.write(self.bin)


def export(src, out, lod_i=0):
    s = skm.SkeletalMesh(src)
    m = s.m
    rs = m["refskel"]
    lod = m["lods"][lod_i]
    g = GltfBuilder()
    # skeleton nodes
    nb = len(rs["bones"])
    children = [[] for _ in range(nb)]
    for i, (ni, nn, parent) in enumerate(rs["bones"]):
        if parent >= 0: children[parent].append(i)
    for i, ((ni, nn, parent), pose) in enumerate(zip(rs["bones"], rs["pose"])):
        node = {"name": s.name((ni, nn)), "rotation": list(q_ue2gl(pose[0:4])),
                "translation": list(p_ue2gl(pose[4:7])), "scale": [pose[7], pose[9], pose[8]]}
        if children[i]: node["children"] = children[i]
        g.j["nodes"].append(node)
    G = bone_globals(rs)
    ibm = []
    for Gi in G:
        inv = m_ue2gl(minv(Gi))
        ibm.append(tuple(inv[r][c] for c in range(4) for r in range(4)))   # column-major
    ibm_acc = g.accessor("16f", ibm, "MAT4", 5126)
    g.j["skins"].append({"joints": list(range(nb)), "inverseBindMatrices": ibm_acc,
                         "skeleton": next(i for i, b in enumerate(rs["bones"]) if b[2] < 0)})
    # materials
    for mat in m["materials"]:
        g.j["materials"].append({"name": s.name(mat["slot_name"]),
                                 "extras": {"ue_material": s.pkg.obj(mat["material"])}})
    P = lod["positions"]["positions"]
    T = lod["static_vb"]["tangents"]
    ntc = lod["static_vb"]["num_texcoords"]
    UV = lod["static_vb"]["uvs"]
    W = skm.decode_weights(lod["skin_weights"])
    C = lod.get("colors", {}).get("colors") if "colors" in lod else None
    I = [x[0] for x in lod["indices"]["indices"]]
    prims = []
    for sec in lod["sections"]:
        if sec["disabled"]: continue
        b, n = sec["base_vertex_index"], sec["num_vertices"]
        bm = [x[0] for x in sec["bone_map"]]
        pos = [p_ue2gl(P[v]) for v in range(b, b + n)]
        nrm, tan = [], []
        for v in range(b, b + n):
            z = skm.unpack_normal(T[v][4:8]); x = skm.unpack_normal(T[v][0:4])
            nrm.append(norm(v_ue2gl(z[:3])))
            tx = norm(v_ue2gl(x[:3]))
            tan.append(tx + (-1.0 if z[3] >= 0 else 1.0,))
        attrs = {"POSITION": g.accessor("3f", pos, "VEC3", 5126, target=34962, minmax=True),
                 "NORMAL": g.accessor("3f", nrm, "VEC3", 5126, target=34962),
                 "TANGENT": g.accessor("4f", tan, "VEC4", 5126, target=34962)}
        for c in range(ntc):
            attrs[f"TEXCOORD_{c}"] = g.accessor("2f", [tuple(float(x) for x in UV[v * ntc + c]) for v in range(b, b + n)],
                                               "VEC2", 5126, target=34962)
        k = len(W[0]) if W else 4
        for set_i in range((k + 3) // 4):
            js, ws = [], []
            for v in range(b, b + n):
                infl = W[v][set_i * 4:set_i * 4 + 4]
                js.append(tuple(bm[i] if w else 0 for i, w in infl))
                ws.append(tuple(w / 255.0 for i, w in infl))
            attrs[f"JOINTS_{set_i}"] = g.accessor("4H", js, "VEC4", 5123, target=34962)
            attrs[f"WEIGHTS_{set_i}"] = g.accessor("4f", ws, "VEC4", 5126, target=34962)
        if C:
            attrs["COLOR_0"] = g.accessor("4B", [(c[2], c[1], c[0], c[3]) for c in C[b:b + n]], "VEC4", 5121,
                                          normalized=True, target=34962)          # FColor is BGRA
        tri = I[sec["base_index"]:sec["base_index"] + 3 * sec["num_triangles"]]
        idx = g.accessor("I", [(i - b,) for i in tri], "SCALAR", 5125, target=34963)
        prims.append({"attributes": attrs, "indices": idx, "material": sec["material_index"]})
    g.j["meshes"].append({"name": s.export["name"], "primitives": prims})
    g.j["nodes"].append({"name": s.export["name"], "mesh": 0, "skin": 0})
    roots = [i for i, b in enumerate(rs["bones"]) if b[2] < 0]
    g.j["scenes"].append({"nodes": roots + [len(g.j["nodes"]) - 1]})
    g.j["extras"] = {"b4b_template": s.pkg.path}
    g.save_glb(out)
    print(f"wrote {out}: {len(prims)} primitives, {len(P)} vertices, {nb} bones")


# ---- glTF reader --------------------------------------------------------------------------------------------------

CTYPES = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
NORM = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}


class Gltf:
    def __init__(self, path):
        data = open(path, "rb").read()
        if data[:4] == b"glTF":
            jl = struct.unpack_from("<I", data, 12)[0]
            self.j = json.loads(data[20:20 + jl])
            o = 20 + jl
            self.bins = [data[o + 8:o + 8 + struct.unpack_from("<I", data, o)[0]]] if o < len(data) else []
        else:
            self.j = json.loads(data)
            base = os.path.dirname(path)
            self.bins = [open(os.path.join(base, b["uri"]), "rb").read() for b in self.j["buffers"]]

    def acc(self, i):
        a = self.j["accessors"][i]
        bv = self.j["bufferViews"][a["bufferView"]]
        c, n = CTYPES[a["componentType"]], NCOMP[a["type"]]
        esz = struct.calcsize("<" + c) * n
        stride = bv.get("byteStride", esz)
        buf = self.bins[bv["buffer"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        st = struct.Struct("<" + c * n)
        rows = [st.unpack_from(buf, off + k * stride) for k in range(a["count"])]
        if a.get("normalized") and a["componentType"] in NORM:
            d = NORM[a["componentType"]]
            rows = [tuple(x / d for x in r) for r in rows]
        return rows


# ---- import -------------------------------------------------------------------------------------------------------

def node_world(gl, target):
    parent = {}
    for i, n in enumerate(gl.j["nodes"]):
        for c in n.get("children", []): parent[c] = i

    def local(n):
        if "matrix" in n:
            m = n["matrix"]; return [[m[c * 4 + r] for c in range(4)] for r in range(4)]
        return q_to_m(n.get("rotation", (0, 0, 0, 1)), n.get("translation", (0, 0, 0)), n.get("scale", (1, 1, 1)))
    m = local(gl.j["nodes"][target]); i = target
    while i in parent:
        i = parent[i]; m = mm(local(gl.j["nodes"][i]), m)
    return m


def compute_tangents(pos, nrm, uv, tris):
    tan = [[0.0, 0.0, 0.0] for _ in pos]; bit = [[0.0, 0.0, 0.0] for _ in pos]
    for a, b, c in tris:
        e1 = [pos[b][k] - pos[a][k] for k in range(3)]; e2 = [pos[c][k] - pos[a][k] for k in range(3)]
        du1, dv1 = uv[b][0] - uv[a][0], uv[b][1] - uv[a][1]
        du2, dv2 = uv[c][0] - uv[a][0], uv[c][1] - uv[a][1]
        r = du1 * dv2 - du2 * dv1
        if abs(r) < 1e-12: continue
        r = 1.0 / r
        t = [(e1[k] * dv2 - e2[k] * dv1) * r for k in range(3)]
        bb = [(e2[k] * du1 - e1[k] * du2) * r for k in range(3)]
        for v in (a, b, c):
            for k in range(3): tan[v][k] += t[k]; bit[v][k] += bb[k]
    out = []
    for v in range(len(pos)):
        n = nrm[v]
        t = tan[v]
        t = [t[k] - n[k] * dot(n, t) for k in range(3)]
        if dot(t, t) < 1e-12:
            t = cross(n, (0, 0, 1) if abs(n[2]) < 0.9 else (1, 0, 0))
        t = norm(t)
        w = -1.0 if dot(cross(n, t), bit[v]) < 0 else 1.0
        out.append(t + (w,))
    return out


def compute_normals(pos, tris):
    nrm = [[0.0, 0.0, 0.0] for _ in pos]
    for a, b, c in tris:
        n = cross([pos[b][k] - pos[a][k] for k in range(3)], [pos[c][k] - pos[a][k] for k in range(3)])
        for v in (a, b, c):
            for k in range(3): nrm[v][k] += n[k]
    return [norm(n) for n in nrm]


def quantize_weights(infl, k):
    """[(bone, float w)] -> k (bone, u8) pairs summing to 255 (UE's rounding: fix the error on the largest)."""
    infl = sorted([x for x in infl if x[1] > 0], key=lambda x: -x[1])[:k]
    tot = sum(w for _, w in infl) or 1.0
    q = [(b, int(round(w / tot * 255))) for b, w in infl]
    q = [x for x in q if x[1] > 0] or [(infl[0][0] if infl else 0, 255)]
    err = 255 - sum(w for _, w in q)
    q[0] = (q[0][0], q[0][1] + err)
    return q


def build_dups(pos_list):
    """FDuplicatedVerticesBuffer for one section: (DupVertData, DupVertIndexData) of section-local indices."""
    by = {}
    for i, p in enumerate(pos_list):
        by.setdefault(p, []).append(i)
    data, index = [], []
    for i, p in enumerate(pos_list):
        others = [j for j in by[p] if j != i]
        index.append((len(others), len(data)))
        data.extend((j,) for j in others)
    if not data:
        data = [(0,)]                      # the engine never writes an empty buffer (retail: 1 dummy entry)
    return data, index


def edit_lodinfo_count(s, n):
    """Truncate the tagged LODInfo array to its first n elements (SkeletalMeshLODInfo structs)."""
    p = s.props["LODInfo"]
    d = s.data
    r = skm.upkg.R(d, p["off"])
    cnt = r.i32()
    if cnt <= n: return
    tag_start = r.p
    s.pkg.fname(r); s.pkg.fname(r)                         # inner tag name, type
    size_pos = r.p; inner_size = r.i32(); r.i32()
    s.pkg.fname(r); r.p += 16
    if r.u8(): r.p += 16
    elems = []
    for _ in range(cnt):
        a = r.p; s.pkg.skip_tagged(r); elems.append((a, r.p))
    end = elems[n - 1][1]
    new_inner = end - elems[0][0]
    head = bytearray(d[p["off"]:elems[0][0]])
    struct.pack_into("<i", head, 0, n)
    struct.pack_into("<i", head, size_pos - p["off"], new_inner)
    newval = bytes(head) + d[elems[0][0]:end]
    # outer property Size sits 8 bytes before ArrayIndex... recompute: the tag's Size field precedes its value by
    # (ArrayIndex 4 + InnerType FName 8 + HasGuid 1) bytes for an ArrayProperty without a property guid
    size_field = p["off"] - 1 - 8 - 4 - 4
    assert struct.unpack_from("<i", d, size_field)[0] == p["size"]
    out = bytearray(d[:p["off"]]) + newval + d[p["off"] + p["size"]:]
    struct.pack_into("<i", out, size_field, len(newval))
    delta = len(newval) - p["size"]
    s.data = bytes(out)
    s.props_end += delta
    s.props = {}
    rr = skm.upkg.R(s.data); s.pkg.skip_tagged(rr, s.props)
    assert rr.p == s.props_end


MODEL_EXTS = (".fbx", ".obj", ".dae", ".blend", ".vrm")


def blender_exe():
    return os.environ.get("B4B_BLENDER") or os.environ.get("BLENDER") or "blender"


def to_gltf(src, tmpdir=None):
    """FBX/OBJ/DAE/.blend -> glb through Blender (blender/b4bfit.py convert, next to this file). glTF passes through."""
    if not src.lower().endswith(MODEL_EXTS):
        return src
    import subprocess, tempfile
    tmpdir = tmpdir or tempfile.mkdtemp(prefix="b4bconv")
    out = os.path.join(tmpdir, os.path.splitext(os.path.basename(src))[0] + ".glb")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender", "b4bfit.py")
    r = subprocess.run([blender_exe(), "-b", "--factory-startup", "--python", script, "--", "convert",
                        os.path.abspath(src), out], capture_output=True, text=True)
    if r.returncode or not os.path.exists(out):
        sys.stderr.write(r.stdout[-3000:] + r.stderr[-3000:])
        raise SystemExit(f"Blender could not convert {src} (set B4B_BLENDER to the blender executable)")
    print(f"converted {src} -> {out} (Blender)")
    return out


def read_gltf(gl, s, bind, matmap):
    """All skinned primitives of a glTF as UE-space vertices and per-slot triangle lists."""
    m = s.m
    rs = m["refskel"]
    names = {s.name((b[0], b[1])).lower(): i for i, b in enumerate(rs["bones"])}
    slots = {s.name(x["slot_name"]).lower(): i for i, x in enumerate(m["materials"])}
    G = bone_globals(rs)
    mesh_nodes = [n for n in gl.j["nodes"] if "mesh" in n and "skin" in n]
    if not mesh_nodes:
        raise SystemExit("no skinned mesh in the glTF")
    verts = []            # (pos_ue, nrm_ue, tan_ue, sign, [uv...], color bgra, [(bone, w)])
    sections = []         # (slot, [tri (a,b,c) global vertex idx])
    ntc = 1
    for node in mesh_nodes:
        skin = gl.j["skins"][node["skin"]]
        joints = skin["joints"]
        jbone = []
        for jn in joints:
            nm = gl.j["nodes"][jn].get("name", "").lower()
            if nm not in names:
                raise SystemExit(f"joint {gl.j['nodes'][jn].get('name')!r} is not a bone of the template skeleton")
            jbone.append(names[nm])
        ibm = gl.acc(skin["inverseBindMatrices"]) if "inverseBindMatrices" in skin else None
        # rebind: glTF bind (IBM^-1, in glTF space) -> template bind (UE). Per joint: T_j = G_tmpl * IBM_gl (in UE).
        # Joint positions of the glTF bind pose vs the template's. Blender re-orients bone axes on import, so only
        # positions are compared; "rebind" (full matrices) is only right for rigs whose bone axes were kept.
        rebind = []
        worst, worst_bone = 0.0, None
        used = set()
        for prim in gl.j["meshes"][node["mesh"]]["primitives"]:
            si = 0
            while f"JOINTS_{si}" in prim["attributes"]:
                for J, Wt in zip(gl.acc(prim["attributes"][f"JOINTS_{si}"]), gl.acc(prim["attributes"][f"WEIGHTS_{si}"])):
                    used.update(int(j) for j, w in zip(J, Wt) if w > 0)
                si += 1
        for k, b in enumerate(jbone):
            ib = [[ibm[k][c * 4 + r] for c in range(4)] for r in range(4)] if ibm else minv(node_world(gl, joints[k]))
            bind_ue = m_gl2ue(minv(ib))
            d = math.sqrt(sum((bind_ue[r][3] - G[b][r][3]) ** 2 for r in range(3)))
            if k in used and d > worst: worst, worst_bone = d, s.name(rs["bones"][b][:2])
            rebind.append(mm(G[b], m_gl2ue(ib)))
        use_rebind = bind == "rebind"
        print(f"node {node.get('name')}: {len(joints)} joints ({len(used)} used), max offset of a used joint from the "
              f"template bind pose {worst:.3f} cm ({worst_bone})" + (" -> re-binding vertices" if use_rebind else ""))
        if worst > 0.5 and not use_rebind:
            print("  WARNING: the rig's bones moved; the game animates with the template skeleton, so the mesh "
                  "will be skinned as if the bones were where the template has them")
        for prim in gl.j["meshes"][node["mesh"]]["primitives"]:
            if prim.get("mode", 4) != 4: raise SystemExit("only triangle primitives")
            a = prim["attributes"]
            pos = gl.acc(a["POSITION"])
            n = len(pos)
            idx = [r[0] for r in gl.acc(prim["indices"])] if "indices" in prim else list(range(n))
            tris = [tuple(idx[i:i + 3]) for i in range(0, len(idx) - 2, 3)]
            nrm = gl.acc(a["NORMAL"]) if "NORMAL" in a else compute_normals(pos, tris)
            uvs = []
            c = 0
            while f"TEXCOORD_{c}" in a:
                uvs.append(gl.acc(a[f"TEXCOORD_{c}"])); c += 1
            if not uvs: uvs = [[(0.0, 0.0)] * n]
            ntc = max(ntc, len(uvs))
            tan = gl.acc(a["TANGENT"]) if "TANGENT" in a else compute_tangents(pos, nrm, uvs[0], tris)
            col = gl.acc(a["COLOR_0"]) if "COLOR_0" in a else None
            infl = [[] for _ in range(n)]
            s_i = 0
            while f"JOINTS_{s_i}" in a:
                J = gl.acc(a[f"JOINTS_{s_i}"]); Wt = gl.acc(a[f"WEIGHTS_{s_i}"])
                for v in range(n):
                    for jj, ww in zip(J[v], Wt[v]):
                        if ww > 0: infl[v].append((jbone[int(jj)], ww, int(jj)))
                s_i += 1
            mname = gl.j["materials"][prim["material"]]["name"] if "material" in prim else ""
            base_name = re.sub(r"\.\d{3}$", "", mname).lower()        # Blender's "Name.001" duplicates
            slot = matmap.get(mname.lower(), matmap.get(base_name, slots.get(mname.lower(), slots.get(base_name))))
            if slot is None:
                slot = 0
                print(f"  material {mname!r}: no slot of that name, using slot 0 "
                      f"({s.name(m['materials'][0]['slot_name'])}); map with --material {mname}=<slot>")
            base = len(verts)
            unweighted = 0
            for v in range(n):
                p = p_gl2ue(pos[v]); nn = norm(v_gl2ue(nrm[v])); t4 = tan[v]
                tt = norm(v_gl2ue(t4[:3])); sign = -t4[3] if len(t4) > 3 else -1.0
                if use_rebind and infl[v]:
                    tot = sum(w for _, w, _ in infl[v])
                    M = [[sum(w * rebind[j][r][c] for _, w, j in infl[v]) / tot for c in range(4)] for r in range(4)]
                    p = mp(M, p); nn = norm(mv(M, nn)); tt = norm(mv(M, tt))
                if not infl[v]: unweighted += 1
                cc = col[v] if col else (1.0, 1.0, 1.0, 1.0)
                cc = tuple(cc) + (1.0,) * (4 - len(cc))
                bgra = tuple(max(0, min(255, int(round(x * 255)))) for x in (cc[2], cc[1], cc[0], cc[3]))
                verts.append((p, nn, tt, sign, [u[v] for u in uvs], bgra, [(b, w) for b, w, _ in infl[v]]))
            if unweighted:
                print(f"  WARNING: {unweighted} vertices of {mname!r} have no bone weights (bound to the root)")
            # a cloth region (blender/b4bdangle.py names its objects B4BCLOTH_...) gets its own section on the slot
            # (B4BCLOTH_R<k>_...: the k-th garment, its own section and clothing asset)
            key = slot
            mt = CLOTH_RX.match(node.get("name", ""))
            if mt: key = (slot, "cloth", int(mt.group(1) or 0))
            sections.append((key, [(a_ + base, b_ + base, c_ + base) for a_, b_, c_ in tris]))
    return verts, sections, ntc


def build_lod(s, verts, sections, ntc, tmpl_lod):
    """FSkeletalMeshLODRenderData from imported vertices: one section per slot, vertices re-ordered per section."""
    m = s.m
    rs = m["refskel"]
    by_slot = {}
    for slot, tris in sections:
        by_slot.setdefault(slot, []).extend(tris)
    maxk = max(len(v[6]) for v in verts)
    K = 8 if maxk > 4 else 4
    if maxk > 8: print(f"  {maxk} influences on some vertices: keeping the 8 largest")
    root = 0
    new_pos, new_tan, new_uv, new_col, new_w, new_idx, secs = [], [], [], [], bytearray(), [], []
    for slot in sorted(by_slot, key=lambda k: (k, 0, 0) if isinstance(k, int) else (k[0], 1, k[2])):
        tris = by_slot[slot]
        cloth_tag = not isinstance(slot, int)
        region = slot[2] if cloth_tag else -1
        slot = slot if isinstance(slot, int) else slot[0]
        remap = {}
        order = []
        for t in tris:
            for v in t:
                if v not in remap: remap[v] = len(order); order.append(v)
        vbase = len(new_pos)
        ibase = len(new_idx)
        qw = [quantize_weights(verts[v][6] or [(root, 1.0)], K) for v in order]
        bone_map = sorted({b for q in qw for b, _ in q})
        if len(bone_map) > 255: raise SystemExit("a section uses more than 255 bones")
        bl = {b: i for i, b in enumerate(bone_map)}
        sec_max_infl = max(len(q) for q in qw)
        for v, q in zip(order, qw):
            p, nn, tt, sign, uv, bgra, _ = verts[v]
            new_pos.append(tuple(float(x) for x in p))
            new_tan.append(skm.pack_normal(tt + (1.0,)) + skm.pack_normal(nn + (sign,)))
            for c in range(ntc):
                new_uv.append(tuple(uv[c]) if c < len(uv) else tuple(uv[0]))   # extra template channels: UV0
            new_col.append(bgra)
            q = q + [(bone_map[0], 0)] * (K - len(q))
            new_w += bytes(bl[b] if w else 0 for b, w in q) + bytes(w for _, w in q)
        for a_, b_, c_ in tris:
            new_idx += [vbase + remap[a_], vbase + remap[b_], vbase + remap[c_]]
        dup_data, dup_index = build_dups(new_pos[vbase:])
        secs.append(skm.Obj(
            strip=(1, 0), material_index=slot, base_index=ibase, num_triangles=len(tris), recompute_tangent=False,
            cast_shadow=True, base_vertex_index=vbase, cloth_mapping=[], bone_map=[(b,) for b in bone_map],
            num_vertices=len(order), max_bone_influences=sec_max_infl, cloth_asset_index=-1,
            clothing_data=(b"\0" * 16, -1), dup_vert_data=dup_data, dup_vert_index=dup_index, disabled=False,
            cloth_tag=cloth_tag, cloth_region=region))
    nv = len(new_pos)
    parents = [b[2] for b in rs["bones"]]
    active = set()
    for sec in secs:
        for (b,) in sec["bone_map"]:
            while b >= 0 and b not in active: active.add(b); b = parents[b]
    wide = nv > 0xFFFF
    lod = skm.Obj(
        strip=(1, 0), cooked_out=False, inlined=True,
        required_bones=[(i,) for i in range(len(rs["bones"]))],
        sections=secs, active_bones=[(b,) for b in sorted(active)], buffers_size=0,
        stream_strip=(1, 1),                                  # CDSF_AdjacencyData stripped: no adjacency buffer
        indices=skm.Obj(data_size=4 if wide else 2, indices=[(i,) for i in new_idx]),
        positions=skm.Obj(stride=12, num_vertices=nv, positions=new_pos),
        static_vb=skm.Obj(strip=(1, 0), num_texcoords=ntc, num_vertices=nv,
                          full_uvs=tmpl_lod["static_vb"]["full_uvs"],          # weapons: float UVs, heroes: half
                          high_prec_tangents=False, tangents=new_tan, uvs=new_uv),
        skin_weights=skm.Obj(strip=(1, 0), variable_bones=False, max_influences=K, num_bones=K * nv,
                             num_vertices=nv, use_16bit_index=False, data=(1, len(new_w), bytes(new_w)),
                             lookup_strip=(1, 0), lookup_num_vertices=0, lookup=(4, 0, b"")),
        skin_weight_profiles=[], ray_tracing_data=[])
    if m["has_vertex_colors"]:
        lod["colors"] = skm.Obj(strip=(1, 0), stride=4, num_vertices=nv, colors=new_col)
    # BuffersSize = byte size of the streamed block (verified on retail meshes)
    ar = skm.Ar(s.pkg); skm.ser_lod(ar, lod, m); full = len(ar.b)
    ar2 = skm.Ar(s.pkg)
    skm.ser_strip(ar2, lod["strip"]); ar2.bool32(False); ar2.bool32(True); ar2.packed_array("h", lod["required_bones"])
    ar2.array(lambda x: skm.ser_section(ar2, x), lod["sections"]); ar2.packed_array("h", lod["active_bones"]); ar2.u32(0)
    lod["buffers_size"] = full - len(ar2.b)
    print(f"  LOD: {nv} vertices, {len(new_idx) // 3} triangles, sections on slots {[x['material_index'] for x in secs]}, "
          f"{K} influences, {ntc} UV channel(s)")
    return lod


def lodinfo_elements(s):
    """(start, end) offsets in s.data of each SkeletalMeshLODInfo struct of the tagged LODInfo array."""
    p = s.props["LODInfo"]
    r = skm.upkg.R(s.data, p["off"])
    cnt = r.i32()
    s.pkg.fname(r); s.pkg.fname(r); r.i32(); r.i32(); s.pkg.fname(r); r.p += 16
    if r.u8(): r.p += 16
    out = []
    for _ in range(cnt):
        a = r.p; s.pkg.skip_tagged(r); out.append((a, r.p))
    return out


def clear_lod_material_maps(s):
    """LODMaterialMap (section index -> material slot, per LOD) is applied at runtime; our sections carry their own
    slot, so every entry becomes -1 ("use the section's material"). In place, sizes unchanged."""
    d = bytearray(s.data)
    n = 0
    for a, b in lodinfo_elements(s):
        r = skm.upkg.R(d, a); props = {}
        s.pkg.skip_tagged(r, props)
        p = props.get("LODMaterialMap")
        if p:
            cnt = struct.unpack_from("<i", d, p["off"])[0]
            for k in range(cnt):
                struct.pack_into("<i", d, p["off"] + 4 + 4 * k, -1); n += 1
    s.data = bytes(d)
    return n


def edit_sockets(s, sockets):
    """--socket NAME=x,y,z (cm, mesh component space): rewrite that SkeletalMeshSocket's RelativeLocation relative to
    its bone's bind pose (same package, in place)."""
    if not sockets: return
    rs = s.m["refskel"]
    G = bone_globals(rs)
    bone_index = {s.name((b[0], b[1])).lower(): i for i, b in enumerate(rs["bones"])}
    pkg = s.pkg
    found = set()
    for e in pkg.exports:
        if pkg.class_name(e) != "SkeletalMeshSocket": continue
        data = bytearray(pkg.export_data(e))
        r = skm.upkg.R(data); props = {}
        pkg.skip_tagged(r, props)
        rr = skm.upkg.R(data, props["SocketName"]["off"]); sname = pkg.fname(rr)
        if sname.lower() not in sockets: continue
        rr = skm.upkg.R(data, props["BoneName"]["off"]); bname = pkg.fname(rr)
        if "RelativeLocation" not in props:
            print(f"  socket {sname}: no RelativeLocation property to edit (at the bone origin); skipped"); continue
        want = sockets[sname.lower()]
        loc = mp(minv(G[bone_index[bname.lower()]]), want)
        struct.pack_into("<3f", data, props["RelativeLocation"]["off"], *loc)
        pkg.set_export_data(e, bytes(data))
        found.add(sname.lower())
        print(f"  socket {sname} (bone {bname}): component {tuple(round(x, 2) for x in want)} -> "
              f"relative {tuple(round(x, 2) for x in loc)}")
    for k in sockets:
        if k not in found: print(f"  WARNING: no socket {k!r} in the template package")


def set_bone_positions(s, bones):
    """--bone NAME=x,y,z (cm, component space): move a bone of the mesh's reference skeleton (bind pose) there,
    keeping its rotation (for weapon bones like muzzle on 3P weapon skeletons; face bones onto a custom face).
    Bones not listed keep their local transform (children move with their parent); listed children of a moved parent
    still land on their own target (parents are processed first)."""
    if not bones: return
    rs = s.m["refskel"]
    G = bone_globals(rs)
    idx = {s.name((b[0], b[1])).lower(): i for i, b in enumerate(rs["bones"])}
    for name in bones:
        if name not in idx: print(f"  WARNING: no bone {name!r}")
    want = {idx[n]: p for n, p in bones.items() if n in idx}
    pose = list(rs["pose"])
    newG = []
    moved = 0
    for i, ((ni, nn, par), pz) in enumerate(zip(rs["bones"], pose)):
        if i in want:
            local = mp(minv(newG[par]), want[i]) if par >= 0 else want[i]
            pose[i] = tuple(pz[0:4]) + tuple(local) + tuple(pz[7:10])
            moved += 1
            if len(want) <= 8:
                print(f"  bone {s.name((ni, nn))}: bind position {tuple(round(G[i][r][3], 2) for r in range(3))} -> "
                      f"{tuple(round(x, 2) for x in want[i])}")
        L = q_to_m(pose[i][0:4], pose[i][4:7], pose[i][7:10])
        newG.append(mm(newG[par], L) if par >= 0 else L)
    if len(want) > 8:
        far = max(math.sqrt(sum((G[i][r][3] - newG[i][r][3]) ** 2 for r in range(3))) for i in want)
        print(f"  {moved} bones moved in the bind pose (up to {far:.2f} cm)")
    rs["pose"] = pose


def import_gltf(template, srcs, out, matmap=None, bind="keep", copies=1, sockets=None, bones=None, slot_colors=None,
                bind_bones=None, cloth=None, cloth_src=None):
    """slot_colors: {slot name: (b, g, r, a)} vertex colour for every vertex of that slot's sections (e.g. hero hair:
    Master_Hair_M tints vertex-coloured strands with 'Vertex Color Multiplier'; retail strands are mostly black).
    bind_bones: {bone: UE cm} the glTF's own bind skeleton (a model fitted with its own proportions), written before the
    glTF is read; bones: extra bind positions written after (weapon markers, face bones).
    cloth: simulation meshes from blender/b4bdangle.py (manifest extras "cloth"): written into the template's clothing
    assets, or new ones added to the package (cloth_src: the extract folder with cloth.NEEDS), cloth sections bound to
    them (cloth.py)."""
    s = skm.SkeletalMesh(template)
    m = s.m
    names = [s.name(x["slot_name"]).lower() for x in m["materials"]]
    recolor = {names.index(k.lower()): tuple(v) for k, v in (slot_colors or {}).items() if k.lower() in names}
    if s.props.get("MorphTargets"):
        raise SystemExit("template has morph targets: not supported (their vertex indices would not match); no "
                         "retail survivor, weapon or FP mesh has any")
    matmap = {k.lower(): v for k, v in (matmap or {}).items()}
    tmpl_lod = next(l for l in m["lods"] if "sections" in l)
    tmpl_ntc = tmpl_lod["static_vb"]["num_texcoords"]
    # the bind skeleton of a model fitted with its own proportions: the glTF's joints are compared to it
    set_bone_positions(s, bind_bones or {})
    lods = []
    for i, src in enumerate(srcs):
        print(f"LOD{i}: {src}")
        verts, sections, ntc = read_gltf(Gltf(to_gltf(src)), s, bind, matmap)
        for slot, tris in sections:
            if slot in recolor:
                for v in {x for t in tris for x in t}:
                    verts[v] = verts[v][:5] + (recolor[slot],) + verts[v][6:]
        lods.append(build_lod(s, verts, sections, max(ntc, tmpl_ntc), tmpl_lod))
    if len(srcs) == 1 and copies > 1:
        lods = lods * copies
    nl = min(len(lods), len(m["lods"]))
    if len(lods) > len(m["lods"]):
        print(f"  the template has {len(m['lods'])} LODs: keeping the first {nl} of yours")
    m["lods"] = lods[:nl]
    m["num_inlined_lods"] = nl
    edit_lodinfo_count(s, nl)
    n = clear_lod_material_maps(s)
    if n: print(f"  LODMaterialMap: {n} entries set to -1 (sections keep their own slots)")
    set_bone_positions(s, bones or {})
    if cloth:
        import cloth as _cloth
        _cloth.apply(s, cloth, log=lambda *a: print(" ", *a), src=cloth_src)
    # bounds (bind pose, all LODs)
    allp = [p for lod in m["lods"] for p in lod["positions"]["positions"]]
    xs = [p[0] for p in allp]; ys = [p[1] for p in allp]; zs = [p[2] for p in allp]
    o = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    e = ((max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2, (max(zs) - min(zs)) / 2)
    r = max(math.sqrt(sum((p[k] - o[k]) ** 2 for k in range(3))) for p in allp)
    m["bounds"] = o + e + (r,)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    s.pkg.set_export_data(s.export, s.serialize())
    edit_sockets(s, sockets or {})
    s.pkg.save(out)
    chk = skm.SkeletalMesh(out)
    print(f"wrote {out}: {nl} LOD(s), bounds {tuple(round(x, 1) for x in m['bounds'])}, "
          f"{len(chk.m['lods'])} LODs re-read OK")


def parse_vec(spec):
    k, v = spec.split("=", 1)
    return k.lower(), tuple(float(x) for x in v.split(","))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    e = sp.add_parser("export", help="SKM -> glb (reference for Blender)")
    e.add_argument("src"); e.add_argument("out"); e.add_argument("--lod", type=int, default=0)
    i = sp.add_parser("import", help="glTF/glb or FBX/OBJ/DAE/.blend (converted with Blender) -> SKM on a template",
                      description="Build a cooked skeletal mesh from a model on a template SKM's skeleton. Input: "
                                  "glTF/glb, or FBX/OBJ/DAE/.blend converted with Blender (B4B_BLENDER=<blender>). "
                                  "For a model that isn't on the B4B skeleton yet, fit it first with "
                                  "blender/b4bfit.py (or b4bmodel.py).")
    i.add_argument("template"); i.add_argument("src", help="LOD0 model (.glb/.gltf/.fbx/.obj/.dae/.blend)")
    i.add_argument("out")
    i.add_argument("--lod", action="append", default=[], help="LOD1, LOD2, ... models (repeat, in order)")
    i.add_argument("--lods", type=int, default=1, help="without --lod: write N copies of LOD0 (old behaviour)")
    i.add_argument("--material", action="append", default=[], help="glTF material NAME=SLOT index")
    i.add_argument("--bind", choices=["keep", "rebind"], default="keep")
    i.add_argument("--socket", action="append", default=[], help="NAME=x,y,z: move a mesh socket (cm, mesh space)")
    i.add_argument("--bone", action="append", default=[], help="NAME=x,y,z: move a bone of the mesh's bind pose")
    a = ap.parse_args()
    if a.cmd == "export":
        export(a.src, a.out, a.lod)
    else:
        mm_ = {k: int(v) for k, v in (x.split("=", 1) for x in a.material)}
        import_gltf(a.template, [a.src] + a.lod, a.out, mm_, a.bind, copies=a.lods,
                    sockets=dict(parse_vec(x) for x in a.socket), bones=dict(parse_vec(x) for x in a.bone))
