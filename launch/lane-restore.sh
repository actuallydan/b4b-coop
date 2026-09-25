#!/usr/bin/env bash
# Lane 2 (launch/lane.sh) tests in the Flatpak Steam copy of the game, which also holds the player build of Dan's
# second Steam account (X3DAudio1_7.dll, xinput1_3.dll, its b4bcoop.ini, readmes, logs). This keeps that intact:
#   lane-restore.sh backup    before the first dev install/run (idempotent: an existing backup is the original and
#                             is kept); install.sh, run.sh and `gamelock.sh acquire` call it on lane 2
#   lane-restore.sh restore   put every backed-up file back byte-identically (sha256-checked), remove what the lane
#                             added (dev DLLs, steam_appid.txt, ...), move lane test logs to
#                             ~/.local/share/b4b-coop/lane2-logs/, then drop the backup; `gamelock.sh release` calls it
#   lane-restore.sh status    backup present or not; Flatpak game running or not
#   lane-restore.sh busy      print PIDs of a game started by Flatpak Steam (Dan playing); exit 1 if none
# Only top-level files of the game root and Gobi/Binaries/Win64 are covered (the only places the lane writes);
# the Flatpak Steam's compatdata and saves are never touched.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
B4B_LANE=2   # always the Flatpak folder (B4B_DIR is ignored here)
source "$here/lane.sh"
game="$lane_game" bin="$lane_game/Gobi/Binaries/Win64"
bk="$HOME/.local/share/b4b-coop/lane2-player-backup"
logs="$HOME/.local/share/b4b-coop/lane2-logs"
# the game's own files: never copied, never removed
is_game_file() { case "$1" in Back4Blood.exe|start_protected_game.exe|libScePad.dll) return 0 ;; esac; return 1; }
# files a lane run may add; anything else unknown is left alone and reported
is_lane_file() { case "$1" in *.dll|steam_appid.txt|b4bcoop*|vkd3d-proton.cache*) return 0 ;; esac; return 1; }

flatpak_game_pids() {
  local p
  for p in $(pgrep -u "$(id -u)" -f 'Back4Blood\.exe'); do
    grep -qzx 'FLATPAK_ID=com.valvesoftware.Steam' "/proc/$p/environ" 2>/dev/null && echo "$p"
  done
  return 0
}

backup() {
  [[ -f $bk/SHA256SUMS ]] && return 0
  [[ -d $bin ]] || { echo "lane-restore: no game at $game" >&2; exit 1; }
  local busy; busy=$(flatpak_game_pids)
  [[ -z $busy ]] || { echo "lane-restore: the Flatpak game is running (pids $busy): not touching its folder" >&2; exit 1; }
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
  (cd "$bk.tmp" && find root bin -type f -print0 | sort -z | xargs -0 -r sha256sum > SHA256SUMS)
  mv "$bk.tmp" "$bk"
  echo "lane-restore: backed up $(wc -l < "$bk/SHA256SUMS") player file(s) of lane 2 into $bk"
}

restore() {
  [[ -f $bk/SHA256SUMS ]] || { echo "lane-restore: no backup, nothing to restore"; return 0; }
  local busy; busy=$(flatpak_game_pids)
  [[ -z $busy ]] || { echo "lane-restore: the Flatpak game is running (pids $busy): not touching its folder" >&2; exit 1; }
  local d sub f n kept=()
  for sub in root bin; do
    d=$game; [[ $sub == bin ]] && d=$bin
    for f in "$d"/*; do
      [[ -f $f ]] || continue
      n=${f##*/}
      is_game_file "$n" && continue
      [[ -e $bk/$sub/$n ]] && continue
      if [[ $sub == bin && $n == b4bcoop-*test*-*.log ]]; then mkdir -p "$logs"; mv -f "$f" "$logs/"
      elif is_lane_file "$n"; then rm -f "$f"
      else kept+=("$f"); fi
    done
    for f in "$bk/$sub"/*; do
      [[ -f $f ]] || continue
      # rm first: a running game maps the old DLL, overwriting it in place would corrupt that mapping
      rm -f "$d/${f##*/}"; cp -p "$f" "$d/"
    done
  done
  # verify against the game folder itself
  local bad=0
  while read -r sum path; do
    sub=${path%%/*} n=${path#*/}; d=$game; [[ $sub == bin ]] && d=$bin
    [[ $(sha256sum < "$d/$n" | cut -d' ' -f1) == "$sum" ]] || { echo "lane-restore: MISMATCH $d/$n" >&2; bad=1; }
  done < "$bk/SHA256SUMS"
  [[ $bad == 0 ]] || { echo "lane-restore: backup kept in $bk" >&2; exit 1; }
  ((${#kept[@]})) && printf 'lane-restore: left unknown new file %s\n' "${kept[@]}"
  echo "lane-restore: restored $(wc -l < "$bk/SHA256SUMS") player file(s) in $game (sha256 verified)"
  rm -rf "$bk"
}

case "${1:-status}" in
  backup) backup ;;
  restore) restore ;;
  busy) p=$(flatpak_game_pids); [[ -n $p ]] && echo $p || exit 1 ;;
  status)
    if [[ -f $bk/SHA256SUMS ]]; then echo "backup present ($bk): lane 2 folder holds test files"; else echo "no backup: lane 2 folder is the player's"; fi
    p=$(flatpak_game_pids); [[ -n $p ]] && echo "Flatpak game running: $p"; true ;;
  *) echo "usage: lane-restore.sh backup|restore|status|busy" >&2; exit 2 ;;
esac
