@echo off
rem botkit chat API launcher (Windows). Starts ONLY the /chat HTTP service,
rem without connecting to WeCom. Use it to serve a bot to Dify / a portal.
rem
rem Usage:
rem   - Drag a bot folder (e.g. bots\finance-bot) onto this .bat, or
rem   - Run:  start-api.bat bots\finance-bot
rem   - Or edit the BOT_DIR line below to hard-code your bot folder.
rem
rem ASCII-only on purpose: cmd decodes .bat with the console code page,
rem so non-ASCII here could be mis-executed. Chinese output comes from Python.

setlocal
cd /d "%~dp0"

rem ---- Pick bot dir: command-line arg wins, else the hard-coded default ----
set "BOT_DIR=%~1"
if not defined BOT_DIR set "BOT_DIR=bots\DemandAgent"

if not exist "%BOT_DIR%\bot.yaml" (
    echo [x] Bot folder not found: "%BOT_DIR%"
    echo     Pass one like:  start-api.bat bots\finance-bot
    echo     or edit BOT_DIR inside this .bat.
    echo.
    pause
    exit /b 1
)

rem ---- Find Python ----
set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    py --version >nul 2>&1 && set "PY=py"
)
if not defined PY (
    echo [x] Python not found. Install Python 3.11+ from:
    echo     https://www.python.org/downloads/
    echo     Remember to tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

echo Starting chat API for "%BOT_DIR%" ...
%PY% -m botkit serve-api "%BOT_DIR%"
set "RC=%ERRORLEVEL%"

echo.
pause
exit /b %RC%
