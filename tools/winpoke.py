"""Windows-side (run under Proton): write bytes into every running Back4Blood.exe.  winpoke.py <hexaddr> <hexbytes>"""
import ctypes, ctypes.wintypes as wt, sys
sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import memprobe
k32 = memprobe.k32
addr, data = int(sys.argv[1], 16), bytes.fromhex(sys.argv[2])
snap = k32.CreateToolhelp32Snapshot(2, 0)
e = memprobe.PROCESSENTRY32W(); e.dwSize = ctypes.sizeof(e)
ok = k32.Process32FirstW(snap, ctypes.byref(e))
k32.OpenProcess.restype = wt.HANDLE
while ok:
    if e.szExeFile.lower() == "back4blood.exe":
        h = k32.OpenProcess(0x0008 | 0x0020 | 0x0010 | 0x0400, False, e.th32ProcessID)
        n = ctypes.c_size_t()
        w = k32.WriteProcessMemory(h, ctypes.c_void_p(addr), data, len(data), ctypes.byref(n)) if h else 0
        print(f"pid {e.th32ProcessID}: {'ok' if w else 'FAILED %d' % ctypes.get_last_error()}")
    ok = k32.Process32NextW(snap, ctypes.byref(e))
