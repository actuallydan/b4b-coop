"""b4bmod convert: a survivor *replacement* mod (a .pak that overrides a survivor outfit's files) -> an ADDED outfit
any survivor can wear (/model <name>), next to the original and without replacing anything. Guide: docs/meshes.md
"Turn a replacement mod into an outfit"; background: docs/investigations/new-assets.md (b4b-coop repository).

What it does, per outfit the mod replaces (a third-person survivor mesh 3P_<Hero>_..._SKM, plus the FP_ arms of the
same outfit when the mod has them):
  - copies the mesh and everything of the mod it uses (material instances, materials, textures; also game material
    instances whose textures the mod replaced) to /Game/b4bcoop/outfits/<name>/, references rewritten to the copies;
  - leaves out the mod's own physics asset (the copy points at the survivor's retail one, and the b4bcoop mod uses the
    wearer's own hitboxes anyway), so the add-on stays cosmetic; skeletons, blueprints etc. are never copied;
  - leaves out portraits, ability cards and other UI (they only make sense as replacements);
  - writes the `outfit=` line(s) into <moddir>/addoninfo.txt.
Outfits the mod replaces with the same meshes (e.g. one look put on several outfits) become one. The original pak is
only read.
"""
import hashlib, os, re, shutil, struct, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import upkg, skm, addon  # noqa: E402
from b4bmodel import OUTFIT_ROOT, OUTFIT_NAME_RX, add_info_line  # noqa: E402

MESH_RX = re.compile(r"/Heroes/(?P<hero>[^/]+)/.*/3P_(?P=hero)_(?P<rest>[^/]*)_SKM$", re.I)


def log(*a):
    print("convert:", *a, flush=True)


def die(msg):
    print(f"b4bmod convert: {msg}", file=sys.stderr)
    raise SystemExit(1)


def pkgs_in(root):
    """{game package path (as written): .uasset file} of an extracted folder (<root>/Gobi/Content/...)."""
    out = {}
    base = os.path.join(root, "Gobi", "Content")
    for d, _, fs in os.walk(base):
        for f in fs:
            if f.endswith(".uasset"):
                p = os.path.join(d, f)
                out[upkg.file_to_game_path(p)] = p
    return out


def file_hash(uasset):
    """Same mesh = same export data (.uexp, .ubulk) and the same packages imported (the .uasset itself differs with
    the package's own name)."""
    h = hashlib.sha1()
    for ext in (".uexp", ".ubulk"):
        f = uasset[:-len(".uasset")] + ext
        if os.path.exists(f):
            h.update(open(f, "rb").read())
    p = upkg.Package(uasset)
    h.update("|".join(sorted(n for cp, cn, outer, n in p.imports if cn == "Package")).encode())
    return h.hexdigest()


def find_outfits(stage, retail_pkgs, retail_file):
    """[(hero, 3P package, FP package or None, key)] for the survivor outfits the mod replaces: 3P_<Hero>_*_SKM meshes
    on the game's survivor skeleton (a LastGen variant only when there is no other), else a game outfit whose folder
    the mod changes (e.g. only its textures)."""
    pk = pkgs_in(stage)
    low = {p.lower(): p for p in pk}
    found = []
    for p in sorted(pk):
        m = MESH_RX.search(p)
        if not m:
            continue
        cls, sk = mesh_class_skeleton(pk[p])
        if cls != "SkeletalMesh" or sk != "3P_Biped_SK":
            continue
        if "_lastgen" in p.lower() and p.lower().replace("_lastgen", "") in low:
            continue
        fp = re.sub(r"/3P_([^/]*)$", r"/FP_\1", p)
        fp = low.get(fp.lower())
        found.append((m.group("hero").lower(), p, fp))
    if not found:
        # a mod that only re-textures a game outfit: the outfit folders it changes -> the game's own meshes there
        folders = {os.path.dirname(p).lower() for p in pk}
        for r in sorted(retail_pkgs):
            m = MESH_RX.search(r)
            if not m or "_lastgen" in r.lower():
                continue
            d = os.path.dirname(r).lower()
            if any(f == d or f.startswith(d + "/") for f in folders):
                fp = re.sub(r"/3P_([^/]*)$", r"/FP_\1", r)
                found.append((m.group("hero").lower(), r, fp if fp.lower() in {x.lower() for x in retail_pkgs} else None))
    out, seen = [], {}
    for hero, p3, pf in found:
        f3 = pk.get(p3) or retail_file(p3)
        ff = (pk.get(pf) or retail_file(pf)) if pf else None
        key = (file_hash(f3), file_hash(ff) if ff else "")
        if key in seen:
            log(f"{p3}: same meshes as {seen[key]}, one outfit")
            continue
        seen[key] = p3
        out.append((hero, p3, pf))
    return out


def mesh_class_skeleton(uasset):
    p = upkg.Package(uasset)
    e = next((x for x in p.exports if x["outer"] == 0), None)
    cls = p.class_name(e) if e else None
    sk = next((n for cp, cn, outer, n in p.imports if cn == "Skeleton"), None)
    return cls, sk


def template_physics_asset(mesh_pkg, retail_file):
    """The retail mesh's PhysicsAsset object path (what the copy points at instead of the mod's own), or None."""
    f = retail_file(mesh_pkg)
    if not f:
        return None
    m = skm.SkeletalMesh(f)
    pa = m.props.get("PhysicsAsset")
    if not pa:
        return None
    idx = int.from_bytes(m.data[pa["off"]:pa["off"] + 4], "little", signed=True)
    return m.pkg.obj_path(idx) if idx < 0 else None


def imports_of(uasset):
    """[(package, class of the object imported from it)] of a package."""
    p = upkg.Package(uasset)
    out = []
    for cp, cn, outer, n in p.imports:
        if cn == "Package" or outer >= 0:
            continue
        op = p.imports[-outer - 1]
        if op[1] == "Package" and op[3].startswith("/Game/"):
            out.append((op[3], cn))
    return out


def plan(stage, meshes, retail_pkgs, retail_file, overrides):
    """What to copy for these meshes: {package: file}, {mod's or missing physics asset package: {meshes using it}},
    notes. overrides: the mod's packages at game paths (retail ones it replaces), which make game material instances
    that use them come along."""
    pk = pkgs_in(stage)
    copy, remap, notes, memo = {}, {}, [], {}
    low_retail = {r.lower() for r in retail_pkgs}

    def visit(pkg, cls, by=None):
        if pkg in remap:
            remap[pkg].add(by)
        if pkg in memo:
            return memo[pkg]
        memo[pkg] = False
        f = pk.get(pkg)
        if not f and pkg.lower() not in low_retail and cls == "PhysicsAsset":
            remap.setdefault(pkg, set()).add(by)   # a mesh's physics asset that doesn't exist: the retail one instead
            notes.append(f"missing physics asset {pkg} (used by {by}): the survivor's retail one is used")
            return False
        if not f and pkg.lower() not in low_retail:
            notes.append(f"warning: {pkg} is neither in the mod nor in the game (used by {by}); the game shows a default "
                         f"there, as with the original mod")
            return False
        if f:
            ok, what = addon.judge_package(open(f, "rb").read())
            if not ok:
                if cls == "PhysicsAsset" or what == "physics asset":
                    remap.setdefault(pkg, set()).add(by)   # the caller points each mesh at its retail one
                    notes.append(f"left out (the survivor's own hitboxes are used): {pkg}")
                else:
                    notes.append(f"left out ({what}; its references stay as they are): {pkg}")
                return False
            copy[pkg] = f
            memo[pkg] = True
            for dep, dcls in imports_of(f):
                visit(dep, dcls, pkg)
            return True
        if not overrides or cls not in ("MaterialInstanceConstant", "Material"):
            return False   # the game's own package, used as it is
        f = retail_file(pkg)
        if not f:
            return False
        hit = False
        for dep, dcls in imports_of(f):
            hit |= visit(dep, dcls, pkg)
        if hit:   # a game material instance using a texture the mod replaced: copied so the copy shows the mod's
            copy[pkg] = f
            memo[pkg] = True
        return hit

    for m in meshes:
        visit(m, "SkeletalMesh")
    for m in meshes:
        if m not in copy:
            die(f"{m}: not usable as an outfit ({'; '.join(notes) or 'not in the mod'})")
    return copy, remap, notes


def new_paths(copy, folder):
    """Copies keep their base names under <folder>/; a base name used twice keeps its parent folder too."""
    by_base = {}
    for p in copy:
        by_base.setdefault(p.rsplit("/", 1)[1].lower(), []).append(p)
    new = {}
    for p in copy:
        parts = p.split("/")
        base = parts[-1]
        if len(by_base[base.lower()]) > 1:
            base = parts[-2] + "/" + base
        new[p] = folder + base
    if len({v.lower() for v in new.values()}) != len(new):
        die("two packages of the mod end up with the same name; can't convert this one")
    return new


def run_rename(b4bmod, f, new, refs, moddir, src):
    r = subprocess.run([sys.executable, b4bmod, "rename", f, new, "-o", moddir, "--src", src] + refs,
                       capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stdout + r.stderr)
        die(f"copying {f} failed")


def keep_layout(src_uasset, out_uasset):
    """A renamed copy whose export data is unchanged keeps the original's export sizes and BulkDataStartOffset,
    moved by the header's size change. UAssetAPI recomputes them from BulkDataStartOffset, which some community
    cooks write 4 bytes low: the export then comes out 4 bytes short ("Serial size mismatch", a fatal load error)."""
    a, b = upkg.Package(src_uasset), upkg.Package(out_uasset)
    if bytes(a.uexp) != bytes(b.uexp) or len(a.exports) != len(b.exports):
        return False
    d = b.total_header_size - a.total_header_size
    fixed = False
    for ea, eb in zip(a.exports, b.exports):
        if (eb["size"], eb["offset"]) != (ea["size"], ea["offset"] + d):
            struct.pack_into("<q", b.head, eb["size_pos"], ea["size"])
            struct.pack_into("<q", b.head, eb["off_pos"], ea["offset"] + d)
            fixed = True
    if b.bulk_start != a.bulk_start + d:
        struct.pack_into("<q", b.head, b.bulk_start_off, a.bulk_start + d)
        fixed = True
    if fixed:
        open(out_uasset, "wb").write(b.head)
    return fixed


def convert(stage, name, moddir, src, retail_pkgs, retail_file, title=None, only=None, list_only=False):
    """stage: the mod's files extracted (<stage>/Gobi/Content/...). Returns the outfit lines written."""
    if not OUTFIT_NAME_RX.match(name):
        die(f"--as {name!r}: a name of letters, digits and _ (up to 32, starting with a letter), e.g. --as my_outfit")
    outfits = find_outfits(stage, retail_pkgs, retail_file)
    if not outfits:
        die("no survivor outfit in this pak (it replaces no 3P_<Survivor>_..._SKM mesh or outfit folder); "
            "only survivor outfit mods can be converted")
    many = len(outfits) > 1
    names = [f"{name}_{i + 1:02d}" if many else name for i in range(len(outfits))]
    if many and len(names[-1]) > 32:
        die(f"--as {name}: too long for {len(outfits)} outfits ({names[-1]}); use a shorter name")
    log(f"{len(outfits)} outfit(s) in the mod:")
    for n, (hero, p3, pf) in zip(names, outfits):
        log(f"  {n}: {p3}{' + ' + pf if pf else ' (no first-person arms: the wearer keeps their own)'}")
    if list_only:
        return []
    if only:
        want = [x.strip().lower() for x in only.split(",") if x.strip()]
        pick = [i for i, (n, o) in enumerate(zip(names, outfits))
                if any((w.isdigit() and int(w) == i + 1) or w == n or (not w.isdigit() and w in o[1].lower())
                       for w in want)]
        if not pick:
            die(f"--only {only}: matches none of the outfits above (give a number, a name, or part of the mesh path)")
        names, outfits = [names[i] for i in pick], [outfits[i] for i in pick]
        if len(outfits) == 1:   # one picked: it gets the plain name
            names, many = [name], False
    pk = pkgs_in(stage)
    low_retail = {r.lower() for r in retail_pkgs}
    meshes = [p for _, p3, pf in outfits for p in (p3, pf) if p]
    overrides = {p for p in pk if p.lower() in low_retail and p not in meshes}
    copy, remap, notes = plan(stage, meshes, retail_pkgs, retail_file, overrides)
    # each mesh that used the mod's (or a missing) physics asset points at the one of the retail mesh it replaced
    mesh_refs = {}
    for pa, users in sorted(remap.items()):
        for m in sorted(u for u in users if u):
            tpa = template_physics_asset(m, retail_file)
            if not tpa:
                die(f"{m}: uses the mod's physics asset {pa}, and there is no retail mesh {m} whose physics asset "
                    f"could be used instead")
            mesh_refs.setdefault(m, []).extend(["--ref", f"{pa}={tpa.split('.')[0]}"])
    for n in notes:
        log(n)
    folder = OUTFIT_ROOT + name + "/"
    new = new_paths(copy, folder)
    old_dir = os.path.join(moddir, "Gobi", "Content", *folder[len("/Game/"):].strip("/").split("/"))
    if os.path.isdir(old_dir):
        shutil.rmtree(old_dir)   # a previous run
    refs = [x for old, nw in sorted(new.items()) for x in ("--ref", f"{old}={nw}")]
    b4bmod = os.path.join(HERE, "b4bmod.py")
    order = {"Texture2D": 0, "MaterialInstanceConstant": 2, "Material": 1}
    classes = {p: (mesh_class_skeleton(f)[0] or "?") for p, f in copy.items()}
    # what the copies import from each other gets the class it really has (some mods' meshes import their material
    # instances as `Material`: the loader then refuses the import, "class mismatch", and the slot renders wrong)
    refs += [x for p in sorted(copy) if classes[p] != "?" for x in ("--import-class", f"{new[p]}={classes[p]}")]
    for i, p in enumerate(sorted(copy, key=lambda k: (order.get(classes[k], 3), k))):
        run_rename(b4bmod, copy[p], new[p], refs + mesh_refs.get(p, []), moddir, src)
        if classes[p] != "Texture2D" and keep_layout(copy[p], upkg.game_path_to_file(new[p], moddir)):
            log(f"  {new[p]}: export sizes kept as in the mod (its BulkDataStartOffset is off)")
        if i % 20 == 0 or classes[p] == "SkeletalMesh":
            log(f"  [{i + 1}/{len(copy)}] {classes[p]}: {p} -> {new[p]}")
    # the copies must not point at anything that was copied or left out
    bad = set(new) | set(remap)
    for p, nw in new.items():
        f = upkg.game_path_to_file(nw, moddir)
        stale = sorted({d for d, _ in imports_of(f) if d in bad})
        if stale:
            die(f"{nw} still references {stale}")
    # addoninfo: this conversion's outfit lines (earlier runs' <name> / <name>_NN lines replaced)
    info = os.path.join(moddir, "addoninfo.txt")
    if os.path.exists(info):
        keep = [x for x in open(info, encoding="utf-8-sig").read().splitlines()
                if not re.match(rf"\s*outfit\s*=\s*{re.escape(name)}(_\d\d)?\s*\|", x, re.I)]
        with open(info, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write("\n".join(keep) + ("\n" if keep else ""))
    lines = []
    base_title = (title or name).replace("|", "/").replace("\r", " ").replace("\n", " ")
    for i, (n, (hero, p3, pf)) in enumerate(zip(names, outfits)):
        obj = lambda pkg: f"{new[pkg]}.{pkg.rsplit('/', 1)[1]}" if pkg and pkg in new else ""
        t = f"{base_title} {n.rsplit('_', 1)[1]}" if many else base_title
        line = f"outfit={n}|{hero}|{obj(p3)}|{obj(pf)}|{t}"
        add_info_line(moddir, "outfit", n, line)
        lines.append(line)
        log(f"outfit {n} (from {hero}): /model {n}")
    log(f"{len(copy)} package(s) under {folder}; the mod's physics asset, portraits and cards are not in it")
    return lines
