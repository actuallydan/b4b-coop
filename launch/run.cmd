@echo off
rem Launch B4B on Windows directly (no EAC bootstrapper) so the agent loads. Dev fallback for the root xinput1_3.dll.
rem Steam must be running. Set B4B_DIR if the game is not in the default library. Extra args go to the game.
setlocal
if not defined B4B_DIR set "B4B_DIR=C:\Program Files (x86)\Steam\steamapps\common\Back 4 Blood"
cd /d "%B4B_DIR%\Gobi\Binaries\Win64" || exit /b 1
rem Stops steam_api from relaunching the game through the Steam client (and thus EAC).
echo 924970> steam_appid.txt
set SteamAppId=924970
set SteamGameId=924970
start "" Back4Blood.exe -log %*
