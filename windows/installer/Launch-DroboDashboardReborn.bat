@echo off
REM ---------------------------------------------------------------
REM  Drobo Dashboard REBORN -- installed launcher.
REM
REM  Starts the Python agent in its own minimised window (Run-Agent.bat,
REM  which stays open with the reason if the agent stops), then opens
REM  the native Windows app. This is the installed-app equivalent of the
REM  repo's "START DROBO DASHBOARD.bat" -- same idea, adjusted for the
REM  install-folder layout (agent lives in .\agent alongside this file).
REM
REM  Python: the installer bundles its own runtime under .\python (the
REM  embeddable distribution from python.org), so nothing needs to be on
REM  the machine. The `py` launcher is only a fallback for a hand-copied
REM  payload that has no python\ folder.
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

REM The agent gets its own window so this launcher can hand off to the
REM app and exit -- closing the app shouldn't require also babysitting a
REM console. That window is titled so it can be found on the taskbar,
REM and Run-Agent.bat keeps it open if the agent stops.
start "Drobo Dashboard REBORN - Agent" /min "%~dp0Run-Agent.bat"

REM Give the agent a moment to bind its port before the app's first poll.
timeout /t 2 /nobreak >nul

start "" "%~dp0DroboDashboardReborn.exe"
