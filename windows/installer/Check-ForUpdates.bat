@echo off
REM ===================================================================
REM  Drobo Dashboard REBORN -- check for a newer version.
REM
REM  On demand only, never automatic. An update check that nags while
REM  you are on a train with no signal is worse than no update check.
REM
REM  This calls the SAME code the app itself uses
REM  (agent\drobo_agent\updates.py) against the SAME settings block in
REM  the agent's config.json. Until 2026-07-28 there was a second checker
REM  here -- its own PowerShell script, its own config file, its own idea
REM  of what a feed looks like. Two mechanisms that both had to be
REM  switched on separately is how an updater quietly stops working: you
REM  enable one and assume you are covered. There is now one.
REM
REM  Update checks ship OFF. To turn them on, set "enabled": true in the
REM  "updates" block of the agent's config.json -- that file is
REM  %LOCALAPPDATA%\DroboDashboardReborn\config.json in an installed copy
REM  and agent\config.json in the source tree -- and point "manifest_url"
REM  at either a GitHub Releases API URL or your own JSON manifest.
REM
REM  Runs on the bundled Python under .\python; falls back to `py` for a
REM  hand-copied payload that has no python\ folder.
REM
REM  It runs agent\check_updates.py BY PATH, never `python -m` from inside
REM  the agent folder. The bundled runtime's python314._pth keeps the
REM  working directory off sys.path, so `-m drobo_agent.updates` failed in
REM  every installed copy with ModuleNotFoundError from 2026-09-02 until
REM  2026-09-26 -- and because this window closed at once, nobody saw it.
REM  check_updates.py puts its own folder on sys.path, as run_agent.py does.
REM
REM  The agent's code is in .\agent in an installed copy, and its settings
REM  are in %LOCALAPPDATA%\DroboDashboardReborn\config.json. Double-clicked
REM  inside the source tree, where this file sits in windows\installer\, it
REM  uses the repository's own agent folder two levels up, and the
REM  agent\config.json beside it -- the settings file a source tree uses.
REM
REM  The window always waits for a key before closing: whatever the answer,
REM  a shortcut that flashes a console and vanishes has told nobody
REM  anything. The exit status is the check's own (0 up to date or switched
REM  off, 1 an update is available, 2 could not tell, 3 the check could not
REM  run), so a script can still branch on it.
REM ===================================================================
setlocal
set "CONFIG=%LOCALAPPDATA%\DroboDashboardReborn\config.json"
set "PY=%~dp0python\python.exe"
if not exist "%PY%" set "PY=py"
set "AGENT=%~dp0agent"
REM  The next two lines test the SAME thing, so the code and the settings can
REM  never come from different installs: no .\agent means this is the source
REM  tree, where the agent's code and its config.json both live in agent\.
if not exist "%AGENT%\check_updates.py" set "AGENT=%~dp0..\..\agent"
if not exist "%~dp0agent\check_updates.py" set "CONFIG=%~dp0..\..\agent\config.json"

"%PY%" "%AGENT%\check_updates.py" --config "%CONFIG%"
set "RC=%ERRORLEVEL%"

REM  0, 1 and 2 are answers, and the check has already printed them.
if "%RC%"=="0" goto done
if "%RC%"=="1" goto done
if "%RC%"=="2" goto done
echo.
if "%RC%"=="9009" goto nopython
echo   The update check could not run: exit status %RC%. The lines above
echo   say why. Drobo Dashboard REBORN itself is unaffected; if this keeps
echo   happening, copy this window's text into an issue.
goto done

:nopython
echo   Could not run Python, so the update check could not be performed.
echo   Drobo Dashboard REBORN itself is unaffected.

:done
echo.
pause
exit /b %RC%
