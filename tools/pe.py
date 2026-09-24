"""Tiny static-analysis helpers for Back4Blood.exe: string lookup, RIP-relative xrefs, call xrefs, disasm."""
import mmap, re, struct, sys, os
import pefile, capstone

EXE = os.environ.get("B4B_EXE", os.path.expanduser(
    "~/.local/share/Steam/steamapps/common/Back 4 Blood/Gobi/Binaries/Win64/Back4Blood.exe"))

class Image:
    def __init__(self, path=EXE):
        self.pe = pefile.PE(path, fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.data = open(path, "rb").read()
        self.secs = [(s.Name.rstrip(b"\0").decode(), self.base + s.VirtualAddress, s.Misc_VirtualSize,
                      s.PointerToRawData, s.SizeOfRawData) for s in self.pe.sections]
        t = next(s for s in self.secs if s[0] == ".text")
        self.text_va, self.text = t[1], self.data[t[3]:t[3] + t[4]]
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)

    def off2va(self, off):
        for n, va, vs, ro, rs in self.secs:
            if ro <= off < ro + rs: return va + off - ro
    def va2off(self, va):
        for n, sva, vs, ro, rs in self.secs:
            if sva <= va < sva + rs: return ro + va - sva
    def read(self, va, n): o = self.va2off(va); return self.data[o:o + n]

    def find_string(self, s, wide=True):
        pat = s.encode("utf-16le") if wide else s.encode()
        return [self.off2va(m.start()) for m in re.finditer(re.escape(pat), self.data)]

    def xrefs(self, target):
        """RIP-relative disp32 refs (lea/mov/call/jmp) to target within .text."""
        out, t = [], self.text
        for i in range(len(t) - 4):
            d = struct.unpack_from("<i", t, i)[0]
            # disp32 is followed by end of instruction for lea/mov reg,[rip+x] (common case)
            if self.text_va + i + 4 + d == target: out.append(self.text_va + i)
        return out

    def calls_to(self, target):
        out, t = [], self.text
        for m in re.finditer(b"[\xe8\xe9]", t):
            i = m.start()
            if i + 5 > len(t): break
            if self.text_va + i + 5 + struct.unpack_from("<i", t, i + 1)[0] == target:
                out.append((self.text_va + i, "call" if t[i] == 0xE8 else "jmp"))
        return out

    def func_start(self, va, maxback=0x4000):
        """Heuristic: walk back to the previous CC/C3 padding boundary."""
        o = self.va2off(va)
        for k in range(o - 1, o - maxback, -1):
            if self.data[k] == 0xCC and self.data[k + 1] != 0xCC: return self.off2va(k + 1)
        return None

    def dis(self, va, n=40, size=0x200):
        return list(self.md.disasm(self.read(va, size), va))[:n]
