#!/usr/bin/env python3
"""b4bmod: texture and material-instance mods for Back 4 Blood without the UE editor (#18/#21, epic #23).

Front end for the .NET tool in tools/modkit/b4bmod/ (builds it on first use) plus asset search and packing.
Step-by-step guide: docs/investigations/texture-mods.md.

  b4bmod.py setup                                 fetch .NET 10 SDK + UAssetAPI into vendor/, build the tool
  b4bmod.py find <regex> [--paks DIR]             search every game file path (offline, from the pak indexes)
  b4bmod.py extract <glob>                        dev build + running game: agent `dumpassets` (e.g.
                                                  '/Game/Characters/Heroes/Walker/*')
  b4bmod.py info <asset>...                       texture format/size/mips, MI parameters, what a mesh uses
  b4bmod.py tree <asset>                          mesh -> material instances -> textures
  b4bmod.py export <asset> <out.png> [--mip N]    texture -> PNG (paint over this)
  b4bmod.py texture <asset> <in.png> -o <moddir> [--resize] [--quality fast|balanced|best]
  b4bmod.py mi <asset> [list]
  b4bmod.py mi <asset> set <param> <value> [set <param> <value>...] [parent <path>] -o <moddir>
  b4bmod.py pak <moddir> <out.pak>                plain mod pak (tools/b4bpak.py); dev ini modpaks=<its folder>

<asset> is a game path (/Game/Characters/Heroes/Walker/.../Walker_Torso_00_A_BC_T) looked up under --src (default
$B4B_EXTRACT or ~/.local/share/b4b-coop/extract, where `dumpassets` writes), or a .uasset file. <moddir> gets the
edited cooked files laid out like the game (<moddir>/Gobi/Content/...): that folder is what goes into a pak or an
add-on. Never commit extracted or edited game files.
"""
import os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
VENDOR = os.path.join(ROOT, "vendor")
DOTNET_DIR = os.path.join(VENDOR, "dotnet")
DOTNET = os.path.join(DOTNET_DIR, "dotnet.exe" if os.name == "nt" else "dotnet")
UASSETAPI = os.path.join(VENDOR, "UAssetAPI")
UASSETAPI_COMMIT = "3228c1e"          # tested; MIT, github.com/atenfyr/UAssetAPI
PROJ = os.path.join(HERE, "b4bmod")
DLL = os.path.join(PROJ, "bin", "Release", "net10.0", "b4bmod.dll")
# Community AES key of B4B's pak indexes (docs/investigations/model-mods-paks.md): only needed to list file names.
PAK_AES = "0208250257E8EA16828509DEBF23D703A5B509FE4F15F33F11BEE4BAB1F97CFD"
DATA = os.path.join(os.path.expanduser("~"), ".local", "share", "b4b-coop")
DEFAULT_PAKS = (r"C:\Program Files (x86)\Steam\steamapps\common\Back 4 Blood\Gobi\Content\Paks" if os.name == "nt" else
                os.path.join(os.path.expanduser("~"), ".local/share/Steam/steamapps/common/Back 4 Blood/Gobi/Content/Paks"))


def env():
    e = dict(os.environ, DOTNET_ROOT=DOTNET_DIR, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")
    return e


def setup(force_build=False):
    if not os.path.exists(DOTNET):
        print("fetching the .NET 10 SDK into vendor/dotnet ...")
        if os.name == "nt":
            ps = ("& ([scriptblock]::Create((Invoke-WebRequest -UseBasicParsing https://dot.net/v1/dotnet-install.ps1))) "
                  f"-Channel 10.0 -InstallDir '{DOTNET_DIR}'")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
        else:
            subprocess.run(f"curl -sSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel 10.0 --install-dir '{DOTNET_DIR}'",
                           shell=True, check=True)
    if not os.path.isdir(os.path.join(UASSETAPI, "UAssetAPI")):
        print(f"fetching UAssetAPI ({UASSETAPI_COMMIT}) into vendor/UAssetAPI ...")
        subprocess.run(["git", "clone", "-q", "https://github.com/atenfyr/UAssetAPI", UASSETAPI], check=True)
        subprocess.run(["git", "-C", UASSETAPI, "checkout", "-q", UASSETAPI_COMMIT], check=True)
    srcs = [os.path.join(PROJ, f) for f in os.listdir(PROJ) if f.endswith((".cs", ".csproj"))]
    if force_build or not os.path.exists(DLL) or max(map(os.path.getmtime, srcs)) > os.path.getmtime(DLL):
        print("building tools/modkit/b4bmod ...", file=sys.stderr)
        r = subprocess.run([DOTNET, "build", "-c", "Release", "-v", "q", "-nologo", PROJ], env=env(),
                           capture_output=True, text=True)
        if r.returncode:
            sys.stderr.write(r.stdout + r.stderr)
            raise SystemExit("build failed")


def run_tool(args):
    setup()
    return subprocess.run([DOTNET, DLL] + args, env=env()).returncode


def find(pattern, paks):
    """Every file name in the retail paks (decrypted indexes), cached; print the ones matching <regex>."""
    cache = os.path.join(DATA, "listing", "files.txt")
    if not os.path.exists(cache):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import b4bpak   # needs the `cryptography` package
        names = []
        for p in sorted(os.listdir(paks)):
            if not p.endswith(".pak"):
                continue
            _, mount, entries = b4bpak.read_index(os.path.join(paks, p), PAK_AES)
            base = mount.replace("../../../", "")
            names += [f"{base}{e['name']}\t{p}" for e in entries]
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w") as f:
            f.write("\n".join(sorted(names)) + "\n")
        print(f"(indexed {len(names)} files from {paks} into {cache})", file=sys.stderr)
    rx = re.compile(pattern, re.I)
    n = 0
    for line in open(cache):
        name, pak = line.rstrip("\n").split("\t")
        if not name.endswith(".uasset"):
            continue
        game = "/Game/" + name[len("Gobi/Content/"):-len(".uasset")] if name.startswith("Gobi/Content/") else name[:-7]
        if not rx.search(game):
            continue
        print(f"{game}\t{pak}")
        n += 1
    print(f"{n} assets", file=sys.stderr)


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = a[0]
    if cmd == "setup":
        setup(force_build=True)
        print("ready")
        return 0
    if cmd == "find":
        paks = DEFAULT_PAKS
        if "--paks" in a:
            i = a.index("--paks"); paks = a[i + 1]; del a[i:i + 2]
        if len(a) != 2:
            raise SystemExit("usage: b4bmod.py find <regex> [--paks DIR]")
        find(a[1], paks)
        return 0
    if cmd == "extract":
        if len(a) < 2:
            raise SystemExit("usage: b4bmod.py extract <glob> [windows outdir]")
        return subprocess.run([sys.executable, os.path.join(ROOT, "tools", "b4b.py"), "dumpassets"] + a[1:]).returncode
    if cmd == "pak":
        if len(a) != 3:
            raise SystemExit("usage: b4bmod.py pak <moddir> <out.pak>")
        return subprocess.run([sys.executable, os.path.join(ROOT, "tools", "b4bpak.py"), "pack", a[1], a[2]]).returncode
    return run_tool(a)


if __name__ == "__main__":
    sys.exit(main())
