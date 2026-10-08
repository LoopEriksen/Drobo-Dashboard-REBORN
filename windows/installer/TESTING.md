# Testing Drobo Dashboard REBORN

Thank you for testing. This file ships inside the installed program folder
and is also in the repository. It covers installing, what to expect on first
run, how to report what you find, and how to remove it completely.

## Installing

1. Download `DroboDashboardReborn-<version>-win-x64.msi` from the release.
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
minimised window, then opens the app.

- The first time, pick your Drobo from the list it finds. It remembers your
  choice by the Drobo's permanent serial, so it still finds it after your
  router hands it a new address.
- The agent generates a private access token on first run and keeps it in
  `%LOCALAPPDATA%\DroboDashboardReborn\config.json`. The app picks it up
  automatically. If the browser dashboard asks for a token, that file is
  where it is.
- The agent runs only while its window is open. Closing that window stops
  it, and the app stops updating.

## What it will and will not do to your Drobo

It never formats, repairs, flashes firmware, resets, or touches the disk
pack; 53 of the 103 commands the firmware understands are refused in code.
The full list of what it *does* change -- lights, brightness, spin-down,
share permissions, creating and deleting shares, starting and stopping
DroboApps -- is in the README, with how each write is checked. **Deleting a
share deletes everything in it**, and the app makes you retype the name.

The complete inventory, every capability with its status (working on real
hardware, built but unproven, planned, blocked, or deliberately not going to
happen), is `docs/capabilities.md` in the repository.

## Reporting what you find

Open an issue on the repository. The most useful report has:

1. What you did, what you expected, what happened.
2. **Tools tab -> Copy diagnostics** in the app, pasted in. It leads with why
   the Drobo was unreachable, if it was.
3. Your Drobo model and firmware version (both are on the Status tab).

### Read the diagnostics before you paste them

An issue on the repository is **public**. The diagnostics report contains no
passwords and no access token, and it never has. It does contain, and says so
at the top of the file itself:

- your Drobo's **name, model, firmware and address on your network**,
- the **serial number of every drive** in it,
- your Drobo's **permanent hardware serial**,
- the **private network addresses of your PC**, but only when the Drobo could
  not be reached, since that is the information that explains why.

None of that is dangerous to someone on the far side of the internet: private
addresses are not reachable from outside your home, and a drive serial is
useful for a warranty claim and very little else. It is still yours. Read the
file, delete anything you would rather not publish, and send the rest. If you
would rather not post it at all, say so in the issue and it can be dealt with
another way.

**Never attach a packet capture.** This protocol is unencrypted and a capture
can carry your Drobo password in plain text. The same goes for the raw output
of anything under `tools/`: those are capture and decode utilities, and their
console output is the wire, printed.

If you have hardware this project does not -- a second Drobo, a model other
than the 5N, or a redundant pack you can safely pull a drive from -- see
`docs/testers-tasks.md` in the repository for exact, read-only steps that
would settle open questions.

## Removing it

Either:

- Start Menu -> Drobo Dashboard REBORN -> **Uninstall Drobo Dashboard
  REBORN**, or
- Settings -> Apps -> Installed apps -> Drobo Dashboard REBORN -> Uninstall.

Uninstalling needs administrator approval, the same as installing, and
removes the program folder completely, including anything the agent generated
inside it. The window it runs in waits for you to press Enter before it
closes, so the last message can be read. Your **settings** -- and the
private access token the agent generated -- live in
`%LOCALAPPDATA%\DroboDashboardReborn` and are **kept**,
the way most apps keep their settings, so reinstalling or upgrading picks up
where you left off. To erase them too, delete that folder by hand; the token
is only ever used over the local loopback and is worthless once the agent is
gone.

A **saved share login** is not part of that folder and is not removed either.
It lives in Windows' Credential Manager, which is where Windows keeps its own
network logins. To remove one: Control Panel > User Accounts > Credential
Manager > Windows Credentials, find your Drobo's address, and click Remove.
The app can also do it: open the connect dialog for that share and click
"Forget the saved login for this Drobo".

Uninstalling does not touch your Drobo, your shares, any mapped drive letters,
or any photos the agent stored -- those live wherever you pointed the photo
backup.
