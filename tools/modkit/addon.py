#!/usr/bin/env python3
"""b4bcoop add-on packer (#20; docs/investigations/addons.md, player side: docs/COMMANDS.md "Add-ons").

An add-on is one .pak (tools/b4bpak.py format: v9, B4B footer, uncompressed, unencrypted) that the agent mounts from
<game>/b4bcoop-addons/ after the retail paks. Besides the cooked files it holds `b4bcoop-addoninfo.txt` (title,
author, version, category, description), which the agent shows in /addons.

  addon.py pack <src> [-o OUT.pak] [--name N] [--title T] [--author A] [--version V] [--category C]
                [--description D] [--zip]
      <src> is a folder of cooked files laid out like the game (Gobi/Content/..., Engine/Content/...), as
      `dumpassets` extracts them and the modkit tools write them, with an optional addoninfo.txt at its top
      (`key=value` lines, or L4D's `addontitle "..."` KeyValues). <src> may also be one of our uncompressed .pak
      files (e.g. from `b4bpak.py pack`): its files are repacked with the addoninfo.
      --zip also writes OUT.zip = b4bcoop-addons/<name>.pak, which players unzip into the game folder.
  addon.py info <addon.pak>          addoninfo, content id (index SHA1) and files
  addon.py check <addons dir>        what the agent will do with that folder: load order from addonlist.txt, on/off,
                                     conflicts (same file in two enabled add-ons; the later one wins)
Never commit or ship game assets: build add-ons under ~/.local/share/b4b-coop/.
"""
import argparse, hashlib, io, os, re, struct, sys, zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import b4bpak  # noqa: E402

INFO = "b4bcoop-addoninfo.txt"
KEYS = ("title", "author", "version", "category", "description")
CATEGORIES = ("survivors", "ridden", "weapons", "items", "ui", "sounds", "maps", "misc")
PKG_EXT = (".uasset", ".uexp", ".ubulk", ".uptnl", ".umap")
ENTRY_HDR = 53


def parse_info(text):
    """Same rules as the agent (addons.c parse_info): key=value, or KeyValues `addontitle "value"`."""
    out = {}
    for line in text.splitlines():
        l = line.strip()
        if not l or l[0] in "#;{}" or l.startswith("//"):
            continue
        if "=" in l:
            k, v = l.split("=", 1)
        elif l.startswith('"'):
            q = l.find('"', 1)
            if q < 0:
                continue
            k, v = l[1:q], l[q + 1:]
        else:
            k, _, v = l.partition(" ") if " " in l else l.partition("\t")
        k, v = k.strip().strip('"').lower(), v.strip()
        if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1]
        if k.startswith("addon"):
            k = k[5:]
        if k in KEYS:
            out[k] = v
    return out


def info_text(info):
    return "".join(f"{k}={info[k]}\r\n" for k in KEYS if info.get(k))


def read_pak_files(path):
    """(name relative to ../../../, bytes) of every file in one of our uncompressed paks."""
    ft, mount, entries = b4bpak.read_index(path)
    prefix = mount
    while prefix.startswith("../"):
        prefix = prefix[3:]
    out = []
    with open(path, "rb") as f:
        for e in entries:
            if e["method"]:
                raise SystemExit(f"{path}: {e['name']} is compressed; only uncompressed paks can be repacked")
            f.seek(e["offset"] + ENTRY_HDR)
            out.append((prefix + e["name"], f.read(e["size"])))
    return ft, out


def collect(src):
    """Files to pack from a folder: [(name, path)], plus addoninfo text if present."""
    files, info = [], None
    for root, dirs, names in os.walk(src):
        dirs.sort()
        for n in sorted(names):
            p = os.path.join(root, n)
            rel = os.path.relpath(p, src).replace(os.sep, "/")
            if rel.lower() in ("addoninfo.txt", INFO):
                info = open(p, encoding="utf-8-sig").read()
                continue
            files.append((rel, p))
    return files, info


def check_names(names):
    errs, warns = [], []
    for n in names:
        top = n.split("/", 1)[0]
        if top not in ("Gobi", "Engine"):
            errs.append(f"{n}: not under Gobi/ or Engine/ (lay the files out like the game: Gobi/Content/...)")
        if not n.isascii():
            errs.append(f"{n}: non-ASCII file name")
    lower = {n.lower() for n in names}
    for n in names:
        base, ext = os.path.splitext(n)
        if ext.lower() == ".uasset" and (base + ".uexp").lower() not in lower:
            warns.append(f"{n}: no {os.path.basename(base)}.uexp next to it (cooked packages come as .uasset + .uexp)")
        if ext.lower() in (".uexp", ".ubulk") and (base + ".uasset").lower() not in lower:
            warns.append(f"{n}: no {os.path.basename(base)}.uasset next to it: part of a package only, mixes with the "
                         "game's own .uasset (may crash)")
    return errs, warns


def write_pak(dst, items, mount="../../../"):
    """items: [(name, bytes)], written in sorted order (deterministic output)."""
    items = sorted(items, key=lambda t: t[0])
    index = [b4bpak.fstring(mount), struct.pack("<i", len(items))]
    with open(dst, "wb") as f:
        for name, data in items:
            sha1 = hashlib.sha1(data).digest()
            off = f.tell()
            f.write(b4bpak.entry(0, len(data), sha1))
            f.write(data)
            index.append(b4bpak.fstring(name) + b4bpak.entry(off, len(data), sha1))
        idx = b"".join(index)
        ioff = f.tell()
        f.write(idx)
        f.write(struct.pack("<II", b4bpak.VERSION, b4bpak.MAGIC) + bytes(16) + b"\0" + hashlib.sha1(idx).digest() +
                struct.pack("<QQ", len(idx), ioff) + b"\0" + bytes(160))
    return hashlib.sha1(idx).hexdigest()


def safe_name(s):
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")
    return s or "addon"


def cmd_pack(a):
    src = a.src.rstrip("/")
    if os.path.isfile(src):
        _, items = read_pak_files(src)
        items = [(n, d) for n, d in items if n.lower() != INFO]
        info = {}
    else:
        paths, text = collect(src)
        items = [(n, open(p, "rb").read()) for n, p in paths]
        info = parse_info(text) if text else {}
    for k in KEYS:
        if getattr(a, k):
            info[k] = getattr(a, k)
    if not items:
        raise SystemExit(f"{src}: no files")
    errs, warns = check_names([n for n, _ in items])
    for w in warns:
        print("warning:", w, file=sys.stderr)
    if errs:
        raise SystemExit("\n".join(errs))
    name = safe_name(a.name or os.path.splitext(os.path.basename(a.out or src))[0])
    out = a.out or name + ".pak"
    info.setdefault("title", name)
    if info.get("category") and info["category"].lower() not in CATEGORIES:
        print(f"warning: category {info['category']!r} is not one of {', '.join(CATEGORIES)}", file=sys.stderr)
    items.append((INFO, info_text(info).encode("utf-8")))
    cid = write_pak(out, items)
    print(f"{out}: {len(items) - 1} file(s) + addoninfo, content id {cid}")
    for k in KEYS:
        if info.get(k):
            print(f"  {k}: {info[k]}")
    if a.zip:
        zpath = os.path.splitext(out)[0] + ".zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            zi = zipfile.ZipInfo(f"b4bcoop-addons/{os.path.basename(out)}", date_time=(2020, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, open(out, "rb").read())
        print(f"{zpath}: unzip into the game folder (creates b4bcoop-addons/{os.path.basename(out)})")


def load_addon(path):
    ft, items = read_pak_files(path)
    info, files = {}, []
    for n, d in items:
        if n.lower() == INFO:
            info = parse_info(d.decode("utf-8", "replace"))
        else:
            files.append(n)
    return ft["index_sha1"], info, files


def cmd_info(a):
    cid, info, files = load_addon(a.pak)
    print(f"{a.pak}: content id {cid}, {len(files)} file(s)")
    for k in KEYS:
        print(f"  {k}: {info.get(k, '')}")
    for n in files:
        print("  ", n)


def read_list(d):
    order = []
    p = os.path.join(d, "addonlist.txt")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8", errors="replace"):
            l = line.strip()
            if not l or l[0] in "#;" or "=" not in l:
                continue
            k, v = (s.strip().strip('"') for s in l.split("=", 1))
            if k and k.lower() not in (o[0].lower() for o in order):
                order.append((k, v.lower() not in ("0", "off", "false")))
    return order


def cmd_check(a):
    d = a.dir
    listed = read_list(d)
    present = {f.lower(): f for f in os.listdir(d) if f.lower().endswith(".pak")}
    order = [(present[k.lower()], on) for k, on in listed if k.lower() in present]
    known = {k.lower() for k, _ in listed}
    order += [(present[k], True) for k in sorted(present, key=str.lower) if k not in known]
    owner, conflicts = {}, {}
    for i, (f, on) in enumerate(order):
        try:
            cid, info, files = load_addon(os.path.join(d, f))
            why = ""
        except SystemExit as e:
            cid, info, files, why = "-", {}, [], str(e)
        print(f"{i + 1}. {f} [{'on' if on else 'off'}] {info.get('title', f[:-4])} {info.get('version', '')}"
              f"{'  NOT LOADED: ' + why if why else ''}")
        if not on:
            continue
        for n in files:
            k = n.lower()
            if k in owner and owner[k] != f:
                conflicts.setdefault((owner[k], f), []).append(n)
            owner[k] = f
    stems = {}
    for k, f in owner.items():
        base, ext = os.path.splitext(k)
        s = base if ext in PKG_EXT else k
        if s in stems and stems[s] != f:
            print(f"conflict: {stems[s]} and {f} mix parts of one asset ({s}): may crash, switch one off")
        stems.setdefault(s, f)
    for (lo, hi), names in conflicts.items():
        print(f"conflict: {hi} overrides {lo} ({len(names)} file(s), e.g. {names[0]})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("src")
    p.add_argument("-o", "--out")
    p.add_argument("--name", help="file name without .pak (default: from -o or the folder name)")
    for k in KEYS:
        p.add_argument("--" + k)
    p.add_argument("--zip", action="store_true")
    p = sub.add_parser("info"); p.add_argument("pak")
    p = sub.add_parser("check"); p.add_argument("dir")
    a = ap.parse_args()
    {"pack": cmd_pack, "info": cmd_info, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    main()
