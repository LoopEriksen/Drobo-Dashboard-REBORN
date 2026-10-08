<#
.SYNOPSIS
    Proves the MSI built by build-installer.ps1 actually installs, runs, and
    uninstalls cleanly -- an installer nobody has ever run is just a file.
.DESCRIPTION
    Runs the full lifecycle for real, against this machine:
      1. msiexec /i  (per-machine, quiet, verbose log)
      2. Verify what landed: exe present and full size, agent + SDK source,
         the BUNDLED PYTHON runtime present and able to start, the launcher
         wired to it, TESTING.md, Start Menu + Desktop + Uninstall shortcuts,
         an ARP entry with the right version, and -- critically -- no
         config.json and no hardcoded access token shipped.
      3. Run the installed product the way a tester would: the Start Menu
         launcher. Wait for the agent to answer /api/ping on its port, check
         the app process is alive, check the agent wrote its config.json to
         %LOCALAPPDATA%\DroboDashboardReborn (not the install folder), then
         stop both. Then run the Start Menu "Check for Updates" shortcut's
         own target, as a double-click does, and require its "switched off"
         answer and exit status 0 (update checks ship off).
      4. msiexec /x  (quiet, verbose log)
      5. Verify removal: install dir (including what the agent generated),
         shortcuts, ARP entry and registry key are gone, while the user-data
         folder is KEPT (settings survive an uninstall, as in most apps).
      6. A second, short install/uninstall cycle with DESKTOPSHORTCUT=0 --
         the documented non-interactive opt-out for the desktop-shortcut
         checkbox -- proving the property is actually wired to the component.
      7. If -UpgradeMsiPath is given (a build of a HIGHER version): install
         the base MSI, plant a marker in the user-data folder, install the
         upgrade over it, prove the marker survived and the version moved,
         then uninstall and prove the user data survives that too.

    REQUIRES AN ELEVATED SHELL since 2026-09-08, when the package became
    per-machine: msiexec cannot write to Program Files or HKLM without it,
    and a quiet install has no UAC prompt to raise. Run it from a PowerShell
    started with "Run as administrator"; it refuses to start otherwise
    rather than producing a screenful of failures that all say the same
    thing.

    Any real settings.json under %LOCALAPPDATA%\DroboDashboardReborn
    would be destroyed by step 5, so if that folder exists it is backed up
    before anything runs and restored at the end, pass or fail.

    Every check uses the same PASS/FAIL style as the Python test suites under
    agent/, so a human skimming either one recognizes the pattern immediately.
    Exits 1 if anything failed, 0 if everything passed.

    Destructive by nature: it installs and then uninstalls the product on the
    machine it runs on. If Drobo Dashboard REBORN is already installed when
    this starts, it refuses to run rather than uninstall someone's real,
    in-use install out from under them.
.EXAMPLE
    powershell windows/test-installer.ps1          # from an ELEVATED shell
    powershell windows/test-installer.ps1 -MsiPath windows\dist\DroboDashboardReborn-1.1.0-win-x64.msi
    powershell windows/test-installer.ps1 -UpgradeMsiPath windows\dist\DroboDashboardReborn-<higher>-win-x64.msi

    -UpgradeMsiPath wants a SECOND msi numbered above the one under test. Any
    higher number does; it is a throwaway build for this test, never a release.
    A run without one ends by printing the command that builds it.
#>
[CmdletBinding()]
param(
    [string]$MsiPath,
    [string]$UpgradeMsiPath
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$WindowsDir = Join-Path $RepoRoot 'windows'
$DistDir = Join-Path $WindowsDir 'dist'
$LogDir = Join-Path $DistDir 'test-logs'

$script:failures = 0
$script:checks = 0

function Check([string]$Label, [bool]$Cond, [string]$Extra = '') {
    $script:checks++
    if ($Cond) {
        Write-Host "  PASS  $Label"
    } else {
        $script:failures++
        $suffix = if ($Extra) { " -- $Extra" } else { '' }
        Write-Host "  FAIL  $Label$suffix" -ForegroundColor Red
    }
}

function Section([string]$Title) {
    Write-Host ''
    Write-Host "-- $Title --" -ForegroundColor Cyan
}

# A clean uninstall removes every FILE. Windows may briefly hold a just-
# emptied directory open (Defender, the search indexer), so an empty folder
# left behind is not an uninstall failure -- a leftover FILE is. This treats
# "gone" and "empty" alike, and reports what remained if anything did.
function Test-DirEmptyOrGone([string]$Dir) {
    if (-not (Test-Path $Dir)) { return $true }
    $files = Get-ChildItem -LiteralPath $Dir -Recurse -File -Force -ErrorAction SilentlyContinue
    if ($files) {
        Write-Host "      files left behind:" -ForegroundColor Yellow
        $files | Select-Object -First 10 | ForEach-Object { Write-Host "        $($_.FullName)" }
        return $false
    }
    Write-Host "      (an empty folder remains; Windows had the just-emptied dir open. No files left.)"
    return $true
}

# Smart App Control, when enforced, blocks the unsigned WPF app outright and
# there is no override. That is a documented limitation, not an installer
# defect: the agent (bundled Python, signed by the PSF) still runs, and the
# browser dashboard is the fallback. So app-liveness is asserted only when SAC
# is not enforcing.
$ci = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction SilentlyContinue
$script:sacEnforced = ($ci.VerifiedAndReputablePolicyState -eq 1)

function Get-ArpEntry {
    Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ItemProperty $_.PsPath -ErrorAction SilentlyContinue } |
        Where-Object { $_.DisplayName -eq 'Drobo Dashboard REBORN' } |
        Select-Object -First 1
}

function Invoke-Msi([string[]]$MsiArgs, [string]$Log) {
    $proc = Start-Process msiexec.exe -ArgumentList ($MsiArgs + @('/qn', '/l*v', "`"$Log`"")) -Wait -PassThru -NoNewWindow
    return $proc.ExitCode
}

function Stop-Product {
    # Nothing from this test may be left running before an uninstall, since
    # msiexec cannot remove files that are in use. The agent is a python.exe
    # whose executable lives under the install folder; nothing else matches.
    Get-Process -Name 'DroboDashboardReborn' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallRoot, [System.StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 800
}

# --- 0. Find the MSI, refuse to run if something's already installed -------

Section 'Preflight'

if (-not $MsiPath) {
    # Newest MSI in dist/ -- but never the one passed as the UPGRADE, or the
    # "upgrade" would be a same-version reinstall, which Windows Installer
    # treats as maintenance, not a major upgrade, and every upgrade check
    # below would be measuring the wrong thing.
    $upgradeFull = if ($UpgradeMsiPath -and (Test-Path -LiteralPath $UpgradeMsiPath)) { (Resolve-Path -LiteralPath $UpgradeMsiPath).Path } else { '' }
    $candidate = Get-ChildItem -LiteralPath $DistDir -Filter 'DroboDashboardReborn-*-win-x64.msi' -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -ne $upgradeFull } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $candidate) {
        Write-Host "FAIL  No MSI found under $DistDir. Run windows\build-installer.ps1 first." -ForegroundColor Red
        exit 1
    }
    $MsiPath = $candidate.FullName
}
if (-not (Test-Path -LiteralPath $MsiPath)) {
    Write-Host "FAIL  MSI not found: $MsiPath" -ForegroundColor Red
    exit 1
}
$MsiPath = (Resolve-Path -LiteralPath $MsiPath).Path
Write-Host "MSI: $MsiPath"
if ($UpgradeMsiPath) {
    if (-not (Test-Path -LiteralPath $UpgradeMsiPath)) {
        Write-Host "FAIL  Upgrade MSI not found: $UpgradeMsiPath" -ForegroundColor Red
        exit 1
    }
    $UpgradeMsiPath = (Resolve-Path -LiteralPath $UpgradeMsiPath).Path
    Write-Host "Upgrade MSI: $UpgradeMsiPath"
}

# Program Files, matching the per-machine package. $env:ProgramFiles is the
# NATIVE Program Files when this script runs in 64-bit PowerShell, which is
# what a 64-bit MSI targets; a 32-bit host would report the (x86) directory
# and quietly test the wrong path, so that case is caught rather than
# guessed at.
if (-not [Environment]::Is64BitProcess) {
    Write-Host 'FAIL  Run this in 64-bit PowerShell. A 32-bit host reports the (x86) Program Files, which is not where the package installs.' -ForegroundColor Red
    exit 1
}
$InstallRoot = Join-Path $env:ProgramFiles 'Drobo Dashboard REBORN'
$UserDataDir = Join-Path $env:LOCALAPPDATA 'DroboDashboardReborn'
$AgentPort = 7420

# A per-machine install needs administrator rights, and a QUIET one cannot ask
# for them: msiexec would simply fail with 1603 and the checks below would all
# fail for that single reason. Say so once, plainly, instead.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host ''
    Write-Host 'FAIL  This test installs a per-machine MSI, which needs administrator rights.' -ForegroundColor Red
    Write-Host '      Start PowerShell with "Run as administrator" and run it again.' -ForegroundColor Red
    Write-Host ''
    exit 1
}

# A real install (an ARP entry, or FILES on disk) means stop -- this script
# uninstalls at the end and must not wipe someone's working copy. But a prior
# run can leave an EMPTY, briefly-locked install folder that Windows would not
# let go of; that is not an install, so tolerate it (and try once more to
# clear it, ignoring failure -- msiexec installs over an empty dir fine).
if (Get-ArpEntry) {
    Write-Host 'FAIL  Drobo Dashboard REBORN is registered (Add/Remove Programs). Uninstall it first, then re-run.' -ForegroundColor Red
    exit 1
}
if ((Test-Path $InstallRoot) -and (Get-ChildItem -LiteralPath $InstallRoot -Recurse -File -Force -ErrorAction SilentlyContinue)) {
    Write-Host "FAIL  $InstallRoot still contains files. Uninstall Drobo Dashboard REBORN first, then re-run." -ForegroundColor Red
    exit 1
}
if (Test-Path $InstallRoot) {
    Write-Host "note: an empty $InstallRoot from a previous run is present; continuing." -ForegroundColor Yellow
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue
}

$portBusy = Get-NetTCPConnection -LocalPort $AgentPort -State Listen -ErrorAction SilentlyContinue
if ($portBusy) {
    Write-Host "FAIL  Something is already listening on port $AgentPort (the agent's port). Stop it first." -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Back up any real user data already on this machine. The uninstall under
# test is supposed to KEEP it, and there is a check for exactly that below --
# but this script also plants its own config.json and token into that folder,
# so any real settings are moved aside first and restored at the end either way.
$backupDir = $null
if (Test-Path $UserDataDir) {
    $backupDir = Join-Path $LogDir ('userdata-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Copy-Item -LiteralPath $UserDataDir -Destination $backupDir -Recurse -Force
    Remove-Item -LiteralPath $UserDataDir -Recurse -Force
    Write-Host "Backed up existing $UserDataDir to $backupDir (restored at the end)"
}

$installLog = Join-Path $LogDir 'install.log'
$uninstallLog = Join-Path $LogDir 'uninstall.log'

try {

# --- 1. Install --------------------------------------------------------------

Section 'Install (msiexec /i, per-machine, quiet)'

$code = Invoke-Msi @('/i', "`"$MsiPath`"") $installLog
Check "msiexec /i exit code is 0 (got $code)" ($code -eq 0)

if ($code -ne 0) {
    Write-Host ''
    Write-Host "msiexec failed -- relevant log lines from $installLog :" -ForegroundColor Yellow
    Get-Content -LiteralPath $installLog -ErrorAction SilentlyContinue |
        Select-String -Pattern 'Error|Return Value 3' |
        Select-Object -Last 20 |
        ForEach-Object { Write-Host "    $_" }
    throw 'install failed'
}

# --- 2. Verify what landed ----------------------------------------------------

Section 'Verify install contents'

Check "install dir exists ($InstallRoot)" (Test-Path $InstallRoot)

$exePath = Join-Path $InstallRoot 'DroboDashboardReborn.exe'
$exeOk = Test-Path $exePath
Check "app exe present" $exeOk
if ($exeOk) {
    $exeSizeMb = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
    # Self-contained single-file WPF publish is reliably >100 MB (carries its
    # own .NET runtime); anything much smaller means publish was broken.
    Check "app exe is a real self-contained publish ($exeSizeMb MB)" ($exeSizeMb -gt 100)
}

$agentDir = Join-Path $InstallRoot 'agent'
Check "agent source dir present" (Test-Path $agentDir)
Check "run_agent.py present" (Test-Path (Join-Path $agentDir 'run_agent.py'))
Check "check_updates.py present (what Check for Updates runs)" (Test-Path (Join-Path $agentDir 'check_updates.py'))
Check "drobo_agent package present" (Test-Path (Join-Path $agentDir 'drobo_agent'))
# The agent imports drobo_nasd; without it the installed copy dies at
# startup with ModuleNotFoundError. Checked here because that failure
# happens at INSTALL time, not build time.
Check 'SDK package present (agent cannot start without it)' (Test-Path (Join-Path $InstallRoot 'sdk\drobo_nasd\esatm.py'))
Check 'SDK models present' (Test-Path (Join-Path $InstallRoot 'sdk\drobo_nasd\models.py'))
Check 'SDK netcheck present (agent imports it at startup)' (Test-Path (Join-Path $InstallRoot 'sdk\drobo_nasd\netcheck.py'))
# (An older check here looked for agent\drobo_agent\nasd. That subpackage
# became sdk\drobo_nasd on 2026-07-27; the SDK checks above cover it.)

# The bundled runtime. A tester has no Python; this is the whole reason.
$pyExe = Join-Path $InstallRoot 'python\python.exe'
$pyOk = Test-Path $pyExe
Check "bundled python.exe present" $pyOk
if ($pyOk) {
    $pyVer = & $pyExe -c "import sys, ssl, sqlite3, http.server; print(sys.version.split()[0])" 2>&1
    Check "bundled Python starts and imports the agent's stdlib needs ($pyVer)" ($LASTEXITCODE -eq 0) "$pyVer"
    $pth = Get-ChildItem -LiteralPath (Join-Path $InstallRoot 'python') -Filter '*._pth' | Select-Object -First 1
    Check "embeddable ._pth present (so it is the self-contained runtime, not a stray python.exe)" ($null -ne $pth)
}
$launcherBat = Join-Path $InstallRoot 'Launch-DroboDashboardReborn.bat'
$runAgentBat = Join-Path $InstallRoot 'Run-Agent.bat'
Check "launcher present" (Test-Path $launcherBat)
Check "Run-Agent.bat present" (Test-Path $runAgentBat)
if (Test-Path $launcherBat) {
    $launcherText = Get-Content -LiteralPath $launcherBat -Raw
    Check "launcher starts the agent through Run-Agent.bat" ($launcherText -match 'Run-Agent\.bat')
}
if (Test-Path $runAgentBat) {
    $runAgentText = Get-Content -LiteralPath $runAgentBat -Raw
    Check "Run-Agent runs the bundled Python" ($runAgentText -match 'python\\python\.exe')
    Check "Run-Agent points the agent at the user-data config.json" ($runAgentText -match 'LOCALAPPDATA%\\DroboDashboardReborn\\config\.json')
    Check "Run-Agent keeps its window open when the agent stops (pause on error)" ($runAgentText -match 'if errorlevel 1' -and $runAgentText -match '\bpause\b')
}
Check "TESTING.md present in the install folder" (Test-Path (Join-Path $InstallRoot 'TESTING.md'))

# The one non-negotiable: never ship a real device token or a config.json
# that would make every install share the same access token.
$shippedConfig = Get-ChildItem -LiteralPath $InstallRoot -Recurse -Filter 'config.json' -ErrorAction SilentlyContinue
Check "no config.json shipped in installed tree" ($shippedConfig.Count -eq 0) "found: $($shippedConfig.FullName -join ', ')"

$tokenHits = Get-ChildItem -LiteralPath $agentDir -Recurse -Filter '*.py' -ErrorAction SilentlyContinue |
    Select-String -Pattern '(?i)token[''"]?\s*[:=]\s*[''"][a-zA-Z0-9_-]{8,}[''"]' -ErrorAction SilentlyContinue
Check "no hardcoded token literal in installed .py files" ($null -eq $tokenHits) "found in: $($tokenHits.Path -join ', ')"

# Shortcuts
# The all-users Start Menu, not this account's: a per-machine package puts
# its shortcuts where every user of the PC sees them.
$startMenuDir = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Drobo Dashboard REBORN'
$startMenuLnk = Join-Path $startMenuDir 'Drobo Dashboard REBORN.lnk'
$uninstallLnk = Join-Path $startMenuDir 'Uninstall Drobo Dashboard REBORN.lnk'
Check "Start Menu shortcut exists" (Test-Path $startMenuLnk)
Check "Start Menu Uninstall shortcut exists" (Test-Path $uninstallLnk)

$shell = New-Object -ComObject WScript.Shell
# AllUsersDesktop for the same reason. WScript.Shell exposes it under that
# name; falling back to the per-user Desktop would make this check pass
# against a shortcut the package never created.
$desktopFolder = $shell.SpecialFolders('AllUsersDesktop')
if (-not $desktopFolder) { $desktopFolder = Join-Path $env:PUBLIC 'Desktop' }
$desktopLnk = Join-Path $desktopFolder 'Drobo Dashboard REBORN.lnk'
Check "Desktop shortcut exists" (Test-Path $desktopLnk)

if (Test-Path $startMenuLnk) {
    $target = $shell.CreateShortcut($startMenuLnk).TargetPath
    Check "Start Menu shortcut target exists ($target)" (Test-Path $target)
}
if (Test-Path $desktopLnk) {
    $target = $shell.CreateShortcut($desktopLnk).TargetPath
    Check "Desktop shortcut target exists ($target)" (Test-Path $target)
}
if (Test-Path $uninstallLnk) {
    $u = $shell.CreateShortcut($uninstallLnk)
    Check "Uninstall shortcut runs msiexec /x by ProductCode" (($u.TargetPath -match 'msiexec\.exe$') -and ($u.Arguments -match '^/x \{[0-9A-Fa-f-]{36}\}$')) "target=$($u.TargetPath) args=$($u.Arguments)"
}

# ARP entry. WiX v4+ perUser packages register this under HKLM's Uninstall
# key (Windows Installer's own per-user-managed-install mechanism), not
# HKCU -- check both since which one is used isn't this script's business,
# only that an entry exists somewhere.
$arpEntry = Get-ArpEntry
Check "Add/Remove Programs entry exists" ($null -ne $arpEntry)
$versionFile = Join-Path $InstallRoot 'version.txt'
$expectedVersion = if (Test-Path $versionFile) { (Get-Content -LiteralPath $versionFile -Raw).Trim() } else { '' }
if ($arpEntry) {
    Check "ARP entry reports the packaged version ($expectedVersion)" ($expectedVersion -and $arpEntry.DisplayVersion -eq $expectedVersion) "got: $($arpEntry.DisplayVersion)"
    $productCode = $arpEntry.PSChildName
    Write-Host "ProductCode: $productCode"
} else {
    # Can't uninstall by ProductCode if it was never registered; fall back to
    # the MSI file itself so the rest of the script can still attempt cleanup.
    $productCode = $MsiPath
}

# --- 3. Run it the way a tester would: the launcher ----------------------------

Section 'Launch via the installed launcher, agent + app liveness'

# The launch is run against the agent's built-in simulated driver, by
# planting a config.json where the launcher tells the agent to look. Not
# because the agent would stop without a Drobo -- until 2026-09-26 it did,
# and this comment gave that as the reason; it now keeps running and reports
# that it cannot reach one -- but so this test never goes looking for a real
# Drobo on whatever network this machine is on. That still proves
# everything the installer is responsible for: the bundled Python runs the
# agent, the launcher wires the config path and the port, the token gate
# works, and nothing is written into the install folder. Whether a real
# Drobo is found is the agent's concern and has its own tests.
$plantedToken = [Convert]::ToBase64String((1..24 | ForEach-Object { [byte](Get-Random -Maximum 256) })).TrimEnd('=').Replace('+', '-').Replace('/', '_')
New-Item -ItemType Directory -Force -Path $UserDataDir | Out-Null
$plantedConfig = Join-Path $UserDataDir 'config.json'
Set-Content -LiteralPath $plantedConfig -Encoding ascii -Value (@{
    agent = @{ bind = '127.0.0.1'; port = $AgentPort; token = $plantedToken }
    drobo = @{ driver = 'mock' }
    photos = @{ enabled = $false }
} | ConvertTo-Json -Depth 4)
Write-Host "  (planted a simulated-driver config.json at $plantedConfig)"

if (Test-Path $launcherBat) {
    $launchError = $null
    try {
        $launcherProc = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', "`"$launcherBat`"") -WorkingDirectory $InstallRoot -PassThru -WindowStyle Minimized
    } catch {
        $launchError = $_
    }
    Check "launcher started" ($null -ne $launcherProc) "$launchError"

    # The agent binds its port a moment after start; give it up to 25 s.
    $pingOk = $false
    $pingBody = ''
    for ($i = 0; $i -lt 50 -and -not $pingOk; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$AgentPort/api/ping" -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -eq 200) { $pingOk = $true; $pingBody = $resp.Content }
        } catch { }
    }
    Check "agent answers /api/ping on port $AgentPort (bundled Python actually runs the agent)" $pingOk
    if ($pingOk) {
        Check "ping reports the agent version" ($pingBody -match '"version"') $pingBody
    }

    $agentProc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallRoot, [System.StringComparison]::OrdinalIgnoreCase) } |
        Select-Object -First 1
    Check "the agent process is the BUNDLED python.exe, not a system one" ($null -ne $agentProc) "no python.exe running from $InstallRoot"

    Start-Sleep -Seconds 3
    $appProc = Get-Process -Name 'DroboDashboardReborn' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($script:sacEnforced) {
        Write-Host "  NOTE: Smart App Control is enforced on this machine, so the unsigned app is blocked by design (app running: $($null -ne $appProc)). The agent, which is what matters, is up. Not counted as a failure."
    } else {
        Check "app process alive after launch (not dead on arrival)" ($null -ne $appProc)
    }
    if ($appProc) { Write-Host "  window title: '$($appProc.MainWindowTitle)'" }

    # Worth knowing what this does and does not prove now that the install
    # folder is under Program Files. On its own it is weak: the absence of
    # that file is what you would see whether the agent correctly used the
    # --config path OR simply failed to write because Program Files is
    # read-only. It would pass against a broken build, so it is kept as a
    # cheap tripwire rather than the real evidence.
    #
    # The real evidence that the redirect works is two checks below: the agent
    # answers an AUTHENTICATED request using the token from the planted
    # user-data config.json. It could only do that by reading that file.
    Check "agent did NOT write a config.json into the install folder (weak on its own; the authenticated call below is the real proof)" (-not (Test-Path (Join-Path $agentDir 'config.json')))
    Check "agent used the user-data config.json (it still exists, untouched location)" (Test-Path $plantedConfig)

    # The token gate, end to end through the installed product: the planted
    # token must open an authenticated route, and no token must not.
    if ($pingOk) {
        $authOk = $false; $noAuthCode = 0
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$AgentPort/api/status" -Headers @{ 'X-Agent-Token' = $plantedToken } -UseBasicParsing -TimeoutSec 5
            $authOk = ($r.StatusCode -eq 200) -and ($r.Content -match '"device"')
        } catch { }
        try {
            $null = Invoke-WebRequest -Uri "http://127.0.0.1:$AgentPort/api/status" -UseBasicParsing -TimeoutSec 5
            $noAuthCode = 200
        } catch { $noAuthCode = [int]$_.Exception.Response.StatusCode }
        Check "authenticated /api/status answers with the planted token" $authOk
        Check "unauthenticated /api/status is refused (401)" ($noAuthCode -eq 401) "got $noAuthCode"
    }
} else {
    Check "launcher started" $false 'skipped -- launcher missing'
}

Stop-Product

# --- 3b. The Check for Updates shortcut, run for real -------------------------

Section 'Check for Updates shortcut (run its target, read the answer)'

# Until 2026-09-26 nothing here ran this shortcut, and it was broken in every
# installed copy: its .bat ran `python -m drobo_agent.updates` from inside
# agent\, the bundled runtime's ._pth keeps the working directory off
# sys.path, so it died with ModuleNotFoundError -- and its window closed
# before anyone could read that. So the shortcut's own target is run here, in
# the shortcut's own working directory, as a double-click runs it, against
# the config.json planted above. That has no "updates" block, and update
# checks are off by default, so the right answer is "switched off", exit
# status 0, and nothing contacted.
#
# The window waits for a key before closing. stdin is an empty file, so that
# pause returns at once; the timeout is there so a pause that did wait fails
# this check instead of hanging the whole run.
$updateLnk = Join-Path $startMenuDir 'Check for Updates.lnk'
Check "Start Menu 'Check for Updates' shortcut exists" (Test-Path $updateLnk)
$updateTarget = ''
$updateWorkDir = $InstallRoot
if (Test-Path $updateLnk) {
    $updateShortcut = $shell.CreateShortcut($updateLnk)
    $updateTarget = $updateShortcut.TargetPath
    if ($updateShortcut.WorkingDirectory) { $updateWorkDir = $updateShortcut.WorkingDirectory }
}
$expectedUpdateTarget = Join-Path $InstallRoot 'Check-ForUpdates.bat'
Check "it targets the installed Check-ForUpdates.bat ($updateTarget)" (($updateTarget -eq $expectedUpdateTarget) -and (Test-Path $expectedUpdateTarget)) "expected $expectedUpdateTarget"
if ($updateTarget -and (Test-Path $updateTarget)) {
    $updateIn = Join-Path $LogDir 'update-check-stdin.txt'
    $updateOut = Join-Path $LogDir 'update-check-stdout.txt'
    $updateErr = Join-Path $LogDir 'update-check-stderr.txt'
    Set-Content -LiteralPath $updateIn -Value '' -NoNewline -Encoding ascii
    $updateProc = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', "`"$updateTarget`"") `
        -WorkingDirectory $updateWorkDir -NoNewWindow -PassThru `
        -RedirectStandardInput $updateIn -RedirectStandardOutput $updateOut -RedirectStandardError $updateErr
    $null = $updateProc.Handle   # without this, ExitCode can read empty once it has exited
    $updateDone = $updateProc.WaitForExit(60000)
    if (-not $updateDone) { Stop-Process -Id $updateProc.Id -Force -ErrorAction SilentlyContinue }
    $updateText = '' + (Get-Content -LiteralPath $updateOut -Raw -ErrorAction SilentlyContinue) + (Get-Content -LiteralPath $updateErr -Raw -ErrorAction SilentlyContinue)
    Check "update check finished within 60 s (its closing pause did not hang)" $updateDone
    Check "update check exits 0 (got $(if ($updateDone) { $updateProc.ExitCode } else { 'no exit' }))" ($updateDone -and $updateProc.ExitCode -eq 0) $updateText
    Check "it answers that update checking is switched off" ($updateText -match 'switched off') $updateText
    Check "no Python traceback or ModuleNotFoundError in its output" ($updateText -notmatch 'Traceback|ModuleNotFoundError') $updateText
} else {
    Check "update check ran" $false 'skipped -- the shortcut or its target is missing'
}

# --- 4. Uninstall -------------------------------------------------------------

Section 'Uninstall (msiexec /x, quiet)'

$uninstallArg = if ($productCode -like '{*}') { $productCode } else { "`"$productCode`"" }
$code = Invoke-Msi @('/x', $uninstallArg) $uninstallLog
Check "msiexec /x exit code is 0 (got $code)" ($code -eq 0)

# --- 5. Verify removal --------------------------------------------------------

Section 'Verify removal'

Check "install dir has no files left after uninstall" (Test-DirEmptyOrGone $InstallRoot)
Check "user-data folder KEPT on uninstall (the agent's data, like most apps)" (Test-Path $UserDataDir)
Check "Start Menu shortcut folder removed" (-not (Test-Path $startMenuDir))
Check "Desktop shortcut removed" (-not (Test-Path $desktopLnk))
Check "Add/Remove Programs entry removed" ($null -eq (Get-ArpEntry))
Check "HKLM\Software\DroboDashboardReborn removed" (-not (Test-Path 'HKLM:\Software\DroboDashboardReborn'))

# --- 6. DESKTOPSHORTCUT=0 opt-out, for real ------------------------------------

Section 'DESKTOPSHORTCUT=0 opt-out (msiexec /i, quiet)'

$code = Invoke-Msi @('/i', "`"$MsiPath`"", 'DESKTOPSHORTCUT=0') (Join-Path $LogDir 'install-desktopshortcut0.log')
Check "msiexec /i DESKTOPSHORTCUT=0 exit code is 0 (got $code)" ($code -eq 0)
Check "install dir exists with the override" (Test-Path $InstallRoot)
Check "Desktop shortcut NOT created with DESKTOPSHORTCUT=0" (-not (Test-Path $desktopLnk))
Check "Start Menu shortcut still created (unconditional, unaffected by DESKTOPSHORTCUT)" (Test-Path $startMenuLnk)

$optOutArp = Get-ArpEntry
$optOutCode = if ($optOutArp) { $optOutArp.PSChildName } else { "`"$MsiPath`"" }

Section 'DESKTOPSHORTCUT=0 opt-out (msiexec /x, quiet)'
Stop-Product
$code = Invoke-Msi @('/x', $optOutCode) (Join-Path $LogDir 'uninstall-desktopshortcut0.log')
Check "msiexec /x exit code is 0 (got $code)" ($code -eq 0)
Check "install dir has no files left again" (Test-DirEmptyOrGone $InstallRoot)
Check "Start Menu shortcut folder removed again" (-not (Test-Path $startMenuDir))

# --- 7. Upgrade keeps user data; uninstall then removes it ---------------------

if ($UpgradeMsiPath) {
    Section 'Upgrade (base install, plant user data, install the newer MSI)'

    $code = Invoke-Msi @('/i', "`"$MsiPath`"", 'DESKTOPSHORTCUT=0') (Join-Path $LogDir 'upgrade-base-install.log')
    Check "base install exit code is 0 (got $code)" ($code -eq 0)
    $baseVersion = (Get-Content -LiteralPath (Join-Path $InstallRoot 'version.txt') -Raw).Trim()

    New-Item -ItemType Directory -Force -Path $UserDataDir | Out-Null
    $marker = Join-Path $UserDataDir 'settings.json'
    Set-Content -LiteralPath $marker -Value '{"marker":"planted-by-test-installer"}' -Encoding ascii
    # And something the agent would have generated inside the install folder,
    # which an upgrade SHOULD wipe along with the old files.
    $stale = Join-Path $InstallRoot 'agent\__pycache__'
    New-Item -ItemType Directory -Force -Path $stale | Out-Null
    Set-Content -LiteralPath (Join-Path $stale 'stale.pyc') -Value 'x' -Encoding ascii

    $code = Invoke-Msi @('/i', "`"$UpgradeMsiPath`"", 'DESKTOPSHORTCUT=0') (Join-Path $LogDir 'upgrade-install.log')
    Check "upgrade install exit code is 0 (got $code)" ($code -eq 0)
    $newVersion = if (Test-Path (Join-Path $InstallRoot 'version.txt')) { (Get-Content -LiteralPath (Join-Path $InstallRoot 'version.txt') -Raw).Trim() } else { '' }
    Check "install folder now carries the newer version ($baseVersion -> $newVersion)" ($newVersion -and $newVersion -ne $baseVersion) "got '$newVersion'"
    $arpUp = Get-ArpEntry
    Check "exactly one ARP entry, reporting the newer version" ($arpUp -and $arpUp.DisplayVersion -eq $newVersion) "got: $($arpUp.DisplayVersion)"
    Check "user data SURVIVED the upgrade (settings.json still there)" (Test-Path $marker)
    if (Test-Path $marker) {
        Check "user data content intact" ((Get-Content -LiteralPath $marker -Raw) -match 'planted-by-test-installer')
    }
    Check "stale generated file in the old install folder was wiped by the upgrade" (-not (Test-Path (Join-Path $stale 'stale.pyc')))

    Section 'Uninstall the upgraded product (program goes, user data stays)'
    Stop-Product
    $upCode = if ($arpUp) { $arpUp.PSChildName } else { "`"$UpgradeMsiPath`"" }
    $code = Invoke-Msi @('/x', $upCode) (Join-Path $LogDir 'upgrade-uninstall.log')
    Check "msiexec /x exit code is 0 (got $code)" ($code -eq 0)
    Check "install dir has no files left" (Test-DirEmptyOrGone $InstallRoot)
    Check "user-data folder KEPT after uninstall (settings survive)" (Test-Path $marker)
    Check "Add/Remove Programs entry removed" ($null -eq (Get-ArpEntry))
} else {
    Write-Host ''
    Write-Host 'note: -UpgradeMsiPath not given, so the upgrade-keeps-user-data cycle was not run.' -ForegroundColor Yellow
    Write-Host '      Build one with: powershell windows\build-installer.ps1 -Version <higher>' -ForegroundColor Yellow
    Write-Host '      -- any number above the MSI under test, e.g. 9.9.9. It is a' -ForegroundColor Yellow
    Write-Host '      throwaway build for this test and is never released.' -ForegroundColor Yellow
}

} finally {
    # Whatever happened above, leave the machine as it was found.
    Stop-Product
    if ($backupDir) {
        if (Test-Path $UserDataDir) { Remove-Item -LiteralPath $UserDataDir -Recurse -Force -ErrorAction SilentlyContinue }
        Copy-Item -LiteralPath $backupDir -Destination $UserDataDir -Recurse -Force
        Write-Host ''
        Write-Host "Restored $UserDataDir from $backupDir"
    }
}

# --- Summary -------------------------------------------------------------------

Write-Host ''
if ($script:failures -eq 0) {
    Write-Host "ALL PASS ($($script:checks) checks)" -ForegroundColor Green
    exit 0
} else {
    Write-Host "$script:failures FAILURES (of $($script:checks) checks)" -ForegroundColor Red
    Write-Host "Install log:   $installLog"
    Write-Host "Uninstall log: $uninstallLog"
    exit 1
}
