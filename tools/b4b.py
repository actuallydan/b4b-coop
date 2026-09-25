#!/usr/bin/env python3
"""CLI for the in-game agent (B4B_AGENT=n-1 targets instance n: port B4B_PORT_BASE(47112, lane 2: 47140)+B4B_AGENT): b4b.py status | exec <console cmd> | find <substr> [max] | call <Class> <Func> [cdo]"""
import os, socket, sys
from lane import PORT_BASE   # B4B_LANE (tools/lane.py)
port = PORT_BASE + int(os.environ.get("B4B_AGENT", "0"))
s = socket.create_connection(("127.0.0.1", port), timeout=20)
s.sendall((" ".join(sys.argv[1:]) + "\n").encode())
while d := s.recv(65536): sys.stdout.write(d.decode(errors="replace"))
