# Testing Drobo Dashboard REBORN

Thank you for testing. This file ships inside the installed program folder
and is also in the repository. It covers installing, what to expect on first
run, what the app can change on your Drobo and how far each change has been
proven, how to report what you find, and how to remove it completely. The
copy inside your installed folder describes the version you installed; the
copy on GitHub describes the newest code.

## Installing

1. Download `DroboDashboardReborn-<version>-win-x64.msi` from the
   [Releases page](https://github.com/LoopEriksen/Drobo-Dashboard-REBORN/releases).
   Your browser may warn that the file is not commonly downloaded. The exact
   words are the browser's own and vary between browsers and versions; the
   button that keeps the file is usually **Keep**, sometimes behind a "..."
   menu, followed by **Keep anyway**.
2. Double-click it. Windows asks for administrator approval, because the
   program installs into `C:\Program Files\Drobo Dashboard REBORN`, for
   everyone who uses the PC.
3. Untick "Create a shortcut on the desktop" if you do not want one. That is
   the only choice the installer asks.

**Nothing else needs installing.** The Python the agent runs on is bundled
inside the package. You do not need Python on your machine.

**Your own settings stay yours.** The program is shared; its settings are
not. Each Windows account gets its own configuration and its own access
token under `%LOCALAPPDATA%\DroboDashboardReborn`, so two people using the
same PC do not see each other's setup.

> **Upgrading?** First close the app and the **Drobo Dashboard REBORN -
> Agent** window (it sits minimised on the taskbar). 1.1.0 and 1.2.0
> upgrade in place: install the new version over them, and your settings
> and token are kept.
>
> **Upgrading from 1.0.0?** Uninstall it first. 1.0.0, like every build
> before the installer changed to an all-users install, installed into your
> user profile without an administrator prompt, and Windows Installer cannot
> upgrade one of those in place with a package that installs for the whole
> machine. Remove the old one from Settings > Apps, then install this. Your
> settings and token survive: they were never in the program folder.

### The SmartScreen warning, and why it is there

The installer and the app are **not code-signed, on purpose**. A certificate
costs money every year and puts a private individual's legal name inside every
file shipped; this is a hobby project given away for free, so it does neither.
The trade is that Windows does not know who published it, and says so:

- On the MSI: "Windows protected your PC" -> click **More info** -> **Run
  anyway**.
- On first launch of the app, possibly the same again.

Nothing about that warning is specific to this program. It is what Windows
shows for any installer whose publisher it cannot name.

### If your machine has Smart App Control on

Smart App Control is a Windows 11 feature that runs **only unsigned software it
already recognises**, with no per-app exception and no "run anyway" button. It
is off on most machines: it turns itself on only after a clean install of
Windows 11, and switches itself off if it decides you look like a developer.
Check under Settings -> Privacy & security -> Windows Security -> App & browser
control.

If it is on, you have two options, and **either one is a perfectly good way to
test**:

1. **Use the browser dashboard and skip the app.** The agent still runs, because
   the Python it is built on carries the Python Software Foundation's own
   signature. Start the agent from the Start Menu entry, open
   `http://127.0.0.1:7420`, and you get the same information and the same
   writes as the Windows app.
2. **Switch Smart App Control off while you test, then back on.** Same settings
   page, one toggle. This used to be a one-way door -- once off, it could not be
   turned back on without reinstalling Windows -- but **since the April 2026
   Windows update that is no longer true**, and it can be switched back on the
   same way. It is still a real security setting, so it is your call, and only
   you should make it.

## First run

Start it from the Start Menu ("Drobo Dashboard REBORN") or the desktop
shortcut. That starts the agent -- the piece that talks to the Drobo -- in a
minimised window called **Drobo Dashboard REBORN - Agent**, waits for it to
answer, then opens the app.

- Starting Drobo Dashboard REBORN again while it is already running is fine:
  the launcher sees the running agent and only opens the app. The first start
  can take up to about 20 seconds while the agent looks for your Drobo; the
  launcher waits for it before it opens the app.
- The first time, pick your Drobo from the list it finds. It remembers your
  choice by the Drobo's permanent serial, so it still finds it after your
  router hands it a new address.
- The agent generates a private access token on first run and keeps it in
  `%LOCALAPPDATA%\DroboDashboardReborn\config.json`. The app picks it up
  automatically. If the browser dashboard asks for a token, that file is
  where it is.
- The agent runs only while its window is open. Closing that window stops
  it, and the app stops updating.

### If it does not find your Drobo

If the app shows no Drobo, disconnect any VPN on this PC and press Refresh.
If it still finds none, press "Add one in Settings", type the Drobo's IP
address in the ADDRESS box, press Add, then press "Connect →" on its row. The
agent checks that a Drobo answers at that address and watches it from then
on. It remembers the choice in
`%LOCALAPPDATA%\DroboDashboardReborn\config.json`. If Connect cannot use the
address, the reason is shown under the list and nothing is changed.

The browser dashboard has the same box: while it cannot reach the Drobo, it
shows "Find the Drobo". Type the address and press "Watch this Drobo".

As a last resort, close the agent window, open
`%LOCALAPPDATA%\DroboDashboardReborn\config.json`, set
`"host": "<the Drobo's address>"` in the `"drobo"` block, and start the app
again.

### If you have two Drobos

With two Drobos, "Connect →" on a row now opens that row's Drobo, and the
agent watches it until you pick another (Change Drobo, then Connect on the
other row). Opening the app reconnects to the Drobo you used last only if the
agent is still watching it; if not, the app says so and you press Connect on
the row you want. Please report whether the dashboard that opens shows the
name and serial of the row you clicked.

The agent watches one Drobo at a time. Every change the app or the browser
dashboard sends names the Drobo it was made against, so after you switch the
agent to another Drobo (Connect on another row, or the browser's address
box), a change made from a page or window that still shows the first Drobo
is refused and nothing is sent: the result says the agent now watches
another Drobo. If you see that, reload the browser page, or give the app
about ten seconds to show the new Drobo, check which Drobo is on screen, and
try again. A browser page that was open when you switched should move to the
new Drobo by itself within about ten seconds; please say in your report
whether it did. And if a change ever reaches a Drobo other than the one on
screen, report that above all.

### If something goes wrong at the start

- **If you never get past the first screen**, copy the text in the
  **Drobo Dashboard REBORN - Agent** window and put it in your report: that
  window says what the agent is doing and why it stopped, if it did. Select
  the text with the mouse, then press Ctrl+C while it is still highlighted.
  With nothing selected, Ctrl+C stops the agent; if that happens, start it
  again from the Start Menu. Read the text before you post it: unlike Copy
  diagnostics (below), the agent's window is not masked, and it can show
  your Drobo's address, its serial and your Windows user name in a path.
- If the agent window says **Another program is using port 7420; change
  agent.port in ...**, something else on this PC has the agent's port. The
  simplest way out is to close that program, start Drobo Dashboard REBORN
  again, and say in your report which program it was. If you cannot close
  it, change `agent.port` in the file the message names. The launcher and
  the agent follow that setting, but the app does not: each time you open
  the app, set **AGENT** in the **SETTINGS** under its list of Drobos to
  `http://127.0.0.1:<the new port>` and press the **Connect** beside it.
  The browser dashboard is then at that address too, not at 7420.
- If the agent window says **An agent is already running on port 7420;
  this one will close.**, nothing is wrong: one agent is already running,
  and that window closes by itself.

## If your Drobo is not a 5N

This software was built and checked against one Drobo 5N. On a Drobo that is
not a 5N, or on firmware other than 4.3.1, you may see one or more Disk pack
health warnings that start "This Drobo reports device_status=..." (or
disk_pack_status, dnas_status, status_ex). The only healthy values this
software has seen come from one Drobo 5N on firmware 4.3.1, so on your model
these may be perfectly normal. Please include the full text of each such
warning in your report. The bay list may also show an extra empty bay. Keep
your first session to reading: do not use share create, share delete,
DroboApps start/stop, or the Advanced Configuration card (in the browser,
the "Device settings" panel and the share access editor).

## What it will and will not do to your Drobo

It never formats, repairs, flashes firmware, resets, or touches the disk
pack; 54 of the 103 commands the firmware understands are refused in code.
**Deleting a share deletes everything in it**, and the app makes you retype
the name.

These are the only changes it can make, and how far each has been proven on
a real Drobo. Both apps show the same sentences beside the buttons.

| Change | Where | On a real Drobo |
|---|---|---|
| Identify (blink the front lights) | Tools tab; **Identify** in the browser | Sent and accepted. On the one Drobo 5N tested so far, the lights did not visibly change at the setting this app sends. Please say in your report whether yours blinked. Press Stop when you are done. |
| LED brightness | Tools tab, Advanced Configuration; "Device settings" in the browser | **Proven** on a Drobo 5N |
| Drive spin-down delay | Tools tab, Advanced Configuration; "Device settings" in the browser | **Proven** on a Drobo 5N |
| Share access, the Everyone row | Tools tab, Advanced Configuration; "Network & shares" in the browser | **Proven** on a Drobo 5N |
| Share access, rows for named accounts | The same places | **Not yet proven.** Only the Everyone row has been proven on a real Drobo. Rows for named accounts, and share changes on a Drobo that has user accounts, have not been tried yet. |
| Create a share | Shares tab; "Add or remove shares" in the browser | **Not yet proven.** Creating a share is not yet proven on a real Drobo: it was applied once in testing, before a fix. Try it with a throwaway name first. |
| Delete a share | Shares tab; "Add or remove shares" in the browser | **Not yet proven.** This app's delete has never been sent to a real Drobo. Try it first on an empty share you made for the purpose. |
| Start or stop a DroboApp | Tools tab; "Installed on the Drobo" in the browser | **Not yet proven.** Start and Stop have not yet been tried on a real Drobo. If one does not behave, say so in a Tester report. |

Share changes on a Drobo that has user accounts have not been tried yet, for
create and delete as much as for share access.

**Use a throwaway share for create and delete, and say exactly what the app
showed**: the words of the result, and its colour. Green means the change
was made and read back; red means nothing happened; amber means the app
cannot say whether it happened, so check whether the change is there
before you press anything again. Close the original Drobo Dashboard 3.x
before you change anything here, so two programs are never changing the
Drobo's settings at the same moment.

The README describes how each write is checked, and the complete inventory,
every capability with its status (working on real hardware, built but
unproven, planned, blocked, or deliberately not going to happen), is
[`docs/capabilities.md`](https://github.com/LoopEriksen/Drobo-Dashboard-REBORN/blob/main/docs/capabilities.md)
in the repository.

## Reporting what you find

Open an issue on the repository with the **Tester report** form:
<https://github.com/LoopEriksen/Drobo-Dashboard-REBORN/issues/new?template=tester-report.yml>.
It asks for what the most useful report has:

1. What you did, what you expected, what happened.
2. If you changed anything on the Drobo, what the app said about it.
3. **Tools tab -> Copy diagnostics** in the app (see below).
4. Your Drobo model and firmware version (both are on the Status tab), the
   app's version (on the Tools tab), and your Windows version.

If something should not be posted publicly at all -- a security problem, or
diagnostics you cannot redact -- report it privately instead, through the
repository's **Security** tab -> **Report a vulnerability**.
[`SECURITY.md`](https://github.com/LoopEriksen/Drobo-Dashboard-REBORN/blob/main/SECURITY.md)
says how.

### Diagnostics, and reading before you post

An issue on the repository is **public**. The diagnostics report contains no
passwords and no access token, and it never has.

Tools tab -> Copy diagnostics gives you a copy made for posting in a public
issue, on the clipboard or in a file. The Drobo's serial number, name and
address, every drive serial, volume names, event-log messages, other network
addresses and the Windows user name in any path are replaced with
placeholders such as SERIAL-1, NAME-1, ADDRESS-1, DRIVE-SERIAL-1, VOLUME-1,
EVENT-1, NETWORK-1 and USER-1, and the same value always gets the same
placeholder. The model, firmware, states and figures are kept, because they
are what explains a problem. Paste or attach it as it is; give it a quick
read first: the masking is a safety net, not a promise that nothing slipped
through. The form asks you to confirm that you used this copy, or removed
those values by hand. The button
beside it, "Unmasked copy (not for posting)", gives the same report with the
real values, for your own use only; its file name says UNMASKED. (Version 1.2.0's
Copy diagnostics is not masked: on 1.2.0, remove those values by hand.)

Text copied from the **Drobo Dashboard REBORN - Agent** window is not
masked (see above): take out anything that looks like a serial number, an
address, your Drobo's name, a share name or your Windows user name before
you post it.

If the app shows "Something went wrong inside the app", press Ctrl+C while
that message is open to copy it, and paste it into your report. In that
copy, names, addresses and your Windows user name are already replaced with
placeholders; read it before you post all the same. The full text, with the
app version and the time, is kept for your own use in
`%LOCALAPPDATA%\DroboDashboardReborn\app.log`. It never holds the access
token or a password, and like your settings it is kept when you uninstall.
Do not post app.log itself without reading it: it keeps paths and addresses
as they are.

**Never attach a packet capture.** This protocol is unencrypted and a capture
can carry your Drobo password in plain text. For the tools in the
repository's `tools/` folder, one rule applies everywhere in this project:

- **Safe to paste after reading:** the console output of `nasd_sweep.py` and
  `drobo_probe.py`. Read it first and take out any address, name or serial it
  shows.
- **Paste only with names removed:** the decoded command list that
  `CAPTURE DROBO COMMANDS (run me).bat` prints, and the output of
  `access_codes.py`. They name your shares and user accounts, and can show
  serials and addresses.
- **Never post:** any capture file, or the JSON report `nasd_sweep.py` writes.
  They can hold your Drobo's serial, every drive serial, share and user names
  and your Drobo's address, and a capture can hold your password.

If you have hardware this project does not -- a second Drobo, a model other
than the 5N, or a pack that is degraded or rebuilding -- see
[`docs/testers-tasks.md`](https://github.com/LoopEriksen/Drobo-Dashboard-REBORN/blob/main/docs/testers-tasks.md)
in the repository for exact, read-only steps that would settle open
questions.

## Updates

Check for Updates is switched off on purpose. New versions are published on
the Releases page, <https://github.com/LoopEriksen/Drobo-Dashboard-REBORN/releases>.
On GitHub, Watch > Custom > Releases tells you when one appears.

## Removing it

First close the app and the **Drobo Dashboard REBORN - Agent** window. Then
either:

- Start Menu -> Drobo Dashboard REBORN -> **Uninstall Drobo Dashboard
  REBORN**, or
- Settings -> Apps -> Installed apps -> Drobo Dashboard REBORN -> Uninstall.

The Start Menu's **Uninstall Drobo Dashboard REBORN** runs Windows' own
removal, the same one Settings > Apps uses. Windows asks you to confirm and
for administrator approval, then removes the program. Your settings and
access token in `%LOCALAPPDATA%\DroboDashboardReborn` are kept, so a
reinstall picks up where you left off; delete that folder by hand to erase
them.

The program folder is removed completely, including anything the agent
generated inside it. The token is only ever used over the local loopback
and is worthless once the agent is gone.

A **saved share login** is not part of that folder and is not removed either.
It lives in Windows' Credential Manager, which is where Windows keeps its own
network logins. To remove one: Control Panel > User Accounts > Credential
Manager > Windows Credentials, find your Drobo's address, and click Remove.
The app can also do it: open the connect dialog for that share and click
"Forget the saved login for this Drobo".

Uninstalling does not touch your Drobo, your shares, any mapped drive letters,
or any photos the agent stored -- those live wherever you pointed the photo
backup.
