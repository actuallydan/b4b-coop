"""B4B UStaticMesh cooked render data: parse, edit and re-serialize (byte-identical round trip), glTF/OBJ import.

Layout: docs/investigations/mesh-mods.md §1b. Same symmetric-serializer approach as skm.py (class skm.Ar).

  sm.py info <x_SM.uasset>...                  summary: materials, LODs, sections, buffers, distance fields
  sm.py roundtrip <x_SM.uasset>...             parse + re-serialize every file, compare bytes
  sm.py import <template_SM.uasset> <in.glb|.gltf|.fbx|.obj|.dae|.blend> <out.uasset> [--lod <LOD1 model>]...
        [--material NAME=SLOT]...
      new render data from the model onto the template package: its materials, sockets, BodySetup (collision),
      NavCollision and LOD screen sizes stay; geometry, bounds (+ ExtendedBounds) are rebuilt; distance fields are
      dropped. Every primitive is flattened with its node transform (glTF metres, Y up -> UE cm, Z up; same axis rules
      as skmgltf.py). FBX/OBJ/DAE/.blend go through Blender (B4B_BLENDER).
  sm.py from-skinned <template_SM> <template_SKM> <out.uasset> <LOD0 model> [<LOD1 model>...] [--only-bone mag]
      a static mesh from a model fitted onto a skeletal template (b4bmodel weapon: pickup, 3P static, dropped magazine):
      the model is moved from the skeletal template's space into the static template's space by the transform that
      maps the retail skeletal mesh onto the retail static one (area-weighted surface moments).

The collision (BodySetup export: simple shapes + cooked PhysX data) is the template's. It stays valid for a model of
about the same size (weapon pickups: one box).
"""
import math, os, re, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upkg, skm
from skm import Ar, Obj, S, ser_strip, ser_position_vb, ser_static_vb, ser_color_vb


def ser_section(ar, s=None):
    s = s if s is not None else Obj()
    S(ar, s, "material_index", ar.i32)
    S(ar, s, "first_index", ar.u32)
    S(ar, s, "num_triangles", ar.u32)
    S(ar, s, "min_vertex_index", ar.u32)
    S(ar, s, "max_vertex_index", ar.u32)
    S(ar, s, "enable_collision", ar.bool32)
    S(ar, s, "cast_shadow", ar.bool32)
    S(ar, s, "force_opaque", ar.bool32)
    S(ar, s, "visible_in_ray_tracing", ar.bool32)            # TRS (4.26 backport), CUE4Parse HasVisibleInRayTracing
    return s


def ser_raw_index(ar, b=None):
    """FRawStaticIndexBuffer (4.25): bool b32Bit, bulk uint8 IndexStorage, bool bShouldExpandTo32Bit."""
    b = b if b is not None else Obj()
    S(ar, b, "is32", ar.bool32)
    S(ar, b, "data", lambda v: ar.bulk_raw(v))
    S(ar, b, "expand32", ar.bool32)
    return b


def ser_sampler(ar, s=None):
    """FWeightedRandomSampler: TArray<float> Prob, TArray<int32> Alias, float TotalWeight."""
    s = s if s is not None else Obj()
    S(ar, s, "prob", lambda v: ar.packed_array("f", v))
    S(ar, s, "alias", lambda v: ar.packed_array("i", v))
    S(ar, s, "total", ar.f32)
    return s


def ser_buffers(ar, lod):
    """FStaticMeshLODResources::SerializeBuffers."""
    S(ar, lod, "buf_strip", lambda v: ser_strip(ar, v))
    cls = lod["buf_strip"][1]
    S(ar, lod, "positions", lambda v: ser_position_vb(ar, v))
    S(ar, lod, "static_vb", lambda v: ser_static_vb(ar, v))
    S(ar, lod, "colors", lambda v: ser_color_vb(ar, v))
    S(ar, lod, "indices", lambda v: ser_raw_index(ar, v))
    if not cls & 4:
        S(ar, lod, "reversed_indices", lambda v: ser_raw_index(ar, v))
    S(ar, lod, "depth_indices", lambda v: ser_raw_index(ar, v))
    if not cls & 4:
        S(ar, lod, "reversed_depth_indices", lambda v: ser_raw_index(ar, v))
    if not lod["buf_strip"][0] & 1:
        S(ar, lod, "wireframe_indices", lambda v: ser_raw_index(ar, v))
    if not cls & 1:
        S(ar, lod, "adjacency_indices", lambda v: ser_raw_index(ar, v))
    if not cls & 8:
        S(ar, lod, "ray_tracing", lambda v: ar.bulk_raw(v))
    S(ar, lod, "section_samplers", lambda v: [ser_sampler(ar, x) for x in (v if v is not None else [None] * len(lod["sections"]))])
    S(ar, lod, "sampler", lambda v: ser_sampler(ar, v))


def ser_lod(ar, lod=None):
    """FStaticMeshLODResources::Serialize (cooked)."""
    lod = lod if lod is not None else Obj()
    S(ar, lod, "strip", lambda v: ser_strip(ar, v))
    S(ar, lod, "sections", lambda v: ar.array(lambda x: ser_section(ar, x), v))
    S(ar, lod, "max_deviation", ar.f32)
    S(ar, lod, "cooked_out", ar.bool32)
    S(ar, lod, "inlined", ar.bool32)
    if not lod["strip"][0] & 2 and not lod["cooked_out"]:
        if not lod["inlined"]:
            raise NotImplementedError("streamed (.ubulk) static mesh LOD")
        ser_buffers(ar, lod)
        S(ar, lod, "buffers_size", lambda v: ar.fmt("3I", v))  # SerializedBuffersSize, DepthOnlyIBSize, ReversedIBsSize
    return lod


def ser_distance_field(ar, d=None):
    d = d if d is not None else Obj()
    S(ar, d, "compressed", lambda v: ar.packed_array("B", v))
    S(ar, d, "size", lambda v: ar.fmt("3i", v))
    S(ar, d, "box", lambda v: ar.fmt("6fB", v))
    S(ar, d, "min_max", lambda v: ar.fmt("2f", v))
    S(ar, d, "flags", lambda v: ar.fmt("3I", v))             # bMeshWasClosed, bBuiltAsIfTwoSided, bMeshWasPlane
    return d


def ser_static_material(ar, m=None):
    m = m if m is not None else Obj()
    S(ar, m, "material", ar.i32)
    S(ar, m, "slot_name", ar.fname)
    S(ar, m, "uv_channel", lambda v: ar.fmt("II4f", v))
    return m


def ser_mesh(ar, m):
    """Native part of UStaticMesh::Serialize after the tagged properties (4.25, cooked)."""
    S(ar, m, "has_guid", ar.bool32)
    if m["has_guid"]:
        S(ar, m, "guid", lambda v: ar.raw(16, v))
    S(ar, m, "strip", lambda v: ser_strip(ar, v))
    S(ar, m, "cooked", ar.bool32)
    S(ar, m, "body_setup", ar.i32)
    S(ar, m, "nav_collision", ar.i32)
    S(ar, m, "lighting_guid", lambda v: ar.raw(16, v))
    S(ar, m, "sockets", lambda v: ar.packed_array("i", v))
    if not m["cooked"]:
        raise NotImplementedError("uncooked static mesh")
    # FStaticMeshRenderData::Serialize
    S(ar, m, "lods", lambda v: ar.array(lambda x: ser_lod(ar, x), v))
    S(ar, m, "num_inlined_lods", ar.u8)
    S(ar, m, "df_strip", lambda v: ser_strip(ar, v))
    if not m["df_strip"][0] & 2 and not m["df_strip"][1] & 1:
        dfs = m.get("distance_fields") if not ar.loading else None
        out = []
        for i in range(len(m["lods"])):
            d = None if ar.loading else dfs[i]
            valid = ar.bool32(None if ar.loading else d is not None)
            out.append(ser_distance_field(ar, d) if valid else None)
        m["distance_fields"] = out
    S(ar, m, "bounds", lambda v: ar.fmt("7f", v))
    S(ar, m, "lods_share_static_lighting", ar.bool32)
    S(ar, m, "screen_sizes", lambda v: ar.fmt("If" * 8, v))   # 8 x FPerPlatformFloat {bool bCooked, float}
    # back in UStaticMesh::Serialize
    S(ar, m, "has_occluder_data", ar.bool32)
    if m["has_occluder_data"]:
        S(ar, m, "occluder_vertices", lambda v: ar.bulk_raw(v))
        S(ar, m, "occluder_indices", lambda v: ar.bulk_raw(v))
    S(ar, m, "has_speedtree_wind", ar.bool32)
    if m["has_speedtree_wind"]:
        raise NotImplementedError("SpeedTree wind")
    S(ar, m, "static_materials", lambda v: ar.array(lambda x: ser_static_material(ar, x), v))
    return m


class StaticMesh:
    def __init__(self, uasset):
        self.pkg = upkg.Package(uasset)
        self.export = next(e for e in self.pkg.exports if self.pkg.class_name(e) == "StaticMesh")
        self.data = bytes(self.pkg.export_data(self.export))
        r = upkg.R(self.data)
        self.props = {}
        self.pkg.skip_tagged(r, self.props)
        self.props_end = r.p
        ar = Ar(self.pkg, self.data)
        ar.p = self.props_end
        self.m = Obj()
        ser_mesh(ar, self.m)
        self.rest = self.data[ar.p:]

    def name(self, fn):
        s = self.pkg.names[fn[0]]
        return s if fn[1] == 0 else f"{s}_{fn[1] - 1}"

    def serialize(self):
        ar = Ar(self.pkg)
        ser_mesh(ar, self.m)
        return self.data[:self.props_end] + bytes(ar.b) + self.rest

    def save(self, uasset_out):
        self.pkg.set_export_data(self.export, self.serialize())
        self.pkg.save(uasset_out)


def index_list(b):
    es, n, data = b["data"]
    return list(struct.unpack_from("<%d%s" % (n // (4 if b["is32"] else 2), "I" if b["is32"] else "H"), data)) if n else []


def info(path):
    s = StaticMesh(path)
    m = s.m
    print(os.path.basename(path), "props end", s.props_end, "export", len(s.data), "rest", len(s.rest),
          "strip", m["strip"], "body", s.pkg.obj(m["body_setup"]), "nav", s.pkg.obj(m["nav_collision"]),
          "sockets", len(m["sockets"]))
    print("  bounds", ["%.1f" % x for x in m["bounds"]], "inlined", m["num_inlined_lods"], "df strip", m["df_strip"],
          "df", [d is not None for d in m.get("distance_fields") or []], "occluder", m["has_occluder_data"],
          "screen", [round(x, 3) for x in m["screen_sizes"][1::2]])
    for i, mat in enumerate(m["static_materials"]):
        print("  mat", i, s.pkg.obj(mat["material"]), s.name(mat["slot_name"]))
    for li, lod in enumerate(m["lods"]):
        if "positions" not in lod:
            print("  lod", li, "stripped"); continue
        sv = lod["static_vb"]
        print(f"  lod {li}: strip {lod['strip']}/{lod['buf_strip']} verts {lod['positions']['num_vertices']} "
              f"idx {lod['indices']['data'][1]}B 32bit {lod['indices']['is32']} uv {sv['num_texcoords']} "
              f"fulluv {sv['full_uvs']} hpt {sv['high_prec_tangents']} colors {lod['colors']['num_vertices']} "
              f"rev {'reversed_indices' in lod} adj {'adjacency_indices' in lod} rt {lod.get('ray_tracing', (0, 0))[1]} "
              f"maxdev {lod['max_deviation']:.3f} sizes {lod['buffers_size']} "
              f"samplers {[len(x['prob']) for x in lod['section_samplers']]}/{len(lod['sampler']['prob'])}")
        for si, sec in enumerate(lod["sections"]):
            print(f"    sec {si}: mat {sec['material_index']} first {sec['first_index']} tris {sec['num_triangles']} "
                  f"verts {sec['min_vertex_index']}-{sec['max_vertex_index']} coll {sec['enable_collision']} "
                  f"shadow {sec['cast_shadow']} opaque {sec['force_opaque']} rt {sec['visible_in_ray_tracing']}")


def roundtrip(paths, quiet=False):
    bad = 0
    for p in paths:
        try:
            s = StaticMesh(p)
            out = s.serialize()
            ok = out == s.data
            msg = "" if ok else f"{len(out)} vs {len(s.data)}"
        except Exception as e:
            ok, msg = False, repr(e)
        if not ok: bad += 1
        if not ok or not quiet:
            print("OK  " if ok else "FAIL", p, msg)
    print(f"{len(paths) - bad}/{len(paths)} byte-identical")
    return bad == 0


# ---- building render data ---------------------------------------------------------------------------------------

def alias_sampler(weights):
    """FWeightedRandomSampler::Initialize (Vose alias method, as the engine does) over non-negative weights."""
    n = len(weights)
    total = float(sum(weights))
    if n == 0 or total <= 0:
        return Obj(prob=[], alias=[], total=0.0)
    prob = [w * n / total for w in weights]
    alias = [0] * n
    small = [i for i, p in enumerate(prob) if p < 1.0]
    large = [i for i, p in enumerate(prob) if p >= 1.0]
    while small and large:
        s_, l_ = small.pop(), large.pop()
        alias[s_] = l_
        prob[l_] = prob[l_] + prob[s_] - 1.0
        (small if prob[l_] < 1.0 else large).append(l_)
    for i in large + small:
        prob[i] = 1.0
    return Obj(prob=[(p,) for p in prob], alias=[(a,) for a in alias], total=total)


def index_buffer(idx, is32):
    data = struct.pack("<%d%s" % (len(idx), "I" if is32 else "H"), *idx)
    return Obj(is32=is32, data=(1, len(data), data), expand32=False)


def build_lod(tmpl_lod, verts, secs_tris, ntc, full_uvs, keep_samplers):
    rt_flag = bool(tmpl_lod["sections"][0]["visible_in_ray_tracing"]) if tmpl_lod.get("sections") else False
    """verts: [(pos, nrm, tan, sign, [uv...], bgra)], secs_tris: [(slot, [(a,b,c)...])] with global vertex ids.
    Vertices are re-ordered per section (sections own contiguous vertex ranges, as the engine builds them)."""
    new_pos, new_tan, new_uv, new_col, new_idx, secs = [], [], [], [], [], []
    area_by_sec = []
    for slot, tris in secs_tris:
        remap, order = {}, []
        for t in tris:
            for v in t:
                if v not in remap: remap[v] = len(order); order.append(v)
        vbase, ibase = len(new_pos), len(new_idx)
        for v in order:
            p, nn, tt, sign, uv, bgra = verts[v]
            new_pos.append(tuple(float(x) for x in p))
            new_tan.append(skm.pack_normal(tt + (1.0,)) + skm.pack_normal(nn + (sign,)))
            for c in range(ntc):
                new_uv.append(tuple(uv[c]) if c < len(uv) else tuple(uv[0]))
            new_col.append(bgra)
        areas = []
        for a, b, c in tris:
            new_idx += [vbase + remap[a], vbase + remap[b], vbase + remap[c]]
            pa, pb, pc = verts[a][0], verts[b][0], verts[c][0]
            e1 = [pb[k] - pa[k] for k in range(3)]; e2 = [pc[k] - pa[k] for k in range(3)]
            cr = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
            areas.append(0.5 * math.sqrt(sum(x * x for x in cr)))
        area_by_sec.append(areas)
        secs.append(Obj(material_index=slot, first_index=ibase, num_triangles=len(tris), min_vertex_index=vbase,
                        max_vertex_index=vbase + len(order) - 1, enable_collision=True, cast_shadow=True,
                        force_opaque=False, visible_in_ray_tracing=rt_flag))
    nv = len(new_pos)
    is32 = nv > 0xFFFF
    strip = tmpl_lod["strip"]
    bstrip = tmpl_lod["buf_strip"]
    lod = Obj(strip=strip, sections=secs, max_deviation=0.0, cooked_out=False, inlined=True, buf_strip=bstrip,
              positions=Obj(stride=12, num_vertices=nv, positions=new_pos),
              static_vb=Obj(strip=(1, 0), num_texcoords=ntc, num_vertices=nv, full_uvs=full_uvs,
                            high_prec_tangents=False, tangents=new_tan, uvs=new_uv),
              colors=Obj(strip=(1, 0), stride=4, num_vertices=nv, colors=new_col)
              if tmpl_lod["colors"]["num_vertices"] else Obj(strip=(1, 0), stride=0, num_vertices=0))
    lod["indices"] = index_buffer(new_idx, is32)
    cls = bstrip[1]
    # Retail cooks leave the reversed, wireframe and adjacency buffers present but empty (0 bytes); the depth-only
    # buffer is the engine's position-welded copy, the full index list is a valid (unoptimized) stand-in.
    empty = index_buffer([], is32)
    if not cls & 4:
        lod["reversed_indices"] = empty
    lod["depth_indices"] = index_buffer(new_idx, is32)
    if not cls & 4:
        lod["reversed_depth_indices"] = empty
    if not bstrip[0] & 1:
        lod["wireframe_indices"] = empty
    if not cls & 1:
        lod["adjacency_indices"] = empty
    if not cls & 8:
        lod["ray_tracing"] = (1, 0, b"")
    if keep_samplers:
        lod["section_samplers"] = [alias_sampler(a) for a in area_by_sec]
        lod["sampler"] = alias_sampler([sum(a) for a in area_by_sec])
    else:                                                  # retail: empty samplers (no uniform sampling support)
        lod["section_samplers"] = [Obj(prob=[], alias=[], total=0.0) for _ in secs]
        lod["sampler"] = Obj(prob=[], alias=[], total=0.0)
    lod["buffers_size"] = buffers_size(lod)
    return lod


IB_KEYS = ("indices", "reversed_indices", "depth_indices", "reversed_depth_indices", "wireframe_indices",
           "adjacency_indices")


def buffers_size(lod):
    """FStaticMeshBuffersSize as retail stores it (verified on 2118 meshes): payload bytes of every vertex and index
    buffer; the depth-only index bytes; the two reversed index buffers' bytes."""
    sv, pv, cv = lod["static_vb"], lod["positions"], lod["colors"]
    tan = struct.calcsize("<" + ("4H4H" if sv["high_prec_tangents"] else "4B4B")) * sv["num_vertices"]
    uv = struct.calcsize("<" + ("2f" if sv["full_uvs"] else "2e")) * sv["num_vertices"] * sv["num_texcoords"]
    total = pv["stride"] * pv["num_vertices"] + tan + uv + 4 * cv["num_vertices"]
    total += sum(len(lod[k]["data"][2]) for k in IB_KEYS if k in lod)
    rev = sum(len(lod[k]["data"][2]) for k in ("reversed_indices", "reversed_depth_indices") if k in lod)
    return (total, len(lod["depth_indices"]["data"][2]), rev)


def check_sizes(paths):
    bad = 0
    for p in paths:
        try:
            s = StaticMesh(p)
        except StopIteration:
            continue
        for li, lod in enumerate(s.m["lods"]):
            if "positions" in lod and tuple(lod["buffers_size"]) != buffers_size(lod):
                bad += 1
                print("MISMATCH", p, li, lod["buffers_size"], buffers_size(lod))
    print("mismatches:", bad)


# ---- import: frames, template alignment ----------------------------------------------------------------------------

def jacobi3(a):
    """Eigen decomposition of a symmetric 3x3 (Jacobi). Returns (values, vectors as columns)."""
    a = [row[:] for row in a]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(50):
        off = max((abs(a[i][j]), i, j) for i in range(3) for j in range(3) if i < j)
        if off[0] < 1e-12: break
        _, p, q = off
        th = 0.5 * math.atan2(2 * a[p][q], a[q][q] - a[p][p])
        c, s_ = math.cos(th), math.sin(th)
        for k in range(3):
            akp, akq = a[k][p], a[k][q]
            a[k][p] = c * akp - s_ * akq; a[k][q] = s_ * akp + c * akq
        for k in range(3):
            apk, aqk = a[p][k], a[q][k]
            a[p][k] = c * apk - s_ * aqk; a[q][k] = s_ * apk + c * aqk
        for k in range(3):
            vkp, vkq = v[k][p], v[k][q]
            v[k][p] = c * vkp - s_ * vkq; v[k][q] = s_ * vkp + c * vkq
    return [a[i][i] for i in range(3)], v


def surface_frame(tris):
    """Area-weighted surface moments of triangles [(p0, p1, p2)]: centroid, principal axes (rows, largest first,
    signs fixed by the third moment), eigenvalues. Tessellation-independent, so a retail LOD and our model can be
    compared."""
    W, C = 0.0, [0.0, 0.0, 0.0]
    items = []
    for p0, p1, p2 in tris:
        e1 = [p1[k] - p0[k] for k in range(3)]; e2 = [p2[k] - p0[k] for k in range(3)]
        cr = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
        ar = 0.5 * math.sqrt(sum(x * x for x in cr))
        if ar <= 0: continue
        c = [(p0[k] + p1[k] + p2[k]) / 3 for k in range(3)]
        items.append((ar, c)); W += ar
        for k in range(3): C[k] += ar * c[k]
    C = [x / W for x in C]
    M = [[0.0] * 3 for _ in range(3)]
    for ar, c in items:
        d = [c[k] - C[k] for k in range(3)]
        for i in range(3):
            for j in range(3): M[i][j] += ar * d[i] * d[j] / W
    vals, vecs = jacobi3(M)
    order = sorted(range(3), key=lambda i: -vals[i])
    axes = []
    for i in order:
        ax = [vecs[k][i] for k in range(3)]
        sk = sum(ar * sum((c[k] - C[k]) * ax[k] for k in range(3)) ** 3 for ar, c in items)
        if sk < 0: ax = [-x for x in ax]
        axes.append(ax)
    return C, axes, [vals[i] for i in order]


def frame_transform(src_frame, dst_frame):
    """Rotation R (rows) and translation so that dst = R (p - c_src) + c_dst maps the source frame onto the destination
    frame (axis i -> axis i). A reflection is avoided by flipping the least certain (third) axis."""
    cs, As, _ = src_frame
    cd, Ad, _ = dst_frame
    # R = Ad^T As  (As rows = source axes)
    R = [[sum(Ad[k][i] * As[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    det = (R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1]) - R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0])
           + R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0]))
    if det < 0:
        Ad2 = [Ad[0], Ad[1], [-x for x in Ad[2]]]
        R = [[sum(Ad2[k][i] * As[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return R, cs, cd


def apply_rt(R, cs, cd, p):
    d = [p[k] - cs[k] for k in range(3)]
    return tuple(sum(R[i][j] * d[j] for j in range(3)) + cd[i] for i in range(3))


def rot(R, v):
    return tuple(sum(R[i][j] * v[j] for j in range(3)) for i in range(3))


def sm_triangles(lod):
    P = lod["positions"]["positions"]
    idx = index_list(lod["indices"])
    return [(P[idx[t]], P[idx[t + 1]], P[idx[t + 2]]) for t in range(0, len(idx), 3)]


def skm_triangles(lod, only_bone=None):
    P = lod["positions"]["positions"]
    I = [x[0] for x in lod["indices"]["indices"]]
    keep = None
    if only_bone is not None:
        W = skm.decode_weights(lod["skin_weights"])
        keep = [False] * len(P)
        for sec in lod["sections"]:
            bm = [x[0] for x in sec["bone_map"]]
            for v in range(sec["base_vertex_index"], sec["base_vertex_index"] + sec["num_vertices"]):
                best = max(W[v], key=lambda x: x[1])
                keep[v] = bm[best[0]] == only_bone
    out = []
    for t in range(0, len(I), 3):
        a, b, c = I[t], I[t + 1], I[t + 2]
        if keep is None or (keep[a] and keep[b] and keep[c]):
            out.append((P[a], P[b], P[c]))
    return out


def update_extended_bounds(s, bounds):
    """ExtendedBounds tagged struct (Origin, BoxExtent, SphereRadius) of the StaticMesh export, in place."""
    p = s.props.get("ExtendedBounds")
    if not p: return
    d = bytearray(s.data)
    r = upkg.R(d, p["off"]); sub = {}
    s.pkg.skip_tagged(r, sub)
    if "Origin" in sub: struct.pack_into("<3f", d, sub["Origin"]["off"], *bounds[0:3])
    if "BoxExtent" in sub: struct.pack_into("<3f", d, sub["BoxExtent"]["off"], *bounds[3:6])
    if "SphereRadius" in sub: struct.pack_into("<f", d, sub["SphereRadius"]["off"], bounds[6])
    s.data = bytes(d)


def import_static_from_skinned(sm_file, lod_models, skm_template, out, only_bone=None, slot_map=None, fill_slots=True):
    """Static mesh from a model fitted onto a skeletal template (a weapon's 3P mesh): the model's bind-pose geometry is
    moved from the skeletal template's space into the static template's space (the transform that maps the retail
    skeletal mesh onto the retail static mesh, found from their surface moments), then written onto the static
    template. only_bone: keep only triangles skinned to that bone (e.g. the magazine for a dropped-magazine mesh)."""
    import skmgltf
    s = StaticMesh(sm_file)
    k = skm.SkeletalMesh(skm_template)
    tl = next(l for l in s.m["lods"] if "positions" in l)
    kl = next(l for l in k.m["lods"] if "sections" in l)
    ref_src = surface_frame(skm_triangles(kl, None if only_bone is None else bone_index(k, only_bone)))
    ref_dst = surface_frame(sm_triangles(tl))
    R, cs, cd = frame_transform(ref_src, ref_dst)
    print(f"{os.path.basename(sm_file)}: skeletal -> static template transform: axes {[[round(x, 2) for x in r] for r in R]}"
          f", size check {[round(math.sqrt(a), 1) for a in ref_src[2]]} vs {[round(math.sqrt(a), 1) for a in ref_dst[2]]} cm")
    # slots: the skeletal template's slot -> the static slot using the same material instance, else by name, else 0
    sm_mats = [s.pkg.obj_path(m["material"]) for m in s.m["static_materials"]]
    sk_mats = [k.pkg.obj_path(m["material"]) for m in k.m["materials"]]
    def slot_for(i):
        if slot_map and i in slot_map: return slot_map[i]
        if len(sm_mats) == 1: return 0
        if sk_mats[i] in sm_mats: return sm_mats.index(sk_mats[i])
        base = sk_mats[i].split(".")[-1].lower().replace("_3p", "").replace("_fp", "")
        for j, m in enumerate(sm_mats):
            if m.split(".")[-1].lower().replace("_3p", "").replace("_fp", "") == base: return j
        return 0
    bi = None if only_bone is None else bone_index(k, only_bone)
    lods = []
    ntc = tl["static_vb"]["num_texcoords"]
    for li, model in enumerate(lod_models[:len(s.m["lods"])]):
        verts, sections, _ = skmgltf.read_gltf(skmgltf.Gltf(skmgltf.to_gltf(model)), k, "keep", {})
        vv = []
        for p, nn, tt, sign, uv, bgra, infl in verts:
            vv.append((apply_rt(R, cs, cd, p), rot(R, nn), rot(R, tt), sign, uv, bgra))
        keepv = None
        if bi is not None:
            keepv = [bool(v[6]) and max(v[6], key=lambda x: x[1])[0] == bi for v in verts]
        by = {}
        for slot, tris in sections:
            tr = [t for t in tris if keepv is None or all(keepv[x] for x in t)]
            if tr: by.setdefault(slot_for(slot), []).extend(tr)
        if not by: raise SystemExit(f"{model}: no triangles" + (f" skinned to {only_bone}" if only_bone else ""))
        if fill_slots:
            # every material slot keeps a section, in slot order, like the retail mesh (unused slots get one
            # zero-area triangle): game code that addresses sections by index (skins, attachments) sees the same list
            for i in range(len(sm_mats)):
                if i not in by:
                    c = vv[0]
                    base = len(vv)
                    vv += [c, c, c]
                    by[i] = [(base, base + 1, base + 2)]
        lods.append(build_lod(tl, vv, sorted(by.items()), ntc, tl["static_vb"]["full_uvs"], False))
        print(f"  LOD{li}: {lods[-1]['positions']['num_vertices']} vertices, "
              f"{sum(x['num_triangles'] for x in lods[-1]['sections'])} triangles, slots {sorted(by)}")
    write_static(s, lods, out)


def bone_index(k, name):
    for i, b in enumerate(k.m["refskel"]["bones"]):
        if k.name(b[:2]).lower() == name.lower(): return i
    raise SystemExit(f"no bone {name!r} in {k.pkg.path}")


def write_static(s, lods, out):
    m = s.m
    m["lods"] = lods
    m["num_inlined_lods"] = len(lods)
    if m.get("distance_fields") is not None:
        m["distance_fields"] = [None] * len(lods)
    allp = [p for l in lods for p in l["positions"]["positions"]]
    lo = [min(p[k] for p in allp) for k in range(3)]; hi = [max(p[k] for p in allp) for k in range(3)]
    o = [(lo[k] + hi[k]) / 2 for k in range(3)]; e = [(hi[k] - lo[k]) / 2 for k in range(3)]
    r = max(math.sqrt(sum((p[k] - o[k]) ** 2 for k in range(3))) for p in allp)
    m["bounds"] = tuple(o) + tuple(e) + (r,)
    update_extended_bounds(s, m["bounds"])
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    s.save(out)
    chk = StaticMesh(out)
    print(f"wrote {out}: {len(chk.m['lods'])} LOD(s), bounds {tuple(round(x, 1) for x in m['bounds'])} "
          f"(collision: the template's BodySetup)")


def import_model(template, srcs, out, matmap=None, transform=None):
    """Static mesh from plain models (glTF/FBX/OBJ...): all primitives, node transforms applied (skins ignored),
    glTF metres Y-up -> UE cm Z-up. transform: optional 4x4 (UE space, row-major) applied after."""
    import skmgltf
    s = StaticMesh(template)
    tl = next(l for l in s.m["lods"] if "positions" in l)
    names = {s.name(m["slot_name"]).lower(): i for i, m in enumerate(s.m["static_materials"])}
    matmap = {k.lower(): v for k, v in (matmap or {}).items()}
    lods = []
    for model in srcs:
        gl = skmgltf.Gltf(skmgltf.to_gltf(model))
        verts, secs = [], {}
        for ni, node in enumerate(gl.j["nodes"]):
            if "mesh" not in node: continue
            W = skmgltf.node_world(gl, ni)
            for prim in gl.j["meshes"][node["mesh"]]["primitives"]:
                a = prim["attributes"]
                pos = gl.acc(a["POSITION"]); n = len(pos)
                idx = [r[0] for r in gl.acc(prim["indices"])] if "indices" in prim else list(range(n))
                tris = [tuple(idx[i:i + 3]) for i in range(0, len(idx) - 2, 3)]
                nrm = gl.acc(a["NORMAL"]) if "NORMAL" in a else skmgltf.compute_normals(pos, tris)
                uvs = []; c = 0
                while f"TEXCOORD_{c}" in a: uvs.append(gl.acc(a[f"TEXCOORD_{c}"])); c += 1
                if not uvs: uvs = [[(0.0, 0.0)] * n]
                tan = gl.acc(a["TANGENT"]) if "TANGENT" in a else skmgltf.compute_tangents(pos, nrm, uvs[0], tris)
                mname = gl.j["materials"][prim["material"]]["name"] if "material" in prim else ""
                bn = re.sub(r"\.\d{3}$", "", mname).lower()
                slot = matmap.get(bn, names.get(bn, 0))
                base = len(verts)
                for v in range(n):
                    p = skmgltf.p_gl2ue(skmgltf.mp(W, pos[v]))
                    nn = skmgltf.norm(skmgltf.v_gl2ue(skmgltf.mv(W, nrm[v])))
                    tt = skmgltf.norm(skmgltf.v_gl2ue(skmgltf.mv(W, tan[v][:3])))
                    sign = -tan[v][3] if len(tan[v]) > 3 else -1.0
                    if transform:
                        p = skmgltf.mp(transform, p); nn = skmgltf.norm(skmgltf.mv(transform, nn))
                        tt = skmgltf.norm(skmgltf.mv(transform, tt))
                    verts.append((p, nn, tt, sign, [u[v] for u in uvs], (255, 255, 255, 255)))
                secs.setdefault(slot, []).extend((x + base, y + base, z + base) for x, y, z in tris)
        lods.append(build_lod(tl, verts, sorted(secs.items()), tl["static_vb"]["num_texcoords"],
                              tl["static_vb"]["full_uvs"], False))
    write_static(s, lods[:len(s.m["lods"])], out)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "info":
        for p in sys.argv[2:]: info(p)
    elif cmd == "roundtrip":
        q = "-q" in sys.argv
        sys.exit(0 if roundtrip([a for a in sys.argv[2:] if a != "-q"], q) else 1)
    elif cmd == "sizes":
        check_sizes(sys.argv[2:])
    elif cmd == "import":
        import argparse
        ap = argparse.ArgumentParser(prog="sm.py import")
        ap.add_argument("template"); ap.add_argument("src"); ap.add_argument("out")
        ap.add_argument("--lod", action="append", default=[], help="LOD1.. models")
        ap.add_argument("--material", action="append", default=[], help="model material NAME=SLOT index")
        a = ap.parse_args(sys.argv[2:])
        import_model(a.template, [a.src] + a.lod, a.out, {k: int(v) for k, v in (x.split("=", 1) for x in a.material)})
    elif cmd == "from-skinned":
        import argparse
        ap = argparse.ArgumentParser(prog="sm.py from-skinned")
        ap.add_argument("template_sm"); ap.add_argument("template_skm"); ap.add_argument("out")
        ap.add_argument("models", nargs="+", help="LOD0, LOD1, ... models fitted onto the skeletal template")
        ap.add_argument("--only-bone")
        a = ap.parse_args(sys.argv[2:])
        import_static_from_skinned(a.template_sm, a.models, a.template_skm, a.out, a.only_bone)
    else:
        print(__doc__)
