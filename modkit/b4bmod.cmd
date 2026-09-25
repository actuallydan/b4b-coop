@echo off
rem b4bmod for Windows: runs b4bmod.py with the Python launcher (py), else python from PATH.
where py >nul 2>nul || goto nopy
py -3 "%~dp0b4bmod.py" %*
exit /b %errorlevel%
:nopy
python "%~dp0b4bmod.py" %*
exit /b %errorlevel%
