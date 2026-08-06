@echo off
rem Double-click to start PyWR Reader and open it in your browser.
rem Closing this window - or pressing Ctrl+C - stops the app.
rem On the very first run it sets up a private .venv, which needs internet once.

cd /d "%~dp0"

rem "py" is the launcher the python.org installer adds; fall back to "python"
set PY=py
where %PY% >nul 2>nul || set PY=python
where %PY% >nul 2>nul || goto nopython

%PY% app.py --open
if errorlevel 1 (
  echo.
  echo PyWR Reader stopped with an error.
  pause
)
exit /b %errorlevel%

:nopython
echo Python 3 is not installed, or not on your PATH.
echo.
echo Install it from https://www.python.org/downloads/windows/ and tick
echo "Add Python to PATH" on the first screen of the installer.
pause
exit /b 1
