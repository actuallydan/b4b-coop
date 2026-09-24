#!/usr/bin/env bash
# Stop the local test instances started by launch/multi.sh / instance.sh — only processes running on a test prefix
# (B4B_PREFIX under ~/.local/share/b4b-coop/prefixes), matched by PID via /proc/<pid>/environ.
# A game on the real prefix is never touched. `multi-stop.sh 3` stops only test3; `--list` only prints PIDs.
set -uo pipefail
root="${B4B_TEST_ROOT:-$HOME/.local/share/b4b-coop/prefixes}"
list=0; [[ ${1:-} == --list ]] && { list=1; shift; }   # --list: print matching PIDs, kill nothing
one="${1:-}"
want="B4B_PREFIX=$root/test$one"   # exported by instance.sh; Proton resets STEAM_COMPAT_DATA_PATH in the game

pids_on_test_prefix() {
  local p
  for p in $(pgrep -u "$(id -u)"); do
    [[ $p == "$$" || $p == "$BASHPID" ]] && continue
    if [[ -n $one ]]; then grep -qzxF -- "$want" "/proc/$p/environ" 2>/dev/null || continue
    else grep -qz -- "^$want" "/proc/$p/environ" 2>/dev/null || continue; fi
    echo "$p"
  done
}

mapfile -t pids < <(pids_on_test_prefix)
[[ $list == 1 ]] && { printf '%s\n' "${pids[@]}" | grep .; exit 0; }
[[ ${#pids[@]} -eq 0 ]] && { echo "no test instances running"; exit 0; }
echo "stopping ${#pids[@]} process(es): ${pids[*]}"
# SIGKILL: on SIGTERM the Wine game process exits its main thread but leaves ~200 threads parked in ntsync
# (a "zombie" that still holds its UDP port) once its wineserver is gone.
kill -9 "${pids[@]}" 2>/dev/null
for _ in $(seq 20); do
  alive=(); for p in "${pids[@]}"; do [[ -d /proc/$p ]] && alive+=("$p"); done
  [[ ${#alive[@]} -eq 0 ]] && break
  sleep 0.5
done
[[ ${#alive[@]} -gt 0 ]] && { echo "still alive: ${alive[*]}" >&2; exit 1; }
echo "stopped"
