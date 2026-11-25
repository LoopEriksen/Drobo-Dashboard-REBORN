@echo off
REM ===================================================================
REM  Drobo Dashboard REBORN
REM
REM  Double-click this. Nothing else in the repo needs finding.
REM
REM  It starts the agent (the piece that talks to your Drobo) in its own
REM  minimised window, waits until the agent answers, and then opens the
REM  native Windows app if one has been built. Otherwise the browser
REM  dashboard shows the same information and works on your phone as
REM  well; its address is printed here.
REM
REM  The agent runs for as long as the window named
REM  "Drobo Dashboard REBORN - Agent (from source)" is open. Closing that
REM  window stops it, and the dashboard stops updating. This window can
REM  be closed once the app is open.
REM
REM  It starts the agent FIRST and waits for it (since 2026-11-25), the
REM  way the installed launcher (windows\installer\
REM  Launch-DroboDashboardReborn.bat) has done since 2026-11-20. Until
REM  then this file opened the app and only then ran the agent in this
REM  window. The app reads its access token once, as it starts, from
REM  agent\config.json; on a fresh clone that file did not exist yet,
REM  because the agent writes it with a new token when it first starts.
REM  So the app started with no token, the agent then made one, and
REM  every request the app sent was refused with "Token rejected" until
REM  the app was restarted. Now:
REM    1. ask http://127.0.0.1:PORT/api/ping once; if an agent answers,
REM       start no second one (it would close at once anyway: run_agent.py
REM       exits with status 3 when an agent already has its port);
REM    2. otherwise start the agent and keep asking for up to
REM       AGENT_WAIT_SECONDS before opening the app;
REM    3. if it never answered, open the app anyway and say so, since
REM       the agent's own window then says why.
REM  PORT is agent.port from agent\config.json, 7420 if that file or
REM  that setting is not there yet. The asking is done by curl.exe, which
REM  every Windows 10 since version 1803 and every Windows 11 has in
REM  System32.
REM
REM  The agent's window is this same file, started again with
REM  --agent-window (see :agent_window at the bottom), so that it stays
REM  open showing why if the agent stops -- what Run-Agent.bat does for
REM  the installed copy -- without a second file to keep in step.
REM ===================================================================

setlocal
cd /d "%~dp0"
REM This file's own path, kept before any `call :label`: inside one, the
REM script's own parameter names the label rather than the file.
REM :start_agent runs this file again from it.
set "SELF=%~f0"
set "AGENT_TITLE=Drobo Dashboard REBORN - Agent (from source)"
if "%~1"=="--agent-window" goto agent_window

title Drobo Dashboard REBORN

echo.
echo   Drobo Dashboard REBORN
echo   ----------------------
echo.

REM --- Python is the only hard requirement -------------------------
where py >nul 2>&1
if errorlevel 1 (
    echo   Python was not found.
    echo.
    echo   Install it from https://www.python.org/downloads/ and tick
    echo   "Add Python to PATH" during setup, then run this again.
    echo.
    pause
    exit /b 1
)

REM --- The native app is optional. If none has been built we just use the
REM     browser dashboard, which shows the same information.
REM
REM     Which build to run is decided by tools\find-app-exe.ps1, shared with
REM     the simulator launcher. It picks the NEWEST executable wherever it
REM     landed, rather than the first entry in a hand-kept list.
REM
REM     That list used to live here, and it had already drifted from the other
REM     launcher's copy: this one never looked in ...\net8.0-windows\win-x64\,
REM     so a build made with -r win-x64 was invisible to it while being the
REM     preferred path for the other. Two lists, two different answers, no way
REM     to tell which one you got. One shared rule instead.
set "NATIVE="
for /f "delims=" %%F in ('powershell -NoProfile -ExecutionPolicy Bypass -File "tools\find-app-exe.ps1" -Quiet 2^>nul') do (
    if not defined NATIVE set "NATIVE=%%F"
)

REM --- The agent's settings file, and the port in it. Python reads the
REM     file the way the agent does (utf-8-sig, so a BOM left by an editor
REM     is harmless). A missing file, a missing setting or a value that is
REM     not a number prints nothing, and 7420, the agent's default, stays.
REM     `call` is there because for /f hands its command to cmd /c, which
REM     would strip the first and last quote of a command line that
REM     begins with one. Taken from the installed launcher.
set "CONFIG=%~dp0agent\config.json"
set "PORT=7420"
if exist "%CONFIG%" for /f "usebackq delims=" %%P in (`call py -I -c "import json, sys; print(int(json.load(open(sys.argv[1], encoding='utf-8-sig'))['agent']['port']))" "%CONFIG%" 2^>nul`) do set "PORT=%%P"

REM About 20 seconds: the agent's start-up is a few seconds of looking
REM for the Drobo, longer on a PC whose network is still coming up.
set "AGENT_WAIT_SECONDS=20"
set "CURL=%SystemRoot%\System32\curl.exe"
REM Set when something above the app's start needs reading, so this
REM window waits for a key instead of closing by itself.
set "KEEP_OPEN="
if not exist "%CURL%" goto no_curl

call :ask_agent 0
if not defined AGENT_UP goto start_agent_and_wait
call :say_agent_already_running
goto start_app

:start_agent_and_wait
echo   Starting the agent in its own window...
call :start_agent
call :ask_agent %AGENT_WAIT_SECONDS%
if defined AGENT_UP goto start_app
set "KEEP_OPEN=1"
echo.
echo   The agent has not answered after %AGENT_WAIT_SECONDS% seconds. The app opens
echo   anyway; if it cannot reach the agent, the window named
echo   "%AGENT_TITLE%" on the taskbar says why.
echo.
goto start_app

:no_curl
REM No curl.exe: a Windows 10 older than version 1803. Start the agent
REM and give it a few seconds, as the installed launcher does. A second
REM agent started this way closes by itself (run_agent.py checks the
REM port before anything else), so this cannot pile agents up either.
echo   Starting the agent in its own window...
call :start_agent
timeout /t 5 /nobreak >nul

:start_app
REM --- --agent-config tells the app which agent it is talking to: the one
REM     started from this folder, whose settings file is agent\config.json.
REM     The app then takes its access token from that file and no other.
REM
REM     Without it the app took the first token it could find, and on a PC
REM     where the installer has also been used that is the INSTALLED copy's,
REM     in %LOCALAPPDATA%\DroboDashboardReborn\config.json. The agent started
REM     here has a different token, so it refused every request the app made.
REM     The simulator launcher (tools\start-with-mock.ps1) names its config
REM     the same way, for the same reason.
REM
REM     Deliberately no browser. It used to be launched automatically
REM     (--open), which was wrong for a reason worth writing down: the web
REM     dashboard always shows whichever Drobo the AGENT is monitoring, and
REM     it opened before you had chosen anything. On a network with more
REM     than one Drobo that means a window appears showing a device you did
REM     not pick -- and it looks authoritative. The Windows app's Tools tab
REM     has an "Open web dashboard" button, which is the right place for it.
if defined NATIVE (
    echo   Starting the Windows app...
    start "" "%NATIVE%" --agent-config "%~dp0agent\config.json"
) else (
    call :say_no_native_app
)

echo.
echo   The agent runs in the window named "%AGENT_TITLE%"
echo   on the taskbar. Close that window when you're done: that stops it.
echo.
if defined KEEP_OPEN (
    pause
    exit /b 0
)
echo   This window closes by itself.
timeout /t 15 >nul
exit /b 0

REM ---------------------------------------------------------------
REM :say_agent_already_running -- an agent answered before this file
REM started one. It may be the one this folder started earlier, which
REM is fine. But the app is given THIS folder's agent\config.json, so if
REM the agent answering is an installed copy's or the simulated Drobo's,
REM its token is a different one and the app will say "Token rejected".
REM This file cannot tell which agent it is without a token, so it says
REM both, and waits for a key so the line can be read. It also says to
REM restart the PC if one still answers: a simulated Drobo whose
REM launcher window was closed is a python.exe with no window and no
REM app left to stop it, so "exit the app" cannot end it.
REM ---------------------------------------------------------------
:say_agent_already_running
set "KEEP_OPEN=1"
echo   An agent already answers on port %PORT%, so no second one is started.
echo   The app uses the token in this folder's agent\config.json. If that
echo   agent is an installed copy's or the simulated Drobo's, the app will
echo   say its token was rejected: close the window named
echo   "Drobo Dashboard REBORN - Agent" (an installed copy), or exit the app
echo   from its tray icon (the simulated Drobo), then run this again.
echo   If an agent still answers after that, restart the PC (a simulated
echo   Drobo whose launcher window was closed keeps running unseen), then
echo   run this again.
echo.
exit /b 0

REM ---------------------------------------------------------------
REM :say_no_native_app -- no built app, so the browser dashboard's
REM address is printed here: the agent prints it too, but its window
REM is minimised. The token is never printed (see run_agent.py's
REM banner for why); the page asks for it once.
REM ---------------------------------------------------------------
:say_no_native_app
set "KEEP_OPEN=1"
echo   Windows app not built yet.
echo   ^(To build it: dotnet build windows\DroboDashboardReborn -c Release^)
echo.
echo   Until then the browser dashboard shows the same information:
echo   open http://127.0.0.1:%PORT%/ on this PC. The page asks once for
echo   the access token, which is "token" in agent\config.json.
exit /b 0

REM ---------------------------------------------------------------
REM :start_agent -- the agent in its own minimised window, titled so a
REM tester can find it on the taskbar and tell it from an installed
REM copy's "Drobo Dashboard REBORN - Agent". The window is this file
REM again, run with --agent-window. cmd /c, with the whole command in
REM one more pair of quotes, keeps a folder path with spaces or
REM brackets in one piece: cmd /c strips that outer pair and runs the
REM rest as written.
REM ---------------------------------------------------------------
:start_agent
start "%AGENT_TITLE%" /min cmd /c ""%SELF%" --agent-window"
exit /b 0

REM ---------------------------------------------------------------
REM :ask_agent RETRIES -- sets AGENT_UP=1 if an agent answers
REM /api/ping on PORT. RETRIES 0 asks once; more asks again once a
REM second while nothing is listening yet, for up to AGENT_WAIT_SECONDS.
REM Only an answer naming "drobo-agent" counts, so some other program
REM on the port is not mistaken for the agent. Taken from the installed
REM launcher, which explains each curl option.
REM ---------------------------------------------------------------
:ask_agent
set "AGENT_UP="
"%CURL%" --silent --noproxy "*" --connect-timeout 3 --max-time %AGENT_WAIT_SECONDS% --retry %~1 --retry-delay 1 --retry-connrefused --retry-max-time %AGENT_WAIT_SECONDS% "http://127.0.0.1:%PORT%/api/ping" 2>nul | "%SystemRoot%\System32\findstr.exe" /c:"drobo-agent" >nul && set "AGENT_UP=1"
exit /b 0

REM ---------------------------------------------------------------
REM :agent_window -- what runs in the agent's own window.
REM If the agent stops, the window stays open showing why, rather than
REM vanishing with the reason. Exit status 3 means another agent already
REM answers on this agent's port, so this one closed (run_agent.py, since
REM 2026-11-20): not a failure, so the window says so briefly and closes.
REM "if errorlevel N" is true for N and anything above it, so exactly 3
REM is tested as "3 or more, and not 4 or more".
REM ---------------------------------------------------------------
:agent_window
title %AGENT_TITLE%
echo.
echo   Drobo Dashboard REBORN - agent
echo   ------------------------------
echo   This window is the agent. Leave it open while you use the app;
echo   closing it stops the agent.
echo.
py "agent\run_agent.py"
if errorlevel 3 if not errorlevel 4 goto agent_already_running
echo.
echo   The agent has stopped. If it stopped by itself, the message above
echo   says why. Read it through before copying it into a report: it can
echo   show your Drobo's address, its serial and your Windows user name.
echo.
pause
exit /b

:agent_already_running
echo.
echo   An agent is already running, so this second one has closed.
echo   Nothing is wrong. This window closes by itself.
timeout /t 10 >nul
exit /b 0
