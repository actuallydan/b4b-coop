#!/usr/bin/env bash
# Unattended local co-op test: launch N (1-5) game copies, each on its own test prefix/profile, instance 1 hosts
# and the rest join it; then wait until the host sees N players. No clicks needed (agent config offline=1).
#   launch/multi.sh 3            -> test1 = host, test2/test3 join 127.0.0.1:$B4B_GAME_PORT
#   launch/multi-stop.sh         -> kill them (by PID; a game on the real prefix is never touched)
# Instance n: prefix ~/.local/share/b4b-coop/prefixes/test<n>, agent port 47112+n-1 (B4B_AGENT=n-1 for tools/b4b.py),
# log Gobi/Binaries/Win64/b4bcoop-test<n>-<winpid>.log. Env: B4B_GAME_PORT (default 7787, not 7777 so test clients
# can never land in a real session), B4B_STAGGER (s between launches, default 20), B4B_TIMEOUT (s, default 600),
# B4B_FRESH=1 re-clones the prefixes from the real one (~600 MB each, ~20 s), B4B_BLANK="2 3" gives those
# instances a fresh offline profile (no decks/unlocks), B4B_INI_EXTRA="netguard=off;..." appends agent config lines
# to every instance's b4bcoop.ini, B4B_INI_EXTRA<n>="..." to instance n's only. The host's ini has no host= line
# (hosting is the default); joiners get join=127.0.0.1:<port> (loopback: allowed without host_ip=1, and the game's
# sockets are bound to 127.0.0.1). All copies share one Steam account: same player name and
# same offline.<steamid64> id on the host.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
n="${1:?usage: multi.sh N (1-5)}"
[[ $n =~ ^[1-5]$ ]] || { echo "N must be 1-5" >&2; exit 2; }
root="${B4B_TEST_ROOT:-$HOME/.local/share/b4b-coop/prefixes}"
game_port="${B4B_GAME_PORT:-7787}"
port_base="${B4B_PORT_BASE:-47112}"
stagger="${B4B_STAGGER:-20}"
timeout="${B4B_TIMEOUT:-600}"
py="$repo/.venv/bin/python"; [[ -x $py ]] || py=python3
export B4B_TEST_ROOT="$root" B4B_PORT_BASE="$port_base"
die() { echo "multi.sh: $*" >&2; exit 1; }
agent() { B4B_AGENT=$(( $1 - 1 )) timeout 25 "$py" "$repo/tools/b4b.py" "${@:2}" 2>/dev/null; }

# ---- preflight ----
if "$here/multi-stop.sh" --list | grep -q .; then die "test instances already running; run launch/multi-stop.sh first"; fi
for i in $(seq "$n"); do
  ss -ltn | grep -q "127.0.0.1:$(( port_base + i - 1 )) " && die "agent port $(( port_base + i - 1 )) is in use (another game with the agent running?)"
done
ss -lun | grep -q ":$game_port " && die "UDP $game_port is in use; set B4B_GAME_PORT"
fresh=(); [[ ${B4B_FRESH:-0} == 1 ]] && fresh=(--fresh)
for i in $(seq "$n"); do
  if [[ $i == 1 ]]; then role=(--host); else role=(--join "127.0.0.1:$game_port"); fi
  blank=(); [[ " ${B4B_BLANK:-} " == *" $i "* ]] && blank=(--blank)
  "$py" "$repo/tools/testprefix.py" "$i" "${fresh[@]}" "${blank[@]}" "${role[@]}" >/dev/null || die "prefix test$i failed"
done

# game pid (unix) of instance i: the Back4Blood.exe whose environment points at its prefix
game_pid() {
  local p
  for p in $(pgrep -f '^\./Back4Blood\.exe'); do
    grep -qzxF "B4B_PREFIX=$root/test$1" "/proc/$p/environ" 2>/dev/null && { echo "$p"; return; }
  done
}
label() {   # label <i> <text>: rename the instance's window once it exists (titles have trailing spaces)
  local pid id
  pid=$(game_pid "$1"); [[ -n $pid ]] || return 1
  id=$(wmctrl -lp | awk -v p="$pid" '$3==p && /Back 4 Blood *$/ {print $1}' | head -1)
  [[ -n $id ]] || return 1
  wmctrl -i -r "$id" -N "$2"
}

# ---- launch, staggered: host first, clients once the host agent is up ----
for i in $(seq "$n"); do
  args=(); [[ $i == 1 ]] && args=("-Port=$game_port")
  setsid "$here/instance.sh" "$i" "${args[@]}" >"$root/test$i.out" 2>&1 </dev/null &
  echo "started test$i (agent port $(( port_base + i - 1 )), B4B_AGENT=$(( i - 1 )))"
  if [[ $i == 1 ]]; then
    for _ in $(seq 120); do agent 1 ping | grep -q pong && break; sleep 1; done
    agent 1 ping | grep -q pong || die "host agent never came up (see $root/test1.out)"
  fi
  [[ $i -lt $n ]] && sleep "$stagger"
done

# ---- label windows, wait for N players on the host ----
declare -A labelled
deadline=$(( SECONDS + timeout ))
while (( SECONDS < deadline )); do
  for i in $(seq "$n"); do
    [[ -n ${labelled[$i]:-} ]] && continue
    if [[ $i == 1 ]]; then t="B4B #1 HOST"; else t="B4B #$i"; fi
    label "$i" "$t" && labelled[$i]=1
  done
  players=$(agent 1 players | sed -n 's/^\([0-9]\+\) player state.*/\1/p')
  [[ -n $players && $players -ge $n && ${#labelled[@]} -ge $n ]] && break
  sleep 3
done
echo "host sees ${players:-0}/$n player(s)"
agent 1 players
for i in $(seq "$n"); do echo "  test$i: pid $(game_pid "$i") B4B_AGENT=$(( i - 1 ))"; done
[[ ${players:-0} -ge $n ]] || die "timed out after ${timeout}s waiting for $n players (logs: Gobi/Binaries/Win64/b4bcoop-test*-*.log)"
echo "ready"
