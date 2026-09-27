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
        self.name_offset_pos = r.p
        self.name_count, self.name_offset = r.i32(), r.i32()
        if not self.package_flags & 0x80000000: r.fstr()  # LocalizationId
        self.gatherable = r.i32(); r.i32()              # GatherableTextData
        self.counts_pos = r.p
        self.export_count, self.export_offset = r.i32(), r.i32()
        self.import_count, self.import_offset = r.i32(), r.i32()
        self.depends_pos = r.p
        self.depends_off, self.soft_refs, _, self.searchable_off, self.thumb_off = [r.i32() for _ in range(5)]
        r.p += 16                                       # Guid
        self.gen_pos = r.p
        ng = r.i32(); r.p += ng * 8                     # Generations
        for _ in range(2):                              # SavedBy/CompatibleWith engine version
            r.p += 10; r.fstr()
        r.u32()                                         # CompressionFlags
        nc = r.i32(); assert nc == 0
        r.u32()                                         # PackageSource
        na = r.i32()
        for _ in range(na): r.fstr()
        self.asset_reg_off_pos = r.p; self.asset_reg_off = r.i32()
        self.bulk_start_off = r.p; self.bulk_start = r.i64()
        self.world_tile_off = r.i32()
        nchunks = r.i32(); r.p += 4 * nchunks           # ChunkIDs
        self.preload_pos = r.p; self.preload_count, self.preload_off = r.i32(), r.i32()
        self.summary_end = r.p
        # names
        r.p = self.name_offset
        self.names, self.name_hashes = [], []
        for _ in range(self.name_count):
            self.names.append(r.fstr()); self.name_hashes.append(r.raw(4))
        r.p = self.import_offset
        self.imports, self.import_raw = [], []
        for _ in range(self.import_count):
            raw = r.unpack("iiiiiii")
            r.p -= 28
            cp, cn = self._fname(r), self._fname(r)
            outer = r.i32(); name = self._fname(r)
            self.imports.append((cp, cn, outer, name))
            self.import_raw.append(raw)
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
            e["raw"] = bytearray(self.head[e["pos"]:e["pos"] + 104])
            self.exports.append(e)
        self.structural = False                         # names/imports/exports added: save() rebuilds the header

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
        if e["size_pos"] is not None:                   # (exports added since the header was written: none yet)
            struct.pack_into("<q", self.head, e["size_pos"], e["size"])
        for x in self.exports:
            if x["offset"] > e["offset"]:
                x["offset"] += delta
                if x["off_pos"] is not None: struct.pack_into("<q", self.head, x["off_pos"], x["offset"])
        self.bulk_start += delta
        struct.pack_into("<q", self.head, self.bulk_start_off, self.bulk_start)

    def save(self, uasset_path):
        if self.structural: self.rebuild_header()
        open(uasset_path, "wb").write(self.head)
        open(uasset_path[:-7] + ".uexp", "wb").write(self.uexp)

    # ---- structural edits: new names, imports and exports --------------------------------------------------
    # New entries are appended, so every existing FName and FPackageIndex in the export data stays valid; the new
    # exports' data goes after the last export (before any inline bulk data). save() then writes the whole header
    # again (name map with the engine's name hashes, import/export maps, depends map, preload dependencies, summary
    # offsets) and moves every serial offset by the header's size change. rebuild_header() of an unedited package
    # is byte-identical to the original (checked: `upkg.py rebuild-check`).
    PRELOAD_KEYS = ("sbs", "cbs", "sbc", "cbc")         # SerializationBeforeSerialization, CreateBeforeSerialization,
                                                        # SerializationBeforeCreate, CreateBeforeCreate

    def name_index(self, s):
        """Index of name s in the name map (exact spelling first, then case-insensitive like FName), or None."""
        if s in self.names: return self.names.index(s)
        low = s.lower()
        return next((i for i, n in enumerate(self.names) if n.lower() == low), None)

    def add_name(self, s):
        i = self.name_index(s)
        if i is not None: return i
        self.names.append(s); self.name_hashes.append(name_hashes(s))
        self.structural = True
        return len(self.names) - 1

    def fname_of(self, s, number=0):
        """Raw FName (index, number) for string s, added to the name map if needed."""
        return (self.add_name(s), number)

    def find_import(self, class_name, name, outer=None):
        for i, (cp, cn, o, n) in enumerate(self.imports):
            if cn == class_name and n.lower() == name.lower() and (outer is None or o == outer): return -i - 1
        return 0

    def add_import(self, class_package, class_name, outer, name):
        """FPackageIndex (negative) of an import, added if the package doesn't import it yet."""
        i = self.find_import(class_name, name, outer)
        if i: return i
        cp, cn, nm = self.fname_of(class_package), self.fname_of(class_name), self.fname_of(name)
        self.imports.append((class_package, class_name, outer, name))
        self.import_raw.append(cp + cn + (outer,) + nm)
        self.structural = True
        return -len(self.imports)

    def import_object(self, obj_path, class_package, class_name):
        """Import /Game/Dir/Pkg.Obj (a package + an object in it); returns the object's FPackageIndex."""
        pkg, obj = obj_path.split(".", 1) if "." in obj_path else (obj_path, obj_path.rsplit("/", 1)[-1])
        po = self.add_import("/Script/CoreUObject", "Package", 0, pkg)
        return self.add_import(class_package, class_name, po, obj)

    def import_class(self, script_package, class_name):
        """The import of a native class (/Script/Module.Class) and of its class default object; returns (class, CDO)."""
        po = self.add_import("/Script/CoreUObject", "Package", 0, script_package)
        c = self.add_import("/Script/CoreUObject", "Class", po, class_name)
        d = self.add_import(script_package, class_name, po, "Default__" + class_name)
        return c, d

    def preload_deps(self):
        """{export index: {sbs, cbs, sbc, cbc: [FPackageIndex]}} from the preload dependency array."""
        if not hasattr(self, "_deps"):
            r = R(self.head, self.preload_off)
            arr = [r.i32() for _ in range(self.preload_count)]
            self._deps = {}
            for i, e in enumerate(self.exports):
                v = struct.unpack_from("<5i", e["raw"], 84)
                k, d = v[0], {}
                for key, n in zip(self.PRELOAD_KEYS, v[1:]):
                    d[key] = arr[k:k + n] if n else []; k += n if n else 0
                self._deps[i + 1] = d
        return self._deps

    def add_export(self, cls, template, outer, name, data, flags=0x8, is_asset=False, **deps):
        """Append an export (class/template/outer as FPackageIndex, name as a string or raw FName) with its serialized
        data; deps: sbs/cbs/sbc/cbc lists (the event-driven loader's order: e.g. cbc=[outer], sbc=[class, CDO]).
        Returns its FPackageIndex (positive)."""
        self.preload_deps()
        fn = name if isinstance(name, tuple) else self.fname_of(name)
        at = self.bulk_start - self.total_header_size     # end of the export data in .uexp
        self.uexp[at:at] = data
        raw = bytearray(104)
        struct.pack_into("<4i2iI", raw, 0, cls, 0, template, outer, fn[0], fn[1], flags)
        struct.pack_into("<3i", raw, 44, 0, 0, 0)           # bForcedExport, bNotForClient, bNotForServer
        struct.pack_into("<I3i", raw, 72, 0, 1, int(is_asset), -1)   # PackageFlags, bNotAlwaysLoaded.. (1 as
                                                                      # retail), bIsAsset
        e = {"pos": None, "class": cls, "super": 0, "template": template, "outer": outer, "flags": flags,
             "name": self.names[fn[0]] if fn[1] == 0 else f"{self.names[fn[0]]}_{fn[1] - 1}",
             "size": len(data), "offset": self.bulk_start, "raw": raw, "size_pos": None, "off_pos": None}
        self.exports.append(e)
        self.bulk_start += len(data)
        self._deps[len(self.exports)] = {k: list(deps.get(k, [])) for k in self.PRELOAD_KEYS}
        self.structural = True
        return len(self.exports)

    def rebuild_header(self):
        if self.gatherable or self.soft_refs or self.searchable_off or self.thumb_off or self.world_tile_off:
            raise ValueError(f"{self.path}: package has gatherable text / soft package refs / searchable names / "
                             "thumbnails / world tile info: adding exports is not supported for it")
        deps = self.preload_deps()
        h = self.head
        summary = bytearray(h[:self.name_offset])
        names = bytearray()
        for s, hh in zip(self.names, self.name_hashes):
            names += fstring(s) + hh
        imports = b"".join(struct.pack("<7i", *x) for x in self.import_raw)
        arr, exps = [], []
        for i, e in enumerate(self.exports):
            raw = bytearray(e["raw"])
            struct.pack_into("<4i", raw, 0, e["class"], e["super"], e["template"], e["outer"])
            struct.pack_into("<I", raw, 24, e["flags"])
            d = deps[i + 1]
            first = len(arr) if any(d[k] for k in self.PRELOAD_KEYS) else -1
            for k in self.PRELOAD_KEYS: arr += d[k]
            struct.pack_into("<5i", raw, 84, first, *(len(d[k]) for k in self.PRELOAD_KEYS))
            exps.append(raw)
        old_depends = R(h, self.depends_off)
        depends = bytearray()
        for i in range(len(self.exports)):
            if i < self.export_count:
                n = old_depends.i32(); depends += struct.pack("<i", n) + old_depends.raw(4 * n)
            else:
                depends += struct.pack("<i", 0)
        areg_end = self.preload_off if self.preload_off else self.total_header_size
        asset_reg = bytes(h[self.asset_reg_off:areg_end])
        name_off = len(summary)
        imp_off = name_off + len(names)
        exp_off = imp_off + len(imports)
        dep_off = exp_off + 104 * len(exps)
        areg_off = dep_off + len(depends)
        pre_off = areg_off + len(asset_reg)
        total = pre_off + 4 * len(arr)
        delta = total - self.total_header_size
        for e, raw in zip(self.exports, exps):
            e["offset"] += delta
            struct.pack_into("<qq", raw, 28, e["size"], e["offset"])
        self.bulk_start += delta
        struct.pack_into("<i", summary, self.total_header_size_off, total)
        struct.pack_into("<ii", summary, self.name_offset_pos, len(self.names), name_off)
        struct.pack_into("<4i", summary, self.counts_pos, len(exps), exp_off, len(self.imports), imp_off)
        struct.pack_into("<i", summary, self.depends_pos, dep_off)
        ng = struct.unpack_from("<i", summary, self.gen_pos)[0]
        for g in range(ng):
            struct.pack_into("<ii", summary, self.gen_pos + 4 + 8 * g, len(exps), len(self.names))
        struct.pack_into("<i", summary, self.asset_reg_off_pos, areg_off)
        struct.pack_into("<q", summary, self.bulk_start_off, self.bulk_start)
        struct.pack_into("<ii", summary, self.preload_pos, len(arr), pre_off)
        out = summary + names + imports + b"".join(exps) + depends + asset_reg + b"".join(struct.pack("<i", x) for x in arr)
        assert len(out) == total
        self.head = out
        # the parsed view follows the new layout (a later edit or save works on it)
        self.total_header_size, self.name_count, self.name_offset = total, len(self.names), name_off
        self.export_count, self.export_offset = len(exps), exp_off
        self.import_count, self.import_offset = len(self.imports), imp_off
        self.depends_off, self.asset_reg_off = dep_off, areg_off
        self.preload_count, self.preload_off = len(arr), pre_off
        for i, (e, raw) in enumerate(zip(self.exports, exps)):
            e["pos"] = exp_off + 104 * i
            e["size_pos"], e["off_pos"] = e["pos"] + 28, e["pos"] + 36
            e["raw"] = raw
        del self._deps
        self.structural = False

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


def fstring(s):
    """FString as UE serializes it: ANSI (+ NUL) when every character is 7-bit, else UTF-16 with a negative length."""
    if all(ord(c) < 128 for c in s):
        return struct.pack("<i", len(s) + 1) + s.encode("ascii") + b"\0"
    b = s.encode("utf-16le") + b"\0\0"
    return struct.pack("<i", -(len(b) // 2)) + b


def _crc_table():
    t = []
    for i in range(256):
        c = i
        for _ in range(8): c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        t.append(c)
    return t


_CRC = _crc_table()


def _crc_table_deprecated():
    t = []
    for i in range(256):
        c = i << 24
        for _ in range(8): c = ((c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if c & 0x80000000 else (c << 1) & 0xFFFFFFFF
        t.append(c)
    return t


_CRC_DEP = _crc_table_deprecated()                      # FCrc::CRCTable_DEPRECATED (MSB-first)


def name_hashes(s):
    """The two uint16 hashes UE 4.25 saves after each name-map entry: FCrc::Strihash_DEPRECATED (upper-cased) and
    FCrc::StrCrc32, low 16 bits each."""
    h = 0
    wide = any(ord(c) >= 128 for c in s)
    for ch in s:
        c = ord(ch.upper()) if ord(ch) < 128 else ord(ch)
        for b in ((c & 0xFF, c >> 8) if wide else (c & 0xFF,)):
            h = ((h >> 8) & 0x00FFFFFF) ^ _CRC_DEP[(h ^ b) & 0xFF]
    crc = 0xFFFFFFFF
    for ch in s:
        c = ord(ch)
        for _ in range(4):
            crc = (crc >> 8) ^ _CRC[(crc ^ c) & 0xFF]; c >>= 8
    crc ^= 0xFFFFFFFF
    return struct.pack("<HH", h & 0xFFFF, crc & 0xFFFF)


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
    elif len(sys.argv) >= 3 and sys.argv[1] == "rebuild-check":
        # header rebuild of unedited packages must give the same bytes; name hashes must match the engine's
        bad = 0
        for f in sys.argv[2:]:
            p = Package(f)
            hb = sum(1 for n, hh in zip(p.names, p.name_hashes) if name_hashes(n) != hh)
            old = bytes(p.head)
            try:
                p.rebuild_header(); same = bytes(p.head) == old
            except ValueError as ex:
                print(f"skip {f}: {ex}"); continue
            if not same or hb:
                bad += 1; print(f"DIFF {f}: header {'same' if same else 'differs'}, {hb} name hash mismatches")
        print(f"{len(sys.argv) - 2} packages, {bad} differ")
        sys.exit(1 if bad else 0)
    else:
        print("usage: upkg.py props <x.uasset> [export name] | rebuild-check <x.uasset>...")
