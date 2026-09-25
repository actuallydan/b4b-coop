# Sourced by the launch scripts: live-test "lanes", i.e. independent game installs so two agents can run test
# instances at the same time. B4B_LANE=1 (default) or 2; tools/lane.py is the Python copy of this table.
#   lane 1: native Steam game dir, lock /tmp/b4b-game.lock, prefixes ~/.local/share/b4b-coop/prefixes/test<n>,
#           game port 7787, agent ports 47112+, windows "B4B #n"
#   lane 2: the Flatpak Steam copy of the game (same build), lock /tmp/b4b-game-lane2.lock, prefixes
#           ~/.local/share/b4b-coop/prefixes/lane2/test<n>, game port 7887, agent ports 47140+, windows "B4B L2 #n".
#           That folder holds the player build of Dan's second account: launch/lane-restore.sh backs it up before
#           the first dev install/run and restores it on `gamelock.sh release`.
# B4B_DIR, B4B_TEST_ROOT, B4B_GAME_PORT, B4B_PORT_BASE still override the lane's values.
B4B_LANE="${B4B_LANE:-1}"
case "$B4B_LANE" in
  1) lane_game="$HOME/.local/share/Steam/steamapps/common/Back 4 Blood"
     lane_root="$HOME/.local/share/b4b-coop/prefixes"
     lane_lock=/tmp/b4b-game.lock lane_game_port=7787 lane_port_base=47112 lane_win="B4B" ;;
  2) lane_game="$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/Back 4 Blood"
     lane_root="$HOME/.local/share/b4b-coop/prefixes/lane2"
     lane_lock=/tmp/b4b-game-lane2.lock lane_game_port=7887 lane_port_base=47140 lane_win="B4B L2" ;;
  *) echo "B4B_LANE must be 1 or 2" >&2; exit 2 ;;
esac
