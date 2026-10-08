# Installer files

What the Windows installer (`.msi`) is built from, and the files copied into
every installed copy of the program. Testers: open [`TESTING.md`](TESTING.md)
first.

| File | What it is for |
|---|---|
| [`Package.wxs`](Package.wxs) | The recipe WiX turns into the `.msi` |
| [`python-embed.lock`](python-embed.lock) | Pins the exact Python version, and its checksum, bundled into the installer. The build stops without it |
| [`TESTING.md`](TESTING.md) | The testers' guide: installing, first run, reporting problems, removing it. Copied into every install |
| [`Launch-DroboDashboardReborn.bat`](Launch-DroboDashboardReborn.bat) | What the Start Menu and Desktop shortcuts run: starts the agent, then the app. Copied into every install |
| [`Run-Agent.bat`](Run-Agent.bat) | Runs the agent in its own window, which stays open showing the reason if the agent stops. Copied into every install |
| [`Check-ForUpdates.bat`](Check-ForUpdates.bat) | What the "Check for Updates" shortcut runs. Copied into every install |
| [`install.ps1`](install.ps1), [`uninstall.ps1`](uninstall.ps1) | A backup installer and its uninstaller, used only when WiX is not available or its build fails (the build then makes a `.zip` instead of an `.msi`). Proved by [`../test-fallback-installer.ps1`](../test-fallback-installer.ps1), which drives both into a temporary folder and needs no administrator window |

To build, from the repository's top folder:
`powershell -ExecutionPolicy Bypass -File windows/build-installer.ps1`.
The result lands in `windows/dist/`, which git ignores. To prove it installs,
runs and uninstalls cleanly, run [`../test-installer.ps1`](../test-installer.ps1)
from an administrator window.

The build picks up each file here by its exact name: do not rename or move
them.
