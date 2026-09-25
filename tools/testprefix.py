#!/usr/bin/env python3
"""Create/refresh an isolated Proton prefix for local test instance N (never touches the real prefix).

    testprefix.py N [--fresh] [--blank] [--host | --join ADDR] [--force]   (--host: no join=, i.e. the default: host)
    testprefix.py N --golden [--force]    take the golden profile snapshot now (from the current profile)
    testprefix.py N --restore             put the golden profile back (tools/e2e.py does this before every run)

Prefix:  ~/.local/share/b4b-coop/prefixes/test<N>  (cloned from steamapps/compatdata/924970, ~600 MB)
         B4B_LANE=2: ~/.local/share/b4b-coop/prefixes/lane2/test<N> (same source; tools/lane.py)
Config:  <prefix>/b4bcoop.ini  (pass to the agent via B4B_COOP_CONFIG)
Patches (test copy only): muted, small window, low quality, ui cvars in Engine.ini.
--blank: delete the copy's profile save -> the game starts a fresh offline profile (no decks/unlocks), a
distinguishable identity. The profile's source of truth is the AES-encrypted PlayerProfileSettings.sav; the .json
next to it is only an export the game overwrites, so editing it (deck names etc.) has no effect.
Refuses (exit 1) to change a prefix while a game process runs on it (B4B_PREFIX in /proc/<pid>/environ, as
launch/multi-stop.sh matches), unless --force. Change prefixes only while holding launch/gamelock.sh.
Golden profile: <prefix>/profile-golden/ holds a known-good copy of PlayerProfileSettings.sav/.json, taken at clone
time (or, for an older prefix, the first time it is needed: the current profile if it is healthy, else the real
prefix's). tools/e2e.py restores it before every run, so a client profile the game wiped to a blank one (sign-in
"HydraPublicId mismatch", docs/investigations/test-profiles.md) or a half-written save heals itself. --blank keeps it.
"""
import datetime, json, os, re, shutil, subprocess, sys
import lane

STEAM = os.path.expanduser("~/.local/share/Steam")
REAL = os.path.join(STEAM, "steamapps/compatdata/924970")
ROOT = lane.ROOT
SAVED = "pfx/drive_c/users/steamuser/AppData/Local/Back4Blood/Steam/Saved"
SAVES = SAVED + "/SaveGames"
PROFILE_FILES = ("PlayerProfileSettings.sav", "PlayerProfileSettings.json")
GOLDEN = "profile-golden"

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


def profile_health(saves_dir):
    """(ok, why) for the profile in a SaveGames dir: ok = both files there and the .json export has decks and SP (a
    profile the game reset is 'publicId offline.<id>', no decks, 0 SP)."""
    if not all(os.path.exists(os.path.join(saves_dir, f)) for f in PROFILE_FILES): return False, "missing"
    try:
        prof = json.load(open(os.path.join(saves_dir, PROFILE_FILES[1])))
    except (OSError, ValueError) as e:
        return False, f"unreadable .json ({e.__class__.__name__})"
    od = prof.get("offlineData", {})
    decks, sp = len(od.get("decks", [])), od.get("supplyPoints", {}).get("acquired", 0)
    why = f"publicId {prof.get('publicId') or '-'}, {decks} deck(s), SP {sp}, " \
          f"{os.path.getsize(os.path.join(saves_dir, PROFILE_FILES[0]))} B"
    return decks > 0 and sp > 0, why


def _copy_profile(src_dir, dst_dir):
    tmp = dst_dir.rstrip("/") + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for f in PROFILE_FILES: shutil.copy2(os.path.join(src_dir, f), os.path.join(tmp, f))
    if os.path.basename(dst_dir.rstrip("/")) == GOLDEN:
        shutil.rmtree(dst_dir, ignore_errors=True)
        os.rename(tmp, dst_dir)
    else:   # the live SaveGames dir: replace the two files only (rename each, never a half-written file)
        os.makedirs(dst_dir, exist_ok=True)
        for f in PROFILE_FILES: os.replace(os.path.join(tmp, f), os.path.join(dst_dir, f))
        shutil.rmtree(tmp, ignore_errors=True)


def snapshot_golden(dst, allow_unhealthy=False):
    """Golden = the prefix's current profile if healthy, else the real prefix's. Returns a one-line description."""
    src, note = os.path.join(dst, SAVES), "current profile"
    ok, why = profile_health(src)
    if not ok and not allow_unhealthy:
        src, note = os.path.join(REAL, SAVES), f"real prefix's profile (current one: {why})"
        ok, why = profile_health(src)
        if not ok: raise SystemExit(f"testprefix.py: no healthy profile for a golden snapshot ({why})")
    _copy_profile(src, os.path.join(dst, GOLDEN))
    return f"golden snapshot from the {note}: {why}"


def restore_golden(dst):
    """Put the golden profile back into the prefix (created first if missing). Returns a one-line description."""
    g, saves = os.path.join(dst, GOLDEN), os.path.join(dst, SAVES)
    made = "" if profile_health(g)[0] else snapshot_golden(dst) + "; "
    was = profile_health(saves)[1]
    if all(os.path.exists(os.path.join(saves, f)) and open(os.path.join(saves, f), "rb").read()
           == open(os.path.join(g, f), "rb").read() for f in PROFILE_FILES):
        return made + f"profile = golden ({was})"
    _copy_profile(g, saves)
    t = datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(g, PROFILE_FILES[0])))
    return made + f"restored golden profile of {t:%Y-%m-%d %H:%M} ({profile_health(saves)[1]}); was: {was}"


def write_config(dst, n, join):
    # The host needs no host= line: hosting is the default (what a player gets), join= turns it off.
    lines = ["offline=1"]
    if join: lines.append(f"join={join}")
    # B4B_INI_EXTRA="netguard=off;netguard_eos=0": extra agent config lines for every instance (';'-separated);
    # B4B_INI_EXTRA<n> (e.g. B4B_INI_EXTRA2="b4bcoop_protocol_override=2"): only for instance n
    for var in ("B4B_INI_EXTRA", f"B4B_INI_EXTRA{n}"):
        lines += [l.strip() for l in os.environ.get(var, "").split(";") if l.strip()]
    open(os.path.join(dst, "b4bcoop.ini"), "w").write("\n".join(lines) + "\n")


def users(dst):
    """PIDs of processes running on this prefix (B4B_PREFIX=<dst> in their environment, set by launch/instance.sh)."""
    want = ("B4B_PREFIX=" + dst.rstrip("/")).encode()
    pids = []
    for d in os.listdir("/proc"):
        if not d.isdigit(): continue
        try:
            env = open(f"/proc/{d}/environ", "rb").read().split(b"\0")
        except OSError:
            continue
        if want in env: pids.append(int(d))
    return pids


def main():
    args = sys.argv[1:]
    if not args or not args[0].isdigit(): sys.exit(__doc__)
    n = int(args[0])
    dst = os.path.join(ROOT, f"test{n}")
    busy = users(dst)
    if busy and "--force" not in args:
        sys.exit(f"testprefix.py: test{n} is in use by running process(es) {' '.join(map(str, busy[:8]))}: refusing to "
                 f"change it (stop them with launch/multi-stop.sh {n}, hold launch/gamelock.sh, or pass --force)")
    if "--fresh" in args or not os.path.isdir(os.path.join(dst, "pfx")):
        shutil.rmtree(dst, ignore_errors=True)
        clone(dst)
        patch(dst)
        if profile_health(os.path.join(dst, SAVES))[0]: snapshot_golden(dst)
    if "--golden" in args:
        print(f"test{n}: " + snapshot_golden(dst, allow_unhealthy="--force" in args)); return
    if "--restore" in args:
        print(f"test{n}: " + restore_golden(dst)); return
    if "--blank" in args:
        blank_profile(dst)
    join = args[args.index("--join") + 1] if "--join" in args else ""
    write_config(dst, n, join)
    print(winpath(os.path.join(dst, "b4bcoop.ini")))


if __name__ == "__main__":
    main()
