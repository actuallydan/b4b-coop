"""Minimal reader/writer for B4B cooked packages (UE 4.25 legacy -7, unversioned, split .uasset/.uexp).

Only what the mesh tools need: summary, name map, imports, exports, tagged-property skipping, and rewriting an
export's serialized bytes (fixes SerialSize/SerialOffset of later exports and BulkDataStartOffset).
"""
import struct


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
