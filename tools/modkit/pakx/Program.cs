using System.Text.RegularExpressions;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Versions;

// Offline extraction from the retail paks (no game running), CUE4Parse GAME_Back4Blood + the community AES key.
// Same bytes as the agent's `dumpassets` (verified 453/453 in model-mods-paks.md).
//   pakx <Paks dir> <aes hex> <liboodle .so> <out dir> <regex over pak paths, e.g. 'Heroes/Holly/.*_SKM\.'> [--list]
// Writes <out dir>/Gobi/Content/... with every sibling (.uasset/.uexp/.ubulk) of each matching package.
CUE4Parse.Compression.OodleHelper.Initialize(args[2]);
var pp = new DefaultFileProvider(args[0], SearchOption.TopDirectoryOnly, true, new VersionContainer(EGame.GAME_Back4Blood));
pp.Initialize();
pp.SubmitKey(new CUE4Parse.UE4.Objects.Core.Misc.FGuid(), new CUE4Parse.Encryption.Aes.FAesKey(args[1]));
var re = new Regex(args[4], RegexOptions.IgnoreCase);
bool list = args.Length > 5 && args[5] == "--list";
int n = 0;
foreach (var k in pp.Files.Keys.OrderBy(k => k))
{
    var f = pp.Files[k];
    if (!re.IsMatch(f.Path)) continue;
    n++;
    if (list) { Console.WriteLine($"{f.Size,10} {f.Path}"); continue; }
    var outp = Path.Combine(args[3], f.Path);
    Directory.CreateDirectory(Path.GetDirectoryName(outp)!);
    File.WriteAllBytes(outp, pp.SaveAsset(f));
}
Console.Error.WriteLine($"{n} files");
