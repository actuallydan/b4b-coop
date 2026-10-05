#!/usr/bin/env python3
"""Two-account Steam Join Game check (#41, #10): host = lane 1 test1 (native Steam, Hergmgurk), client = lane 2 test1
(Flatpak Steam, dreamsofants). The join is Steam's own Join Game: the client's Steam client opens
steam://rungame/924970/<host id64>/<host's connect string as the client's Steam sees it> (what the friends list's
"Join Game" does), Steam posts GameRichPresenceJoinRequested_t (337) to the running game.
usage: tools/jointest2acct.py OUTDIR cell[*n] ...   cells: camp (client in its camp), title (client on its title screen),
mload (host starts a mission at the click), m2c (host starts a mission once the client is welcomed), lobby (host in a
mission before ready), play (host in a mission after ready: hot-join, the host hands the client its bot), invite (the
host sends a Steam invite, `invite <client id>`; the client accepts it the way the invite's Join button does, the
same rungame URL with the invite's connect string), session (join in camp, mission follow, a burn card each, the client
leaves and comes back through Join Game mid-mission, saferoom charge, endmission success: the client's card and SP land
on the client's own profile and nothing on the host's; profile diffs after the deferred save).
Needs: both lane locks (launch/gamelock.sh acquire, B4B_LANE=1 and B4B_STEAM=flatpak), the build installed in both
lanes (launch/install.sh, B4B_STEAM=flatpak launch/install.sh), the native Steam signed in to Hergmgurk and NOT in use
by Dan (no ~/.local/share/b4b-coop/native-steam-in-use), the Flatpak Steam running (launch/flatpak-steam.sh start, after
the native one so each owns its service port), the two accounts Steam friends. Per trial: golden profiles restored,
inis offline=1/addons=0 (client title cell: no offline=1, the join arms the sign-in), the client reads the host's
connect from its own Steam friends cache (agent `friends`), screenshots of both on success, logs + `steamnet` per
trial in OUTDIR. docs/investigations/join-timing.md "Two-account check"."""
import glob, os, re, shutil, socket, subprocess, sys, time, urllib.parse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for k in ("B4B_LANE", "B4B_STEAM", "B4B_AGENT"): os.environ.pop(k, None)   # each side sets its own (H/C env)
sys.path.insert(0, os.path.join(REPO, "tools"))
import e2e   # profile helpers only (pure functions)
PROFILE = e2e.PROFILE
MAP_B = "Evansburgh_B"
H = dict(port=47112, ini=os.path.expanduser("~/.local/share/b4b-coop/prefixes/test1/b4bcoop.ini"),
         logs=os.path.expanduser("~/.local/share/Steam/steamapps/common/Back 4 Blood/Gobi/Binaries/Win64"),
         env=dict(B4B_LANE="1", B4B_STEAM="native", B4B_GPU="4090"), args=["-Port=7787"],
         prefix=os.path.expanduser("~/.local/share/b4b-coop/prefixes/test1"))
C = dict(port=47140, ini=os.path.expanduser("~/.local/share/b4b-coop/prefixes/lane2/test1/b4bcoop.ini"),
         logs=os.path.expanduser("~/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/Back 4 Blood/Gobi/Binaries/Win64"),
         env=dict(B4B_LANE="2", B4B_STEAM="flatpak", B4B_GPU="4090"), args=[],
         prefix=os.path.expanduser("~/.local/share/b4b-coop/prefixes/lane2/test1"))
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
e2e.OUT = OUT
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
    # flatpak enter starts with an empty environment: the running Steam client's own (as launch/run.sh does), so the
    # second `steam` finds the running one and hands it the URL
    pid = re.search(r"pid (\d+)", sh([os.path.join(REPO, "launch/flatpak-steam.sh"), "status"]).stdout).group(1)
    senv = [kv for kv in open(f"/proc/{pid}/environ", "rb").read().decode(errors="replace").split("\0") if "=" in kv]
    r = sh(["flatpak", "enter", inst, "env", "-i"] + senv + ["sh", "-c", 'exec "$HOME/.local/share/Steam/ubuntu12_32/steam" "$1"',
            "sh", url], timeout=60)
    log(f"  Join Game via {url} (instance {inst}): rc={r.returncode} {r.stderr.strip()[-200:]}")


def world(st):
    m = re.search(r"^world: \S*?(\w+)\.\w+\s*$", st, re.M) or re.search(r"^world: (\S+)", st, re.M)
    return m.group(1) if m else ""


def has_hero(who):
    return "light=" in agent(who, "flashlight status")


def in_mission(who):
    return MAP_B in world(agent(who, "status")) and has_hero(who)


def host_to_mission(play):
    """Host: start Evansburgh Easy and wait for its hero; play = also `ready` (match in progress)."""
    agent(H, "mission Easy")
    if not wait(lambda: in_mission(H), 240, 3): return False
    time.sleep(3)
    if play:
        agent(H, "ready")
        time.sleep(5)
    return True


def shot(who, name):
    sh([os.path.join(REPO, "launch/shot.sh"), "1", os.path.join(OUT, name)], who["env"], timeout=60)


def profile(who):
    return e2e.load_profile(os.path.join(who["prefix"], PROFILE))


def joined_fn(cell, since):
    """Pass condition: client connected, same map as the host; mission cells: Evansburgh_B with the client's hero
    (play/session: a hot-join spectates its bot until the host hands it over, `takeover`)."""
    def joined():
        sh_, sc = agent(H, "status"), agent(C, "status")
        wh, wc = world(sh_), world(sc)
        if "server_conn=yes" not in sc or not wh or wh != wc: return False
        if cell in ("mload", "m2c", "mpre", "lobby", "play") and MAP_B not in wh: return False
        if MAP_B in wh and not has_hero(C):
            if cell in ("play", "session"):
                for slot in (1, 2, 3): agent(H, f"takeover {slot}")
            return False
        return wc
    return joined


def check(res, what, ok, detail=""):
    res["checks"].append((what, bool(ok), detail))
    log(f"    {'PASS' if ok else 'FAIL'} {what}: {detail}")
    return bool(ok)


def host_connect(host_id):
    m = re.search(rf"^{host_id} .*\n(?:    .*\n)*?    connect = (\S.*)$", agent(C, "friends"), re.M)
    return m.group(1).strip() if m else None


def session(res, since, host_id, client_id, connect, name):
    """Two-account session: mission follow, burn cards across a drop + Join Game rejoin, rewards to own profiles."""
    before = {"host": profile(H), "client": profile(C)}
    for k, who in (("host", H), ("client", C)):
        shutil.copy(os.path.join(who["prefix"], PROFILE), os.path.join(OUT, f"{name}-profile-{k}-before.json"))
    t0 = time.time()
    join_game(host_id, connect)
    if not check(res, "join in camp (Join Game)", wait(joined_fn("camp", since), 300, 3), f"{round(time.time() - t0)}s"): return
    agent(H, "mission Easy")
    ok = wait(lambda: in_mission(H) and in_mission(C), 300, 4)
    hum = len(e2e.humans(agent(H, "slots")))
    if not check(res, "mission follow (both heroes in Evansburgh_B)", ok and hum == 2, f"{hum} human slot(s)"): return
    time.sleep(4)
    shot(H, f"{name}-mission-host.png"); shot(C, f"{name}-mission-client.png")
    card_c = e2e.pick_card(agent(C, "burncard list"))
    card_h = e2e.pick_card(agent(H, "burncard list"), avoid=(card_c,))
    if not check(res, "burn cards available", card_c and card_h, f"host {card_h} client {card_c}"): return
    agent(C, f"burncard {card_c}")
    time.sleep(2)
    agent(H, f"burncard {card_h}")
    ok = wait(lambda: grep(H, since, rf"remote player played {card_c}") and grep(H, since, rf"local player played {card_h}"), 30, 2)
    if not check(res, "burn cards played (host + client)", ok, f"host {card_h}, client {card_c}"): return
    # the client drops and comes back through Steam's Join Game mid-mission
    agent(C, "leave")
    ok = wait(lambda: (lambda st: "server_conn=yes" not in st and MAP_B not in world(st))(agent(C, "status")), 120, 3)
    check(res, "client left (own camp)", ok, world(agent(C, "status")))
    time.sleep(10)
    c2 = wait(lambda: host_connect(host_id), 60, 3)
    t0 = time.time()
    join_game(host_id, c2 or connect)
    ok = wait(joined_fn("session", since), 300, 3)
    if not check(res, "rejoin mid-mission (Join Game) + bot take-over", ok, f"{round(time.time() - t0)}s"): return
    time.sleep(3)
    agent(H, "ready")
    time.sleep(3)
    agent(H, "burncard charge")
    got = wait(lambda: grep(C, since, rf"CLIENT RPC.*consumable {card_c} by -1"), 30, 2)
    rem = grep(H, since, r"burncards: charging b4bcoop\.burn\.\d+ -> remote player's profile[^\n]*")
    loc = grep(H, since, r"burncards: charging b4bcoop\.burn\.\d+ -> local player's profile")
    check(res, "saferoom charge: client's card to the client after the rejoin", got and len(rem) == 1 and len(loc) == 1,
          f"client RPC {bool(got)}; host remote {rem}; local {len(loc)}")
    agent(H, "endmission 1")
    fwd = wait(lambda: grep(H, since, r"rewards: forwarding AdjustSupplyPoints \((-?\d+)\) to remote player (\S+)"), 40, 2)
    rpc = wait(lambda: grep(C, since, r"\[CLIENT RPC\] adjusting SP by (-?\d+)"), 40, 2)
    n_fwd = int(fwd[0][0]) if fwd else 0
    check(res, "endmission success: client's SP forwarded to the client's id",
          fwd and rpc and len(fwd) == 1 and int(rpc[0]) == n_fwd and client_id in fwd[0][1], f"forwarded {fwd}, applied {rpc}")
    time.sleep(4)
    shot(H, f"{name}-postround-host.png"); shot(C, f"{name}-postround-client.png")
    exp_c = e2e.sp(before["client"]) + n_fwd
    cs = e2e.consumable_spent
    wait(lambda: e2e.sp(profile(C)) >= exp_c and cs(profile(C), card_c) > cs(before["client"], card_c), 120, 5)
    time.sleep(40)   # deferred saves (~30 s), both sides
    after = {"host": profile(H), "client": profile(C)}
    for k, who in (("host", H), ("client", C)):
        shutil.copy(os.path.join(who["prefix"], PROFILE), os.path.join(OUT, f"{name}-profile-{k}-after.json"))
        e2e.write_diff(before[k], after[k], f"{name}-profile-{k}.diff")
    d = {"client SP": (e2e.sp(after["client"]) - e2e.sp(before["client"]), n_fwd),
         f"client {card_c}.spent": (cs(after["client"], card_c) - cs(before["client"], card_c), 1),
         f"host {card_h}.spent": (cs(after["host"], card_h) - cs(before["host"], card_h), 1),
         f"host {card_c}.spent": (cs(after["host"], card_c) - cs(before["host"], card_c), 0),
         f"client {card_h}.spent": (cs(after["client"], card_h) - cs(before["client"], card_h), 0)}
    bad = {k: v for k, v in d.items() if v[0] != v[1]}
    check(res, "profiles after the deferred save (own profiles, once each)", not bad and all(after.values()),
          "; ".join(f"{k} {v[0]:+d} (want {v[1]:+d})" for k, v in d.items())
          + f"; host SP {e2e.sp(after['host']) - e2e.sp(before['host']):+d}")


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
    res = dict(cell=cell, rep=rep, ok=False, secs=None, why="", checks=[])
    try:
        if not wait(lambda: grep(H, since, r'presence: advertising connect="([^"]+)"'), 300):
            res["why"] = "host not up"; return res
        ok_c = (lambda: grep(C, since, r"Created screen 'SignInScreen'")) if cell == "title" else \
               (lambda: grep(C, since, r"presence: advertising connect"))
        if not wait(ok_c, 300): res["why"] = "client not up"; return res
        time.sleep(5)
        host_id = grep(H, since, r"presence: steam bound, user (\d+)")[-1]
        client_id = (grep(C, since, r"presence: steam bound, user (\d+)") or ["?"])[-1]
        # the host's connect string as the client's Steam has it (friends' rich presence), like the friends list
        connect = wait(lambda: host_connect(host_id), 60, 3)
        if not connect: res["why"] = "client's Steam doesn't see the host's connect"; return res
        log(f"  client sees host {host_id}: connect={connect}")
        if cell == "session":
            session(res, since, host_id, client_id, connect, name)
            res["ok"] = len(res["checks"]) >= 9 and all(c[1] for c in res["checks"])
            res["why"] = "; ".join(c[0] for c in res["checks"] if not c[1])
            res["callback"] = len(grep(C, since, r"presence: steam join request from " + host_id))
            return res
        if cell in ("lobby", "play") and not host_to_mission(cell == "play"):
            res["why"] = "host didn't reach the mission"; return res
        if cell == "invite":
            out = agent(H, f"invite {client_id}")
            log(f"  host: {out.strip()}")
            if "sent" not in out: res["why"] = f"invite: {out.strip()}"; return res
        if cell == "mload": agent(H, "mission Easy")
        t0 = time.time()
        join_game(host_id, connect)
        if cell == "mpre":   # host starts the mission while the join is in its handshake: (1 + rep) s after the click
            time.sleep(1 + rep); agent(H, "mission Easy")
        if cell == "m2c":
            if wait(lambda: grep(C, since, r"Welcomed by server"), 180, 0.3): agent(H, "mission Easy")
        v = wait(joined_fn(cell, since), 300, 3)
        res["secs"] = round(time.time() - t0)
        res["ok"] = bool(v)
        res["callback"] = bool(grep(C, since, r"presence: steam join request from " + host_id))
        if not v:
            sc = agent(C, "status")
            res["why"] = f"client world={world(sc)} conn={'server_conn=yes' in sc}"
        else:
            time.sleep(4)
            shot(H, f"{name}-host.png"); shot(C, f"{name}-client.png")
    finally:
        open(os.path.join(OUT, f"{name}-steamnet.txt"), "w").write(agent(H, "steamnet") + "\n----\n" + agent(C, "steamnet"))
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
        hlp = os.path.join(OUT, f"{cell}-{r}-host.log")
        if os.path.exists(hlp):   # host: connections accepted (one per attempt since 0.9.2), P2P session requests
            t = open(hlp, errors="replace").read()
            notes += [f"host {k} x{len(re.findall(p, t))}" for k, p in (("accepted", r"NotifyAcceptedConnection"),
                      ("p2p requests", r"steamnet: P2P session request"), ("not kicking", r"travel: not kicking")) if re.search(p, t)]
        res["notes"] = notes
        results.append(res)
        log(f"  {cell}-{r}: {'PASS' if res['ok'] else 'FAIL'} {'' if res['secs'] is None else str(res['secs']) + 's'} callback337={res.get('callback')} {res['why']} {notes}")
open(os.path.join(OUT, "results.txt"), "w").write("\n".join(map(str, results)) + "\n")
