#!/usr/bin/env python3
"""Back 4 Blood .pak reader/writer (pak v9 as the game accepts it; docs/investigations/model-mods-paks.md).

B4B's pak format is stock UE 4.25 pak v9 (FrozenIndex) with three changes:
  - footer field order: Version u32, Magic u32, EncryptionKeyGuid[16], bEncryptedIndex u8, IndexHash[20] (SHA1),
    IndexSize u64, IndexOffset u64, bIndexIsFrozen u8, CompressionMethods 5 x char[32]  (222 bytes, like stock v9)
  - magic 0x00018772 (stock 0x5A6F12E1)
  - FPakEntry: ... Hash[20], [CompressionBlocks], CompressionBlockSize u32, Flags u8 (stock: Flags before block size)
The index itself is stock (FString MountPoint, int32 NumEntries, {FString name, FPakEntry}...). Retail paks encrypt
only the index (AES-256-ECB, key GUID 0) and compress with Oodle; this writer stores files uncompressed and
unencrypted, which the engine accepts.

  b4bpak.py info <pak>                         footer
  b4bpak.py list <pak> [--aes-key HEX]         index (retail indexes are encrypted: needs the key + `cryptography`)
  b4bpak.py pack <dir> <out.pak> [--mount P]   every file under <dir>; names relative to <dir>, mount point P
                                               (default ../../../ : <dir> must then contain Gobi/Content/...)
Never pack game assets into the repo; keep paks under ~/.local/share/b4b-coop/.
"""
import argparse, hashlib, os, struct, sys

MAGIC = 0x18772
VERSION = 9
FOOTER = 222


def fstring(s):
    b = s.encode("ascii") + b"\0"
    return struct.pack("<i", len(b)) + b


def entry(offset, size, sha1):
    # Offset, Size, UncompressedSize, CompressionMethodIndex (0 = none), Hash, CompressionBlockSize, Flags
    return struct.pack("<qqqI", offset, size, size, 0) + sha1 + struct.pack("<IB", 0, 0)


def read_footer(f):
    f.seek(0, 2)
    size = f.tell()
    f.seek(size - FOOTER)
    ft = f.read(FOOTER)
    ver, magic = struct.unpack_from("<II", ft, 0)
    isz, ioff = struct.unpack_from("<QQ", ft, 45)
    return dict(version=ver, magic=magic, guid=ft[8:24].hex(), encrypted_index=ft[24], index_sha1=ft[25:45].hex(),
                index_size=isz, index_offset=ioff, frozen=ft[61],
                compression=[ft[62 + 32 * i:94 + 32 * i].split(b"\0")[0].decode() for i in range(5)], file_size=size)


def read_index(path, aes_key=None):
    with open(path, "rb") as f:
        ft = read_footer(f)
        if ft["magic"] != MAGIC:
            raise SystemExit(f"{path}: magic {ft['magic']:#x}, not a B4B pak")
        f.seek(ft["index_offset"])
        raw = f.read(ft["index_size"])
    if ft["encrypted_index"]:
        if not aes_key:
            raise SystemExit("index is encrypted: pass --aes-key")
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        d = Cipher(algorithms.AES(bytes.fromhex(aes_key)), modes.ECB()).decryptor()
        raw = d.update(raw) + d.finalize()
    if hashlib.sha1(raw).hexdigest() != ft["index_sha1"]:
        raise SystemExit("index SHA1 mismatch (wrong key?)")
    o = 0

    def fstr():
        nonlocal o
        n, = struct.unpack_from("<i", raw, o); o += 4
        if n < 0:
            s = raw[o:o - 2 * n].decode("utf-16le"); o += -2 * n
        else:
            s = raw[o:o + n].decode("latin1"); o += n
        return s.rstrip("\0")
    mount = fstr()
    n, = struct.unpack_from("<i", raw, o); o += 4
    out = []
    for _ in range(n):
        name = fstr()
        off, csz, usz, cm = struct.unpack_from("<qqqI", raw, o); o += 28 + 20
        nb = 0
        if cm:
            nb, = struct.unpack_from("<i", raw, o); o += 4 + 16 * nb
        bs, fl = struct.unpack_from("<IB", raw, o); o += 5
        out.append(dict(name=name, offset=off, size=csz, usize=usz, method=cm, blocks=nb, block_size=bs, flags=fl))
    return ft, mount, out


def pack(src, dst, mount):
    files = []
    for root, _, names in os.walk(src):
        for n in names:
            p = os.path.join(root, n)
            files.append((os.path.relpath(p, src).replace(os.sep, "/"), p))
    files.sort()
    index = [fstring(mount), struct.pack("<i", len(files))]
    with open(dst, "wb") as f:
        for name, p in files:
            data = open(p, "rb").read()
            sha1 = hashlib.sha1(data).digest()
            off = f.tell()
            f.write(entry(0, len(data), sha1))   # in-data header: same entry, offset 0
            f.write(data)
            index.append(fstring(name) + entry(off, len(data), sha1))
        idx = b"".join(index)
        ioff = f.tell()
        f.write(idx)
        f.write(struct.pack("<II", VERSION, MAGIC) + bytes(16) + b"\0" + hashlib.sha1(idx).digest() +
                struct.pack("<QQ", len(idx), ioff) + b"\0" + bytes(160))
    print(f"{dst}: {len(files)} files, mount {mount}, pak v{VERSION} magic {MAGIC:#x}, uncompressed, unencrypted")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("info"); a.add_argument("pak")
    a = sub.add_parser("list"); a.add_argument("pak"); a.add_argument("--aes-key")
    a = sub.add_parser("pack"); a.add_argument("dir"); a.add_argument("out"); a.add_argument("--mount", default="../../../")
    args = ap.parse_args()
    if args.cmd == "info":
        with open(args.pak, "rb") as f:
            for k, v in read_footer(f).items():
                print(f"{k}: {hex(v) if k == 'magic' else v}")
    elif args.cmd == "list":
        ft, mount, entries = read_index(args.pak, args.aes_key)
        print(f"mount {mount}, {len(entries)} entries")
        for e in entries:
            print(f"{e['name']}  off={e['offset']} size={e['size']} usize={e['usize']} method={e['method']} "
                  f"blocks={e['blocks']} block_size={e['block_size']} flags={e['flags']}")
    else:
        pack(args.dir, args.out, args.mount)


if __name__ == "__main__":
    sys.exit(main())
