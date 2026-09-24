#!/usr/bin/env bash
# Run local test instance N on its own prefix (~/.local/share/b4b-coop/prefixes/testN, see tools/testprefix.py)
# with its own agent config and agent port (B4B_PORT_BASE + N - 1). Extra args go to the game.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
n="${1:?usage: instance.sh N [game args]}"; shift
root="${B4B_TEST_ROOT:-$HOME/.local/share/b4b-coop/prefixes}"
prefix="$root/test$n"
[[ -d $prefix/pfx ]] || { echo "no prefix $prefix (run tools/testprefix.py $n)" >&2; exit 1; }
export B4B_PREFIX="$prefix"
export B4B_COOP_CONFIG="Z:${prefix//\//\\}\\b4bcoop.ini"
export B4B_COOP_PORT=$(( ${B4B_PORT_BASE:-47112} + n - 1 ))
export B4B_COOP_TAG="test$n"
exec "$here/run.sh" "$@"
