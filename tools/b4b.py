#!/usr/bin/env python3
"""CLI for the in-game agent (B4B_AGENT=1 targets the second instance): b4b.py status | exec <console cmd> | find <substr> [max] | call <Class> <Func> [cdo]"""
import os, socket, sys
s = socket.create_connection(("127.0.0.1", 47112 + int(os.environ.get("B4B_AGENT", "0"))), timeout=20)
s.sendall((" ".join(sys.argv[1:]) + "\n").encode())
while d := s.recv(65536): sys.stdout.write(d.decode(errors="replace"))
