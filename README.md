# Drobo Dashboard REBORN

**Software that keeps a Drobo NAS working now that the company behind it is gone.**

Drobo's parent, StorCentric, filed for bankruptcy in 2022 and was liquidated in
2023. `drobo.com` no longer resolves. Both iPhone apps were pulled from the App
Store. The last Windows release of Drobo Dashboard came out in December 2019 and
will never be updated again.

The hardware still works. This project rebuilds the software around it, and
keeps that software updatable as Windows and iOS move on.

**This project is unaffiliated with Drobo, StorCentric, or their successors.**
"Drobo" is used only to identify the hardware this software talks to.

---

## Installing

Download `DroboDashboardReborn-<version>-win-x64.msi` from this repository's
**Releases** page and double-click it. It installs into
`C:\Program Files\Drobo Dashboard REBORN` for everyone on the PC, so Windows
asks for administrator approval, and it **bundles its own Python runtime** so
nothing else needs installing. It adds a Start Menu entry that starts the agent
and opens the app. Uninstall from the Start Menu entry or from Settings > Apps;
that removes the program folder completely, including anything the agent
generated in it. Upgrading from 1.0.0? Uninstall it first:
[CHANGELOG.md](CHANGELOG.md) explains why.

Settings are per Windows account, not shared with the program: yours and the
loopback-only access token stay in `%LOCALAPPDATA%\DroboDashboardReborn`, the
way most apps keep settings, so a reinstall or an upgrade picks up where you
left off. Delete that folder to erase them. A share login you asked Windows
to remember lives in Credential Manager instead, and is removed there or from
the app's connect dialog.

The installer and app are **unsigned by choice** -- a certificate is an annual
cost and puts a private individual's legal name in every file, and this is a
free hobby project. So Windows SmartScreen warns on first run and calls the
publisher unknown ("More info" -> "Run anyway"). A machine with **Smart App
Control** switched on will not run the app at all: either use the browser
dashboard, which is unaffected because the bundled Python carries the Python
Software Foundation's signature, or switch Smart App Control off while you test
and back on afterwards (possible without reinstalling Windows since the April
2026 update). The full first-run guide is in
[`windows/installer/TESTING.md`](windows/installer/TESTING.md), which also
ships inside the installed folder.

## Running from source

Windows 10 or 11 with Python 3 installed. Double-click
**`START DROBO DASHBOARD.bat`** in the repository's top folder. Nothing else
needs finding.

It tells you if Python is missing. It starts the background service and opens
the Windows app, if one is installed or built; if not, open the web dashboard
address it prints. Keep its window open while you use the app: closing it
stops the service. It does not open your browser by itself; the app's Tools
tab has an "Open web dashboard" button. The first time, pick your Drobo from
the list; after that it remembers and goes straight there.

No Drobo at hand? Double-click **`START WITH SIMULATED DROBO (testing).bat`**
instead. It runs the same software against a built-in simulator, no hardware
needed.

Doing it by hand, building the Windows app, and running the tests are covered
under [Running it by hand](#running-it-by-hand) below.

---

## What it will and will not do to your Drobo

The command layer refuses 53 of the 103 commands the firmware understands.
Format, repair, firmware flash, factory reset and every raw `eCmdSet*` are
blocked in code, not by convention: the send helpers check the block list
themselves, so no code path reaches them by accident. Nothing here writes to
the disk pack.

It does make a small, deliberate set of changes, each through one audited path:

- **Identify** (blink the front lights) -- the only command on the safe list;
  it stops on its own.
- **LED brightness** and **drive spin-down delay** -- written by reading the
  device's live settings block, changing only what was asked for (one field,
  or two when spin-down is switched off), and echoing the rest back
  exactly as read. Refused outright if the block holds a field this software
  does not recognise.
- **Per-user share access**, limited to read/write (confirmed against the
  original Dashboard), code 0 (most likely no access, but not confirmed --
  the safe direction to be wrong in), or a level already set on the device.
  It will not guess a permission.
- **Create a share** and **delete a share**. Deleting a share deletes
  everything in it; the API requires the name typed twice and the app makes
  you retype it.
- **Start or stop an installed DroboApp**, validated against the list the
  device itself reports.

Every settings and share write reads the device back afterwards and reports
exactly one of: verified; verified but unacknowledged (the device closed the
connection instead of answering, yet the read-back shows the change); failed
(refused, rejected, unreachable, or applied nothing); or unknown -- never a
guess. Identify and DroboApp start/stop work differently: Identify reports the
device's own reply, and a DroboApp start or stop shows a fresh read of the app
list without treating it as proof, because an app changes state in its own
time. Not supported: renaming the Drobo, creating or deleting users, and
anything involving firmware.

And beyond the device itself:

- **It never formats, resets or repairs your disk pack, and it will not flash
  firmware.** The one change it can make on the Drobo that removes data is
  deleting a share, and it does not do that until you have typed the share's
  name exactly. This hardware is irreplaceable and there is no vendor recovery
  tool left.
- **It is never exposed to the internet.** Remote access, when it comes, goes
  through Tailscale, which opens no ports.
- **It keeps no password of its own.** A share login you ask it to save goes
  into Windows Credential Manager, where you can see and delete it yourself
  (Control Panel, then Credential Manager). This project invents no
  encryption, and the Windows app's settings file holds no secret. The agent's
  `config.json` holds its access token and, only if you set up email alerts,
  your email server's password; git ignores that file.
- **No cloud, no account, no subscription.** Nothing leaves the house.

---

## What it does

### It finds your Drobo

No IP address to type. The app lists the Drobos it can see on your network, with
their name, model and firmware, and you click the one you want. It remembers
your choice and opens straight to it next time.

It remembers by the Drobo's **permanent hardware serial**, not its IP address —
so if your router hands the Drobo a different address, it still recognises it.
Every candidate is confirmed against that serial before it's trusted, so
something else answering on the same address can never be silently accepted.

### It shows you the array

- **Drive health, per bay** — which drives are fine, which are complaining,
  which have failed
- **Capacity** — used, free, usable, and how much is held back for protection
- **Chassis temperature and uptime**, always shown with the age of the reading
- **Drive model, serial and firmware** for every bay, so you can order a
  replacement without opening the box
- **Error counts and SSD life**, shown only when they're worth worrying about

### It gets you to your files

This is the part modern Windows broke. Your Drobo offers its folders to
"Everyone", which Windows treats as a *guest* login — and Windows 11 stopped
accepting those. The share is fine; Windows just refuses.

The **Shares** tab fixes it: it lists your shares, and signs you in with a real
user name and password so Windows is satisfied. Tick *Save this login on this
PC* and it is stored in Windows' own Credential Manager, encrypted by Windows
and readable only by your Windows account, so you are not asked again. Leave it
unticked and nothing is written down at all: the login lasts until you sign
out. The whole story is in
[`docs/accessing-your-files.md`](docs/accessing-your-files.md).

### It tells you when something is wrong

- An **Alerts** tab that stays quiet when all is well, and marks itself when it
  isn't. Alerts stay listed until they're resolved, then move to a history.
- **Notifications to your phone**, once you switch them on (see "Alerts" in
  [`agent/README.md`](agent/README.md#alerts)): a failed drive at 3am reaches
  you even with the app closed, as long as the agent, the background service,
  is still running.
- It also flags things nothing else would tell you, like a Drobo that has
  negotiated a 100 Mbit link on a gigabit port and is running ten times slower
  than it should.

[`docs/capabilities.md`](docs/capabilities.md) lists every capability with its
status, checked against the code: complete on real hardware, built but not
yet proven, dormant, planned, blocked, or deliberately out of scope.

---

## Helping test

This was built against one Drobo 5N. Anything that needs different hardware --
a second unit, another model, a redundant pack you can safely degrade -- is
listed in [`docs/testers-tasks.md`](docs/testers-tasks.md) with exact,
read-only steps. Results, problems and questions go in an **issue** on this
repository. Never attach a packet capture: this protocol is unencrypted, and a
capture can carry the Drobo password. An issue is public, so read the
diagnostics report before pasting it; the testers' guide says what it
contains.

This repository is published for people to run on their own hardware and
report back. Nobody but the maintainer edits it, so there is no contribution
or pull-request process: an issue is the way in.

---

## What's in this repository

| What | What it is | Open first |
|---|---|---|
| `START DROBO DASHBOARD.bat` | Double-click to use the software with your real Drobo. Starts the background service and the Windows app. Keep its window open while you use it | — |
| `START WITH SIMULATED DROBO (testing).bat` | Double-click to try the app with a pretend Drobo. No Drobo needed; the app still looks for Drobos on your network when it starts | — |
| `CAPTURE DROBO COMMANDS (run me).bat` | Despite "run me" in its name, double-click it only when a capture is asked for. It asks for administrator rights, then records the original Drobo Dashboard 3.5.0 talking to your Drobo | [`docs/captures/README.md`](docs/captures/README.md) first: a recording can hold your Drobo password. What to record: [`docs/testers-tasks.md`](docs/testers-tasks.md) |
| `README.md` | This page | — |
| [`ROADMAP.md`](ROADMAP.md) | The plan of record: six phases, dated decisions, and what is complete, in progress or blocked | — |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed between releases | — |
| [`CREDITS.md`](CREDITS.md) | Other people's work this project relies on, and why it was trusted | — |
| [`LICENSE`](LICENSE) | The licence: GPL-3.0-or-later, the full text | — |
| [`CLAUDE.md`](CLAUDE.md) | Working notes for the Claude sessions this project is developed with; not needed to use the software | — |
| [`agent/`](agent/) | The background service: it watches the Drobo and serves the dashboard | [`agent/README.md`](agent/README.md) |
| [`sdk/`](sdk/) | The library that speaks the Drobo's own protocol, reverse-engineered: discovery, status, health, shares. The agent uses it | [`sdk/README.md`](sdk/README.md) |
| [`windows/`](windows/) | The Windows app, its tests, and the installer build | [`windows/README.md`](windows/README.md); testers: [`windows/installer/TESTING.md`](windows/installer/TESTING.md) |
| [`ios/`](ios/) | The iPhone app plan (nothing built yet) | [`ios/README.md`](ios/README.md) |
| [`docs/`](docs/) | The project's documents, grouped by who they are for | [`docs/README.md`](docs/README.md) |
| [`tools/`](tools/) | Packet-capture and firmware-analysis tooling used to map the protocol, plus the scripts the double-click `.bat` files run | [`tools/README.md`](tools/README.md), which marks the ones that contact a real Drobo |
| [`scripts/`](scripts/) | The privacy guard that checks every commit, once `sh scripts/install-hooks.sh` has been run in that copy. Never move or rename anything in it | [`scripts/README.md`](scripts/README.md) |
| [`vendor/`](vendor/) | Where Drobo's own software is kept for reference on a local copy. None of it is committed; git tracks only the README | [`vendor/README.md`](vendor/README.md) |
| [`.github/workflows/`](.github/workflows/) | The automatic checks GitHub runs on every push | [`.github/workflows/README.md`](.github/workflows/README.md) |
| `.gitignore`, `.gitattributes`, `.editorconfig` | Settings for git and text editors: which files git leaves out, and line endings | — |
| `.wix/` and `__pycache__/` folders (only on your PC) | Caches made by the installer build and by Python. Git ignores them | — |

## Where things are written down

| Document | What it answers |
|---|---|
| [`docs/README.md`](docs/README.md) | The index of the project's documents, grouped by who they are for |
| [`docs/capabilities.md`](docs/capabilities.md) | What this software is meant to do, and how far each part has got, checked against the code |
| [`docs/development-history.md`](docs/development-history.md) | How it was built, in order, including the turns that were wrong |
| [`docs/accessing-your-files.md`](docs/accessing-your-files.md) | Why Windows 11 refuses the Drobo's shared folders, and what to do about it. The Windows app opens this same page from its Tools tab |
| [`docs/protocol-map.md`](docs/protocol-map.md) | How a Drobo is spoken to: the protocol reference. The rest of the research is listed in [`docs/README.md`](docs/README.md) |
| [`windows/README.md`](windows/README.md) | Building the Windows app and its installer, and how versions and updates work |
| [`windows/installer/TESTING.md`](windows/installer/TESTING.md) | Installing, first run, reporting, and removal, for testers |
| [`docs/testers-tasks.md`](docs/testers-tasks.md) | What needs hardware this project does not have, with read-only steps |

The records cite commit hashes where a claim is worth checking. This
repository's public history starts with one fresh commit, so those hashes
point into the earlier, private history and will not resolve here;
[`docs/development-history.md`](docs/development-history.md) tells that path
in order instead.

---

## How it's put together

Three pieces. Only one of them knows anything about Drobo internals.

```
   Drobo 5N
      |
      |  the original protocol -- spoken only in sdk/drobo_nasd/
      v
   Drobo Agent          runs in the background, always watching
      |
      |  plain JSON over your LAN
      |
      +--- Windows app     native, WPF / .NET 10
      +--- web dashboard   any browser, including your phone
      +--- iPhone app      photo + video backup
```

| Piece | What it is | Status |
|---|---|---|
| **[Drobo Agent](agent/)** | Always-on Python service. Finds the Drobo, polls it, keeps history, raises alerts, receives photo uploads, serves everything as JSON. Standard library only — no dependencies to rot. | **Working against a real Drobo 5N** |
| **[Windows app](windows/)** | WPF / .NET 10. Device picker, Status, Alerts, Shares, Settings, Tools. | **Working** |
| **Web dashboard** | Served by the agent. Live-updating, works on a phone. | **Working** |
| **[iPhone app](ios/)** | SwiftUI. Automatic photo and video backup. | Not started — **needs a Mac to build.** Agent side is done. |

Keeping Drobo knowledge in one place means the apps never need to understand the
protocol, adding an app doesn't mean reimplementing anything, and the agent can
move to a Raspberry Pi later without changing a line in either app.

---

## How the protocol was recovered

Drobo published no documentation, and the company that knew it no longer exists.
So the first job was working out how a Drobo is spoken to at all.

Unpacking the official firmware gave the discovery mechanism (`_nasd._tcp`), the
daemon (`/sbin/nasd`), the message format, the connection model, and **all 103
protocol commands with their numeric IDs**. See
[`docs/protocol-map.md`](docs/protocol-map.md).

Two channels, both now working:

- **TCP 5000 — status.** The device volunteers its entire state the instant you
  connect: an unprompted `<ESATMUpdate>` XML greeting, no login. Identity,
  firmware, per-bay health and capacity all come from this.
- **TCP 5001 — commands.** A `DRINETTM` frame and a 220-byte login. This is how
  network settings, the share list, chassis temperature and uptime are read.

Everything this software sends is either a query or one of a short, captured
list of writes: Identify; LED brightness, spin-down, per-user share access, and
share create/delete, each through a read-modify-write of the device's own
configuration; and DroboApp start/stop. The dangerous commands in that table —
53 of the 103: format, flash, reset, repair, every raw `eCmdSet*` — are listed
by name in `sdk/drobo_nasd/commands.py` specifically so they are recognised
and refused.

**What's still unknown:** some commands — fan, power supply, cache battery —
accept the login and then answer nothing. The device also performs a
cryptographic challenge–response that we have not reversed. Neither blocks
anything in Phase 1. See [`docs/command-surface.md`](docs/command-surface.md) for
exactly what answers and what doesn't, and
[`docs/crypto-handshake.md`](docs/crypto-handshake.md) for the handshake.

---

## Where the project is

**Read [`ROADMAP.md`](ROADMAP.md) first.** It has the six development phases and
is the document to return to when things drift.

**Phase 1's original goal is met**: a Windows application that finds the Drobo
on the network, shows drive health, capacity, temperature and alerts from the
real device, lets you reach your files, and never writes to the disk pack or
the firmware.

**Phase 1 is not complete against the moved bar.** The bar moved on
2026-07-27 from "show me the Drobo's health" to "match what Drobo Dashboard
actually did" — a replacement that only reads is a monitor, not a dashboard —
and much of the writing half has since been built: LED brightness, spin-down
and per-user share access verified on hardware; share create and delete built
(create applied once, delete not yet sent to a device); DroboApps start and
stop built but not yet sent to a device. What remains against that bar is in
the roadmap's Phase 1 row: renaming the Drobo, share create's re-run and
delete's first send, user create and delete (waiting on a capture), and the
DroboSync trigger (needs a second unit), plus decoding the pack status codes,
which needs a degraded array to observe; the parser fails safe meanwhile.
Phase 2 is mostly built too. [`docs/capabilities.md`](docs/capabilities.md) has every capability with
its status; [`ROADMAP.md`](ROADMAP.md) has the phases.

The parameters this project would not guess were captured from the original
Dashboard on 2026-08-05/06 and 2026-08-25. What is still capture-blocked —
creating users, the event log's per-line format, permission code 2 — is
marked in [`docs/capabilities.md`](docs/capabilities.md). The jobs that need
hardware this project does not have, permission code 2 among them, are listed
with exact, read-only steps in
**[`docs/testers-tasks.md`](docs/testers-tasks.md)**.

---

## Running it by hand

The easy way is `START DROBO DASHBOARD.bat`. If you'd rather do it by hand:

```bash
py agent/run_agent.py
```

That prints the dashboard's address. Open it in a browser on this PC, and the
page asks once for the access token, which is in the agent's `config.json`. To
open it from your phone as well, the agent must first be told to listen on your
home network: see "Security" in [`agent/README.md`](agent/README.md#security).

To build the Windows app from source:

```bash
dotnet build windows/DroboDashboardReborn -c Release
```

By default the agent looks for a **real** Drobo (`"driver": "drobo5n"`, found
automatically by its serial). To try everything without hardware, run
`START WITH SIMULATED DROBO (testing).bat`, or set `"driver": "mock"` in
`agent/config.json`.

### Tests

Every suite runs against recorded or simulated data — no Drobo needed. One
suite, `sdk/tests/test_discovery.py`, also tries an optional read-only check
against a real Drobo at a placeholder address (or the address in
`DROBO_LIVE_HOST`), and skips it when nothing answers there:

```bash
py agent/tests/test_agent.py
```

The full inventory is thirty-one suites: ten under `agent/tests/`, sixteen
under `sdk/tests/` (the protocol, including every write path and its four
outcomes), and five under `tools/`. Every suite runs standalone with
`py <file>`, from its own folder or from the repository's top folder, and none
needs hardware. Each is a `test_*.py` file in one of those three folders;
[`agent/README.md`](agent/README.md) names the agent's ten, and
[`tools/README.md`](tools/README.md) describes the five in `tools/`.

The Windows app's notification rules and saved logins are tested too — they
decide when this software is allowed to interrupt you, and what it puts in
Windows' Credential Manager, so they don't get to be untested:

```bash
dotnet run --project windows/DroboDashboardReborn.Tests
```

Nobody has to remember to run any of this: GitHub runs every suite, and the
Windows app's tests, on each push — see
[`.github/workflows/README.md`](.github/workflows/README.md).

---

## Releases

Installers are attached to this repository's **Releases** page, and what
changed between releases is in [`CHANGELOG.md`](CHANGELOG.md). Release 1.0.0
was published from a separate, code-only copy of this repository, which is now
retired; from 1.1.0 on, releases come from here.

## License

Drobo Dashboard REBORN is free software, licensed under the GNU General
Public License, version 3 or (at your option) any later version
(GPL-3.0-or-later). The full text is in [`LICENSE`](LICENSE); from the
next release, the installer also puts a copy in the program's folder. You
may run, study, share and change it; if you distribute it, changed or not,
you must do so under the same licence and make the source available.

## A note on what this is

This is unaffiliated with Drobo, StorCentric, or their successors. It is an
independent reimplementation, written for hardware its maintainer already has, of a
protocol whose vendor no longer exists to maintain it.

No Drobo software is redistributed here.

Almost none of the code is borrowed from anyone else either — but where a
piece of it is, [`CREDITS.md`](CREDITS.md) says so by name.
