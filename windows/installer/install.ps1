<#
.SYNOPSIS
    Fallback installer for Drobo Dashboard REBORN (used only if the WiX MSI
    build isn't available -- see windows/build-installer.ps1).

.DESCRIPTION
    Copies the already-published payload (this script's own folder) to
    "C:\Program Files\Drobo Dashboard REBORN", creates all-users Start Menu
    and Desktop shortcuts, and registers a real "Add/Remove Programs" entry
    under HKLM so it can be found and removed the normal Windows way.

    Needs administrator rights, and says so rather than failing halfway:
    it matches the MSI, which became a per-machine package on 2026-09-08.

    Run this from inside the extracted installer package, i.e. the folder
    that already contains DroboDashboardReborn.exe, agent\, and the other
    payload files alongside this script.

    The four places it writes are parameters for one reason only: so that
    windows\test-fallback-installer.ps1 can aim them at a temporary folder and
    exercise this file end to end without administrator rights and without
    touching a single real system location. Nothing that ships passes them.
    Their defaults are the machine-wide places this script has always used,
    spelled exactly as it always spelled them, so a normal run -- no
    parameters -- does precisely what it did before.
#>
param(
    # C:\Program Files\Drobo Dashboard REBORN: where the payload lands.
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'Drobo Dashboard REBORN'),
    # The all-users Start Menu folder holding the three shortcuts.
    [string]$StartMenuDir = (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Drobo Dashboard REBORN'),
    # The all-users Desktop shortcut.
    [string]$DesktopLink = (Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'Drobo Dashboard REBORN.lnk'),
    # The Add/Remove Programs key.
    [string]$UninstallKey = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\DroboDashboardReborn'
)
$ErrorActionPreference = 'Stop'

# The MSI installs per-machine, so this fallback must land in the same place
# under the same rules -- a fallback that quietly installs somewhere else is
# worse than no fallback, because the two copies then disagree about where the
# program lives and the uninstaller finds neither.
#
# All four of those places need administrator rights, so a run with everything
# left at its default says so here rather than failing halfway. A run that was
# handed different places has been told exactly where to write and needs
# whatever rights those places need instead, which is what lets the test
# harness drive this into a temp folder as an ordinary user.
$RedirectedTargets = @('InstallDir', 'StartMenuDir', 'DesktopLink', 'UninstallKey') |
    Where-Object { $PSBoundParameters.ContainsKey($_) }

# All four or none. Redirecting a target is what switches the elevation check
# above off, so a run that redirects only some of them keeps the real
# machine-wide defaults for the rest with that check already disabled, and
# writes into the real Program Files, the real all-users Start Menu and
# Desktop and the real HKLM key with nothing left guarding them. Nothing that
# ships passes any of these, and the test harness passes all four together,
# so refusing the in-between costs nothing and closes it.
if ($RedirectedTargets -and @($RedirectedTargets).Count -ne 4) {
    Write-Host ''
    Write-Host 'Redirecting one target redirects all four: pass -InstallDir, -StartMenuDir, -DesktopLink and -UninstallKey together.' -ForegroundColor Yellow
    Write-Host ('Given only: ' + ($RedirectedTargets -join ', ')) -ForegroundColor Yellow
    Write-Host 'Nothing was written.' -ForegroundColor Yellow
    Write-Host ''
    exit 1
}

if (-not $RedirectedTargets) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host ''
        Write-Host 'This installs into Program Files, which needs administrator rights.' -ForegroundColor Yellow
        Write-Host 'Start PowerShell with "Run as administrator" and run this again.' -ForegroundColor Yellow
        Write-Host ''
        exit 1
    }
}

$PayloadDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$versionPath = Join-Path $PayloadDir 'version.txt'
$version = if (Test-Path $versionPath) { (Get-Content $versionPath -Raw).Trim() } else { '0.0.0' }

Write-Host "Installing Drobo Dashboard REBORN $version to $InstallDir ..."

# --- Copy payload -----------------------------------------------------
# Re-runnable: wipe and re-copy rather than trying to diff, so upgrades and
# re-installs behave the same as a fresh install.
if (Test-Path $InstallDir) {
    Remove-Item -Recurse -Force $InstallDir
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

Get-ChildItem -LiteralPath $PayloadDir -Force |
    Where-Object { $_.Name -notin @('install.ps1') } |
    ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $InstallDir -Recurse -Force
    }

# uninstall.ps1 ships in the payload too, but copy it explicitly in case the
# Where-Object filter above ever changes -- the uninstall entry depends on
# it existing inside InstallDir, not the (temporary) payload dir.
Copy-Item -LiteralPath (Join-Path $PayloadDir 'uninstall.ps1') -Destination $InstallDir -Force

# --- Shortcuts ----------------------------------------------------------
New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
$shell = New-Object -ComObject WScript.Shell

$targetBat = Join-Path $InstallDir 'Launch-DroboDashboardReborn.bat'
$iconPath = Join-Path $InstallDir 'DroboDashboardReborn.exe'

foreach ($linkPath in @(
    (Join-Path $StartMenuDir 'Drobo Dashboard REBORN.lnk'),
    $DesktopLink
)) {
    $shortcut = $shell.CreateShortcut($linkPath)
    $shortcut.TargetPath = $targetBat
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.IconLocation = $iconPath
    $shortcut.Description = 'Start the Drobo agent and the dashboard'
    $shortcut.Save()
}

$updateShortcut = $shell.CreateShortcut((Join-Path $StartMenuDir 'Check for Updates.lnk'))
$updateShortcut.TargetPath = Join-Path $InstallDir 'Check-ForUpdates.bat'
$updateShortcut.WorkingDirectory = $InstallDir
$updateShortcut.Description = 'Check whether a newer Drobo Dashboard REBORN is available'
$updateShortcut.Save()

$uninstallShortcut = $shell.CreateShortcut((Join-Path $StartMenuDir 'Uninstall Drobo Dashboard REBORN.lnk'))
$uninstallShortcut.TargetPath = 'powershell.exe'
$uninstallShortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`""
$uninstallShortcut.WorkingDirectory = $InstallDir
$uninstallShortcut.Description = 'Remove Drobo Dashboard REBORN'
$uninstallShortcut.Save()

# --- Add/Remove Programs entry ------------------------------------------
New-Item -Path $UninstallKey -Force | Out-Null
Set-ItemProperty -Path $UninstallKey -Name 'DisplayName' -Value 'Drobo Dashboard REBORN'
Set-ItemProperty -Path $UninstallKey -Name 'DisplayVersion' -Value $version
Set-ItemProperty -Path $UninstallKey -Name 'Publisher' -Value 'Drobo Dashboard REBORN Project'
Set-ItemProperty -Path $UninstallKey -Name 'InstallLocation' -Value $InstallDir
Set-ItemProperty -Path $UninstallKey -Name 'DisplayIcon' -Value $iconPath
Set-ItemProperty -Path $UninstallKey -Name 'UninstallString' `
    -Value "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`""
Set-ItemProperty -Path $UninstallKey -Name 'QuietUninstallString' `
    -Value "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`" -Quiet"
Set-ItemProperty -Path $UninstallKey -Name 'NoModify' -Value 1 -Type DWord
Set-ItemProperty -Path $UninstallKey -Name 'NoRepair' -Value 1 -Type DWord
try {
    $sizeKb = [math]::Round(((Get-ChildItem $InstallDir -Recurse -File | Measure-Object Length -Sum).Sum) / 1KB)
    Set-ItemProperty -Path $UninstallKey -Name 'EstimatedSize' -Value $sizeKb -Type DWord
} catch {
    # Cosmetic field only -- if it can't be computed, Windows just shows no size.
}

Write-Host ''
Write-Host "Done. Drobo Dashboard REBORN $version is installed."
Write-Host "Launch it from the Start Menu or Desktop shortcut."
Write-Host "To remove it later: Settings > Apps, or the Start Menu 'Uninstall' shortcut."
