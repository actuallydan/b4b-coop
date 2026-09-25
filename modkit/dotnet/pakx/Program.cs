using System.Security.Cryptography;
using System.Text.RegularExpressions;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Versions;

// pakx: offline reader for Back 4 Blood's retail paks (no game running). CUE4Parse (NuGet, GAME_Back4Blood) plus
// the pak AES key, which the modder supplies (modkit/README.md). Same bytes as the dev agent's `dumpassets`
// (453/453 in docs/investigations/model-mods-paks.md). Normally run through modkit/b4bmod.py.
const string Usage = @"pakx key     --paks <Paks dir> --aes <hex>                       check the key against the pak indexes
pakx list    --paks <Paks dir> --aes <hex> [<regex>]             every file path (size, path), TSV with the pak name
pakx extract --paks <Paks dir> --aes <hex> --out <dir> [--oodle <lib>] <regex>
                                                                 write every matching file to <dir>/Gobi/Content/...
<regex> is matched (case-insensitive) against paths like Gobi/Content/Characters/Heroes/Holly/.../x_SKM.uasset.
Oodle: CUE4Parse decompresses with its built-in managed decoder (OodleSharp, MIT). --oodle <oo2core/oodle-data-shared
library> uses a native Oodle library instead; pakx never downloads one.";

try { return Run(args); }
catch (UsageException e) { Console.Error.WriteLine($"pakx: {e.Message}\n\n{Usage}"); return 2; }
catch (PakxException e) { Console.Error.WriteLine($"pakx: {e.Message}"); return 1; }

static int Run(string[] args)
{
    if (args.Length == 0 || args[0] is "-h" or "--help" or "help") { Console.WriteLine(Usage); return 0; }
    string cmd = args[0];
    var named = new Dictionary<string, string>();
    var pos = new List<string>();
    if (Directory.Exists(cmd) && args.Length >= 5)
    {
        // legacy form: pakx <Paks dir> <aes> <oodle lib> <out dir> <regex> [--list]
        named["--paks"] = args[0]; named["--aes"] = args[1]; named["--oodle"] = args[2]; named["--out"] = args[3];
        pos.Add(args[4]);
        cmd = args.Length > 5 && args[5] == "--list" ? "list" : "extract";
    }
    else
    {
        for (int i = 1; i < args.Length; i++)
        {
            if (args[i] is "--paks" or "--aes" or "--out" or "--oodle")
            {
                if (i + 1 >= args.Length) throw new UsageException($"{args[i]} needs a value");
                named[args[i]] = args[++i];
            }
            else pos.Add(args[i]);
        }
    }
    string paks = named.GetValueOrDefault("--paks") ?? throw new UsageException("--paks <Paks dir> is required");
    if (!Directory.Exists(paks)) throw new PakxException($"no such folder: {paks}");
    byte[] key = ParseKey(named.GetValueOrDefault("--aes") ?? throw new UsageException("--aes <key> is required"));

    switch (cmd)
    {
        case "key":
        {
            var (ok, n) = CheckKey(paks, key);
            Console.WriteLine($"key OK: it decrypts all {ok} encrypted pak indexes in {paks} (index SHA1 matches)");
            return 0;
        }
        case "list":
        case "extract":
        {
            CheckKey(paks, key);   // "wrong key" before CUE4Parse gets to it
            Regex re = new(pos.Count > 0 ? pos[0] : (cmd == "list" ? "." : throw new UsageException("<regex> is required")),
                           RegexOptions.IgnoreCase);
            string outDir = cmd == "extract" ? named.GetValueOrDefault("--out") ?? throw new UsageException("--out <dir> is required") : null;
            if (cmd == "extract" && named.TryGetValue("--oodle", out var oodle))
            {
                // CUE4Parse downloads a library when the path doesn't exist: never let it
                if (!File.Exists(oodle)) throw new PakxException($"no Oodle library at {oodle}");
                CUE4Parse.Compression.OodleHelper.Initialize(oodle);
            }
            var pp = new DefaultFileProvider(paks, SearchOption.TopDirectoryOnly, new VersionContainer(EGame.GAME_Back4Blood),
                                             StringComparer.OrdinalIgnoreCase);
            pp.Initialize();
            pp.SubmitKey(new CUE4Parse.UE4.Objects.Core.Misc.FGuid(), new CUE4Parse.Encryption.Aes.FAesKey(key));
            int n = 0;
            long bytes = 0;
            foreach (var k in pp.Files.Keys.OrderBy(k => k, StringComparer.Ordinal))
            {
                var f = pp.Files[k];
                if (!re.IsMatch(f.Path)) continue;
                n++;
                if (cmd == "list")
                {
                    string pak = f is CUE4Parse.UE4.VirtualFileSystem.VfsEntry v ? Path.GetFileName(v.Vfs.Name) : "";
                    Console.WriteLine($"{f.Path}\t{f.Size}\t{pak}");
                    continue;
                }
                var outp = Path.Combine(outDir, f.Path.Replace('/', Path.DirectorySeparatorChar));
                Directory.CreateDirectory(Path.GetDirectoryName(outp)!);
                var data = pp.SaveAsset(f);
                File.WriteAllBytes(outp, data);
                bytes += data.Length;
            }
            Console.Error.WriteLine(cmd == "list" ? $"{n} files" : $"{n} files, {bytes / 1048576.0:F1} MB -> {outDir}");
            return 0;
        }
        default: throw new UsageException($"unknown command {cmd}");
    }
}

static byte[] ParseKey(string s)
{
    s = s.Trim();
    if (s.StartsWith("0x", StringComparison.OrdinalIgnoreCase)) s = s[2..];
    if (s.Length != 64 || !s.All(Uri.IsHexDigit))
        throw new PakxException("the AES key must be 64 hex digits (0x... as in the community key lists)");
    return Convert.FromHexString(s);
}

// B4B pak footer (222 bytes): Version u32, Magic u32 0x18772, KeyGuid[16], bEncryptedIndex u8, IndexHash[20] (SHA1 of
// the plain index), IndexSize u64, IndexOffset u64, ... (docs/investigations/model-mods-paks.md §3). The index is
// AES-256-ECB, so a key is right exactly when SHA1(decrypt(index)) == IndexHash.
static (int ok, int total) CheckKey(string paks, byte[] key)
{
    using var aes = Aes.Create();
    aes.Key = key;
    int ok = 0, seen = 0;
    foreach (var p in Directory.GetFiles(paks, "*.pak").OrderBy(p => p, StringComparer.Ordinal))
    {
        using var fs = File.OpenRead(p);
        if (fs.Length < 222) continue;
        var ft = new byte[222];
        fs.Seek(-222, SeekOrigin.End); fs.ReadExactly(ft);
        if (BitConverter.ToUInt32(ft, 4) != 0x18772) throw new PakxException($"{Path.GetFileName(p)} is not a Back 4 Blood pak (footer magic)");
        seen++;
        if (ft[24] == 0) continue;
        long size = BitConverter.ToInt64(ft, 45), off = BitConverter.ToInt64(ft, 53);
        if (size <= 0 || size % 16 != 0 || off < 0 || off + size > fs.Length) throw new PakxException($"{Path.GetFileName(p)}: bad index bounds");
        var idx = new byte[size];
        fs.Seek(off, SeekOrigin.Begin); fs.ReadExactly(idx);
        var plain = aes.DecryptEcb(idx, PaddingMode.None);
        if (!SHA1.HashData(plain).AsSpan().SequenceEqual(ft.AsSpan(25, 20)))
            throw new PakxException($"wrong AES key: it does not decrypt {Path.GetFileName(p)} (index SHA1 mismatch). " +
                                    "Back 4 Blood has one key for every copy of the game; see the modkit README, \"The AES key\".");
        ok++;
    }
    if (seen == 0) throw new PakxException($"no .pak files in {paks} (expected <game>/Gobi/Content/Paks)");
    return (ok, seen);
}

class UsageException(string m) : Exception(m);
class PakxException(string m) : Exception(m);
