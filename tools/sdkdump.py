"""Dump B4B reflection data (classes, structs, enums, functions, properties) from the live game.
Runs inside the probe daemon:  tools/probe.py 'import sdkdump; sdkdump.run(g)'
Writes sdk/*.txt and sdk/sdk.json in the repo.

Layout notes (B4B's modified 4.25 engine; stock offsets +8 after UObject, FField shuffled):
  UObject   : vtbl 0, Flags 8, Index 0xC, Class 0x10, Name 0x18, Outer 0x20, <extra> 0x28  (size 0x30)
  UField    : Next 0x30
  UStruct   : Super 0x48, Children 0x50, ChildProperties 0x58, PropertiesSize 0x60      (size 0xB8)
  UClass    : ClassFlags 0xD4, ClassCastFlags 0xD8, ClassWithin 0xE0, CDO 0x120
  UFunction : Flags 0xB8, NumParms 0xBC(u8), ParmsSize 0xBE(u16), RetOff 0xC0(u16), Func 0xE0
  FField    : Class 0x8, Owner 0x10, Flags 0x20, Name 0x24, Next 0x30
  FProperty : PropertyFlags 0x38, ElementSize 0x40, ArrayDim 0x44, Offset 0x4C; subtype ptr 0x78
"""
import json, os, struct, collections

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sdk")

FUNC_FLAGS = [(0x1, "Final"), (0x40, "Net"), (0x80, "NetReliable"), (0x200, "Exec"), (0x400, "Native"),
              (0x800, "Event"), (0x2000, "Static"), (0x4000, "NetMulticast"), (0x20000, "Public"),
              (0x40000, "Private"), (0x80000, "Protected"), (0x200000, "NetServer"), (0x1000000, "NetClient"),
              (0x4000000, "BlueprintCallable"), (0x8000000, "BlueprintEvent"), (0x10000000, "BlueprintPure"),
              (0x40000000, "Const")]
CPF_PARM, CPF_OUT, CPF_RET, CPF_NET, CPF_REPNOTIFY = 0x80, 0x100, 0x400, 0x20, 0x100000000
STRUCT_KINDS = {"Class", "BlueprintGeneratedClass", "WidgetBlueprintGeneratedClass", "AnimBlueprintGeneratedClass",
                "ScriptStruct", "UserDefinedStruct"}
ENUM_KINDS = {"Enum", "UserDefinedEnum"}
FUNC_KINDS = {"Function", "DelegateFunction", "SparseDelegateFunction"}


def run(g, only_script=False):
    m = g.m
    if g.objs is None: g.load_objects()
    clsname = {}
    def cname(c):
        if c not in clsname: clsname[c] = g.obj_name(c)
        return clsname[c]
    path_cache = {}
    def path(o):
        if o in path_cache: return path_cache[o]
        outer = g.obj_outer(o)
        p = (path(outer) + "." if outer else "") + g.obj_name(o)
        path_cache[o] = p
        return p
    objset = set(x for x in g.objs if x)

    def ptrname(p):
        if p in objset: return g.obj_name(p)
        return None

    def prop_type(f, fcname):
        sub = m.u64(f + 0x78)
        if fcname in ("StructProperty", "ObjectProperty", "WeakObjectProperty", "SoftObjectProperty",
                      "LazyObjectProperty", "InterfaceProperty", "ByteProperty", "EnumProperty"):
            n = ptrname(sub)
            if fcname == "EnumProperty":  # UnderlyingProp at 0x78, Enum at 0x80
                n = ptrname(m.u64(f + 0x80))
            if n: return f"{fcname}<{n}>"
        if fcname in ("ClassProperty", "SoftClassProperty"):
            n = ptrname(m.u64(f + 0x80))  # MetaClass after PropertyClass
            if n: return f"{fcname}<{n}>"
        if fcname in ("ArrayProperty", "SetProperty"):
            if sub and sub not in objset:
                try: return f"{fcname}<{prop_type(sub, g.name(m.u32(m.u64(sub + 8))))}>"
                except Exception: pass
        if fcname == "MapProperty":
            try:
                k, v = m.u64(f + 0x78), m.u64(f + 0x80)
                return (f"MapProperty<{prop_type(k, g.name(m.u32(m.u64(k + 8))))}, "
                        f"{prop_type(v, g.name(m.u32(m.u64(v + 8))))}>")
            except Exception: pass
        if fcname in ("DelegateProperty", "MulticastInlineDelegateProperty", "MulticastSparseDelegateProperty"):
            n = ptrname(sub)
            if n: return f"{fcname}<{n}>"
        return fcname

    def props(s):
        out, f = [], m.u64(s + 0x58)
        guard = 0
        while f and guard < 4000:
            raw = m.read(f, 0x50)
            fc = struct.unpack_from("<Q", raw, 0x8)[0]
            fcname = g.name(m.u32(fc))
            name = g.name(*struct.unpack_from("<II", raw, 0x24))
            flags = struct.unpack_from("<Q", raw, 0x38)[0]
            esize, adim, off = struct.unpack_from("<iii", raw, 0x40)[0], struct.unpack_from("<i", raw, 0x44)[0], struct.unpack_from("<i", raw, 0x4C)[0]
            out.append({"name": name, "type": prop_type(f, fcname), "offset": off, "size": esize, "dim": adim, "flags": flags})
            f = struct.unpack_from("<Q", raw, 0x30)[0]; guard += 1
        return out

    data = {"classes": {}, "structs": {}, "enums": {}}
    n = 0
    for o in g.objs:
        if not o: continue
        try:
            k = cname(g.obj_class(o))
        except Exception: continue
        if k not in STRUCT_KINDS and k not in ENUM_KINDS: continue
        try:
            p = path(o)
            if only_script and not p.startswith("/Script/"): continue
            if k in ENUM_KINDS:
                arr, num = m.u64(o + 0x48), m.i32(o + 0x50)
                vals = []
                if 0 < num < 5000:
                    raw = m.read(arr, num * 0x10)
                    for i in range(num):
                        ni, nn, v = struct.unpack_from("<IIq", raw, i * 0x10)
                        vals.append([g.name(ni, nn), v])
                data["enums"][p] = vals
                continue
            sup = m.u64(o + 0x48)
            entry = {"kind": k, "super": path(sup) if sup in objset else None, "size": m.i32(o + 0x60),
                     "props": props(o), "funcs": []}
            if k.endswith("Class"):
                entry["classFlags"] = m.u32(o + 0xD4)
                fn = m.u64(o + 0x50)
                guard = 0
                while fn and guard < 5000:
                    if fn in objset and cname(g.obj_class(fn)) in FUNC_KINDS:
                        raw = m.read(fn + 0xB8, 0x30)
                        ff, nparm, psize, roff = struct.unpack_from("<IBxHH", raw, 0)
                        func_ptr = struct.unpack_from("<Q", raw, 0x28)[0]
                        entry["funcs"].append({"name": g.obj_name(fn), "flags": ff, "parmsSize": psize,
                                               "native": func_ptr, "params": props(fn)})
                    fn = m.u64(fn + 0x30); guard += 1
                data["classes"][p] = entry
            else:
                data["structs"][p] = entry
            n += 1
        except Exception as e:
            print("skip", hex(o), e)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "sdk.json"), "w") as fh: json.dump(data, fh)
    write_text(data)
    print(f"dumped {len(data['classes'])} classes, {len(data['structs'])} structs, {len(data['enums'])} enums -> {OUT}")


def fflags(f): return " ".join(n for b, n in FUNC_FLAGS if f & b)


def write_text(data):
    by_pkg = collections.defaultdict(list)
    for kind in ("classes", "structs"):
        for p, e in data[kind].items(): by_pkg[p.split(".")[0]].append((p, e))
    for p, vals in data["enums"].items(): by_pkg[p.split(".")[0]].append((p, {"enum": vals}))
    for pkg, items in by_pkg.items():
        fn = pkg.strip("/").replace("/", "_") or "root"
        with open(os.path.join(OUT, fn + ".txt"), "w", encoding="utf-8") as fh:
            for p, e in sorted(items, key=lambda x: x[0]):
                if "enum" in e:
                    fh.write(f"enum {p} {{ " + ", ".join(f"{n}={v}" for n, v in e["enum"]) + " }\n\n")
                    continue
                fh.write(f"{e['kind']} {p} : {e['super']}  // size {e['size']:#x}\n")
                for pr in sorted(e["props"], key=lambda x: x["offset"]):
                    dim = f"[{pr['dim']}]" if pr["dim"] != 1 else ""
                    rep = " Replicated" if pr["flags"] & CPF_NET else ""
                    fh.write(f"    {pr['offset']:#06x} {pr['size']:#05x}  {pr['type']} {pr['name']}{dim}{rep}\n")
                for f in e["funcs"]:
                    ps = [x for x in f["params"] if x["flags"] & CPF_PARM]
                    ret = next((x["type"] for x in ps if x["flags"] & CPF_RET), "void")
                    args = ", ".join(("out " if x["flags"] & CPF_OUT else "") + f"{x['type']} {x['name']}"
                                     for x in ps if not x["flags"] & CPF_RET)
                    fh.write(f"    fn {ret} {f['name']}({args})  [{fflags(f['flags'])}] native={f['native']:#x}\n")
                fh.write("\n")
