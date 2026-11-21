# Capabilities

What this software is meant to do, and how far each part has got. Kept
against the code at HEAD, not against intentions: an item moves only when the
git history says it has. Last checked 2026-09-26.

Companions: [`development-history.md`](development-history.md) is how it got
here; [`../ROADMAP.md`](../ROADMAP.md) is the plan and its phases;
[`../CHANGELOG.md`](../CHANGELOG.md) is what changed between releases.

**Status words used below**

| Word | Meaning |
|---|---|
| **Complete** | Built, tested, and working against a real Drobo 5N |
| **Built, unverified** | Built and tested against the simulator; not yet proven on hardware |
| **Built, dormant** | Built and tested; does nothing until something outside the code exists (a second Drobo, a manifest URL) |
| **In progress** | Partly built, or built with a known gap |
| **Planned** | Not started; nothing blocks it but time |
| **Blocked** | Needs hardware or a situation this project does not have; see [`testers-tasks.md`](testers-tasks.md) |
| **Out of scope** | Deliberately not going to be built, with the reason |

`../ROADMAP.md` uses four coarser words for the same thing, so the two files
cannot drift apart in vocabulary: **Working** covers *Complete*, **Built,
unverified** covers *Built, unverified*, **Partial** covers *In progress*, and
**Dormant** covers *Built, dormant*. *Planned* and *Blocked* items appear in
the roadmap's Future features and Working on now tables instead.

---

## What it is for

Drobo Dashboard REBORN keeps a Drobo network-attached storage unit usable now
that the company behind it is gone: no vendor, no documentation, no updates,
and a last official Dashboard from December 2019 that Windows will eventually
break. It finds the Drobo on the network, shows what the device knows about
itself, gets you to your files on a modern Windows, tells you when something
is wrong, and makes the handful of configuration changes the original
Dashboard made. It talks to stock, unmodified firmware; it never touches the
firmware or the disk pack.

It is built against one Drobo 5N. Other network models are expected to speak
the same protocol and have not been tried; see the testers' task list.

## The safety boundary

Everything below sits inside this, and it is enforced in code rather than by
convention.

- The firmware understands **103 commands**. **54 names are refused outright**
  in `commands.DO_NOT_SEND`: six arbitrary-execution escape hatches,
  forty-seven destructive ones (format, firmware flash, factory reset, repair,
  and every raw `eCmdSet*`), and, since 2026-11-02, one read that by its name
  returns credentials (`eCmdGetNASUsernamePassword`, which answered empty the
  one time it was sent, 2026-07-27); 52 of them carry a confirmed id in the
  table and two are refused by name alone. An id or name the table does not
  hold counts as refused too (since 2026-11-02). The send helpers check the block list
  themselves, so no code path reaches them by accident.
- **One command is on the safe list** (`SAFE_WRITES`): Identify, which asks
  the front lights to blink. By the original Dashboard's own warning they
  blink for 15 minutes, so both apps say to press Stop when you are done; no
  blink has been seen on the one 5N tested, and that end has never been
  watched (this said it "stops on its own" until 2026-11-20). A command
  joins that list only if it
  touches no file and no disk pack, affects no availability, is reversible,
  and costs nothing worse than mild confusion when it goes wrong.
- **Two commands are vetted** (`VETTED_WRITES`): they stay on the refused
  list for ordinary sends, and each has exactly one sanctioned sender that
  makes it safe by construction. Configuration writes go through a
  read-modify-write transaction that reads the device's live settings block,
  changes one field, echoes the rest back exactly, and refuses if the block
  holds a field this software does not recognise. DroboApp start/stop is
  validated against the list the device itself reports.
- **Every write reports exactly one outcome**, and the agent's names for them
  are `verified` (the device read back the change), `verified_unacknowledged`
  (the device closed the connection instead of answering, but the read-back
  shows the change), `failed` (refused by this software, rejected by the
  device, unreachable, or applied nothing), or `unknown` (it went out and
  could not be settled either way). Never a guess.
- **Nothing is guessed.** Every write format came from a capture of the
  original Dashboard. A permission code whose meaning has not been observed
  is shown by its number; the only codes that can be written are 0 (most
  likely "no access", not confirmed), the confirmed read/write level, or one
  already set on the device. An event-log line whose format
  is uncaptured carries an explicit not-decoded marker rather than an
  invented timestamp.
- **LAN only.** The agent binds to the loopback address by default; the
  Drobo and the agent are never to be exposed to the internet. Remote access,
  when it comes, is a VPN on a separate box. The agent's access token is
  generated with `secrets` and compared in constant time; the SMB password
  crosses to Windows through a direct API call and never a command line.

## The pieces

| Piece | Where | What it is |
|---|---|---|
| Protocol SDK | `sdk/drobo_nasd/` | The Drobo protocol, reverse-engineered: the status document the device pushes on TCP 5000, and the command channel on TCP 5001 with its framing and login. Standard library only. |
| Agent | `agent/` | An always-on Python service that speaks the protocol in exactly one place and exposes a local HTTP/JSON API (port 7420), a browser dashboard, alerts and notifications, backup, a photo-upload API, and an on-demand update check. |
| Windows app | `windows/DroboDashboardReborn/` | A native WPF app (.NET 10) over the agent's API. Five tabs: **Shares**, **Status**, **Alerts**, **Settings** (The Drobo, which also shows this app's version and the Drobo's firmware; Preferences), **Tools**, where the Advanced Configuration card sits behind the admin login. |
| Installer | `windows/installer/` | A per-machine MSI installing to Program Files, bundling its own Python runtime, with Start Menu, optional desktop shortcut, an Uninstall entry, and a testing guide. |
| Tools | `tools/` | Packet-capture, firmware-analysis and command-sweep tooling used to map the protocol. Read-only toward the device. |

## Capability inventory

### Finding the Drobo

| Capability | Status | Notes |
|---|---|---|
| Discover by permanent serial: mDNS, hostname, last-known address, optional bounded subnet sweep | **Complete** | Remembers the device by serial, so a new DHCP address still resolves |
| Refuse an impostor answering on the same address | **Complete** | Identity mismatch is an explicit failure, never silently accepted. Checked at startup and by discovery; until 2026-11-02 a poll did not compare the serial it read at the remembered address, so a Drobo given that address by DHCP would have been trusted and logged in to. Every poll now compares it and runs discovery again on a mismatch, and until ours answers again the agent's command routes (shares, settings, DroboApps, Identify, every write) answer 503 rather than log in to the stranger (tested against simulated greetings, not seen on hardware) |
| Typed address (manual discovery) for networks that filter mDNS | **Built, unverified** | Graded Complete until 2026-11-20, which was wrong: the address was checked and came back with the device's own name, but nothing made the agent watch it, so Connect opened a dashboard that stayed unreachable (readiness M2). Since 2026-11-20 `POST /api/drobo/select` reads the address's status greeting (no login, nothing that changes the Drobo), checks the serial, keeps the address first in `manual_discovery_ips` (or replaces a fixed `host`) with `expected_esa_id`, and switches the running agent with no restart. The Windows app's Connect calls it, and the browser dashboard has a "Find the Drobo" address box while the Drobo cannot be reached. Tested against loopback fakes in `test_agent.py`; not yet tried in the WPF app or on real hardware |
| Choosing between two or more Drobos in the picker | **Built, unverified** | Until 2026-11-20 Connect opened whichever Drobo the agent already watched, whichever row was clicked (readiness S8). A row's Connect now names the serial its device reported, and only a request that names a serial switches the agent to a Drobo that is not the paired one; a typed address of another Drobo is refused with both serials named, and the browser then offers a deliberate second button. A switch forgets the previous Drobo's alerts and graph with no notification, so its standing alerts are never sent as "Resolved". Choosing the Drobo already being read, and answering, changes nothing, so the app's reconnect at every launch never rewrites `config.json`. Until 2026-11-21 a page or window already open on the first Drobo sent its changes (share access, share create or delete, DroboApps, brightness, spin-down, Identify) to whichever Drobo the agent watched when they were sent, so a delete armed on the first could remove the second's share of the same name. Now every change carries an optional `esa_id`, the serial of the Drobo it was made against, and the agent refuses one made against a Drobo it no longer watches (409, `"reason": "another_drobo"`, nothing sent); both apps send it, share changes with the serial `/api/shares` now reports for its list, and a reading of another Drobo makes both put away what they read from the first (the browser disarms an armed delete and reads its shares and settings again; the Windows app clears its DroboApps list and re-reads the Advanced Configuration card). A change with no `esa_id`, as from a script, still goes ahead. Tested against two loopback fakes and the simulator, with every SDK write a recorder; never with two real Drobos, and the Windows app's half not yet on a PC |
| "This PC is on a different network" diagnosis | **Complete** | Replaces a misleading "cannot reach the Drobo" |
| Starting before the Drobo can be found: the Drobo off or still booting, or this PC's own network not up yet | **Built, unverified** | The agent starts anyway: it serves the dashboard, raises the critical "cannot reach" alert, and runs discovery again on every poll until the Drobo answers, with no restart. Until 2026-09-26 it exited instead whenever automatic discovery found nothing, so an agent started at boot on a PC that came up before its Drobo monitored nothing at all. A device that answers with a different serial still stops it, now with a message instead of a traceback. `run_agent.py --once`, the one-reading check, answers the same cases with the same reasons and exits 1; until 2026-11-06 each of them ended it in a traceback, because the 2026-09-26 fix changed only how the agent starts. Tested against simulated discovery, including the Drobo turning up later (2026-09-26); not yet seen on a real boot |
| A bounded time limit on frame reads from the device | **Built, unverified** | Something on the network that answers and then never finishes replying used to be able to hold a read open for hours, because a socket timeout bounds each read attempt rather than the whole read. On the status channel that was enough to wedge the device picker until a restart, since the agent holds the discovery lock across that read. The status channel (`esatm`) and the command channel's settings reads and writes (`config_cmd`'s own reads) now give each frame one deadline covering header and payload together (2026-09-19). The other command-channel requests do not: temperature and uptime (`sysinfo`), the event log, Identify, DroboApps and DroboSync call `_read_frame` with no deadline and rely on the per-read socket timeout alone. Extending it to them is open work; until 2026-09-26 this row said both channels were covered. Proven against loopback sockets that behave that way, not against a real device doing it |

### Reading the device

| Capability | Status | Notes |
|---|---|---|
| Identity: name, model, serial, firmware, with an advisory on the withdrawn 4.3.x builds | **Complete** | |
| Per-bay drive health: state, model, serial, capacity, error count, SSD life, drive firmware | **Complete** | Only state 16 reads as healthy. A bay is first judged empty or not from `mStatus` 128 or no reported size; a present disk reporting any other state, 0 included, reads as a warning and raises its bay's drive warning once. Until 2026-11-20 a present disk reporting 0 was badged empty and raised no alert; the fix is tested against the sanitised status sample with one bay's code changed, and a populated bay reporting 0 has not been seen on hardware |
| Capacity: raw, usable, used and free; what is held back for protection where the Drobo reports it | **Complete** | A 5N's status greeting carries no protection figure, so the agent sends `protection_reported` false beside a `protection_bytes` of 0, and the browser dashboard, the Windows Status tab and the diagnostics report say "not reported by this Drobo". Until 2026-11-20 this row claimed protection without qualification, and all three printed "Protection 0 B" beside real figures, which reads as unprotected. Only the simulated Drobo gives a figure. The Windows helper behind both Windows views is tested on every push; seeing the Status tab draw the words needs Windows |
| Volumes (what Windows shows as drive letters) | **Complete** | |
| Chassis temperature and uptime, always with the age of the reading | **Complete** | Per-drive temperature reads 0 on a 5N; the chassis reading stands in, in the browser dashboard's 24-hour temperature graph too since 2026-11-02 (it plotted drive readings only, so on a 5N it was always empty) |
| Throughput and IOPS, with the age of a kept reading | **Complete** | Units inferred as megabytes per second; counters lag ~25 s. A failed fetch keeps the last figures, which carry `measured_at` (the time the Drobo sent them) since 2026-10-19; both UIs show their age once it passes three minutes, and the Windows diagnostics report gives the time. Before that, kept figures looked fresh. The Windows side is compiled by CI's Windows job on every push, and on 2026-11-05 it was built into the 1.2.0 MSI, installed and launched by the installer's elevated lifecycle test; no check looks at the age line itself |
| Pack health: relayout and double-degraded counts, rebuild notice | **Complete** | |
| Network configuration (read-only) and port speed | **Complete** | |
| Shares, with each share's per-user access list | **Complete** | Code 1 = read/write confirmed; code 0 shown hedged; code 2 never observed |
| Installed DroboApps and their state | **Complete** | |
| LED brightness and spin-down delay (read) | **Complete** | |
| The device's own event log | **In progress** | Fetch is complete; the per-line timestamp/severity format is uncaptured, so those fields are marked not decoded |
| Meanings of the pack status codes (`mStatus`, `DNASStatus`) | **Blocked** | Needs a degraded array to observe; unknown codes read as a warning meanwhile. The warning names the only healthy values seen, from one Drobo 5N on firmware 4.3.1, says that on another model or firmware the code may be normal, and asks for a report; until 2026-11-20 it said the code had "changed ... different from before", untrue on a unit seen for the first time. Nothing is refused by model |
| Cache battery, fan and PSU readings | **Blocked** | A 5N answers empty or times out; the firmware appears to have no code path for them. Alert rules and UI exist and light up if another model reports them |
| DroboSync (Drobo-to-Drobo replication) status | **Built, dormant** | Reports "not configured" honestly on one unit |

### Alerts and notifications

| Capability | Status | Notes |
|---|---|---|
| Alerts, worst-first, with history and a calm empty state | **Complete** | Drive failure/warning, capacity, temperature thresholds, PSU and fan |
| Desktop notifications (tray balloon) | **Complete** | Off by default; switched on in Settings |
| ntfy and email alerts sent by the agent | **Built, unverified** | Both off by default and each switched on separately. With `use_tls` on, email speaks STARTTLS on every port other than 465; port 465, since 2026-11-02, uses implicit TLS whether `use_tls` is on or off; neither checks the server's certificate (recorded in the roadmap for a decision). Built, but thinly tested: email's tests cover that the password cannot leak, that the mail library is never loaded while email is off, and how `use_tls` written as text is read, and ntfy's cover that a failed push is reported without its topic and does not stop the other backends, and that the topic is quoted into the address. No message has ever been sent to a real ntfy topic or through a real mail server. Email has a "send a test email" endpoint and a button in the app; ntfy has neither, so the only way to try it today is to cause an alert |
| End-to-end notification simulation without a fault on real hardware | **Complete** | |
| A simulated Drobo account, user `admin` with password `admin`, so the sign-in flow can be tried without hardware | **Complete** | Simulator only. The Connect dialog asks the simulator to check the login, and nothing simulated is ever handed to Windows |

### Getting to your files

| Capability | Status | Notes |
|---|---|---|
| Connect to a share as a named user, which Windows 11 requires now that it refuses the Drobo's guest logon | **Complete** | The password goes straight to Windows through a direct API call, never a command line |
| Save that login on the PC, so it is not retyped every session | **Built, unverified** | Tested against the real Credential Manager; not yet proven in use. Written to Windows Credential Manager, encrypted by Windows under the signed-in account and removable by the owner in Control Panel. Optional and off unless ticked. Works with or without a mapped drive letter. The save, replace, find and forget round trip runs against the real Credential Manager, under a test-only `.invalid` name, in the app's own test program; it passes locally and in the automatic checks on a push (first green run 2026-09-20). Not yet proven: signing out and back in, then reaching a Drobo share on the saved login without being asked again |
| Mount as a drive letter, open, disconnect | **Complete** | |
| In-app file browser for a mounted share | **Complete** | |

### Changing the device

| Capability | Status | Notes |
|---|---|---|
| Identify (blink the front lights) | **In progress** | The command is sent and the device accepts it, verified on hardware. On this unit the lights did not visibly change for an empty parameter or for 15, the default this software sends (2026-08-07). The original Dashboard's press was captured on 2026-07-25 carrying `IdentifyInterval` 900, which has never been sent from here. Sending 900 while watching the unit is the next step, and needs no capture. If it blinks, the default changes from 15 to 900. Until 2026-11-20 the Windows app, the browser dashboard and the agent's reply all told the user the lights should now be blinking (this row said the opposite until 2026-09-26). By the maintainer's decision of 2026-11-20 (D4) none of them promises blinking any more: before a click, both apps say that on the one 5N tested the lights did not visibly change at the setting this app sends and ask the tester to report; the reply says the same at interval 15, calls any interval not watched on hardware (900 included) untried, and on the simulated Drobo says there are no lights to watch. The browser button reads "Identify", as in the Windows app. Until 2026-10-29 the reply also credited the original Dashboard with the value just sent, which was untrue for 15. It now names the value sent and says the original Dashboard sent 900, and the help text attributes the 15 minutes to the original Dashboard's own warning |
| LED brightness | **Complete** | Through the vetted configuration transaction; verified on hardware |
| Drive spin-down delay, including a correct "off" | **Complete** | The device coerces a bare delay of 0; off is the feature bit and the delay together |
| Per-user share access | **Complete** | The write path is verified on hardware (2026-08-12). What hardware confirmed is the Everyone row: this unit reports no user accounts at all, so a populated user list has never been seen on a real device and the named-user paths are exercised only against the shape the fixtures use. Since 2026-11-20 both apps say so beside the access editor, in one sentence: only the Everyone row is proven, and named-account rows and share changes on a Drobo with user accounts are untried. Writes are limited to the confirmed read/write level (1), code 0 (most likely no access, but not confirmed; written because no access is the safe direction to be wrong in), or a code already live on the device — nothing is invented |
| Start or stop an installed DroboApp | **Built, unverified** | Wire format captured in both directions (2026-08-06) and validated against the device's own app list. No record shows this project's `droboapps_action` ever sent to a device; graded Complete until 2026-09-26. When the connection fails after it opened (a hang-up with no result, or a timeout), the answer has said since 2026-11-02 that the action may have been taken, in amber in both apps; until then a hang-up dropped the connection and both read as nothing sent. A hang-up during the read-back after an accepted action is answered "accepted, unconfirmed" since 2026-11-02; until then it too dropped the connection. Since 2026-11-20 both apps say beside Start and Stop that they have not yet been tried on a real Drobo, and ask for a report if one misbehaves |
| Create a share | **Built, unverified** | Applied to a real device once; the device answered by closing the connection, which is now decided by the read-back. Not re-run on hardware since that fix. Since 2026-11-20 both apps say so beside New share, advise a throwaway name, and add that share changes on a Drobo with user accounts have not been tried (a warning in words, not a refusal: every share write resends the user list as read, and a populated one has never been seen) |
| Delete a share (name typed twice) | **Built, unverified** | Never sent to a device. Since 2026-11-20 both apps' delete confirmations say so, advise trying it first on an empty share made for the purpose, and add the same user-accounts warning as create |
| Rename the Drobo | **Planned** | Mechanism captured: the whole network block is resent, and a partial document would blank the omitted settings |
| Create or delete users | **Blocked** | Wanted -- decided 2026-09-21. Never observed on the wire: this unit reports no users at all, so there has been nothing to watch, and a write is never guessed. Waits on a recording of the original Dashboard creating a throwaway user, and a second one deleting it; the capture script's steps cover both |

### Backup and photos

| Capability | Status | Notes |
|---|---|---|
| Back up the Drobo to an external drive: plan first, confirm, then copy with bounded retries and progress | **Built, unverified** | Nothing in it speaks the Drobo's protocol: it plans, then runs robocopy between two paths, and only on a separate explicit call. `agent/tests/test_backup.py` copies real files between ordinary folders, and that part runs only on Windows -- on every push, since the suites run on a Windows runner (52 checks there; 42 elsewhere, where the copy is skipped). No record shows a copy ever made from a real Drobo share, and no full array has been copied. The roadmap graded it Working until 2026-10-29, a split left by the 88bedd2 merge; settled as Built, unverified because *Complete* means working against a real Drobo 5N, and the source this is built for, an SMB share on the Drobo, has never been read by it. One copy from a real share to an external drive moves it |
| Photo-upload API: pairing by QR code, hash-first dedupe, resumable chunked upload for large video, capture-date preservation | **Built, dormant** | Complete and tested on the agent side; waits for a phone client. Pairing gives the phone a token of its own, not the agent's admin token: the QR code carries a one-time code (ten minutes, one claim, withdrawn after five wrong ones), the phone trades it at `POST /api/pair/claim` for a token that opens the seven photo-upload routes and is refused on every other, and the agent keeps only that token's SHA-256, beside `config.json`. The paired phones are listed and revoked by the admin (`/api/photos/devices`). Until 2026-11-05 `/api/pair` answered with the admin token itself, and its QR code was a sign-in link to the whole dashboard; nothing used pairing yet |
| Local thumbnail generation | **Planned** | |

### Keeping it running

| Capability | Status | Notes |
|---|---|---|
| Diagnostics report, leading with *why* the Drobo was unreachable | **Complete** | No passwords and no token. Since 2026-11-20 the default copy, on the clipboard and in the saved file, is masked: the Drobo's serial, name and address, every drive serial, volume names, event-log messages and the PC's addresses and networks become placeholders such as `SERIAL-1`, `DRIVE-SERIAL-1` and `ADDRESS-1`, and so do addresses, emails, UNC servers and shares, `.local` names, the Windows user name in a profile path and both serials of an identity mismatch inside free text; the Drobo remembered in `settings.json` is masked too, even when the reply's device block is empty. The unmasked copy is a separate, labelled button ("Unmasked copy (not for posting)"), and the saved file no longer carries a second, raw copy of the JSON. Until then the clipboard held the raw JSON and the file every identifying value twice, and testers were asked to remove them by hand (readiness S12). The masking and both reports are checked by the Windows test program, which runs off Windows; the buttons have not yet been clicked on a PC. The agent's own window is not masked, so `TESTING.md` and the Tester report form still ask testers to read what they paste. Until 2026-11-02 a reply with no device in it (the agent's first seconds) lost the *why* section and said the reply was not JSON, and an unknown uptime read "just started"; both are covered by the Windows test program now |
| The whole of an unexpected error, on screen and in `app.log` | **Built, unverified** | Since 2026-11-20 (readiness S13). The Windows app's last-chance handlers showed the message alone and dropped unobserved task errors. The dialog now shows the type, message and stack (up to 6000 characters, Ctrl+C copies it), masked as the diagnostics are because it asks to be posted; the full text, with the time and version and with only the token and passwords removed, is appended to `%LOCALAPPDATA%\DroboDashboardReborn\app.log`, cut to its newest half past 1 MB and kept on uninstall. The formatting, scrubbing and trimming are tested off Windows; the handlers firing in the running app have not been seen on a PC |
| One agent per port: a second agent says why it closed | **Built, unverified** | Since 2026-11-20 the agent claims its port before it looks for the Drobo, so a second one closes before any discovery or Drobo read. A port another agent holds gives "An agent is already running on port N; this one will close." and exit status 3, which `Run-Agent.bat` treats as nothing wrong and closes its window after 10 seconds; a port another program holds gives "Another program is using port N; change agent.port in <config path>." and exit status 1. It tells them apart by asking `/api/ping` on the port, waiting long enough for an agent that is itself still starting. Until then a second agent died in an `OSError` traceback on Linux, and on Windows `SO_REUSEADDR` let it bind beside the first; the server now leaves `SO_REUSEADDR` off on Windows and `SO_REUSEPORT` off everywhere. Tested on loopback in `test_agent.py` (a running, a still-starting and a foreign-held port); the Windows bind is checked only on CI's Windows runners, which had not run that check when this was written (2026-11-21), and not yet on a tester's PC. A changed `agent.port` is followed by the agent and the installed launcher but not by the Windows app, whose AGENT box starts at `http://127.0.0.1:7420` at every launch (readiness C3, not fixed); `TESTING.md` says to set it by hand and press Connect, or rather to close the program holding the port and report which one it was |
| Update check, on demand only | **Built, dormant** | Off until a manifest URL is set; verifies a checksum; never nags |
| MSI installer into Program Files for all users, bundling its own Python runtime, with upgrade-in-place, an Uninstall entry, and a bundled testing guide | **Complete** | A lifecycle test installs, launches, uninstalls and upgrades it for real. The per-machine package was run on 2026-09-18 from an elevated shell, against a build of this tree: 53 checks, all passing — install into Program Files, launch of the installed copy through its own launcher against the simulated driver, removal, and the no-desktop-shortcut opt-out cycle. Run again on 2026-09-21 with the upgrade leg (`-UpgradeMsiPath`, a 1.1.1 build over 1.1.0): 64 checks, all passing, including the planted user settings surviving the upgrade and the final uninstall. Neither run touched the "Check for Updates" shortcut, which was broken in every installed copy at the time (see its row); the checks that run it were added on 2026-09-26. Run again on 2026-10-08 from an elevated shell against the .NET 10 build of 1.1.0, with the upgrade leg: 70 checks, all passing, the shortcut included; one error mentioning a constructor appeared in the console part-way through and was not captured; it did not fail a check and could not be reproduced afterwards (the installed app opened normally on its own and against the simulated driver). Since 2026-11-05 a CI workflow (`.github/workflows/installer.yml`) builds the MSI on a fresh Windows runner and runs the same test with the upgrade leg on every change to the installer, its scripts, or what it carries (the app, the agent and the SDK), and on a release tag; the test now also checks that `LICENSE` reaches the install folder (since the first runs, as a file holding the GPL's text). Its first runs, on 2026-11-05 against 1.2.0 with a throwaway 1.2.1 as the upgrade: 72 checks, all passing, `LICENSE` included. The run recorded for release 1.2.0, the same day: on GitHub's `windows-latest` runner (elevated), not a PC, against commit 1483daf, with the upgrade leg (1.2.0 to 1.2.1): 72 checks, all passing, exit 0, `LICENSE` present in `C:\Program Files\Drobo Dashboard REBORN`; that MSI was 71,438,336 bytes (68 MB), SHA-256 `df14c1dd91126ead5a136c5c1cd32857a195facca168e8addbc18c29e8c94362` (the released MSI is rebuilt from the `v1.2.0` tag and carries its own hash, published beside it). No run on a tester's own PC has been recorded for 1.2.0. Settings stay per Windows account. Since 2026-11-20 the installed launcher asks the agent's `/api/ping` (on `agent.port` from the agent's `config.json`, 7420 by default) before it starts one: an agent that answers means only the app is opened; otherwise it starts the agent and waits up to about 20 seconds for an answer, then opens the app either way. Until then it started an agent on every launch and waited a fixed 2 seconds, so each later launch added an agent and the app could open before the first one answered (readiness S4). The shares guide now ships at `docs\accessing-your-files.md` beside the exe, so the Tools tab's guide button appears in an installed copy; until 2026-11-20 it was hidden in every install (readiness C2). The Uninstall shortcut's tooltip said it removed the settings and the access token, which it never did; it now says they are kept. `windows/test-installer.ps1` gained thirteen checks for these (about 85 with the upgrade leg, from 72), among them a second launch, a second agent started by hand and a second `Run-Agent.bat` each leaving exactly one agent; they run only in CI's installer job, which had not run them when this was written (2026-11-21), and not yet on a PC |
| Fallback installer and uninstaller, used when the MSI toolchain is unavailable and the build produces a zip instead | **Built, unverified** | PowerShell scripts that mirror the MSI's layout exactly in reverse. The uninstaller is copied into the install folder, so it survives the download folder being cleaned up; it refuses to remove anything while the app or agent is still running, and asks Windows for administrator rights at most once rather than looping. `windows/test-fallback-installer.ps1` drives both end to end — fresh install, a re-install over it, the refusal, and an uninstall that removes all four places and keeps the user-data folder — into a temporary folder and a scratch key under `HKCU`, so it needs no administrator window and touches nothing real (78 checks). The four target paths are parameters of both scripts with the machine-wide defaults they have always had, and that test is their only caller. Redirecting them is all or nothing: passing one and leaving the other three at their real machine-wide defaults is refused, because redirecting is also what switches the elevation check off. What is still unproven is one real, elevated install into Program Files |
| "Check for Updates" shortcut, installed beside the app | **Built, dormant** | Runs the same update code and reads the same settings block the agent uses, so there is only one updater to switch on. Finds the agent whether it is run from an installed copy or from inside the source tree. Does nothing until a release address is configured. Since 2026-11-20, where it says it is switched off, it also prints the Releases page's address, and the app's Updates card has an "Open the Releases page" button (readiness C4, decision D7: the check itself stays dormant). **It failed in every installed copy from 2026-09-02, when the installer began bundling Python, until 2026-09-26**: it ran `python -m drobo_agent.updates` from inside `agent\`, the bundled runtime's `python314._pth` keeps the working directory off `sys.path`, so it stopped with `ModuleNotFoundError` -- and its window closed at once, so nobody saw it. No published release had it (1.0.0 used the PC's own Python). It now runs `agent\check_updates.py` by path, which puts its own folder on `sys.path` as `run_agent.py` does; the window waits for a key whatever the answer; and a check that cannot run exits 3, not Python's 1, which means "an update is available". Proven against a copy of the staged runtime (answer, broken config, missing package, no Python, and the source-tree fallback); `agent/tests/test_updates.py` runs it on an isolated interpreter on every push. `windows/test-installer.ps1` now runs the installed shortcut's target and requires the "switched off" answer and exit 0 -- seven checks, all passing in the elevated run of 2026-10-08 |
| Code signing | **Out of scope — decided 2026-09-05** | Not a for-profit project. SmartScreen says Unknown publisher (*More info* -> *Run anyway*); a Smart App Control machine must switch SAC off or use the browser dashboard. The bundled Python is signed by the Python Software Foundation, so the agent runs either way |
| Privacy guard on every commit and every push | **Complete** | A scanner plus a commit hook that blocks personal information, database files and real environment files. The automatic checks on a push go further: credential files blocked from being tracked, tracked content scanned for secret material, and the same scan for personal information. The test suites run in a separate job; see the last row of this table |
| Auto-start at boot | **Planned** | Documented as a manual Task Scheduler or Startup-folder step; not installed by the MSI. Since 2026-09-26 the documented boot task keeps monitoring on a PC that boots before its Drobo, instead of exiting; see Finding the Drobo |
| The tests run by themselves on every push | **Complete** | All thirty-one Python suites and the Windows app's own checks run on Windows on each push, beside the personal-data scan and the compile check that already ran (2026-09-19). A suite that runs but checks nothing, that prints a failed check and exits 0 anyway, or that moves out of its folder, fails the run rather than disappearing from the count. First green run on GitHub 2026-09-20: both jobs passed, including the Windows app job, which builds the WPF project and writes to the runner's own Credential Manager |

### Phases not yet started

| Capability | Status | Notes |
|---|---|---|
| iOS app: health, alerts, and automatic photo/video upload when you get home | **Blocked** | Needs a Mac to build. The agent side is done. Not file browsing, which the Files app already does over SMB |
| Drobo Pix replacement (view, send, receive; HEIC and Live Photos) | **Planned** | Phase 4; design questions open |
| Remote access via Tailscale on a separate always-on box | **Planned** | Phase 5; unblocked; two things to verify when begun |
| WinUI 3 rewrite of the app | **Planned** | Optional polish, not a gap |
| DroboSync trigger (start a replication) | **Blocked** | Needs two Drobos on one network |

## Out of scope, deliberately

| Not doing | Why |
|---|---|
| Firmware updating or flashing | The last builds were withdrawn by the vendor; there is no recovery tool; this was the one feature that could destroy the hardware |
| Custom firmware | A separate fork, needing a sacrificial second unit; BeyondRAID's on-disk format is undocumented |
| Exposing the Drobo or the agent to the internet | The device gets no security patches |
| TLS or token authentication toward the device | Both ends must agree, and the device's end is frozen |
| Push notifications through Apple's service | Needs a server; the agent pushes through ntfy or email instead |
| File browsing on iOS | The built-in Files app already does it over SMB |

## Platforms and distribution

- **Windows 10 and 11.** The MSI installs for all users with an administrator
  prompt and needs nothing else on the machine. Running from source needs
  Python 3.
- **The browser dashboard** works on anything on the same network, including a
  phone.
- **Releases** are published on this repository's own Releases page, each
  with a changelog. This repository has been the public one since 2026-10-08,
  published with a fresh history; release 1.0.0 came from a separate,
  code-only copy, now retired, and the export procedure and derived roadmaps
  that fed it are gone. The latest release is **1.2.0 (2026-11-05)**,
  after 1.1.0 (2026-10-15): every place the number is written says so,
  and the changelog's top section is 1.2.0, dated 2026-11-05. Its MSI
  passed the elevated lifecycle test on GitHub's Windows runner (72
  checks, with the upgrade leg); no run on a tester's own PC has been
  recorded for it.
  The people using releases are feature testers running it on their own
  hardware and reporting back through issues, with a **Tester report** issue
  form since 2026-11-08; nobody else edits the code. Each release's notes,
  from the tracked `.github/release-notes.md`, link the tester guide, the
  tester tasks and that form, and say what to do under Smart App Control.
  Anything that needs hardware this project does not have is listed with
  exact steps in [`testers-tasks.md`](testers-tasks.md). The README shows
  three screenshots of the browser dashboard, taken against the simulated
  Drobo; none of the Windows app yet, since those need a Windows PC.
  Since 2026-11-20 (readiness S9-S11, S14; decisions D5, D6) the tester
  documents say what an outside tester needs before a first session.
  `TESTING.md` has a table of every change the app can make on a Drobo,
  each marked proven on a Drobo 5N or not yet proven in the sentences both
  apps show, an "If your Drobo is not a 5N" section, how to type the
  Drobo's address (VPN off and Refresh first), the two-Drobo behaviour,
  what to copy when the app never gets past the first screen, the masked
  Copy diagnostics, closing the app and the Agent window before upgrading
  or uninstalling, and an Updates section pointing at the Releases page.
  One rule for pasting tool output is written the same in `TESTING.md`,
  `testers-tasks.md` and `docs/captures/README.md`. `testers-tasks.md`
  gained a "What you need" section, gives the sweep its address argument,
  no longer asks anyone to pull a drive, and labels its Identify step as a
  write. The Tester report form asks what was changed on the Drobo, what
  the app said and whether it found the Drobo; blank issues are off; and
  `SECURITY.md` offers a private route through GitHub's private
  vulnerability reporting, which is switched on in the repository's
  settings (checked 2026-11-20).
  The release text says reading is verified on a 5N and names the
  unproven writes. None of this text has yet been read by an outside
  tester; the form's YAML was checked to parse here, and GitHub renders
  it only once it is on the default branch.
- **License:** GPL-3.0-or-later since 2026-10-19 (the maintainer's
  decision; until then all rights were reserved). The verbatim text is
  `LICENSE` at the repository root, the SDK's package metadata declares the
  same, and the installer build copies `LICENSE` into the program folder
  beside `TESTING.md`. That copy is settled: since 2026-11-05 the
  installer's lifecycle test checks it, as a file holding the GPL's text,
  and the run recorded for 1.2.0 found it in
  `C:\Program Files\Drobo Dashboard REBORN`.

## How to check any claim here

Every row above points at code that exists at HEAD. To see it for yourself:

```bash
py agent/run_agent.py --once          # one reading from the device, as JSON
py agent/tests/test_agent.py          # the agent's end-to-end suite, no hardware
py sdk/tests/test_sharewrite.py       # the share write path and its guards
py sdk/tests/test_write_arbitration.py  # the four write outcomes, silence included
git log --reverse --format="%ad  %s" --date=short
```

The public history starts at one fresh commit (2026-10-08), so `git log` here
begins there. The path before it is told in
[`development-history.md`](development-history.md), and the commit hashes
quoted in these records point into the earlier history, which is kept
privately and will not resolve here.
