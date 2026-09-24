"""Read-only live probe of a running Back4Blood.exe via /proc/<pid>/mem (process must be our descendant).

Addresses are from the current Steam build (buildid 14216215); image loads at its preferred base.
"""
import os, re, struct, sys

GUOBJECTARRAY = 0x14667C740
NAMEPOOL      = 0x146986C80   # FNamePool; Blocks[] at +0x10
CHUNK         = 64 * 1024
ITEM_SIZE     = 0x18
OBJECTS_XOR   = 0x8375

if os.name == "nt":
    import ctypes, ctypes.wintypes as wt
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    def find_pid():
        snap = k32.CreateToolhelp32Snapshot(2, 0)
        e = PROCESSENTRY32W(); e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        found = None
        while ok:
            if e.szExeFile.lower() == "back4blood.exe": found = e.th32ProcessID  # last one = the real game, not the stub
            ok = k32.Process32NextW(snap, ctypes.byref(e))
        k32.CloseHandle(snap)
        return found

    class Mem:
        def __init__(self, pid):
            k32.OpenProcess.restype = wt.HANDLE
            self.h = k32.OpenProcess(0x0010 | 0x0400, False, pid)  # VM_READ | QUERY_INFORMATION
            if not self.h: raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        def read(self, a, n):
            buf = ctypes.create_string_buffer(n); got = ctypes.c_size_t()
            if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(a), buf, n, ctypes.byref(got)):
                raise OSError(ctypes.get_last_error(), f"ReadProcessMemory {a:#x}+{n:#x}")
            return buf.raw[:got.value]
        def u64(self, a): return struct.unpack("<Q", self.read(a, 8))[0]
        def u32(self, a): return struct.unpack("<I", self.read(a, 4))[0]
        def i32(self, a): return struct.unpack("<i", self.read(a, 4))[0]
else:
    def find_pid():
        for p in os.listdir("/proc"):
            if not p.isdigit(): continue
            try: exe = open(f"/proc/{p}/cmdline", "rb").read().split(b"\0")[0]
            except OSError: continue
            if exe.endswith(b"Back4Blood.exe") and (exe.startswith(b"./") or b"Win64" in exe): return int(p)

    class Mem:
        def __init__(self, pid):
            self.f = open(f"/proc/{pid}/mem", "rb", buffering=0)
        def read(self, a, n):
            self.f.seek(a); return self.f.read(n)
        def u64(self, a): return struct.unpack("<Q", self.read(a, 8))[0]
        def u32(self, a): return struct.unpack("<I", self.read(a, 4))[0]
        def i32(self, a): return struct.unpack("<i", self.read(a, 4))[0]

class Game:
    def __init__(self, pid=None):
        self.m = Mem(pid or find_pid())
        self._names = {}
        self.objs = None

    def name(self, idx, number=0):
        if idx in self._names: s = self._names[idx]
        else:
            blk = self.m.u64(NAMEPOOL + 0x10 + (idx >> 18) * 8)
            e = blk + (idx & 0xFFFF) * 2
            hdr = struct.unpack("<H", self.m.read(e, 2))[0]
            wide, ln = hdr & 1, hdr >> 6
            raw = self.m.read(e + 2, ln * (2 if wide else 1))
            s = raw.decode("utf-16le" if wide else "latin-1")
            self._names[idx] = s
        return f"{s}_{number-1}" if number else s

    def fname_at(self, a):
        idx, num = struct.unpack("<II", self.m.read(a, 8))
        return self.name(idx, num)

    def load_objects(self):
        # B4B's FUObjectArray: MaxElements +0x38, NumElements +0x3C, MaxChunks +0x40, NumChunks +0x44,
        # Objects (FUObjectItem**) +0x48 stored XOR 0x8375.
        m, a = self.m, GUOBJECTARRAY
        maxel, numel, maxch, numch = struct.unpack("<iiii", m.read(a + 0x38, 0x10))
        objects = m.u64(a + 0x48) ^ OBJECTS_XOR
        self.objs = []
        for c in range(numch):
            chunk = m.u64(objects + c * 8)
            n = min(CHUNK, numel - c * CHUNK)
            raw = m.read(chunk, n * ITEM_SIZE)
            for i in range(n):
                self.objs.append(struct.unpack_from("<Q", raw, i * ITEM_SIZE + 8)[0])  # item: Flags, ClusterRoot, Object, Serial
        return numel

    def obj_name(self, o):  return self.fname_at(o + 0x18)
    def obj_class(self, o): return self.m.u64(o + 0x10)
    def obj_outer(self, o): return self.m.u64(o + 0x20)
    def full_name(self, o):
        parts, x = [], o
        while x: parts.append(self.obj_name(x)); x = self.obj_outer(x)
        return f"{self.obj_name(self.obj_class(o))} " + ".".join(reversed(parts))

    def find(self, name, cls=None):
        out = []
        for o in self.objs:
            if not o: continue
            try:
                if self.obj_name(o) == name and (cls is None or self.obj_name(self.obj_class(o)) == cls): out.append(o)
            except Exception: pass
        return out

    def describe_ptr(self, p):
        if p in self._objset: return self.full_name(p)
        return None

    def annotate(self, o, size=0x140):
        self._objset = getattr(self, "_objset", None) or set(x for x in self.objs if x)
        raw = self.m.read(o, size)
        for off in range(0, size, 8):
            q = struct.unpack_from("<Q", raw, off)[0]
            lo, hi = struct.unpack_from("<ii", raw, off)
            d = self.describe_ptr(q)
            print(f"  +{off:#05x}: {q:#018x}  i32=({lo},{hi})  {d or ''}")

if __name__ == "__main__":
    g = Game(int(sys.argv[1]) if len(sys.argv) > 1 else None)
    print("objects:", g.load_objects())
    for n in sys.argv[2:] or ["Object", "Actor"]:
        for o in g.find(n, "Class"):
            print(g.full_name(o), hex(o)); g.annotate(o)
