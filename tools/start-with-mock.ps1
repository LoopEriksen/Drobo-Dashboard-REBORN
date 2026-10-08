<#
.SYNOPSIS
    Start Drobo Dashboard REBORN against a SIMULATED Drobo, and shut the
    simulator down again when the app exits.

.DESCRIPTION
    For trying the app out, and for exercising states a healthy Drobo will
    never show you -- a failed drive, a dead cache battery, a full array. The
    simulator answers every endpoint the real driver does, so the whole app
    works against it.

    THE LIFETIME TIE IS THE POINT. The agent this starts belongs to this app
    instance and nothing else, so when the app exits -- including via Exit on
    the tray icon -- the simulated agent is stopped too. Without that you get
    an orphaned Python process holding port 7420, which then quietly serves a
    SIMULATED Drobo to the next launch that expects a real one. That is a
    genuinely confusing failure and the reason this script exists rather than
    a note in the README telling you to remember.

    Written in PowerShell rather than batch for exactly that: batch cannot get
    a child's PID reliably, so it cannot kill the right process afterwards.
    Here the agent is a real process object, waited on and stopped by identity.

.NOTES
    Double-click START WITH SIMULATED DROBO (testing).bat instead of running
    this by hand. No administrator rights are needed.
#>
param(
    [int]$Port = 7420,
    # Empty on purpose. The agent mints a random token on its first run and
    # saves it; this script reads that value back once the agent is up. A fixed
    # default here would be a published credential -- see the note by $cfg.
    # Passing -Token still works and still wins: the value is written into the
    # simulator's config, so the agent, the app and the links below all agree.
    [string]$Token = ''
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

function Say($msg, $colour = 'Gray') { Write-Host "  $msg" -ForegroundColor $colour }

Write-Host ''
Write-Host '  Drobo Dashboard REBORN -- simulated Drobo' -ForegroundColor Cyan
Write-Host '  ------------------------------------------'
Write-Host ''

# --- python present? --------------------------------------------------------
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Say 'Python was not found. Install it from python.org and tick' 'Red'
    Say '"Add Python to PATH" during setup, then run this again.' 'Red'
    Read-Host "`n  Press Enter to close"
    exit 1
}

# --- is something already on the port? --------------------------------------
# Refuse rather than fight it. Two agents on one port is how you end up talking
# to the wrong one, and silently binding alongside an existing listener (which
# Windows will allow) makes which-one-answers a coin toss.
$inUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    Say "Something is already listening on port $Port." 'Yellow'
    Say 'That is probably an agent from an earlier run. Close it first,'
    Say 'or close any other Drobo Dashboard window and try again.'
    Read-Host "`n  Press Enter to close"
    exit 1
}

# --- find the app -----------------------------------------------------------
# Newest build wins, and the helper says which one it picked. This used to be
# a first-match-wins list that ran an eleven-day-old binary while the agent ran
# from source -- see tools\find-app-exe.ps1 for the whole story.
$app = & (Join-Path $PSScriptRoot 'find-app-exe.ps1') -Repo $repo

# --- start the simulated agent ---------------------------------------------
$cfg = Join-Path $repo 'agent\config.mock.json'
if (-not (Test-Path -LiteralPath $cfg)) {
    # Synthesize it. config.*.json is gitignored (local configs carry real
    # tokens), which had a consequence nobody noticed until a public-release
    # review: a fresh clone had NO mock config, so the one launcher every
    # hardware-less stranger tries died right here with "Missing ...". Writing
    # it on demand costs nothing and makes a clean checkout actually work.
    # The token stays EMPTY: config.py generates a random one on first load and
    # saves it back, exactly as it does for the real config.json. Nothing that
    # ships in a tracked file can serve as a credential.
    Say 'First run: creating agent\config.mock.json for the simulator...' 'DarkGray'
    @'
{
  "agent": {
    "bind": "127.0.0.1",
    "port": 7420,
    "token": "",
    "poll_seconds": 5
  },
  "drobo": {
    "driver": "mock",
    "simulate_config": true
  },
  "photos": {
    "enabled": false
  },
  "alerts": {
    "resend_seconds": 3600
  }
}
'@ | Set-Content -LiteralPath $cfg -Encoding ascii
}

# Settle which token this run uses, before the agent reads the file.
#
# Earlier versions of this script wrote a constant -- 'live-demo-token' -- both
# as the -Token default and into the config above, and this script is in a public
# repository, so that value was readable by anyone. That is
# not a demo-scoped secret: api.py hands the one token to the whole API, and
# the API is not read-only (POST /api/backup/start runs robocopy, with /MIR
# when the caller asks to mirror, over paths the caller chooses), so a page the
# user happened to have open could drive it. A credential that ships in the
# source is the same as no credential at all.
#
# The fix needs no new machinery: config.py already generates a random token
# and saves it back whenever the field is empty, exactly as it does for the
# real config.json -- the old constant was simply defeating that generator. So
# an empty field is left empty, and a machine still carrying the burned value
# is healed here, because config.mock.json is written once and never refreshed:
# without this, a PC that got the published token would keep it forever.
#
# Only the token field is rewritten, through ConvertFrom-Json/ConvertTo-Json
# rather than by replacing the file, so a driver or photos setting edited by
# hand survives. An unreadable or non-JSON config is left alone: the agent
# reports a bad config far better than a half-repair here would.
$retired = 'live-demo-token'
if ($Token -eq $retired) {
    Say 'That token is the retired demo value; using a fresh random one instead.' 'Yellow'
    $Token = ''
}
try {
    $current = Get-Content -LiteralPath $cfg -Raw | ConvertFrom-Json
    $wanted = $null
    if ($Token) {
        # -Token means "use this one", so put it where the agent will read it.
        if ($current.agent.token -cne $Token) { $wanted = $Token }
    }
    elseif ($current.agent.token -eq $retired) {
        Say 'Replacing the old published demo token with a fresh random one...' 'Yellow'
        $wanted = ''
    }
    if ($null -ne $wanted) {
        $current.agent.token = $wanted
        # utf8 (with BOM, on 5.1) rather than the ascii used for a fresh file:
        # config.py reads utf-8-sig, and re-encoding a file someone has edited
        # must not mangle a non-ASCII path in it.
        $current | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $cfg -Encoding utf8
    }
}
catch { }

Say 'Starting the simulated Drobo...' 'Green'
# Each path is quoted EXPLICITLY. Windows PowerShell 5.1 does not quote array
# arguments when handing them to a native executable, so this repository's own
# path -- "...\Drobo Dashboard REBORN\..." -- gets split at the first space and
# Python is asked to run a file called "...\GitHub\Drobo". Caught by testing
# this rather than by reading it.
$agentArgs = @("`"$(Join-Path $repo 'agent\run_agent.py')`"", '--config', "`"$cfg`"")
# Start python.exe itself, not the `py` launcher. Given a script, `py` reads
# its first line (#!/usr/bin/env python3), searches PATH for python3, and on
# a PC with Microsoft's Python install manager finds that product's Store
# alias first. Started hidden, as here, the alias cannot run, `py` exits with
# code 101, and this script said "The simulated agent did not start".
# Measured 2026-09-14 with PYLAUNCHER_DEBUG=1. `py -c` has
# no script line to follow, so it names the interpreter actually installed,
# and that is what runs the agent.
#
# It also makes $agent the agent itself. Before, $agent was the `py` wrapper,
# so stopping it in `finally` could leave the real agent holding the port:
# the orphan this whole script exists to prevent.
$python = $null
try { $python = (& py -c "import sys; print(sys.executable)" | Select-Object -First 1) } catch { }
if ($python) { $python = $python.Trim() }
if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    Say 'Python is installed, but it would not say where its interpreter is.' 'Red'
    Say 'Run  py -c "import sys; print(sys.executable)"  in a terminal to see why.' 'Red'
    Read-Host "`n  Press Enter to close"
    exit 1
}
$agent = Start-Process -FilePath $python -ArgumentList $agentArgs `
                       -WorkingDirectory $repo -PassThru -WindowStyle Hidden

# Give it a moment to bind, then confirm it actually did. Launching the app
# against an agent that failed to start produces "cannot reach the agent",
# which sends you looking in the wrong place entirely.
$ready = $false
foreach ($i in 1..20) {
    Start-Sleep -Milliseconds 400
    if ($agent.HasExited) { break }
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/ping" -TimeoutSec 2 -UseBasicParsing | Out-Null
        $ready = $true; break
    } catch { }
}

if (-not $ready) {
    Say 'The simulated agent did not start.' 'Red'
    if (-not $agent.HasExited) { Stop-Process -Id $agent.Id -Force -ErrorAction SilentlyContinue }
    Read-Host "`n  Press Enter to close"
    exit 1
}

Say "Simulated Drobo is up on http://127.0.0.1:$Port" 'Green'
Write-Host ''

# Read the token the agent generated, now that it has answered /api/ping. It
# has to be AFTER that: config.py writes the value during load(), so the file
# read a moment earlier would still say "". This script no longer knows the
# token in advance -- that is the point -- so the links below come from disk.
if (-not $Token) {
    try { $Token = (Get-Content -LiteralPath $cfg -Raw | ConvertFrom-Json).agent.token } catch { }
}

if ($Token) {
    Say "Browser dashboard:  http://127.0.0.1:$Port/?token=$Token" 'Cyan'
    Say "Agent token:        $Token" 'Cyan'
    Write-Host ''
    Say 'Break things on purpose while it runs, from another terminal:'
    Say "  curl -X POST -H `"X-Agent-Token: $Token`" http://127.0.0.1:$Port/api/mock/fail/3" 'DarkGray'
    Say "  curl -X POST -H `"X-Agent-Token: $Token`" http://127.0.0.1:$Port/api/mock/reset" 'DarkGray'
}
else {
    # Better than printing "?token=" and letting you wonder why it 401s.
    Say "Could not read the agent token from $cfg." 'Yellow'
    Say 'Open that file, copy "token", and browse to:' 'Yellow'
    Say "  http://127.0.0.1:$Port/?token=<token>" 'DarkGray'
}
Write-Host ''

try {
    if ($app) {
        Say 'Starting the app...' 'Green'
        # -Wait blocks until the app process really ends. Closing to the tray
        # does NOT end it, which is correct: the simulator should stay up for
        # as long as the app is still running, tray or not. Only Exit ends it.
        # --agent-config names the config this simulator was started with.
        # Without it the app takes its token from the first config it finds,
        # which in a checkout is the REAL agent's agent\config.json, and the
        # simulator refused every request it made. Found 2026-09-14.
        Start-Process -FilePath $app -ArgumentList @('--agent-config', "`"$cfg`"") -Wait
        Write-Host ''
        Say 'The app has closed.'
    }
    else {
        Say 'The Windows app is not built, so only the browser dashboard is'  'Yellow'
        Say 'available. To build it:'                                          'Yellow'
        Say '  dotnet build windows\DroboDashboardReborn -c Release'           'DarkGray'
        Write-Host ''
        Say 'Open the browser link above. Press Enter here when you are done.'
        Read-Host
    }
}
finally {
    # Runs whatever happened above, including Ctrl+C: the simulator must not
    # outlive the thing it was started for.
    if ($agent -and -not $agent.HasExited) {
        Say 'Stopping the simulated Drobo...'
        Stop-Process -Id $agent.Id -Force -ErrorAction SilentlyContinue
    }
    Say 'Done.' 'Green'
    Write-Host ''
}
