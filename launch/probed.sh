#!/usr/bin/env bash
# Start the memory probe daemon (Windows Python) in the running game's Proton prefix.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
steam="$HOME/.local/share/Steam"
proton="${PROTON:-$steam/steamapps/common/Proton - Experimental/proton}"
export STEAM_COMPAT_DATA_PATH="$steam/steamapps/compatdata/924970"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$steam"
exec "$proton" run "$here/vendor/winpy/python.exe" -u "$here/tools/probed.py"
