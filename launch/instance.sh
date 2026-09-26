#!/usr/bin/env bash
# Run local test instance N on its own prefix (~/.local/share/b4b-coop/prefixes/testN, see tools/testprefix.py)
# with its own agent config and agent port (B4B_PORT_BASE + N - 1). Extra args go to the game.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
source "$here/lane.sh"   # B4B_LANE=2: prefixes/lane2/test<n>, agent ports 47140+
n="${1:?usage: instance.sh N [game args]}"; shift
root="${B4B_TEST_ROOT:-$lane_root}"
prefix="$root/test$n"
[[ -d $prefix/pfx ]] || { echo "no prefix $prefix (run tools/testprefix.py $n)" >&2; exit 1; }
export B4B_PREFIX="$prefix"
export B4B_COOP_CONFIG="Z:${prefix//\//\\}\\b4bcoop.ini"
export B4B_COOP_PORT=$(( ${B4B_PORT_BASE:-$lane_port_base} + n - 1 ))
export B4B_COOP_TAG="test$n"
if [[ -n ${B4B_GPU:-} ]]; then   # render on another GPU (lane.sh): a GPU with no display can't present to the desktop,
  # so the game runs in a headless gamescope that composites on that GPU; vkd3d/DXVK pick it by name
  id=$(nvidia-smi --query-gpu=name,pci.device_id --format=csv,noheader | awk -F', ' -v g="$B4B_GPU" 'index($1, g) {print $2; exit}')
  [[ -n $id ]] || { echo "B4B_GPU=$B4B_GPU: no such GPU (nvidia-smi)" >&2; exit 1; }
  id=${id,,}   # 0x<device><vendor>, e.g. 0x268410de
  export VKD3D_FILTER_DEVICE_NAME="$B4B_GPU" DXVK_FILTER_DEVICE_NAME="$B4B_GPU"
  exec gamescope --backend headless --prefer-vk-device "${id:6:4}:${id:2:4}" -W 960 -H 540 -w 960 -h 540 -- "$here/run.sh" "$@"
fi
exec "$here/run.sh" "$@"
