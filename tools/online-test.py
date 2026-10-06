#!/usr/bin/env python3
"""Live test of the launch choice b4bcoop co-op / Online (#47, docs/investigations/online-mode.md "Tests") on test
instance 1 (its own prefix and b4bcoop.ini), without ever going online:
  1. first start (game exe directly, as Proton's first start through Steam would): the agent sets the Wine
     DllOverride for the launcher; the game's sign-in popup answered "Online" (dev `signin online`) signs in Offline
  2. the root stub (B4B_STUB=1): the launcher loads through that override, prompts (dev auto-answer), co-op =
     the game without EAC, the agent loaded, no start_protected_game.exe
  3. Online + "Remember my choice": the agent DLLs move to <game>\\b4bcoop-online\\, launch=online lands in the ini,
     and Easy Anti-Cheat's launcher is created SUSPENDED (dev -b4bcoop_test_suspend): it never runs a single
     instruction; its mapped files and command line are checked, no game process exists, then it is killed
  4. remembered Online: no prompt, the same; a Steam join (+b4bcoop_join) then starts co-op and moves the agent back
  5. -b4bcoop=ask with a remembered choice: prompt; cancel = nothing starts, nothing moves
  6. -b4bcoop=off on the game's own command line with the agent loaded: the game is closed before it runs
The lane's game files are checked byte for byte at the end (agent back in place, b4bcoop-online gone).
Needs the build under test installed in the lane's game folder (launch/install.sh) and the lane's lock (taken here
unless --no-lock). Artifacts in /tmp/b4b-online-<time>/. Exit 1 on failure.
  B4B_LANE=2 B4B_STEAM=flatpak B4B_GPU=4090 tools/online-test.py [--no-lock]"""
import argparse, glob, hashlib, os, re, shutil, socket, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lane import GAME, ROOT, PORT_BASE, LANE

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(GAME, "Gobi", "Binaries", "Win64")
PARK = os.path.join(GAME, "b4bcoop-online")
PREFIX = os.path.join(ROOT, "test1")
INI = os.path.join(PREFIX, "b4bcoop.ini")
USERREG = os.path.join(PREFIX, "pfx", "user.reg")
LLOG = os.path.join(BIN, "b4bcoop-launcher.log")
SAVES = os.path.join(PREFIX, "pfx", "drive_c", "users", "steamuser", "AppData", "Local", "Back4Blood", "Steam", "Saved", "SaveGames")
OUT = f"/tmp/b4b-online-{time.strftime('%Y%m%d-%H%M%S')}"
SELF_ID = "76561198994546085"   # lane 2's account (dreamsofants): a join to ourselves goes nowhere
REGKEY = "[Software\\\\Wine\\\\AppDefaults\\\\Back4Blood.exe\\\\DllOverrides]"
T0 = time.time()
results = []


def log(msg):
    line = f"[{time.time() - T0:7.1f}s] {msg}"
    print(line, flush=True)
    with open(os.path.join(OUT, "online-test.log"), "a") as f: f.write(line + "\n")


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    log(f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")
    return ok


def agent(*args, timeout=25):
    try:
        s = socket.create_connection(("127.0.0.1", PORT_BASE), timeout=timeout)
        s.settimeout(timeout)
        s.sendall((" ".join(args) + "\n").encode())
        chunks = []
        while d := s.recv(65536): chunks.append(d)
        return b"".join(chunks).decode(errors="replace")
    except OSError:
        return ""


def sha(p):
    try: return hashlib.sha256(open(p, "rb").read()).hexdigest()
    except OSError: return None


def read(p):
    try: return open(p, errors="replace").read()
    except OSError: return ""


def procs():
    """this test's processes on test prefix 1: [(pid, comm, cmdline, state)]"""
    out = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            env = open(d + "/environ", "rb").read().split(b"\0")
            if f"B4B_PREFIX={PREFIX}".encode() not in env: continue
            comm = open(d + "/comm").read().strip()
            cmd = open(d + "/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace").strip()
            state = re.search(r"State:\s+(\S)", open(d + "/status").read()).group(1)
            out.append((int(d[6:]), comm, cmd, state))
        except (OSError, AttributeError):
            pass
    return out


def find(pred):
    return [p for p in procs() if pred(p)]


def is_eac(p): return p[1].startswith("start_protected")
def is_game(p): return p[1] == "Back4Blood.exe" and "Gobi" in p[2] and "-SaveToUserDir" in p[2]
def is_stub(p): return p[1] == "Back4Blood.exe" and not is_game(p)


def stop():
    subprocess.run([os.path.join(REPO, "launch", "multi-stop.sh"), "1"], capture_output=True, text=True)
    time.sleep(3)


def launch(args=(), stub=False, answer=None, tag="run"):
    env = dict(os.environ)
    env.pop("B4B_LAUNCHER_ANSWER", None)
    if stub: env["B4B_STUB"] = "1"
    if answer: env["B4B_LAUNCHER_ANSWER"] = answer
    if os.path.exists(LLOG): os.remove(LLOG)
    out = open(os.path.join(OUT, f"{tag}.out"), "w")
    log(f"start {tag}: {'stub' if stub else 'game exe'} {' '.join(args)}{' answer=' + answer if answer else ''}")
    return subprocess.Popen([os.path.join(REPO, "launch", "instance.sh"), "1", *args], env=env, stdout=out,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)


def wait(fn, t, step=1.0):
    end = time.time() + t
    while time.time() < end:
        r = fn()
        if r: return r
        time.sleep(step)
    return fn()


def newest_agent_log(since):
    c = [p for p in glob.glob(os.path.join(BIN, "b4bcoop-test1-*.log")) if os.path.getmtime(p) >= since]
    return read(max(c, key=os.path.getmtime)) if c else ""


def ini_set(lines):
    open(INI, "w", newline="\r\n").write("\n".join(lines) + "\n")


def save_launcher_log(tag):
    if os.path.exists(LLOG): shutil.copy(LLOG, os.path.join(OUT, f"{tag}-launcher.log"))
    return read(LLOG)


def agent_files():
    return {n: sha(os.path.join(BIN, n)) for n in ("X3DAudio1_7.dll", "dwmapi.dll")}


def mapped_files(pid):
    try: return sorted({l.split(None, 5)[5].strip() for l in open(f"/proc/{pid}/maps") if len(l.split(None, 5)) == 6})
    except OSError: return []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-lock", action="store_true", help="the lane's gamelock is already held")
    a = ap.parse_args()
    os.makedirs(OUT)
    log(f"lane {LANE}, game {GAME}, prefix {PREFIX}, out {OUT}")
    lock = os.path.join(REPO, "launch", "gamelock.sh")
    if not a.no_lock: subprocess.run([lock, "acquire", "online-test"], check=True)
    ini_orig = read(INI)
    try:
        run()
    finally:
        stop()
        for p in find(lambda p: True): os.kill(p[0], 9)
        open(INI, "w").write(ini_orig)
        for f in glob.glob(os.path.join(SAVES, "PlayerProfileSettings-b4bcoop-before-online-*")): os.remove(f)
        if not a.no_lock: subprocess.run([lock, "release", "online-test"])
    w = max(len(n) for n, _, _ in results) if results else 10
    print("\n" + "\n".join(f"{'PASS' if ok else 'FAIL'}  {n:<{w}}  {d}" for n, ok, d in results))
    bad = sum(not ok for _, ok, _ in results)
    print(f"\n{len(results) - bad}/{len(results)} passed; artifacts in {OUT}")
    sys.exit(1 if bad else 0)


def run():
    stop()
    if os.path.isdir(PARK): shutil.rmtree(PARK)
    dll = sha(os.path.join(BIN, "X3DAudio1_7.dll"))
    if not check("agent installed (X3DAudio1_7.dll + root xinput1_3.dll)", dll and os.path.exists(os.path.join(GAME, "xinput1_3.dll"))): return
    base = [l for l in ini_orig_lines() if not l.startswith(("launch=", "offline=", ";launch="))]
    # the prefix as before the first start: no DllOverride for the launcher
    reg = read(USERREG)
    if REGKEY in reg:
        i = reg.index(REGKEY); j = reg.find("\n\n", i)
        open(USERREG, "w").write(reg[:i] + reg[j + 2:])
    check("test prefix: no launcher DllOverride before the first start", REGKEY not in read(USERREG))

    # 1. first start (no stub): override set; the sign-in popup's Online becomes Offline
    ini_set(base + ["host=0"])
    t = time.time()
    launch(tag="1-first")
    if not check("1: agent up", wait(lambda: "pong" in agent("ping"), 180, 2)): return
    lg = newest_agent_log(t)
    check("1: agent sets the Wine DllOverride for the launcher", "xinput1_3=native,builtin for Back4Blood.exe" in lg)
    check("1: sign-in guard hooked", "Online at the sign-in popup is answered Offline" in lg)
    answered = None
    for _ in range(90):
        r = agent("signin", "online")
        if "signed in: yes" in r: break
        if "refused" in r: break
        time.sleep(1)
    lg = newest_agent_log(t)
    open(os.path.join(OUT, "1-agent.log"), "w").write(lg)
    check("1: Online pressed at the game's popup", 'answering online/offline popup with Online' in lg)
    check("1: the guard answered Offline instead", 'the sign-in popup answered "Online"; b4bcoop is running, so this game signs in Offline' in lg)
    check("1: the game logged the Offline response", re.search(r"online/offline prompt closed with response Offline", lg) is not None,
          (re.search(r".*prompt closed with response.*", lg) or re.search("(.*)", "")).group(0)[:120])
    check("1: signed in (Offline)", wait(lambda: "signed in: yes" in agent("signin"), 120, 3))
    nb = agent("netguard")
    open(os.path.join(OUT, "1-netguard.txt"), "w").write(nb)
    allowed = nb.split("allowed (", 1)[-1] if "allowed (" in nb else ""
    check("1: no publisher service reached (netguard allowed list)", nb and not re.search(r"epicgames|4vngame|wbagora|wbinsights|vivox", allowed, re.I),
          nb.splitlines()[0] if nb else "no agent")
    log("waiting 35 s so Wine writes the registry to user.reg")
    time.sleep(35)
    stop()
    check("1: the override is in the prefix's registry", REGKEY in read(USERREG) and '"xinput1_3"="native,builtin"' in read(USERREG).split(REGKEY, 1)[-1][:200])

    # 2. stub, prompt -> co-op
    t = time.time()
    seen = set()
    launch(stub=True, answer="coop", tag="2-coop")
    up = wait(lambda: (seen.update(p[1] for p in procs()), "pong" in agent("ping"))[1], 180, 1)
    ll = save_launcher_log("2-coop")
    check("2: launcher loaded in the stub under Proton (DllOverride)", "hooked CreateProcessW" in ll)
    check("2: prompt answered co-op", "choice: b4bcoop co-op (the prompt)" in ll, "dev auto-answer coop" if "dev auto-answer coop" in ll else ll[-200:])
    check("2: game started without EAC", "redirect (no EAC)" in ll and up)
    check("2: no start_protected_game.exe seen", not any(s.startswith("start_protected") for s in seen), ", ".join(sorted(seen)))
    check("2: agent loaded in that game", "b4bcoop loaded" in newest_agent_log(t))
    # the ~ window's Settings tab, "Game start": writes launch= like the launcher's "Remember my choice"
    agent("overlay", "open"); agent("overlay", "tab", "Settings")
    time.sleep(2)
    r1 = agent("overlay", "press", "Always b4bcoop co-op##launch"); time.sleep(2)
    coop = re.search(r"(?m)^launch=coop\r?$", read(INI)) is not None
    subprocess.run([os.path.join(REPO, "launch", "shot.sh"), "1", os.path.join(OUT, "2-settings-tab.png")], capture_output=True)
    r2 = agent("overlay", "press", "Ask every time##launch"); time.sleep(2)
    back = not re.search(r"(?m)^launch=", read(INI))
    check("2: ~ Settings 'Game start' writes launch=coop, then back to ask", coop and back, f"{r1.strip()[:80]} / {r2.strip()[:80]}")
    stop()

    # 3. stub, prompt -> Online + remember; EAC's launcher created suspended
    before = agent_files()
    launch(["-b4bcoop_test_suspend"], stub=True, answer="online+remember", tag="3-online")
    eac = wait(lambda: find(is_eac), 120, 0.5)
    time.sleep(2)
    ll = save_launcher_log("3-online")
    check("3: prompt answered Online, remembered", "choice: online (the prompt)" in ll and "remembered: launch=online" in ll)
    check("3: ini has launch=online", re.search(r"(?m)^launch=online\r?$", read(INI)) is not None)
    check("3: X3DAudio1_7.dll moved out of Gobi\\Binaries\\Win64", not os.path.exists(os.path.join(BIN, "X3DAudio1_7.dll")))
    check("3: no dwmapi.dll in Gobi\\Binaries\\Win64", not os.path.exists(os.path.join(BIN, "dwmapi.dll")))
    check("3: the same file in b4bcoop-online", sha(os.path.join(PARK, "Gobi", "Binaries", "Win64", "X3DAudio1_7.dll")) == before["X3DAudio1_7.dll"])
    check("3: b4bcoop-online\\README.txt", os.path.exists(os.path.join(PARK, "README.txt")))
    bk = sorted(glob.glob(os.path.join(SAVES, "PlayerProfileSettings-b4bcoop-before-online-*.sav")))
    check("3: offline save backed up before the online start", bk and sha(bk[-1]) == sha(os.path.join(SAVES, "PlayerProfileSettings.sav"))
          and "save backup: PlayerProfileSettings-b4bcoop-before-online-" in ll, os.path.basename(bk[-1]) if bk else "none")
    check("3: EAC's launcher created (suspended)", eac and "test: starting Easy Anti-Cheat's launcher suspended" in ll and "started pid" in ll,
          f"{eac[0][1]} pid {eac[0][0]} state {eac[0][3]}: {eac[0][2][:140]}" if eac else "none")
    if eac:
        maps = mapped_files(eac[0][0])
        open(os.path.join(OUT, "3-eac-maps.txt"), "w").write("\n".join(maps))
        ours = [m for m in maps if re.search(r"xinput1_3|x3daudio|dwmapi|b4bcoop", m, re.I)]
        check("3: EAC's launcher maps no b4bcoop file", not ours, f"{len(maps)} mapped files; ours: {ours}")
        check("3: EAC's launcher command line unchanged", "start_protected_game.exe" in eac[0][2] and "Gobi -SaveToUserDir" in eac[0][2], eac[0][2][:160])
        stub = find(is_stub)
        smaps = mapped_files(stub[0][0]) if stub else []
        check("3: (the stub itself maps the launcher, as expected: the map check works)",
              any("xinput1_3.dll" in m and "Back 4 Blood" in m for m in smaps), f"{len(smaps)} mapped files")
    check("3: no game process", not find(is_game), str(find(is_game)))
    for p in find(is_eac): os.kill(p[0], 9)
    log("killed EAC's launcher")
    time.sleep(3)
    stop()
    check("3: still switched off after EAC's launcher ended", not os.path.exists(os.path.join(BIN, "X3DAudio1_7.dll")))

    # 4. remembered Online: no prompt; then a Steam join starts co-op and switches b4bcoop back on
    launch(["-b4bcoop_test_suspend"], stub=True, answer="cancel", tag="4-remembered")
    eac = wait(lambda: find(is_eac), 120, 0.5)
    ll = save_launcher_log("4-remembered")
    check("4: remembered Online, no prompt", "choice: online (b4bcoop.ini launch=)" in ll and "prompt" not in ll.split("choice:")[0])
    check("4: EAC's launcher created suspended, no game", bool(eac) and not find(is_game))
    for p in find(is_eac): os.kill(p[0], 9)
    time.sleep(3)
    stop()
    t = time.time()
    launch(["+b4bcoop_join", f"steam:{SELF_ID}"], stub=True, answer="cancel", tag="4-join")
    up = wait(lambda: "pong" in agent("ping"), 180, 2)
    ll = save_launcher_log("4-join")
    check("4: a Steam join starts co-op despite launch=online", "choice: b4bcoop co-op (a Steam join)" in ll and up)
    check("4: agent switched back on (same file)", sha(os.path.join(BIN, "X3DAudio1_7.dll")) == before["X3DAudio1_7.dll"] and "switched on:" in ll)
    check("4: b4bcoop-online removed", not os.path.exists(PARK))
    check("4: agent loaded", "b4bcoop loaded" in newest_agent_log(t))
    stop()

    # 5. -b4bcoop=ask with a remembered choice: prompt; cancel starts nothing
    snap = agent_files()
    p = launch(["-b4bcoop=ask"], stub=True, answer="cancel", tag="5-cancel")
    wait(lambda: "choice:" in read(LLOG), 120, 1)
    ended = wait(lambda: not find(is_stub) and not find(is_game) and not find(is_eac), 60, 1)
    ll = save_launcher_log("5-cancel")
    check("5: -b4bcoop=ask asks despite launch=online", "but -b4bcoop=ask: asking" in ll)
    check("5: cancel: nothing started", "choice: cancel (the prompt)" in ll and "nothing started" in ll and ended)
    check("5: cancel: nothing moved", agent_files() == snap and not os.path.exists(PARK))
    stop()

    # 6. -b4bcoop=off with the agent loaded (no launcher in between): the game is closed before it runs
    t = time.time()
    launch(["-b4bcoop=off"], answer="cancel", tag="6-off")   # answer: no message box in the dev build
    wait(lambda: "closing the game before it starts" in newest_agent_log(t), 120, 1)
    gone = wait(lambda: not find(lambda p: p[1] == "Back4Blood.exe"), 60, 1)
    lg = newest_agent_log(t)
    open(os.path.join(OUT, "6-agent.log"), "w").write(lg)
    check("6: -b4bcoop=off with the agent loaded: closed before it starts", "closing the game before it starts" in lg and gone
          and "init: tick hooked" not in lg)
    stop()
    ini_set(base)
    check("end: agent in place, nothing switched off", sha(os.path.join(BIN, "X3DAudio1_7.dll")) == dll and not os.path.exists(PARK))


_INI0 = None
def ini_orig_lines():
    global _INI0
    if _INI0 is None: _INI0 = read(INI).splitlines()
    return _INI0


if __name__ == "__main__":
    ini_orig_lines()
    main()
