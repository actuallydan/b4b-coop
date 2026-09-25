"""Minimal reader/writer for B4B cooked packages (UE 4.25 legacy -7, unversioned, split .uasset/.uexp).

Only what the mesh tools need: summary, name map, imports, exports, tagged-property skipping, and rewriting an
export's serialized bytes (fixes SerialSize/SerialOffset of later exports and BulkDataStartOffset).
"""
import os, struct


class R:
    """Little-endian cursor over bytes."""
    def __init__(self, b, pos=0):
        self.b, self.p = b, pos

    def u8(self): v = self.b[self.p]; self.p += 1; return v
    def i32(self): v = struct.unpack_from("<i", self.b, self.p)[0]; self.p += 4; return v
    def u32(self): v = struct.unpack_from("<I", self.b, self.p)[0]; self.p += 4; return v
    def i64(self): v = struct.unpack_from("<q", self.b, self.p)[0]; self.p += 8; return v
    def u16(self): v = struct.unpack_from("<H", self.b, self.p)[0]; self.p += 2; return v
    def i16(self): v = struct.unpack_from("<h", self.b, self.p)[0]; self.p += 2; return v
    def f32(self): v = struct.unpack_from("<f", self.b, self.p)[0]; self.p += 4; return v
    def raw(self, n): v = bytes(self.b[self.p:self.p + n]); self.p += n; return v
    def unpack(self, fmt):
        v = struct.unpack_from("<" + fmt, self.b, self.p); self.p += struct.calcsize("<" + fmt); return v

    def fstr(self):
        n = self.i32()
        if n == 0: return ""
        if n > 0: s = self.b[self.p:self.p + n - 1].decode("latin-1"); self.p += n; return s
        s = self.b[self.p:self.p + (-n - 1) * 2].decode("utf-16le"); self.p += -n * 2; return s


class Package:
    def __init__(self, uasset_path):
        self.path = uasset_path
        self.head = bytearray(open(uasset_path, "rb").read())
        self.uexp = bytearray(open(uasset_path[:-7] + ".uexp", "rb").read())
        r = R(self.head)
        assert r.u32() == 0x9E2A83C1
        assert r.i32() == -7
        r.i32(); r.i32(); r.i32()                      # LegacyUE3, UE4, Licensee (0 = unversioned)
        ncv = r.i32(); r.p += ncv * 20
        self.total_header_size_off = r.p; self.total_header_size = r.i32()
        r.fstr()                                        # FolderName
        self.package_flags = r.u32()
        self.name_count, self.name_offset = r.i32(), r.i32()
        if not self.package_flags & 0x80000000: r.fstr()  # LocalizationId
        r.i32(); r.i32()                                # GatherableTextData
        self.export_count, self.export_offset = r.i32(), r.i32()
        self.import_count, self.import_offset = r.i32(), r.i32()
        r.i32(); r.i32(); r.i32(); r.i32(); r.i32()     # Depends, SoftPackageRefs x2, SearchableNames, Thumbnail
        r.p += 16                                       # Guid
        ng = r.i32(); r.p += ng * 8                     # Generations
        for _ in range(2):                              # SavedBy/CompatibleWith engine version
            r.p += 10; r.fstr()
        r.u32()                                         # CompressionFlags
        nc = r.i32(); assert nc == 0
        r.u32()                                         # PackageSource
        na = r.i32()
        for _ in range(na): r.fstr()
        r.i32()                                         # AssetRegistryDataOffset
        self.bulk_start_off = r.p; self.bulk_start = r.i64()
        # names
        r.p = self.name_offset
        self.names = []
        for _ in range(self.name_count):
            self.names.append(r.fstr()); r.p += 4
        r.p = self.import_offset
        self.imports = []
        for _ in range(self.import_count):
            cp, cn = self._fname(r), self._fname(r)
            outer = r.i32(); name = self._fname(r)
            self.imports.append((cp, cn, outer, name))
        r.p = self.export_offset
        self.exports = []
        for _ in range(self.export_count):
            e = {"pos": r.p}
            e["class"], e["super"], e["template"], e["outer"] = r.i32(), r.i32(), r.i32(), r.i32()
            e["name"] = self._fname(r)
            e["flags"] = r.u32()
            e["size_pos"] = r.p; e["size"] = r.i64()
            e["off_pos"] = r.p; e["offset"] = r.i64()
            r.p += 104 - (r.p - e["pos"])
            self.exports.append(e)

    def _fname(self, r):
        i, n = r.i32(), r.i32()
        s = self.names[i]
        return s if n == 0 else f"{s}_{n - 1}"

    def fname(self, r):
        return self._fname(r)

    def obj(self, idx):
        if idx == 0: return None
        if idx < 0: return self.imports[-idx - 1][3]
        return self.exports[idx - 1]["name"]

    def obj_path(self, idx):
        """Full object path of an import (/Game/.../Pkg.Obj) or an export (its own name)."""
        if idx == 0: return None
        if idx > 0: return self.exports[idx - 1]["name"]
        cp, cn, outer, name = self.imports[-idx - 1]
        if outer == 0: return name
        parent = self.obj_path(outer)
        return f"{parent}.{name}" if outer < 0 and self.imports[-outer - 1][1] == "Package" else f"{parent}:{name}"

    def class_name(self, e):
        return self.obj(e["class"])

    def export_data(self, e):
        o = e["offset"] - self.total_header_size
        return self.uexp[o:o + e["size"]]

    def set_export_data(self, e, data):
        o = e["offset"] - self.total_header_size
        delta = len(data) - e["size"]
        self.uexp[o:o + e["size"]] = data
        e["size"] = len(data)
        struct.pack_into("<q", self.head, e["size_pos"], e["size"])
        for x in self.exports:
            if x["offset"] > e["offset"]:
                x["offset"] += delta
                struct.pack_into("<q", self.head, x["off_pos"], x["offset"])
        self.bulk_start += delta
        struct.pack_into("<q", self.head, self.bulk_start_off, self.bulk_start)

    def save(self, uasset_path):
        open(uasset_path, "wb").write(self.head)
        open(uasset_path[:-7] + ".uexp", "wb").write(self.uexp)

    # ---- tagged properties -------------------------------------------------------------------------------
    def skip_tagged(self, r, collect=None):
        """Walk a tagged property list up to 'None'. collect: dict name -> (type, value_offset, size)."""
        while True:
            name = self.fname(r)
            if name == "None": return
            typ = self.fname(r)
            size = r.i32(); r.i32()                     # Size, ArrayIndex
            info = {"type": typ}
            if typ == "StructProperty":
                info["struct"] = self.fname(r); r.p += 16
            elif typ == "BoolProperty":
                info["value"] = r.u8()
            elif typ in ("ByteProperty", "EnumProperty"):
                info["enum"] = self.fname(r)
            elif typ in ("ArrayProperty", "SetProperty"):
                info["inner"] = self.fname(r)
            elif typ == "MapProperty":
                info["key"] = self.fname(r); info["val"] = self.fname(r)
            if r.u8(): r.p += 16                        # property guid
            info["off"], info["size"] = r.p, size
            r.p += size
            if collect is not None: collect[name] = info


def read_material_instance(path):
    """MaterialInstanceConstant: (parent object path, {texture parameter name: texture object path},
    {scalar name: value}, {vector name: (r,g,b,a)}). Only the tagged parameter arrays and Parent are read."""
    pkg = Package(path)
    e = next(x for x in pkg.exports if pkg.class_name(x) == "MaterialInstanceConstant")
    data = pkg.export_data(e)
    r = R(data); props = {}
    pkg.skip_tagged(r, props)
    parent = None
    if "Parent" in props:
        parent = pkg.obj_path(struct.unpack_from("<i", data, props["Parent"]["off"])[0])
    out = {"TextureParameterValues": {}, "ScalarParameterValues": {}, "VectorParameterValues": {}}
    for key in out:
        p = props.get(key)
        if not p: continue
        rr = R(data, p["off"])
        n = rr.i32()
        pkg.fname(rr); pkg.fname(rr); rr.i32(); rr.i32(); pkg.fname(rr); rr.p += 16
        if rr.u8(): rr.p += 16
        for _ in range(n):
            el = {}
            pkg.skip_tagged(rr, el)
            ri = R(data, el["ParameterInfo"]["off"]); info = {}
            pkg.skip_tagged(ri, info)
            name = pkg.fname(R(data, info["Name"]["off"]))
            vo = el["ParameterValue"]["off"]
            if key == "TextureParameterValues":
                out[key][name] = pkg.obj_path(struct.unpack_from("<i", data, vo)[0])
            elif key == "ScalarParameterValues":
                out[key][name] = struct.unpack_from("<f", data, vo)[0]
            else:
                out[key][name] = struct.unpack_from("<4f", data, vo)
    return parent, out["TextureParameterValues"], out["ScalarParameterValues"], out["VectorParameterValues"]


def game_path_to_file(obj_path, src):
    """/Game/X/Y.Y -> <src>/Gobi/Content/X/Y.uasset"""
    pkg = obj_path.split(".")[0].split(":")[0]
    if pkg.startswith("/Game/"):
        return os.path.join(src, "Gobi", "Content", *pkg[len("/Game/"):].split("/")) + ".uasset"
    return None


def file_to_game_path(path):
    p = os.path.abspath(path).replace(os.sep, "/")
    i = p.find("/Gobi/Content/")
    if i < 0: return None
    return "/Game/" + p[i + len("/Gobi/Content/"):].rsplit(".", 1)[0]


# ---- tagged property dump (debugging / inspection) ------------------------------------------------------------

NATIVE_STRUCTS = {"Vector": "3f", "Vector2D": "2f", "Vector4": "4f", "Rotator": "3f", "Quat": "4f", "Guid": "4I",
                  "LinearColor": "4f", "Color": "4B", "IntPoint": "2i", "Box": "6fB", "BoxSphereBounds": "7f",
                  "PerPlatformFloat": "If", "PerPlatformInt": "Ii", "PerPlatformBool": "II",
                  "FrameNumber": "i", "SoftObjectPath": None}


def _dump_value(pkg, r, typ, info, size, indent, out):
    pad = "  " * indent
    end = r.p + size
    if typ in ("IntProperty",): out.append(f"{r.i32()}")
    elif typ == "FloatProperty": out.append(f"{r.f32():g}")
    elif typ in ("ObjectProperty", "SoftObjectProperty") and size == 4: out.append(f"obj {pkg.obj(r.i32())}")
    elif typ == "NameProperty": out.append(pkg.fname(r))
    elif typ == "StrProperty": out.append(repr(r.fstr()))
    elif typ == "BoolProperty": out.append(str(info.get("value")))
    elif typ == "ByteProperty" and size == 1: out.append(str(r.u8()))
    elif typ in ("ByteProperty", "EnumProperty"): out.append(pkg.fname(r))
    elif typ == "StructProperty":
        st = info.get("struct")
        f = NATIVE_STRUCTS.get(st, "?")
        if f and f != "?" and struct.calcsize("<" + f) == size:
            out.append(f"{st}{r.unpack(f)}")
        else:
            out.append(f"{st} {{")
            try:
                dump_tagged(pkg, r, indent + 1, out)
            except (IndexError, struct.error, UnicodeDecodeError):
                out.append(f"{pad}  (native struct, {size} bytes)")
            out.append(pad + "}")
    elif typ == "ArrayProperty":
        n = r.i32()
        inner = info.get("inner")
        if inner == "StructProperty":
            iname = pkg.fname(r); pkg.fname(r); r.i32(); r.i32(); st = pkg.fname(r); r.p += 16
            if r.u8(): r.p += 16
            out.append(f"[{n} x {st}]")
            for k in range(n):
                f = NATIVE_STRUCTS.get(st, "?")
                if f and f != "?":
                    out.append(f"{pad}  [{k}] {st}{r.unpack(f)}")
                else:
                    out.append(f"{pad}  [{k}] {{"); dump_tagged(pkg, r, indent + 2, out); out.append(pad + "  }")
        elif inner in ("IntProperty",): out.append(str([r.i32() for _ in range(n)]))
        elif inner == "FloatProperty": out.append(str([round(r.f32(), 4) for _ in range(n)]))
        elif inner == "ObjectProperty": out.append(str([pkg.obj(r.i32()) for _ in range(n)]))
        elif inner == "NameProperty": out.append(str([pkg.fname(r) for _ in range(n)]))
        elif inner == "BoolProperty": out.append(str([r.u8() for _ in range(n)]))
        else: out.append(f"[{n} x {inner}] ({size} bytes)")
    else:
        out.append(f"({typ}, {size} bytes)")
    r.p = end


def dump_tagged(pkg, r, indent=0, out=None):
    """Readable dump of a tagged property list (recursing into tagged structs and arrays of structs)."""
    out = [] if out is None else out
    pad = "  " * indent
    while True:
        name = pkg.fname(r)
        if name == "None": return out
        typ = pkg.fname(r)
        size = r.i32(); aidx = r.i32()
        info = {"type": typ}
        if typ == "StructProperty": info["struct"] = pkg.fname(r); r.p += 16
        elif typ == "BoolProperty": info["value"] = r.u8()
        elif typ in ("ByteProperty", "EnumProperty"): info["enum"] = pkg.fname(r)
        elif typ in ("ArrayProperty", "SetProperty"): info["inner"] = pkg.fname(r)
        elif typ == "MapProperty": info["key"] = pkg.fname(r); info["val"] = pkg.fname(r)
        if r.u8(): r.p += 16
        line = [f"{pad}{name}{'[%d]' % aidx if aidx else ''} ({typ}) = "]
        sub = []
        _dump_value(pkg, r, typ, info, size, indent, sub)
        out.append(line[0] + (sub[0] if sub else ""))
        out.extend(sub[1:])


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "props":
        p = Package(sys.argv[2])
        for e in p.exports:
            if len(sys.argv) > 3 and e["name"] != sys.argv[3]: continue
            print(f"== {e['name']} ({p.class_name(e)}) size {e['size']}")
            rr = R(p.export_data(e))
            try:
                print("\n".join(dump_tagged(p, rr)))
            except Exception as ex:
                print("  (dump stopped:", ex, ")")
            print(f"  -- native data after tags: {e['size'] - rr.p} bytes")
    else:
        print("usage: upkg.py props <x.uasset> [export name]")
