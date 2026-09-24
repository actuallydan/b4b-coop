#!/usr/bin/env python3
"""Dump one app's section from Steam's binary appcache/appinfo.vdf (format v28/v29).

Usage: appinfo.py [appid] [key/path ...]      e.g.  appinfo.py 924970 config/launch
Default appid 924970 (Back 4 Blood); default path prints the whole app. Read-only.
"""
import os, struct, sys, json

PATH = os.path.expanduser('~/.local/share/Steam/appcache/appinfo.vdf')

def load(path=PATH):
    d = open(path, 'rb').read()
    magic, universe = struct.unpack_from('<II', d, 0)
    ver = magic & 0xff
    if ver not in (0x28, 0x29): raise SystemExit(f'unknown appinfo magic {magic:#x}')
    pos, strings = 8, None
    if ver == 0x29:
        st, = struct.unpack_from('<q', d, 8); pos = 16
        n, = struct.unpack_from('<I', d, st); p = st + 4; strings = []
        for _ in range(n):
            e = d.index(b'\0', p); strings.append(d[p:e].decode('utf-8', 'replace')); p = e + 1

    def cstr(p):
        e = d.index(b'\0', p); return d[p:e].decode('utf-8', 'replace'), e + 1

    def kv(p):
        out = {}
        while True:
            t = d[p]; p += 1
            if t == 8: return out, p
            if strings is not None: k = strings[struct.unpack_from('<I', d, p)[0]]; p += 4
            else: k, p = cstr(p)
            if t == 0: v, p = kv(p)
            elif t == 1: v, p = cstr(p)
            elif t in (2, 4, 6): v = struct.unpack_from('<i', d, p)[0]; p += 4
            elif t == 3: v = struct.unpack_from('<f', d, p)[0]; p += 4
            elif t in (7, 10): v = struct.unpack_from('<Q' if t == 7 else '<q', d, p)[0]; p += 8
            else: raise ValueError(f'type {t} at {p:#x}')
            out[k] = v

    apps = {}
    while True:
        appid, = struct.unpack_from('<I', d, pos)
        if appid == 0: break
        size, = struct.unpack_from('<I', d, pos + 4)
        body = pos + 8 + 4 + 4 + 8 + 20 + 4 + 20  # infostate, last_updated, token, sha1, changenumber, binary sha1
        apps[appid] = (body, pos + 8 + size)
        pos += 8 + size
    return apps, kv

def main():
    appid = int(sys.argv[1]) if len(sys.argv) > 1 else 924970
    apps, kv = load()
    v = kv(apps[appid][0])[0]
    for path in (sys.argv[2:] or ['']):
        node = v
        for k in filter(None, path.split('/')): node = node[k]
        print(json.dumps(node, indent=1, ensure_ascii=False))

if __name__ == '__main__':
    main()
