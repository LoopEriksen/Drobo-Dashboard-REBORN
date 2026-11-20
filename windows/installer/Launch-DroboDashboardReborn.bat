@echo off
REM ---------------------------------------------------------------
REM  Drobo Dashboard REBORN -- installed launcher.
REM
REM  Starts the Python agent, unless one is already running, in its own
REM  minimised window (Run-Agent.bat, which stays open with the reason if
REM  the agent stops), then opens the native Windows app. This is the
REM  installed-app equivalent of the repo's "START DROBO DASHBOARD.bat"
REM  -- same idea, adjusted for the install-folder layout (agent lives
REM  in .\agent alongside this file).
REM
REM  Python: the installer bundles its own runtime under .\python (the
REM  embeddable distribution from python.org), so nothing needs to be on
REM  the machine. The `py` launcher is only a fallback for a hand-copied
REM  payload that has no python\ folder.
REM
REM  It asks the agent before it starts one (since 2026-11-20). Until
REM  then it started an agent every time and waited a fixed 2 seconds.
REM  The agent's window stays open after the app closes, so every
REM  launch after the first added another agent, and 2 seconds is less
REM  than the agent's own start-up: it looks for the Drobo on the
REM  network before it answers, and that search alone listens for 3
REM  seconds. So the app could open to "Is the agent running?", or ask
REM  for an access token the agent had not yet written. Now:
REM    1. ask http://127.0.0.1:PORT/api/ping once; if this agent
REM       answers, open the app and start nothing else;
REM    2. otherwise start the agent and keep asking for up to
REM       AGENT_WAIT_SECONDS before opening the app;
REM    3. if it never answered, open the app anyway and say so, since
REM       the agent's own window then says why.
REM  PORT is agent.port from the agent's config.json, 7420 if that file
REM  or that setting is not there yet. The asking is done by curl.exe,
REM  which every Windows 10 since version 1803 and every Windows 11
REM  has in System32.
REM ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist "%~dp0python\python.exe" (
    where py >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   The bundled Python runtime is missing from this folder and
        echo   Python was not found on this machine either.
        echo.
        echo   Reinstall Drobo Dashboard REBORN, or install Python from
        echo   https://www.python.org/downloads/ and tick "Add Python to
        echo   PATH" during setup, then run this again.
        echo.
        pause
        exit /b 1
    )
)

REM Where the agent's settings live (the same path Run-Agent.bat gives
REM it), and the port in them. The bundled Python reads the file the way
REM the agent does (utf-8-sig, so a BOM left by an editor is harmless).
REM A missing file, a missing setting or a value that is not a number
REM prints nothing, and 7420, the agent's default, stays. `call` is there
REM because for /f hands its command to cmd /c, which would strip the
REM first and last quote of a command line that begins with one.
set "CONFIG=%LOCALAPPDATA%\DroboDashboardReborn\config.json"
set "PY=%~dp0python\python.exe"
if not exist "%PY%" set "PY=py"
set "PORT=7420"
if exist "%CONFIG%" for /f "usebackq delims=" %%P in (`call "%PY%" -I -c "import json, sys; print(int(json.load(open(sys.argv[1], encoding='utf-8-sig'))['agent']['port']))" "%CONFIG%" 2^>nul`) do set "PORT=%%P"

REM About 20 seconds: the agent's start-up is a few seconds of looking
REM for the Drobo, longer on a PC whose network is still coming up.
set "AGENT_WAIT_SECONDS=20"
set "CURL=%SystemRoot%\System32\curl.exe"
if not exist "%CURL%" goto no_curl

call :ask_agent 0
if not defined AGENT_UP goto start_agent_and_wait
echo   Drobo Dashboard REBORN's agent is already running; opening the app.
goto start_app

:start_agent_and_wait
echo   Starting Drobo Dashboard REBORN's agent...
call :start_agent
call :ask_agent %AGENT_WAIT_SECONDS%
if defined AGENT_UP goto start_app
echo.
echo   The agent has not answered after %AGENT_WAIT_SECONDS% seconds. The app opens
echo   anyway; if it cannot reach the agent, the window named
echo   "Drobo Dashboard REBORN - Agent" on the taskbar says why.
timeout /t 10 >nul
goto start_app

:no_curl
REM No curl.exe: a Windows 10 older than version 1803. Start the agent
REM and give it a few seconds, as this file did before. A second agent
REM started this way closes by itself (run_agent.py checks the port
REM before anything else), so this cannot pile agents up either.
call :start_agent
timeout /t 5 /nobreak >nul

:start_app
start "" "%~dp0DroboDashboardReborn.exe"
exit /b 0

REM ---------------------------------------------------------------
REM :start_agent -- the agent in its own window.
REM The agent gets its own window so this launcher can hand off to the
REM app and exit -- closing the app shouldn't require also babysitting a
REM console. That window is titled so it can be found on the taskbar,
REM and Run-Agent.bat keeps it open if the agent stops.
REM ---------------------------------------------------------------
:start_agent
start "Drobo Dashboard REBORN - Agent" /min "%~dp0Run-Agent.bat"
exit /b 0

REM ---------------------------------------------------------------
REM :ask_agent RETRIES -- sets AGENT_UP=1 if this agent answers
REM /api/ping on PORT. RETRIES 0 asks once; more asks again once a
REM second while nothing is listening yet, for up to AGENT_WAIT_SECONDS.
REM Only an answer naming "drobo-agent" counts, so some other program
REM on the port is not mistaken for the agent.
REM   --noproxy "*"        a question to this PC never goes to a proxy
REM   --max-time           one question may wait AGENT_WAIT_SECONDS: an
REM                        agent holds its port from the moment it
REM                        starts but answers only once it has looked
REM                        for the Drobo, so a question that arrives
REM                        mid-start waits for that answer instead of
REM                        giving up and leaving the agent to answer a
REM                        connection nobody is reading any more
REM   --retry-connrefused  "nothing listening yet" is worth asking again
REM   --retry-max-time     the limit on asking again: AGENT_WAIT_SECONDS
REM An agent that is already running answers in a moment, and a port
REM nothing listens on refuses at once, so asking once is quick either
REM way.
REM ---------------------------------------------------------------
:ask_agent
set "AGENT_UP="
"%CURL%" --silent --noproxy "*" --connect-timeout 3 --max-time %AGENT_WAIT_SECONDS% --retry %~1 --retry-delay 1 --retry-connrefused --retry-max-time %AGENT_WAIT_SECONDS% "http://127.0.0.1:%PORT%/api/ping" 2>nul | "%SystemRoot%\System32\findstr.exe" /c:"drobo-agent" >nul && set "AGENT_UP=1"
exit /b 0
