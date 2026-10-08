# Windows app — Drobo Dashboard REBORN

Native Windows client. It finds your Drobo on the network, shows its health,
capacity, drive bays and alerts (and can pop up a notification for an alert),
lists its shares and connects you to them, and makes the changes this software
supports: LED brightness, spin-down, share access, starting and stopping
DroboApps, and flashing the lights. Creating and deleting shares is built too,
but not yet proven on a Drobo. Everything comes from the local agent, which the
app polls every 10 seconds.

**It knows nothing about the Drobo protocol** — it talks only to the agent's
JSON API (the `/api/...` routes described in
[`../agent/README.md`](../agent/README.md#api)), exactly like the web
dashboard. All Drobo logic stays in `agent/` and `sdk/`.

## In this folder

| | |
|---|---|
| [`DroboDashboardReborn/`](DroboDashboardReborn/) | The app's source code. What each file is: [The app's source files](#the-apps-source-files), below |
| [`DroboDashboardReborn.Tests/`](DroboDashboardReborn.Tests/) | Tests for the app's notification rules and saved-login handling. Run them from the repository's top folder: `dotnet run --project windows/DroboDashboardReborn.Tests` |
| [`installer/`](installer/README.md) | Everything the `.msi` installer is built from. **Testers: open [`installer/TESTING.md`](installer/TESTING.md) first** |
| [`build-installer.ps1`](build-installer.ps1) | Builds the installer. Run it from the repository's top folder |
| [`test-installer.ps1`](test-installer.ps1) | Installs, runs and uninstalls the built `.msi` to prove it works. Needs an administrator window |
| [`test-fallback-installer.ps1`](test-fallback-installer.ps1) | Installs, re-installs and uninstalls the fallback (non-`.msi`) scripts to prove they work. Drives them into a temporary folder, so it needs no administrator window and touches nothing real |
| `dist/` | Build output. Appears after a build; git ignores it |
| `installer-identifiers.local.txt` | Optional and private: extra patterns, such as names, that the installer build refuses to package. It exists only on the maintainer's PC and git ignores it; without it the build uses only its built-in checks. Never commit it |
| `.wix/` | A download cache WiX creates in the folder the installer build was started from. The build is normally started from the repository's top folder, so this copy only appears if it was once started from inside `windows/`. Git ignores it, and it is safe to delete: the next build downloads it again |

## The app's source files

`DroboDashboardReborn/` holds 38 files. Start with `MainWindow.xaml` (the main
window: the Drobo picker and the five tabs) and `MainWindow.xaml.cs` (what they
do).

**Pop-ups and extra windows.** Each is a pair: a `.xaml` file for the layout
and a `.xaml.cs` file for the code.

| | |
|---|---|
| `ConnectShareDialog` | "Connect as...": connects to a share with a Drobo user name and password, and can save that login |
| `NewShareDialog` | "New share...": creates a share |
| `DeleteShareDialog` | Deletes a share, once its name has been typed exactly |
| `DiagnosticsChoiceDialog` | "Copy diagnostics": to the clipboard or to a file |
| `ShareBrowserWindow` | Shows a share's files. It only looks: it never copies, moves, renames or deletes |
| `SplashWindow` | The small window shown while the app starts |

**One line in an on-screen list.** Each `*Row.cs` file: `AlertRow` (an alert),
`DiscoveredDeviceRow` (a Drobo in the picker), `DriveRow` (a drive bay),
`DroboAppRow` (a DroboApp), `EventLogRow` (a line of the Drobo's event log),
`NotificationCategoryRow` (a notification switch in Settings), `ShareRow` (a
share) and `VolumeRow` (a volume).

**The rest.**

| | |
|---|---|
| `App.xaml`, `App.xaml.cs` | Start-up, the splash screen, and the button and drop-down styles every window uses |
| `AlertNotifier.cs` | Which alerts may pop up a notification |
| `CredentialStore.cs` | Saved share logins, kept in Windows Credential Manager |
| `SmbConnections.cs` | Connecting to a share as a named user, through Windows itself |
| `DroboSettings.cs` | What the app remembers between launches (`settings.json`); never a password |
| `DiagnosticsReport.cs` | The readable report "Copy diagnostics" saves to a file |
| `MarkdownGuide.cs` | Shows the guide to reaching your files, opened from the Tools tab, as a readable page. The button appears only where the app can find `docs/accessing-your-files.md` within six folders above itself. A plain `dotnet build -c Release` inside a checkout is close enough; a `win-x64` publish sits one folder deeper, and an installed copy has no `docs/` at all, so in both of those the button hides instead of offering a page it does not have |
| `TrayIconManager.cs` | The icon in the notification area, by the clock |
| `WriteOutcome.cs` | The four ways a settings change can end |
| `JsonHelpers.cs` | Small helpers for reading the agent's answers |
| `AssemblyInfo.cs` | Standard WPF boilerplate |
| `Assets/` | The two app icons (normal, and with an alert badge) and the script that draws them |
| `DroboDashboardReborn.csproj` | Build settings and the version number |

## Requirements

- .NET 10 SDK (`winget install Microsoft.DotNet.SDK.10`) — only for building. An
  installed copy needs nothing else: the installer bundles its own Python.
- Python — only when running from source (see [agent/README.md](../agent/README.md))
- The agent running somewhere reachable (usually this same PC)

## Installing (the easy way)

If someone handed you a `DroboDashboardReborn-*-win-x64.msi`, that's the whole
install:

1. Double-click it. It installs to
   `C:\Program Files\Drobo Dashboard REBORN` for everyone on the PC, so
   Windows asks for administrator approval, and adds a Start Menu shortcut
   and, unless you untick it, a Desktop shortcut.
2. Launch it from a shortcut. That runs `Launch-DroboDashboardReborn.bat`,
   which starts the agent on the Python bundled inside the install folder
   (nothing else to install) and then opens the app.
3. First run: the agent generates its access token and writes
   `config.json` into `%LOCALAPPDATA%\DroboDashboardReborn`, per Windows
   account, outside the install folder. (It used to land beside the agent in
   the install folder; that changed on 2026-09-02 so an uninstall or upgrade
   could not take your settings with it, and Program Files is not writable
   anyway.) The app reads that file automatically and fills in the token — you shouldn't have
   to type or paste anything.

To remove it: **Settings → Apps → Drobo Dashboard REBORN → Uninstall**, same
as any other Windows app. That is a real, registered uninstall — it removes
the install folder, the shortcuts, and the Add/Remove Programs entry.

There's also a **Check for Updates** shortcut in the Start Menu folder. See
[Updating](#updating) below — right now, until a release feed exists, it will
truthfully tell you update checking is switched off, and its window waits for
a key so you can read that. (From 2026-09-02 until 2026-09-26 it failed in
every installed copy and closed before saying so; see Updating.)

### Install-tested, for real

`windows/test-installer.ps1` installs the built `.msi` on this PC and checks
what landed: the program files, the bundled Python, the shortcuts, the
Add/Remove Programs entry, and that no `config.json` or access token was
shipped. It then starts the program the way a tester would, against a
simulated Drobo, runs the Check for Updates shortcut's own target and requires
its "switched off" answer, uninstalls it and checks the removal was clean, and
repeats a short install and uninstall without the Desktop shortcut. Given a
second, higher-numbered build it also tests an upgrade. It needs a PowerShell window
started with "Run as administrator", refuses to run if the program is already
installed, and prints `PASS`/`FAIL` per check with a total at the end, exiting
1 if any check failed. Re-run it after any change to the installer.

`windows/test-fallback-installer.ps1` does the same job for the fallback
scripts below — a fresh install, a re-install over it, an uninstall that
refuses while something is still running from the install folder, and an
uninstall that removes all four places while keeping your settings. It needs
no administrator window and installs nothing for real: it hands `install.ps1`
and `uninstall.ps1` a temporary folder and a scratch registry key under
`HKCU`, so Program Files, the Start Menu, the Desktop and `HKLM` are never
named to them, and the elevation checks are verified by reading the scripts
rather than by triggering a prompt. The script's own header explains why that
is safe. Same `PASS`/`FAIL` output, same exit codes.

## Building the installer

```powershell
powershell -ExecutionPolicy Bypass -File windows/build-installer.ps1
```

One command: publishes the app self-contained (win-x64, single-file, no .NET
runtime required on the target machine — that's the ~155 MB), stages it
together with the agent's Python source, and packages the result as an `.msi`
with [WiX](https://wixtoolset.org/) if WiX is installed
(`dotnet tool install --global wix`; the build adds the extensions it needs by
itself, and runs WiX through `dotnet` when Windows blocks the `wix` command,
as Smart App Control does). It prints the exact output path when done. Safe to
re-run — each run replaces that version's output under `windows/dist/`
(gitignored, never commit anything from it).

If WiX isn't installed, or its build fails, the script falls back automatically
to `windows/installer/install.ps1`, a self-contained PowerShell installer zipped
up with the same payload — still a genuine install, to the same place as the
MSI (`C:\Program Files\Drobo Dashboard REBORN`, for all users, so it needs
administrator rights; all-users Start Menu and Desktop shortcuts; a real
Add/Remove Programs entry under `HKLM`) and a genuine uninstall
(`windows/installer/uninstall.ps1`, the Start Menu "Uninstall" shortcut it
creates, or Settings > Apps; it asks Windows for administrator approval
itself), just not an `.msi`. The script tells you plainly which path it took.
Prove that pair with
[`test-fallback-installer.ps1`](test-fallback-installer.ps1), described under
[Install-tested, for real](#install-tested-for-real) above.

`windows/installer/` holds the WiX source (`Package.wxs`) and the fallback
scripts; `windows/installer/*.bat` ship inside the installed app too (they're
how the installed copy launches itself and checks for updates).

After building, prove the MSI works, from a PowerShell window started with
"Run as administrator":

```powershell
powershell -ExecutionPolicy Bypass -File windows/test-installer.ps1
```

What it checks is listed under
[Install-tested, for real](#install-tested-for-real) above.

## Updating

Update checks ship **off by default** and there is one implementation of them,
shared: `agent/drobo_agent/updates.py`, configured by the `updates` block in
the agent's `config.json` (`agent/config.json` when running from source,
`%LOCALAPPDATA%\DroboDashboardReborn\config.json` for an installed copy).

Two entry points, one engine:

- **In the app**, under Settings → Preferences → Updates, which asks the
  agent's `/api/update`. The web dashboard does not show it.
- **The Check for Updates shortcut**, which runs `agent\check_updates.py` —
  a thin wrapper round `drobo_agent.updates` — on the bundled Python, so it
  still works when the agent isn't running, which is the reason to keep it.
  It runs that file by path, never `python -m`: the bundled runtime's
  `python314._pth` keeps the working directory off `sys.path`, and the
  `-m` form it used until 2026-09-26 failed in every installed copy with
  `ModuleNotFoundError`. Its exit status is the answer — 0 up to date or
  switched off, 1 an update is available, 2 could not tell, 3 the check could
  not run — and `windows/test-installer.ps1` runs it and requires the
  switched-off answer.

To turn checking on, set `"enabled": true` in that block and point
`manifest_url` at either a GitHub Releases API URL:

```json
{ "enabled": true, "manifest_url": "https://api.github.com/repos/<owner>/<repo>/releases/latest" }
```

…or your own JSON manifest carrying `version`, `download_url` and `sha256`. The
engine detects which it got from the content, because a URL can't tell you what
it will return. A GitHub release is announced but flagged as unverifiable —
GitHub publishes no checksum — while a manifest with a valid SHA-256 says so.
Neither is ever downloaded or installed automatically.

> Until 2026-07-28 there were **two** update checkers: this one, and a separate
> `check-update.ps1` with its own `update-config.json`. Two mechanisms that each
> had to be switched on independently is how an updater quietly stops working —
> you enable one and assume you're covered. The PowerShell one is gone; the
> shortcut it served now calls the shared engine.

What it does **not** do yet: check on its own schedule, notify you
unprompted, or download/install anything itself. It checks only when asked
(opening Settings, or the shortcut), and a failed or disabled check never
raises a popup or a nag: the app shows it quietly on its Preferences panel,
and the shortcut, which you started yourself, prints it and waits for a key.

## Versioning

`<Version>` in `DroboDashboardReborn/DroboDashboardReborn.csproj` is the number
a release is named by: the installer's file name and MSI version,
`version.txt` (which the Check for Updates shortcut compares against), and the
app's own version on its Tools and Settings tabs all come from it.

It is not the only copy, though. Before building a release, set the same
number in every one of these:

- `<Version>`, `<AssemblyVersion>` and `<FileVersion>` in
  `DroboDashboardReborn.csproj` (the last two take four parts, for example
  `1.1.0.0`)
- `VERSION` in `agent/drobo_agent/api.py` (the agent version the Tools tab
  shows, and what the update check under Settings compares against) and
  `__version__` in `agent/drobo_agent/__init__.py`
- `__version__` in `sdk/drobo_nasd/__init__.py` and `version` in
  `sdk/pyproject.toml`

That list used to be the only thing holding those seven numbers together, and
it did not hold: the 1.0.0 release shipped `sdk/pyproject.toml` still
saying `0.1.0`. There is now a check that fails when they disagree --

```bash
py scripts/check_versions.py
```

-- and it also runs inside `agent/tests/test_updates.py`, so an ordinary test
sweep catches a bump that stopped half way. Run it after changing any of them.

`build-installer.ps1 -Version` overrides the number for one build, compiler
included, and is for `test-installer.ps1`'s upgrade cycle only: a real release
bumps the files above and builds with no `-Version` at all.

## Build and run (development)

```bash
dotnet run -c Release --project windows/DroboDashboardReborn
```

Or build the `.exe` and launch it:

```bash
dotnet build windows/DroboDashboardReborn -c Release
```

The executable lands at
`windows/DroboDashboardReborn/bin/Release/net10.0-windows/DroboDashboardReborn.exe`.

## Using it (dev build)

1. Start the agent: `py agent/run_agent.py` (it prints the dashboard's
   address).
2. Start the app. It fills in the access token by itself, from the first of
   these files it finds: `%LOCALAPPDATA%\DroboDashboardReborn\config.json`
   (where an installed copy's agent keeps its settings), then
   `agent/config.json`. With the right token it goes straight to the list of
   Drobos it can find; pick yours. If it finds no token, or says "The agent
   rejected that token" (which happens when the installed copy has been used
   on this PC, because the first file then belongs to that other agent), it
   shows the **Agent** address (default `http://127.0.0.1:7420`) and a
   **Token** box: paste the token from `agent/config.json` and press
   **Connect**.

It then shows five tabs (Shares, Status, Alerts, Settings, Tools) and
refreshes every 10 seconds.

## Why WPF and not WinUI 3 (yet)

WinUI 3 would give the exact Windows 11 look. This first build is
WPF because it produces a real native `.exe` with no packaging workload, so it
builds and launches with just the .NET SDK — which made it verifiable end to
end. The API boundary is identical, so moving the same views to WinUI 3 later is
a UI-layer change, not a rearchitecture.

## Not committed

`bin/` and `obj/` are build output and are gitignored, as is all of
`windows/dist/` (everything `build-installer.ps1` produces: the staged
payload, the `.msi`, the `.wixpdb`). The project sources (`.csproj`, `.xaml`,
`.cs`) and the installer sources (`windows/installer/**`,
`windows/build-installer.ps1`) are tracked.
