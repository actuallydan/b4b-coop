"""Dump the custom-version registrations (GUID, LatestVersion, friendly name) compiled into Back4Blood.exe.

Every FCustomVersionRegistration static initializer loads the GUID from .rdata/.data with movups, stores the version
at [rsp+0x20] and the friendly-name TCHAR* at [rsp+0x28], then calls the registrar (0x1424B6A90, build 14216215).
B4B cooks unversioned packages (FileVersionUE4 = 0, no custom-version table), so these values are what the loader
assumes for every cooked package. Usage: .venv/bin/python tools/modkit/customversions.py
"""
import os, re, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from pe import Image

REGISTRAR = 0x1424B6A90


def wstr(im, va):
    b = im.read(va, 200)
    e = next(i for i in range(0, len(b) - 1, 2) if b[i:i + 2] == b'\0\0')
    return b[:e].decode('utf-16le', 'replace')


def rip_target(ins):
    m = re.search(r'rip \+ (0x[0-9a-f]+)', ins.op_str)
    return ins.address + ins.size + int(m.group(1), 16) if m else None


def main():
    im = Image()
    for site, _ in im.calls_to(REGISTRAR):
        st = im.func_start(site)
        guid = ver = name = None
        for ins in im.md.disasm(im.read(st, site - st + 5), st):
            ops = ins.op_str
            if ins.mnemonic in ('movups', 'movaps', 'movdqu', 'movdqa') and ops.startswith('xmm') and 'rip' in ops:
                g = rip_target(ins)
                if im.va2off(g) is not None: guid = struct.unpack('<4I', im.read(g, 16))
            elif ins.mnemonic == 'mov' and ops.startswith('dword ptr [rsp + 0x20]'):
                ver = int(ops.split(', ')[1], 16)
            elif ins.mnemonic == 'lea' and ops.startswith('rax, [rip'):
                name = wstr(im, rip_target(ins))
        gs = '-'.join('%08X' % x for x in guid) if guid else '?'
        print(f'{gs:35}  {ver if ver is not None else "?":>4}  {name}')


if __name__ == '__main__':
    main()
