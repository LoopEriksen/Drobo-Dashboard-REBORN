@echo off
REM ===================================================================
REM  Drobo Dashboard REBORN -- capture what the ORIGINAL Dashboard sends
REM
REM  DOUBLE-CLICK THIS. It elevates itself, so there is no need to find
REM  an administrator terminal, and no need to be in the right folder.
REM
REM  Why this exists: the earlier instructions were "open an admin
REM  PowerShell, cd to the repo, run the script". That fails in two ways
REM  at once -- an elevated PowerShell opens in C:\Windows\System32 so a
REM  relative path is not found, and the default execution policy on a
REM  Windows desktop is Restricted so the script is refused anyway. Both
REM  are handled below.
REM
REM  It records up to seven minutes of traffic (press Enter to stop
REM  sooner) while you click through Drobo Dashboard 3.5.0, then decodes
REM  it and prints every command the original software sent, with the
REM  parameters we have never been able to recover any other way.
REM ===================================================================

setlocal
set "SCRIPT=%~dp0tools\capture-session.ps1"

REM --- already elevated? then just run it ---------------------------
net session >nul 2>&1
if %errorlevel% equ 0 goto :run

REM --- not elevated: relaunch this .bat as administrator ------------
echo.
echo   Asking Windows for administrator rights...
echo   (pktmon cannot capture without them -- approve the prompt.)
echo.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:run
if not exist "%SCRIPT%" (
    echo.
    echo   Could not find:
    echo     %SCRIPT%
    echo.
    echo   This .bat must stay in the repository folder, next to tools\.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"

echo.
echo   ===============================================================
echo    Save everything above (copy it into a text file), together
echo    with your notes:
echo      - first recording or second
echo      - which steps you managed and which you skipped
echo      - what the lights did on Blink Lights (including "nothing")
echo      - roughly what the event log page showed on screen
echo      - the exact words of the permission you gave the throwaway user
echo   ===============================================================
echo.
pause
