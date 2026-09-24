"""Send a Python snippet to the probe daemon: tools/probe.py 'code'  (or code on stdin)."""
import os, socket, sys
s = socket.create_connection(("127.0.0.1", int(os.environ.get("B4B_PROBE_PORT", "47111"))))
s.sendall((sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()).encode()); s.shutdown(socket.SHUT_WR)
while d := s.recv(65536): sys.stdout.write(d.decode(errors="replace"))
