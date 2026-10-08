<#
.SYNOPSIS
    Proves the FALLBACK installer -- windows\installer\install.ps1 and
    uninstall.ps1, used when the WiX MSI build is unavailable -- actually
    installs, re-installs, refuses, and uninstalls. Its companion
    windows\test-installer.ps1 drives the .msi only and never touches these
    two files.

.DESCRIPTION
    WHY THIS IS SAFE TO RUN, AND WHY IT NEEDS NO ADMINISTRATOR RIGHTS

    install.ps1 and uninstall.ps1 write to exactly four places: C:\Program
    Files\Drobo Dashboard REBORN, the all-users Start Menu, the all-users
    Desktop, and an Add/Remove Programs key under HKLM. All four are
    machine-wide, all four need elevation, and the uninstaller's whole job is
    deleting a Program Files tree -- which is precisely the code nobody wants
    to meet for the first time with real rights and a real install on the
    other end of it.

    So those four places are now parameters of both scripts, with defaults
    that are the literal expressions they always used. A run with no
    parameters is the install it has always been: the elevation check still
    fires, and the uninstaller still asks Windows to relaunch it elevated.
    This harness is the only caller that passes them, and it passes a
    temporary folder and a scratch key under HKCU. That means:

      * nothing is ever written to Program Files, the real Start Menu, the
        real Desktop, or HKLM -- the scripts are never even told those paths;
      * no elevation is needed, and none is asked for: both scripts gate
        their elevation handling on "were my targets left at their defaults",
        so a redirected run can never raise a UAC prompt -- and a redirect
        has to name all four, so that gate can never be switched off for a
        target that is still the real machine-wide one;
      * the elevation checks themselves are therefore verified by INSPECTION
        (reading the parsed script, below), never by triggering one.

    As a belt-and-braces check, this script records whether the four real
    locations exist before it starts and asserts they are untouched at the
    end.

    What it covers:
      1. Inspection: both elevation checks exist, the uninstaller relaunches
         itself at most once, both gate that on default targets, and the
         uninstaller's four targets are spelled identically to the
         installer's (they silently disagreed until 2026-09-16).
      2. A partial redirect (one target given, three left real) is refused by
         both scripts, so the elevation guard can never be switched off for
         places that are still the real ones.
      3. Fresh install: payload copied, Start Menu folder with its three
         shortcuts, Desktop shortcut, Add/Remove Programs entry, and
         install.ps1 itself NOT copied into the install folder.
      4. Re-running the installer over an existing install: stale files from
         the old install are gone, the payload is whole, the version moves.
      5. The uninstaller refuses and removes NOTHING while a process is
         running from the install folder. A copy of Windows' own waitfor.exe
         is started inside the temp install folder for this (a console
         program that simply waits; never the app, never a window) and is
         stopped by PID afterwards.
      6. The uninstaller removes all four and KEEPS the user-data folder.

    Every check uses the same PASS/FAIL style as windows\test-installer.ps1
    and the Python suites under agent\. Exits 1 if anything failed, 0 if
    everything passed.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File windows/test-fallback-installer.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File windows/test-fallback-installer.ps1 -KeepWorkDir
#>
[CmdletBinding()]
param(
    # Where the pretend install goes. Defaults to a fresh folder under %TEMP%.
    [string]$WorkDir,
    # Leave the temp folder behind for inspection. A failing run keeps it anyway.
    [switch]$KeepWorkDir
)

$ErrorActionPreference = 'Stop'

$InstallerDir = Join-Path $PSScriptRoot 'installer'
$InstallScript = Join-Path $InstallerDir 'install.ps1'
$UninstallScript = Join-Path $InstallerDir 'uninstall.ps1'

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

# --- 0. Preflight ---------------------------------------------------------

Section 'Preflight'

foreach ($s in @($InstallScript, $UninstallScript)) {
    if (-not (Test-Path -LiteralPath $s)) {
        Write-Host "FAIL  Not found: $s" -ForegroundColor Red
        exit 1
    }
}

if (-not $WorkDir) {
    $WorkDir = Join-Path ([IO.Path]::GetTempPath()) ('drobo-fallback-installer-test-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
if (Test-Path -LiteralPath $WorkDir) {
    Write-Host "FAIL  Work folder already exists: $WorkDir" -ForegroundColor Red
    exit 1
}

# The pretend machine. Every path the two scripts are given lives under here.
$PayloadDir = Join-Path $WorkDir 'payload'
$InstallDir = Join-Path $WorkDir 'prefix\Program Files\Drobo Dashboard REBORN'
$StartMenuDir = Join-Path $WorkDir 'prefix\ProgramData\Start Menu\Programs\Drobo Dashboard REBORN'
$DesktopLink = Join-Path $WorkDir 'prefix\Public Desktop\Drobo Dashboard REBORN.lnk'
$UserDataDir = Join-Path $WorkDir 'prefix\LocalAppData\DroboDashboardReborn'
$LogDir = Join-Path $WorkDir 'logs'
# A scratch key in THIS user's own hive. Writable without elevation, nowhere
# near the real Add/Remove Programs list, and deleted at the end.
$TestRegRoot = 'HKCU:\Software\DroboDashboardRebornFallbackInstallerTest'
$UninstallKey = "$TestRegRoot\Uninstall\DroboDashboardReborn"

# The four real places, recorded now and re-checked at the end. This harness
# must leave every one of them exactly as it found it.
$RealInstallDir = Join-Path $env:ProgramFiles 'Drobo Dashboard REBORN'
$RealStartMenuDir = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Drobo Dashboard REBORN'
$RealDesktopLink = Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'Drobo Dashboard REBORN.lnk'
$RealUninstallKey = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\DroboDashboardReborn'
$realBefore = @{}
foreach ($r in @($RealInstallDir, $RealStartMenuDir, $RealDesktopLink, $RealUninstallKey)) {
    $realBefore[$r] = Test-Path -LiteralPath $r
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path $PayloadDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $InstallDir) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $StartMenuDir) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DesktopLink) | Out-Null
if (Test-Path -LiteralPath $TestRegRoot) { Remove-Item -LiteralPath $TestRegRoot -Recurse -Force }

Write-Host "Work folder: $WorkDir"
Write-Host "Scratch registry key: $TestRegRoot"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host "Running elevated: $elevated (this test does not need it, and asks for none)"

# Stand in for the extracted installer package: install.ps1 works out the
# payload from its OWN folder, so both scripts are copied in beside a payload
# shaped like the one build-installer.ps1 zips up.
Copy-Item -LiteralPath $InstallScript -Destination $PayloadDir -Force
Copy-Item -LiteralPath $UninstallScript -Destination $PayloadDir -Force
foreach ($name in @('Launch-DroboDashboardReborn.bat', 'Run-Agent.bat', 'Check-ForUpdates.bat', 'TESTING.md')) {
    $src = Join-Path $InstallerDir $name
    if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $PayloadDir -Force }
}
# Stand-ins for the big binaries, so nothing here is ever executed. The
# installer only ever copies them and names the .exe as a shortcut icon.
Set-Content -LiteralPath (Join-Path $PayloadDir 'DroboDashboardReborn.exe') -Value 'placeholder, not a program' -Encoding ascii
Set-Content -LiteralPath (Join-Path $PayloadDir 'version.txt') -Value '9.9.9-test' -Encoding ascii
New-Item -ItemType Directory -Force -Path (Join-Path $PayloadDir 'agent') | Out-Null
Set-Content -LiteralPath (Join-Path $PayloadDir 'agent\run_agent.py') -Value '# placeholder' -Encoding ascii
New-Item -ItemType Directory -Force -Path (Join-Path $PayloadDir 'sdk\drobo_nasd') | Out-Null
Set-Content -LiteralPath (Join-Path $PayloadDir 'sdk\drobo_nasd\__init__.py') -Value '# placeholder' -Encoding ascii
New-Item -ItemType Directory -Force -Path (Join-Path $PayloadDir 'python') | Out-Null
Set-Content -LiteralPath (Join-Path $PayloadDir 'python\python.exe') -Value 'placeholder, not a program' -Encoding ascii

$PayloadInstall = Join-Path $PayloadDir 'install.ps1'
$InstalledUninstall = Join-Path $InstallDir 'uninstall.ps1'

# An empty file, fed to every child process as stdin. Both scripts end some
# paths with Read-Host ("Press Enter to close"); reading straight to
# end-of-file means a non-interactive run can never hang on one.
$EmptyStdin = Join-Path $WorkDir 'empty-stdin.txt'
[IO.File]::WriteAllText($EmptyStdin, '')

# The same host this is running in, so -File means what it means here.
$PsExe = Join-Path $PSHOME 'powershell.exe'
if (-not (Test-Path -LiteralPath $PsExe)) { $PsExe = Join-Path $PSHOME 'pwsh.exe' }

# Quoted individually: Start-Process joins an argument array with spaces and
# adds no quotes of its own, and every one of these paths can contain them.
function Q([string]$s) { return '"' + $s + '"' }

$TargetArgs = @(
    '-InstallDir', (Q $InstallDir),
    '-StartMenuDir', (Q $StartMenuDir),
    '-DesktopLink', (Q $DesktopLink),
    '-UninstallKey', (Q $UninstallKey)
)

$script:lastOutput = ''
function Invoke-InstallerScript {
    param(
        [string]$Script,
        [string[]]$ScriptArgs,
        [string]$LogName,
        [string]$FromDir
    )
    $out = Join-Path $LogDir "$LogName.out.txt"
    $err = Join-Path $LogDir "$LogName.err.txt"
    $psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Q $Script)) + $ScriptArgs
    $proc = Start-Process -FilePath $PsExe -ArgumentList $psArgs -Wait -PassThru -NoNewWindow `
        -WorkingDirectory $FromDir -RedirectStandardOutput $out -RedirectStandardError $err `
        -RedirectStandardInput $EmptyStdin
    $text = ''
    foreach ($f in @($out, $err)) {
        if (Test-Path -LiteralPath $f) { $text += (Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue) }
    }
    $script:lastOutput = $text
    return $proc.ExitCode
}

function Get-Shortcut([string]$Lnk) {
    $sh = New-Object -ComObject WScript.Shell
    return $sh.CreateShortcut($Lnk)
}

$holder = $null

try {

# --- 1. Inspect the scripts (no elevation asked for, none triggered) ------

Section 'Inspect both scripts (elevation checks, by reading them)'

$installAst = [System.Management.Automation.Language.Parser]::ParseFile($InstallScript, [ref]$null, [ref]$null)
$uninstallAst = [System.Management.Automation.Language.Parser]::ParseFile($UninstallScript, [ref]$null, [ref]$null)

function Get-ParamDefaults($Ast) {
    $d = @{}
    if ($Ast.ParamBlock) {
        foreach ($p in $Ast.ParamBlock.Parameters) {
            $name = $p.Name.VariablePath.UserPath
            if ($p.DefaultValue) { $d[$name] = $p.DefaultValue.Extent.Text } else { $d[$name] = '' }
        }
    }
    return $d
}

function Find-RoleChecks($Ast) {
    return $Ast.FindAll({
        param($n)
        $n -is [System.Management.Automation.Language.InvokeMemberExpressionAst] -and "$($n.Member)" -eq 'IsInRole'
    }, $true)
}

# Walks up from a node looking for an "if" whose condition mentions
# $RedirectedTargets -- i.e. "this only happens when the targets are the real,
# machine-wide ones". That guard is what makes a redirected run prompt-free.
function Test-GuardedByRedirectedTargets($Node) {
    $n = $Node.Parent
    while ($n) {
        if ($n -is [System.Management.Automation.Language.IfStatementAst]) {
            foreach ($clause in $n.Clauses) {
                if ($clause.Item1.Extent.Text -match 'RedirectedTargets') { return $true }
            }
        }
        $n = $n.Parent
    }
    return $false
}

$installRoles = @(Find-RoleChecks $installAst)
$uninstallRoles = @(Find-RoleChecks $uninstallAst)
Check 'install.ps1 checks for administrator rights' ($installRoles.Count -ge 1)
Check 'uninstall.ps1 checks for administrator rights' ($uninstallRoles.Count -ge 1)

$installGuarded = ($installRoles.Count -ge 1)
foreach ($r in $installRoles) { if (-not (Test-GuardedByRedirectedTargets $r)) { $installGuarded = $false } }
Check 'install.ps1 only applies it to the default machine-wide targets' $installGuarded
$uninstallGuarded = ($uninstallRoles.Count -ge 1)
foreach ($r in $uninstallRoles) { if (-not (Test-GuardedByRedirectedTargets $r)) { $uninstallGuarded = $false } }
Check 'uninstall.ps1 only applies it to the default machine-wide targets' $uninstallGuarded

$installText = Get-Content -LiteralPath $InstallScript -Raw
$uninstallText = Get-Content -LiteralPath $UninstallScript -Raw
Check 'install.ps1 stops with a message instead of failing halfway' `
    (($installText -match 'needs administrator rights') -and ($installText -match '(?m)^\s+exit 1'))

$runAs = @($uninstallAst.FindAll({
    param($n)
    $n -is [System.Management.Automation.Language.CommandAst] -and $n.Extent.Text -match '-Verb\s+RunAs'
}, $true))
Check 'uninstall.ps1 asks Windows to relaunch it elevated (-Verb RunAs)' ($runAs.Count -eq 1)
$runAsGuarded = ($runAs.Count -ge 1)
foreach ($r in $runAs) { if (-not (Test-GuardedByRedirectedTargets $r)) { $runAsGuarded = $false } }
Check 'that relaunch cannot fire on a redirected run, so this test never prompts' $runAsGuarded
Check 'uninstall.ps1 relaunches itself at most once (-Relaunched guard)' `
    (($uninstallText -match '\[switch\]\$Relaunched') -and ($uninstallText -match 'if \(\$Relaunched\)'))

# The 2026-09-16 bug in one check: the uninstaller named the per-user places
# while the installer named the machine-wide ones, so an uninstall left the
# shortcuts and the Add/Remove Programs entry behind.
$instDefaults = Get-ParamDefaults $installAst
$uninstDefaults = Get-ParamDefaults $uninstallAst
foreach ($name in @('InstallDir', 'StartMenuDir', 'DesktopLink', 'UninstallKey')) {
    $a = [string]$instDefaults[$name]
    $b = [string]$uninstDefaults[$name]
    Check "$name default is the same in both scripts" (($a -ne '') -and ($a -eq $b)) "install: '$a' / uninstall: '$b'"
}
Check 'InstallDir defaults to Program Files' `
    (([string]$instDefaults['InstallDir'] -match '\$env:ProgramFiles') -and ([string]$instDefaults['InstallDir'] -match 'Drobo Dashboard REBORN'))
Check 'StartMenuDir defaults to the ALL-USERS Start Menu' `
    (([string]$instDefaults['StartMenuDir'] -match '\$env:ProgramData') -and ([string]$instDefaults['StartMenuDir'] -match 'Start Menu\\Programs'))
Check 'DesktopLink defaults to the ALL-USERS Desktop' ([string]$instDefaults['DesktopLink'] -match 'CommonDesktopDirectory')
Check 'UninstallKey defaults to the HKLM Add/Remove Programs key' `
    ([string]$instDefaults['UninstallKey'] -match 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\DroboDashboardReborn')

# --- 2. A partial redirect is refused --------------------------------------

Section 'A partial redirect is refused, so the real places are never half-used'

# Redirecting a target is what switches the elevation handling off. While a
# partial redirect was allowed, redirecting ONE left the other three at their
# real machine-wide defaults with that guard already disabled: from an
# elevated shell, `uninstall.ps1 -InstallDir D:\somewhere` would have deleted
# the real all-users Start Menu folder, the real Public Desktop shortcut and
# the real HKLM Uninstall key. Both scripts now take all four together or
# refuse. Driven here with only -InstallDir, aimed at a folder under this
# test's own work directory, and run BEFORE the fresh install below so there
# is nothing yet for a mistake to hide behind.
$PartialDir = Join-Path $WorkDir 'partial-redirect-never-created'
$PayloadUninstall = Join-Path $PayloadDir 'uninstall.ps1'
foreach ($s in @(
        @{ Name = 'install.ps1'; Path = $PayloadInstall; Log = 'partial-install' },
        @{ Name = 'uninstall.ps1'; Path = $PayloadUninstall; Log = 'partial-uninstall' })) {
    $code = Invoke-InstallerScript -Script $s.Path -ScriptArgs @('-InstallDir', (Q $PartialDir)) `
        -LogName $s.Log -FromDir $PayloadDir
    Check "$($s.Name) refuses a partial redirect (exit 1, got $code)" ($code -eq 1) $script:lastOutput
    Check "$($s.Name) names all four targets in the refusal" `
        ($script:lastOutput -match 'InstallDir.*StartMenuDir.*DesktopLink.*UninstallKey') $script:lastOutput
}
Check 'the partly-redirected runs created nothing' (-not (Test-Path -LiteralPath $PartialDir))

# --- 3. Fresh install -----------------------------------------------------

Section 'Fresh install into the temp prefix'

$code = Invoke-InstallerScript -Script $PayloadInstall -ScriptArgs $TargetArgs -LogName 'install-1' -FromDir $PayloadDir
Check "install.ps1 exit code is 0 (got $code)" ($code -eq 0) $script:lastOutput

Check "install dir created ($InstallDir)" (Test-Path -LiteralPath $InstallDir)
foreach ($rel in @('Launch-DroboDashboardReborn.bat', 'Run-Agent.bat', 'Check-ForUpdates.bat', 'TESTING.md',
                   'version.txt', 'DroboDashboardReborn.exe', 'agent\run_agent.py',
                   'sdk\drobo_nasd\__init__.py', 'python\python.exe')) {
    Check "payload laid down: $rel" (Test-Path -LiteralPath (Join-Path $InstallDir $rel))
}
Check 'uninstall.ps1 copied into the install folder (it must outlive the download)' (Test-Path -LiteralPath $InstalledUninstall)
Check 'install.ps1 itself was NOT copied into the install folder' (-not (Test-Path -LiteralPath (Join-Path $InstallDir 'install.ps1')))

Check 'Start Menu folder created' (Test-Path -LiteralPath $StartMenuDir)
$mainLnk = Join-Path $StartMenuDir 'Drobo Dashboard REBORN.lnk'
$updateLnk = Join-Path $StartMenuDir 'Check for Updates.lnk'
$uninstallLnk = Join-Path $StartMenuDir 'Uninstall Drobo Dashboard REBORN.lnk'
Check 'Start Menu: launch shortcut' (Test-Path -LiteralPath $mainLnk)
Check 'Start Menu: Check for Updates shortcut' (Test-Path -LiteralPath $updateLnk)
Check 'Start Menu: Uninstall shortcut' (Test-Path -LiteralPath $uninstallLnk)
Check 'Desktop shortcut created' (Test-Path -LiteralPath $DesktopLink)

if (Test-Path -LiteralPath $mainLnk) {
    $sc = Get-Shortcut $mainLnk
    Check 'launch shortcut runs the launcher inside the install folder' `
        ($sc.TargetPath -eq (Join-Path $InstallDir 'Launch-DroboDashboardReborn.bat')) $sc.TargetPath
    Check 'launch shortcut starts in the install folder' ($sc.WorkingDirectory -eq $InstallDir) $sc.WorkingDirectory
}
if (Test-Path -LiteralPath $DesktopLink) {
    $sd = Get-Shortcut $DesktopLink
    Check 'desktop shortcut runs the same launcher' `
        ($sd.TargetPath -eq (Join-Path $InstallDir 'Launch-DroboDashboardReborn.bat')) $sd.TargetPath
}
if (Test-Path -LiteralPath $uninstallLnk) {
    $su = Get-Shortcut $uninstallLnk
    Check 'Uninstall shortcut runs the COPY of uninstall.ps1 inside the install folder' `
        ($su.Arguments -match [regex]::Escape($InstalledUninstall)) $su.Arguments
}

Check 'Add/Remove Programs entry created' (Test-Path -LiteralPath $UninstallKey)
if (Test-Path -LiteralPath $UninstallKey) {
    $arp = Get-ItemProperty -LiteralPath $UninstallKey
    Check 'ARP DisplayName' ($arp.DisplayName -eq 'Drobo Dashboard REBORN') $arp.DisplayName
    Check 'ARP DisplayVersion comes from version.txt (9.9.9-test)' ($arp.DisplayVersion -eq '9.9.9-test') $arp.DisplayVersion
    Check 'ARP InstallLocation points at the install folder' ($arp.InstallLocation -eq $InstallDir) $arp.InstallLocation
    Check 'ARP UninstallString runs the installed uninstall.ps1' `
        ($arp.UninstallString -match [regex]::Escape($InstalledUninstall)) $arp.UninstallString
    Check 'ARP QuietUninstallString passes -Quiet' ($arp.QuietUninstallString -match '-Quiet') $arp.QuietUninstallString
    Check 'ARP NoModify / NoRepair set' (($arp.NoModify -eq 1) -and ($arp.NoRepair -eq 1))
    Check 'ARP EstimatedSize computed' ($null -ne $arp.EstimatedSize)
}

# --- 4. Re-run the installer over the existing install --------------------

Section 'Re-run the installer over an existing install'

$stale = Join-Path $InstallDir 'agent\__pycache__'
New-Item -ItemType Directory -Force -Path $stale | Out-Null
Set-Content -LiteralPath (Join-Path $stale 'stale.pyc') -Value 'x' -Encoding ascii
Set-Content -LiteralPath (Join-Path $PayloadDir 'version.txt') -Value '9.9.10-test' -Encoding ascii

$code = Invoke-InstallerScript -Script $PayloadInstall -ScriptArgs $TargetArgs -LogName 'install-2' -FromDir $PayloadDir
Check "re-install exit code is 0 (got $code)" ($code -eq 0) $script:lastOutput
Check 'stale file from the previous install was wiped' (-not (Test-Path -LiteralPath (Join-Path $stale 'stale.pyc')))
Check 'payload is whole again after the re-install' (Test-Path -LiteralPath (Join-Path $InstallDir 'Run-Agent.bat'))
Check 'uninstall.ps1 still in the install folder' (Test-Path -LiteralPath $InstalledUninstall)
Check 'Start Menu shortcuts still there' (Test-Path -LiteralPath $mainLnk)
Check 'Desktop shortcut still there' (Test-Path -LiteralPath $DesktopLink)
if (Test-Path -LiteralPath $UninstallKey) {
    $arp2 = Get-ItemProperty -LiteralPath $UninstallKey
    Check 'ARP version moved to the re-installed version (9.9.10-test)' ($arp2.DisplayVersion -eq '9.9.10-test') $arp2.DisplayVersion
}
Check 'still exactly one Add/Remove Programs entry' (@(Get-ChildItem -LiteralPath "$TestRegRoot\Uninstall" -ErrorAction SilentlyContinue).Count -eq 1)

# --- 5. The uninstaller refuses while something runs from the folder ------

Section 'Uninstall refuses, and removes NOTHING, while a process runs from the install folder'

# A copy of Windows' own waitfor.exe: a console program that does nothing but
# wait for a signal it will never get, then time out. Never the app, never a
# window, and stopped by PID below. Only its LOCATION matters to the check
# under test, which looks for any process whose image sits inside the install
# folder.
$holderExe = Join-Path $InstallDir 'test-holds-the-folder-open.exe'
$waitfor = Join-Path $env:SystemRoot 'System32\waitfor.exe'
$holderStarted = $false
if (Test-Path -LiteralPath $waitfor) {
    Copy-Item -LiteralPath $waitfor -Destination $holderExe -Force
    $holder = Start-Process -FilePath $holderExe -ArgumentList '/t', '120', 'DroboFallbackInstallerTest' `
        -PassThru -NoNewWindow -WorkingDirectory $InstallDir `
        -RedirectStandardOutput (Join-Path $LogDir 'holder.out.txt') `
        -RedirectStandardError (Join-Path $LogDir 'holder.err.txt') `
        -RedirectStandardInput $EmptyStdin
    Start-Sleep -Milliseconds 800
    $live = Get-Process -Id $holder.Id -ErrorAction SilentlyContinue
    $holderStarted = ($null -ne $live) -and ($null -ne $live.Path) -and $live.Path.StartsWith($InstallDir, [StringComparison]::OrdinalIgnoreCase)
}
Check 'a console process is running from inside the temp install folder' $holderStarted

if ($holderStarted) {
    $code = Invoke-InstallerScript -Script $InstalledUninstall -ScriptArgs ($TargetArgs + @('-UserDataDir', (Q $UserDataDir), '-Quiet')) `
        -LogName 'uninstall-refused' -FromDir $InstallDir
    Check "uninstall.ps1 exit code is 1 (got $code)" ($code -eq 1) $script:lastOutput
    Check 'it says why, naming the still-open program' ($script:lastOutput -match 'still open') $script:lastOutput
    Check 'NOTHING removed: install folder intact' (Test-Path -LiteralPath (Join-Path $InstallDir 'Run-Agent.bat'))
    Check 'NOTHING removed: Start Menu folder intact' (Test-Path -LiteralPath $mainLnk)
    Check 'NOTHING removed: Desktop shortcut intact' (Test-Path -LiteralPath $DesktopLink)
    Check 'NOTHING removed: Add/Remove Programs entry intact' (Test-Path -LiteralPath $UninstallKey)

    Stop-Process -Id $holder.Id -Force -ErrorAction SilentlyContinue
    $holder.WaitForExit(10000) | Out-Null
    $holder = $null
    Start-Sleep -Milliseconds 500
}

# --- 6. Uninstall removes all four and keeps the user data ----------------

Section 'Uninstall removes all four, and keeps the user-data folder'

New-Item -ItemType Directory -Force -Path $UserDataDir | Out-Null
$marker = Join-Path $UserDataDir 'settings.json'
Set-Content -LiteralPath $marker -Value '{"marker":"planted-by-test-fallback-installer"}' -Encoding ascii

$code = Invoke-InstallerScript -Script $InstalledUninstall -ScriptArgs ($TargetArgs + @('-UserDataDir', (Q $UserDataDir))) `
    -LogName 'uninstall' -FromDir $InstallDir
Check "uninstall.ps1 exit code is 0 (got $code)" ($code -eq 0) $script:lastOutput

Check 'Start Menu folder removed' (-not (Test-Path -LiteralPath $StartMenuDir))
Check 'Desktop shortcut removed' (-not (Test-Path -LiteralPath $DesktopLink))
Check 'Add/Remove Programs entry removed' (-not (Test-Path -LiteralPath $UninstallKey))

# The install folder goes last, from a helper the uninstaller leaves behind:
# a script cannot delete the folder it is running out of while PowerShell
# still holds the file open. The helper retries for about twenty seconds, so
# wait for it rather than checking once.
$deadline = (Get-Date).AddSeconds(40)
while ((Test-Path -LiteralPath $InstallDir) -and ((Get-Date) -lt $deadline)) { Start-Sleep -Milliseconds 500 }
$leftOver = @(Get-ChildItem -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
Check 'install folder removed (by the deferred cleanup the uninstaller leaves behind)' `
    (-not (Test-Path -LiteralPath $InstallDir)) ($leftOver -join ', ')

Check 'user-data folder KEPT' (Test-Path -LiteralPath $UserDataDir)
Check 'user-data content intact' `
    ((Test-Path -LiteralPath $marker) -and ((Get-Content -LiteralPath $marker -Raw) -match 'planted-by-test-fallback-installer'))
Check 'it says where the kept settings are' ($script:lastOutput -match 'settings were kept') $script:lastOutput

# --- 7. The real machine was never touched --------------------------------

Section 'The four real locations are exactly as they were found'

foreach ($r in @($RealInstallDir, $RealStartMenuDir, $RealDesktopLink, $RealUninstallKey)) {
    $now = Test-Path -LiteralPath $r
    Check "unchanged: $r" ($now -eq $realBefore[$r]) "was $($realBefore[$r]), now $now"
}

} finally {
    if ($holder) {
        Stop-Process -Id $holder.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
    if (Test-Path -LiteralPath $TestRegRoot) {
        Remove-Item -LiteralPath $TestRegRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (($script:failures -eq 0) -and (-not $KeepWorkDir)) {
        # The uninstaller's deferred helper may still have the folder open.
        for ($i = 0; $i -lt 10 -and (Test-Path -LiteralPath $WorkDir); $i++) {
            Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
            if (Test-Path -LiteralPath $WorkDir) { Start-Sleep -Milliseconds 500 }
        }
    } else {
        Write-Host ''
        Write-Host "Work folder kept for inspection: $WorkDir" -ForegroundColor Yellow
    }
}

# --- Summary --------------------------------------------------------------

Write-Host ''
if ($script:failures -eq 0) {
    Write-Host "ALL PASS ($($script:checks) checks)" -ForegroundColor Green
    exit 0
} else {
    Write-Host "$script:failures FAILURES (of $($script:checks) checks)" -ForegroundColor Red
    exit 1
}
