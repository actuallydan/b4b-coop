#!/usr/bin/env python3
"""Join timing matrix (#41): one host + one client per trial, the join triggered with the host and the client in
chosen states, repeated; a table of pass/fail per cell at the end.

    tools/jointest.py [--cells camp:cold,lobby:title*4,...] [--reps N] [--pairs 1-3] [--gpus 4090,4090,5090]
                      [--no-lock] [--out DIR]          (h:c*N = N reps of that cell, else --reps)
    tools/jointest.py --list      the host states and client modes

Host states (what the host is doing when the client's join is triggered):
    boot    host launched at the same moment as the client (still loading, then on the title, signing in)
    title   host on the title screen, not signed in (signed in by the driver 30 s after the trigger)
    camp    host signed in, in its Fort Hope (advertising)
    mload   host loading a mission (war table start right at the trigger)
    lobby   host in the mission before `ready` (pre-round)
    play    host in the mission after `ready`
Client modes (how and from where the client joins; the target is the host's loopback address):
    cold    client started with the Steam launch string on its command line (+b4bcoop_join addr:... proto: ver:),
            no ini, like Steam's Join Game with the game closed
    ini     client started with join=<addr> + offline=1 in its ini (dev path of launch/multi.sh)
    title   client on its title screen (not signed in), then a simulated Steam Join Game (steamjoin)
    camp    client signed in and hosting its own Fort Hope (the default), then steamjoin
    load    client started without a join; steamjoin as soon as its agent answers (startup load)
    fhload  same, steamjoin right after its title Fort Hope loaded (before the sign-in screen is up)
A trial passes when, within --timeout s of the trigger, the client is connected to the host, in the host's map
(with a hero when the host is in a mission), the host lists 2 players, and neither profile was reset.

Pairs: --pairs 2|3 runs independent trials at once (pair p: test<2p-1> hosts on the lane's game port + 10(p-1),
test<2p> joins; pair 3 needs a test6 prefix); --gpus gives each pair its own B4B_GPU.
Each trial restores both prefixes' golden profiles first and writes their b4bcoop.ini. Holds launch/gamelock.sh
as "jointest" unless --no-lock. Artifacts (per-trial logs, results.json, table) in /tmp/b4b-jointest-<time>/.
"""
import argparse, datetime, json, os, re, shutil, subprocess, sys, threading, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
from lane import LANE, ROOT, PORT_BASE, GAME_PORT
import testprefix
import e2e

HOSTS = ["boot", "title", "camp", "mload", "lobby", "play"]
CLIENTS = ["cold", "ini", "title", "camp", "load", "fhload"]
STEAMJOIN = ("title", "camp", "load", "fhload")
GPUS = []   # --gpus: B4B_GPU per pair
MAP_B = "Evansburgh_B"
VER = dict(l.strip().split("=", 1) for l in open(os.path.join(REPO, "VERSION")) if "=" in l and not l.startswith("#"))
LOCK = threading.Lock()


def log(msg):
    with LOCK: e2e.log(msg)


def kill_instance(n):
    subprocess.run([os.path.join(REPO, "launch/multi-stop.sh"), str(n)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=60)


def launch(n, args, out):
    env = dict(os.environ)
    pair = (n + 1) // 2
    if len(GPUS) >= pair and GPUS[pair - 1]: env["B4B_GPU"] = GPUS[pair - 1]
    return subprocess.Popen([os.path.join(REPO, "launch/instance.sh"), str(n)] + args, stdout=open(out, "w"),
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=env)


def write_ini(n, lines):
    open(os.path.join(ROOT, f"test{n}", "b4bcoop.ini"), "w").write("\n".join(lines + ["addons=0"]) + "\n")


def status(n):
    return e2e.agent(n, "status", timeout=10)


def world(st):
    m = re.search(r"^world: \S*?(\w+)\.\w+\s*$", st, re.M) or re.search(r"^world: (\S+)", st, re.M)
    return m.group(1) if m else ""


class Trial:
    def __init__(self, pair, host_state, client_mode, rep, timeout):
        self.h, self.c = 2 * pair - 1, 2 * pair
        self.port = GAME_PORT + 10 * (pair - 1)
        self.hs, self.cm, self.rep, self.timeout = host_state, client_mode, rep, timeout
        self.name = f"{host_state}-{client_mode}-{rep}-p{pair}"
        self.res = dict(cell=f"{host_state}:{client_mode}", rep=rep, ok=False, secs=None, why="", notes=[])

    def connect_string(self):
        return f"+b4bcoop_join addr:127.0.0.1:{self.port} proto:{VER['protocol']} ver:{VER['version']} addons:cosmetic"

    def note(self, s):
        self.res["notes"].append(s)

    # ---- host ----
    def host_up(self, start):
        """Launch the host; for host states other than boot, wait until it is in the wanted state."""
        signed_in = self.hs != "title"
        write_ini(self.h, ["offline=1"] if signed_in else [])
        self.hp = launch(self.h, [f"-Port={self.port}"], os.path.join(e2e.OUT, f"{self.name}-host.out"))
        self.hl = e2e.GameLog(self.h, start)
        if self.hs == "boot": return True
        if self.hs == "title":
            return e2e.wait_for(lambda: self.hl.grep(r"Created screen 'SignInScreen'", False), 180, 2) and (time.sleep(5) or True)
        if not e2e.wait_for(lambda: self.hl.grep(r"presence: advertising connect", False), 180, 2): return False
        if self.hs in ("lobby", "play"):
            e2e.agent(self.h, "mission", "Easy")
            if not e2e.wait_for(lambda: MAP_B in status(self.h) and e2e.has_hero(self.h), 240, 3): return False
            time.sleep(3)
            if self.hs == "play":
                e2e.agent(self.h, "ready")
                time.sleep(5)
        return True

    def host_sign_in(self):
        for _ in range(60):
            out = e2e.agent(self.h, "signin", timeout=10)
            if "state=7" in out or ("no sign-in screen" in out and self.hl.grep(r"StartSignIn", False)): return True
            time.sleep(2)
        return False

    # ---- client ----
    def client_go(self, start):
        cm = self.cm
        ini = {"cold": [], "ini": ["offline=1", f"join=127.0.0.1:{self.port}"], "title": [], "camp": ["offline=1"],
               "load": [], "fhload": []}[cm]
        write_ini(self.c, ini)
        args = self.connect_string().split() if cm == "cold" else []
        self.cl = e2e.GameLog(self.c, start)
        if self.hs == "boot" or cm in ("cold", "ini"):
            self.cp = launch(self.c, args, os.path.join(e2e.OUT, f"{self.name}-client.out"))
            return True
        self.cp = launch(self.c, [], os.path.join(e2e.OUT, f"{self.name}-client.out"))
        if cm == "load":
            ok = e2e.wait_for(lambda: "pong" in e2e.agent(self.c, "ping", timeout=5), 180, 1)
        elif cm == "fhload":   # Fort Hope just loaded, sign-in screen not up yet (signin_arm's window)
            ok = e2e.wait_for(lambda: self.cl.grep(r"LoadMap Level: MAP_PERS_FortHope_A", False), 180, 0.3)
        elif cm == "title":
            ok = e2e.wait_for(lambda: self.cl.grep(r"Created screen 'SignInScreen'", False), 180, 2) and (time.sleep(5) or True)
        else:   # camp: signed in, hosting its own Fort Hope
            ok = e2e.wait_for(lambda: self.cl.grep(r"presence: advertising connect", False), 180, 2)
        return ok

    def client_trigger(self):
        if self.cm in STEAMJOIN and self.hs != "boot":
            e2e.agent(self.c, "steamjoin", self.connect_string())

    # ---- check ----
    def joined(self):
        sh, sc = status(self.h), status(self.c)
        wh, wc = world(sh), world(sc)
        if "server_conn=yes" not in sc or not wh or wh != wc or "client_conns=1" not in sh: return False
        if MAP_B in wh and not e2e.has_hero(self.c): return False
        return f"{wc}"

    def run(self):
        start = time.time() - 2
        try:
            kill_instance(self.h); kill_instance(self.c)
            for n in (self.h, self.c):
                testprefix.restore_golden(os.path.join(ROOT, f"test{n}"))
            if self.hs == "boot":
                self.host_up(start)
                self.client_go(start)
                t0 = time.time()
            else:
                if self.cm in STEAMJOIN:   # get the client into its state first, in parallel
                    cth = threading.Thread(target=lambda: setattr(self, "cok", self.client_go(start)))
                    cth.start()
                if not self.host_up(start):
                    self.res["why"] = "host never reached its state"; return self.res
                if self.cm in STEAMJOIN:
                    cth.join()
                    if not self.cok: self.res["why"] = "client never reached its state"; return self.res
                    if self.hs == "mload": e2e.agent(self.h, "mission", "Easy")
                    t0 = time.time()
                    self.client_trigger()
                else:   # cold / ini: the trigger is the client's launch
                    t0 = time.time()
                    self.client_go(start)
                    if self.hs == "mload":   # start the mission when the client is about to join
                        e2e.wait_for(lambda: self.cl.grep(r"signin: answering online/offline popup", False), 180, 1)
                        e2e.agent(self.h, "mission", "Easy")
            if self.hs == "title":
                threading.Timer(30, self.host_sign_in).start()
            v = e2e.wait_for(lambda: self.joined(), self.timeout, 3)
            self.res["secs"] = round(time.time() - t0)
            self.res["ok"] = bool(v)
            if v:   # stays joined (no drop right after)
                time.sleep(8)
                if not self.joined(): self.res["ok"] = False; self.res["why"] = "joined, then dropped"
            if not self.res["ok"] and not self.res["why"]:
                sc = status(self.c); sh = status(self.h)
                self.res["why"] = (f"client world={world(sc) or '?'} conn={'yes' if 'server_conn=yes' in sc else 'no'}; "
                                   f"host world={world(sh) or '?'} clients={re.findall(r'client_conns=(\d+)', sh)}")
        finally:
            self.collect()
            kill_instance(self.h); kill_instance(self.c)
        return self.res

    def collect(self):
        for tag, g in (("host", getattr(self, "hl", None)), ("client", getattr(self, "cl", None))):
            if not g or not g.find(): continue
            txt = g.text(False)
            shutil.copy(g.path, os.path.join(e2e.OUT, f"{self.name}-{tag}.log"))
            for pat, label in ((r"HydraPublicId mismatch", "PROFILE RESET"), (r"suppressed \?closed", "closed suppressed"),
                               (r"handshake failed during follow", "handshake retry"), (r"NetworkFailure: (\w+)", "netfail"),
                               (r"auto: joining ", "join attempt"), (r"giving up", "gave up"),
                               (r"Login request", "login"), (r"Kicking remote clients", "host kicked clients")):
                k = len(re.findall(pat, txt))
                if k: self.note(f"{tag} {label} x{k}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", default="")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--pairs", type=int, default=1, choices=(1, 2, 3))
    ap.add_argument("--gpus", default="", help="B4B_GPU per pair, e.g. 4090,4090,5090")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list: print(__doc__); return
    GPUS[:] = [g.strip() for g in a.gpus.split(",")] if a.gpus else []
    reps = {}
    for c in a.cells.split(","):   # h:c or h:c*N (N reps for that cell)
        if c: reps[tuple(c.split("*")[0].split(":"))] = int(c.split("*")[1]) if "*" in c else a.reps
    cells = list(reps) or \
        [(h, c) for h in HOSTS for c in CLIENTS if h != "boot" or c in ("cold", "ini")]
    for h, c in cells:
        if h not in HOSTS or c not in CLIENTS: sys.exit(f"bad cell {h}:{c}")
    e2e.OUT = a.out or f"/tmp/b4b-jointest{'-l2' if LANE == '2' else ''}-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    os.makedirs(e2e.OUT, exist_ok=True)
    lock = os.path.join(REPO, "launch/gamelock.sh")
    if not a.no_lock: subprocess.run([lock, "acquire", "jointest"], check=True)
    jobs = [(h, c, r) for r in range(1, max(reps.get(x, a.reps) for x in cells) + 1) for h, c in cells
            if r <= reps.get((h, c), a.reps)]
    results = []
    try:
        while jobs:
            batch = [jobs.pop(0) for _ in range(min(a.pairs, len(jobs)))]
            trials = [Trial(p + 1, h, c, r, a.timeout) for p, (h, c, r) in enumerate(batch)]
            ths = [threading.Thread(target=t.run) for t in trials]
            for t in trials: log(f"trial {t.name}")
            for th in ths: th.start()
            for th in ths: th.join()
            for t in trials:
                results.append(t.res)
                log(f"  {t.name}: {'PASS' if t.res['ok'] else 'FAIL'} {t.res['secs']}s {t.res['why']} [{'; '.join(t.res['notes'])}]")
            json.dump(results, open(os.path.join(e2e.OUT, "results.json"), "w"), indent=1)
            subprocess.run([lock, "touch"])
    finally:
        if not a.no_lock: subprocess.run([lock, "release", "jointest"])
    table = {}
    for r in results:
        t = table.setdefault(r["cell"], [0, 0, []])
        t[0 if r["ok"] else 1] += 1
        if r["ok"]: t[2].append(r["secs"])
    lines = [f"{'cell':16} pass fail  join s (median)"]
    for cell, (p, f, s) in table.items():
        s.sort()
        lines.append(f"{cell:16} {p:4} {f:4}  {s[len(s) // 2] if s else '-'}")
    open(os.path.join(e2e.OUT, "table.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"artifacts: {e2e.OUT}")


if __name__ == "__main__":
    main()
