# Drobo Dashboard REBORN — roadmap
_Last updated: 2026-10-19_

**This is how we develop the software.** When work drifts or a decision feels
unclear, come back here first. The phases below are the maintainer's, recorded
verbatim except for two personal phrases restated in the third person (shown
in square brackets), followed by what each one actually requires.

Ordering rule: **a phase does not start until the phase before it works.** The
one exception is noted under Phase 5.

Companions: [`docs/capabilities.md`](docs/capabilities.md) is the inventory of
what the software does and how far each part has got; [`docs/development-history.md`](docs/development-history.md)
is the path taken, in order; [`CHANGELOG.md`](CHANGELOG.md) is what changed
between releases. All three are kept against the code, like this file.

## What this is

Drobo Dashboard REBORN is replacement software for a Drobo network storage
box, written because the company that made it is gone: no vendor, no
documentation, no updates, and a last official Dashboard from December 2019
that Windows will eventually refuse to run. It finds the Drobo on the network,
shows what the device knows about itself, gets you back to your files on a
modern Windows, tells you when something is wrong, and makes the handful of
settings changes the original Dashboard could make. It talks to stock,
unmodified firmware and never writes to the disk pack or the firmware itself.

It is for people who already own one of these boxes and want to keep using it.
It is built and proven against a single Drobo 5N; other network models are
expected to speak the same protocol and have not been tried.

---

## The phases, as stated

> **Phase 1: MVP** — Recreate the Drobo Dashboard as Drobo Dashboard REBORN
> which is just for connecting to the Drobos on the network. This will be on
> Windows. Do this without having to rewrite the firmware.
>
> **Phase 2: Optional Features** — Build out the optional features after all
> core functions are confirmed to be working. We may have to rewrite the
> firmware to support this.
>
> **Phase 3: Drobo Dashboard Reborn Mobile** — iOS Creation and Integration.
> [An iOS app that connects to the Drobo and uploads photos and videos.]
>
> **Phase 4: Drobo Pix REBORN** — Make an app mimicking Drobo Pix given the
> functionality of our new program. It would be best for it to function as a
> replacement/competitor of icloud photos. It needs the ability to view, send,
> and receive the photos and videos. it would need to either auto convert to
> jpeg or be able to interpret hiec files. Would it be able to retain all the
> meta data and properly save live photos?
>
> **Phase 5** — [Reach the Drobo over the internet from away from the network
> it lives on.]
>
> **Phase 6: Share with Others Online** — We will eventually make the repository
> public so the we need to scrub all personal or identifying information from GH
> and keep it off for versions that people can download.
>
> If there is any inspiration you are taking from the firmware of software for
> the original drobo stuff, then go for it but ask for permission to include
> additional features conceieved this way and put them in our markdown.

---

## Where we are

Three lists, so nobody has to infer state from the phase table below. Kept
against the code, not against intentions -- an item moves only when git HEAD
says it has.

**Complete -- built and tested; everything here that talks to a Drobo has
been proven against the real 5N**

- Discovery by permanent serial (mDNS, hostname, last-known address, subnet
  sweep, typed address), refusing an impostor on the same address
- Live status: identity, per-bay health, capacity, volumes, chassis
  temperature and uptime, throughput; the health fields surfaced in both UIs
- Alerts worst-first; desktop notifications, off by default (ntfy and email
  are built but have never sent a real message -- see Features)
- Shares: list, connect as a named user, mount, in-app file browser
- Windows app: Shares, Status, Alerts, Settings and Tools tabs, with the
  Advanced Configuration card on Tools behind the admin login
- Browser dashboard at write parity with the Windows app
- MSI installer that bundles its own Python runtime (2026-09-02), installing
  into Program Files for all users since 2026-09-08, with a Start Menu
  Uninstall entry, clean removal, and upgrades that keep settings -- the
  lifecycle test ran elevated against the per-machine package on 2026-09-18
  (53 checks) and again on 2026-09-21 with the upgrade leg (64 checks, all
  passing: install 1.1.0, launch the installed copy, remove it, then install
  1.1.0, upgrade it in place to a 1.1.1 build, and prove the settings
  survived both the upgrade and the uninstall), and on 2026-10-08 against the
  .NET 10 build (70 checks, all passing, the installed Check for Updates
  shortcut included; one error mentioning a constructor appeared in the console part-way through and was not captured; it did not fail a check and could not be reproduced afterwards (the installed app opened normally on its own and against the simulated driver)); auto-updater built and
  dormant until a manifest URL is set
- LED brightness; spin-down delay
- Per-user share access writes (the Everyone row verified on hardware; this
  unit reports no user accounts, so a populated user list has never been seen)
- DroboApps list
- Event log fetch (per-line timestamp/severity format still undecoded)
- Backup to an external drive (tested over ordinary folders; no full array
  has been copied). **Graded differently in the two records:** Working here
  and in Features, Built, unverified in `docs/capabilities.md` -- a split left
  by the 88bedd2 merge, flagged 2026-09-26 for the maintainer to settle
- Photo-upload API on the agent side, ready for a mobile client
- DroboSync status (dormant; reports "not configured" honestly)
- Diagnostics that lead with *why* the Drobo was unreachable
- PII scanner and pre-commit guard; this changelog

*Work in flight has its own section below — see "Working on now". Three items
left this list on 2026-09-26. Identify and saving a share login were never
graded Working in Features (Partial and Built, unverified). DroboApp start and
stop was graded Working, but no record shows this project ever sending it to
a device, so it was downgraded to Built, unverified the same day. All three
are now in Working on now.*

**Not started, or blocked on something the project does not have**

- Rename the Drobo; create or delete users (wanted since 2026-09-21, and
  capture-blocked -- see Working on now)
- Local thumbnail generation
- iOS app (needs a Mac); Drobo Pix REBORN (Phase 4, design questions open)
- Remote access via Tailscale (Phase 5, unblocked, not begun)
- Status-code meanings (needs a degraded array); fan/PSU/battery (a 5N reports
  none); DroboSync trigger (needs a second unit) -- all listed with exact
  steps in `docs/testers-tasks.md`
- WinUI 3 (optional polish)

*Not on any list, because it is not happening: code signing (decided
2026-09-05, see the decision note in Phase 1).*

*A license left this list on 2026-10-19: the project is GPL-3.0-or-later,
now graded in Features.*

### Phase by phase

| | Phase | State |
|---|---|---|
| ▓▓▓▓▓▓▓▓▓░ | **1 — Windows MVP** | **The original definition of done is met; the finish line moved on 2026-07-27 and the writing half is not finished.** Agent, web UI and the WPF app (Status, Alerts, Shares, Settings, Tools) read a live 5N; chassis temperature and performance ship; the MSI installs; getting to your files is solved; and the health fields the greeting already sent are now surfaced in both UIs (`error_count`, `ssd_life_remaining`, `relayout_count`, `double_degraded_count`). Outstanding against the original bar: decoding `mStatus` / `DNASStatus`, which needs a degraded array to observe. The parser fails safe until then, so nothing waits on it. Outstanding against the moved bar ("match what Drobo Dashboard actually did", see the command table below): renaming the Drobo, re-running share create and sending share delete for the first time, user create/delete (capture-blocked), `eCmdStandby` (in or out of scope is undecided) and the DroboSync trigger (needs a second unit). |
| ▓▓▓▓▓▓▓░░░ | 2 — Optional features | **Mostly built — this row said "not started" until 2026-08-28, which was never true.** Features were pulled forward as they became reachable. Done: dark mode (WPF dark palette; the web UI honours `prefers-color-scheme`), desktop notifications (`TrayIconManager` balloon tips, plus the Ntfy/Email notifiers), the installer and auto-updater, DroboApps management (`droboapps_action`, gated), and share management (access writes verified on hardware; create built and applied once; delete built but never sent). Also done: the transfer manager (`backup.py` plans, then copies with robocopy only on a separate explicit call; hardware-independent, and its copy is exercised end to end by `agent/tests/test_backup.py`). Not built: local thumbnail generation, and user create/delete (capture-blocked). |
| ░░░░░░░░░░ | 3 — iOS upload app | Not started. The agent half is done and tested — pairing with QR, hash-first dedupe, resumable chunked upload. Needs a Mac. |
| ░░░░░░░░░░ | 4 — Drobo Pix REBORN | Not started. Has an unsolved design question — see below. |
| — | **Getting to your files** | Diagnosed and fixed 2026-07-26. Windows 11 blocks the Drobo's guest logon (`0xC05D0003`); the answer is to connect as a named user. Done end to end: agent (`/api/shares`) and the Windows app's Shares/Settings tabs (open, connect-as, disconnect; read-only network config). See [`docs/accessing-your-files.md`](docs/accessing-your-files.md). |
| ▓▓░░░░░░░░ | 5 — Remote access | Approach settled (Tailscale), nothing built |
| ▓▓▓▓▓▓░░░░ | 6 — Public release | **Decided 2026-10-08: this repository itself is published**, with a fresh history that starts at one first commit; the earlier history is kept privately and never published. Every tracked file was scrubbed of personal and device detail the same day, and `.gitignore` covers vendor binaries, captures and `agent/config.json`. Release 1.0.0 was published 2026-08-10 as a code-only export into a separate repository, which is now retired along with the export procedure and its derived roadmaps; 1.1.0 is the first release from here, and every push to this repository carries `CHANGELOG.md`. Not yet done: the publication itself. See the Phase 6 section below. |
| — | **Parked** | After Phase 6. Sensor fields this chassis may not expose (battery/fan/PSU). The event log was parked here too and was unparked on 2026-08-21, when its fetch was built. TCP 5001 itself has been swept (`docs/command-surface.md`) — it's not unexplored, it just hasn't given up these particular fields yet. Nothing depends on these. |

## Working on now

Several rows below wait on the same thing: one sitting with the maintainer's own
5N and the original Dashboard. Everything that session has to settle, in order,
is written out step by step in [the hardware-session guide](docs/hardware-session.md)
(2026-09-21, revised 2026-09-26) -- share create and delete, Blink Lights (this
app sending the 900 the original Dashboard sends, and Dashboard's off press),
the event log's replies, one permission code, and creating and deleting a user.

| Item | Status | What is left |
|---|---|---|
| Bounded frame reads, so one slow network peer cannot stall the device picker | **Built, unverified** | Carried back from the separate public repository of the time (since retired) and merged on 2026-09-19, with one fix of its own on the way (a finished read now hands the socket its original timeout back). It covers the status channel and the command channel's settings reads and writes (`config_cmd`'s own reads) only. Temperature and uptime (`sysinfo`), the event log, Identify, DroboApps and DroboSync still call `_read_frame` with no deadline, relying on a per-read socket timeout alone. Extending it to them is open work; until 2026-09-26 this row said both channels were covered. Proven against loopback peers that answer and then never finish replying; a real 5N has not yet been read under the new time limit. The next run of the app against the hardware settles that half. |
| Blink the front lights (Identify) | **Partial** | The device accepts the command. On this unit nothing visible happened for an empty parameter or for 15, this project's default (2026-08-07). The original Dashboard's press was captured on 2026-07-25 carrying `IdentifyInterval` 900, a value this project has never sent. Next: send 900 while watching the unit (hardware session, part F). If it blinks, the default changes from 15 to 900; that code change waits on the test. |
| Start and stop an installed DroboApp | **Built, unverified** | Wire format captured in both directions (2026-08-06) and validated against the device's own app list, but no record shows this project's sender ever reaching a device. Needs one start and one stop on hardware. |
| Remembering a share login | **Built, unverified** | Tested against the real Credential Manager. Not yet proven in use: sign out, sign back in, and reach a Drobo share on the saved login without being asked again. |
| Create a share on the device | **Built, unverified** | Applied to a real Drobo once, before the fix that settles an unacknowledged write by reading the value back. One more run against the hardware decides it. |
| Delete a share on the device | **Built, unverified** | Built from a capture, never sent to a device at all. Needs one run against the hardware. |
| The "Check for Updates" shortcut in an installed copy | **Working** | Broken in every installed copy from 2026-09-02, when the installer began bundling its own Python, until 2026-09-26: it asked that Python to find the agent's code in the folder it was started from, which the bundled runtime never looks in, and its window closed before the error could be read. No published release had it; 1.0.0 used the PC's own Python. Fixed, and proven against a copy of the bundled runtime; a check on every push now runs it the way that runtime does. The lifecycle test now runs the installed shortcut as well, and did so in the elevated run of 2026-10-08: it gave the "switched off" answer and exit 0. |
| Publishing this repository, with release 1.1.0 | **Working** | The next release is **1.1.0** (a minor bump, decided 2026-09-21): every place the number is written says so, and the changelog's top section is 1.1.0, dated 2026-10-15, and opens by telling testers to uninstall 1.0.0 first. No longer blocked by the bounded reads, which are merged. How it is published changed on 2026-10-08: this repository becomes the public one, published as one fresh first commit with none of its earlier history, and the separate export repository is retired, so there is no export or carry-back step any more (see Phase 6). Every tracked file was scrubbed for it the same day. Published 2026-10-08; release 1.1.0, on .NET 10, went out on this repository's Releases page on 2026-10-15 after the elevated lifecycle test passed (70 checks). |
| Reading the device's event log line by line | **Blocked** | The log is fetched; the format of each line's timestamp and severity has never been captured, so both fields carry an explicit not-decoded marker instead of an invented value. Needs a capture of the original Dashboard reading the log. |
| Meaning of share access codes 0 and 2 | **Blocked** | Code 1 (read/write) is confirmed. 0 is displayed hedged but can still be written, because "no access" is the safe direction to be wrong in; 2 has never been seen and can be written only by copying a code already live on the device. Settling either meaning needs it observed on real hardware. The maintainer's hardware session measures one of them on the way: the throwaway user it creates is given a permission other than read/write, and `tools/access_codes.py` read before and after pins the word the original Dashboard shows to the number the device stores. |
| Add and remove Drobo user accounts | **Blocked** | Wanted: the maintainer decided on 2026-09-21 that this gets built. Nothing about it has ever been seen on the wire -- this unit has never reported a user at all -- and this project does not guess a write, so it waits on a capture: one recording of the original Dashboard creating a throwaway user (and giving it that permission), and a second, short one deleting it, made only after the permission codes have been read back. The capture script's on-screen steps now cover both. Moved here from Future features, where it was Someday. |
| Meaning of the pack status codes | **Blocked** | Needs a degraded array to watch. Anything unrecognised is treated as a warning, so nothing else is waiting on this. |

## Features

What the software has today. `docs/capabilities.md` carries the same ground in
finer grain, one row per capability; if a row here changes, that file changes in
the same commit.

| Feature | State | Notes |
|---|---|---|
| Find the Drobo on the network | **Working** | By its permanent serial, so a new address from the router still finds it: mDNS, hostname, last-known address, an optional bounded sweep of the subnet, or an address typed in. A different device answering on the same address is refused rather than silently accepted. If nothing answers when the service starts — the Drobo off, or this PC's own network not up yet — it starts anyway, says it cannot reach the Drobo, and keeps looking; until 2026-09-26 it stopped instead, so a PC that booted before its Drobo monitored nothing. That part is tested against simulated discovery, not yet seen on a real boot. |
| Live device status | **Working** | Identity, per-bay drive health, capacity, volumes, chassis temperature, uptime, throughput and pack health. Read from the real 5N, and regression-tested offline against a sanitised recording of its status message. |
| Alerts, worst first | **Working** | Drive failure and warning, capacity, temperature. Fan and power-supply rules exist and light up if a model ever reports them; this chassis reports neither. |
| Desktop notifications | **Working** | Off until switched on, category by category. The rules about when the app may interrupt you exist because the balloons were seen and found intrusive in real use. A test drives a running agent through every fault it can inject and checks each one reaches the right notification category. |
| The same alerts by ntfy or email | **Built, unverified** | Written, switchable, and off by default — but no message has ever been sent to a real ntfy topic or a real mail server. The email tests prove only that a password cannot leak and that the mail library is never even loaded when email is switched off; the ntfy path has no test beyond being switched off. |
| Getting to your files | **Working** | Windows 11 refuses the Drobo's guest logon, so the app connects as a named user, mounts a drive letter, opens, disconnects, and browses the share in-app. The password goes to Windows through a direct call, never a command line. |
| Remembering a share login | **Built, unverified** | Optional and off unless ticked, saved only after the connection has already succeeded. Stored in Windows Credential Manager, encrypted by Windows under the signed-in account, removable from Control Panel; this project writes no password file of its own. Added 2026-09-08. The Windows app's own test program saves, replaces, finds and forgets a login in the real Credential Manager under a test-only name that can never be a real server, and passes locally and in the automatic checks on a push (first green run 2026-09-20). What has not been proven is the point of the feature in use: signing out, signing back in, and reaching a Drobo share on the saved login without being asked again. |
| Browser dashboard | **Working** | One self-contained page, no frameworks and no build step, usable from anything on the same network including a phone. Updates the moment the Drobo does. At parity with the Windows app for everything written to the device; backup and getting to your files stay in the Windows app. |
| Windows app | **Working** | A native app with five tabs — Shares, Status, Alerts, Settings, Tools — with the Advanced Configuration card on Tools behind an in-app sign-in (user admin, password admin by default). That sign-in is a safety catch against accidental changes, not the device's own security: the Drobo accepts those writes on its plain device login, and the card says so. (Until 2026-09-26 this row called it "the device's own admin login".) |
| A write boundary enforced in code | **Working** | Of the 103 commands the firmware understands, 53 are refused outright, one is on the safe list (blink the lights) and two are vetted with exactly one sanctioned sender each. A test names nine destructive commands and fails if any is ever promoted. |
| Settings the software can change: light brightness, drive spin-down delay, per-user share access | **Working** | Each is a read-modify-write of the device's live settings block that refuses if the block holds a field this software does not recognise, and is confirmed by reading the value back. Verified on real hardware. For share access, what the hardware confirmed is the Everyone row: this unit reports no user accounts at all, so a populated user list has never been seen on a real device. |
| Blink the front lights to identify the box | **Partial** | The device accepts the command. On this unit nothing visible happened for an empty parameter or for 15, the value this software sends by default. The original Dashboard sends 900 (captured 2026-07-25), and that has not been tried yet; see Working on now. What the software tells the user does not match what has been observed. The Windows app, the browser dashboard and the agent's reply all say the lights should be blinking now. The agent's reply also says the original Dashboard used the value just sent, which is untrue for the default of 15. (This row said the software admits nothing changes. It does not.) Whether to change that wording is a separate decision. |
| Start and stop installed Drobo apps | **Built, unverified** | Wire format captured in both directions and validated against the list the device itself reports. No record shows this project ever sending it to a device; graded Working until 2026-09-26. See Working on now. |
| The device's own event log | **Partial** | Fetching works; the per-line format does not decode. See Working on now. |
| Create and delete a share | **Built, unverified** | See Working on now. |
| Back up the Drobo to an external drive | **Working** | Plans the copy, asks for confirmation, then copies with robocopy, with bounded retries and progress, only on a separate explicit call. Nothing in it talks to the Drobo: it copies between two folders, and its own test suite runs a real copy on Windows end to end (44 checks). There is no record of a whole array having been copied, which is a question of size, not of whether the copy works. **The two records disagree on this row:** `docs/capabilities.md` grades it Built, unverified (tested over ordinary folders), a split left by the 88bedd2 merge. Flagged 2026-09-26 for the maintainer to decide which grade is right. |
| Photo upload service for a future phone app | **Dormant** | Pairing by QR code, duplicate detection by hash before any bytes move, resumable chunked upload for long videos — all three covered by tests against a running agent, including the attempts to write outside the target folder. Capture dates survive a resume as well — the test resumes without resending the date and still finds the video filed under it; only setting the stored file's own timestamp is untested. Nothing connects to any of it until the iPhone app exists. |
| Drobo-to-Drobo replication status | **Dormant** | Reports "not configured" honestly. Needs a second Drobo to do anything at all. |
| Update check | **Dormant** | On demand only, never automatic, and off until a release address is configured. Verifies a checksum. The Windows app shows the answer on its Preferences panel; the Start Menu "Check for Updates" shortcut the installer creates is the only way to ask for one deliberately. That shortcut failed in every installed copy from 2026-09-02 until its fix on 2026-09-26, and the fix still waits on one elevated installer run -- see Working on now. |
| Windows installer | **Working** | Installs for all users into Program Files, bundles its own Python so nothing needs installing first, adds Start Menu and Uninstall entries, and upgrades in place keeping settings. The lifecycle test ran from an administrator shell against the all-users package on 2026-09-18 (53 checks) and again on 2026-09-21 with its upgrade leg (64 checks, all passing): install, launch of the installed copy against the simulated Drobo, removal, and then a 1.1.0 install upgraded in place by a 1.1.1 build, with the planted user settings surviving both the upgrade and the final uninstall. Neither run exercised the "Check for Updates" shortcut; the checks that do were added on 2026-09-26 and have not yet run. |
| Fallback installer and uninstaller | **Built, unverified** | A pair of PowerShell scripts used when the installer toolchain is unavailable and the build produces a zip instead. Mirrors the real installer exactly in reverse, refuses to remove anything while the app or service is still running, and asks for administrator rights at most once rather than looping. Since 2026-09-19 a test drives both end to end — install, re-install, the refusal, uninstall — into a temporary folder and a scratch registry key, as an ordinary user, touching nothing real (78 checks); redirecting the four target places is all or nothing, so the test cannot leave one pointing at the real location. What is still unproven is one real, elevated install into Program Files. |
| Diagnostics report | **Working** | Leads with *why* the Drobo could not be reached. Carries no password and no access token. It does carry hardware serial numbers, and this PC's own private network addresses when the Drobo is unreachable; it says so at the top, and the testers' guide says to read it before pasting it anywhere public. |
| Privacy guard on every commit | **Working** | A scanner plus a commit hook that blocks personal information, database files and real environment files. The checks on every push go further: credential files blocked from being tracked, tracked content scanned for secrets, and the same scan for personal information. |
| The tests run by themselves on every push | **Working** | Beside the secrets and personal-data scan and the compile check: all thirty-one Python suites, and the Windows app's own tests on a Windows runner. A suite that runs no checks, prints a failed check and exits 0 anyway, or moves out of its folder fails the run rather than dropping out of the count. Built 2026-09-19; first green run on GitHub 2026-09-20, both jobs. |
| A simulated Drobo | **Working** | The whole program runs against a pretend device, including a sign-in account, so the alert path and the connect flow can be tried with no hardware. Nothing simulated is ever handed to Windows. |
| A licence for the public release | **Working** | GPL-3.0-or-later, the maintainer's decision on 2026-10-19. The full text is `LICENSE` at the top of the repository, the SDK's package metadata declares the same licence, and the installer build copies `LICENSE` into the program folder beside `TESTING.md` -- that copy has not been through a build yet, and the installer's lifecycle test does not check for it yet. Moved here from Future features, where it was Later. |

## Future features

| Feature | When | Depends on |
|---|---|---|
| Reach the Drobo from outside the house | **Next** | Nothing. The approach is settled — a private network on the PC or a small always-on box, no ports opened — and none of it is built. |
| Rename the Drobo from the app | **Later** | Nothing technical — the mechanism is captured. The whole network settings block has to be resent, because a partial one blanks whatever it leaves out. |
| Start the software automatically at login | **Later** | Nothing. It is a documented manual step today; the installer does not set it up. |
| An iPhone app: health, alerts, and photo and video upload when you get home | **Later** | A Mac, which is the only thing missing. The service half is finished and tested. |
| A photo library to replace Drobo Pix — view, send and receive, including Apple's photo and Live Photo formats | **Someday** | The iPhone app, and a change to the upload service: one Live Photo is two files that must stay linked, and the service has no notion of a group. |
| Thumbnails generated on the machine running the service | **Someday** | Nothing but time. |
| Start a Drobo-to-Drobo replication | **Someday** | A second Drobo on the same network. |
| Rebuild the Windows app on a newer toolkit | **Someday** | Nothing. Optional polish, not a gap. |

### Not planned

- **Updating or flashing the Drobo's firmware.** The last builds were withdrawn
  by the vendor and there is no recovery tool. This is the one feature that
  could destroy the hardware.
- **Custom firmware.** It would need a second unit to sacrifice, and the
  on-disk format is undocumented, so custom firmware could not read an existing
  disk pack anyway.
- **Creating, resizing or deleting volumes.** Thirteen commands exist for it;
  none has been observed on the wire, and this project will not guess a write
  to the disk pack.
- **Putting the Drobo or the service on the internet.** The device will never
  receive another security patch.
- **Encryption or logins on the wire toward the device.** Both ends have to
  agree and the device's end is frozen.
- **Code signing** — decided against 2026-09-05; this is not a for-profit
  project. What that costs is set out under Phase 1.
- **A file browser on the phone.** The built-in Files app already does it.
- **Apple push notifications.** They need a server; ntfy and email do the job.

## Keeping this current

- Update this file at every major change — a feature lands, a feature is
  proven, a plan changes, or work starts or stops.
- Move items between the tables rather than deleting them, so the record stays
  honest.
- Change the "Last updated" date in the same commit.

Two more rules this project needs specifically. `docs/capabilities.md` holds
the same ground in finer grain: a row that changes here changes there in the
same commit, or the two will disagree. And the rule above is about *this*
file — every thread doing work in this repository updates it, rather than
leaving it to whoever notices later.

---

*Everything below is the detail behind the tables above: every dated decision
and the reasoning behind it, what was tried and what was wrong. It is the
project's record, not a summary, and it is kept. The phases as stated are
above, immediately under "What this is".*

---

## Phase 1 — MVP: Windows, network Drobos, stock firmware

**Definition of done:** a Windows application that finds the Drobo on the
network, shows drive health, capacity, temperatures and alerts from the *real*
device, and never writes to the disk pack or the firmware.

### The finish line moved — 2026-07-27

That definition was met, and Phase 1 was reported complete. The maintainer then read
the full list of what the original Dashboard could do and said, plainly:

> "we still need to program all those things before we consider phase 1
> complete."

**So Phase 1 is not complete.** The original bar was "show me the Drobo's
health"; the new bar is "match what Drobo Dashboard actually did". That is a
substantially larger target and it is the right one — a replacement that only
reads is a monitor, not a dashboard.

The full comparison lives in the capability review: all 103 firmware commands,
what each does, our plan, and the specific issue blocking it. The shape of the
remaining work, in one line: **we built the reading half and almost none of the
writing half.**

What that leaves, roughly in the order it is worth doing:

| Area | Commands | State |
|---|---|---|
| Identify / lights | `eCmdIdentify` | **Done 2026-07-27** — the first write. Sent and accepted, but no blink has been seen on this unit, and the original Dashboard's value, 900, has not been tried yet; Features grades it Partial (noted 2026-09-26) |
| Installed software | `eCmdGetDroboAppsStatus` | **Done** |
| LED brightness (read) | `eCmdGetDimming` | **Done** — reads the current level |
| LED brightness (write) | `eCmdSetConfig` `<Dim>` — Dashboard never sends `eCmdSetDimming` (captured 2026-08-05) | **Shipped 2026-08-19** — agent endpoint + WPF Advanced panel, read-back-verified |
| Rename the Drobo | `eCmdSetConfig` carrying the whole `DRINasNetworkConfig` — not `eCmdRename` (captured 2026-08-05) | Mechanism captured, not built. A partial document would blank the omitted network settings — see `docs/protocol-map.md` |
| Device event log | `eCmdGetUserEventLog` (91) with `<Path>/uel.log</Path>` (captured 2026-08-05) | **Fetch built 2026-08-21** — `sdk/drobo_nasd/eventlog.py`, wired through `drivers.py` to the agent and the WPF viewer. The *reply's per-line format* is still uncaptured, so entries carry `TS_NOT_DECODED` / `SEVERITY_NOT_DECODED` rather than a fabricated timestamp or an every-line-"info" severity. The old row's `eCmdGetEventLogs` (83) was simply the wrong command (this row said 90, which is `eCmdGetUserGroupList`, until 2026-09-26) |
| Users and groups | `eCmdGetUserGroupList` | Answers **empty** — nothing to show |
| Share/user management | `eCmdSetConfig` shares block | Share-access writes **shipped 2026-08-19** (agent endpoint + WPF Advanced panel, verified on hardware 2026-08-12). Share **create**: format captured 2026-08-25, built, and **applied on a real device once** (2026-08-25/26) — but the device answered the write with a connection close instead of a result frame, so it succeeded while the code called it a failure. That acknowledgement bug was **fixed in `b1c2064`** — a write the device never acknowledges is now decided by the read-back (`config_cmd.decided_by_readback`, covered by `sdk/tests/test_write_arbitration.py`). Create has **not been re-run on hardware since that fix**, so it stays **implemented, not shippable-verified**. Share **delete**: format captured, **never sent to hardware**. Both are built, wired and ungated at HEAD, and listed under Added for 1.1.0 in `CHANGELOG.md`, but neither is verified on hardware (this said "neither is shipped" until 2026-09-26). See `docs/protocol-map.md`. User create/delete still capture-blocked |
| Volumes and drive letters | 13 commands | Read half **done** 2026-07-27/28 — the volume list comes from the greeting and shows in the agent, the web UI and the Status tab. The 13 *write* commands (create, resize, delete a LUN) are not started and not planned: nothing has been observed on the wire, and this project will not guess a write to the pack |
| Standby / spin-down | `eCmdStandby` | Wants a safe-to-stop check that doesn't answer |
| DroboSync | 7 commands | Needs a second Drobo to test at all |
| Format / repair / firmware | 8 commands | Refused at every phase, permanently |

Some of these are not technically possible on this hardware — fan, PSU and
cache battery time out consistently, and a 5N plausibly has none of them to
report. Those get raised individually rather than silently dropped.

### Decisions — 2026-07-28

The maintainer answered the four open questions from
[`docs/archive/what-i-need-from-you.md`](docs/archive/what-i-need-from-you.md).

**Auto-updater: build it, leave it dormant until release.** The code goes in
now; the check never fires until a release channel is configured. This is the
right shape — the machinery is the hard part and it wants writing while the
project is being actively worked on, not bolted on in a hurry the day something
needs shipping. No update source needs choosing yet.

**Desktop notifications: yes, but only for alerts, and every one independently
switchable in Settings — default off.** This does NOT reverse the "no toasts,
ever" rule in TrayIconManager; it narrows it. That rule came from the maintainer
twice reporting the app was intrusive, and the distinction that makes this
acceptable is that a notification fires only when something is actually wrong,
never on a routine poll, and never steals focus. A toast that appears because a
drive failed is the app doing its job. Anything else is noise, and the switches
exist so the maintainer can prove that to themselves.

**DroboSync: code it in, dormant, revisit later.** Cannot be tested without a
second Drobo, so it ships disabled and unadvertised rather than as a button
that fails.

*(Reversed 2026-08-19: an in-app file browser for a mounted share shipped
with the explicit mount flow, commit `506030c`, for the case where you are
already in the app and the share is already mounted. The reasoning below
about Explorer still stands for everything else.)*

**File browser: not building one.** Windows File Explorer is better than
anything this project would write, and the Shares tab already mounts shares as
drive letters with real credentials. "Upload/download support" in BLUEPRINT §2
is therefore met by mounting, not by a bespoke transfer UI. Struck from scope.

**Code signing: decided against — 2026-09-05.** The maintainer's decision, in their
words: "we will not do code signing. this is not for profit." The software ships
unsigned, deliberately and permanently, and this is no longer an open item.

What that costs, stated plainly so nobody has to rediscover it:

- SmartScreen shows **Unknown publisher** on first run of the installer. The
  tester clicks *More info*, then *Run anyway*. Signing would not remove that
  prompt for an audience this size anyway — Microsoft's own threshold is weeks
  and hundreds of installs.
- A machine with **Smart App Control** on refuses the WPF app outright, with no
  per-app exception. That machine either turns SAC off, or uses the browser
  dashboard, which is a complete front-end SAC has no opinion about because it
  is a web page, not an executable.
- The bundled Python is signed by the Python Software Foundation, so the agent
  and therefore the browser dashboard run even where the app will not.

The two free signing routes (SignPath Foundation, Certum Open Source) both
require an OSI-approved licence; if the licence question is ever settled that
way, signing becomes free and this decision is worth revisiting. Paid routes
would put the maintainer's legal name on every binary. (Noted 2026-10-19: the
licence is now GPL-3.0-or-later, which is OSI-approved. The decision above is
unchanged; whether to revisit it is the maintainer's call.)

> **Correction, for the record:** an earlier note in this project suggested a
> "free local exclusion" for Smart App Control. There is no such thing — SAC has
> no per-app allowlist; it is on or off. A second claim beside it, that turning
> it off **cannot be undone without reinstalling Windows**, was true when
> written and **is no longer true — corrected 2026-09-05**: since the April 2026
> cumulative update (KB5083769, Windows 11 24H2/25H2) SAC can be switched off
> and back on from Windows Security > App & browser control. Turning it off to
> test, then back on, is now an ordinary thing to ask of a tester.

**The Windows app moved from .NET 8 to .NET 10 — 2026-10-08.** The installer
bundles the .NET runtime, and .NET 8's support ends on 2026-11-10, so 1.1.0
would have handed testers a runtime a month from its last security fix. .NET 10
is supported until November 2028. Both project files target net10.0, CI and
`build-installer.ps1` require the 10.0 SDK, and the app and its tests built and
passed unchanged (69 of 69).

### What's built — and now reading the REAL device (2026-07-24)

- **Protocol cracked and confirmed on live hardware.** nasd on TCP 5000 pushes a
  full `<ESATMUpdate>` status greeting on connect, no login. Framing, fields and
  the read path are all confirmed against a real 5N. See `docs/protocol-map.md`.
- **The command channel is solved too, and is sent to routinely.** TCP 5001,
  `DRINETTM` framing, a 220-byte login carrying the device serial twice. Used
  today by `config_cmd.py` (network/share/admin config), `sysinfo.py` (chassis
  temperature and uptime), and swept 46 commands deep across two sweeps by
  `tools/nasd_sweep.py` and `tools/sweep_remainder.py` — see
  `docs/command-surface.md`.
- **`Drobo5NDriver` is real** (`sdk/drobo_nasd/esatm.py` +
  `sysinfo.py`) — connects, reads the greeting, tops up chassis temperature
  from the command channel, returns a live Snapshot. Regression-tested
  offline against a sanitized capture (`sdk/tests/test_esatm.py`) and against the
  sysinfo measurements (`sdk/tests/test_sysinfo.py`).
- **The agent serves live data.** Point `config.json` at `"driver": "drobo5n"`
  and the dashboard shows the actual array: name, firmware, per-bay disk health,
  capacity, and chassis temperature with its own alert thresholds (warning at
  50 °C, critical at 60 °C).
- **The agent** (`agent/`) — polling, history, alerts, notifications, photo
  intake, shares, config, sysinfo. Thirty-one test suites today — ten under `agent/tests/`,
  sixteen under `sdk/tests/`, five under `tools/` — all passing, last confirmed
  by running every one of them on 2026-09-21 (all but
  `tools/test_dashboard_strings.py`, added 2026-10-19), plus the Windows app's own suite
  under `windows/DroboDashboardReborn.Tests/`. Since 2026-09-19 the automatic
  checks on a push run every one of them too, and the Windows app's suite on a
  Windows runner; the first green run was 2026-09-20, so "all passing" is now
  something the push protects rather than a claim re-established by hand. See
  [`agent/README.md`](agent/README.md) for how to run each suite; it gives a
  check count for the main agent suite only, and no number is duplicated here,
  where it would only go stale again.
- **The web dashboard** — the interim Phase 1 UI; usable on desktop and phone.
- **Tools** — `drobo_probe.py`, `nasd_probe.py`, `fwtool.py`, `nasd_sweep.py`,
  `nasd_tap.py`, `nasd_dissect.py`. The dissector was broken until 2026-07-26
  (it read pcapng block types big-endian, so every real capture came back as
  "0 frames"); it is fixed and now reads both live captures correctly.

### What's left

1. ~~**Per-drive temperature + cache battery / fan / PSU / performance.**~~
   **MOVED OUT OF PHASE 1 — 2026-07-25.** Two live captures of the real
   Dashboard proved these are not obtainable the way we assumed. See
   [Parked](#parked--not-possible-on-what-we-know-today). Nothing in Phase 1
   waits on them any more.
2. **The native Windows app is built and running** (`windows/DroboDashboardReborn/`,
   WPF, on .NET 8 until 2026-10-08 and .NET 10 since). It reads the agent's API and shows device identity,
   capacity, per-bay disk health, and alerts (worst-first, severity chips, a
   calm empty state when there's nothing wrong). WinUI 3 was the original
   target; WPF shipped first because
   it's the toolchain that reliably builds today, and the web dashboard served
   as its layout prototype. Revisiting WinUI 3 is optional polish, not a gap.
3. **MSI installer + auto-updater** — "keeps working as Windows moves on" is the
   point of the project.
4. **Decode the remaining status codes** — exact meanings of `mStatus` /
   `DNASStatus` / non-16 `mDiskState`. The parser fails safe (unknown → warning)
   until a degraded array can be observed.

Phase 1's core — *find the Drobo and show its real health and capacity* — now
works, and the native app exists too. With the sensor extras moved to Parked,
what actually remains is packaging, the status-code meanings, and surfacing the
health fields we already receive but don't yet display (`mErrorCount`,
`mDiskPackStatus`, `DNASStatus`, `mRelayoutCount`, `mDoubleDegradedCnt`,
`SSDLifeRemaining` — no protocol work needed, the data is already arriving).

### Phase 1 scope additions — approved 2026-07-23

Firmware-discovered features the maintainer pulled into Phase 1. All are stock
firmware (no fork), and all are *read status* — they fit Phase 1's "show health"
definition. They are built now against the mock driver and light up the moment
`Drobo5NDriver` works.

| Feature | Command | Note |
|---|---|---|
| Cache-battery health | `eCmdCacheBattery` | Real failure point; gets its own alert rule |
| Device event log | `eCmdGetEventLogs` | The Drobo's own history — more authoritative than our polling |
| Fan / PSU health | `eCmdGetFanInfo`, `eCmdGetPowerInfo` | Completes the hardware-health picture |
| Performance stats | `eCmdGetPerformance` | Throughput, graphed beside temperature |
| Manual discovery by IP | `eCmdAddManualDiscoveryIP` | Reach a Drobo when mDNS fails on Windows |
| DroboSync **status** | `eCmdGetDroboSyncSettings`, sync logs | Show sync state/logs now; the *trigger* waits on a second unit |

### Critical path — CLOSED 2026-07-26

> This section was wrong twice in a row, which is worth keeping on the record
> rather than quietly rewriting.
>
> **Round 1 (2026-07-24):** originally assumed the *only* way to read the
> Drobo was to solve the client→device request header on port 5000 — a
> Wireshark capture, then a dissector, then a driver. Wrong: the device
> volunteers a full status greeting unprompted, no header or login needed, so
> the read side was unblocked without any capture.
>
> **Round 2 (2026-07-25):** this section then said a capture was still the
> one remaining maintainer-gated step, just for the smaller remainder — temperature
> and the other command-only fields — via the diagram below. Also wrong. The
> command channel's header and login were recovered directly: one capture of
> Dashboard clicking *Identify* confirmed the frame shape, then sending
> commands ourselves and reading the replies filled in the rest. **No dissector
> and no "plug the header into the client" step ever happened.** And
> temperature specifically needed none of this — it was blocked by a parsing
> bug in our own code, not a missing header; see
> [Parked](#parked--not-possible-on-what-we-know-today).

The plan as it stood, kept for the record — none of these arrows are how it
actually happened:

```
[DONE] greeting read path → Drobo5NDriver → agent live → WPF app live
[USER: Wireshark capture]  ──►  [dissect capture → header spec]  ──►
[plug header into nasd client]  ──►  [temperature / battery / fan / PSU /
                                       performance / event log go live]  ──►
[MSI + updater]
        ▲
   hardware-gated: only the maintainer can produce this node
```

**What actually shipped:** `sdk/drobo_nasd/config_cmd.py` and
`sysinfo.py` talk to the real command channel (TCP 5001, `DRINETTM` framing, a
220-byte login carrying the device serial twice) directly against the live
5N — see `docs/protocol-map.md`, "THE COMMAND PROTOCOL — SOLVED".
`tools/nasd_sweep.py` then swept 39 commands under that same plain login; see
`docs/command-surface.md` for the results.

What became of the two work packages dispatched off this now-closed path:

- **WP-A — `nasd` client library.** Superseded by the working implementation
  above. The version this work package actually produced
  (`sdk/drobo_nasd/client.py`, built on `framing.py`'s `NasdHeader`
  candidates) guessed at a header shape that turned out not to match the real
  one, and was never reconciled with it — that class still cannot talk to a
  real Drobo. It stays in the tree for its test coverage of the
  command/envelope/framing pieces that don't depend on the header, not as a
  path to real hardware.
- **WP-B — capture dissector (`tools/nasd_dissect.py`).** Turned out
  unnecessary — the header was recovered without it. It also carried a real bug
  for its whole life, fixed 2026-07-26: it parsed pcapng block types as
  big-endian when every capture tool writes little-endian, so it reported
  "Read 0 link-layer frames" on every capture ever tried. It works now; the two live
  captures on file were analysed with a direct parser instead (see
  `docs/protocol-map.md`'s "Tooling note").
- **WP-C — the scope-addition features below**, built against the mock —
  unaffected by any of this, and now landing for real as the command channel
  gets used for more than temperature.

MSI packaging, mentioned as a downstream node in the old diagram, is tracked
in [What's left](#whats-left) above and was never actually blocked by
anything in this section.

### Rules for this phase

- **Read-only against the hardware — with one narrow, named exception.**
  Amended 2026-07-27 at the maintainer's request. No writes to the pack, ever. No
  firmware. The exception is `commands.SAFE_WRITES`, described below.
- **No firmware rewrite.** Explicitly out of scope, by the maintainer's instruction.
- **Never expose the Drobo to the internet.** LAN only until Phase 5.
- **The `nasd` library must never claim to reach real hardware** until a capture
  confirms the header. Guessed layouts are marked as guesses, not facts.

### The one exception to read-only

For its whole life this project only read. `eCmdIdentify` — blink the front
lights so you can tell which box is which — was the first command it would
send that changes anything, and it stops on its own. *(Observed 2026-08-07:
on this unit the device accepts it and the lights do nothing visible; "stops
on its own" is the Dashboard's own "blink for 15 minutes" string, not
something seen. Noted 2026-09-26: that observation covers an empty parameter
and 15 only. The original Dashboard sends `IdentifyInterval` 900, captured
2026-07-25, which has not been sent from here.)* (No longer the only
one: vetted `eCmdSetConfig` writes shipped later — see the correction below,
noted 2026-08-21.)

The boundary is `commands.SAFE_WRITES`, and a command may only enter it if
**all four** hold:

1. It cannot touch the disk pack, the filesystem, or any file.
2. It cannot affect availability — no reboot, unmount or spin-down.
3. It is reversible, or it undoes itself.
4. Getting it wrong costs nothing worse than mild confusion.

Nothing joins that set without the maintainer saying so explicitly.

**Deliberately excluded, and why it matters:** `eCmdSetDimming` and
`eCmdRename` look like obvious next members and are not in the set. Both
*persist* a setting, so both fail test 3 — and when this was written there
was no read-back-and-restore path. **Correction 2026-08-21: that path has
since shipped.** `sdk/drobo_nasd/config_cmd.py` reads a section un-redacted
before every write, sends it through one shared tail (`_send_inner`: confine
to the section's own root, escape once, send, require the accept code), and
the write helpers built on it read the value back afterwards — "ok" means
the device reads back the new value, not merely "accepted". Brightness and
share-access writes ride that path in production (agent endpoints + the WPF
Advanced panel, commits 8a7ec58/9b838d1, 2026-08-19). The two commands named
above still stay out of `SAFE_WRITES`, for a better-informed reason: the
2026-08-05 capture showed the Dashboard never sends either one — brightness
and rename both persist through `eCmdSetConfig` documents instead (see
`docs/protocol-map.md`), and `eCmdSetConfig` is gated by the stricter
`VETTED_WRITES` set in `sdk/drobo_nasd/commands.py`, not this one.

`sdk/tests/test_identify.py` names nine destructive commands and asserts each is
still refused, so a change that quietly promotes one fails loudly rather than
silently widening the boundary.

**Parameters are never guessed.** The firmware table gives command ids and
names; it does not document their `<Params>`. As first written — when
identify was the only write — every command this project sent used an empty
parameter block, because that is what the device demonstrably tolerates. A
guessed parameter is an unreviewed instruction to hardware that may hold
someone's only copy of their data. If a command needs parameters, the answer
is to capture the original Dashboard sending it, not to guess byte layouts —
and since the 2026-08-05/06 captures that is exactly what happened: the
config writes send full parameter documents in the captured format, and
commands with no captured `<Params>` still go out empty (noted 2026-08-21).
One deliberate exception, noted 2026-09-26: Identify sends `IdentifyInterval`
15, this project's own guess, although the value captured from the original
Dashboard is 900. `identify.py` explains the choice, which was made while the
capture was forgotten. The default changes only after 900 has been sent while
someone watches the unit.

---

## Phase 2 — Optional features

**Does not start until Phase 1's core functions are confirmed working against
the real hardware.**

Features carried from `docs/archive/BLUEPRINT.md`: dark mode, Windows toast notifications,
background transfer manager, local thumbnail generation, modern installer and
auto-updater, DroboApps management, share and user management.

**On rewriting firmware:** the maintainer is right that some things need it. Which
ones, specifically, are marked in
[Features discovered in the firmware](#features-discovered-in-the-firmware)
below. Firmware work stays in its own fork and needs a **sacrificial second 5N**
first — see `docs/archive/BLUEPRINT.md` §9. The reason is not caution for its own sake:
BeyondRAID's on-disk format is undocumented, so custom firmware cannot read an
existing disk pack.

---

## Phase 3 — Mobile: connect and upload

**Definition of done:** an app on the maintainer's iPhone, installed without the App
Store, that connects to the Drobo and uploads photos and videos automatically.

Everything on the agent side is **built and tested**: pairing (with QR),
hash-first dedupe, resumable chunked upload for large videos, capture-date
preservation across resumes.

**Hard requirement: a Mac.** Xcode is macOS-only. Nothing else blocks this.

Full detail, including what iOS background execution really permits, is in
[`ios/README.md`](ios/README.md). The short version: **backs up when you get
home and catches up overnight** — no iOS app can do better.

---

## Phase 4 — Drobo Pix REBORN

The most ambitious phase, and the one with real unsolved questions. Answers to
the ones asked:

### HEIC — convert, or interpret?

**Neither, exactly. Store the original and derive on demand.**

Converting HEIC to JPEG on upload is lossy and irreversible — you would be
throwing away quality permanently to solve a viewing problem. Instead:

- **Upload the original HEIC.** Smaller and lossless. This is the archive copy.
- **Generate a JPEG derivative for viewing**, cached alongside. Safari displays
  HEIC natively; Chrome and Firefox do not, so a web gallery needs the JPEG.
- **Offer JPEG on export/share**, converted at that moment.

The phone is the right place to make derivatives — iOS has hardware HEIC
decoding, and the agent is stdlib-only Python with no image libraries.

### Metadata — can it be retained?

**Yes, if we upload the original asset resource rather than a rendered image.**
The distinction matters: asking PhotoKit for a `UIImage` re-encodes and strips
EXIF; asking for the `PHAssetResource` gives the untouched original file with
all EXIF intact.

Caveat worth knowing: some things live in the Photos *database*, not the file —
albums, favourites, keywords, and non-destructive edit adjustments. A true
iCloud replacement backs up the original **plus** the adjustment data.

### Live Photos — can they be saved properly?

**Yes, but the current upload API cannot represent one, and this needs fixing
before Phase 4 starts.**

A Live Photo is **two files** — a still (HEIC/JPEG) and a short MOV — linked by
a Content Identifier stored in both files' metadata. Upload them as two
unrelated files and you get a photo and a stray video, with the Live Photo lost.

To preserve one you must:

1. Upload **both** resources (`.photo` and `.pairedVideo`).
2. Preserve the pairing identifier.
3. Re-associate them on restore.

**Design implication:** the agent's photo API is currently one-file-per-upload
with no notion of a relationship. Phase 4 needs an **asset group** concept — one
logical asset, multiple resources. Better to add it deliberately than to
retrofit it after a year of backups.

### Scope honesty

"Competitor to iCloud Photos" is a large target — sync, dedupe, albums, sharing,
search, and restore-to-a-new-phone. Suggested order: **backup first, then
viewing, then send/receive, then the library features.** A backup you can
restore from is worth more than a gallery you can browse.

---

## Phase 5 — Remote access

**Approach: Tailscale on the machine running the agent, and on the phone.** The
Drobo keeps its ordinary LAN address; the phone reaches it from anywhere; no
ports are ever opened.

It cannot run on the Drobo itself — kernel 3.2.96 predates WireGuard's mainline
merge by eight years, and the one community OpenVPN port is broken. It runs on
the PC or a Raspberry Pi as a subnet router.

**This phase can land early.** It's independent of Phases 3 and 4 and mostly
configuration rather than code. If remote access becomes useful sooner, take it
sooner — it is the one ordering exception.

Two things to verify when we get here: whether iOS App Transport Security treats
a Tailscale address (`100.64.0.0/10`) as local, and whether the agent should
serve TLS for the remote case.

---

## Phase 6 — Public release

### This repository is the public one — decided 2026-10-08

On 2026-10-08 the maintainer decided to publish **this repository itself**,
rather than keep exporting a stripped copy of it into a second one. It is
published with a **fresh history**: one first commit of the scrubbed tree.

The old history is not published. It is kept privately, as the working record
of how the protocol was recovered, and is never pushed to the public
repository or forked into it. Commit hashes quoted in these records point into
it and will not resolve publicly.

Every tracked file was scrubbed for publication the same day: no real person
other than the published credits, no contact details, and nothing that
identifies the maintainer's unit, PC, files, network or circumstances.
Fixtures recorded from the real device carry synthetic values in the same
format. **What stays out of the tree, permanently:** Drobo's own binaries and
extracted firmware (`vendor/`); packet captures, which can hold the Drobo
password in plain text (`docs/captures/README.md`); `agent/config.json`, with
its access token (no default token ships; one is generated on first run);
device and disk serial numbers and the disk pack ID; real addresses, host and
network names; and anyone's name, email or user-profile path. `.gitignore`
blocks the file categories, and the privacy guard in `scripts/` and the checks
on every push catch the rest.

The separate code-only repository that carried release 1.0.0 (published
2026-08-10) is **retired**: it gets no further releases, and installers now go
on this repository's own Releases page. Retiring it loses nothing -- the
comparison made on 2026-09-21 of the eight commits it gained after 1.0.0 found
every fix already here. The machinery that fed it is deleted with it: the
export procedure, the derived testers' and public roadmaps and the script that
generated them, and the carry-back check. This file is now published as it
is, and every push to this repository carries `CHANGELOG.md`.

This replaces two earlier decisions, recorded here in brief. On 2026-07-26 it
was decided that publication would come from a clean, new repository rather
than this one; on 2026-08-10 (amended 2026-08-28, 2026-09-09 and 2026-09-21),
that each release would be a code-only export into that separate repository.
The working history stays private.

**The licence, settled 2026-10-19: GPL-3.0-or-later.** A LICENSE file was
deferred by the maintainer on 2026-09-04, and the project stayed all rights
reserved until then; GPL-3.0 and PolyForm Noncommercial were the two
candidates discussed. The maintainer chose GPL-3.0-or-later on 2026-10-19:
the verbatim text is `LICENSE` at the top of the repository, the README's
License section and the SDK's `pyproject.toml` say the same, and the
installer copies `LICENSE` into the program folder the way it already copies
`TESTING.md`. The README states plainly that this is unaffiliated with
Drobo, StorCentric, or their successors.

---

## Parked — not possible on what we know today

**Added 2026-07-25, after two live captures of the original Dashboard.**

These sit *after* Phase 6. They are not scheduled, not blocking, and nothing in
Phases 1–6 depends on them. Revisit only once the shipping product is done —
this is exploration, not a milestone.

### Why they're parked

We installed Drobo Dashboard 3.5.0 and recorded every packet between it and the
live 5N, twice. Both sessions agreed exactly:

- **Dashboard sends zero payload bytes.** 352 and 364 packets respectively, all
  bare TCP handshake and ACK. It never asks the Drobo for anything.
- The Drobo pushes one document, `<ESATMUpdate>`, 288 times per session,
  always `ver=1.1` and the same length, over TCP 5000 and nothing else.
- A sweep of all 90 element names in that document finds **no battery, fan,
  power, PSU, voltage, RPM or thermal field**. Only `mTemperature`, which the
  device reports as `0` on every slot.

So this was never a parsing gap, and — importantly — it is **not** the
"client→device command framing" problem we spent time planning around. That
framing is not used on port 5000 at all. **We already receive everything the
original Dashboard receives.**

### What's parked

| Feature | Status |
|---|---|
| ~~Temperature~~ | **UNPARKED 2026-07-26 — built and shipping.** See below. |
| Per-drive temperature | Device reports `0` per slot. Appears not to exist on a 5N; the chassis reading replaces it. |
| Cache battery health | No such field in the pushed document; command answers empty. |
| Fan health / RPM | No such field; command accepts the login then goes silent. |
| PSU health | Same as fan. |
| ~~Performance / throughput stats~~ | **UNPARKED 2026-07-26 — built and shipping.** `eCmdGetPerformance` answers on a plain login, returning `Iops`/`ReadThroughout`/`WriteThroughout`/`TierIOps`. Wired into `Drobo5NDriver`, fetched on the same connection as the temperature. Counters confirmed to move under real load; units are megabytes/second (inferred, not stated) and they lag by ~25s in both directions. See `docs/command-surface.md`. |
| Device event log | No such field in the pushed document, and `eCmdGetEventLogs` (83; this said 90 until 2026-09-26) answers empty. **Update 2026-08-05:** that was the wrong command — the Dashboard uses `eCmdGetUserEventLog` (91) with `<Path>/uel.log</Path>` (see `docs/protocol-map.md`). **UNPARKED 2026-08-21 — the fetch is built and shipping** (`sdk/drobo_nasd/eventlog.py`); only the reply's per-line ts/severity format remains undecoded. |

### Temperature came back — and the reason it was ever "impossible" was our bug

`eCmdGetSysInfo` needs **no RSA handshake, no key, and no firmware change.** It
returns a real chassis temperature, **15 times out of 15**, along with the
device's uptime.

It only looked gated because of how the reply is encoded. `eCmdGetConfig` puts
real XML children inside `<ResultDetails>`; `eCmdGetSysInfo` puts an
**entity-escaped XML document** there as text. Code looking for a
`<Temperature>` element found nothing and logged the command as unanswered —
which is where the earlier "succeeds about 1 in 6" figure came from.

Shipped in `sdk/drobo_nasd/sysinfo.py` with `sdk/tests/test_sysinfo.py`.

**Two lessons worth keeping:**

1. **Check the escaping before concluding a command is gated.** Some of the
   "empty" results in `docs/command-surface.md` were classified by looking for
   elements that may only exist in escaped form. That table may understate what
   this device gives up.
2. **The command port can be worn out — temporarily.** After ~90 command
   connections in an hour, `eCmdGetSysInfo` went from 15/15 to 0/20 while
   `eCmdGetConfig` kept working 3/3 and the array stayed healthy. Not a slow
   read (a 60 s timeout also failed), not our framing, not contention with our
   own agent. Left alone it **recovered by itself after about 30 minutes**, no
   restart needed. So it's a refusal under load, not damage — but the recovery
   window is long enough to matter, which is why the driver asks at most once
   every 120 seconds, keeps the last good reading with its timestamp, and never
   retries a failure in a loop.

All six were built end-to-end before any of this — models, API endpoints,
alert rules, mock driver, UI — against the simulated Drobo, reading UNKNOWN
against the real one until a route to the data existed. Temperature now has
that route (above); performance joined it on 2026-07-26 and the event log on
2026-08-21, so only three (cache battery, fan, PSU) still do. **No code needs deleting.** If a route to any of them
appears, they light up with no further work — performance already has a
partial route, see the table above.

### Status changed 2026-07-25, corrected again 2026-07-26

The command channel was solved (TCP 5001, `DRINETTM` framing — see
`docs/protocol-map.md`). These commands can now be *sent*. This section
originally concluded that a **second, cryptographic authentication step**
gated the interesting ones — measured at the time as `eCmdGetConfig`
succeeding 5/5 with a plain login, `eCmdGetSysInfo` only 1/6, and fan/PSU/
performance never replying, with a captured RSA-style challenge–response
(firmware: `UCrypto::DecodeAndDecrypt`, `<RSAKeyValue>`) sitting between the
login and the commands that failed.

**That conclusion does not hold.** The 2026-07-26 sweep (`docs/command-surface.md`)
sent `eCmdGetSysInfo`, `eCmdGetPerformance`, `eCmdGetDroboAppsStatus`,
`eCmdGetDemoModeInfo`, `eCmdGetUserGroupList` and `eCmdGetDimming` with
nothing but the plain login — no crypto exchange attempted — and all of them
answered with real data. `eCmdGetSysInfo`'s earlier "1/6" figure was never a
crypto gate at all: its reply nests an *entity-escaped* XML document instead
of real XML children, so code looking for a literal `<Temperature>` element
found nothing and logged the command as unanswered (see
`sdk/drobo_nasd/sysinfo.py`, and the corrected section above). The
single reading this section used to cite as proof of a rare,
crypto-gated success was that same misread — not a rare success, a rare
correct parse of a reply the code was getting every time.

Fan and PSU genuinely still never reply — but as a timeout, not a refusal,
which is exactly as consistent with "this chassis has no such sensor" as with
any crypto gate. See `docs/crypto-handshake.md` for where that document's own
"what does the handshake gate" question now stands: unresolved, and possibly
"nothing this project has tried."

### The way they could still come back

**1. TCP 5001 has been explored, not left alone.** `docs/command-surface.md`
swept it with a 39-command allowlist under a plain login and found several of
the health-adjacent commands (performance, DroboApps status, demo-mode
flags, dimming) answer with real data; cache battery, controller/expander
card, event logs and the rest came back empty or (fan/PSU) timed out. Sending
anything further still means writing bytes to the single unit available, so
expanding the allowlist stays a deliberate decision, not casual
experimentation — but "unexplored" is no longer accurate.

**2. They may not exist on a 5N.** The `eCmd*` table was recovered from firmware
shared across the whole Drobo line, including business models with redundant
PSUs and genuine cache batteries. A command existing in the binary is not proof
this chassis has the sensor or reports it. This is the likelier explanation.

Custom firmware is *not* an obvious answer either: firmware cannot invent a
sensor that isn't wired. It could potentially read SMART directly from the
drives — which is where temperature would most plausibly come from — but that
belongs to the firmware fork in `docs/archive/BLUEPRINT.md` §9, behind its own hard
prerequisites.

### What we do instead

Surface the health signals the device *does* send and we currently discard:
`mErrorCount` (per drive), `mDiskPackStatus`, `DNASStatus`, `mRelayoutCount`,
`mDoubleDegradedCnt`, `SSDLifeRemaining`, `mSledStatus`,
`mRealTimeIntegrityChecking`, and the device's own `mRedThreshold` /
`mYellowThreshold` capacity levels. These need no protocol work at all — the
data is already arriving in every poll.

---

## Features discovered in the firmware

*(Status 2026-09-04: most rows of the "Available on stock firmware" table
below were approved on 2026-07-23 and have shipped — Identify, performance,
the device event log via `eCmdGetUserEventLog`, email alerts, DroboApps
start/stop, the configuration writes. The preamble is kept as first written.)*

Everything below was found by reading the official firmware, and is **not yet
approved for the build**. Per the maintainer's instruction, these are recorded here
and await a yes or no.

### Available on stock firmware — no firmware rewrite needed

| Feature | Command | Why it's interesting |
|---|---|---|
| **Identify / blink lights** | `eCmdIdentify` | Makes the unit flash so you can tell which box is which. Trivial, genuinely useful. |
| **LED dimming and night mode** | `eCmdSetDimming`, `eCmdToggleNightMode` | The original could dim its lights on a schedule. Nice if the Drobo lives in a bedroom. |
| **Standby / spin-down** | `eCmdStandby` | Park the drives. Saves power and wear. |
| **Fan, PSU and battery health** | `eCmdGetFanInfo`, `eCmdGetPowerInfo`, `eCmdCacheBattery` | The cache battery is a real failure point on these units and nothing currently watches it. **Recommended.** |
| **Performance statistics** | `eCmdGetPerformance` | Throughput graphs alongside the temperature graph. |
| **Device event log** | `eCmdGetEventLogs`, `eCmdGetUserEventLog` | The Drobo's own history of what happened — more authoritative than our polling. **Recommended.** |
| **Email alerts** | `eCmdSendTestEmail`, email alert config | The original could send SMTP alerts. Our agent uses ntfy; email is a good second channel. |
| **Diagnostics bundle** | `eCmdUploadDiags`, `eCmdGetTMDiagFilePath` | Collects the device's logs. Invaluable when something goes wrong. |
| **DroboSync** | `eCmdGetDroboSyncSettings`, `eCmdSyncNow`, sync logs | **Drobo-to-Drobo replication.** The original could mirror one Drobo to another — a genuine backup answer to the BeyondRAID lock-in problem. Needs a second unit. |
| **Manual discovery by IP** | `eCmdAddManualDiscoveryIP` | Find a Drobo when mDNS fails, which it does on some Windows networks. **Recommended.** |
| **Share and user management** | `eCmdGetSharesConfig`, `eCmdGetUserGroupList` | Create shares and users without the old Dashboard. |
| **Demo mode** | `eCmdGetDemoModeInfo` | The original shipped a demo mode. Ours is the mock driver — worth knowing they had the same idea. |

### Would need the firmware fork

| Feature | Why |
|---|---|
| **TLS on the wire** | Both ends must agree; the device's end is frozen. |
| **Token/session authentication** | Same reason. |
| **SMBv3** | The 5N's Samba is fixed at SMBv2. |
| **WireGuard on the device** | Kernel 3.2.96 predates it. |
| **A modern kernel** | 3.2.96 dates from 2013 and will never be patched. |

### Deliberately not proposed

`eCmdRunESACommand`, `eCmdRunCommand`, `eCmdSendTMCommand`, `eCmdRouter`,
`eCmdReadHostBuffer`, `eCmdWriteHostBuffer` — these look like arbitrary command
execution on a device that will never receive another security patch. They are
documented so they are recognised and avoided, not used.

Firmware flashing (`eCmdInstallFirmware`, `eCmdRevertFirmware`) is cut from
Phase 1 and belongs to the fork.

---

## Can we harvest the firmware from the Drobo over the network?

**No — and we don't need to.**

Three separate findings:

1. **There is no read-out command.** The protocol has `eCmdInstallFirmware`,
   `eCmdRevertFirmware`, `eCmdGetUpdate` and `eCmdGetFirmwareInfo` — write,
   check, and version-query. Nothing returns the image. The function named
   `CommandThread::FirmwareDownload` is misleading: it is the *device receiving*
   a firmware file from Dashboard, base64-decoded into `/dev/shm/firmware.tdf`.
   Traffic flows toward the Drobo, not out of it.

2. **A full dump needs root.** The flash lives at `/dev/mtdblock0`, `2` and `3`.
   Reading them means an SSH shell, which means installing a DroboApp, which
   means DroboApps enabled and a working Dashboard — circular with Phase 1.

3. **We already have it.** The 4.2.1 image is fully unpacked, and it is what
   gave us the protocol, the command table and the connection model.

What the device *could* usefully give up, once Phase 1's protocol works:
`eCmdDownloadAllConfigFiles`, `eCmdGetEventLogs`, `eCmdGetSavedFileContent` and
the diagnostics bundle. Those read files, not firmware, and they are Phase 2
material.

**Worth knowing:** the running unit may be on 4.2.2 while we hold 4.2.1. That
difference is minor and does not block anything. The valuable thing to harvest
from the live Drobo is not firmware — it is **one packet capture of Dashboard's
handshake**, which is the only thing standing between here and a working Phase 1.
*(Superseded 2026-07-25/26: the read path needed no capture — the device
pushes its status unprompted — and the command channel was solved directly
against the device; Phase 1's MVP bar was met and 1.0.0 shipped 2026-08-10.
Kept as written.)*

---

## The measurement that was blocking everything — done 2026-07-25

**This section described the project's critical path for weeks. It is closed.**

Drobo Dashboard 3.5.0 was installed and driven against the live 5N while
`pktmon` recorded all traffic to and from the device (filtered by IP, so UDP
would be caught too). Two sessions, ~2.3 MB each.

The answer was not the one anyone expected: **Dashboard sends nothing.** Zero
payload bytes in both sessions. There was no header layout or login exchange to
recover on port 5000, because the client never speaks — the Drobo simply pushes
`<ESATMUpdate>` on connect and repeats it.

What this closed:

- The "client→device request framing" unknown **on this port** — port 5000
  turned out not to use one at all. (A real request framing does exist on the
  separate command channel, port 5001 — solved the next day, directly against
  the device, with no capture involved. See "THE COMMAND PROTOCOL — SOLVED" in
  `docs/protocol-map.md`.)
- Any dependency of Phase 1's *read* path on a future capture
- The assumption that temperature/battery/fan/PSU were one capture away on
  *this* port — they are not in this data at all. Battery/fan/PSU/performance/
  event-log are still in [Parked](#parked--not-possible-on-what-we-know-today);
  temperature was unparked 2026-07-26, but via the command channel above, not
  via a capture of this one

What it confirmed:

- Our driver already receives **everything** the original Dashboard receives
- The frame format (`DRINASD\0`, `ver=1.1`, big-endian length, NUL-terminated
  UTF-8 XML) is exactly as documented in `docs/protocol-map.md`

The captures are gitignored — they carry device and disk serials, and on an
unencrypted protocol a password typed during capture would land in them in plain
text. The findings drawn from them live in `docs/protocol-map.md`.

*(Superseded 2026-07-25: TCP 5001 was solved that day — `DRINETTM` framing,
the 220-byte login — and has been sent to routinely since; the 2026-08-05/06
and 2026-08-25 captures show the Dashboard using it for every configuration
write. The paragraph below is kept as it was written.)*

**The one remaining protocol unknown** is what TCP 5001 speaks. It is open,
silent on connect, and Dashboard never used it. Exploring it means sending bytes
to the device, so it is a deliberate decision rather than a scheduled task — see
Parked.
