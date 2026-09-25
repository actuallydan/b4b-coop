using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Versions;

// Checks on assets extracted through the game's own pak layer (agent `dumpassets`, spike #16), with CUE4Parse in
// GAME_Back4Blood mode. docs/investigations/model-mods-paks.md.
// Build: vendor/dotnet + CUE4Parse copied to vendor/CUE4Parse (Apache-2.0, tested at cb72c6e):
//   DOTNET_ROOT=vendor/dotnet vendor/dotnet/dotnet build -c Release tools/modkit/assetcheck
//   A=tools/modkit/assetcheck/bin/Release/net10.0/assetcheck.dll
// assetcheck <dir containing Gobi/> <path prefix>...
//     parse every .uasset under each prefix (e.g. Gobi/Content/Characters/Heroes/Walker/), report exports plus
//     skeletal mesh / skeleton / texture / MIC details and the first package summary
// assetcheck --compare <Paks dir> <aes key> <liboodle .so> <dump dir containing Gobi/> <path prefix>
//     decode the same files offline from the retail paks and compare them byte for byte with the engine's dump
// assetcheck --paint <dir containing Gobi/> <Gobi/Content/...uasset> <out dir>
//     copy a texture's .uasset/.uexp/.ubulk with every mip overwritten by solid magenta blocks (DXT1/DXT5/BC7/BGRA8;
//     same sizes, so no header or offset changes): the #17 override test
if (args[0] == "--compare")
{
    // --compare <Paks dir> <aes hex> <oodle .so> <engine dump dir containing Gobi/> <path prefix>: decode the same files
    // offline from the retail paks and compare byte for byte with what the engine handed us.
    CUE4Parse.Compression.OodleHelper.Initialize(args[3]);
    var pp = new DefaultFileProvider(args[1], SearchOption.TopDirectoryOnly, true, new VersionContainer(EGame.GAME_Back4Blood));
    pp.Initialize();
    pp.SubmitKey(new CUE4Parse.UE4.Objects.Core.Misc.FGuid(), new CUE4Parse.Encryption.Aes.FAesKey(args[2]));
    int same = 0, diff = 0, missing = 0;
    foreach (var k in pp.Files.Keys.Where(k => k.StartsWith(args[5], StringComparison.OrdinalIgnoreCase)).OrderBy(k => k))
    {
        var f = pp.Files[k];
        var local = Path.Combine(args[4], f.Path);
        if (!File.Exists(local)) { missing++; if (missing < 5) Console.WriteLine($"missing {local}"); continue; }
        var a = pp.SaveAsset(f); var b = File.ReadAllBytes(local);
        if (a.AsSpan().SequenceEqual(b)) same++; else { diff++; Console.WriteLine($"DIFF {k} offline {a.Length} engine {b.Length}"); }
    }
    Console.WriteLine($"compare: {same} identical, {diff} different, {missing} not in the engine dump");
    return;
}
if (args[0] == "--paint")
{
    // --paint <dir containing Gobi/> <package path (Gobi/Content/...uasset)> <out dir>: copy the texture's
    // .uasset/.uexp/.ubulk to <out dir> (same relative paths) with every mip replaced by solid magenta blocks
    // (BC1/DXT1, BC3/DXT5, B8G8R8A8). Byte-level only: sizes, headers and offsets are unchanged.
    var pr = new DefaultFileProvider(Path.Combine(args[1], "Gobi"), SearchOption.AllDirectories, true, new VersionContainer(EGame.GAME_Back4Blood));
    pr.Initialize();
    var pk = pr.LoadPackage(args[2]);
    var tex = pk.GetExports().OfType<UTexture2D>().First();
    Console.WriteLine($"texture {tex.Name} {tex.Format} {tex.PlatformData.SizeX}x{tex.PlatformData.SizeY} mips {tex.PlatformData.Mips.Length}");
    byte[] block = tex.Format switch
    {
        CUE4Parse.UE4.Assets.Exports.Texture.EPixelFormat.PF_DXT1 => [0x1F, 0xF8, 0x1F, 0xF8, 0, 0, 0, 0],
        CUE4Parse.UE4.Assets.Exports.Texture.EPixelFormat.PF_DXT5 => [0xFF, 0xFF, 0, 0, 0, 0, 0, 0, 0x1F, 0xF8, 0x1F, 0xF8, 0, 0, 0, 0],
        CUE4Parse.UE4.Assets.Exports.Texture.EPixelFormat.PF_B8G8R8A8 => [0xFF, 0x00, 0xFF, 0xFF],
        // BC7 mode 6, both endpoints (255,1,255,255), all indices 0
        CUE4Parse.UE4.Assets.Exports.Texture.EPixelFormat.PF_BC7 => [0xC0, 0xFF, 0x1F, 0x00, 0xF8, 0xFF, 0xFF, 0xFF, 0x01, 0, 0, 0, 0, 0, 0, 0],
        _ => throw new Exception($"unsupported format {tex.Format}")
    };
    var baseName = args[2][..^".uasset".Length];
    var files = new Dictionary<string, byte[]>();
    foreach (var ext in new[] { ".uasset", ".uexp", ".ubulk" })
    {
        var p = Path.Combine(args[1], baseName + ext);
        if (File.Exists(p)) files[baseName + ext] = File.ReadAllBytes(p);
    }
    int painted = 0;
    for (int m = 0; m < tex.PlatformData.Mips.Length; m++)
    {
        var mip = tex.PlatformData.Mips[m];
        if (!mip.EnsureValidBulkData(null, m)) { Console.WriteLine($"mip {m}: no data"); continue; }
        var data = mip.BulkData!.Data!;
        bool found = false;
        foreach (var (name, bytes) in files)
        {
            var at = bytes.AsSpan().IndexOf(data);
            if (at < 0) continue;
            for (int i = 0; i < data.Length; i++) bytes[at + i] = block[i % block.Length];
            Console.WriteLine($"mip {m} {mip.SizeX}x{mip.SizeY} ({data.Length} bytes) painted in {name} at {at}");
            painted++; found = true; break;
        }
        if (!found) Console.WriteLine($"mip {m} {mip.SizeX}x{mip.SizeY}: data not found");
    }
    foreach (var (name, bytes) in files)
    {
        var o = Path.Combine(args[3], name);
        Directory.CreateDirectory(Path.GetDirectoryName(o)!);
        File.WriteAllBytes(o, bytes);
    }
    Console.WriteLine($"painted {painted} mips -> {args[3]}");
    return;
}
var root = args[0];
var provider = new DefaultFileProvider(Path.Combine(root, "Gobi"), SearchOption.AllDirectories, true,
    new VersionContainer(EGame.GAME_Back4Blood));
provider.Initialize();
Console.WriteLine($"files: {provider.Files.Count}");
bool summaryDone = false;
int ok = 0, fail = 0;
foreach (var pre in args.Skip(1))
{
    var pkgs = provider.Files.Keys.Where(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) &&
        k.StartsWith(pre, StringComparison.OrdinalIgnoreCase)).OrderBy(k => k).ToList();
    foreach (var path in pkgs)
    {
        try
        {
            var pkg = provider.LoadPackage(path);
            if (!summaryDone && pkg is Package p)
            {
                var s = p.Summary;
                Console.WriteLine($"summary: Tag {s.Tag:X} UE4 {s.FileVersionUE.FileVersionUE4} " +
                    $"Licensee {s.FileVersionLicenseeUE} PackageFlags {s.PackageFlags} NameCount {s.NameCount}");
                foreach (var cv in s.CustomVersionContainer.Versions) Console.WriteLine($"  custom {cv.Key} = {cv.Version}");
                summaryDone = true;
            }
            var exports = pkg.GetExports().ToList();
            Console.WriteLine($"OK {path}: {exports.Count} exports: {string.Join(", ", exports.Select(e => e.ExportType + " " + e.Name).Take(6))}");
            foreach (var e in exports)
            {
                switch (e)
                {
                    case USkeletalMesh m:
                        Console.WriteLine($"   SkeletalMesh LODs {m.LODModels?.Length} verts0 {m.LODModels?[0].NumVertices} sections0 {m.LODModels?[0].Sections.Length} " +
                            $"materials {m.Materials.Length} skeleton {m.Skeleton?.Name} bones {m.ReferenceSkeleton.FinalRefBoneInfo.Length}");
                        foreach (var mat in m.SkeletalMaterials) Console.WriteLine($"     material {mat.MaterialSlotName} -> {mat.MaterialInterface?.ResolvedObject?.GetPathName()}");
                        break;
                    case USkeleton sk:
                        Console.WriteLine($"   Skeleton bones {sk.BoneCount}");
                        break;
                    case UTexture2D t:
                        var mip = t.GetFirstMip();
                        Console.WriteLine($"   Texture2D {t.Format} {t.PlatformData.SizeX}x{t.PlatformData.SizeY} mips {t.PlatformData.Mips.Length} " +
                            $"firstmip {mip?.SizeX}x{mip?.SizeY} bytes {mip?.BulkData?.Data?.Length}");
                        break;
                    case UMaterialInstanceConstant mi:
                        Console.WriteLine($"   MIC parent {mi.Parent?.Name} textures {string.Join(", ", mi.TextureParameterValues.Select(v => v.ParameterValue.ResolvedObject?.GetPathName()))}");
                        break;
                }
            }
            ok++;
        }
        catch (Exception ex)
        {
            Console.WriteLine($"FAIL {path}: {ex.GetType().Name}: {ex.Message.Split('\n')[0]}");
            fail++;
        }
    }
}
Console.WriteLine($"parsed {ok} ok, {fail} failed");
