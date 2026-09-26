"""Live-test lanes (B4B_LANE=1 default, 2 = the Flatpak Steam game copy; B4B_STEAM=flatpak implies 2); same table as launch/lane.sh.
B4B_DIR, B4B_TEST_ROOT, B4B_PORT_BASE, B4B_GAME_PORT still override the lane's values."""
import os, sys

_H = os.path.expanduser("~")
_LANES = {
    "1": dict(game=f"{_H}/.local/share/Steam/steamapps/common/Back 4 Blood", root=f"{_H}/.local/share/b4b-coop/prefixes",
              game_port=7787, port_base=47112, win="B4B"),
    "2": dict(game=f"{_H}/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/Back 4 Blood",
              root=f"{_H}/.local/share/b4b-coop/prefixes/lane2", game_port=7887, port_base=47140, win="B4B L2"),
}
STEAM = os.environ.get("B4B_STEAM", "native") or "native"   # flatpak: lane 2 only, and its default (launch/lane.sh)
if STEAM not in ("native", "flatpak"): sys.exit("B4B_STEAM must be native or flatpak")
LANE = os.environ.get("B4B_LANE", "2" if STEAM == "flatpak" else "1") or "1"
if STEAM == "flatpak" and LANE != "2": sys.exit("B4B_STEAM=flatpak runs on lane 2 only (the Flatpak Steam's game copy)")
if LANE not in _LANES: sys.exit("B4B_LANE must be 1 or 2")
_L = _LANES[LANE]
GAME = os.environ.get("B4B_DIR", _L["game"])
ROOT = os.path.expanduser(os.environ.get("B4B_TEST_ROOT", _L["root"]))
PORT_BASE = int(os.environ.get("B4B_PORT_BASE", str(_L["port_base"])))
GAME_PORT = int(os.environ.get("B4B_GAME_PORT", str(_L["game_port"])))
WIN = _L["win"]   # window label prefix (launch/multi.sh): "<WIN> #n", host "<WIN> #1 HOST"
