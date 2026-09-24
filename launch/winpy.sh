#!/usr/bin/env bash
# Run a Windows Python script inside the game's Proton prefix: launch/winpy.sh tools/x.py args...
here="$(cd "$(dirname "$0")/.." && pwd)"
steam="$HOME/.local/share/Steam"
export STEAM_COMPAT_DATA_PATH="$steam/steamapps/compatdata/924970" STEAM_COMPAT_CLIENT_INSTALL_PATH="$steam"
exec "${PROTON:-$steam/steamapps/common/Proton - Experimental/proton}" run "$here/vendor/winpy/python.exe" -u "$@"
