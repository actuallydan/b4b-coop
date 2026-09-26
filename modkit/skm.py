"""B4B USkeletalMesh cooked render data: parse, edit and re-serialize (byte-identical round trip).

Layout: docs/investigations/mesh-mods.md. One symmetric serializer (class Ar: read or write mode) walks the whole
native part of a cooked USkeletalMesh export (after the tagged properties), so what is read can be written back.

  skm.py info <x.uasset>                   summary: bounds, materials, bones, LODs, sections
  skm.py roundtrip <x.uasset>...           parse + re-serialize every file, compare bytes
  skm.py dump-obj <x.uasset> <out.obj> [lod]
  skm.py edit <in.uasset> <out.uasset> [--inflate CM] [--scale-section L:S:F] [--material L:S:M]
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upkg


class Ar:
    """Symmetric archive: in read mode every call returns the value read, in write mode it writes `v` and returns it."""
    def __init__(self, pkg, data=None):
        self.pkg = pkg
        self.loading = data is not None
        self.b = data if data is not None else bytearray()
        self.p = 0

    def _prim(self, fmt, v):
        if self.loading:
            v = struct.unpack_from(fmt, self.b, self.p)[0]
        else:
            self.b += struct.pack(fmt, v)
        self.p += struct.calcsize(fmt)
        return v

    def u8(self, v=None): return self._prim("<B", v)
    def i16(self, v=None): return self._prim("<h", v)
    def u16(self, v=None): return self._prim("<H", v)
    def i32(self, v=None): return self._prim("<i", v)
    def u32(self, v=None): return self._prim("<I", v)
    def f32(self, v=None): return self._prim("<f", v)
    def bool32(self, v=None): return bool(self._prim("<I", None if v is None else int(v)))

    def raw(self, n, v=None):
        if self.loading:
            v = bytes(self.b[self.p:self.p + n])
        else:
            assert len(v) == n
            self.b += v
        self.p += n
        return v

    def fmt(self, f, v=None):
        """Fixed struct (tuple)."""
        n = struct.calcsize("<" + f)
        if self.loading:
            v = struct.unpack_from("<" + f, self.b, self.p)
        else:
            self.b += struct.pack("<" + f, *v)
        self.p += n
        return v

    def fname(self, v=None):
        """FName as (index, number) pair; resolve with pkg.names."""
        return self.fmt("ii", v)

    def array(self, fn, v=None):
        n = self.i32(None if v is None else len(v))
        if self.loading:
            return [fn(None) for _ in range(n)]
        for x in v:
            fn(x)
        return v

    def packed_array(self, f, v=None):
        """TArray of a fixed struct, stored as the raw element tuples (fast path)."""
        esz = struct.calcsize("<" + f)
        n = self.i32(None if v is None else len(v))
        if self.loading:
            v = list(struct.iter_unpack("<" + f, self.b[self.p:self.p + n * esz]))
        else:
            self.b += b"".join(struct.pack("<" + f, *e) for e in v)
        self.p += n * esz
        return v

    def bulk_array(self, f, v=None):
        """BulkSerialize: int32 element size, int32 count, elements."""
        esz = struct.calcsize("<" + f)
        got = self.i32(esz)
        assert got == esz, f"bulk element size {got} != {esz} ({f}) at {self.p}"
        return self.packed_array(f, v)

    def bulk_raw(self, v=None):
        """BulkSerialize with unknown element type: (elemsize, count, bytes)."""
        if self.loading:
            es, n = self.i32(), self.i32()
            return (es, n, self.raw(es * n))
        es, n, data = v
        self.i32(es); self.i32(n); self.raw(len(data), data)
        return v


class Obj(dict):
    __getattr__ = dict.get
    def __setattr__(self, k, v): self[k] = v


def S(ar, o, key, fn):
    """Serialize one field of dict o via fn(ar, value)."""
    o[key] = fn(o.get(key) if not ar.loading else None)


# ---------------------------------------------------------------------------------------------------------------
# Per-structure serializers. Each takes (ar, obj) and returns obj; obj is None when loading.

def ser_strip(ar, v=None):
    return ar.fmt("BB", v)                                  # (GlobalStripFlags, ClassStripFlags)


def ser_bounds(ar, v=None):
    return ar.fmt("7f", v)                                   # Origin xyz, BoxExtent xyz, SphereRadius


def ser_skel_material(ar, m=None):
    m = m if m is not None else Obj()
    S(ar, m, "material", ar.i32)
    S(ar, m, "slot_name", ar.fname)
    S(ar, m, "b_imported_name", ar.bool32)
    if m["b_imported_name"]:
        S(ar, m, "imported_name", ar.fname)
    S(ar, m, "uv_channel", lambda v: ar.fmt("II4f", v))      # bInitialized, bOverrideDensities, LocalUVDensities[4]
    return m


def ser_refskel(ar, r=None):
    r = r if r is not None else Obj()
    S(ar, r, "bones", lambda v: ar.packed_array("iii", v))   # FName (idx, num), ParentIndex
    S(ar, r, "pose", lambda v: ar.packed_array("10f", v))    # quat xyzw, translation, scale3d
    S(ar, r, "name_to_index", lambda v: ar.packed_array("iii", v))
    return r


MESH_TO_MESH = "4f4f4f4HfI"                                  # FMeshToMeshVertData (4.25), 64 bytes


def ser_section(ar, s=None):
    s = s if s is not None else Obj()
    S(ar, s, "strip", lambda v: ser_strip(ar, v))
    S(ar, s, "material_index", ar.u16)
    S(ar, s, "base_index", ar.u32)
    S(ar, s, "num_triangles", ar.u32)
    S(ar, s, "recompute_tangent", ar.bool32)
    S(ar, s, "cast_shadow", ar.bool32)
    S(ar, s, "base_vertex_index", ar.u32)
    S(ar, s, "cloth_mapping", lambda v: ar.packed_array(MESH_TO_MESH, v))
    S(ar, s, "bone_map", lambda v: ar.packed_array("H", v))
    S(ar, s, "num_vertices", ar.i32)
    S(ar, s, "max_bone_influences", ar.i32)
    S(ar, s, "cloth_asset_index", ar.i16)
    S(ar, s, "clothing_data", lambda v: ar.fmt("16si", v))  # AssetGuid, AssetLodIndex
    if not s["strip"][1] & 1:                                # CDSF_DuplicatedVertices not stripped
        S(ar, s, "dup_vert_data", lambda v: ar.packed_array("I", v))
        S(ar, s, "dup_vert_index", lambda v: ar.packed_array("II", v))
    S(ar, s, "disabled", ar.bool32)
    return s


def ser_index_container(ar, c=None):
    c = c if c is not None else Obj()
    S(ar, c, "data_size", ar.u8)
    S(ar, c, "indices", lambda v: ar.bulk_array("H" if c["data_size"] == 2 else "I", v))
    return c


def ser_skin_weights(ar, w=None):
    """FSkinWeightVertexBuffer, 4.25 'unlimited bone influences' layout."""
    w = w if w is not None else Obj()
    S(ar, w, "strip", lambda v: ser_strip(ar, v))
    S(ar, w, "variable_bones", ar.bool32)
    S(ar, w, "max_influences", ar.u32)
    S(ar, w, "num_bones", ar.u32)
    S(ar, w, "num_vertices", ar.u32)
    S(ar, w, "use_16bit_index", ar.bool32)
    assert not w["strip"][0] & 2
    S(ar, w, "data", lambda v: ar.bulk_raw(v))               # element size 1: bone indices then weights per vertex
    S(ar, w, "lookup_strip", lambda v: ser_strip(ar, v))
    S(ar, w, "lookup_num_vertices", ar.i32)
    S(ar, w, "lookup", lambda v: ar.bulk_raw(v))
    return w


def ser_static_vb(ar, sv=None):
    """FStaticMeshVertexBuffer: tangents (TangentX, TangentZ) + UVs."""
    sv = sv if sv is not None else Obj()
    S(ar, sv, "strip", lambda v: ser_strip(ar, v))
    S(ar, sv, "num_texcoords", ar.i32)
    S(ar, sv, "num_vertices", ar.i32)
    S(ar, sv, "full_uvs", ar.bool32)
    S(ar, sv, "high_prec_tangents", ar.bool32)
    tf = "4H4H" if sv["high_prec_tangents"] else "4B4B"
    S(ar, sv, "tangents", lambda v: ar.bulk_array(tf, v))
    # one bulk element per (vertex, texcoord), vertex-major: uvs[v * num_texcoords + channel]
    S(ar, sv, "uvs", lambda v: ar.bulk_array("2f" if sv["full_uvs"] else "2e", v))
    return sv


def ser_position_vb(ar, pv=None):
    pv = pv if pv is not None else Obj()
    S(ar, pv, "stride", ar.i32)
    S(ar, pv, "num_vertices", ar.i32)
    S(ar, pv, "positions", lambda v: ar.bulk_array("3f", v))
    return pv


def ser_color_vb(ar, cv=None):
    cv = cv if cv is not None else Obj()
    S(ar, cv, "strip", lambda v: ser_strip(ar, v))
    S(ar, cv, "stride", ar.i32)
    S(ar, cv, "num_vertices", ar.i32)
    if cv["num_vertices"] > 0:
        S(ar, cv, "colors", lambda v: ar.bulk_array("4B", v))
    return cv


def ser_cloth_vb(ar, c=None):
    c = c if c is not None else Obj()
    S(ar, c, "strip", lambda v: ser_strip(ar, v))
    S(ar, c, "data", lambda v: ar.bulk_raw(v))
    S(ar, c, "index_mapping", lambda v: ar.packed_array("II", v))
    return c


def ser_lod(ar, lod=None, mesh=None):
    """FSkeletalMeshLODRenderData::Serialize (cooked, inlined)."""
    lod = lod if lod is not None else Obj()
    S(ar, lod, "strip", lambda v: ser_strip(ar, v))
    S(ar, lod, "cooked_out", ar.bool32)
    S(ar, lod, "inlined", ar.bool32)
    S(ar, lod, "required_bones", lambda v: ar.packed_array("h", v))
    if lod["strip"][0] & 2 or lod["cooked_out"]:
        return lod
    S(ar, lod, "sections", lambda v: ar.array(lambda x: ser_section(ar, x), v))
    S(ar, lod, "active_bones", lambda v: ar.packed_array("h", v))
    S(ar, lod, "buffers_size", ar.u32)
    if not lod["inlined"]:
        raise NotImplementedError("streamed (.ubulk) LOD")
    # SerializeStreamedData
    S(ar, lod, "stream_strip", lambda v: ser_strip(ar, v))
    S(ar, lod, "indices", lambda v: ser_index_container(ar, v))
    S(ar, lod, "positions", lambda v: ser_position_vb(ar, v))
    S(ar, lod, "static_vb", lambda v: ser_static_vb(ar, v))
    S(ar, lod, "skin_weights", lambda v: ser_skin_weights(ar, v))
    if mesh["has_vertex_colors"]:
        S(ar, lod, "colors", lambda v: ser_color_vb(ar, v))
    if not lod["stream_strip"][1] & 1:                       # CDSF_AdjacencyData
        S(ar, lod, "adjacency", lambda v: ser_index_container(ar, v))
    if any(s["cloth_mapping"] for s in lod["sections"]):
        S(ar, lod, "cloth_vb", lambda v: ser_cloth_vb(ar, v))
    S(ar, lod, "skin_weight_profiles", lambda v: ar.array(lambda x: _no_profiles(x), v))
    S(ar, lod, "ray_tracing_data", lambda v: ar.packed_array("B", v))
    return lod


def _no_profiles(x):
    raise NotImplementedError("skin weight profiles")


def ser_mesh(ar, m):
    """Native part of USkeletalMesh::Serialize after the tagged properties."""
    S(ar, m, "has_guid", ar.bool32)                          # UObject: lazy object GUID flag
    if m["has_guid"]:
        S(ar, m, "guid", lambda v: ar.raw(16, v))
    S(ar, m, "strip", lambda v: ser_strip(ar, v))
    S(ar, m, "bounds", lambda v: ser_bounds(ar, v))
    S(ar, m, "materials", lambda v: ar.array(lambda x: ser_skel_material(ar, x), v))
    S(ar, m, "refskel", lambda v: ser_refskel(ar, v))
    S(ar, m, "cooked", ar.bool32)
    # FSkeletalMeshRenderData::Serialize (0x144022EE0): LODs, then a TRS bool32 (stored at +0x16; USkeletalMesh
    # seeds it from bit 2 of its flag byte +0x168 before loading), then the stock NumInlinedLODs/NumNonOptionalLODs.
    S(ar, m, "lods", lambda v: ar.array(lambda x: ser_lod(ar, x, m), v))
    S(ar, m, "b4b_render_flag", ar.bool32)
    S(ar, m, "num_inlined_lods", ar.u8)           # retail: == LOD count (CurrentFirstLODIdx = Num - this)
    S(ar, m, "num_non_optional_lods", ar.u8)      # retail: 0
    # back in USkeletalMesh::Serialize (0x144006BD0): stock TArray<UObject*> DummyObjs, always empty
    S(ar, m, "dummy_objs", lambda v: ar.packed_array("i", v))
    return m


class SkeletalMesh:
    def __init__(self, uasset):
        self.pkg = upkg.Package(uasset)
        self.export = next(e for e in self.pkg.exports if self.pkg.class_name(e) == "SkeletalMesh")
        self.data = bytes(self.pkg.export_data(self.export))
        r = upkg.R(self.data)
        self.props = {}
        self.pkg.skip_tagged(r, self.props)
        self.props_end = r.p
        ar = Ar(self.pkg, self.data)
        ar.p = self.props_end
        self.m = Obj(has_vertex_colors=self._bool_prop("bHasVertexColors"))
        ser_mesh(ar, self.m)
        self.rest = self.data[ar.p:]                         # anything after (e.g. per-poly BodySetup)

    def set_tags(self, tag_bytes):
        """Replace the export's tagged properties (uprops.write output, "None" included)."""
        self.data = bytes(tag_bytes) + self.data[self.props_end:]
        r = upkg.R(self.data)
        self.props = {}
        self.pkg.skip_tagged(r, self.props)
        self.props_end = r.p

    def _bool_prop(self, name):
        p = self.props.get(name)
        return bool(p and p["value"])

    def name(self, fn):
        i, n = fn[0], fn[1]
        s = self.pkg.names[i]
        return s if n == 0 else f"{s}_{n - 1}"

    def serialize(self):
        ar = Ar(self.pkg)
        ser_mesh(ar, self.m)
        return self.data[:self.props_end] + bytes(ar.b) + self.rest

    def save(self, uasset_out):
        self.pkg.set_export_data(self.export, self.serialize())
        self.pkg.save(uasset_out)


# ---------------------------------------------------------------------------------------------------------------

def decode_weights(w):
    """Per-vertex [(bone_map_index, weight 0..255)] for the fixed-influence layout."""
    es, n, data = w["data"]
    k = w["max_influences"]
    ib = 2 if w["use_16bit_index"] else 1
    stride = k * (ib + 1)
    out = []
    for v in range(w["num_vertices"]):
        o = v * stride
        idx = struct.unpack_from("<%d%s" % (k, "H" if ib == 2 else "B"), data, o)
        wt = data[o + k * ib:o + stride]
        out.append(list(zip(idx, wt)))
    return out


def info(path):
    s = SkeletalMesh(path)
    m = s.m
    print(os.path.basename(path), "props end", s.props_end, "export size", len(s.data), "rest", len(s.rest))
    print("  bounds", ["%.1f" % x for x in m["bounds"]], "strip", m["strip"])
    for i, mat in enumerate(m["materials"]):
        print("  mat", i, s.pkg.obj(mat["material"]), s.name(mat["slot_name"]), mat["uv_channel"][:2])
    print("  bones", len(m["refskel"]["bones"]), "cooked", m["cooked"], "lods", len(m["lods"]),
          "flag", m["b4b_render_flag"], "inlined/nonopt", m["num_inlined_lods"], m["num_non_optional_lods"],
          "dummy", len(m["dummy_objs"]))
    for li, lod in enumerate(m["lods"]):
        if "sections" not in lod:
            print("  lod", li, "stripped/cooked out"); continue
        sv, w = lod["static_vb"], lod["skin_weights"]
        print(f"  lod {li}: strip {lod['strip']}/{lod['stream_strip']} verts {lod['positions']['num_vertices']} "
              f"idx {len(lod['indices']['indices'])}x{lod['indices']['data_size']} uv {sv['num_texcoords']} "
              f"fulluv {sv['full_uvs']} hpt {sv['high_prec_tangents']} infl {w['max_influences']} var {w['variable_bones']} "
              f"16bit {w['use_16bit_index']} reqbones {len(lod['required_bones'])} active {len(lod['active_bones'])} "
              f"bufsize {lod['buffers_size']} rt {len(lod['ray_tracing_data'])} adj {'adjacency' in lod} "
              f"cloth {'cloth_vb' in lod} colors {'colors' in lod}")
        for si, sec in enumerate(lod["sections"]):
            print(f"    sec {si}: mat {sec['material_index']} base {sec['base_index']} tris {sec['num_triangles']} "
                  f"vbase {sec['base_vertex_index']} nv {sec['num_vertices']} maxinfl {sec['max_bone_influences']} "
                  f"bonemap {len(sec['bone_map'])} cloth {len(sec['cloth_mapping'])} strip {sec['strip']} "
                  f"dup {len(sec.get('dup_vert_data') or [])} shadow {sec['cast_shadow']} disabled {sec['disabled']}")


def roundtrip(paths):
    bad = 0
    for p in paths:
        try:
            s = SkeletalMesh(p)
            out = s.serialize()
            ok = out == s.data
        except Exception as e:
            ok, out = False, repr(e)
        if not ok: bad += 1
        print("OK  " if ok else "FAIL", p, "" if ok else (out if isinstance(out, str) else f"{len(out)} vs {len(s.data)}"))
    print(f"{len(paths) - bad}/{len(paths)} byte-identical")
    return bad == 0


def dump_obj(path, out, lod_i=0):
    s = SkeletalMesh(path)
    lod = s.m["lods"][lod_i]
    with open(out, "w") as f:
        for x, y, z in lod["positions"]["positions"]:
            f.write(f"v {x} {z} {y}\n")
        for uv in lod["static_vb"]["uvs"][::lod["static_vb"]["num_texcoords"]]:
            f.write(f"vt {uv[0]} {1 - uv[1]}\n")
        idx = lod["indices"]["indices"]
        for sec in lod["sections"]:
            f.write(f"g section{sec['material_index']}\n")
            b = sec["base_index"]
            for t in range(sec["num_triangles"]):
                a, c, d = (idx[b + 3 * t + k][0] + 1 for k in range(3))
                f.write(f"f {a}/{a} {c}/{c} {d}/{d}\n")


# ---- edits -----------------------------------------------------------------------------------------------------

def unpack_normal(b4):
    """FPackedNormal (4.20+): signed int8 / 127."""
    return tuple((x - 256 if x > 127 else x) / 127.0 for x in b4)


def pack_normal(v):
    return tuple(max(-127, min(127, round(x * 127.0))) & 0xFF for x in v)


def lod_vertex_sections(lod):
    """Section index per vertex."""
    out = [0] * lod["positions"]["num_vertices"]
    for si, sec in enumerate(lod["sections"]):
        for v in range(sec["base_vertex_index"], sec["base_vertex_index"] + sec["num_vertices"]):
            out[v] = si
    return out


def edit_inflate(mesh, cm, lods=None, sections=None):
    """Push vertices along their normal (TangentZ) by `cm`."""
    for li, lod in enumerate(mesh["lods"]):
        if lods is not None and li not in lods: continue
        vs = lod_vertex_sections(lod)
        P, T = lod["positions"]["positions"], lod["static_vb"]["tangents"]
        for v in range(len(P)):
            if sections is not None and vs[v] not in sections: continue
            n = unpack_normal(T[v][4:8])
            P[v] = (P[v][0] + n[0] * cm, P[v][1] + n[1] * cm, P[v][2] + n[2] * cm)


def edit_scale_section(mesh, li, si, f):
    """Scale one section of one LOD by f around its centroid."""
    lod = mesh["lods"][li]; sec = lod["sections"][si]; P = lod["positions"]["positions"]
    r = range(sec["base_vertex_index"], sec["base_vertex_index"] + sec["num_vertices"])
    c = [sum(P[v][k] for v in r) / len(r) for k in range(3)]
    for v in r:
        P[v] = tuple(c[k] + (P[v][k] - c[k]) * f for k in range(3))


def cmd_edit(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="skm.py edit")
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--inflate", type=float, help="cm along the normal, every LOD/section")
    ap.add_argument("--scale-section", action="append", default=[], help="LOD:SECTION:FACTOR (LOD * = all)")
    ap.add_argument("--material", action="append", default=[], help="LOD:SECTION:MATERIAL_INDEX (LOD * = all)")
    a = ap.parse_args(argv)
    s = SkeletalMesh(a.src)
    if a.inflate: edit_inflate(s.m, a.inflate)
    for spec in a.scale_section:
        l, si, f = spec.split(":")
        for li in (range(len(s.m["lods"])) if l == "*" else [int(l)]):
            if int(si) < len(s.m["lods"][li]["sections"]): edit_scale_section(s.m, li, int(si), float(f))
    for spec in a.material:
        l, si, mi = spec.split(":")
        for li in (range(len(s.m["lods"])) if l == "*" else [int(l)]):
            if int(si) < len(s.m["lods"][li]["sections"]): s.m["lods"][li]["sections"][int(si)]["material_index"] = int(mi)
    os.makedirs(os.path.dirname(os.path.abspath(a.dst)), exist_ok=True)
    s.save(a.dst)
    SkeletalMesh(a.dst)                                  # re-parse the result as a sanity check
    print("wrote", a.dst)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "info":
        for p in sys.argv[2:]: info(p)
    elif cmd == "roundtrip":
        sys.exit(0 if roundtrip(sys.argv[2:]) else 1)
    elif cmd == "edit":
        cmd_edit(sys.argv[2:])
    elif cmd == "dump-obj":
        dump_obj(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 0)
