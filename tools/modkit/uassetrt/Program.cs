// UAssetAPI round-trip / edit test on B4B cooked packages (unversioned, UE 4.25).
// Build: vendor/dotnet (dotnet-install.sh --channel 10.0) + UAssetAPI cloned to vendor/UAssetAPI (MIT, tested at 3228c1e).
// Run:   DOTNET_ROOT=vendor/dotnet vendor/dotnet/dotnet tools/modkit/uassetrt/bin/Release/net10.0/uassetrt.dll check <dir>
// uassetrt check <in.uasset>...                       parse, report export types, byte-identical re-write?
// uassetrt setscalar <in.uasset> <out.uasset> <param> <value>   edit an MIC scalar parameter
// uassetrt settex <in.uasset> <out.uasset> <param> <newTexturePackagePath>   repoint an MIC texture parameter
using UAssetAPI;
using UAssetAPI.ExportTypes;
using UAssetAPI.PropertyTypes.Objects;
using UAssetAPI.PropertyTypes.Structs;
using UAssetAPI.UnrealTypes;

UAsset Load(string p) => new UAsset(p, EngineVersion.VER_UE4_25);

switch (args[0])
{
    case "check":
        foreach (var path in args.Skip(1).SelectMany<string, string>(x => Directory.Exists(x)
                     ? Directory.GetFiles(x, "*.uasset", SearchOption.AllDirectories).OrderBy(s => s).ToArray() : new[] { x }))
        {
            try
            {
                var a = Load(path);
                var kinds = a.Exports.GroupBy(e => e.GetType().Name).Select(g => $"{g.Key}x{g.Count()}");
                long extras = a.Exports.Sum(e => (long)(e.Extras?.Length ?? 0));
                var main = a.Exports.FirstOrDefault(e => e.bIsAsset);
                Console.WriteLine($"{Path.GetFileName(path)}: {string.Join(",", kinds)}; asset={main?.GetExportClassType()} " +
                                  $"extras(unparsed tail bytes)={extras}; binaryEqual={a.VerifyBinaryEquality()}");
            }
            catch (Exception ex) { Console.WriteLine($"{Path.GetFileName(path)}: FAIL {ex.GetType().Name}: {ex.Message}"); }
        }
        break;
    case "setscalar":
    {
        var a = Load(args[1]);
        var mic = a.Exports.OfType<NormalExport>().First(e => e.bIsAsset);
        var arr = (ArrayPropertyData)mic["ScalarParameterValues"];
        foreach (StructPropertyData s in arr.Value)
        {
            var info = (StructPropertyData)s["ParameterInfo"];
            var name = ((NamePropertyData)info["Name"]).Value.ToString();
            if (name == args[3])
            {
                var v = (FloatPropertyData)s["ParameterValue"];
                Console.WriteLine($"{name}: {v.Value} -> {args[4]}");
                v.Value = float.Parse(args[4], System.Globalization.CultureInfo.InvariantCulture);
            }
        }
        a.Write(args[2]);
        break;
    }
    case "settex":
    {
        var a = Load(args[1]);
        var mic = a.Exports.OfType<NormalExport>().First(e => e.bIsAsset);
        var arr = (ArrayPropertyData)mic["TextureParameterValues"];
        foreach (StructPropertyData s in arr.Value)
        {
            var info = (StructPropertyData)s["ParameterInfo"];
            if (((NamePropertyData)info["Name"]).Value.ToString() != args[3]) continue;
            var obj = (ObjectPropertyData)s["ParameterValue"];
            var old = obj.Value.ToImport(a);
            var oldPkg = a.Imports[-old.OuterIndex.Index - 1];
            Console.WriteLine($"{args[3]}: {oldPkg.ObjectName}.{old.ObjectName} -> {args[4]}");
            // add package + object imports for the new texture
            var pkgPath = args[4]; var objName = pkgPath[(pkgPath.LastIndexOf('/') + 1)..];
            a.AddNameReference(new FString(pkgPath)); a.AddNameReference(new FString(objName));
            var pkgImp = a.AddImport(new Import("/Script/CoreUObject", "Package", FPackageIndex.FromRawIndex(0), pkgPath, false, a));
            var texImp = a.AddImport(new Import("/Script/Engine", "Texture2D", pkgImp, objName, false, a));
            obj.Value = texImp;
        }
        a.Write(args[2]);
        break;
    }
}
