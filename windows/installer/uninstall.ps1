<#
.SYNOPSIS
    Removes a Drobo Dashboard REBORN install made by install.ps1 (the
    fallback, non-MSI installer).

.DESCRIPTION
    Mirrors install.ps1 exactly in reverse: deletes the install directory in
    Program Files, the all-users Start Menu folder, the all-users Desktop
    shortcut, and the Add/Remove Programs registry key under HKLM. This file
    is copied into the install directory at install time and is what the
    "Uninstall" shortcut and the ARP entry both call -- it does not run from
    the original download location, so it works even if that was a temp
    folder that's since been cleaned up.

    Needs administrator rights, like install.ps1 (both became per-machine on
    2026-09-08). Neither the Start Menu shortcut nor Settings > Apps starts it
    elevated, so without those rights it asks Windows to run it again as
    administrator, passing -Quiet on, and leaves the removal to that copy.
    It relaunches itself at most once: if the relaunched copy still has no
    administrator rights, it says so and stops, removing nothing.

    While the app or its agent is still running from the install folder, it
    also says so and stops, removing nothing, so it can simply be run again
    once they are closed.

    The places it removes are parameters for one reason only: so that
    windows\test-fallback-installer.ps1 can aim them at a temporary folder and
    exercise this file end to end without administrator rights, without any
    "Run as administrator" prompt, and without putting a single real system
    location within reach of a Remove-Item. Nothing that ships passes them.
    Their defaults are the machine-wide places install.ps1 creates, spelled
    exactly as it spells them, so a normal run -- no target parameters --
    does precisely what it did before.
#>
param(
    [switch]$Quiet,
    # Set only by the relaunch below, never by hand. A copy started with it
    # that still lacks administrator rights stops instead of relaunching
    # again, so this script can never start itself more than once. That
    # happens where Windows runs a "Run as administrator" request without
    # raising the rights, e.g. a standard account with UAC switched off.
    [switch]$Relaunched,
    # The same four places install.ps1 creates, spelled exactly as it spells
    # them. They must stay identical: until 2026-09-16 these still named the
    # per-user places (%APPDATA% Start Menu, the user's own Desktop, HKCU)
    # from before the installer went per-machine, so an uninstall left the
    # all-users shortcuts and the Add/Remove Programs entry behind.
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'Drobo Dashboard REBORN'),
    [string]$StartMenuDir = (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Drobo Dashboard REBORN'),
    [string]$DesktopLink = (Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'Drobo Dashboard REBORN.lnk'),
    [string]$UninstallKey = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\DroboDashboardReborn',
    # Only ever read, never removed -- it is named in the closing message so
    # the user knows where the kept settings are.
    [string]$UserDataDir = (Join-Path $env:LOCALAPPDATA 'DroboDashboardReborn')
)

$ErrorActionPreference = 'Stop'

# True only when this was aimed somewhere other than the four machine-wide
# places above. It gates the elevation handling below: a run left at its
# defaults is about to change Program Files, the all-users Start Menu and
# Desktop, and HKLM, and needs administrator rights to do it; a run that was
# told exactly where to write needs whatever rights that place needs instead,
# and must never raise a "Run as administrator" prompt for it.
$RedirectedTargets = @('InstallDir', 'StartMenuDir', 'DesktopLink', 'UninstallKey') |
    Where-Object { $PSBoundParameters.ContainsKey($_) }

# All four or none. Redirecting a target is what switches the elevation
# handling below off, so a run that redirects only some of them keeps the
# real machine-wide defaults for the rest with that handling already
# disabled: `-InstallDir D:\somewhere` from an elevated shell would have gone
# straight on to delete the real all-users Start Menu folder, the real Public
# Desktop shortcut and the real HKLM Uninstall key. Nothing that ships passes
# any of these, and the test harness passes all four together, so refusing
# the in-between costs nothing and closes it.
if ($RedirectedTargets -and @($RedirectedTargets).Count -ne 4) {
    Write-Host ''
    Write-Host 'Redirecting one target redirects all four: pass -InstallDir, -StartMenuDir, -DesktopLink and -UninstallKey together.' -ForegroundColor Yellow
    Write-Host ('Given only: ' + ($RedirectedTargets -join ', ')) -ForegroundColor Yellow
    Write-Host 'Nothing was removed.' -ForegroundColor Yellow
    Write-Host ''
    exit 1
}

# Program Files, the all-users Start Menu and Desktop, and HKLM all need
# administrator rights to change, so without them every removal below would
# fail. Rather than stop with a message, as install.ps1 does, run this same
# file again elevated: the Add/Remove Programs "Uninstall" button and the
# Start Menu shortcut both start it without rights, and would otherwise never
# work.
if (-not $RedirectedTargets) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        if ($Relaunched) {
            # This is already the relaunched copy, and it still has no rights.
            # Asking again would start the same copy again, endlessly.
            Write-Host ''
            Write-Host 'Removing Drobo Dashboard REBORN needs administrator rights, and this copy still does not have them.' -ForegroundColor Yellow
            Write-Host 'Nothing was removed. Start PowerShell with "Run as administrator" and run this again.' -ForegroundColor Yellow
            Write-Host ''
            # Both entry points run this in a window that closes as soon as the
            # script ends, so wait for Enter, or the message could not be read.
            if (-not $Quiet) { try { [void](Read-Host 'Press Enter to close') } catch { } }
            exit 1
        }
        # Start-Process joins these with spaces and adds no quotes of its own, and
        # this file's path contains "Program Files", so the path is quoted here.
        $elevatedArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$($MyInvocation.MyCommand.Path)`"", '-Relaunched')
        if ($Quiet) { $elevatedArgs += '-Quiet' }
        try {
            # Started outside the install folder, so the new copy is never
            # sitting in the folder it is about to delete.
            Start-Process -FilePath 'powershell.exe' -Verb RunAs -WorkingDirectory $env:SystemRoot -ArgumentList $elevatedArgs | Out-Null
        } catch {
            # The Windows prompt was refused, or could not be shown.
            Write-Host ''
            Write-Host 'Removing Drobo Dashboard REBORN needs administrator rights, and Windows did not grant them.' -ForegroundColor Yellow
            Write-Host 'Nothing was removed. Run the uninstall again and approve the prompt, or start' -ForegroundColor Yellow
            Write-Host 'PowerShell with "Run as administrator" and run this again.' -ForegroundColor Yellow
            Write-Host ''
            if (-not $Quiet) { try { [void](Read-Host 'Press Enter to close') } catch { } }
            exit 1
        }
        # The elevated copy does the removal. This one ends now instead of waiting
        # for it: started from the Start Menu shortcut, its working directory is
        # the install folder, and a process still sitting in that folder would
        # stop the folder being deleted.
        exit 0
    }
}

# Remove nothing while the app or the agent is still running from the install
# folder. Windows will not delete a running program's files, and the program
# files are deleted last and quietly, so the uninstall used to remove the
# shortcuts and the Add/Remove Programs entry, leave the program folder
# behind, and still report success, with nothing left to run the uninstall
# again from. That is the usual state, too: the Start Menu launcher leaves
# the agent (python\python.exe) running in a minimised window. So check first
# and stop before touching anything. Neither program is closed from here.
$stillRunning = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and $_.Path.StartsWith($InstallDir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)
})
if ($stillRunning.Count -gt 0) {
    Write-Host ''
    Write-Host 'Drobo Dashboard REBORN or its agent window is still open. Close them and run the uninstall again. Nothing was removed.' -ForegroundColor Yellow
    Write-Host ''
    # Started from the shortcut or Settings > Apps, the window closes as soon
    # as the script ends, so wait for Enter or the message could not be read.
    if (-not $Quiet) { try { [void](Read-Host 'Press Enter to close') } catch { } }
    exit 1
}

if (-not $Quiet) { Write-Host 'Removing Drobo Dashboard REBORN...' }

if (Test-Path $StartMenuDir) { Remove-Item -Recurse -Force $StartMenuDir }
if (Test-Path $DesktopLink) { Remove-Item -Force $DesktopLink }
if (Test-Path $UninstallKey) { Remove-Item -Recurse -Force $UninstallKey }
# The user's settings and the agent's config.json (with its access token)
# live in %LOCALAPPDATA%\DroboDashboardReborn and are deliberately KEPT, the
# same as the MSI: it is the agent's data, not the installer's, and keeping it
# means a reinstall restores the setup. Delete that folder by hand to erase
# the token (loopback-only, worthless once the agent is gone).
if (-not $Quiet) {
    if (Test-Path $UserDataDir) {
        Write-Host "Your settings were kept in $UserDataDir. Delete that folder to remove them."
    }
}

# Delete the install dir last, and from a copy of this logic running out of
# it -- self-deleting the running script's own folder on Windows fails on
# the last file (this .ps1 itself) while PowerShell still holds it open, so
# schedule that one removal to happen a moment after this process exits.
if (Test-Path $InstallDir) {
    $selfPath = $MyInvocation.MyCommand.Path
    $rest = Get-ChildItem -LiteralPath $InstallDir -Force |
        Where-Object { $_.FullName -ne $selfPath }
    foreach ($item in $rest) {
        Remove-Item -LiteralPath $item.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }

    # Keep trying for about twenty seconds instead of once after half a
    # second. Half a second is not always long enough for the PowerShell that
    # is exiting to let go of this file, and -ErrorAction SilentlyContinue
    # then swallowed the failure, leaving an empty "Drobo Dashboard REBORN"
    # folder behind while the uninstall reported success. The same wait also
    # covers the other holder: a leftover agent window still sitting in this
    # folder stops Windows deleting it until that window is closed. The loop
    # ends as soon as the folder is gone, so the usual case still takes one
    # pass.
    $cleanupCmd = "for (`$i = 0; `$i -lt 40 -and (Test-Path -LiteralPath '$InstallDir'); `$i++) { " +
        "Start-Sleep -Milliseconds 500; " +
        "Remove-Item -LiteralPath '$InstallDir' -Recurse -Force -ErrorAction SilentlyContinue }"
    # -WorkingDirectory: without it the helper starts in this script's current
    # folder, which is the install folder when the shortcut starts it with
    # rights already. Windows does not delete a folder that a running process
    # is using as its current folder, so the helper removed only the folder's
    # contents and left an empty "Drobo Dashboard REBORN" folder behind.
    Start-Process -WindowStyle Hidden -FilePath 'powershell.exe' -WorkingDirectory $env:SystemRoot `
        -ArgumentList @('-NoProfile', '-Command', $cleanupCmd) | Out-Null
}

if (-not $Quiet) {
    Write-Host 'Done. Drobo Dashboard REBORN has been removed.'
    # Same reason as the two messages that stop early: from the Start Menu
    # shortcut and from Settings > Apps this runs in a window that closes the
    # moment the script ends, and the line above about the kept settings
    # folder is the only place the access token's whereabouts is given.
    # -Quiet, which is what Settings > Apps uses for its quiet uninstall,
    # skips this, so nothing automated is left waiting.
    try { [void](Read-Host 'Press Enter to close') } catch { }
}
