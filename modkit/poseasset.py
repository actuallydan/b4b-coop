"""Read a cooked UPoseAsset (B4B faces: FacePoses_<Hero>_PoseAsset): pose names, tracks (bones) and each pose's
local-space bone transforms. For inspecting what the game's face poses do and for previews of a fitted face
(blender/preview.py --face). docs/investigations/mesh-mods.md §10 (b4b-coop repository).

  poseasset.py <PoseAsset.uasset> [out.json]     prints a summary (per pose: bones moved, degrees / cm);
                                                 out.json: {"additive", "tracks", "poses": {name: {bone: [qx,qy,qz,qw,
                                                 tx,ty,tz, sx,sy,sz]}}} in UE units (cm), bone-local (parent space)
"""
import json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upkg


def _tag(p, r):
    name = p.fname(r)
    if name == "None": return None
    typ = p.fname(r); size = r.i32(); r.i32(); info = None
    if typ == "StructProperty": info = p.fname(r); r.p += 16
    elif typ == "BoolProperty": info = r.u8()
    elif typ in ("ArrayProperty", "SetProperty", "ByteProperty", "EnumProperty"): info = p.fname(r)
    elif typ == "MapProperty": info = (p.fname(r), p.fname(r))
    if r.u8(): r.p += 16
    return name, typ, size, info


def _array_header(p, r):
    """TArray<struct> in tagged data: count, then one inner tag (name, type, size, index, struct name, guid)."""
    n = r.i32(); p.fname(r); p.fname(r); r.i32(); r.i32(); st = p.fname(r); r.p += 16
    if r.u8(): r.p += 16
    return n, st


def _struct(p, r):
    out = {}
    while True:
        t = _tag(p, r)
        if t is None: return out
        name, typ, size, info = t
        end = r.p + size
        if typ == "StructProperty" and info == "Quat": out[name] = r.unpack("4f")
        elif typ == "StructProperty" and info == "Vector": out[name] = r.unpack("3f")
        elif typ == "ArrayProperty" and info == "StructProperty":
            n, _ = _array_header(p, r); out[name] = [_struct(p, r) for _ in range(n)]
        elif typ == "ArrayProperty" and info == "FloatProperty":
            n = r.i32(); out[name] = [r.f32() for _ in range(n)]
        elif typ == "MapProperty" and info == ("IntProperty", "IntProperty"):
            r.i32(); n = r.i32(); out[name] = dict(r.unpack("2i") for _ in range(n))
        r.p = end


def read(path):
    """{"additive": bool, "tracks": [bone], "poses": {name: {bone: (q4, t3, s3 flattened)}}} (only tracks a pose has)."""
    p = upkg.Package(path)
    e = next(x for x in p.exports if p.class_name(x) == "PoseAsset")
    r = upkg.R(p.export_data(e))
    names, tracks, poses, additive = [], [], [], False
    while True:
        t = _tag(p, r)
        if t is None: break
        name, typ, size, info = t
        end = r.p + size
        if name == "PoseContainer":
            while True:
                u = _tag(p, r)
                if u is None: break
                un, ut, us, ui = u
                uend = r.p + us
                if un == "PoseNames":
                    n, _ = _array_header(p, r); names = [p.fname(r) for _ in range(n)]   # FSmartName: its FName
                elif un == "Tracks":
                    n = r.i32(); tracks = [p.fname(r) for _ in range(n)]
                elif un == "Poses":
                    n, _ = _array_header(p, r); poses = [_struct(p, r) for _ in range(n)]
                r.p = uend
        elif name == "bAdditivePose":
            additive = bool(info)
        r.p = end
    out = {}
    for pn, pose in zip(names, poses):
        lsp = pose.get("LocalSpacePose", [])
        d = {}
        for ti, bi in sorted(pose.get("TrackToBufferIndex", {}).items()):
            x = lsp[bi]
            q = x.get("Rotation", (0.0, 0.0, 0.0, 1.0)); tr = x.get("Translation", (0.0, 0.0, 0.0))
            sc = x.get("Scale3D", (0.0, 0.0, 0.0) if additive else (1.0, 1.0, 1.0))
            d[tracks[ti]] = list(q) + list(tr) + list(sc)
        out[pn] = d
    return {"additive": additive, "tracks": tracks, "poses": out}


def summary(pa, min_deg=0.5, min_cm=0.05):
    lines = []
    for pn, d in pa["poses"].items():
        parts = []
        for b, x in d.items():
            ang = 2 * math.degrees(math.acos(min(1.0, abs(x[3]))))
            cm = math.sqrt(sum(c * c for c in x[4:7]))
            if ang > min_deg or cm > min_cm: parts.append(f"{b} {ang:.0f}deg/{cm:.2f}cm")
        lines.append(f"{pn}: " + (", ".join(parts) if parts else "(rest)"))
    return lines


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); raise SystemExit(1)
    pa = read(sys.argv[1])
    print(f"{len(pa['poses'])} poses, {len(pa['tracks'])} tracks, additive {pa['additive']}")
    print("\n".join(summary(pa)))
    if len(sys.argv) > 2:
        json.dump(pa, open(sys.argv[2], "w"), indent=0)
        print("wrote", sys.argv[2])
