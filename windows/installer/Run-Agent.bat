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
REM
REM  Exit status 3 means another agent already answers on this agent's
REM  port, so this one closed (run_agent.py, since 2026-11-20). That is
REM  not a failure: the launcher normally sees the running agent and
REM  starts no second one, and this covers a second start it could not
REM  see coming -- two launches at the same moment, or this file run by
REM  hand. The window says so briefly and closes, instead of showing
REM  "The agent stopped" over an agent that is in fact running.
REM ---------------------------------------------------------------
setlocal
cd /d "%~dp0"
title Drobo Dashboard REBORN - Agent

set "CONFIG=%LOCALAPPDATA%\DroboDashboardReborn\config.json"
set "PY=%~dp0python\python.exe"
if not exist "%PY%" set "PY=py"

"%PY%" "agent\run_agent.py" --config "%CONFIG%"

REM  "if errorlevel N" is true for N and anything above it, so exactly 3
REM  is tested as "3 or more, and not 4 or more", before the general case.
if errorlevel 3 if not errorlevel 4 goto already_running

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
exit /b

:already_running
echo.
echo   Drobo Dashboard REBORN's agent is already running, so this second
echo   one has closed. Nothing is wrong. This window closes by itself.
timeout /t 10 >nul
REM  "exit", not "exit /b": the launcher's `start` runs this file under
REM  cmd /K, and "exit /b" would end only the script, leaving this
REM  window open at a prompt with the agent's title still on it -- a
REM  second "Drobo Dashboard REBORN - Agent" on the taskbar, which is the
REM  window the launcher tells a tester to look at. Run by hand from an
REM  open Command Prompt, this closes that prompt too, as it says.
exit 0
