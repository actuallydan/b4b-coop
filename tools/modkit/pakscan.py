"""Index-free reader for B4B paks: lists and extracts the *plaintext* entries without the pak index.

B4B's pak index (footer magic 0x18772, fields reordered, see CUE4Parse FPakInfo GAME_Back4Blood) is encrypted with a
custom scheme (the AES key in the exe is public but plain AES-256-ECB does not decrypt it). The per-entry FPakEntry
copies in front of each file's data are plaintext, and ~half the entries (pakchunk33-80) are not encrypted, so we walk
the entries and name packages from their own export table:

  entry = Offset(8)=0 CompressedSize(8) UncompressedSize(8) Method(4: 0 none, 1 Oodle) Hash(20)
          [nBlocks(4) + n*(start8, end8), relative to the entry] CompressionBlockSize(4) Flags(1: bit0 encrypted)
          (B4B swaps the last two fields vs stock 4.25; encrypted data is padded to 16.)

A .uexp is matched to its .uasset by size (BulkDataStartOffset - TotalHeaderSize + 4) plus a check that its first
property tag resolves against the .uasset's name map. Same-shape siblings (e.g. two 2048^2 BC7 textures) can still be
swapped; the structure is then identical, so this is fine for format work, not for content.

Oodle: set B4B_OODLE to liboodle-data-shared.so (default vendor/oodle/, from github.com/WorkingRobot/OodleUE releases;
do not redistribute). Never commit anything this extracts.

usage: pakscan.py list <pak>                     TSV: pos kind size name
       pakscan.py extract <pak> <outdir> <regex>  write matching .uasset/.uexp pairs
       pakscan.py summary <file.uasset>           dump the package file summary + name map
"""
import ctypes, os, re, struct, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PKG = b'\xc1\x83\x2a\x9e'
_oodle = None


def unoodle(src, rawlen):
    global _oodle
    if _oodle is None:
        _oodle = ctypes.CDLL(os.environ.get('B4B_OODLE', os.path.join(ROOT, 'vendor/oodle/liboodle-data-shared.so')))
        _oodle.OodleLZ_Decompress.restype = ctypes.c_int64
        _oodle.OodleLZ_Decompress.argtypes = [ctypes.c_char_p, ctypes.c_int64, ctypes.c_char_p, ctypes.c_int64] + \
            [ctypes.c_int] * 3 + [ctypes.c_void_p, ctypes.c_int64] + [ctypes.c_void_p] * 3 + [ctypes.c_int64, ctypes.c_int]
    out = ctypes.create_string_buffer(rawlen)
    n = _oodle.OodleLZ_Decompress(src, len(src), out, rawlen, 1, 0, 0, None, 0, None, None, None, 0, 3)
    if n != rawlen: raise ValueError(f'oodle {n} != {rawlen}')
    return out.raw


# ---- pak entries ------------------------------------------------------------------------------------------------
def parse_entry(f, pos, flen):
    f.seek(pos)
    h = f.read(48)
    if len(h) < 48: return None
    off, csz, usz, meth = struct.unpack_from('<qqqI', h)
    if off != 0 or meth > 4 or not (0 < csz <= flen) or not (0 < usz < 1 << 34) or h[28:48] == bytes(20): return None
    blocks = []
    if meth:
        n = struct.unpack('<I', f.read(4))[0]
        if n == 0 or n > 100000: return None
        blocks = [struct.unpack('<qq', f.read(16)) for _ in range(n)]
    bsz, flags = struct.unpack('<IB', f.read(5))
    if flags > 3: return None
    if meth and (bsz == 0 or bsz > 1 << 20 or len(blocks) != (usz + bsz - 1) // bsz): return None
    if meth and any(b[1] < b[0] or b[0] < 0 for b in blocks): return None
    hdr = f.tell() - pos
    a16 = (lambda x: (x + 15) & ~15) if flags & 1 else (lambda x: x)
    if meth:
        if blocks[0][0] != hdr: return None
        end = pos + blocks[-1][0] + a16(blocks[-1][1] - blocks[-1][0])
    else:
        end = pos + hdr + a16(csz)
    return dict(pos=pos, csz=csz, usz=usz, meth=meth, blocks=blocks, bsz=bsz, flags=flags, hdr=hdr, end=end)


def entries(f):
    flen = os.fstat(f.fileno()).st_size
    pos = 0
    while pos < flen - 256:
        e = parse_entry(f, pos, flen)
        if e is None:  # alignment padding: resync, preferring aligned positions
            for al in (0x800, 16, 1):
                for q in range((pos // al + 1) * al, pos + 0x10000, al):
                    e = parse_entry(f, q, flen)
                    if e: break
                if e: break
            if e is None: return
        yield e
        pos = e['end']


def read_data(f, e, need=None):
    """Whole entry, or at least `need` leading bytes."""
    if e['flags'] & 1: raise ValueError('encrypted entry')
    if not e['meth']:
        f.seek(e['pos'] + e['hdr']); return f.read(min(need or e['usz'], e['usz']))
    out, left = [], e['usz']
    for s, t in e['blocks']:
        f.seek(e['pos'] + s); raw = min(e['bsz'], left); left -= raw
        out.append(unoodle(f.read(t - s), raw))
        if need and sum(map(len, out)) >= need: break
    return b''.join(out)


def tail4(f, e):
    if not e['meth']:
        f.seek(e['pos'] + e['hdr'] + e['usz'] - 4); return f.read(4)
    s, t = e['blocks'][-1]
    f.seek(e['pos'] + s); return unoodle(f.read(t - s), e['usz'] - e['bsz'] * (len(e['blocks']) - 1))[-4:]


# ---- package summary (UE 4.25 legacy format, -7) ---------------------------------------------------------------
class R:
    def __init__(s, b, p=0): s.b, s.p = b, p
    def _u(s, fmt, n): v = struct.unpack_from(fmt, s.b, s.p)[0]; s.p += n; return v
    def i32(s): return s._u('<i', 4)
    def u32(s): return s._u('<I', 4)
    def i64(s): return s._u('<q', 8)
    def u16(s): return s._u('<H', 2)
    def guid(s): v = s.b[s.p:s.p + 16]; s.p += 16; return '-'.join('%08X' % x for x in struct.unpack('<4I', v))
    def fstr(s):
        n = s.i32()
        if n < 0: v = s.b[s.p:s.p - 2 * n].decode('utf-16le')[:-1]; s.p += -2 * n; return v
        v = s.b[s.p:s.p + n][:-1].decode('latin1'); s.p += n; return v
    def arr(s, fn): return [fn() for _ in range(s.i32())]


def summary(b):
    r = R(b); d = {'tag': hex(r.u32()), 'legacy': r.i32()}
    if d['legacy'] != -4: d['ue3'] = r.i32()
    d['ue4'] = r.i32(); d['licensee'] = r.i32()
    d['custom'] = r.arr(lambda: (r.guid(), r.i32()))
    d['total_header'] = r.i32(); d['folder'] = r.fstr(); flags = r.u32(); d['flags'] = hex(flags)
    d['name_count'] = r.i32(); d['name_off'] = r.i32()
    if not flags & 0x80000000: d['loc_id'] = r.fstr()  # PKG_FilterEditorOnly
    d['gather'] = (r.i32(), r.i32())
    d['export_count'], d['export_off'], d['import_count'], d['import_off'] = r.i32(), r.i32(), r.i32(), r.i32()
    d['depends_off'] = r.i32(); d['softrefs'] = (r.i32(), r.i32()); d['searchable'] = r.i32(); d['thumb'] = r.i32()
    d['guid'] = r.guid(); d['generations'] = r.arr(lambda: (r.i32(), r.i32()))
    d['saved_by'] = (r.u16(), r.u16(), r.u16(), r.u32(), r.fstr())
    d['compatible'] = (r.u16(), r.u16(), r.u16(), r.u32(), r.fstr())
    d['compression'] = r.u32(); d['chunks'] = r.i32(); d['source'] = hex(r.u32())
    d['additional'] = r.arr(r.fstr); d['assetreg'] = r.i32(); d['bulk_start'] = r.i64(); d['worldtile'] = r.i32()
    d['chunk_ids'] = r.arr(r.i32); d['preload'] = (r.i32(), r.i32())
    r.p = d['name_off']; d['names'] = []
    for _ in range(d['name_count']):
        d['names'].append(r.fstr()); r.p += 4  # two uint16 hashes
    return d


def asset_name(d, b):
    """Package path of the asset: name-map entry ending in the main (bIsAsset, outer-less) export's name."""
    r = R(b, d['export_off']); names = d['names']; best = None
    for _ in range(d['export_count']):
        r.p += 12; outer = r.i32(); ni = r.i32(); r.p += 4 + 4 + 16 + 12 + 16 + 4 + 4
        is_asset = r.i32(); r.p += 20
        if outer == 0 and (best is None or is_asset): best = names[ni]
        if is_asset and outer == 0: break
    base = re.sub(r'^Default__|_C$', '', best or '')
    return next((n for n in names if n.startswith('/') and n.rsplit('/', 1)[-1] == base), f'?/{base}')


def scan(path):
    f = open(path, 'rb'); rows = []
    for e in entries(f):
        row = dict(e=e, kind='bin', name=None)
        if e['flags'] & 1: row['kind'] = 'enc'
        else:
            try:
                if read_data(f, e, 4)[:4] == PKG:
                    th = struct.unpack_from('<i', read_data(f, e, 64), 24)[0]
                    b = read_data(f, e, th + 16); d = summary(b)
                    row.update(kind='uasset', name=asset_name(d, b), names=d['names'],
                               uexp_size=d['bulk_start'] - d['total_header'] + 4)
                elif tail4(f, e) == PKG: row['kind'] = 'uexp'
            except Exception as ex: row['kind'] = 'err:' + str(ex)[:40]
        rows.append(row)
    # Pair by size bucket, keeping pak order (the i-th uasset of a size gets the i-th uexp of that size) when the
    # counts agree; otherwise fall back to the first fitting candidate after the uasset.
    uexps, uassets = {}, {}
    for i, r in enumerate(rows):
        if r['kind'] == 'uexp': uexps.setdefault(r['e']['usz'], []).append(i)
        if r['kind'] == 'uasset': uassets.setdefault(r['uexp_size'], []).append(i)

    def fits(names, j):  # the uexp's first property tag must resolve against this name map
        a, _, t, _ = struct.unpack_from('<4i', read_data(f, rows[j]['e'], 16))
        if not 0 <= a < len(names): return False
        if names[a] == 'None': return True  # export with no tagged properties
        return 0 <= t < len(names) and names[t].endswith('Property') and not names[a].endswith('Property')

    for size, ua in uassets.items():
        ue = uexps.get(size, [])
        if len(ue) == len(ua) and all(fits(rows[i]['names'], j) for i, j in zip(ua, ue)):
            for i, j in zip(ua, ue): rows[j]['name'] = rows[i]['name']
            continue
        free = list(ue)
        for i in ua:
            r = rows[i]
            cand = [j for j in free if rows[j]['e']['pos'] > r['e']['pos']] + \
                   [j for j in free if rows[j]['e']['pos'] < r['e']['pos']]
            ok = [j for j in cand[:64] if fits(r['names'], j)]
            if ok: free.remove(ok[0]); rows[ok[0]]['name'] = r['name']
    return f, rows


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'summary':
        d = summary(open(sys.argv[2], 'rb').read())
        for k, v in d.items(): print(k, v if k != 'names' else f'{len(v)}: {v[:16]}')
    elif cmd == 'list':
        f, rows = scan(sys.argv[2])
        for r in rows: print(f"{r['e']['pos']:x}\t{r['kind']}\t{r['e']['usz']}\t{r['name'] or ''}")
    elif cmd == 'extract':
        f, rows = scan(sys.argv[2]); out, rx = sys.argv[3], re.compile(sys.argv[4], re.I)
        for r in rows:
            if r['kind'] in ('uasset', 'uexp') and r['name'] and rx.search(r['name']):
                o = os.path.join(out, r['name'].lstrip('/') + '.' + r['kind'])
                os.makedirs(os.path.dirname(o), exist_ok=True)
                open(o, 'wb').write(read_data(f, r['e'])); print(o, r['e']['usz'])
