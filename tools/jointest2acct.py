#!/usr/bin/env python3
"""Two-account Steam Join Game check (#41, #10): host = lane 1 test1 (native Steam, Hergmgurk), client = lane 2 test1
(Flatpak Steam, dreamsofants). The join is Steam's own Join Game: the client's Steam client opens
steam://rungame/924970/<host id64>/<host's connect string as the client's Steam sees it> (what the friends list's
"Join Game" does), Steam posts GameRichPresenceJoinRequested_t (337) to the running game.
usage: tools/jointest2acct.py OUTDIR cell[*n] ...   cells: camp (client in its camp), title (client on its title screen),
mload (host starts a mission at the click), m2c (host starts a mission once the client is welcomed)
Needs: both lane locks (launch/gamelock.sh acquire, B4B_LANE=1 and B4B_STEAM=flatpak), the build installed in both
lanes (launch/install.sh, B4B_STEAM=flatpak launch/install.sh), the native Steam signed in to Hergmgurk and NOT in use
by Dan (no ~/.local/share/b4b-coop/native-steam-in-use), the Flatpak Steam running (launch/flatpak-steam.sh start, after
the native one so each owns its service port), the two accounts Steam friends. Per trial: golden profiles restored,
inis offline=1/addons=0 (client title cell: no offline=1, the join arms the sign-in), the client reads the host's
connect from its own Steam friends cache (agent `friends`), screenshots of both on success, logs + `steamnet` per
trial in OUTDIR. docs/investigations/join-timing.md "Two-account check"."""
import glob, os, re, shutil, socket, subprocess, sys, time, urllib.parse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = dict(port=47112, ini=os.path.expanduser("~/.local/share/b4b-coop/prefixes/test1/b4bcoop.ini"),
         logs=os.path.expanduser("~/.local/share/Steam/steamapps/common/Back 4 Blood/Gobi/Binaries/Win64"),
         env=dict(B4B_LANE="1", B4B_STEAM="native", B4B_GPU="4090"), args=["-Port=7787"])
C = dict(port=47140, ini=os.path.expanduser("~/.local/share/b4b-coop/prefixes/lane2/test1/b4bcoop.ini"),
         logs=os.path.expanduser("~/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/Back 4 Blood/Gobi/Binaries/Win64"),
         env=dict(B4B_LANE="2", B4B_STEAM="flatpak", B4B_GPU="4090"), args=[])
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
T0 = time.time()


def log(m):
    line = f"[{time.time() - T0:7.1f}s] {m}"
    print(line, flush=True)
    open(os.path.join(OUT, "run.log"), "a").write(line + "\n")


def agent(who, cmd, timeout=15):
    try:
        s = socket.create_connection(("127.0.0.1", who["port"]), timeout=timeout)
        s.settimeout(timeout)
        s.sendall((cmd + "\n").encode())
        b = b""
        while (d := s.recv(65536)): b += d
        return b.decode(errors="replace")
    except OSError:
        return ""


def wait(fn, t, iv=2.0):
    end = time.time() + t
    while time.time() < end:
        v = fn()
        if v: return v
        time.sleep(iv)
    return None


def newest_log(who, since):
    c = [p for p in glob.glob(os.path.join(who["logs"], "b4bcoop-test1-*.log")) if os.path.getmtime(p) >= since]
    return max(c, key=os.path.getmtime) if c else None


def grep(who, since, pat):
    p = newest_log(who, since)
    return re.findall(pat, open(p, errors="replace").read()) if p else []


def sh(args, env=None, timeout=60):
    return subprocess.run(args, env=dict(os.environ, **(env or {})), capture_output=True, text=True, timeout=timeout)


def stop(who):
    sh([os.path.join(REPO, "launch/multi-stop.sh"), "1"], who["env"])


def launch(who, out):
    return subprocess.Popen([os.path.join(REPO, "launch/instance.sh"), "1"] + who["args"], stdout=open(out, "w"),
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                            env=dict(os.environ, **who["env"]))


def flatpak_instance():
    st = sh([os.path.join(REPO, "launch/flatpak-steam.sh"), "status"]).stdout
    pid = re.search(r"pid (\d+)", st).group(1)
    ns = os.readlink(f"/proc/{pid}/ns/ipc")
    for line in sh(["flatpak", "ps", "--columns=instance,child-pid,application"]).stdout.splitlines():
        f = line.split()
        if len(f) == 3 and f[2] == "com.valvesoftware.Steam":
            try:
                if os.readlink(f"/proc/{f[1]}/ns/ipc") == ns: return f[0]
            except OSError:
                pass
    return None


def join_game(host_id, connect):
    """What the client's friends list Join Game does: open steam://rungame/<app>/<friend>/<connect> in its Steam."""
    url = f"steam://rungame/924970/{host_id}/{urllib.parse.quote(connect, safe='')}"
    inst = flatpak_instance()
    r = sh(["flatpak", "enter", inst, "sh", "-c", 'exec "$HOME/.local/share/Steam/ubuntu12_32/steam" "$1"', "sh", url], timeout=60)
    log(f"  Join Game via {url} (instance {inst}): rc={r.returncode} {r.stderr.strip()[-200:]}")


def world(st):
    m = re.search(r"^world: \S*?(\w+)\.\w+\s*$", st, re.M)
    return m.group(1) if m else ""


def shot(who_n, lane, name):
    sh([os.path.join(REPO, "launch/shot.sh"), "1", os.path.join(OUT, name)], dict(B4B_LANE=lane) if lane == "2" else {}, timeout=60)


def trial(cell, rep):
    name = f"{cell}-{rep}"
    stop(H); stop(C)
    for who in (H, C):
        sh([sys.executable, os.path.join(REPO, "tools/testprefix.py"), "1", "--restore"], who["env"])
    open(H["ini"], "w").write("offline=1\naddons=0\n")
    open(C["ini"], "w").write(("" if cell == "title" else "offline=1\n") + "addons=0\n")
    since = time.time() - 2
    launch(H, os.path.join(OUT, f"{name}-host.out"))
    launch(C, os.path.join(OUT, f"{name}-client.out"))
    res = dict(cell=cell, rep=rep, ok=False, secs=None, why="")
    try:
        if not wait(lambda: grep(H, since, r'presence: advertising connect="([^"]+)"'), 300):
            res["why"] = "host not up"; return res
        ok_c = (lambda: grep(C, since, r"Created screen 'SignInScreen'")) if cell == "title" else \
               (lambda: grep(C, since, r"presence: advertising connect"))
        if not wait(ok_c, 300): res["why"] = "client not up"; return res
        time.sleep(5)
        host_id = grep(H, since, r"presence: steam bound, user (\d+)")[-1]
        # the host's connect string as the client's Steam has it (friends' rich presence), like the friends list
        def seen():
            out = agent(C, "friends")
            m = re.search(rf"^{host_id} .*\n(?:    .*\n)*?    connect = (\S.*)$", out, re.M)
            return m.group(1).strip() if m else None
        connect = wait(seen, 60, 3)
        if not connect: res["why"] = "client's Steam doesn't see the host's connect"; return res
        log(f"  client sees host {host_id}: connect={connect}")
        if cell == "mload": agent(H, "mission Easy")
        t0 = time.time()
        join_game(host_id, connect)
        if cell == "m2c":
            if wait(lambda: grep(C, since, r"Welcomed by server"), 180, 0.3): agent(H, "mission Easy")
        def joined():
            sh_, sc = agent(H, "status"), agent(C, "status")
            wh, wc = world(sh_), world(sc)
            if "server_conn=yes" not in sc or not wh or wh != wc: return False
            if cell in ("mload", "m2c") and "Evansburgh_B" not in wh: return False
            if "Evansburgh_B" in wh and "light=" not in agent(C, "flashlight status"): return False
            return wc
        v = wait(joined, 300, 3)
        res["secs"] = round(time.time() - t0)
        res["ok"] = bool(v)
        res["callback"] = bool(grep(C, since, r"presence: steam join request from " + host_id))
        if not v:
            sc = agent(C, "status")
            res["why"] = f"client world={world(sc)} conn={'server_conn=yes' in sc}"
        else:
            time.sleep(4)
            shot(1, "1", f"{name}-host.png"); shot(1, "2", f"{name}-client.png")
        open(os.path.join(OUT, f"{name}-steamnet.txt"), "w").write(agent(H, "steamnet") + "\n----\n" + agent(C, "steamnet"))
    finally:
        for tag, who in (("host", H), ("client", C)):
            p = newest_log(who, since)
            if p: shutil.copy(p, os.path.join(OUT, f"{name}-{tag}.log"))
        stop(H); stop(C)
    return res


results = []
for spec in sys.argv[2:]:
    cell, n = (spec.split("*") + ["1"])[:2]
    for r in range(1, int(n) + 1):
        log(f"trial {cell}-{r}")
        res = trial(cell, r)
        cl = os.path.join(OUT, f"{cell}-{r}-client.log")
        notes = []
        if os.path.exists(cl):
            t = open(cl, errors="replace").read()
            notes = [f"{k} x{len(re.findall(p, t))}" for k, p in (("attempts", r"auto: joining "), ("DTLS fail", r"DTLSHandler Error"),
                     ("failed", r"auto: join attempt \d+ failed"), ("rejoin", r"travel: rejoin attempt")) if re.search(p, t)]
        res["notes"] = notes
        results.append(res)
        log(f"  {cell}-{r}: {'PASS' if res['ok'] else 'FAIL'} {res['secs']}s callback337={res.get('callback')} {res['why']} {notes}")
open(os.path.join(OUT, "results.txt"), "w").write("\n".join(map(str, results)) + "\n")
