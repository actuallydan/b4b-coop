#!/usr/bin/env python3
"""Live test of the add-on shop, the `~` window's Browse tab (#36, docs/investigations/shop.md "Tests"), on test
instance 1 with a local test catalog.

The source folder (--src, default ~/.local/share/b4b-coop/shop-test/src; never committed: test paks) holds shop.json
(tools/shop-catalog.py format, with "{base}" in url_template/thumb_template), paks/ and thumbs/. Expected ids:
casual_joe (an added outfit), ak47 (an added weapon look), holly_green (a replacement of a game file; its update is
paks/holly_magenta.pak), future (min_b4bcoop 9.0.0). The catalog is built and signed with a throwaway key and served
on 127.0.0.1; the instance runs the dev build under test with shop_catalog= / shop_pubkey= and its own empty add-ons
folder (addons_dir= in the test prefix). Steps:
  1. no add-ons folder at start; a list with a bad signature is refused; the good list shows 4 add-ons + thumbnails
  2. refused Add: needs a newer b4bcoop; damaged download (SHA-256); truncated download; nothing is left behind
  3. Add casual_joe (the tab's button) in Fort Hope: mounted at once, /model casual_joe worn
  4. Add holly_green: added, applies after a restart (replaces a game file), not mounted
  5. in a mission: Add ak47: mounted at once, weapon look usable; Remove ak47: switched off, removed after restart
  6. restart: ak47 deleted and gone from addonlist.txt, holly_green and casual_joe mounted at start
  7. a newer holly_green in the list: Update -> .new staged -> restart -> the new file is in place
Needs the build under test installed (launch/install.sh) and the lane's lock (taken here unless --no-lock).
  B4B_LANE=2 B4B_STEAM=flatpak B4B_GPU=4090 tools/shop-test.py [--no-lock]"""
import argparse, glob, hashlib, http.server, json, os, re, shutil, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e
from lane import GAME, ROOT, LANE

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(GAME, "Gobi", "Binaries", "Win64")
OUT = f"/tmp/b4b-shop{'-l2' if LANE == 2 else ''}-{time.strftime('%Y%m%d-%H%M%S')}"
ADDONS = os.path.join(ROOT, "test1", "shop-addons")
results = []


def log(msg): e2e.log(msg)


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    log(f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")
    return ok


def sha(p):
    try: return hashlib.sha256(open(p, "rb").read()).hexdigest()
    except OSError: return None


def winpath(p): return "Z:" + p.replace("/", "\\")


class Shop:
    """The test catalog: built by tools/shop-catalog.py from --src, signed with a throwaway key, served here."""
    def __init__(self, src):
        self.src, self.dir = src, os.path.join(OUT, "catalog")
        os.makedirs(self.dir)
        self.key = os.path.join(OUT, "test-signing.key")
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", self.key], check=True, capture_output=True)
        self.pubhex = subprocess.run([os.path.join(REPO, "tools/sign-release.sh"), "pubhex", self.key], check=True,
                                     capture_output=True, text=True).stdout.strip()
        self.mode, self.swap = "ok", {}

    def build(self, port, overrides=None):
        m = json.load(open(os.path.join(self.src, "shop.json")))
        base = f"http://127.0.0.1:{port}"
        m["url_template"] = m["url_template"].replace("{base}", base)
        m["thumb_template"] = m["thumb_template"].replace("{base}", base)
        for e in m["addons"]:
            for k, v in (overrides or {}).get(e["id"], {}).items(): e[k] = v
        mp = os.path.join(self.src, ".shop-test.json")
        json.dump(m, open(mp, "w"))
        cat = os.path.join(self.dir, "catalog.json")
        r = subprocess.run([sys.executable, os.path.join(REPO, "tools/shop-catalog.py"), "build", mp, "-o", cat], capture_output=True, text=True)
        os.remove(mp)
        open(os.path.join(OUT, "catalog-build.log"), "a").write(r.stdout + r.stderr)
        subprocess.run([sys.executable, os.path.join(REPO, "tools/shop-catalog.py"), "sign", self.key, cat], check=True, capture_output=True)
        self.files = {"catalog.json": open(cat, "rb").read(), "catalog.json.sig": open(cat + ".sig", "rb").read()}
        for e in m["addons"]:
            self.files[f"{e['id']}.pak"] = os.path.join(self.src, e.get("pak", f"paks/{e['id']}.pak"))
            if e.get("thumb"): self.files[f"thumbs/{e['thumb']}"] = os.path.join(self.src, "thumbs", e["thumb"])
        log(f"catalog: {len(json.loads(self.files['catalog.json'])['addons'])} add-on(s), key {self.pubhex[:16]}...")


class Handler(http.server.BaseHTTPRequestHandler):
    shop: Shop = None

    def log_message(self, fmt, *a):
        with open(os.path.join(OUT, "server.log"), "a") as f: f.write(f"{time.strftime('%H:%M:%S')} {self.shop.mode} {fmt % a}\n")

    def do_GET(self):
        s, name = self.shop, self.path.lstrip("/")
        if name not in s.files:
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        v = s.files[name]
        data = v if isinstance(v, bytes) else open(v, "rb").read()
        if s.mode == "badsig" and name == "catalog.json.sig": data = bytes([data[0] ^ 1]) + data[1:]
        if s.mode == "badpak" and name.endswith(".pak"): data = data[:5000] + bytes([data[5000] ^ 0x10]) + data[5001:]
        full = len(data)
        if s.mode == "truncated" and name.endswith(".pak"): data = data[: full // 3]
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(full))
        self.end_headers()
        try: self.wfile.write(data)
        except OSError: pass
        if len(data) != full: self.close_connection = True


def status():
    return e2e.agent(1, "shop", "status")


def row(s, aid):
    m = re.search(rf"^  {re.escape(aid)} \[([\w-]+)\] dl=(\S+) pending=(\d) thumb=(\S+) local=(\S*) msg=(.*)$", s, re.M)
    return m.groups() if m else None


def wait_shop(pred, t=180):
    return e2e.wait_for(lambda: (lambda s: s if pred(s) else None)(status()), t, 1) or status()


def wait_item(aid, t=300):
    """until the item's download is finished (dl done/error) and no worker runs"""
    return wait_shop(lambda s: "busy=0" in s and row(s, aid) and row(s, aid)[1] in ("done", "error", "-"), t)


def addonlist():
    try: return open(os.path.join(ADDONS, "addonlist.txt"), errors="replace").read()
    except OSError: return ""


def start(S, extra):
    S.ini_extra = ";".join(extra)
    ok = S.launch()
    up = e2e.wait_for(lambda: "shop:" in status() and e2e.has_hero(1), 300, 3)   # in Fort Hope with a hero
    return check("instance 1 up (Fort Hope)", ok and up)


def shot(name):
    e2e.screenshot(1, name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--src", default=os.path.expanduser("~/.local/share/b4b-coop/shop-test/src"))
    a = ap.parse_args()
    os.makedirs(OUT)
    e2e.OUT = OUT
    log(f"lane {LANE}, game {GAME}, out {OUT}")
    lock = os.path.join(REPO, "launch", "gamelock.sh")
    if not a.no_lock: subprocess.run([lock, "acquire", "shop-test"], check=True)
    S = e2e.Session("shop", 1)
    try:
        run(a, S)
    finally:
        S.stop()
        if not a.no_lock: subprocess.run([lock, "release", "shop-test"])
    w = max(len(n) for n, _, _ in results) if results else 10
    print("\n" + "\n".join(f"{'PASS' if ok else 'FAIL'}  {n:<{w}}  {d}" for n, ok, d in results))
    bad = sum(not ok for _, ok, _ in results)
    print(f"\n{len(results) - bad}/{len(results)} passed; artifacts in {OUT}")
    sys.exit(1 if bad else 0)


def run(a, S):
    shop = Shop(a.src)
    Handler.shop = shop
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    shop.build(port)
    log(f"test catalog on 127.0.0.1:{port}")
    if os.path.isdir(ADDONS): shutil.rmtree(ADDONS)
    extra = [f"shop_catalog=http://127.0.0.1:{port}/catalog.json", f"shop_pubkey={shop.pubhex}", f"addons_dir={winpath(ADDONS)}"]

    # 1. the list
    if not start(S, extra): return
    g = S.logs[1]
    check("no add-ons folder at start", bool(g.grep(r"addons: no add-ons folder", False)))
    e2e.agent(1, "overlay", "open"); e2e.agent(1, "overlay", "tab", "Browse")
    shop.mode = "badsig"
    e2e.agent(1, "overlay", "press", "Get the add-on list")
    s = wait_shop(lambda s: "phase=error" in s or "phase=ready" in s, 60)
    check("list with a bad signature refused", "phase=error" in s and "signature" in s, re.search(r"msg=(.*)", s).group(1)[:120] if "msg=" in s else s[:120])
    shop.mode = "ok"
    e2e.agent(1, "overlay", "press", "Get the add-on list")
    s = wait_shop(lambda s: "phase=ready" in s and "busy=0" in s and "items=4" in s, 90)
    check("list shown (4 add-ons)", "items=4" in s, re.search(r"msg=(.*)", s).group(1)[:120] if "msg=" in s else s[:120])
    time.sleep(2)
    s = status()
    th = {i: (row(s, i) or [None] * 6)[3] for i in ("casual_joe", "ak47", "holly_green")}
    check("thumbnails decoded and uploaded (PNG + JPEG)", all(v == "uploaded" for v in th.values()), str(th))
    check("netguard scope around the requests", bool(g.grep(r"netguard: updater scope closed", False)))
    shot("1-browse-list.png")

    # 2. refused downloads
    r = e2e.agent(1, "shop", "add", "future")
    check("Add refused: needs a newer b4bcoop", "needs b4bcoop 9.0.0" in r, r.splitlines()[0] if r else "")
    for mode, want in (("badpak", "damaged"), ("truncated", "")):
        shop.mode = mode
        e2e.agent(1, "shop", "add", "ak47")
        s = wait_item("ak47")
        rw = row(s, "ak47") or ("?",) * 6
        check(f"{mode}: download refused", rw[1] == "error" and want in rw[5], rw[5][:140])
        left = sorted(os.listdir(ADDONS)) + sorted(os.listdir(os.path.join(ADDONS, ".shop"))) if os.path.isdir(ADDONS) else []
        check(f"{mode}: nothing left behind", not [f for f in left if f.endswith((".pak", ".part"))], str(left))
    shop.mode = "ok"

    # 3. an added outfit, usable at once
    g.mark()
    e2e.agent(1, "overlay", "press", "Add##casual_joe")
    s = wait_item("casual_joe")
    rw = row(s, "casual_joe") or ("?",) * 6
    check("Add casual_joe: downloaded, verified, ready now", rw[1] == "done" and "ready now" in rw[5], rw[5][:140])
    check("casual_joe mounted at runtime", bool(g.grep(r"addons: casual_joe\.pak -> mounted \(read order \d+, at runtime\)")))
    check("addonlist.txt has casual_joe.pak=1", "casual_joe.pak=1" in addonlist())
    e2e.agent(1, "overlay", "close")
    e2e.agent(1, "thirdperson", "on")
    out = e2e.agent(1, "model", "casual_joe")
    worn = e2e.wait_for(lambda: g.grep(r"models: hero slot \d+ wears outfit casual_joe\b"), 30, 1)
    check("/model casual_joe right after Add", bool(worn), out.strip().splitlines()[0][:100] if out.strip() else "")
    time.sleep(3)
    shot("3-casual_joe-worn.png")
    e2e.agent(1, "overlay", "open"); e2e.agent(1, "overlay", "tab", "Browse")
    time.sleep(1)
    shot("3-browse-after-add.png")

    # 4. a replacement: after a restart
    g.mark()
    e2e.agent(1, "shop", "add", "holly_green")
    s = wait_item("holly_green")
    rw = row(s, "holly_green") or ("?",) * 6
    check("Add holly_green: applies after a restart (replaces a game file)", rw[1] == "done" and "after a restart" in rw[5] and "replaces" in rw[5], rw[5][:140])
    check("holly_green not mounted now", not g.grep(r"holly_green\.pak -> mounted"))

    # 5. in a mission: add + remove
    e2e.agent(1, "overlay", "close")
    e2e.agent(1, "mission", "Easy")
    inm = e2e.wait_for(lambda: e2e.MAP_B in S.world(1) and e2e.has_hero(1), 300, 5)
    if check("mission loaded", bool(inm), S.world(1)):
        e2e.agent(1, "ready")   # past the character select: the match runs
        time.sleep(20)
        g.mark()
        e2e.agent(1, "shop", "add", "ak47")
        s = wait_item("ak47")
        rw = row(s, "ak47") or ("?",) * 6
        check("in a mission: Add ak47 ready now", rw[1] == "done" and "ready now" in rw[5], rw[5][:140])
        check("ak47 mounted at runtime in the mission", bool(g.grep(r"addons: ak47\.pak -> mounted \(read order \d+, at runtime\)")))
        out = e2e.agent(1, "model", "ak47")
        check("weapon look ak47 usable right after Add", "now looks like" in out, out.strip().splitlines()[0][:120] if out.strip() else "")
        e2e.agent(1, "thirdperson", "on")
        time.sleep(20)
        check("game alive 20 s after the mission mount", e2e.MAP_B in S.world(1) and e2e.has_hero(1))
        shot("5-mission-ak47.png")
        e2e.agent(1, "overlay", "open"); e2e.agent(1, "overlay", "tab", "Browse")
        e2e.agent(1, "overlay", "press", "Remove##ak47"); e2e.agent(1, "overlay", "press", "Remove##ak47")   # confirm click
        time.sleep(2)
        s = status()
        rw = row(s, "ak47") or ("?",) * 6
        check("Remove ak47: pending, switched off", rw[0] == "remove-pending" and "ak47.pak=0" in addonlist(), f"{rw[0]} / {rw[5][:80]}")
        shot("5-browse-remove-pending.png")

    # 6. restart
    S.stop()
    time.sleep(3)
    if not start(S, extra): return
    g = S.logs[1]
    check("restart: ak47 deleted", not os.path.exists(os.path.join(ADDONS, "ak47.pak")) and bool(g.grep(r"shop: removed ak47\.pak", False)))
    check("restart: ak47 gone from addonlist.txt", "ak47" not in addonlist(), addonlist().replace("\r\n", " | ")[-160:])
    check("restart: holly_green mounted at start", bool(g.grep(r"addons: holly_green\.pak -> mounted \(read order", False)))
    check("restart: casual_joe mounted at start", bool(g.grep(r"addons: casual_joe\.pak -> mounted \(read order", False)))

    # 7. update
    shop.build(port, {"holly_green": {"pak": "paks/holly_magenta.pak", "version": "1.1"}})
    e2e.agent(1, "shop", "fetch")
    s = wait_shop(lambda s: "phase=ready" in s and "busy=0" in s and "items=4" in s, 90)
    s = wait_shop(lambda s: (row(s, "holly_green") or ["?"])[0] == "update-available", 30)
    rw = row(s, "holly_green") or ("?",) * 6
    check("newer holly_green offered as an update", rw[0] == "update-available", rw[0])
    e2e.agent(1, "shop", "add", "holly_green")
    s = wait_item("holly_green")
    rw = row(s, "holly_green") or ("?",) * 6
    check("update downloaded, waits for the restart", rw[0] == "update-pending" and os.path.exists(os.path.join(ADDONS, ".shop", "holly_green.pak.new")), rw[5][:120])
    S.stop()
    time.sleep(3)
    if not start(S, extra): return
    g = S.logs[1]
    want = sha(os.path.join(a.src, "paks", "holly_magenta.pak"))
    check("restart: the updated file is in place", sha(os.path.join(ADDONS, "holly_green.pak")) == want and bool(g.grep(r"shop: updated holly_green\.pak", False)))
    srv.shutdown()


if __name__ == "__main__":
    main()
