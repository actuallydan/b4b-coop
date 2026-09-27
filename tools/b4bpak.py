#!/usr/bin/env python3
"""Moved to modkit/b4bpak.py (the mod makers' kit). This stub keeps `tools/b4bpak.py info|list|pack` working."""
import os, runpy, sys

real = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modkit", "b4bpak.py")
sys.argv[0] = real
runpy.run_path(real, run_name="__main__")
