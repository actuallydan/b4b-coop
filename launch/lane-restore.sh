#!/usr/bin/env bash
# Both live-test lanes (launch/lane.sh) test in a game folder that also holds one of Dan's player installs:
#   lane 1: the native Steam game (his own account's player build: xinput1_3.dll, b4bcoop-*.txt, X3DAudio1_7.dll,
#           b4bcoop.ini, his logs, maybe b4bcoop-addons/)
#   lane 2: the Flatpak Steam copy (his second account's player build, same files)
# This keeps that install intact (B4B_LANE picks the lane, default 1; B4B_DIR is ignored here):
#   lane-restore.sh backup    before the first dev install/run (idempotent: an existing backup is the original and
#                             is kept); `gamelock.sh acquire` calls it, and install.sh/run.sh (lane 2 always, lane 1
#                             while its lock is held)
#   lane-restore.sh restore   put every backed-up file back byte-identically (sha256-checked), remove what the lane
#                             added (dev DLLs, steam_appid.txt, b4bcoop-addons/, ...), move the lane's test logs to a
#                             new ~/.local/share/b4b-coop/lane<n>-logs/<time>/, then drop the backup;
#                             `gamelock.sh release` calls it (after multi-stop.sh)
#   lane-restore.sh status    backup present or not; player's game running or not
#   lane-restore.sh busy      print PIDs of the player's own game in this lane's folder (Dan playing); exit 1 if none
# Covered: top-level files of the game root and Gobi/Binaries/Win64, and <game>/b4bcoop-addons/ (the only places a
# lane writes); compatdata and saves are never touched.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
unset B4B_DIR
source "$here/lane.sh"
game="$lane_game" bin="$lane_game/Gobi/Binaries/Win64"
bk="$HOME/.local/share/b4b-coop/lane$B4B_LANE-player-backup"
logs="$HOME/.local/share/b4b-coop/lane$B4B_LANE-logs"
dirs=(b4bcoop-addons)   # directories in the game root covered as a whole (add-ons live next to Back4Blood.exe)
# the game's own files: never copied, never removed
is_game_file() { case "$1" in Back4Blood.exe|start_protected_game.exe|libScePad.dll) return 0 ;; esac; return 1; }
# files a lane run may add; anything else unknown is left alone and reported
is_lane_file() { case "$1" in *.dll|steam_appid.txt|b4bcoop*|vkd3d-proton.cache*) return 0 ;; esac; return 1; }

# The player's game: lane 2 = started by Flatpak Steam (a B4B_STEAM=flatpak test instance runs there too, with
# B4B_PREFIX); lane 1 = a native game that is no test instance (no
# B4B_PREFIX, which launch/instance.sh exports) and not the Flatpak one.
# Only the game process itself (comm Back4Blood.exe), not wrappers or shells that mention it; a process that exits
# while we look is skipped.
player_game_pids() {
  local p env
  for p in $(pgrep -u "$(id -u)" -x 'Back4Blood\.exe'); do
    env=$(tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null) || continue
    [[ -n $env ]] || continue
    if [[ $B4B_LANE == 2 ]]; then
      grep -qx 'FLATPAK_ID=com.valvesoftware.Steam' <<<"$env" && ! grep -q '^B4B_PREFIX=' <<<"$env" && echo "$p"
    else
      grep -q -e '^B4B_PREFIX=' -e '^FLATPAK_ID=' <<<"$env" || echo "$p"
    fi
  done
  return 0
}
not_busy() {
  local busy; busy=$(player_game_pids)
  [[ -z $busy ]] || { echo "lane-restore: lane $B4B_LANE: the player's game is running (pids $busy): not touching its folder" >&2; exit 1; }
}

backup() {
  [[ -f $bk/SHA256SUMS ]] && return 0
  [[ -d $bin ]] || { echo "lane-restore: no game at $game" >&2; exit 1; }
  not_busy
  rm -rf "$bk.tmp"; mkdir -p "$bk.tmp/root" "$bk.tmp/bin"
  local d sub f
  for sub in root bin; do
    d=$game; [[ $sub == bin ]] && d=$bin
    for f in "$d"/*; do
      [[ -f $f && ! -L $f ]] || continue
      is_game_file "${f##*/}" && continue
      cp -p "$f" "$bk.tmp/$sub/"
    done
  done
  for f in "${dirs[@]}"; do [[ -d $game/$f ]] && cp -a "$game/$f" "$bk.tmp/root/"; done
  (cd "$bk.tmp" && find root bin -type f -print0 | sort -z | xargs -0 -r sha256sum > SHA256SUMS)
  mv "$bk.tmp" "$bk"
  echo "lane-restore: backed up $(wc -l < "$bk/SHA256SUMS") player file(s) of lane $B4B_LANE into $bk"
}

restore() {
  [[ -f $bk/SHA256SUMS ]] || { echo "lane-restore: lane $B4B_LANE: no backup, nothing to restore"; return 0; }
  not_busy
  local d sub f n kept=() moved=0 dest
  dest="$logs/$(date +%Y%m%d-%H%M%S)"
  for sub in root bin; do
    d=$game; [[ $sub == bin ]] && d=$bin
    for f in "$d"/*; do
      [[ -f $f ]] || continue
      n=${f##*/}
      is_game_file "$n" && continue
      [[ -e $bk/$sub/$n ]] && continue
      if [[ $sub == bin && $n == b4bcoop-*test*-*.log ]]; then mkdir -p "$dest"; mv -n "$f" "$dest/"; moved=$((moved + 1))
      elif is_lane_file "$n"; then rm -f "$f"
      else kept+=("$f"); fi
    done
    for f in "$bk/$sub"/*; do
      [[ -f $f ]] || continue
      # rm first: a running game maps the old DLL, overwriting it in place would corrupt that mapping
      rm -f "$d/${f##*/}"; cp -p "$f" "$d/"
    done
  done
  for f in "${dirs[@]}"; do
    rm -rf "${game:?}/$f"
    [[ -d $bk/root/$f ]] && cp -a "$bk/root/$f" "$game/"
  done
  # verify against the game folder itself
  local bad=0
  while read -r sum path; do
    sub=${path%%/*} n=${path#*/}; d=$game; [[ $sub == bin ]] && d=$bin
    [[ $(sha256sum < "$d/$n" | cut -d' ' -f1) == "$sum" ]] || { echo "lane-restore: MISMATCH $d/$n" >&2; bad=1; }
  done < "$bk/SHA256SUMS"
  [[ $bad == 0 ]] || { echo "lane-restore: backup kept in $bk" >&2; exit 1; }
  ((${#kept[@]})) && printf 'lane-restore: left unknown new file %s\n' "${kept[@]}"
  ((moved)) && echo "lane-restore: moved $moved test log(s) to $dest"
  echo "lane-restore: restored $(wc -l < "$bk/SHA256SUMS") player file(s) in $game (sha256 verified)"
  rm -rf "$bk"
}

case "${1:-status}" in
  backup) backup ;;
  restore) restore ;;
  busy) p=$(player_game_pids); [[ -n $p ]] && echo $p || exit 1 ;;
  status)
    if [[ -f $bk/SHA256SUMS ]]; then echo "lane $B4B_LANE: backup present ($bk): folder holds test files"
    else echo "lane $B4B_LANE: no backup: folder is the player's"; fi
    p=$(player_game_pids); [[ -n $p ]] && echo "player's game running: $p"; true ;;
  *) echo "usage: [B4B_LANE=1|2] lane-restore.sh backup|restore|status|busy" >&2; exit 2 ;;
esac
