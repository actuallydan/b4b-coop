// Cooked UTexture2D native data (UE 4.25 layout, which B4B keeps; docs/investigations/texture-mods.md §2).
// The export's tagged properties and object GUID are UAssetAPI's; what follows (NormalExport.Extras) is:
//   FStripDataFlags (UTexture) 2 B, FStripDataFlags (UTexture2D) 2 B, bCooked u32,
//   { FName PixelFormat, int64 SkipOffset (absolute offset of the next FName), FTexturePlatformData }*, FName None,
//   then anything else (kept as is).
// FTexturePlatformData: SizeX i32, SizeY i32, PackedData u32 (NumSlices | bHasOptData<<30 | bIsCubemap<<31),
//   FString PixelFormat, [FOptTexturePlatformData 8 B], FirstMipToSerialize i32, NumMips i32,
//   { bCooked u32, FByteBulkData, SizeX i32, SizeY i32, SizeZ i32 }*, bIsVirtual u32.
// FByteBulkData header: Flags u32, ElementCount i32|i64, SizeOnDisk i32|i64 (i64 with BULKDATA_Size64Bit),
//   OffsetInFile i64; then the payload if it is inline. Payloads with BULKDATA_PayloadInSeperateFile live in the
//   package's .ubulk at OffsetInFile (with BULKDATA_NoOffsetFixUp the offset is relative to the .ubulk start).
using System.Text;
using UAssetAPI;
using UAssetAPI.ExportTypes;

namespace B4BMod;

[Flags]
enum BulkFlags : uint
{
    PayloadAtEndOfFile = 0x1, CompressedZlib = 0x2, Unused = 0x20, ForceInlinePayload = 0x40,
    PayloadInSeperateFile = 0x100, OptionalPayload = 0x800, Size64Bit = 0x2000, NoOffsetFixUp = 0x10000,
}

class Mip
{
    public uint Cooked;
    public BulkFlags Flags;
    public long OffsetInFile;          // as stored
    public byte[] Data;                // payload (from the export or the .ubulk)
    public int SizeX, SizeY, SizeZ;
    public bool InUbulk => (Flags & BulkFlags.PayloadInSeperateFile) != 0;
    public bool Inline => (Flags & (BulkFlags.PayloadAtEndOfFile | BulkFlags.PayloadInSeperateFile)) == 0;
}

class PlatformData
{
    public int FormatNameIndex, FormatNameNumber;
    public string PixelFormat;         // e.g. PF_DXT1, PF_BC5, PF_BC7, PF_B8G8R8A8, PF_G8
    public int SizeX, SizeY;
    public uint Packed;
    public byte[] OptData;             // 8 bytes or null
    public int FirstMip;
    public List<Mip> Mips = new();
    public uint IsVirtual;
}

class Texture2DData
{
    public byte[] Prefix;              // strip flags + bCooked
    public List<PlatformData> Platforms = new();
    public byte[] Suffix;              // FName None and anything after it
    public string UbulkPath;

    // Absolute offset of Extras[0] in the combined .uasset+.uexp stream (what the engine's Tell() returns).
    public static long ExtrasBase(UAsset a, Export e) => e.SerialOffset + e.SerialSize - e.Extras.Length;

    public static Texture2DData Read(UAsset a, NormalExport e, string uassetPath)
    {
        var t = new Texture2DData();
        var ubulkPath = Path.ChangeExtension(uassetPath, ".ubulk");
        byte[] ubulk = File.Exists(ubulkPath) ? File.ReadAllBytes(ubulkPath) : null;
        t.UbulkPath = ubulk != null ? ubulkPath : null;
        long baseOff = ExtrasBase(a, e);
        var r = new BinaryReader(new MemoryStream(e.Extras));
        r.BaseStream.Position = 8;     // 2 x FStripDataFlags + bCooked
        uint cooked = BitConverter.ToUInt32(e.Extras, 4);
        if (cooked != 1) throw new Exception($"Texture2D not cooked (bCooked={cooked})");
        t.Prefix = e.Extras[..8];
        while (true)
        {
            long at = r.BaseStream.Position;
            int ni = r.ReadInt32(), nn = r.ReadInt32();
            string fname = a.GetNameReference(ni).ToString();
            if (fname == "None") { t.Suffix = e.Extras[(int)at..]; break; }
            var p = new PlatformData { FormatNameIndex = ni, FormatNameNumber = nn };
            long skip = r.ReadInt64();
            p.SizeX = r.ReadInt32(); p.SizeY = r.ReadInt32(); p.Packed = r.ReadUInt32();
            p.PixelFormat = ReadFString(r);
            if ((p.Packed & (1u << 30)) != 0) p.OptData = r.ReadBytes(8);
            p.FirstMip = r.ReadInt32();
            int n = r.ReadInt32();
            for (int i = 0; i < n; i++)
            {
                var m = new Mip { Cooked = r.ReadUInt32(), Flags = (BulkFlags)r.ReadUInt32() };
                bool big = (m.Flags & BulkFlags.Size64Bit) != 0;
                long count = big ? r.ReadInt64() : r.ReadInt32();
                long onDisk = big ? r.ReadInt64() : r.ReadInt32();
                m.OffsetInFile = r.ReadInt64();
                if ((m.Flags & BulkFlags.CompressedZlib) != 0) throw new Exception("zlib-compressed mip: not supported");
                if (count != onDisk) throw new Exception($"mip {i}: element count {count} != size on disk {onDisk}");
                if (m.Inline) m.Data = r.ReadBytes((int)onDisk);
                else if (m.InUbulk)
                {
                    if (ubulk == null) throw new Exception($"mip {i} is in the .ubulk, but {ubulkPath} is missing");
                    if ((m.Flags & BulkFlags.NoOffsetFixUp) == 0) throw new Exception($"mip {i}: .ubulk payload without NoOffsetFixUp");
                    m.Data = ubulk.AsSpan((int)m.OffsetInFile, (int)onDisk).ToArray();
                }
                else throw new Exception($"mip {i}: payload at the end of the .uexp (flags {m.Flags}): not supported");
                m.SizeX = r.ReadInt32(); m.SizeY = r.ReadInt32(); m.SizeZ = r.ReadInt32();
                p.Mips.Add(m);
            }
            p.IsVirtual = r.ReadUInt32();
            if (p.IsVirtual != 0) throw new Exception("virtual texture: not supported");
            if (baseOff + r.BaseStream.Position != skip)
                throw new Exception($"SkipOffset {skip} != parsed end {baseOff + r.BaseStream.Position}: unknown layout");
            t.Platforms.Add(p);
        }
        return t;
    }

    static string ReadFString(BinaryReader r)
    {
        int n = r.ReadInt32();
        if (n == 0) return "";
        if (n > 0) return Encoding.ASCII.GetString(r.ReadBytes(n)).TrimEnd('\0');
        return Encoding.Unicode.GetString(r.ReadBytes(-n * 2)).TrimEnd('\0');
    }

    static void WriteFString(BinaryWriter w, string s)
    {
        var b = Encoding.ASCII.GetBytes(s + "\0");
        w.Write(b.Length); w.Write(b);
    }

    // Serialize the native data for an Extras that starts at absolute offset baseOff. The .ubulk is rebuilt from
    // every mip that lives there, in order (the cooker writes them the same way: largest first, no padding).
    public (byte[] extras, byte[] ubulk) Write(long baseOff)
    {
        var ms = new MemoryStream(); var w = new BinaryWriter(ms);
        var ub = new MemoryStream();
        w.Write(Prefix);
        foreach (var p in Platforms)
        {
            w.Write(p.FormatNameIndex); w.Write(p.FormatNameNumber);
            long skipAt = ms.Position; w.Write(0L);
            w.Write(p.SizeX); w.Write(p.SizeY); w.Write(p.Packed);
            WriteFString(w, p.PixelFormat);
            if (p.OptData != null) w.Write(p.OptData);
            w.Write(p.FirstMip); w.Write(p.Mips.Count);
            foreach (var m in p.Mips)
            {
                w.Write(m.Cooked); w.Write((uint)m.Flags);
                bool big = (m.Flags & BulkFlags.Size64Bit) != 0;
                if (big) { w.Write((long)m.Data.Length); w.Write((long)m.Data.Length); }
                else { w.Write(m.Data.Length); w.Write(m.Data.Length); }
                if (m.Inline)
                {
                    // the cooker stores the absolute offset of the inline payload (right after this field)
                    m.OffsetInFile = baseOff + ms.Position + 8;
                    w.Write(m.OffsetInFile); w.Write(m.Data);
                }
                else
                {
                    m.OffsetInFile = ub.Position;
                    w.Write(m.OffsetInFile); ub.Write(m.Data);
                }
                w.Write(m.SizeX); w.Write(m.SizeY); w.Write(m.SizeZ);
            }
            w.Write(p.IsVirtual);
            long end = baseOff + ms.Position;
            ms.Position = skipAt; w.Write(end); ms.Position = ms.Length;
        }
        w.Write(Suffix);
        return (ms.ToArray(), ub.Length > 0 ? ub.ToArray() : null);
    }

    public static (int blockW, int blockBytes) FormatInfo(string pf) => pf switch
    {
        "PF_DXT1" or "PF_BC4" => (4, 8),
        "PF_DXT3" or "PF_DXT5" or "PF_BC5" or "PF_BC7" => (4, 16),
        "PF_B8G8R8A8" => (1, 4),
        "PF_G8" => (1, 1),
        _ => throw new Exception($"pixel format {pf} not supported"),
    };

    public static long MipBytes(string pf, int w, int h)
    {
        var (bw, bb) = FormatInfo(pf);
        return (long)((w + bw - 1) / bw) * ((h + bw - 1) / bw) * bb;
    }
}
