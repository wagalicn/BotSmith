@echo off
rem botkit config UI launcher (Windows). Double-click to run.
rem
rem This file is intentionally ASCII-only. cmd reads .bat using the console
rem code page, so any non-ASCII text here can be mis-decoded and executed as
rem a command. All user-facing Chinese text is printed by Python instead,
rem which handles UTF-8 correctly.

setlocal
cd /d "%~dp0"

rem ---- Find Python ----
set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    py --version >nul 2>&1 && set "PY=py"
)
if not defined PY (
    echo [x] Python not found. Please install Python 3.11+ from:
    echo     https://www.python.org/downloads/
    echo     Remember to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

rem ---- Everything else (checks, hints, launch) is driven by Python,
rem      so all Chinese output goes through UTF-8 cleanly.
rem      Any args (e.g. --port 8888) are passed straight through. ----
%PY% start_ui.py %*
set "RC=%ERRORLEVEL%"

rem Keep the window open so the user can read any message.
echo.
pause
exit /b %RC%
