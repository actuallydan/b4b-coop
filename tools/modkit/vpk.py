#!/usr/bin/env python3
"""Minimal Valve VPK (v1/v2) reader: list and extract files from a Source engine game's *_dir.vpk archives.
Dev tool (no dependencies); used by tools/modkit/source_survivor.py to pull models and materials out of a local
Source game install (e.g. Left 4 Dead 2) for private test characters. Nothing it extracts is ever committed.

    tools/modkit/vpk.py list <game dir or *_dir.vpk> [regex]
    tools/modkit/vpk.py extract <game dir or *_dir.vpk> <out dir> <regex> [<regex> ...]

A game dir is searched for every */*_dir.vpk; when several archives hold the same path, the one listed later in
VPK_PRIORITY order wins (update > newest DLC > base game), so the extracted file is the one the game loads.
"""
import os, re, struct, sys, zlib

SIG = 0x55AA1234


def _cstr(f):
    out = bytearray()
    while True:
        c = f.read(1)
        if not c or c == b"\0":
            return out.decode("utf-8", "replace")
        out += c


class VPK:
    def __init__(self, dir_path):
        self.path = dir_path
        self.base = dir_path[:-len("_dir.vpk")]
        self.entries = {}   # path -> (crc, preload, archive, offset, length)
        with open(dir_path, "rb") as f:
            sig, ver, tree = struct.unpack("<III", f.read(12))
            if sig != SIG:
                raise ValueError(f"{dir_path}: not a VPK")
            if ver == 2:
                f.read(16)
            elif ver != 1:
                raise ValueError(f"{dir_path}: VPK version {ver}")
            self.data_start = f.tell() + tree
            while True:
                ext = _cstr(f)
                if not ext:
                    break
                while True:
                    d = _cstr(f)
                    if not d:
                        break
                    while True:
                        name = _cstr(f)
                        if not name:
                            break
                        crc, plen, arc, off, ln, term = struct.unpack("<IHHIIH", f.read(18))
                        pre = f.read(plen)
                        p = (f"{d}/" if d.strip() else "") + name + (f".{ext}" if ext.strip() else "")
                        self.entries[p.lower()] = (crc, pre, arc, off, ln)

    def read(self, p):
        crc, pre, arc, off, ln = self.entries[p.lower()]
        data = pre
        if ln:
            if arc == 0x7FFF:
                fn, off = self.path, self.data_start + off
            else:
                fn = f"{self.base}_{arc:03d}.vpk"
            with open(fn, "rb") as f:
                f.seek(off)
                data += f.read(ln)
        if zlib.crc32(data) & 0xFFFFFFFF != crc:
            raise ValueError(f"{p}: CRC mismatch in {self.path}")
        return data


def priority(path):
    """Later wins: base game < dlc1 < dlc2 < ... < update."""
    d = os.path.basename(os.path.dirname(path)).lower()
    m = re.search(r"_dlc(\d+)$", d)
    if d == "update":
        return 1000
    if m:
        return 100 + int(m.group(1))
    return 0


def open_all(src):
    if src.endswith(".vpk"):
        return [VPK(src)]
    vpks = []
    for d in sorted(os.listdir(src)):
        full = os.path.join(src, d)
        if os.path.isdir(full):
            for fn in sorted(os.listdir(full)):
                if fn.endswith("_dir.vpk"):
                    vpks.append(os.path.join(full, fn))
    vpks.sort(key=priority)
    return [VPK(p) for p in vpks]


def index(vpks):
    """path -> VPK that wins it."""
    idx = {}
    for v in vpks:
        for p in v.entries:
            idx[p] = v
    return idx


def extract(src, out, patterns):
    vpks = open_all(src)
    idx = index(vpks)
    rx = [re.compile(p, re.I) for p in patterns]
    n = 0
    for p, v in sorted(idx.items()):
        if any(r.search(p) for r in rx):
            dst = os.path.join(out, p)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(v.read(p))
            n += 1
    return n


def main(argv):
    if len(argv) >= 2 and argv[0] == "list":
        rx = re.compile(argv[2], re.I) if len(argv) > 2 else None
        idx = index(open_all(argv[1]))
        for p, v in sorted(idx.items()):
            if not rx or rx.search(p):
                print(f"{p}\t{os.path.relpath(v.path, argv[1]) if not argv[1].endswith('.vpk') else ''}")
        return 0
    if len(argv) >= 4 and argv[0] == "extract":
        n = extract(argv[1], argv[2], argv[3:])
        print(f"extracted {n} files to {argv[2]}")
        return 0 if n else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
