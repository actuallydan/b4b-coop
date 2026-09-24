"""Probe daemon. Runs as *Windows* Python inside the game's Proton prefix (launch/probed.sh), attaches to
the running Back4Blood.exe with ReadProcessMemory, and executes Python snippets sent over TCP against a
live `memprobe.Game` bound to `g`.

    launch/probed.sh                     # start (after the game is up)
    tools/probe.py 'print(g.load_objects())'
"""
import contextlib, io, os, socket, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memprobe

PORT = int(os.environ.get("B4B_PROBE_PORT", "47111"))
env = {"memprobe": memprobe, "g": None}

srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", PORT)); srv.listen(1)
print(f"probe daemon on 127.0.0.1:{PORT}", flush=True)
while True:
    c, _ = srv.accept()
    code = b""
    while chunk := c.recv(65536): code += chunk
    out = io.StringIO()
    try:
        if env["g"] is None: env["g"] = memprobe.Game(memprobe.find_pid())
        with contextlib.redirect_stdout(out): exec(code.decode(), env)
    except Exception: out.write(traceback.format_exc())
    c.sendall(out.getvalue().encode()); c.close()
