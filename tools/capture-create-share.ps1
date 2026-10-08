<#
.SYNOPSIS
    Capture the original Dashboard CREATING and then DELETING one throwaway
    share -- the two share writes this project has never seen on the wire.

.DESCRIPTION
    STATUS: DONE 2026-08-25. This capture succeeded -- the create and delete
    write formats are recorded in docs/protocol-map.md ("Create and delete a
    share -- captured"). Nothing asks for a re-run; keep this script only for
    recording another unit or firmware version someday.

    Two things at once, from one short recording:

      1. Creating a share and deleting a share have NEVER been observed on the
         wire (protocol-map.md lists both as "never attempted"). This project
         refuses to guess a write format, so its SDK cannot do either yet. The
         original Dashboard knows both formats; recording it teaches us.

      2. The throwaway is empty and brand new, so deleting it costs nothing --
         and doing create then delete in one sitting captures the pair.

    The share-permission round-trip that once needed a throwaway target has
    already been verified on hardware (2026-08-12), so nothing here depends
    on the share surviving. Press ENTER to stop the recording as soon as the
    delete has completed -- no need to wait out the clock.

.NOTES
    MUST BE RUN AS ADMINISTRATOR -- pktmon needs it.
        Right-click PowerShell -> Run as administrator, cd to the repo, then:
        powershell -ExecutionPolicy Bypass -File tools\capture-create-share.ps1

    Drobo Dashboard 3.5.0 must be CLOSED before you press Enter, so the capture
    sees it connect from scratch.
#>
param([string]$DroboIP = "", [int]$Seconds = 180)

$ErrorActionPreference = 'Stop'
function Say($m, $c = 'Gray') { Write-Host "  $m" -ForegroundColor $c }
function Fail($m) { Write-Host ""; Say $m 'Red'; exit 1 }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Fail "Run this from an ADMINISTRATOR PowerShell -- pktmon needs it." }

$repo = Split-Path -Parent $PSScriptRoot
if (-not $DroboIP) {
    # Same discovery the agent uses; the config address goes stale with DHCP.
    Push-Location (Join-Path $repo 'sdk')
    try {
        $DroboIP = & py -c "from drobo_nasd import discovery; c=[x for x in discovery.discover_mdns(timeout=5.0) if not x.host.startswith('169.254.')]; print(c[0].host if c else '')" 2>$null
    } finally { Pop-Location }
    $DroboIP = "$DroboIP".Trim()
}
if (-not $DroboIP) { Fail "No Drobo found. Pass its address:  -DroboIP 10.0.0.5" }
Say "Drobo at $DroboIP" 'Green'

$stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
$dir    = Join-Path $env:TEMP "drobo-create-share-$stamp"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$etl    = Join-Path $dir 'create.etl'
$pcapng = Join-Path $dir 'create.pcapng'

Write-Host ""
Say "TWO steps. Read them, then press Enter to start (up to $Seconds s; stop early with Enter)." 'Cyan'
Write-Host ""
Say "[1] Open Drobo Dashboard, go to Shares, and CREATE a new share:" 'Yellow'
Say "      name it exactly:  ZZTESTZZ" 'White'
Say "      leave every other setting at its default; do not add users." 'DarkGray'
Say "      Apply / Save it, and wait until it shows in the list." 'DarkGray'
Say "[2] Then DELETE that same share (ZZTESTZZ only), and confirm." 'Yellow'
Say "      It is brand new and empty, so deleting it cannot lose anything." 'DarkGray'
Say "    Then press ENTER back here to stop the recording." 'Cyan'
Write-Host ""
Say "Do NOT: format, repair, update firmware, or touch any other share." 'Red'
Write-Host ""
Read-Host "  Press Enter when Dashboard is CLOSED and you are ready"

pktmon filter remove | Out-Null
pktmon filter add DroboCreate -i $DroboIP | Out-Null
pktmon start --capture --pkt-size 0 --file-name $etl --file-size 256 | Out-Null
$recording = $true
try {
    Write-Host ""
    Say "RECORDING -- open Dashboard and do the steps now." 'Green'
    Say "Press ENTER (or S) here to STOP EARLY once you are done." 'Cyan'
    $deadline = (Get-Date).AddSeconds($Seconds)
    $stoppedEarly = $false
    while ((Get-Date) -lt $deadline) {
        $left = [int][math]::Ceiling(($deadline - (Get-Date)).TotalSeconds)
        Write-Host -NoNewline ("`r  {0}:{1:D2} remaining...  (Enter = stop now)   " -f [math]::Floor($left / 60), ($left % 60))
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
    # Stops pktmon and converts on normal end, early stop, AND Ctrl+C -- the
    # old fixed loop left pktmon running and the .etl empty when interrupted.
    if ($recording) {
        pktmon stop | Out-Null
        pktmon filter remove | Out-Null
        Push-Location $dir
        try { pktmon etl2pcap $etl --out $pcapng | Out-Null } finally { Pop-Location }
    }
}
if (-not (Test-Path $pcapng)) { Fail "Conversion produced no pcapng. Raw capture kept at $etl" }

Write-Host ""
Say "Captured: $pcapng ($([math]::Round((Get-Item $pcapng).Length/1KB)) KB)" 'Green'
Say "Decoding what Dashboard sent..." 'Cyan'
& py (Join-Path $repo 'tools\extract_commands.py') $pcapng --command eCmdSetConfig
Write-Host ""
Say "Capture kept OUTSIDE the repo at: $dir" 'Green'
Say "Do not commit it. Tell Claude the capture is done and it will decode create + delete." 'Yellow'
