"""Tagged UObject properties of B4B cooked packages (UE 4.25, versioned tags): parse into a tree, edit, write back.

Byte-identical round trip for what the modkit touches (clothing assets: nested structs, arrays of structs, maps).
Anything it can't decode stays raw bytes and is written back unchanged.

  tree = parse(pkg, data, pos)          -> (list of Prop, end position after the "None" terminator)
  data = write(pkg, tree)               -> bytes (tags + "None")
  find(tree, "LodData").value ...       arrays: Prop.value = list; struct elements: list of Prop; native structs: bytes

Names written must already be in the package's name map (pkg.names); `name_ref` looks them up.
"""
import struct

NATIVE = {"Vector": 12, "Vector2D": 8, "Vector4": 16, "Rotator": 12, "Quat": 16, "Guid": 16, "LinearColor": 16,
          "Color": 4, "IntPoint": 8, "Box": 25, "BoxSphereBounds": 28, "PerPlatformFloat": 8, "PerPlatformInt": 8,
          "FrameNumber": 4, "SoftObjectPath": None}
PRIM = {"IntProperty": "<i", "UInt32Property": "<I", "FloatProperty": "<f", "Int64Property": "<q",
        "UInt64Property": "<Q", "Int16Property": "<h", "UInt16Property": "<H", "Int8Property": "<b",
        "DoubleProperty": "<d", "ObjectProperty": "<i", "SoftObjectProperty": None}


class Prop:
    __slots__ = ("name", "type", "index", "extra", "guid", "value", "fn")

    def __init__(self, name, type, index=0, extra=None, guid=None, value=None, fn=None):
        self.name, self.type, self.index, self.extra, self.guid, self.value = name, type, index, extra, guid, value
        self.fn = fn                                        # raw FName of the name (None: looked up on write)

    def __repr__(self):
        return f"Prop({self.name}, {self.type}, {self.extra})"


class Tagged(list):
    """A tagged struct's property list plus the native bytes some structs serialize after their tags (`tail`)."""
    tail = b""


def _cloth_lod_tail(r):
    """FClothLODDataCommon::Serialize after the tags: TransitionUpSkinData, TransitionDownSkinData
    (TArray<FMeshToMeshVertData>, 64 bytes each)."""
    s = r.p
    for _ in range(2):
        n = r.i32(); r.p += 64 * n
    return bytes(r.b[s:r.p])


NATIVE_TAIL = {"ClothLODDataCommon": _cloth_lod_tail}


class _R:
    def __init__(self, pkg, b, p):
        self.pkg, self.b, self.p = pkg, b, p

    def un(self, f):
        v = struct.unpack_from(f, self.b, self.p); self.p += struct.calcsize(f); return v

    def i32(self): return self.un("<i")[0]
    def u8(self): return self.un("<B")[0]
    def fname(self): return self.un("<ii")                  # raw (index, number)

    def sname(self, fn):
        s = self.pkg.names[fn[0]]
        return s if fn[1] == 0 else f"{s}_{fn[1] - 1}"


def _tag(r):
    """One property tag; None at the "None" terminator."""
    fn = r.fname()
    if r.sname(fn) == "None": return None
    typ = r.sname(r.fname())
    size, index = r.i32(), r.i32()
    extra = None
    if typ == "StructProperty": extra = (r.fname(), r.b[r.p:r.p + 16]); r.p += 16
    elif typ == "BoolProperty": extra = r.u8()
    elif typ in ("ByteProperty", "EnumProperty"): extra = r.fname()
    elif typ in ("ArrayProperty", "SetProperty"): extra = r.fname()
    elif typ == "MapProperty": extra = (r.fname(), r.fname())
    guid = r.b[r.p + 1:r.p + 17] if r.b[r.p] else None
    r.p += 17 if guid is not None else 1
    return fn, typ, size, index, extra, guid


def parse(pkg, data, pos=0):
    r = _R(pkg, data, pos)
    out = []
    while True:
        t = _tag(r)
        if t is None: return out, r.p
        fn, typ, size, index, extra, guid = t
        start = r.p
        try:
            v = _value(r, typ, extra, size)
            if r.p != start + size: raise ValueError("size mismatch")
        except Exception:
            v = ("raw", bytes(data[start:start + size]))
        r.p = start + size
        out.append(Prop(r.sname(fn), typ, index, extra, guid, v, fn))


def _struct_name(r, extra):
    return r.sname(extra[0])


def _value(r, typ, extra, size):
    if typ in PRIM and PRIM[typ]: return r.un(PRIM[typ])[0]
    if typ == "BoolProperty": return None
    if typ == "NameProperty": return r.fname()
    if typ == "StrProperty":
        n = r.i32(); s = bytes(r.b[r.p:r.p + (n if n >= 0 else -2 * n)]); r.p += len(s); return (n, s)
    if typ == "ByteProperty": return r.u8() if size == 1 else r.fname()
    if typ == "EnumProperty": return r.fname()
    if typ == "StructProperty":
        st = _struct_name(r, extra)
        if st in NATIVE and NATIVE[st] == size: v = bytes(r.b[r.p:r.p + size]); r.p += size; return v
        v, r.p = parse(r.pkg, r.b, r.p)
        return v
    if typ == "ArrayProperty":
        inner = r.sname(extra)
        n = r.i32()
        if inner == "StructProperty":
            it = _tag(r)                                  # the inner tag: name, StructProperty, size, index, struct
            st = r.sname(it[4][0])
            els = []
            for _ in range(n):
                if st in NATIVE and NATIVE[st]:
                    els.append(bytes(r.b[r.p:r.p + NATIVE[st]])); r.p += NATIVE[st]
                else:
                    v, r.p = parse(r.pkg, r.b, r.p)
                    v = Tagged(v)
                    if st in NATIVE_TAIL: v.tail = NATIVE_TAIL[st](r)
                    els.append(v)
            return {"inner_tag": it, "items": els}
        if inner in PRIM and PRIM[inner]: return [r.un(PRIM[inner])[0] for _ in range(n)]
        if inner == "NameProperty": return [r.fname() for _ in range(n)]
        if inner == "BoolProperty": return [r.u8() for _ in range(n)]
        if inner in ("ByteProperty",): return [r.u8() for _ in range(n)]
        raise ValueError(inner)
    if typ == "MapProperty":
        kt, vt = r.sname(extra[0]), r.sname(extra[1])
        nrem = r.i32()
        if nrem: raise ValueError("map removals")
        n = r.i32()
        items = []
        for _ in range(n):
            k = _map_elem(r, kt); v = _map_elem(r, vt); items.append((k, v))
        return {"items": items}
    raise ValueError(typ)


def _map_elem(r, t):
    if t in PRIM and PRIM[t]: return r.un(PRIM[t])[0]
    if t == "NameProperty": return r.fname()
    if t == "StructProperty":
        v, r.p = parse(r.pkg, r.b, r.p); return v
    raise ValueError(t)


# ---- writing ---------------------------------------------------------------------------------------------------------

class _W:
    def __init__(self, pkg):
        self.pkg, self.b = pkg, bytearray()

    def pk(self, f, *v): self.b += struct.pack(f, *v)
    def fname(self, fn): self.pk("<ii", *fn)


def name_ref(pkg, s):
    """FName (index, number) of an existing name-map entry; 'foo_3' also resolves as ('foo', 4)."""
    low = [n.lower() for n in pkg.names]
    if s.lower() in low: return (low.index(s.lower()), 0)
    base, _, num = s.rpartition("_")
    if base and num.isdigit() and base.lower() in low: return (low.index(base.lower()), int(num) + 1)
    raise KeyError(f"name {s!r} is not in {pkg.path}'s name map")


def write(pkg, tree):
    w = _W(pkg)
    _write_list(w, tree)
    return bytes(w.b)


def _write_list(w, tree):
    for p in tree:
        body = _W(w.pkg)
        _write_value(body, p.type, p.extra, p.value)
        w.fname(p.fn or name_ref(w.pkg, p.name))
        w.fname(name_ref(w.pkg, p.type))
        w.pk("<ii", len(body.b), p.index)
        if p.type == "StructProperty": w.fname(p.extra[0]); w.b += p.extra[1]
        elif p.type == "BoolProperty": w.pk("<B", p.extra)
        elif p.type in ("ByteProperty", "EnumProperty", "ArrayProperty", "SetProperty"): w.fname(p.extra)
        elif p.type == "MapProperty": w.fname(p.extra[0]); w.fname(p.extra[1])
        if p.guid is not None: w.pk("<B", 1); w.b += p.guid
        else: w.pk("<B", 0)
        w.b += body.b
    w.fname(name_ref(w.pkg, "None"))


def _write_value(w, typ, extra, v):
    if isinstance(v, tuple) and len(v) == 2 and v[0] == "raw": w.b += v[1]; return
    if typ in PRIM and PRIM[typ]: w.pk(PRIM[typ], v); return
    if typ == "BoolProperty": return
    if typ == "NameProperty": w.fname(v); return
    if typ == "StrProperty": w.pk("<i", v[0]); w.b += v[1]; return
    if typ == "ByteProperty":
        if isinstance(v, int): w.pk("<B", v)
        else: w.fname(v)
        return
    if typ == "EnumProperty": w.fname(v); return
    if typ == "StructProperty":
        if isinstance(v, (bytes, bytearray)): w.b += v
        else: _write_list(w, v)
        return
    if typ == "ArrayProperty":
        inner = w.pkg.names[extra[0]]
        if inner == "StructProperty":
            items = v["items"]
            w.pk("<i", len(items))
            fn, _, _, index, iextra, guid = v["inner_tag"]
            body = _W(w.pkg)
            for it in items:
                if isinstance(it, (bytes, bytearray)): body.b += it
                else:
                    _write_list(body, it)
                    body.b += getattr(it, "tail", b"")
            w.fname(fn); w.fname(name_ref(w.pkg, "StructProperty")); w.pk("<ii", len(body.b), index)
            w.fname(iextra[0]); w.b += iextra[1]
            if guid is not None: w.pk("<B", 1); w.b += guid
            else: w.pk("<B", 0)
            w.b += body.b
            return
        w.pk("<i", len(v))
        for x in v:
            if inner in PRIM and PRIM[inner]: w.pk(PRIM[inner], x)
            elif inner == "NameProperty": w.fname(x)
            else: w.pk("<B", x)
        return
    if typ == "MapProperty":
        kt, vt = w.pkg.names[extra[0][0]], w.pkg.names[extra[1][0]]
        w.pk("<ii", 0, len(v["items"]))
        for k, x in v["items"]:
            for t, e in ((kt, k), (vt, x)):
                if t in PRIM and PRIM[t]: w.pk(PRIM[t], e)
                elif t == "NameProperty": w.fname(e)
                else: _write_list(w, e)
        return
    raise ValueError(typ)


# ---- helpers ---------------------------------------------------------------------------------------------------------

def find(tree, name):
    """First property called `name` in a property list."""
    return next((p for p in tree if p.name == name), None)


def vec_bytes(v):
    return struct.pack("<3f", *v)


def bytes_vec(b):
    return struct.unpack("<3f", b)


# ---- copying a tree between packages -----------------------------------------------------------------------------

def remap(tree, src, dst, obj=None):
    """Deep copy of a property tree parsed from package `src`, every FName re-pointed into `dst`'s name map (added
    there when missing: dst must be a upkg.Package that can add names, saved with a rebuilt header). obj(i) maps an
    ObjectProperty value (src FPackageIndex) to dst's; default: None (0). Used to make new exports from a retail
    one (cloth.py: a clothing asset for an outfit that has none)."""
    obj = obj or (lambda i: 0)

    def fn(f):
        s = src.names[f[0]]
        return (dst.add_name(s), f[1])

    def name_of(f): return src.names[f[0]]

    for t in ("None", "StructProperty"): dst.add_name(t)

    def value(typ, extra, v):
        if isinstance(v, tuple) and len(v) == 2 and v[0] == "raw":
            raise ValueError("remap: undecoded property data can't be re-pointed")
        if typ == "ObjectProperty": return obj(v)
        if typ == "NameProperty": return fn(v)
        if typ in ("ByteProperty", "EnumProperty") and isinstance(v, tuple): return fn(v)
        if typ == "StructProperty":
            return v if isinstance(v, (bytes, bytearray)) else lst(v)
        if typ == "ArrayProperty":
            inner = name_of(extra)
            if inner == "StructProperty":
                f0, ityp, size, index, iextra, guid = v["inner_tag"]
                dst.add_name(ityp)
                items = [it if isinstance(it, (bytes, bytearray)) else _tagged(lst(it), it) for it in v["items"]]
                return {"inner_tag": (fn(f0), ityp, size, index, (fn(iextra[0]), iextra[1]), guid), "items": items}
            if inner == "ObjectProperty": return [obj(x) for x in v]
            if inner == "NameProperty": return [fn(x) for x in v]
            return list(v)
        if typ == "MapProperty":
            kt, vt = name_of(extra[0]), name_of(extra[1])

            def el(t, e):
                if t == "NameProperty": return fn(e)
                if t == "ObjectProperty": return obj(e)
                if t == "StructProperty": return lst(e)
                return e
            return {"items": [(el(kt, k), el(vt, x)) for k, x in v["items"]]}
        return v

    def _tagged(new, old):
        t = Tagged(new); t.tail = getattr(old, "tail", b""); return t

    def lst(props):
        out = []
        for p in props:
            dst.add_name(p.name); dst.add_name(p.type)
            if p.type == "StructProperty": extra = (fn(p.extra[0]), p.extra[1])
            elif p.type in ("ByteProperty", "EnumProperty", "ArrayProperty", "SetProperty"): extra = fn(p.extra)
            elif p.type == "MapProperty": extra = (fn(p.extra[0]), fn(p.extra[1]))
            else: extra = p.extra
            out.append(Prop(p.name, p.type, p.index, extra, p.guid, value(p.type, p.extra, p.value),
                            fn(p.fn) if p.fn else None))
        return out
    return lst(tree)
