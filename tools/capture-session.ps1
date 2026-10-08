<#
.SYNOPSIS
    Record ONE session with the original Drobo Dashboard that unblocks
    several stuck features at once.

.DESCRIPTION
    Almost everything left on Phase 1's list is stuck for the same reason: the
    firmware gave us all 103 command names and ids, but never what goes inside
    their <Params>. This project refuses to guess -- a guessed parameter is an
    unreviewed instruction to somebody's storage -- so those commands go out
    empty and the device either ignores them or answers empty back.

    The only thing that settles it is watching the original Dashboard do the
    same job and reading the bytes. capture-identify.ps1 does that for one
    command. This does it for the whole list, because the expensive part is not
    the recording -- it is you sitting down with the old software and the Drobo
    on the same network, and that should happen once rather than six times.

    Each step below is a feature currently blocked. Work through as many as you
    can; skipping some is fine and the rest still pay off. When it finishes it
    runs tools/extract_commands.py, which prints every command that went past
    with its parameters.

    STEP LIST REVISED 2026-08-16. Settled since the last revision and REMOVED:
    spin-down (no on-demand action; enable/disable semantics verified on the
    live device) and cycling every permission level (the SDK writes codes 0
    and 1, but only 1 is confirmed, as read/write; 0 is most likely no
    access and NOT confirmed, and code 2 is open. This line called 0
    confirmed until 2026-09-26). Creating/deleting a share moved to the TOP: the SDK's
    create_share is blocked on it and both prior attempts recorded nothing.
    Keep doing this: when a step's answer lands in the protocol map, delete
    the step.

    STEP LIST REVISED 2026-08-25. Create/delete share: DONE -- captured
    2026-08-25 and both write formats are now in docs/protocol-map.md, so
    the step is deleted per this file's own rule. The remaining asks are
    down to TWO, and one short recording covers both: Blink Lights (thought
    then never seen on the wire -- wrong, corrected 2026-09-26: the
    2026-07-25 capture holds the ON press, IdentifyInterval 900; only the
    OFF press is unrecorded) and opening the device's event log view (the
    eCmdGetUserEventLog request is captured; its REPLY format is not).
    Read-only (code 2), Time Machine, and add/remove-a-user come off the
    ask list too -- still unobserved, but nothing being built is blocked on
    them; re-add whichever one a feature actually needs.

    STEP LIST REVISED 2026-09-21. A feature now needs one: the maintainer decided
    that creating and deleting Drobo user accounts is WANTED, and it cannot
    be built until the original Dashboard has been recorded doing both. So
    two steps come back. Step 4 creates a throwaway user; step 5 gives that
    user a permission other than read/write on the throwaway share
    ZZDELETEME, which doubles as the permission-code measurement
    (tools/access_codes.py before/after pins the word Dashboard shows to the
    number the device stores). DELETING the user is step 6 and is a SECOND,
    short recording, made only after `py tools/access_codes.py after` has
    run: delete it inside the first recording and the before/after
    comparison never sees the user at all. docs/hardware-session.md is the
    whole session in order, including the parts before and after this script.

.NOTES
    MUST BE RUN AS ADMINISTRATOR -- pktmon needs it.

        Right-click Windows Terminal or PowerShell -> "Run as administrator"
        cd "C:\Users\<you>\...\Drobo Dashboard REBORN"
        powershell -ExecutionPolicy Bypass -File tools\capture-session.ps1

    BEFORE YOU START:
      - this PC must be on the same network as the Drobo
      - Drobo Dashboard 3.5.0 must be CLOSED, so the capture catches it
        connecting from scratch rather than joining a conversation in progress
      - this project's own app and its agent window must be closed too, so
        their traffic stays out of the recording
      - for steps 4 and 5 (first recording): the throwaway share ZZDELETEME
        must already exist -- made from this project's app, Shares tab,
        "New share..." -- and `py tools\access_codes.py before` must already
        have been run. docs/hardware-session.md walks through it in order.
      - have paper next to you. Note what the lights physically do on step 2,
        what the event log page shows on step 3, and the exact words of the
        permission you choose on step 5 -- the packets cannot tell us what
        you saw on screen

    SAFETY: every step here is something the ORIGINAL Dashboard does in normal
    use. Nothing asks you to format, repair, or flash anything. If a screen
    offers one of those, do not click it -- and if you are not sure, stop.

    The capture is written to your TEMP directory, never into the repo.
    On an unencrypted protocol it contains whatever is typed while recording,
    including passwords -- see docs/captures/README.md. Do not commit it.
#>
param(
    [string]$DroboIP = "",
    # Seven minutes -- roomy for the two-step list of 2026-08-25, and still
    # enough for the five-step first recording of 2026-09-21 as long as
    # nothing has to be hunted for mid-recording (docs/hardware-session.md
    # suggests finding Dashboard's user settings beforehand). Left alone on
    # purpose: recording longer costs nothing but disk; running out costs
    # another session. Enter stops it early.
    [int]$Seconds = 420
)

$ErrorActionPreference = 'Stop'

function Fail($msg) { Write-Host ""; Write-Host "  $msg" -ForegroundColor Red; exit 1 }

# One indented, coloured line. Everything this script prints is indented two
# spaces, so the indent lives here rather than in every caller.
function Say($msg, $color = 'Gray') { Write-Host "  $msg" -ForegroundColor $color }

<#
Find a working Python.

Not just "call py and hope". On some machines `py` resolves through a
WindowsApps app-execution alias that lives on the USER path only, and this
script runs ELEVATED -- a context where those aliases are not always
resolvable. If that failed we would record the capture perfectly and then die
at the decode step, which reads like the whole session was wasted when in fact
the valuable part is safely on disk.

So: try the launcher, then real interpreters at their known locations, and
verify each candidate actually executes rather than merely existing on disk (a
Store stub exists and does nothing useful). Returns "" if nothing works, and
the caller prints the capture path so the run is recoverable by hand.
#>
function Find-Python {
    # Memoised: this is asked three times (discovery, the unreachable
    # diagnosis, the decode) and each miss spawns a process per candidate.
    if ($null -ne $script:_python) { return $script:_python }

    $candidates = @('py', 'python')
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Python" -Directory -ErrorAction SilentlyContinue |
                   ForEach-Object { Join-Path $_.FullName 'python.exe' }
    $candidates += Get-ChildItem 'C:\Program Files\Python*' -Directory -ErrorAction SilentlyContinue |
                   ForEach-Object { Join-Path $_.FullName 'python.exe' }

    foreach ($c in $candidates) {
        try {
            $out = & $c -c "print('ok')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -eq 'ok') { $script:_python = $c; return $c }
        } catch { }
    }
    $script:_python = ""
    return ""
}
function Step($n, $title, $why, $lines) {
    Write-Host ""
    Write-Host "  [$n] $title" -ForegroundColor Yellow
    Write-Host "      unblocks: $why" -ForegroundColor DarkGray
    foreach ($l in $lines) { Write-Host "      $l" }
}

# --- must be elevated -------------------------------------------------------
$admin = ([Security.Principal.WindowsPrincipal] `
          [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Fail @"
This needs an ADMINISTRATOR terminal -- pktmon will not capture without it.

  Right-click PowerShell or Windows Terminal, choose "Run as administrator",
  then run this script again from the repository folder.
"@
}

# --- where is the Drobo -----------------------------------------------------
$repo = Split-Path -Parent $PSScriptRoot
if (-not $DroboIP) {
    $cfg = Join-Path $repo 'agent\config.json'
    if (Test-Path $cfg) {
        try { $DroboIP = (Get-Content $cfg -Raw | ConvertFrom-Json).drobo.host } catch { }
    }
}
# --- does the configured address still answer? ------------------------------
# DHCP moves this device. The address in config.json was correct when it was
# written and silently goes stale the next time the lease changes -- which has
# already cost one capture attempt. So the configured address is a HINT: if it
# does not answer, fall back to the same mDNS browse the agent uses, rather
# than failing with "not reachable" at somebody who is standing right next to a
# working Drobo.
function Test-DroboAt([string]$addr) {
    if (-not $addr -or $addr -eq 'auto') { return $false }
    try {
        $probe = New-Object System.Net.Sockets.TcpClient
        $iar = $probe.BeginConnect($addr, 5000, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(2500, $false) -and $probe.Connected
        $probe.Close()
        return $ok
    } catch { return $false }
}

if (-not (Test-DroboAt $DroboIP)) {
    if ($DroboIP -and $DroboIP -ne 'auto') {
        Write-Host ""
        Say "$DroboIP did not answer -- looking for the Drobo on the network..." 'Yellow'
    } else {
        Write-Host ""
        Say "Looking for the Drobo on the network..." 'Cyan'
    }

    # Find-Python, not a bare `py`. Its own comment explains why -- under
    # elevation the WindowsApps alias may not resolve -- and this call site was
    # the one place that ignored it, so the search died with
    # CommandNotFoundException on exactly the machines it was written for.
    $found = ""
    $pyFind = Find-Python
    if (-not $pyFind) {
        Say "No working Python here, so the network search is unavailable." 'Yellow'
        Say "Give the address directly instead:  -DroboIP 10.0.0.5" 'Yellow'
    } else {
        Push-Location (Join-Path $repo 'sdk')
        try {
            # discover_mdns returns every candidate; verify_candidate proves
            # which is a real Drobo. Link-local (169.254.x) answers are skipped
            # -- a device advertising one alongside a routable address should
            # always be reached on the routable one.
            $found = & $pyFind -c @"
from drobo_nasd import discovery
for c in discovery.discover_mdns(timeout=4.0):
    if c.host.startswith('169.254.'):
        continue
    try:
        discovery.verify_candidate(c, timeout=6)
        print(c.host)
        break
    except Exception:
        pass
"@ 2>$null
        } catch {
            # A search that fails is not a reason to abandon the session -- the
            # caller can still pass -DroboIP by hand.
            $found = ""
        } finally { Pop-Location }
    }

    $found = ($found | Select-Object -First 1)
    if ($found) {
        $DroboIP = $found.Trim()
        Say "Found it at $DroboIP" 'Green'
    } elseif ($pyFind) {
        # A search that RAN and found nothing has to say so. Testing this found
        # a Python that could not import the SDK's dependencies: its errors
        # went to the suppressed stderr, the search "succeeded" with no result,
        # and the run carried on against the stale address as though the search
        # had never been attempted.
        Say "The network search did not find a Drobo." 'Yellow'
    }
}

if (-not $DroboIP -or $DroboIP -eq 'auto') {
    Fail "No Drobo found, and no address given. Pass one:  -DroboIP 10.0.0.5"
}

# --- is it actually reachable ----------------------------------------------
# Checked BEFORE recording rather than after. Discovering at the end that five
# minutes of clicking produced an empty capture is a miserable way to find out
# the PC was on the wrong network.
Write-Host ""
Write-Host "  Checking $DroboIP answers before we start recording..." -ForegroundColor Cyan
$reachable = $false
try {
    $probe = New-Object System.Net.Sockets.TcpClient
    $probe.Connect($DroboIP, 5000)
    $reachable = $probe.Connected
    $probe.Close()
} catch { }
if (-not $reachable) {
    Write-Host ""
    Write-Host "  $DroboIP did not answer on port 5000." -ForegroundColor Red
    Write-Host ""
    $pyEarly = Find-Python
    if ($pyEarly) {
        Push-Location (Join-Path $repo 'sdk')
        try { & $pyEarly -m drobo_nasd.netcheck $DroboIP } catch { } finally { Pop-Location }
    }
    Fail @"
Nothing to capture until the Drobo answers. Check:
  - this PC is on the same network as the Drobo (the app's Network banner
    will say so if it is not)
  - the Drobo is powered on and its lights are steady
"@
}
Write-Host "  It answered. Good." -ForegroundColor Green

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$dir   = Join-Path $env:TEMP "drobo-session-$stamp"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$etl    = Join-Path $dir 'session.etl'
$pcapng = Join-Path $dir 'session.pcapng'

Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host "   Read the whole list FIRST, then press Enter to start." -ForegroundColor Cyan
Write-Host "   Recording runs for $Seconds seconds and cannot be paused." -ForegroundColor Cyan
Write-Host "  ============================================================" -ForegroundColor Cyan

Step 1 "Open Drobo Dashboard and let it connect" `
     "nothing on its own -- but every later step needs a live session" `
     @("Just open it and wait until it shows your Drobo.",
       "Do not click anything else yet.")

# Now with the button's REAL NAME. The 2026-08-05 and 08-06 sessions both
# missed this step because they said "click Identify" and there is no button
# called Identify. It is "Blink Lights", on the Tools page, next to Rename --
# established 2026-08-06 by reading Drobo Dashboard.exe. Naming a control by
# its internal name and hoping the operator finds it is how a step goes
# unrecorded twice.
#
# CORRECTED 2026-09-26. This step was written as if Dashboard's press had never
# been seen. It had: the 2026-07-25 capture holds one ON press, carrying
# IdentifyInterval 900, and protocol-map.md's command table recorded it. What
# is still unrecorded is the OFF press (our Stop sends 0, a guess), and what
# the lights do for the ORIGINAL Dashboard on this unit. Whether OUR send of
# 900 blinks needs no recording at all -- docs/hardware-session.md part A
# does it before this script runs.
Step 2 "Tools -> BLINK LIGHTS. Press it, then press it again to turn it off." `
     "eCmdIdentify's OFF press -- the ON press (interval 900) was captured 2026-07-25" `
     @("It is NOT called Identify. On the Tools page, next to Rename, there is",
       "a button labelled 'Blink Lights' with a small sun/rays icon.",
       "Press it once and watch the front of the Drobo. Then press it again",
       "to turn it off.",
       "The ON press is already on record: on 2026-07-25 the original",
       "Dashboard sent IdentifyInterval 900. Nobody has recorded the OFF",
       "press, and our Stop button sends 0 as a guess. The second press",
       "settles that.",
       "Write down what the lights did for the ORIGINAL Dashboard, including",
       "'nothing' -- that says whether this unit blinks for it at all.")

# The event log's REQUEST is captured (eCmdGetUserEventLog with
# <Path>/uel.log</Path>, 2026-08-05); the shape of the ANSWER has never been
# decoded. Opening the page that shows the log makes the device send it.
Step 3 "Open the page that shows the device's event log" `
     "the REPLY format of eCmdGetUserEventLog (91) -- the request is already captured" `
     @("Find the screen that lists the Drobo's own events/history and open",
       "it. Let it load completely; scroll it once if it scrolls.",
       "The request Dashboard sends is already captured -- what we need is",
       "the shape of the ANSWER, so letting the page fill is the whole step.",
       "Note down roughly what it showed on screen: that is the cross-check",
       "for whatever the packets say.")

# Re-added 2026-09-21, because a feature now needs them (see the header): a
# user is created and given a permission in the FIRST recording (steps 4 and
# 5), and deleted in a SECOND one (step 6), after `py tools\access_codes.py
# after`. Deleting it in the first recording would remove the user before the
# "after" snapshot, so the permission code step 5 set would never be seen.
Step 4 "Create a throwaway Drobo user" `
     "creating user accounts -- wanted (2026-09-21), and never seen on the wire" `
     @("Find where Dashboard manages the Drobo's users and add ONE new user.",
       "Call it zzdeleteme -- nothing personal, and obviously disposable.",
       "Give it a throwaway password you use NOWHERE else: this protocol is",
       "unencrypted, so the password lands in the capture in plain text.",
       "Apply or save it, and let Dashboard finish before the next step.")

Step 5 "Give zzdeleteme a permission on ZZDELETEME -- anything but read/write" `
     "share access codes 0 and 2 -- 'access_codes.py after' pins the word to the number" `
     @("Open the share ZZDELETEME (made from this project's app before this",
       "recording) and give zzdeleteme a permission OTHER than read/write --",
       "read-only, or no access. Apply it.",
       "Write down the EXACT words Dashboard shows for the level you chose.",
       "Only ZZDELETEME. Never ZZTESTZZ2, never a share with real files.")

Step 6 "SECOND RECORDING ONLY -- delete zzdeleteme" `
     "deleting user accounts -- never seen on the wire" `
     @("NOT in this recording if you have just done steps 4 and 5.",
       "First: stop this recording, close Dashboard, and -- in an ordinary",
       "command window opened in the repository folder, NOT this one -- run",
       "    py tools\access_codes.py after",
       "Then run this capture again (double-click the .bat), open Dashboard,",
       "delete zzdeleteme -- only that user -- and press Enter here to stop.",
       "In that second recording, steps 1 and 6 are the whole job.")

# Removed 2026-08-16 (settled on hardware, do not re-record):
#   - "Spin the drives down on demand": there is no such action -- confirmed
#     from a capture, and the full enable/disable semantics were then
#     verified on the live device (a bare delay of 0 is coerced; off = clear
#     the FeatureStates bit AND zero the delay together).
#   - "Cycle every permission level": code 1 (read/write) is CONFIRMED and
#     code 0 is written as "no access" but its meaning is NOT confirmed --
#     the maintainer could not verify a 0-share was unopenable rather than merely
#     unmounted (2026-09-01). Code 2 remains unobserved. Re-add this step
#     only if a feature needs 0 or 2 settled.
#
# Removed 2026-08-25:
#   - "Create a share, then delete it": DONE -- captured 2026-08-25. Both
#     eCmdSetConfig formats (the compact new-share node; the whole-config
#     resend with <ShareDeleteConfirm>Y</...>) are in docs/protocol-map.md.
#   - "Read only" (code 2), Time Machine on/off, add/remove a user: NOT
#     settled -- still never observed on the wire. Off the ask list because
#     nothing being built is blocked on them; re-add whichever one a
#     feature actually needs. (Add/remove a user and the permission-code
#     measurement came back on 2026-09-21 as steps 4-6 above, because user
#     management is now wanted. Time Machine stays off the list.)

Write-Host ""
Write-Host "  Do NOT: format, repair, update firmware, or change the disk pack." -ForegroundColor Red
Write-Host ""
Read-Host "  Press Enter when you have read all of that and are ready"

# --- record -----------------------------------------------------------------
# The whole record/stop/convert sequence lives in try/finally, and the reason
# is a 0 KB capture. The old loop was a fixed countdown with no way out; the
# only exit was Ctrl+C, which killed the SCRIPT but not pktmon -- the capture
# kept running system-wide, `pktmon stop` and the etl2pcap conversion never
# ran, and the maintainer was left with an empty .etl and no .pcapng. Now: press
# Enter (or S) to stop early the moment you have done what you came for, and
# whatever way this block is left, pktmon is stopped and the file converted.
pktmon filter remove | Out-Null
pktmon filter add DroboSession -i $DroboIP | Out-Null
pktmon start --capture --pkt-size 0 --file-name $etl --file-size 512 | Out-Null
$recording = $true

try {
    Write-Host ""
    Write-Host "  RECORDING -- go and work through the steps above." -ForegroundColor Green
    Write-Host "  Press ENTER (or S) here to STOP EARLY once you are done." -ForegroundColor Cyan
    Write-Host ""

    $deadline = (Get-Date).AddSeconds($Seconds)
    $stoppedEarly = $false
    while ((Get-Date) -lt $deadline) {
        $left = [int][math]::Ceiling(($deadline - (Get-Date)).TotalSeconds)
        Write-Host -NoNewline ("`r  {0}:{1:D2} remaining...  (Enter = stop now)   " -f [math]::Floor($left / 60), ($left % 60))
        # Poll the keyboard rather than sleep a full second blind, so a
        # keypress is honoured within a quarter second.
        $t = 0
        while ($t -lt 4 -and (Get-Date) -lt $deadline) {
            # KeyAvailable THROWS when stdin is not a real console (piped or
            # redirected input). Guarded so an odd terminal degrades to "no
            # early stop" -- the deadline and the finally still protect the
            # capture -- instead of aborting the recording mid-loop.
            $key = $null
            try { if ([Console]::KeyAvailable) { $key = [Console]::ReadKey($true) } } catch { $key = $null }
            if ($key -and ($key.Key -eq 'Enter' -or $key.KeyChar -eq 's' -or $key.KeyChar -eq 'S')) { $stoppedEarly = $true; break }
            Start-Sleep -Milliseconds 250
            $t++
        }
        if ($stoppedEarly) { break }
    }
    Write-Host ("`r  " + $(if ($stoppedEarly) { "Stopped early by you." } else { "Time is up." }) + "                          ")
}
finally {
    # Runs on normal end, early stop, AND Ctrl+C. Nothing may leave pktmon
    # running or the capture unconverted.
    if ($recording) {
        pktmon stop | Out-Null
        pktmon filter remove | Out-Null
        $recording = $false
        Push-Location $dir
        try { pktmon etl2pcap $etl --out $pcapng | Out-Null } finally { Pop-Location }
    }
}

if (-not (Test-Path $pcapng)) { Fail "Conversion produced no pcapng. Capture kept at $etl" }
$size = (Get-Item $pcapng).Length
Write-Host ""
Write-Host "  Captured: $pcapng  ($([math]::Round($size/1KB)) KB)" -ForegroundColor Green

# --- read it ----------------------------------------------------------------
Write-Host ""
# Built with Join-Path, and NOT spelled out inside the message string below.
# It was, once, and the backslash-t of "\tools\" survived a round trip through
# an editor as a literal TAB -- so the one line printed to somebody with no
# Python, whose entire purpose is to be copied and pasted, told them to run a
# path that does not exist.
$extractor = Join-Path $repo 'tools\extract_commands.py'
$py = Find-Python
if (-not $py) {
    # The capture is safe on disk; only the convenience decode is unavailable.
    # Say exactly how to finish it by hand rather than implying a lost session.
    Write-Host "  Recorded successfully, but no working Python was found to decode it." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  THE CAPTURE IS SAFE. Decode it yourself with:" -ForegroundColor Green
    Write-Host "      py `"$extractor`" `"$pcapng`""
    Write-Host ""
    Write-Host "  Or feed the path above to tools/extract_commands.py later."
} else {
    Write-Host "  --- every command Dashboard sent, with its parameters ---" -ForegroundColor Cyan
    & $py $extractor $pcapng
}

Write-Host ""
Write-Host "  Capture kept at: $dir" -ForegroundColor Green
Write-Host "  It is outside the repository. DO NOT COMMIT IT -- captures carry"
Write-Host "  device serials and anything typed while recording, including"
Write-Host "  passwords, because nasd has no encryption."
Write-Host ""
Write-Host "  Save the output above, along with:" -ForegroundColor Yellow
Write-Host "    - whether this was the first recording or the second (step 6)"
Write-Host "    - which steps you managed and which you skipped"
Write-Host "    - what the lights did on Blink Lights, if anything (step 2)"
Write-Host "    - roughly what the event log page showed on screen (step 3) --"
Write-Host "      that is the cross-check for whatever the packets say"
Write-Host "    - the exact words of the permission you gave zzdeleteme (step 5)"
Write-Host ""
Write-Host "  First recording? Close Dashboard now and, in an ordinary command" -ForegroundColor Yellow
Write-Host "  window opened in the repository folder (not this one), run" -ForegroundColor Yellow
Write-Host "      py tools\access_codes.py after" -ForegroundColor Yellow
Write-Host "  BEFORE the second recording deletes the user (step 6)." -ForegroundColor Yellow
