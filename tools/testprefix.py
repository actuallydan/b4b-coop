#!/usr/bin/env python3
"""Create/refresh an isolated Proton prefix for local test instance N (never touches the real prefix).

    testprefix.py N [--fresh] [--blank] [--host | --join ADDR]

Prefix:  ~/.local/share/b4b-coop/prefixes/test<N>  (cloned from steamapps/compatdata/924970, ~600 MB)
Config:  <prefix>/b4bcoop.ini  (pass to the agent via B4B_COOP_CONFIG)
Patches (test copy only): muted, small window, low quality, ui cvars in Engine.ini.
--blank: delete the copy's profile save -> the game starts a fresh offline profile (no decks/unlocks), a
distinguishable identity. The profile's source of truth is the AES-encrypted PlayerProfileSettings.sav; the .json
next to it is only an export the game overwrites, so editing it (deck names etc.) has no effect.
"""
import os, re, shutil, subprocess, sys

STEAM = os.path.expanduser("~/.local/share/Steam")
REAL = os.path.join(STEAM, "steamapps/compatdata/924970")
ROOT = os.path.expanduser(os.environ.get("B4B_TEST_ROOT", "~/.local/share/b4b-coop/prefixes"))
SAVED = "pfx/drive_c/users/steamuser/AppData/Local/Back4Blood/Steam/Saved"

# Registered cvars in this build (strings next to their registration): skip intro movies/MOTD/tutorials, start
# sign-in without "press any key". The Online/Offline popup has no cvar; the agent answers it (offline=1).
ENGINE_INI = """[ConsoleVariables]
ui.AutoSignIn=1
ui.SkipMOTD=1
ui.SkipTutorials=1
ui.SkipPreRenderedCinematics=1
"""


def winpath(p):
    return "Z:" + p.replace("/", "\\")


def ini_set(text, key, value):
    pat = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    return pat.sub(f"{key}={value}", text) if pat.search(text) else text


def clone(dst):
    if os.path.realpath(dst).startswith(os.path.realpath(REAL)):
        sys.exit("refusing: destination is inside the real prefix")
    tmp = dst + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    # cp -a keeps the dosdevices symlinks; reflink when the filesystem supports it
    subprocess.run(["cp", "-a", "--reflink=auto", REAL, tmp], check=True)
    os.rename(tmp, dst)


def patch(dst):
    saved = os.path.join(dst, SAVED)
    cfg = os.path.join(saved, "Config/WindowsNoEditor")
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "Engine.ini"), "w") as f:
        f.write(ENGINE_INI)
    gus = os.path.join(cfg, "GameUserSettings.ini")
    if os.path.exists(gus):
        t = open(gus).read()
        for k, v in {"MasterVolume": "0.000000", "bSuppressAudioOnFocusLost": "True", "GraphicsQuality": "0",
                     "FullscreenMode": "2", "LastConfirmedFullscreenMode": "2", "PreferredFullscreenMode": "2",
                     "ResolutionSizeX": "960", "ResolutionSizeY": "540", "LastUserConfirmedResolutionSizeX": "960",
                     "LastUserConfirmedResolutionSizeY": "540", "FrameRateLimit": "30.000000",
                     "TargetFramerate": "30.000000", "UpscalingMode": "None", "MotionBlurMode": "Off"}.items():
            t = ini_set(t, k, v)
        for k in ["ViewDistanceQuality", "AntiAliasingQuality", "ShadowQuality", "PostProcessQuality", "TextureQuality",
                  "EffectsQuality", "FoliageQuality", "ShadingQuality"]:
            t = ini_set(t, "sg." + k, "0")
        t = ini_set(t, "sg.ResolutionQuality", "50.000000")
        open(gus, "w").write(t)


def blank_profile(dst):
    for ext in ("sav", "json"):
        f = os.path.join(dst, SAVED, "SaveGames/PlayerProfileSettings." + ext)
        if os.path.exists(f): os.remove(f)


def write_config(dst, host, join):
    lines = ["offline=1"]
    if host: lines.append("host=1")
    if join: lines.append(f"join={join}")
    open(os.path.join(dst, "b4bcoop.ini"), "w").write("\n".join(lines) + "\n")


def main():
    args = sys.argv[1:]
    if not args or not args[0].isdigit(): sys.exit(__doc__)
    n = int(args[0])
    dst = os.path.join(ROOT, f"test{n}")
    if "--fresh" in args or not os.path.isdir(os.path.join(dst, "pfx")):
        shutil.rmtree(dst, ignore_errors=True)
        clone(dst)
        patch(dst)
    if "--blank" in args:
        blank_profile(dst)
    join = args[args.index("--join") + 1] if "--join" in args else ""
    write_config(dst, "--host" in args, join)
    print(winpath(os.path.join(dst, "b4bcoop.ini")))


if __name__ == "__main__":
    main()
