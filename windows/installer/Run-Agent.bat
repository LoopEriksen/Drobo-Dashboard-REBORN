@echo off
REM ---------------------------------------------------------------
REM  Drobo Dashboard REBORN -- runs the agent, in its own window.
REM
REM  The launcher starts this minimised and hands off to the app. It is
REM  its own file, rather than a one-liner inside the launcher, for one
REM  reason: if the agent stops, the window must STAY OPEN showing why.
REM  Before this existed the agent's window closed with it, and a tester
REM  whose Drobo was not found saw an app with no agent and no message.
REM
REM  A Drobo that cannot be found no longer stops the agent (2026-09-26):
REM  it keeps running, the app says it cannot reach the Drobo, and the
REM  agent keeps looking. Until then that was the usual reason this window
REM  was left open, and the footer below said so. What still stops it is
REM  something else -- for example a device that answers in the Drobo's
REM  place but has a different serial, or a config.json that cannot be
REM  read.
REM
REM  Python is the bundled runtime under .\python; `py` is only a
REM  fallback for a hand-copied payload. The agent's config.json (holding
REM  the access token it generates) lives in
REM  %LOCALAPPDATA%\DroboDashboardReborn so that uninstall can remove this
REM  folder completely and an upgrade can replace it.
REM ---------------------------------------------------------------
setlocal
cd /d "%~dp0"
title Drobo Dashboard REBORN - Agent

set "CONFIG=%LOCALAPPDATA%\DroboDashboardReborn\config.json"
set "PY=%~dp0python\python.exe"
if not exist "%PY%" set "PY=py"

"%PY%" "agent\run_agent.py" --config "%CONFIG%"

if errorlevel 1 (
    echo.
    echo   ------------------------------------------------------------
    echo   The agent stopped. The message above says why.
    echo.
    echo   A Drobo that is switched off, or not on this network, does
    echo   not stop it: the app says so and the agent keeps looking.
    echo   So this is something else -- for example a device that
    echo   answered in your Drobo's place but is not your Drobo, or a
    echo   settings file that can no longer be read. Fix what the
    echo   message describes, then start Drobo Dashboard REBORN again.
    echo.
    echo   If it keeps happening, copy this window's text into an issue.
    echo   Read it through first: it can include your Drobo's serial.
    echo   ------------------------------------------------------------
    echo.
    pause
)
